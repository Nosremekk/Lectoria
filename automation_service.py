"""
Módulo de Automação e Fila Persistente de Tarefas do Lectoria (v1.5.0 — Biblioteca Viva).
Responsável por:
- Agendamento configurável e persistente no SQLite (`scheduled_jobs`) sobrevivendo a reinícios.
- Fila persistente de tarefas (`background_jobs`) com estados:
  pending, running, completed, failed, cancelled, interrupted.
- Recuperação de tarefas interrompidas no startup da aplicação.
- Monitoramento leve de modificações nos diretórios das fontes ativas (sem polling agressivo).
- Execução protegida contra concorrência de:
  1. scan_library (Escanear a biblioteca)
  2. enrich_metadata (Buscar metadados pendentes)
  3. update_missing_covers (Atualizar capas ausentes)
  4. rebuild_search_index (Reindexar busca FTS5)
  5. db_maintenance (Manutenção SQLite: PRAGMA optimize, ANALYZE, limpeza de sessões/caches)
  6. auto_backup (Backup automático com política de retenção)
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import backup_service
from background_enricher import enricher
import covers_service
from database import get_db
import library

logger = logging.getLogger("lectoria.automation")

VALID_JOB_TYPES = {
    "scan_library": "Escanear a Biblioteca",
    "enrich_metadata": "Buscar Metadados Pendentes",
    "update_missing_covers": "Atualizar Capas Ausentes",
    "rebuild_search_index": "Reindexar Busca Textual (FTS5)",
    "db_maintenance": "Manutenção do Banco SQLite",
    "auto_backup": "Backup Automático do Acervo",
}

VALID_SCHEDULE_MODES = {"manual", "interval", "daily", "weekly", "custom_days"}

_running_job_types: set = set()
_cancelled_job_ids: set = set()
_job_mutex = threading.Lock()
_dir_snapshots: Dict[int, float] = {}


def _get_tz(tz_name: Optional[str]) -> Any:
    try:
        return ZoneInfo(tz_name or "America/Sao_Paulo")
    except Exception:
        return timezone.utc


def compute_next_run_at(
    schedule_mode: str,
    interval_minutes: int = 360,
    run_time: str = "03:00",
    days_of_week: Optional[List[int]] = None,
    tz_name: str = "America/Sao_Paulo",
    from_dt: Optional[datetime] = None,
) -> Optional[str]:
    """
    Calcula o próximo instante de execução (ISO 8601 com timezone) com base na configuração.
    Retorna None se o modo for 'manual'.
    """
    if schedule_mode == "manual" or schedule_mode not in VALID_SCHEDULE_MODES:
        return None

    tz = _get_tz(tz_name)
    now = from_dt.astimezone(tz) if from_dt else datetime.now(tz)

    if schedule_mode == "interval":
        mins = max(5, int(interval_minutes or 60))
        nxt = now + timedelta(minutes=mins)
        return nxt.isoformat()

    # Parseia run_time "HH:MM"
    hour, minute = 3, 0
    if run_time and ":" in str(run_time):
        try:
            parts = str(run_time).strip().split(":")
            hour = max(0, min(23, int(parts[0])))
            minute = max(0, min(59, int(parts[1])))
        except Exception:
            hour, minute = 3, 0

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if schedule_mode == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    # weekly ou custom_days: days_of_week usa 0=Segunda .. 6=Domingo (padrão Python weekday())
    valid_days = []
    if days_of_week:
        for d in days_of_week:
            try:
                di = int(d)
                if 0 <= di <= 6:
                    valid_days.append(di)
            except Exception:
                pass
    if not valid_days:
        valid_days = [6]  # Domingo por padrão

    for offset in range(0, 8):
        test_dt = candidate + timedelta(days=offset)
        if test_dt.weekday() in valid_days and test_dt > now:
            return test_dt.isoformat()

    return (candidate + timedelta(days=7)).isoformat()


def recover_interrupted_jobs_on_startup() -> int:
    """
    Ao iniciar o servidor, identifica tarefas em `background_jobs` que ficaram
    com status 'running' ou 'pending' de uma execução anterior e as marca como 'interrupted'.
    Também recalcula `next_run_at` para agendamentos ativos que não possuem data futura.
    """
    interrupted_count = 0
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE background_jobs
                SET status = 'interrupted',
                    error_message = 'Interrompido por reinicialização do servidor.',
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('running', 'pending');
                """
            )
            interrupted_count = cursor.rowcount

            cursor.execute("SELECT * FROM scheduled_jobs WHERE is_enabled = 1;")
            schedules = [dict(r) for r in cursor.fetchall()]
            for sch in schedules:
                if sch["schedule_mode"] != "manual" and not sch.get("next_run_at"):
                    try:
                        days = json.loads(sch.get("days_of_week") or "[]")
                    except Exception:
                        days = []
                    nxt = compute_next_run_at(
                        schedule_mode=sch["schedule_mode"],
                        interval_minutes=sch.get("interval_minutes") or 360,
                        run_time=sch.get("run_time") or "03:00",
                        days_of_week=days,
                        tz_name=sch.get("timezone") or "America/Sao_Paulo",
                    )
                    cursor.execute(
                        "UPDATE scheduled_jobs SET next_run_at = ? WHERE job_type = ?;",
                        (nxt, sch["job_type"]),
                    )
    except Exception as e:
        logger.error("Erro ao recuperar tarefas interrompidas no startup: %s", e)

    if interrupted_count > 0:
        logger.info("Recuperadas %d tarefas interrompidas de execução anterior.", interrupted_count)
    return interrupted_count


def list_schedules() -> List[Dict[str, Any]]:
    """Lista todas as configurações de agendamento persistidas no banco."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scheduled_jobs ORDER BY id ASC;")
        rows = [dict(r) for r in cursor.fetchall()]

    for r in rows:
        r["label"] = VALID_JOB_TYPES.get(r["job_type"], r["job_type"])
        r["is_enabled"] = bool(r.get("is_enabled"))
        r["enabled"] = r["is_enabled"]
        try:
            r["days_of_week"] = json.loads(r.get("days_of_week") or "[]")
        except Exception:
            r["days_of_week"] = []
        try:
            r["config"] = json.loads(r.get("config_json") or "{}")
        except Exception:
            r["config"] = {}
    return rows


def update_schedule(job_type: str, payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """Atualiza a configuração de agendamento de um tipo de tarefa e recalcula `next_run_at`."""
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"Tipo de tarefa inválido: {job_type}")

    merged_payload: Dict[str, Any] = {}
    if payload:
        merged_payload.update(payload)
    if kwargs:
        merged_payload.update(kwargs)
    if "enabled" in merged_payload and "is_enabled" not in merged_payload:
        merged_payload["is_enabled"] = merged_payload["enabled"]
    payload = merged_payload

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scheduled_jobs WHERE job_type = ?;", (job_type,))
        existing = cursor.fetchone()
        if not existing:
            raise ValueError(f"Agendamento não encontrado: {job_type}")
        cur = dict(existing)

        schedule_mode = payload.get("schedule_mode", cur["schedule_mode"])
        if schedule_mode not in VALID_SCHEDULE_MODES:
            raise ValueError(f"Modo de agendamento inválido: {schedule_mode}")

        is_enabled = int(bool(payload.get("is_enabled", cur["is_enabled"])))
        if schedule_mode == "manual":
            is_enabled = 0

        interval_minutes = max(5, int(payload.get("interval_minutes", cur.get("interval_minutes") or 360)))
        run_time = str(payload.get("run_time", cur.get("run_time") or "03:00")).strip()
        days_of_week = payload.get("days_of_week")
        if days_of_week is None:
            try:
                days_of_week = json.loads(cur.get("days_of_week") or "[]")
            except Exception:
                days_of_week = []

        tz_name = str(payload.get("timezone", cur.get("timezone") or "America/Sao_Paulo")).strip()
        config_dict = payload.get("config")
        if config_dict is None:
            try:
                config_dict = json.loads(cur.get("config_json") or "{}")
            except Exception:
                config_dict = {}

        next_run_at = None
        if is_enabled and schedule_mode != "manual":
            next_run_at = compute_next_run_at(
                schedule_mode=schedule_mode,
                interval_minutes=interval_minutes,
                run_time=run_time,
                days_of_week=days_of_week,
                tz_name=tz_name,
            )

        cursor.execute(
            """
            UPDATE scheduled_jobs
            SET schedule_mode = ?,
                interval_minutes = ?,
                run_time = ?,
                days_of_week = ?,
                timezone = ?,
                is_enabled = ?,
                next_run_at = ?,
                config_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE job_type = ?;
            """,
            (
                schedule_mode,
                interval_minutes,
                run_time,
                json.dumps(days_of_week),
                tz_name,
                is_enabled,
                next_run_at,
                json.dumps(config_dict),
                job_type,
            ),
        )

    for s in list_schedules():
        if s["job_type"] == job_type:
            return s
    return {}


def list_jobs(limit: int = 50, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lista o histórico persistente de tarefas executadas ou em andamento."""
    with get_db() as conn:
        cursor = conn.cursor()
        if status_filter:
            cursor.execute(
                "SELECT * FROM background_jobs WHERE status = ? ORDER BY id DESC LIMIT ?;",
                (status_filter, max(1, min(limit, 200))),
            )
        else:
            cursor.execute(
                "SELECT * FROM background_jobs ORDER BY id DESC LIMIT ?;",
                (max(1, min(limit, 200)),),
            )
        rows = [dict(r) for r in cursor.fetchall()]

    for r in rows:
        r["label"] = VALID_JOB_TYPES.get(r["job_type"], r["job_type"])
        try:
            r["details"] = json.loads(r.get("details_json") or "{}")
        except Exception:
            r["details"] = {}
    return rows


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Obtém uma tarefa específica pelo ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM background_jobs WHERE id = ?;", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["label"] = VALID_JOB_TYPES.get(d["job_type"], d["job_type"])
        try:
            d["details"] = json.loads(d.get("details_json") or "{}")
        except Exception:
            d["details"] = {}
        return d


def cancel_job(job_id: int) -> Dict[str, Any]:
    """Solicita o cancelamento seguro de uma tarefa pendente ou em execução."""
    _cancelled_job_ids.add(job_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM background_jobs WHERE id = ?;", (job_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Tarefa #{job_id} não encontrada.")
        job = dict(row)
        if job["status"] in ("pending", "running"):
            cursor.execute(
                """
                UPDATE background_jobs
                SET status = 'cancelled',
                    error_message = 'Cancelado pelo administrador.',
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (job_id,),
            )
            if job["job_type"] == "enrich_metadata":
                enricher.cancel()
    return get_job(job_id) or {}


def create_job_record(job_type: str, trigger_source: str = "manual") -> int:
    """Cria um registro inicial de tarefa na tabela `background_jobs`."""
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"Tipo de tarefa desconhecido: {job_type}")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO background_jobs (job_type, status, trigger_source, started_at)
            VALUES (?, 'pending', ?, CURRENT_TIMESTAMP);
            """,
            (job_type, trigger_source),
        )
        return int(cursor.lastrowid)


def execute_job_sync(job_id: int) -> Dict[str, Any]:
    """
    Executa sincronamente uma tarefa registrada em `background_jobs`,
    garantindo exclusão mútua por `job_type`, atualização de progresso e persistência de resultados.
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Tarefa #{job_id} não existe.")

    job_type = job["job_type"]

    with _job_mutex:
        if job_id in _cancelled_job_ids or job["status"] == "cancelled":
            _cancelled_job_ids.discard(job_id)
            return get_job(job_id) or {}
        if job_type in _running_job_types:
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'failed',
                        error_message = 'Já existe uma execução ativa deste mesmo tipo de tarefa.',
                        finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?;
                    """,
                    (job_id,),
                )
            return get_job(job_id) or {}
        _running_job_types.add(job_type)

    with get_db() as conn:
        conn.execute(
            """
            UPDATE background_jobs
            SET status = 'running', started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (job_id,),
        )

    try:
        if job_type == "scan_library":
            res = _handle_scan_library(job_id)
        elif job_type == "enrich_metadata":
            res = _handle_enrich_metadata(job_id)
        elif job_type == "update_missing_covers":
            res = _handle_update_missing_covers(job_id)
        elif job_type == "rebuild_search_index":
            res = _handle_rebuild_search_index(job_id)
        elif job_type == "db_maintenance":
            res = _handle_db_maintenance(job_id)
        elif job_type == "auto_backup":
            res = _handle_auto_backup(job_id)
        else:
            raise ValueError(f"Handler não implementado para {job_type}")

        if job_id in _cancelled_job_ids:
            _cancelled_job_ids.discard(job_id)
            return get_job(job_id) or {}

        with get_db() as conn:
            conn.execute(
                """
                UPDATE background_jobs
                SET status = 'completed',
                    total_items = ?,
                    processed_items = ?,
                    success_items = ?,
                    review_items = ?,
                    not_found_items = ?,
                    failed_items = ?,
                    progress_pct = 100.0,
                    details_json = ?,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != 'cancelled';
                """,
                (
                    res.get("total", 0),
                    res.get("processed", 0),
                    res.get("success", 0),
                    res.get("review", 0),
                    res.get("not_found", 0),
                    res.get("failed", 0),
                    json.dumps(res),
                    job_id,
                ),
            )
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET last_run_at = CURRENT_TIMESTAMP,
                    last_status = 'completed'
                WHERE job_type = ?;
                """,
                (job_type,),
            )

    except Exception as e:
        logger.error("Erro na tarefa #%d (%s): %s", job_id, job_type, e)
        with get_db() as conn:
            conn.execute(
                """
                UPDATE background_jobs
                SET status = 'failed',
                    error_message = ?,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (str(e)[:500], job_id),
            )
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET last_run_at = CURRENT_TIMESTAMP,
                    last_status = 'failed'
                WHERE job_type = ?;
                """,
                (job_type,),
            )
    finally:
        with _job_mutex:
            _running_job_types.discard(job_type)
            _cancelled_job_ids.discard(job_id)

    return get_job(job_id) or {}


def run_job(job_type: str, trigger_source: str = "manual", run_async: bool = False) -> Dict[str, Any]:
    """Cria e executa uma tarefa imediatamente (síncrona ou em thread background)."""
    job_id = create_job_record(job_type, trigger_source=trigger_source)
    if run_async:
        t = threading.Thread(target=execute_job_sync, args=(job_id,), daemon=True)
        t.start()
        return get_job(job_id) or {}
    return execute_job_sync(job_id)


def _handle_scan_library(job_id: int) -> Dict[str, Any]:
    stats = library.scan_library()
    total = stats.get("total", 0)
    added = stats.get("added", 0)
    updated = stats.get("updated", 0)
    missing = stats.get("missing", 0)
    return {
        "total": total,
        "processed": total,
        "success": added + updated,
        "review": 0,
        "not_found": missing,
        "failed": 0,
        "scan_stats": stats,
    }


def _handle_enrich_metadata(job_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM books
            WHERE is_missing = 0 AND (
                identification_status IS NULL
                OR identification_status IN ('pending', 'failed', 'not_found', 'rate_limited', 'technical_error')
            )
            ORDER BY created_at DESC;
            """
        )
        book_ids = [r["id"] for r in cursor.fetchall()]

    total = len(book_ids)
    processed = 0
    identified = 0
    review = 0
    not_found = 0
    failed = 0

    for bid in book_ids:
        if job_id in _cancelled_job_ids:
            break
        try:
            enricher._enrich_single_book_sync(bid)
            processed += 1
            with get_db() as conn:
                st_row = conn.execute(
                    "SELECT identification_status FROM books WHERE id = ?;", (bid,)
                ).fetchone()
                st = st_row["identification_status"] if st_row else "not_found"
                if st == "identified":
                    identified += 1
                elif st in ("needs_review", "ambiguous"):
                    review += 1
                elif st in ("rate_limited", "technical_error"):
                    failed += 1
                else:
                    not_found += 1
        except Exception as e:
            logger.debug("Falha ao enriquecer %s no job #%d: %s", bid, job_id, e)
            processed += 1
            failed += 1

        pct = round((processed / total) * 100.0, 1) if total > 0 else 100.0
        with get_db() as conn:
            conn.execute(
                """
                UPDATE background_jobs
                SET total_items = ?, processed_items = ?, success_items = ?,
                    review_items = ?, not_found_items = ?, failed_items = ?,
                    progress_pct = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (total, processed, identified, review, not_found, failed, pct, job_id),
            )

    return {
        "total": total,
        "processed": processed,
        "success": identified,
        "review": review,
        "not_found": not_found,
        "failed": failed,
    }


def _handle_update_missing_covers(job_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM books
            WHERE is_missing = 0 AND cover_type != 'custom';
            """
        )
        book_ids = [r["id"] for r in cursor.fetchall()]

    total = len(book_ids)
    processed = 0
    extracted_or_valid = 0
    fallback_count = 0
    failed = 0

    for bid in book_ids:
        if job_id in _cancelled_job_ids:
            break
        try:
            cov = covers_service.get_or_create_book_cover(bid)
            processed += 1
            if cov.get("cover_type") in ("extracted", "custom"):
                extracted_or_valid += 1
            else:
                fallback_count += 1
        except Exception:
            processed += 1
            failed += 1

    return {
        "total": total,
        "processed": processed,
        "success": extracted_or_valid,
        "review": fallback_count,
        "not_found": 0,
        "failed": failed,
    }


def _handle_rebuild_search_index(job_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM books_fts;")
        cursor.execute(
            """
            SELECT b.id, b.title, b.author, b.description, b.publisher, b.isbn13,
                   GROUP_CONCAT(s.name, ' ') as subjects_str
            FROM books b
            LEFT JOIN book_subjects bs ON b.id = bs.book_id
            LEFT JOIN subjects s ON bs.subject_id = s.id
            WHERE b.is_missing = 0
            GROUP BY b.id;
            """
        )
        rows = cursor.fetchall()
        total = len(rows)
        for r in rows:
            subj = f"{r['subjects_str'] or ''} {r['isbn13'] or ''}".strip()
            library.update_book_fts(
                cursor,
                r["id"],
                r["title"],
                r["author"],
                r["description"],
                r["publisher"],
                subj,
            )
    return {
        "total": total,
        "processed": total,
        "success": total,
        "review": 0,
        "not_found": 0,
        "failed": 0,
    }


def _handle_db_maintenance(job_id: int) -> Dict[str, Any]:
    expired_sessions = 0
    expired_cache = 0
    integrity = "ok"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE expires_at <= CURRENT_TIMESTAMP;")
        expired_sessions = cursor.rowcount
        cursor.execute("DELETE FROM metadata_api_cache WHERE expires_at <= CURRENT_TIMESTAMP;")
        expired_cache = cursor.rowcount
        cursor.execute("PRAGMA optimize;")
        cursor.execute("ANALYZE;")
        cursor.execute("PRAGMA integrity_check;")
        row = cursor.fetchone()
        integrity = row[0] if row else "unknown"

    return {
        "total": 1,
        "processed": 1,
        "success": 1 if integrity == "ok" else 0,
        "review": 0,
        "not_found": 0,
        "failed": 0 if integrity == "ok" else 1,
        "expired_sessions_removed": expired_sessions,
        "expired_cache_removed": expired_cache,
        "integrity_check": integrity,
    }


def _handle_auto_backup(job_id: int) -> Dict[str, Any]:
    retention_count = 7
    with get_db() as conn:
        row = conn.execute(
            "SELECT config_json FROM scheduled_jobs WHERE job_type = 'auto_backup';"
        ).fetchone()
        if row and row["config_json"]:
            try:
                cfg = json.loads(row["config_json"])
                retention_count = max(1, int(cfg.get("keep_count", 7)))
            except Exception:
                retention_count = 7

    binfo = backup_service.create_backup(custom_name="auto")
    pruned = backup_service.prune_backups(keep_count=retention_count)
    return {
        "total": 1,
        "processed": 1,
        "success": 1,
        "review": 0,
        "not_found": 0,
        "failed": 0,
        "backup": binfo,
        "pruned_old_backups": pruned,
    }


def check_and_run_due_schedules(now_utc: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Verifica quais agendamentos habilitados atingiram `next_run_at` e os executa,
    atualizando imediatamente o próximo `next_run_at`.
    """
    now = now_utc or datetime.now(timezone.utc)
    triggered = []

    schedules = list_schedules()
    for sch in schedules:
        if not sch.get("is_enabled") or sch.get("schedule_mode") == "manual":
            continue
        nxt_str = sch.get("next_run_at")
        if not nxt_str:
            continue
        try:
            nxt_dt = datetime.fromisoformat(nxt_str)
            if nxt_dt.tzinfo is None:
                nxt_dt = nxt_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if now >= nxt_dt.astimezone(timezone.utc):
            jtype = sch["job_type"]
            # Calcula e grava o próximo horário antes de disparar
            new_next = compute_next_run_at(
                schedule_mode=sch["schedule_mode"],
                interval_minutes=sch.get("interval_minutes") or 360,
                run_time=sch.get("run_time") or "03:00",
                days_of_week=sch.get("days_of_week") or [],
                tz_name=sch.get("timezone") or "America/Sao_Paulo",
                from_dt=now,
            )
            with get_db() as conn:
                conn.execute(
                    "UPDATE scheduled_jobs SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP WHERE job_type = ?;",
                    (new_next, jtype),
                )
            job_res = run_job(jtype, trigger_source="scheduler", run_async=False)
            triggered.append(job_res)

    return triggered


def check_directory_changes_and_auto_scan() -> Optional[Dict[str, Any]]:
    """
    Monitora de forma leve o maior mtime dos diretórios das fontes ativas.
    Se detectar mudança nos diretórios desde a última verificação, dispara `scan_library` automaticamente.
    """
    changed = False
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, path FROM library_sources WHERE is_active = 1;"
        ).fetchall()

    for r in rows:
        sid = r["id"]
        spath = Path(r["path"])
        if not spath.exists() or not spath.is_dir():
            continue
        try:
            max_mtime = spath.stat().st_mtime
            for root, dirs, _ in os.walk(spath):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                try:
                    mt = Path(root).stat().st_mtime
                    if mt > max_mtime:
                        max_mtime = mt
                except OSError:
                    pass
            prev = _dir_snapshots.get(sid)
            _dir_snapshots[sid] = max_mtime
            if prev is not None and max_mtime > prev:
                changed = True
        except Exception:
            pass

    if changed:
        logger.info("Alteração detectada nos diretórios da biblioteca; disparando scan_library automático.")
        return run_job("scan_library", trigger_source="fs_watcher", run_async=False)
    return None


class AutomationSchedulerLoop:
    """Loop assíncrono leve em background para verificar agendamentos vencidos e mudanças de pasta."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run_loop())
        except RuntimeError:
            pass

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        # Inicializa snapshot inicial dos diretórios sem disparar scan
        await asyncio.to_thread(check_directory_changes_and_auto_scan)
        while self._running:
            try:
                await asyncio.sleep(30.0)
                if not self._running:
                    break
                await asyncio.to_thread(check_and_run_due_schedules)
                await asyncio.to_thread(check_directory_changes_and_auto_scan)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Erro no loop do agendador: %s", e)


scheduler_loop = AutomationSchedulerLoop()
