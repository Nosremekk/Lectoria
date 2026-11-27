"""
Serviço de Otimização Automática de Documentos em Segundo Plano (Fases 12.4.1 e 12.5).

Características principais:
- Preservação absoluta dos originais: nenhum arquivo em `/storage/books` é modificado.
- Derivados regeneráveis em `<DATA_DIR>/optimized/pdf/` (excluídos do backup obrigatório).
- Sem migração de schema SQLite (`SCHEMA_VERSION=13` preservado): estado persistido
  atomicamente em `<DATA_DIR>/optimized/state.json` sob lock reentrante.
- Execução segura de `qpdf` sem `preexec_fn`: utiliza `scripts/bounded_exec.py` (shell=False,
  argumentos explícitos, timeout, RLIMIT_AS e RLIMIT_CPU aplicados no filho após exec,
  cancelamento real via terminate/kill e degradação graciosa quando `qpdf` não está instalado).
- Validação real e ampliada pré-publicação: `qpdf --check`, `qpdf --is-linearized` (quando
  disponível), rejeição de comentários falsos `/Linearized`, contagem exata de páginas,
  geometria (`MediaBox`, `CropBox`, `/Rotate`), consistência textual, `/Annots`, sumário
  (outlines), `/AcroForm` e rechecagem do fingerprint de origem antes de `os.replace`.
- Fixação imutável da representação por sessão de leitura (`rep_id`): uma URL vinculada a
  uma representação nunca mistura bytes de original e derivado; derivados em uso são protegidos
  contra limpeza administrativa e evicção LRU; se a representação deixar de existir ou o original
  mudar, retorna erro controlado (`RepresentationStaleError` -> HTTP 409).
- Fila real em segundo plano com thread daemon única, recuperação após reinício, gancho pós-scan
  e coordenação bidirecional com backups, tarefas de automação e atividade de leitura.
- Modos de execução: `disabled` (padrão conservador), `immediate`, `scheduled` (janela + timezone
  + dias da semana) e `adaptive` (ociosidade de leitura + janela noturna opcional + backup).
- EPUBs: apenas análise e validação estrutural ZIP/container.xml (sem reescrita destrutiva).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import zipfile
from zoneinfo import ZoneInfo

from pypdf import PdfReader

import backup_service
import config
from database import get_db

logger = logging.getLogger("lectoria.doc_optimization")

ALGO_VERSION = "lin_v1"

VALID_MODES = ("disabled", "immediate", "scheduled", "adaptive")

VALID_STATES = (
    "pending",
    "analyzing",
    "waiting_resources",
    "processing",
    "validating",
    "optimized",
    "already_adequate",
    "ignored",
    "failed",
    "cancelled",
    "outdated",
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "mode": "disabled",  # disabled | immediate | scheduled | adaptive
    "window_start": "02:00",
    "window_end": "05:00",
    "timezone": "America/Sao_Paulo",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],  # 0=Seg .. 6=Dom
    "adaptive_use_window": False,
    "reading_idle_seconds": 120,
    "max_storage_mb": 1024,
    "min_free_disk_mb": 2048,
    "min_document_mb": 2.0,
    "max_document_mb": 250.0,
    "timeout_seconds": 180,
    "max_retries": 2,
    "subprocess_mem_limit_mb": 384,
}

PINNED_SESSION_TTL_SECONDS = 1800  # 30 minutos de inatividade por sessão de leitura

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_BOUNDED_EXEC_SCRIPT = Path(__file__).resolve().parent / "scripts" / "bounded_exec.py"

_STATE_LOCK = threading.RLock()
_WORKER_SEMAPHORE = threading.BoundedSemaphore(1)
_LAST_READING_ACTIVITY_TS: float = 0.0
_PAUSED: bool = False
_CANCEL_REQUESTED: bool = False
_OPTIMIZATION_RUNNING: bool = False
_ACTIVE_SUBPROC: Optional[subprocess.Popen] = None
_ACTIVE_SUBPROC_LOCK = threading.Lock()

# Compatibilidade com código/testes que inspecionam arquivos protegidos diretamente
_ACTIVE_FILES: Set[str] = set()
# Sessões pinadas: rep_id -> {book_id, user_id, representation, source_fingerprint, path, expires_at, created_at}
_PINNED_SESSIONS: Dict[str, Dict[str, Any]] = {}

_QPDF_CACHE: Optional[Dict[str, Any]] = None

_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_STOP_EVENT = threading.Event()
_WORKER_WAKE_EVENT = threading.Event()


class RepresentationStaleError(Exception):
    """Levantada quando uma representação pinada foi invalidada, removida ou o original mudou."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RepresentationForbiddenError(Exception):
    """Levantada quando um usuário tenta usar uma representação pinada de outro usuário/livro."""
    pass


def get_optimized_root() -> Path:
    return config.DATA_DIR / "optimized"


def get_optimized_pdf_dir() -> Path:
    return get_optimized_root() / "pdf"


def get_state_file_path() -> Path:
    return get_optimized_root() / "state.json"


def _sanitize_book_id(book_id: str) -> str:
    cleaned = _SAFE_ID_RE.sub("", str(book_id or ""))
    if not cleaned:
        raise ValueError("Identificador de livro inválido.")
    return cleaned[:64]


def compute_fast_fingerprint(file_path: Path) -> Tuple[str, os.stat_result]:
    """Fingerprint leve (mtime_ns + size + ino) sem leitura integral do arquivo no caminho quente."""
    st = file_path.stat()
    raw = f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12], st


def compute_fingerprint_from_stat(st: os.stat_result) -> str:
    raw = f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def mark_reading_activity() -> None:
    """Registra atividade recente de leitura para o modo adaptativo."""
    global _LAST_READING_ACTIVITY_TS
    _LAST_READING_ACTIVITY_TS = time.time()


def is_reading_active(idle_seconds: int = 120) -> bool:
    if _LAST_READING_ACTIVITY_TS <= 0:
        return False
    return (time.time() - _LAST_READING_ACTIVITY_TS) < max(0, int(idle_seconds))


def is_backup_running() -> bool:
    """Verifica se `backup_service` está executando backup/restore no momento."""
    lock = getattr(backup_service, "_backup_lock", None)
    if lock is None:
        return False
    return bool(lock.locked())


def is_optimization_running() -> bool:
    """Coordenação bidirecional: informa a outros subsistemas se há otimização pesada em curso."""
    return bool(_OPTIMIZATION_RUNNING)


def _is_automation_or_enrichment_busy() -> bool:
    """Coordenação leve com fila de metadados/automação para evitar disputa no 1 vCPU."""
    try:
        import background_enricher

        status = background_enricher.enricher.get_status()
        if status.get("is_running"):
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Subprocesso seguro sem preexec_fn (via scripts/bounded_exec.py)
# ---------------------------------------------------------------------------

def _build_bounded_command(cmd: List[str], mem_limit_mb: int, cpu_limit_s: int) -> List[str]:
    if os.name == "posix" and _BOUNDED_EXEC_SCRIPT.is_file() and (mem_limit_mb > 0 or cpu_limit_s > 0):
        return [
            sys.executable,
            str(_BOUNDED_EXEC_SCRIPT),
            "--mem-mb",
            str(max(0, int(mem_limit_mb))),
            "--cpu-s",
            str(max(0, int(cpu_limit_s))),
            "--",
            *cmd,
        ]
    return list(cmd)


def _run_bounded_subprocess(
    cmd: List[str],
    timeout_s: int = 30,
    mem_limit_mb: int = 384,
    track_for_cancel: bool = False,
) -> subprocess.CompletedProcess:
    """
    Executa subprocesso com shell=False e limites POSIX via launcher dedicado (sem preexec_fn).
    Quando `track_for_cancel=True`, permite encerramento imediato por `cancel_processing()`.
    """
    full_cmd = _build_bounded_command(cmd, mem_limit_mb=mem_limit_mb, cpu_limit_s=timeout_s + 5)

    if not track_for_cancel:
        return subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            check=False,
        )

    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    with _ACTIVE_SUBPROC_LOCK:
        global _ACTIVE_SUBPROC
        _ACTIVE_SUBPROC = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return subprocess.CompletedProcess(full_cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise
    finally:
        with _ACTIVE_SUBPROC_LOCK:
            if _ACTIVE_SUBPROC is proc:
                _ACTIVE_SUBPROC = None


def get_qpdf_info(force_refresh: bool = False) -> Dict[str, Any]:
    """Detecta disponibilidade e versão do executável `qpdf` com degradação graciosa."""
    global _QPDF_CACHE
    if _QPDF_CACHE is not None and not force_refresh:
        return dict(_QPDF_CACHE)

    qpdf_bin = shutil.which("qpdf")
    if not qpdf_bin:
        info = {"available": False, "path": None, "version": None}
        _QPDF_CACHE = info
        return dict(info)

    version_str = "unknown"
    try:
        proc = _run_bounded_subprocess([qpdf_bin, "--version"], timeout_s=5, mem_limit_mb=128)
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        if first_line:
            version_str = first_line[0].strip()
    except Exception:
        pass

    info = {"available": True, "path": qpdf_bin, "version": version_str}
    _QPDF_CACHE = info
    return dict(info)


# ---------------------------------------------------------------------------
# Persistência atômica de state.json
# ---------------------------------------------------------------------------

def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
    try:
        data = json.dumps(payload, indent=2, ensure_ascii=False)
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_state_unlocked() -> Dict[str, Any]:
    state_path = get_state_file_path()
    default_state = {
        "version": 1,
        "config": dict(DEFAULT_CONFIG),
        "items": {},
    }
    if not state_path.is_file():
        return default_state
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return default_state
        cfg = dict(DEFAULT_CONFIG)
        if isinstance(loaded.get("config"), dict):
            cfg.update(loaded["config"])
        items = loaded.get("items") if isinstance(loaded.get("items"), dict) else {}
        return {"version": 1, "config": cfg, "items": items}
    except Exception as exc:
        logger.warning("Falha ao ler state.json de otimização; restaurando padrão: %s", exc)
        return default_state


def _save_state_unlocked(state: Dict[str, Any]) -> None:
    _write_json_atomic(get_state_file_path(), state)


def get_config() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_load_state_unlocked()["config"])


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Atualiza configurações administrativas com validação estrita."""
    with _STATE_LOCK:
        state = _load_state_unlocked()
        cfg = state["config"]

        if "mode" in updates and updates["mode"] is not None:
            mode = str(updates["mode"]).strip().lower()
            if mode not in VALID_MODES:
                raise ValueError(f"Modo inválido: '{mode}'. Use um de {VALID_MODES}.")
            cfg["mode"] = mode
            if mode == "disabled":
                cfg["enabled"] = False
            elif "enabled" not in updates:
                cfg["enabled"] = True

        if "enabled" in updates and updates["enabled"] is not None:
            cfg["enabled"] = bool(updates["enabled"])
            if not cfg["enabled"]:
                cfg["mode"] = "disabled"
            elif cfg["mode"] == "disabled":
                cfg["mode"] = "adaptive"

        for time_key in ("window_start", "window_end"):
            if time_key in updates and updates[time_key] is not None:
                val = str(updates[time_key]).strip()
                if not re.match(r"^\d{2}:\d{2}$", val):
                    raise ValueError(f"Horário inválido em {time_key}: '{val}' (esperado HH:MM).")
                hh, mm = int(val[:2]), int(val[3:])
                if not (0 <= hh <= 23 and 0 <= mm <= 59):
                    raise ValueError(f"Horário fora do intervalo em {time_key}: '{val}'.")
                cfg[time_key] = val

        if "timezone" in updates and updates["timezone"] is not None:
            tz_name = str(updates["timezone"]).strip()
            ZoneInfo(tz_name)  # valida timezone IANA
            cfg["timezone"] = tz_name

        if "weekdays" in updates and updates["weekdays"] is not None:
            days = sorted({int(d) for d in updates["weekdays"] if 0 <= int(d) <= 6})
            if not days:
                raise ValueError("Selecione ao menos um dia da semana (0..6).")
            cfg["weekdays"] = days

        for bool_key in ("adaptive_use_window",):
            if bool_key in updates and updates[bool_key] is not None:
                cfg[bool_key] = bool(updates[bool_key])

        for int_key, lo, hi in (
            ("reading_idle_seconds", 0, 3600),
            ("max_storage_mb", 64, 50_000),
            ("min_free_disk_mb", 128, 100_000),
            ("timeout_seconds", 10, 1200),
            ("max_retries", 0, 5),
            ("subprocess_mem_limit_mb", 128, 2048),
        ):
            if int_key in updates and updates[int_key] is not None:
                cfg[int_key] = max(lo, min(hi, int(updates[int_key])))

        for flt_key, lo, hi in (
            ("min_document_mb", 0.0, 500.0),
            ("max_document_mb", 1.0, 2048.0),
        ):
            if flt_key in updates and updates[flt_key] is not None:
                cfg[flt_key] = max(lo, min(hi, float(updates[flt_key])))

        state["config"] = cfg
        _save_state_unlocked(state)

    wake_worker()
    return dict(cfg)


def is_within_schedule_window(cfg: Dict[str, Any], now_dt: Optional[datetime] = None) -> bool:
    """Avalia se o instante atual está dentro da janela configurada (suporta virada de meia-noite)."""
    try:
        tz = ZoneInfo(cfg.get("timezone") or "America/Sao_Paulo")
    except Exception:
        tz = timezone.utc
    now = now_dt.astimezone(tz) if now_dt else datetime.now(tz)

    weekdays = cfg.get("weekdays") or [0, 1, 2, 3, 4, 5, 6]
    if now.weekday() not in weekdays:
        return False

    ws = str(cfg.get("window_start") or "02:00")
    we = str(cfg.get("window_end") or "05:00")
    sh, sm = int(ws[:2]), int(ws[3:])
    eh, em = int(we[:2]), int(we[3:])
    cur_min = now.hour * 60 + now.minute
    start_min = sh * 60 + sm
    end_min = eh * 60 + em

    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= cur_min < end_min
    return cur_min >= start_min or cur_min < end_min


def can_run_background_task(cfg: Optional[Dict[str, Any]] = None, now_dt: Optional[datetime] = None) -> Tuple[bool, str]:
    """Verifica se o sistema está autorizado a iniciar uma nova tarefa pesada de otimização."""
    cfg = cfg or get_config()
    if _PAUSED:
        return False, "paused"
    if not cfg.get("enabled") or cfg.get("mode") == "disabled":
        return False, "disabled"
    if is_backup_running():
        return False, "backup_in_progress"
    if _is_automation_or_enrichment_busy():
        return False, "metadata_enrichment_in_progress"

    mode = cfg.get("mode")
    if mode == "scheduled":
        if not is_within_schedule_window(cfg, now_dt=now_dt):
            return False, "outside_schedule_window"
    elif mode == "adaptive":
        if is_reading_active(int(cfg.get("reading_idle_seconds", 120))):
            return False, "active_reading_detected"
        if cfg.get("adaptive_use_window") and not is_within_schedule_window(cfg, now_dt=now_dt):
            return False, "outside_adaptive_window"

    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(config.DATA_DIR)
        free_mb = usage.free / (1024 * 1024)
        if free_mb < float(cfg.get("min_free_disk_mb", 2048)):
            return False, f"low_free_disk ({free_mb:.0f} MB)"
    except OSError:
        pass

    return True, "ok"


# ---------------------------------------------------------------------------
# Detecção estrutural real de linearização (rejeita comentários falsos)
# ---------------------------------------------------------------------------

def _strip_pdf_comments(raw: bytes) -> bytes:
    """Remove linhas de comentário PDF (`%...`) para impedir falsos positivos em cabeçalho."""
    out_lines: List[bytes] = []
    for line in raw.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(b"%"):
            continue
        out_lines.append(line)
    return b"\n".join(out_lines)


def _has_real_linearization_dict(head: bytes, expected_size: int) -> Tuple[bool, bool]:
    """
    Verifica nos primeiros bytes (sem comentários) se existe um objeto PDF real de dicionário
    de linearização (`<num> <gen> obj << ... /Linearized ... /L <N> ... >>`).
    Retorna `(has_dict, length_matches)`.
    """
    uncommented = _strip_pdf_comments(head)
    m_obj = re.search(rb"\b\d+\s+\d+\s+obj\b\s*<<(.*?)>>", uncommented, flags=re.DOTALL)
    if not m_obj:
        return False, False
    dict_body = m_obj.group(1)
    if b"/Linearized" not in dict_body:
        return False, False
    m_l = re.search(rb"/L\s+(\d+)", dict_body)
    len_ok = bool(m_l and int(m_l.group(1)) == expected_size)
    return True, len_ok


def check_pdf_linearization_with_qpdf(file_path: Path, qpdf_bin: Optional[str] = None, mem_limit_mb: int = 256) -> Optional[bool]:
    """
    Consulta `qpdf --check-linearization` quando disponível.
    Retorna True (linearizado válido), False (não linearizado), ou None (qpdf indisponível/erro).
    """
    if not qpdf_bin:
        info = get_qpdf_info()
        qpdf_bin = info["path"] if info["available"] else None
    if not qpdf_bin:
        return None
    try:
        proc = _run_bounded_subprocess(
            [qpdf_bin, "--check-linearization", "--", str(file_path)],
            timeout_s=20,
            mem_limit_mb=mem_limit_mb,
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
        if "is not linearized" in combined:
            return False
        # qpdf --check-linearization: 0 = linearized OK, 3 = linearized with warnings, 2 = error
        if proc.returncode in (0, 3):
            return True
        if proc.returncode == 2:
            return False
    except Exception:
        pass
    return None


def analyze_pdf_eligibility(file_path: Path, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Analisa somente-leitura se um PDF é candidato à linearização com QPDF.
    Rejeita arquivos criptografados, assinados digitalmente, muito pequenos/grandes ou já linearizados.
    Não aceita comentários falsos `% /Linearized` como linearização válida.
    """
    cfg = cfg or get_config()
    if not file_path.is_file():
        return {"eligible": False, "state": "ignored", "reason": "file_missing"}

    st = file_path.stat()
    size_bytes = st.st_size
    size_mb = size_bytes / (1024 * 1024)

    if size_bytes < 1024:
        return {"eligible": False, "state": "ignored", "reason": "file_too_small", "size_bytes": size_bytes}

    min_mb = float(cfg.get("min_document_mb", 2.0))
    max_mb = float(cfg.get("max_document_mb", 250.0))
    if size_mb > max_mb:
        return {"eligible": False, "state": "ignored", "reason": f"exceeds_max_size_{max_mb:.0f}mb", "size_bytes": size_bytes}

    with open(file_path, "rb") as fh:
        head = fh.read(4096)
        fh.seek(max(0, size_bytes - 32768))
        tail = fh.read(32768)

    if not head.startswith(b"%PDF-"):
        return {"eligible": False, "state": "ignored", "reason": "invalid_pdf_header", "size_bytes": size_bytes}

    if b"/Encrypt" in head or b"/Encrypt" in tail:
        return {"eligible": False, "state": "ignored", "reason": "encrypted_pdf_unsupported", "size_bytes": size_bytes}

    if b"/ByteRange" in head or b"/ByteRange" in tail or b"/Type/Sig" in tail or b"/Type /Sig" in tail:
        return {"eligible": False, "state": "ignored", "reason": "digitally_signed_pdf_preserved", "size_bytes": size_bytes}

    has_lin_dict, lin_len_match = _has_real_linearization_dict(head, size_bytes)
    qpdf_lin = check_pdf_linearization_with_qpdf(file_path)

    if qpdf_lin is True and has_lin_dict and lin_len_match:
        return {
            "eligible": False,
            "state": "already_adequate",
            "reason": "already_linearized",
            "is_linearized": True,
            "size_bytes": size_bytes,
        }
    if qpdf_lin is None and has_lin_dict and lin_len_match:
        return {
            "eligible": False,
            "state": "already_adequate",
            "reason": "already_linearized",
            "is_linearized": True,
            "size_bytes": size_bytes,
        }

    if size_mb < min_mb:
        return {
            "eligible": False,
            "state": "already_adequate",
            "reason": f"below_min_size_{min_mb:.1f}mb",
            "is_linearized": bool(qpdf_lin) if qpdf_lin is not None else has_lin_dict,
            "size_bytes": size_bytes,
        }

    return {
        "eligible": True,
        "state": "pending",
        "reason": "non_linearized_candidate" if not has_lin_dict else "stale_linearization_candidate",
        "is_linearized": bool(qpdf_lin) if qpdf_lin is not None else has_lin_dict,
        "size_bytes": size_bytes,
    }


def analyze_epub_structure(file_path: Path) -> Dict[str, Any]:
    """
    Estratégia específica para EPUBs: apenas análise e validação estrutural
    (integridade ZIP, container.xml, contagem de entradas) sem reconstrução destrutiva.
    """
    if not file_path.is_file():
        return {"eligible": False, "state": "ignored", "reason": "file_missing"}
    size_bytes = file_path.stat().st_size
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                return {"eligible": False, "state": "ignored", "reason": f"corrupt_zip_entry:{bad}", "size_bytes": size_bytes}
            names = zf.namelist()
            has_container = "META-INF/container.xml" in names
            return {
                "eligible": False,
                "state": "already_adequate" if has_container else "ignored",
                "reason": "epub_validated_no_rewrite_needed" if has_container else "epub_missing_container",
                "entries_count": len(names),
                "size_bytes": size_bytes,
            }
    except Exception as exc:
        return {"eligible": False, "state": "ignored", "reason": f"invalid_epub_zip:{exc}", "size_bytes": size_bytes}


# ---------------------------------------------------------------------------
# Validação rigorosa e ampliada de integridade de derivados (Seções 4 e 8)
# ---------------------------------------------------------------------------

def _count_outline_entries(outline_obj: Any) -> int:
    if not outline_obj:
        return 0
    total = 0
    for item in outline_obj:
        if isinstance(item, list):
            total += _count_outline_entries(item)
        else:
            total += 1
    return total


def _normalize_text_sample(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "").strip())


def _validate_pdf_derivative(
    original_path: Path,
    derivative_path: Path,
    qpdf_bin: str,
    timeout_s: int = 30,
    mem_limit_mb: int = 384,
) -> Tuple[bool, str]:
    """
    Validação rigorosa pré-publicação do derivado:
    1. `qpdf --check` com limites de recursos (sem preexec_fn).
    2. Verificação real de linearização: objeto PDF `/Linearized` fora de comentários + `/L` exato
       + `qpdf --is-linearized` (rejeita comentários falsos).
    3. Mesma quantidade exata de páginas (> 0).
    4. Geometria por página: MediaBox, CropBox e `/Rotate`.
    5. Consistência textual, contagem de anotações/links (`/Annots`), sumário (outlines)
       e preservação de `/AcroForm` quando presente no original.
    """
    chk = _run_bounded_subprocess(
        [qpdf_bin, "--check", "--", str(derivative_path)],
        timeout_s=timeout_s,
        mem_limit_mb=mem_limit_mb,
    )
    if chk.returncode not in (0, 3):
        return False, f"qpdf_check_failed_rc_{chk.returncode}"

    deriv_size = derivative_path.stat().st_size
    with open(derivative_path, "rb") as fh:
        head = fh.read(4096)

    has_lin_dict, len_ok = _has_real_linearization_dict(head, deriv_size)
    if not has_lin_dict:
        return False, "derivative_not_linearized"
    if not len_ok:
        return False, "derivative_linearized_length_mismatch"

    # Prova real via QPDF (quando binário real suporta --is-linearized)
    qpdf_is_lin = check_pdf_linearization_with_qpdf(derivative_path, qpdf_bin=qpdf_bin, mem_limit_mb=mem_limit_mb)
    if qpdf_is_lin is False:
        return False, "qpdf_reports_not_linearized"

    orig_reader = PdfReader(str(original_path))
    deriv_reader = PdfReader(str(derivative_path))
    try:
        n_orig = len(orig_reader.pages)
        n_deriv = len(deriv_reader.pages)
        if n_orig != n_deriv or n_orig == 0:
            return False, f"page_count_mismatch:{n_orig}!={n_deriv}"

        # Preservação de AcroForm (formulários)
        orig_root = orig_reader.trailer.get("/Root") or {}
        deriv_root = deriv_reader.trailer.get("/Root") or {}
        if hasattr(orig_root, "get_object"):
            orig_root = orig_root.get_object()
        if hasattr(deriv_root, "get_object"):
            deriv_root = deriv_root.get_object()
        if "/AcroForm" in orig_root and "/AcroForm" not in deriv_root:
            return False, "acroform_lost_in_derivative"

        # Preservação de sumário (outlines)
        try:
            orig_outlines = _count_outline_entries(orig_reader.outline)
            deriv_outlines = _count_outline_entries(deriv_reader.outline)
            if orig_outlines > 0 and deriv_outlines != orig_outlines:
                return False, f"outline_count_mismatch:{orig_outlines}!={deriv_outlines}"
        except Exception:
            pass

        if n_orig <= 20:
            indices = list(range(n_orig))
        else:
            indices = sorted(set(list(range(5)) + [n_orig // 2] + list(range(n_orig - 5, n_orig))))

        # Para controle de RAM em PDFs muito grandes (> 80 MB), restringe extração de texto a 3 páginas
        orig_size = original_path.stat().st_size
        text_indices = set(indices if orig_size <= 80 * 1024 * 1024 else sorted({0, n_orig // 2, n_orig - 1}))

        for idx in indices:
            op = orig_reader.pages[idx]
            dp = deriv_reader.pages[idx]

            o_mbox = tuple(round(float(v), 2) for v in op.mediabox)
            d_mbox = tuple(round(float(v), 2) for v in dp.mediabox)
            if o_mbox != d_mbox:
                return False, f"mediabox_mismatch_page_{idx + 1}"

            o_cbox = tuple(round(float(v), 2) for v in op.cropbox)
            d_cbox = tuple(round(float(v), 2) for v in dp.cropbox)
            if o_cbox != d_cbox:
                return False, f"cropbox_mismatch_page_{idx + 1}"

            if int(op.get("/Rotate", 0) or 0) != int(dp.get("/Rotate", 0) or 0):
                return False, f"rotation_mismatch_page_{idx + 1}"

            # Verifica preservação de links/anotações internas da página
            o_annots = op.get("/Annots")
            d_annots = dp.get("/Annots")
            if hasattr(o_annots, "get_object"):
                o_annots = o_annots.get_object()
            if hasattr(d_annots, "get_object"):
                d_annots = d_annots.get_object()
            o_acount = len(o_annots) if isinstance(o_annots, (list, tuple)) else 0
            d_acount = len(d_annots) if isinstance(d_annots, (list, tuple)) else 0
            if o_acount != d_acount:
                return False, f"annots_count_mismatch_page_{idx + 1}:{o_acount}!={d_acount}"

            # Consistência textual básica nas páginas selecionadas
            if idx in text_indices:
                try:
                    ot = _normalize_text_sample(op.extract_text() or "")
                    dt = _normalize_text_sample(dp.extract_text() or "")
                    if ot and ot != dt:
                        return False, f"text_content_mismatch_page_{idx + 1}"
                except Exception:
                    pass
    finally:
        try:
            orig_reader.stream.close()
        except Exception:
            pass
        try:
            deriv_reader.stream.close()
        except Exception:
            pass

    return True, "ok"


# ---------------------------------------------------------------------------
# Fixação imutável de representação por sessão de leitura (Seção 6)
# ---------------------------------------------------------------------------

def _prune_expired_sessions_unlocked(now_ts: Optional[float] = None) -> None:
    now = now_ts if now_ts is not None else time.time()
    expired = [rid for rid, meta in _PINNED_SESSIONS.items() if float(meta.get("expires_at", 0.0)) <= now]
    for rid in expired:
        _PINNED_SESSIONS.pop(rid, None)


def _is_file_in_use_unlocked(path_obj: Path) -> bool:
    _prune_expired_sessions_unlocked()
    resolved = str(path_obj.resolve())
    if resolved in _ACTIVE_FILES:
        return True
    for meta in _PINNED_SESSIONS.values():
        if meta.get("resolved_path") == resolved:
            return True
    return False


def pin_reader_representation(
    book_id: str,
    user_id: int,
    original_path: Path,
    original_stat: os.stat_result,
    preferred: str = "auto",
) -> Dict[str, Any]:
    """
    Resolve a representação uma única vez no início da sessão de leitura e a fixa sob um `rep_id`
    imutável vinculado ao usuário, livro, fingerprint de origem e arquivo exato.
    """
    if preferred == "original":
        served_path, served_stat, served_rep = original_path, original_stat, "original"
    else:
        served_path, served_stat, served_rep = resolve_pdf_for_reader(book_id, original_path, original_stat)

    orig_fp = compute_fingerprint_from_stat(original_stat)
    prefix = "opt" if served_rep == "optimized" else "orig"
    rep_id = f"{prefix}-{orig_fp}-{secrets.token_hex(8)}"
    resolved_str = str(served_path.resolve())
    now = time.time()

    etag_base = f"{served_rep}-{served_stat.st_mtime_ns}-{served_stat.st_size}-{served_stat.st_ino}"
    etag = f'"{hashlib.sha256(etag_base.encode()).hexdigest()[:24]}"'

    with _STATE_LOCK:
        _prune_expired_sessions_unlocked(now)
        _PINNED_SESSIONS[rep_id] = {
            "rep_id": rep_id,
            "book_id": str(book_id),
            "user_id": int(user_id),
            "representation": served_rep,
            "source_fingerprint": orig_fp,
            "resolved_path": resolved_str,
            "content_length": int(served_stat.st_size),
            "created_at": now,
            "expires_at": now + PINNED_SESSION_TTL_SECONDS,
        }
        # Atualiza last_accessed_ts para LRU
        if served_rep == "optimized":
            state = _load_state_unlocked()
            if book_id in state["items"] and isinstance(state["items"][book_id], dict):
                state["items"][book_id]["last_accessed_ts"] = now
                _save_state_unlocked(state)

    return {
        "rep_id": rep_id,
        "representation": served_rep,
        "content_length": int(served_stat.st_size),
        "etag": etag,
        "source_fingerprint": orig_fp,
        "file_url": f"/api/books/{book_id}/file?rep={rep_id}",
    }


def resolve_pinned_representation(
    rep_id: str,
    book_id: str,
    user_id: int,
    original_path: Path,
    original_stat: os.stat_result,
) -> Tuple[Path, os.stat_result, str]:
    """
    Resolve uma URL fixada com `?rep=<rep_id>`.
    - Nunca troca silenciosamente de original para derivado ou vice-versa.
    - Rejeita uso por outro usuário ou livro (`RepresentationForbiddenError`).
    - Se o original foi substituído ou o derivado desapareceu, levanta `RepresentationStaleError`.
    """
    now = time.time()
    with _STATE_LOCK:
        _prune_expired_sessions_unlocked(now)
        meta = _PINNED_SESSIONS.get(rep_id)
        if not meta:
            raise RepresentationStaleError("session_expired_or_unknown", "Sessão de leitura expirada ou inválida.")
        if int(meta["user_id"]) != int(user_id) or str(meta["book_id"]) != str(book_id):
            raise RepresentationForbiddenError("Representação não pertence ao usuário ou documento solicitado.")

        current_orig_fp = compute_fingerprint_from_stat(original_stat)
        if current_orig_fp != meta["source_fingerprint"]:
            _PINNED_SESSIONS.pop(rep_id, None)
            raise RepresentationStaleError("original_changed", "O documento original foi atualizado; recarregue o leitor.")

        # Renova TTL da sessão ativa
        meta["expires_at"] = now + PINNED_SESSION_TTL_SECONDS
        rep_kind = meta["representation"]
        pinned_path = Path(meta["resolved_path"])

    if rep_kind == "original":
        return original_path, original_stat, "original"

    # Representação otimizada: valida que o arquivo exato ainda existe e mantém integridade básica
    pdf_dir = get_optimized_pdf_dir().resolve()
    try:
        resolved = pinned_path.resolve()
        if not resolved.is_relative_to(pdf_dir) or not resolved.is_file():
            raise RepresentationStaleError("derivative_missing", "Derivado otimizado não está mais disponível; recarregue o leitor.")
        st = resolved.stat()
        if st.st_size < 1024:
            raise RepresentationStaleError("derivative_corrupted", "Derivado otimizado inválido; recarregue o leitor.")
        return resolved, st, "optimized"
    except RepresentationStaleError:
        with _STATE_LOCK:
            _PINNED_SESSIONS.pop(rep_id, None)
        raise
    except OSError:
        with _STATE_LOCK:
            _PINNED_SESSIONS.pop(rep_id, None)
        raise RepresentationStaleError("derivative_unavailable", "Derivado otimizado indisponível; recarregue o leitor.")


def release_user_sessions(user_id: int) -> int:
    """Revoga imediatamente todas as sessões pinadas de leitura de um usuário (ex.: no logout)."""
    with _STATE_LOCK:
        to_del = [rid for rid, m in _PINNED_SESSIONS.items() if int(m.get("user_id", -1)) == int(user_id)]
        for rid in to_del:
            _PINNED_SESSIONS.pop(rid, None)
        return len(to_del)


# ---------------------------------------------------------------------------
# Armazenamento e política LRU segura
# ---------------------------------------------------------------------------

def get_storage_stats() -> Tuple[int, int]:
    """Retorna `(total_bytes, files_count)` dos derivados publicados."""
    pdf_dir = get_optimized_pdf_dir()
    if not pdf_dir.exists():
        return 0, 0
    total = 0
    count = 0
    for f in pdf_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    return total, count


def get_storage_usage_bytes() -> int:
    return get_storage_stats()[0]


def _enforce_storage_budget(needed_bytes: int, cfg: Dict[str, Any]) -> bool:
    """Aplica política LRU segura respeitando max_storage_mb sem remover arquivos em uso."""
    max_bytes = int(cfg.get("max_storage_mb", 1024)) * 1024 * 1024
    current = get_storage_usage_bytes()
    if current + needed_bytes <= max_bytes:
        return True

    pdf_dir = get_optimized_pdf_dir()
    if not pdf_dir.exists():
        return needed_bytes <= max_bytes

    with _STATE_LOCK:
        state = _load_state_unlocked()
        items = state["items"]
        candidates: List[Tuple[float, Path, Optional[str]]] = []
        by_filename = {
            v.get("derivative_filename"): bid
            for bid, v in items.items()
            if isinstance(v, dict) and v.get("derivative_filename")
        }
        for f in pdf_dir.iterdir():
            if not f.is_file() or f.name.startswith("."):
                continue
            if _is_file_in_use_unlocked(f):
                continue
            bid = by_filename.get(f.name)
            last_acc = 0.0
            if bid and isinstance(items.get(bid), dict):
                last_acc = float(items[bid].get("last_accessed_ts") or 0.0)
            if last_acc <= 0.0:
                try:
                    last_acc = f.stat().st_atime
                except OSError:
                    last_acc = 0.0
            candidates.append((last_acc, f, bid))

        candidates.sort(key=lambda x: x[0])
        for _, path_obj, bid in candidates:
            if current + needed_bytes <= max_bytes:
                break
            try:
                fsize = path_obj.stat().st_size
                path_obj.unlink(missing_ok=True)
                current = max(0, current - fsize)
                if bid and bid in items:
                    items[bid]["state"] = "outdated"
                    items[bid]["derivative_filename"] = None
                    items[bid]["derivative_bytes"] = 0
            except OSError:
                pass
        _save_state_unlocked(state)

    return (current + needed_bytes) <= max_bytes


# ---------------------------------------------------------------------------
# Processamento de documento único
# ---------------------------------------------------------------------------

def optimize_single_book(book_id: str, original_path: Path, force: bool = False) -> Dict[str, Any]:
    """
    Analisa, lineariza (via QPDF sem preexec_fn), valida rigorosamente e publica atomicamente
    o derivado de um único livro. Nunca altera o documento original.
    """
    global _OPTIMIZATION_RUNNING
    cfg = get_config()
    now_iso = datetime.now(timezone.utc).isoformat()
    safe_id = _sanitize_book_id(book_id)

    if not original_path.is_file():
        return {"book_id": book_id, "state": "ignored", "reason": "file_missing"}

    ext = original_path.suffix.lower()
    fp_before, st_before = compute_fast_fingerprint(original_path)

    if ext == ".epub":
        analysis = analyze_epub_structure(original_path)
        with _STATE_LOCK:
            state = _load_state_unlocked()
            state["items"][book_id] = {
                "book_id": book_id,
                "format": "epub",
                "state": analysis["state"],
                "reason": analysis["reason"],
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "updated_at": now_iso,
            }
            _save_state_unlocked(state)
        return state["items"][book_id]

    if ext != ".pdf":
        return {"book_id": book_id, "state": "ignored", "reason": "unsupported_format"}

    with _STATE_LOCK:
        state = _load_state_unlocked()
        prev = state["items"].get(book_id) or {}
        retries = int(prev.get("retries", 0))
        if not force and prev.get("state") == "failed" and prev.get("source_fingerprint") == fp_before:
            if retries >= int(cfg.get("max_retries", 2)):
                return prev
        if (
            not force
            and prev.get("state") == "optimized"
            and prev.get("source_fingerprint") == fp_before
            and prev.get("derivative_filename")
            and (get_optimized_pdf_dir() / prev["derivative_filename"]).is_file()
        ):
            return prev

    analysis = analyze_pdf_eligibility(original_path, cfg=cfg)
    if not analysis["eligible"]:
        with _STATE_LOCK:
            state = _load_state_unlocked()
            state["items"][book_id] = {
                "book_id": book_id,
                "format": "pdf",
                "state": analysis["state"],
                "reason": analysis["reason"],
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "updated_at": now_iso,
            }
            _save_state_unlocked(state)
        return state["items"][book_id]

    qpdf_info = get_qpdf_info()
    if not qpdf_info["available"]:
        with _STATE_LOCK:
            state = _load_state_unlocked()
            state["items"][book_id] = {
                "book_id": book_id,
                "format": "pdf",
                "state": "waiting_resources",
                "reason": "qpdf_unavailable",
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "updated_at": now_iso,
            }
            _save_state_unlocked(state)
        return state["items"][book_id]

    if not _enforce_storage_budget(int(st_before.st_size * 1.05), cfg):
        with _STATE_LOCK:
            state = _load_state_unlocked()
            state["items"][book_id] = {
                "book_id": book_id,
                "format": "pdf",
                "state": "waiting_resources",
                "reason": "storage_budget_exceeded",
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "updated_at": now_iso,
            }
            _save_state_unlocked(state)
        return state["items"][book_id]

    pdf_dir = get_optimized_pdf_dir()
    pdf_dir.mkdir(parents=True, exist_ok=True)
    target_filename = f"{safe_id}_{fp_before}_{ALGO_VERSION}.pdf"
    target_path = (pdf_dir / target_filename).resolve()
    if not target_path.is_relative_to(pdf_dir.resolve()):
        raise ValueError("Caminho de derivado fora do diretório autorizado.")

    tmp_path = pdf_dir / f".{target_filename}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"

    with _WORKER_SEMAPHORE:
        if _CANCEL_REQUESTED:
            return {"book_id": book_id, "state": "cancelled", "reason": "cancelled_by_admin"}

        _OPTIMIZATION_RUNNING = True
        with _STATE_LOCK:
            state = _load_state_unlocked()
            prev = state["items"].get(book_id) or {}
            state["items"][book_id] = {
                **prev,
                "book_id": book_id,
                "format": "pdf",
                "state": "processing",
                "reason": "qpdf_linearizing",
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_state_unlocked(state)

        t0 = time.perf_counter()
        try:
            timeout_s = int(cfg.get("timeout_seconds", 180))
            mem_limit_mb = int(cfg.get("subprocess_mem_limit_mb", 384))

            proc = _run_bounded_subprocess(
                [
                    qpdf_info["path"],
                    "--linearize",
                    "--object-streams=preserve",
                    "--",
                    str(original_path),
                    str(tmp_path),
                ],
                timeout_s=timeout_s,
                mem_limit_mb=mem_limit_mb,
                track_for_cancel=True,
            )
            if _CANCEL_REQUESTED:
                tmp_path.unlink(missing_ok=True)
                with _STATE_LOCK:
                    state = _load_state_unlocked()
                    state["items"][book_id]["state"] = "cancelled"
                    state["items"][book_id]["reason"] = "cancelled_by_admin"
                    state["items"][book_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _save_state_unlocked(state)
                return state["items"][book_id]

            if proc.returncode not in (0, 3) or not tmp_path.is_file():
                raise RuntimeError(f"qpdf_failed_rc_{proc.returncode}: {(proc.stderr or '')[:200]}")

            ok, val_reason = _validate_pdf_derivative(
                original_path,
                tmp_path,
                qpdf_info["path"],
                timeout_s=min(60, timeout_s),
                mem_limit_mb=mem_limit_mb,
            )
            if not ok:
                raise RuntimeError(f"validation_failed:{val_reason}")

            # Guarda anti-regressão de tamanho (evita desperdiçar disco/banda em PDFs que incham > 15%)
            deriv_tmp_size = tmp_path.stat().st_size
            if st_before.st_size >= 65536 and deriv_tmp_size > int(st_before.st_size * 1.15):
                tmp_path.unlink(missing_ok=True)
                ratio = round(deriv_tmp_size / max(1, st_before.st_size), 2)
                entry = {
                    "book_id": book_id,
                    "format": "pdf",
                    "state": "ignored",
                    "reason": f"size_regression_exceeded:{ratio}x",
                    "source_fingerprint": fp_before,
                    "original_bytes": st_before.st_size,
                    "derivative_filename": None,
                    "derivative_bytes": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                with _STATE_LOCK:
                    state = _load_state_unlocked()
                    state["items"][book_id] = entry
                    _save_state_unlocked(state)
                return entry

            fp_after, _ = compute_fast_fingerprint(original_path)
            if fp_after != fp_before:
                raise RuntimeError("original_changed_during_processing")

            os.replace(tmp_path, target_path)
            deriv_size = target_path.stat().st_size
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

            with _STATE_LOCK:
                for old in pdf_dir.glob(f"{safe_id}_*.pdf"):
                    if old.resolve() != target_path and not _is_file_in_use_unlocked(old):
                        old.unlink(missing_ok=True)

            entry = {
                "book_id": book_id,
                "format": "pdf",
                "state": "optimized",
                "reason": "linearized_validated",
                "source_fingerprint": fp_before,
                "algo_version": ALGO_VERSION,
                "tool_version": qpdf_info["version"],
                "original_bytes": st_before.st_size,
                "derivative_filename": target_filename,
                "derivative_bytes": deriv_size,
                "processing_ms": elapsed_ms,
                "retries": 0,
                "last_accessed_ts": time.time(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with _STATE_LOCK:
                state = _load_state_unlocked()
                state["items"][book_id] = entry
                _save_state_unlocked(state)
            return entry

        except subprocess.TimeoutExpired:
            tmp_path.unlink(missing_ok=True)
            reason = f"qpdf_timeout_{cfg.get('timeout_seconds', 180)}s"
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            reason = str(exc)[:240]
        finally:
            _OPTIMIZATION_RUNNING = False

        with _STATE_LOCK:
            state = _load_state_unlocked()
            prev = state["items"].get(book_id) or {}
            entry = {
                "book_id": book_id,
                "format": "pdf",
                "state": "failed",
                "reason": reason,
                "source_fingerprint": fp_before,
                "original_bytes": st_before.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "retries": int(prev.get("retries", 0)) + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            state["items"][book_id] = entry
            _save_state_unlocked(state)
        return entry


def resolve_pdf_for_reader(
    book_id: str,
    original_path: Path,
    original_stat: os.stat_result,
) -> Tuple[Path, os.stat_result, str]:
    """
    Resolve a melhor representação disponível no momento da abertura do documento.
    Retorna `(path, stat, representation)` onde representation é `'optimized'` ou `'original'`.
    """
    if original_path.suffix.lower() != ".pdf":
        return original_path, original_stat, "original"

    with _STATE_LOCK:
        state = _load_state_unlocked()
        if not state["config"].get("enabled"):
            return original_path, original_stat, "original"
        item = state["items"].get(book_id)
        if not item or item.get("state") != "optimized" or not item.get("derivative_filename"):
            return original_path, original_stat, "original"
        expected_fp = item.get("source_fingerprint")
        deriv_name = item.get("derivative_filename")

    current_fp = compute_fingerprint_from_stat(original_stat)
    if current_fp != expected_fp:
        return original_path, original_stat, "original"

    pdf_dir = get_optimized_pdf_dir().resolve()
    deriv_path = (pdf_dir / Path(deriv_name).name).resolve()
    if not deriv_path.is_relative_to(pdf_dir) or not deriv_path.is_file():
        return original_path, original_stat, "original"

    try:
        deriv_stat = deriv_path.stat()
        if deriv_stat.st_size < 1024:
            return original_path, original_stat, "original"
        return deriv_path, deriv_stat, "optimized"
    except OSError:
        return original_path, original_stat, "original"


# ---------------------------------------------------------------------------
# Invalidação, limpeza, pausa, cancelamento real e recuperação pós-reinício
# ---------------------------------------------------------------------------

def invalidate_book_derivatives(book_id: str) -> int:
    """Remove derivados de um livro específico (respeitando arquivos em uso; nunca toca no original)."""
    pdf_dir = get_optimized_pdf_dir()
    removed = 0
    with _STATE_LOCK:
        if pdf_dir.exists():
            safe_id = _sanitize_book_id(book_id)
            for f in pdf_dir.glob(f"{safe_id}_*"):
                if f.is_file() and not _is_file_in_use_unlocked(f):
                    f.unlink(missing_ok=True)
                    removed += 1
        state = _load_state_unlocked()
        if book_id in state["items"]:
            state["items"][book_id]["state"] = "outdated"
            state["items"][book_id]["derivative_filename"] = None
            state["items"][book_id]["derivative_bytes"] = 0
            _save_state_unlocked(state)
    return removed


def cleanup_all_derivatives() -> Dict[str, Any]:
    """Remove derivados regeneráveis e temporários fora de uso sem tocar nos originais."""
    pdf_dir = get_optimized_pdf_dir()
    removed_files = 0
    freed_bytes = 0
    skipped_in_use = 0
    with _STATE_LOCK:
        if pdf_dir.exists():
            for f in pdf_dir.iterdir():
                if not f.is_file():
                    continue
                if _is_file_in_use_unlocked(f):
                    skipped_in_use += 1
                    continue
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink(missing_ok=True)
                    removed_files += 1
                except OSError:
                    pass
        state = _load_state_unlocked()
        for item in state["items"].values():
            if isinstance(item, dict) and item.get("state") == "optimized":
                fname = item.get("derivative_filename")
                if fname and pdf_dir.exists() and (pdf_dir / fname).is_file():
                    continue  # ainda em uso por sessão pinada
                item["state"] = "outdated"
                item["derivative_filename"] = None
                item["derivative_bytes"] = 0
        _save_state_unlocked(state)
    return {"removed_files": removed_files, "freed_bytes": freed_bytes, "skipped_in_use": skipped_in_use}


def recover_on_startup() -> Dict[str, int]:
    """
    Recupera tarefas interrompidas por reinicialização/crash e limpa temporários `.tmp.*` órfãos.
    """
    cleaned_tmp = 0
    recovered_items = 0
    pdf_dir = get_optimized_pdf_dir()
    if pdf_dir.exists():
        for f in pdf_dir.iterdir():
            if f.is_file() and f.name.startswith(".") and ".tmp." in f.name:
                try:
                    f.unlink(missing_ok=True)
                    cleaned_tmp += 1
                except OSError:
                    pass

    with _STATE_LOCK:
        state = _load_state_unlocked()
        changed = False
        for item in state["items"].values():
            if isinstance(item, dict) and item.get("state") in ("analyzing", "processing", "validating"):
                item["state"] = "pending"
                item["reason"] = "recovered_after_restart"
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                recovered_items += 1
                changed = True
        if changed:
            _save_state_unlocked(state)

    return {"cleaned_tmp": cleaned_tmp, "recovered_items": recovered_items}


def pause_processing() -> Dict[str, Any]:
    global _PAUSED
    _PAUSED = True
    return {"paused": True}


def resume_processing() -> Dict[str, Any]:
    global _PAUSED, _CANCEL_REQUESTED
    _PAUSED = False
    _CANCEL_REQUESTED = False
    wake_worker()
    return {"paused": False}


def cancel_processing() -> Dict[str, Any]:
    """Solicita cancelamento e encerra imediatamente eventual subprocesso QPDF em execução."""
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True
    terminated = False
    with _ACTIVE_SUBPROC_LOCK:
        proc = _ACTIVE_SUBPROC
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            terminated = True
        except Exception:
            pass
    return {"cancel_requested": True, "subprocess_terminated": terminated}


def get_status() -> Dict[str, Any]:
    """Retorna painel consolidado de métricas e configuração para a interface administrativa."""
    with _STATE_LOCK:
        _prune_expired_sessions_unlocked()
        state = _load_state_unlocked()
        cfg = dict(state["config"])
        items = state["items"]
        active_sessions = len(_PINNED_SESSIONS)

    counts = {st: 0 for st in VALID_STATES}
    for v in items.values():
        if isinstance(v, dict):
            s = v.get("state", "pending")
            if s in counts:
                counts[s] += 1

    storage_bytes, files_count = get_storage_stats()
    can_run, run_reason = can_run_background_task(cfg)

    return {
        "config": cfg,
        "qpdf": get_qpdf_info(),
        "paused": _PAUSED,
        "worker_running": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        "optimization_running": _OPTIMIZATION_RUNNING,
        "active_pinned_sessions": active_sessions,
        "can_run_now": can_run,
        "can_run_reason": run_reason,
        "counts": counts,
        "total_analyzed": len(items),
        "storage_used_bytes": storage_bytes,
        "storage_used_mb": round(storage_bytes / (1024 * 1024), 2),
        "storage_limit_mb": cfg.get("max_storage_mb", 1024),
        "storage_files_count": files_count,
    }


def enqueue_pending_books(limit: int = 50) -> Dict[str, Any]:
    """Analisa livros ativos no banco e enfileira candidatos pendentes (sem processar de forma bloqueante)."""
    import library

    cfg = get_config()
    enqueued = 0
    already_ok = 0
    ignored = 0

    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, format FROM books WHERE is_missing = 0 ORDER BY filesize DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()

    now_iso = datetime.now(timezone.utc).isoformat()
    with _STATE_LOCK:
        state = _load_state_unlocked()
        for row in rows:
            bid = row["id"]
            try:
                fpath = library.resolve_book_path(bid)
            except Exception:
                continue
            fp, st = compute_fast_fingerprint(fpath)
            prev = state["items"].get(bid)
            if prev and prev.get("source_fingerprint") == fp and prev.get("state") in ("optimized", "already_adequate", "processing"):
                already_ok += 1
                continue

            if fpath.suffix.lower() == ".epub":
                res = analyze_epub_structure(fpath)
            else:
                res = analyze_pdf_eligibility(fpath, cfg=cfg)

            state["items"][bid] = {
                "book_id": bid,
                "format": fpath.suffix.lower().lstrip("."),
                "state": res["state"],
                "reason": res["reason"],
                "source_fingerprint": fp,
                "original_bytes": st.st_size,
                "derivative_filename": None,
                "derivative_bytes": 0,
                "retries": int(prev.get("retries", 0)) if isinstance(prev, dict) and prev.get("source_fingerprint") == fp else 0,
                "updated_at": now_iso,
            }
            if res["state"] == "pending":
                enqueued += 1
            elif res["state"] == "already_adequate":
                already_ok += 1
            else:
                ignored += 1
        _save_state_unlocked(state)

    if enqueued > 0:
        wake_worker()
    return {"enqueued": enqueued, "already_adequate": already_ok, "ignored": ignored}


# ---------------------------------------------------------------------------
# Worker real em segundo plano (Seção 9)
# ---------------------------------------------------------------------------

def process_next_pending_book(now_dt: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    Processa um único item pendente da fila caso as políticas de recurso/agenda permitam.
    Retorna o resultado do item processado ou None se não havia trabalho elegível.
    """
    import library

    cfg = get_config()
    ok, _ = can_run_background_task(cfg, now_dt=now_dt)
    if not ok:
        return None

    next_book_id: Optional[str] = None
    with _STATE_LOCK:
        state = _load_state_unlocked()
        max_retries = int(cfg.get("max_retries", 2))
        for bid, item in state["items"].items():
            if not isinstance(item, dict):
                continue
            st = item.get("state")
            if st == "pending" or (st == "waiting_resources" and item.get("reason") != "qpdf_unavailable"):
                if int(item.get("retries", 0)) <= max_retries:
                    next_book_id = bid
                    break
            elif st == "waiting_resources" and item.get("reason") == "qpdf_unavailable":
                if get_qpdf_info()["available"]:
                    next_book_id = bid
                    break

    if not next_book_id:
        return None

    try:
        orig_path = library.resolve_book_path(next_book_id)
    except Exception:
        with _STATE_LOCK:
            state = _load_state_unlocked()
            if next_book_id in state["items"]:
                state["items"][next_book_id]["state"] = "ignored"
                state["items"][next_book_id]["reason"] = "file_missing"
                _save_state_unlocked(state)
        return None

    return optimize_single_book(next_book_id, orig_path, force=False)


def _worker_loop(poll_interval_seconds: float = 30.0) -> None:
    while not _WORKER_STOP_EVENT.is_set():
        try:
            processed = process_next_pending_book()
            if processed is not None and not _WORKER_STOP_EVENT.is_set():
                # Pequena pausa cooperativa para não saturar 1 vCPU
                _WORKER_STOP_EVENT.wait(timeout=0.5)
                continue
        except Exception as exc:
            logger.warning("Erro no worker de otimização de documentos: %s", exc)
        _WORKER_WAKE_EVENT.wait(timeout=poll_interval_seconds)
        _WORKER_WAKE_EVENT.clear()


def wake_worker() -> None:
    _WORKER_WAKE_EVENT.set()


def start_background_worker(poll_interval_seconds: float = 30.0) -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    _WORKER_STOP_EVENT.clear()
    _WORKER_WAKE_EVENT.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        args=(poll_interval_seconds,),
        name="lectoria-doc-opt-worker",
        daemon=True,
    )
    _WORKER_THREAD.start()


def stop_background_worker(timeout_seconds: float = 3.0) -> None:
    global _WORKER_THREAD
    _WORKER_STOP_EVENT.set()
    _WORKER_WAKE_EVENT.set()
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        _WORKER_THREAD.join(timeout=timeout_seconds)
    _WORKER_THREAD = None


def notify_scan_completed() -> None:
    """Enfileira candidatos em background após o scanner de biblioteca quando habilitado."""
    cfg = get_config()
    if not cfg.get("enabled") or cfg.get("mode") == "disabled":
        return

    def _bg():
        try:
            enqueue_pending_books(limit=100)
        except Exception as exc:
            logger.debug("Falha ao enfileirar candidatos pós-scan: %s", exc)

    threading.Thread(target=_bg, name="lectoria-doc-opt-scan-hook", daemon=True).start()
