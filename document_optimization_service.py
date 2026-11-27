"""
Serviço de Otimização Automática de Documentos em Segundo Plano (Fase 12.4.1).

Características principais:
- Preservação absoluta dos originais: nenhum arquivo em `/storage/books` é modificado.
- Derivados regeneráveis em `<DATA_DIR>/optimized/pdf/` (excluídos do backup obrigatório).
- Sem migração de schema SQLite (`SCHEMA_VERSION=13` preservado): estado persistido
  atomicamente em `<DATA_DIR>/optimized/state.json`.
- Integração segura com `qpdf` (argumentos em lista, `shell=False`, timeout explícito,
  limites de memória/CPU, degradação graciosa quando `qpdf` não está instalado).
- Validação rigorosa pré-publicação: `qpdf --check`, contagem de páginas, geometria
  (MediaBox + rotação), verificação de `/Linearized` e revalidação do fingerprint de origem.
- Modos de execução configuráveis: `disabled` (padrão conservador), `immediate`,
  `scheduled` (janela + timezone + dias da semana) e `adaptive` (respeita leitura ativa,
  trava de backup e orçamento de disco/RAM).
- Estratégia específica para EPUBs: análise de integridade ZIP e manifesto (sem reconstrução
  destrutiva, preservando 100% de EPUB CFI e anotações).
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

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_STATE_LOCK = threading.RLock()
_WORKER_SEMAPHORE = threading.BoundedSemaphore(1)
_LAST_READING_ACTIVITY_TS: float = 0.0
_PAUSED: bool = False
_CANCEL_REQUESTED: bool = False
_ACTIVE_FILES: Set[str] = set()
_QPDF_CACHE: Optional[Dict[str, Any]] = None


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
        proc = subprocess.run(
            [qpdf_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            check=False,
        )
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        if first_line:
            version_str = first_line[0].strip()
    except Exception:
        pass

    info = {"available": True, "path": qpdf_bin, "version": version_str}
    _QPDF_CACHE = info
    return dict(info)


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
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
        return dict(cfg)


def is_within_schedule_window(cfg: Dict[str, Any], now_dt: Optional[datetime] = None) -> bool:
    """Avalia se o instante atual está dentro da janela configurada (suporta janelas noturnas que cruzam meia-noite)."""
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
    # Janela cruza meia-noite (ex.: 23:00 -> 05:00)
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

    mode = cfg.get("mode")
    if mode == "scheduled":
        if not is_within_schedule_window(cfg, now_dt=now_dt):
            return False, "outside_schedule_window"
    elif mode == "adaptive":
        if is_reading_active(int(cfg.get("reading_idle_seconds", 120))):
            return False, "active_reading_detected"
        if cfg.get("adaptive_use_window") and not is_within_schedule_window(cfg, now_dt=now_dt):
            return False, "outside_adaptive_window"

    # Verifica espaço livre em disco no filesystem de DATA_DIR
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(config.DATA_DIR)
        free_mb = usage.free / (1024 * 1024)
        if free_mb < float(cfg.get("min_free_disk_mb", 2048)):
            return False, f"low_free_disk ({free_mb:.0f} MB)"
    except OSError:
        pass

    return True, "ok"


def analyze_pdf_eligibility(file_path: Path, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Analisa somente-leitura se um PDF é candidato à linearização com QPDF.
    Rejeita/ignora arquivos criptografados, assinados digitalmente, muito pequenos/grandes ou já linearizados.
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

    # Proteção: nunca otimizar PDFs criptografados/protegidos por senha ou assinados digitalmente
    if b"/Encrypt" in head or b"/Encrypt" in tail:
        return {"eligible": False, "state": "ignored", "reason": "encrypted_pdf_unsupported", "size_bytes": size_bytes}

    if b"/ByteRange" in head or b"/ByteRange" in tail or b"/Type/Sig" in tail or b"/Type /Sig" in tail:
        return {"eligible": False, "state": "ignored", "reason": "digitally_signed_pdf_preserved", "size_bytes": size_bytes}

    is_linearized = b"/Linearized" in head
    lin_len_match = False
    if is_linearized:
        m_l = re.search(rb"/L\s+(\d+)", head)
        if m_l and int(m_l.group(1)) == size_bytes:
            lin_len_match = True

    if is_linearized and lin_len_match:
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
            "is_linearized": is_linearized,
            "size_bytes": size_bytes,
        }

    return {
        "eligible": True,
        "state": "pending",
        "reason": "non_linearized_candidate" if not is_linearized else "stale_linearization_candidate",
        "is_linearized": is_linearized,
        "size_bytes": size_bytes,
    }


def analyze_epub_structure(file_path: Path) -> Dict[str, Any]:
    """
    Estratégia específica para EPUBs (Seção 15): apenas análise e validação estrutural
    (integridade ZIP, container.xml, contagem de entradas/imagens) sem reconstrução destrutiva.
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


def _validate_pdf_derivative(original_path: Path, derivative_path: Path, qpdf_bin: str, timeout_s: int = 30) -> Tuple[bool, str]:
    """
    Validação rigorosa pré-publicação do derivado:
    1. `qpdf --check` (integridade estrutural).
    2. Cabeçalho `/Linearized` presente com `/L` consistente.
    3. Mesma quantidade exata de páginas e geometria (MediaBox + rotação) via pypdf.
    """
    chk = subprocess.run(
        [qpdf_bin, "--check", "--", str(derivative_path)],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        shell=False,
        check=False,
    )
    # qpdf --check retorna 0 (ok) ou 3 (ok com warnings menores); 2 = erro estrutural
    if chk.returncode not in (0, 3):
        return False, f"qpdf_check_failed_rc_{chk.returncode}"

    deriv_size = derivative_path.stat().st_size
    with open(derivative_path, "rb") as fh:
        head = fh.read(4096)
    if b"/Linearized" not in head:
        return False, "derivative_not_linearized"
    m_l = re.search(rb"/L\s+(\d+)", head)
    if not m_l or int(m_l.group(1)) != deriv_size:
        return False, "derivative_linearized_length_mismatch"

    # Validação de páginas e geometria (preservação de coordenadas de anotações)
    orig_reader = PdfReader(str(original_path))
    deriv_reader = PdfReader(str(derivative_path))
    n_orig = len(orig_reader.pages)
    n_deriv = len(deriv_reader.pages)
    if n_orig != n_deriv or n_orig == 0:
        return False, f"page_count_mismatch:{n_orig}!={n_deriv}"

    # Verifica todas as páginas até 20, ou amostra representativa (início + meio + fim) em PDFs extensos
    if n_orig <= 20:
        indices = list(range(n_orig))
    else:
        indices = sorted(set(list(range(5)) + [n_orig // 2] + list(range(n_orig - 5, n_orig))))

    for idx in indices:
        op = orig_reader.pages[idx]
        dp = deriv_reader.pages[idx]
        o_box = tuple(round(float(v), 2) for v in op.mediabox)
        d_box = tuple(round(float(v), 2) for v in dp.mediabox)
        if o_box != d_box:
            return False, f"mediabox_mismatch_page_{idx + 1}"
        if int(op.get("/Rotate", 0) or 0) != int(dp.get("/Rotate", 0) or 0):
            return False, f"rotation_mismatch_page_{idx + 1}"

    return True, "ok"


def get_storage_usage_bytes() -> int:
    pdf_dir = get_optimized_pdf_dir()
    if not pdf_dir.exists():
        return 0
    total = 0
    for f in pdf_dir.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


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
            if str(f.resolve()) in _ACTIVE_FILES:
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


def optimize_single_book(book_id: str, original_path: Path, force: bool = False) -> Dict[str, Any]:
    """
    Analisa, lineariza (via QPDF), valida e publica atomicamente o derivado de um único livro.
    Nunca altera o documento original.
    """
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

    # Verifica limite de retries
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

        t0 = time.perf_counter()
        try:
            timeout_s = int(cfg.get("timeout_seconds", 180))
            mem_limit_bytes = int(cfg.get("subprocess_mem_limit_mb", 384)) * 1024 * 1024

            def _apply_limits():
                try:
                    import resource
                    resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 5, timeout_s + 5))
                    resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, mem_limit_bytes))
                except Exception:
                    pass

            proc = subprocess.run(
                [
                    qpdf_info["path"],
                    "--linearize",
                    "--object-streams=preserve",
                    "--",
                    str(original_path),
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=False,
                check=False,
                preexec_fn=_apply_limits if os.name == "posix" else None,
            )
            if proc.returncode not in (0, 3) or not tmp_path.is_file():
                raise RuntimeError(f"qpdf_failed_rc_{proc.returncode}: {(proc.stderr or '')[:200]}")

            # Validação rigorosa pré-publicação
            ok, val_reason = _validate_pdf_derivative(original_path, tmp_path, qpdf_info["path"], timeout_s=30)
            if not ok:
                raise RuntimeError(f"validation_failed:{val_reason}")

            # Revalida fingerprint do original antes de promover atomicamente
            fp_after, _ = compute_fast_fingerprint(original_path)
            if fp_after != fp_before:
                raise RuntimeError("original_changed_during_processing")

            os.replace(tmp_path, target_path)
            deriv_size = target_path.stat().st_size
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

            # Remove derivados antigos deste mesmo livro (mantendo apenas target_path)
            for old in pdf_dir.glob(f"{safe_id}_*.pdf"):
                if old.resolve() != target_path and str(old.resolve()) not in _ACTIVE_FILES:
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
    Caminho quente do leitor (`/api/books/{id}/file`):
    Retorna `(path, stat, representation)` onde representation é `'optimized'` ou `'original'`.
    - Valida fingerprint rápido (`stat`) sem hash integral do PDF.
    - Se o original mudou ou o derivado estiver ausente/inválido, faz fallback transparente ao original.
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

    raw = f"{original_stat.st_mtime_ns}:{original_stat.st_size}:{original_stat.st_ino}".encode("utf-8")
    current_fp = hashlib.sha256(raw).hexdigest()[:12]
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


def invalidate_book_derivatives(book_id: str) -> int:
    """Remove derivados de um livro específico (nunca toca no original)."""
    pdf_dir = get_optimized_pdf_dir()
    removed = 0
    if pdf_dir.exists():
        safe_id = _sanitize_book_id(book_id)
        for f in pdf_dir.glob(f"{safe_id}_*"):
            if f.is_file() and str(f.resolve()) not in _ACTIVE_FILES:
                f.unlink(missing_ok=True)
                removed += 1
    with _STATE_LOCK:
        state = _load_state_unlocked()
        if book_id in state["items"]:
            state["items"][book_id]["state"] = "outdated"
            state["items"][book_id]["derivative_filename"] = None
            state["items"][book_id]["derivative_bytes"] = 0
            _save_state_unlocked(state)
    return removed


def cleanup_all_derivatives() -> Dict[str, Any]:
    """Remove todos os derivados regeneráveis e arquivos temporários sem tocar nos originais."""
    pdf_dir = get_optimized_pdf_dir()
    removed_files = 0
    freed_bytes = 0
    if pdf_dir.exists():
        for f in pdf_dir.iterdir():
            if f.is_file() and str(f.resolve()) not in _ACTIVE_FILES:
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink(missing_ok=True)
                    removed_files += 1
                except OSError:
                    pass
    with _STATE_LOCK:
        state = _load_state_unlocked()
        for item in state["items"].values():
            if isinstance(item, dict) and item.get("state") == "optimized":
                item["state"] = "outdated"
                item["derivative_filename"] = None
                item["derivative_bytes"] = 0
        _save_state_unlocked(state)
    return {"removed_files": removed_files, "freed_bytes": freed_bytes}


def pause_processing() -> Dict[str, Any]:
    global _PAUSED
    _PAUSED = True
    return {"paused": True}


def resume_processing() -> Dict[str, Any]:
    global _PAUSED, _CANCEL_REQUESTED
    _PAUSED = False
    _CANCEL_REQUESTED = False
    return {"paused": False}


def cancel_processing() -> Dict[str, Any]:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True
    return {"cancel_requested": True}


def get_status() -> Dict[str, Any]:
    """Retorna painel consolidado de métricas e configuração para a interface administrativa."""
    with _STATE_LOCK:
        state = _load_state_unlocked()
        cfg = dict(state["config"])
        items = state["items"]

    counts = {st: 0 for st in VALID_STATES}
    for v in items.values():
        if isinstance(v, dict):
            s = v.get("state", "pending")
            if s in counts:
                counts[s] += 1

    storage_bytes = get_storage_usage_bytes()
    can_run, run_reason = can_run_background_task(cfg)

    return {
        "config": cfg,
        "qpdf": get_qpdf_info(),
        "paused": _PAUSED,
        "can_run_now": can_run,
        "can_run_reason": run_reason,
        "counts": counts,
        "total_analyzed": len(items),
        "storage_used_bytes": storage_bytes,
        "storage_used_mb": round(storage_bytes / (1024 * 1024), 2),
        "storage_limit_mb": cfg.get("max_storage_mb", 1024),
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
            if prev and prev.get("source_fingerprint") == fp and prev.get("state") in ("optimized", "already_adequate"):
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
                "updated_at": now_iso,
            }
            if res["state"] == "pending":
                enqueued += 1
            elif res["state"] == "already_adequate":
                already_ok += 1
            else:
                ignored += 1
        _save_state_unlocked(state)

    return {"enqueued": enqueued, "already_adequate": already_ok, "ignored": ignored}
