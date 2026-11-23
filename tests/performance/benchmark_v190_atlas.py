#!/usr/bin/env python3
"""
Suíte reproduzível de benchmarks sintéticos e auditoria SQLite para a Fase 8 (Lectoria v1.9.0).

Executa isoladamente em diretório temporário (dados 100% sintéticos, sem tocar bancos ou VPS reais):
- Metadados de ambiente, compilação SQLite (FTS5/WAL) e PRAGMAs efetivos;
- Uso de memória diferenciando VmRSS / VmHWM (processo OS) e tracemalloc (objetos Python);
- Latências (mediana, p95, p99), throughput, CPU time e tamanho de payload nos cenários:
  * Inicialização e biblioteca vazia;
  * 100 e 1.000 livros (get_books, get_discovery_shelves, get_book);
  * 1.000 e 10.000 conceitos + 1.000 reflexões + 10.000 anotações + 50.000 conexões;
  * Pesquisa FTS5 (diacríticos PT-BR, múltiplos termos, paginação, fallback relacional);
  * Grafo local (1 e 2 saltos, max_nodes=80, max_edges=160);
  * Exportações Markdown ZIP, JSON v2.0 e CSV (tempo, RSS, tracemalloc, WAL);
  * Gravação concorrente de progresso de leitura + pesquisa + grafo + exportação (WAL);
  * Servidor Uvicorn real (--workers 1): bloqueio do event loop e concorrência (BoundedSemaphore);
  * Auditoria EXPLAIN QUERY PLAN das consultas críticas + custo unitário dos triggers FTS5.

Uso:
    ./.venv/bin/python tests/performance/benchmark_v190_atlas.py --output-json /tmp/bench.json
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def read_proc_status(pid: Optional[int] = None) -> Dict[str, Any]:
    """Lê VmRSS, VmHWM, VmSize e Threads reais de /proc/<pid>/status no Linux."""
    target = Path(f"/proc/{pid}/status" if pid else "/proc/self/status")
    info: Dict[str, Any] = {"vm_rss_mb": None, "vm_hwm_mb": None, "vm_size_mb": None, "threads": None}
    if not target.exists():
        usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        info["vm_hwm_mb"] = round(usage_kb / 1024.0, 2)
        return info
    try:
        for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                info["vm_rss_mb"] = round(int(line.split()[1]) / 1024.0, 2)
            elif line.startswith("VmHWM:"):
                info["vm_hwm_mb"] = round(int(line.split()[1]) / 1024.0, 2)
            elif line.startswith("VmSize:"):
                info["vm_size_mb"] = round(int(line.split()[1]) / 1024.0, 2)
            elif line.startswith("Threads:"):
                info["threads"] = int(line.split()[1])
    except Exception:
        pass
    return info


def percentile(sorted_vals: List[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return round(sorted_vals[int(idx)], 3)
    val = sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)
    return round(val, 3)


def measure_op(
    fn: Callable[[], Any],
    iterations: int = 25,
    warmup: int = 2,
) -> Dict[str, Any]:
    """Executa uma operação repetidamente e retorna estatísticas de latência, CPU, payload e memória."""
    for _ in range(warmup):
        res = fn()

    latencies_ms: List[float] = []
    cpu_times_ms: List[float] = []
    errors = 0
    last_result: Any = None

    tracemalloc.start()
    try:
        for _ in range(iterations):
            t0 = time.perf_counter()
            c0 = time.process_time()
            try:
                last_result = fn()
            except Exception:
                errors += 1
            c1 = time.process_time()
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)
            cpu_times_ms.append((c1 - c0) * 1000.0)
        tm_cur, tm_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    s_lat = sorted(latencies_ms)
    total_wall_s = sum(latencies_ms) / 1000.0
    payload_bytes: Optional[int] = None
    if last_result is not None:
        try:
            payload_bytes = len(json.dumps(last_result, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            payload_bytes = None

    return {
        "iterations": iterations,
        "errors": errors,
        "min_ms": round(s_lat[0], 3) if s_lat else None,
        "median_ms": percentile(s_lat, 50.0),
        "p95_ms": percentile(s_lat, 95.0),
        "p99_ms": percentile(s_lat, 99.0) if len(s_lat) >= 20 else None,
        "max_ms": round(s_lat[-1], 3) if s_lat else None,
        "cpu_median_ms": percentile(sorted(cpu_times_ms), 50.0),
        "throughput_ops_s": round(iterations / total_wall_s, 2) if total_wall_s > 0 else None,
        "payload_bytes": payload_bytes,
        "tracemalloc_current_mb": round(tm_cur / (1024 * 1024), 3),
        "tracemalloc_peak_mb": round(tm_peak / (1024 * 1024), 3),
        "proc_mem_after": read_proc_status(),
    }


def count_sql_statements(db_path: Path, fn: Callable[[], Any]) -> Dict[str, Any]:
    """Conta statements SQL executados usando sqlite3.Connection.set_trace_callback via hook temporário."""
    import database

    orig_get_conn = database.get_connection
    counts = {"connections": 0, "statements": 0}
    lock = threading.Lock()

    def wrapped_get_conn(p=None):
        conn = orig_get_conn(p)
        with lock:
            counts["connections"] += 1

        def tracer(_stmt: str):
            with lock:
                counts["statements"] += 1

        conn.set_trace_callback(tracer)
        return conn

    database.get_connection = wrapped_get_conn
    try:
        res = fn()
    finally:
        database.get_connection = orig_get_conn
    return {"connections": counts["connections"], "statements": counts["statements"], "result_summary": _summarize_result(res)}


def _summarize_result(res: Any) -> Any:
    if isinstance(res, dict):
        out: Dict[str, Any] = {}
        for k in ("total", "count", "node_count", "edge_count", "truncated", "format"):
            if k in res:
                out[k] = res[k]
        if "items" in res and isinstance(res["items"], list):
            out["items_len"] = len(res["items"])
        if "highlights" in res:
            out["highlights_len"] = len(res["highlights"])
        if "notes" in res:
            out["notes_len"] = len(res["notes"])
        return out
    if isinstance(res, list):
        return {"list_len": len(res)}
    return None


def collect_environment_info() -> Dict[str, Any]:
    cpu_model = platform.processor() or "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for ln in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "model name" in ln:
                cpu_model = ln.split(":", 1)[1].strip()
                break

    mem_total_mb = None
    mem_avail_mb = None
    swap_total_mb = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for ln in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("MemTotal:"):
                mem_total_mb = round(int(ln.split()[1]) / 1024.0, 1)
            elif ln.startswith("MemAvailable:"):
                mem_avail_mb = round(int(ln.split()[1]) / 1024.0, 1)
            elif ln.startswith("SwapTotal:"):
                swap_total_mb = round(int(ln.split()[1]) / 1024.0, 1)

    c = sqlite3.connect(":memory:")
    compile_opts = [r[0] for r in c.execute("PRAGMA compile_options;").fetchall()]
    c.close()

    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": cpu_model,
        "cpu_cores_logical": os.cpu_count(),
        "ram_total_mb": mem_total_mb,
        "ram_available_mb": mem_avail_mb,
        "swap_total_mb": swap_total_mb,
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_fts5_enabled": "ENABLE_FTS5" in compile_opts,
        "sqlite_relevant_compile_options": [
            o for o in compile_opts
            if any(k in o for k in ("FTS", "WAL", "THREAD", "TEMP", "MMAP", "MAX_VARIABLE"))
        ],
    }


def _db_file_sizes(db_path: Path) -> Dict[str, int]:
    sizes = {}
    for suffix, key in (("", "db_bytes"), ("-wal", "wal_bytes"), ("-shm", "shm_bytes")):
        p = db_path.with_name(db_path.name + suffix)
        sizes[key] = p.stat().st_size if p.exists() else 0
    return sizes


def _seed_books_batch(cur: sqlite3.Cursor, books_dir: Path, start_idx: int, end_idx: int, user_id: int, now_iso: str) -> None:
    cur.execute(
        """
        INSERT INTO library_sources (id, name, path, is_active) VALUES (1, 'Biblioteca Principal', ?, 1)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, path = excluded.path, is_active = 1;
        """,
        (str(books_dir),),
    )
    book_rows = []
    prog_rows = []
    fts_rows = []
    for i in range(start_idx, end_idx):
        bid = f"bench_book_{i:04d}"
        fmt = "epub" if (i % 5 == 0) else "pdf"
        fname = f"{bid}.{fmt}"
        fpath = books_dir / fname
        if not fpath.exists():
            fpath.write_bytes(b"%PDF-1.4\n%%EOF\n" if fmt == "pdf" else b"PK\x05\x06" + b"\x00" * 18)
        st = fpath.stat()
        fsize = st.st_size
        fhash = f"{fsize}:{int(st.st_mtime)}"
        title = f"Obra Filosófica e Técnica Volume {i:04d}"
        author = f"Autor Sintético {(i % 40):02d}"
        desc = f"Estudo detalhado sobre epistemologia, técnica e método livro {i}."
        book_rows.append((
            bid, 1, title, author, fname, fname, fsize, 180, fmt, 0,
            f"Editora {(i % 10)}", 1950 + (i % 75), "pt", desc, "identified", 90.0, "embedded", "[]",
            fhash, now_iso, now_iso,
        ))
        fts_rows.append((bid, title, author, desc, f"Editora {(i % 10)}", "Filosofia Técnica"))
        if i % 3 == 0:
            cur_pg = (i % 170) + 1
            prog_rows.append((user_id, bid, cur_pg, 180, round((cur_pg / 180.0) * 100.0, 1), now_iso))

    cur.executemany(
        """
        INSERT OR IGNORE INTO books (
            id, source_id, title, author, filename, rel_path, filesize, page_count, format, is_missing,
            publisher, publish_year, language, description, identification_status, identification_score,
            metadata_source, manual_lock_fields, file_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        book_rows,
    )
    cur.executemany(
        """
        INSERT OR IGNORE INTO books_fts (book_id, title, author, description, publisher, subjects)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        fts_rows,
    )
    cur.executemany(
        """
        INSERT OR IGNORE INTO reading_progress (user_id, book_id, current_page, total_pages, percentage, updated_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        prog_rows,
    )


def _bulk_seed_atlas(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    other_user_id: int,
    n_concepts: int,
    n_reflections: int,
    n_highlights: int,
    n_notes: int,
    n_links: int,
    n_books: int,
    now_iso: str,
) -> None:
    """
    Popula volumes grandes (até 10k conceitos / 50k conexões) e sincroniza knowledge_fts em lote.
    Os triggers de FTS são substituídos durante a carga em massa e restaurados integralmente em seguida,
    pois o custo unitário de triggers é medido separadamente em função própria.
    """
    cur = conn.cursor()
    # Salva definições dos triggers para restaurar após bulk insert
    cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%';"
    )
    saved_triggers = [(r["name"], r["sql"]) for r in cur.fetchall() if r["sql"]]
    for name, _ in saved_triggers:
        cur.execute(f"DROP TRIGGER IF EXISTS {name};")

    # Conceitos do usuário principal + 50 conceitos do outro usuário (testar filtragem multiusuário)
    c_rows = []
    for i in range(n_concepts):
        suffix = " TecnicaCritica" if (i % 100 == 0) else ""
        c_rows.append((
            f"bc_c_{i:05d}",
            user_id,
            f"Conceito Técnico {i:05d}{suffix}",
            f"conceito técnico {i:05d}{suffix.lower()}",
            f"Descrição analítica sobre técnica, racionalização e método capítulo {i}.",
            '["filosofia", "técnica"]' if i % 2 == 0 else '["história"]',
            "#d4a853",
            0,
            now_iso,
            now_iso,
        ))
    for j in range(50):
        c_rows.append((
            f"bc_other_c_{j:03d}",
            other_user_id,
            f"Conceito Privado Outro {j} TecnicaCritica",
            f"conceito privado outro {j} tecnicacritica",
            "Texto privado alheio sobre técnica que jamais deve vazar.",
            '["privado"]',
            "#888888",
            0,
            now_iso,
            now_iso,
        ))
    cur.executemany(
        """
        INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        c_rows,
    )

    # Reflexões com corpo Markdown realista (~1.2 KB cada) e primary_book_id válido
    body_template = (
        "## Síntese Analítica\n\n"
        "A transformação técnica descrita nesta passagem evidencia o deslocamento do método "
        "para a eficiência instrumental, articulando teoria do conhecimento, sociologia e crítica histórica. "
    ) * 6
    r_rows = []
    for i in range(n_reflections):
        suffix = " TecnicaCritica" if (i % 25 == 0) else ""
        r_rows.append((
            f"bc_r_{i:05d}",
            user_id,
            f"Reflexão Sobre Técnica e Método {i:04d}{suffix}",
            body_template + f"\n\nReferência interna #{i}.",
            f"bench_book_{i % n_books:04d}",
            '["ensaio", "técnica"]',
            1 if i < 5 else 0,
            0,
            now_iso,
            now_iso,
        ))
    cur.executemany(
        """
        INSERT INTO reflections (id, user_id, title, body_markdown, primary_book_id, tags_json, is_pinned, is_archived, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        r_rows,
    )

    # Destaques e notas (concentrando 250 destaques + 250 notas no bench_book_0000 para testar leitor pesado)
    h_rows = []
    for i in range(n_highlights):
        bid = "bench_book_0000" if i < 250 else f"bench_book_{i % n_books:04d}"
        suffix = " TecnicaCritica" if (i % 120 == 0) else ""
        h_rows.append((
            f"bc_h_{i:05d}",
            bid,
            user_id,
            (i % 150) + 1,
            f"Trecho destacado número {i} sobre técnica moderna e racionalidade{suffix}.",
            "Contexto ao redor do trecho destacado.",
            "[]",
            "yellow",
            "private",
            None,
            f"Cap. {(i % 12) + 1}",
            now_iso,
            now_iso,
        ))
    cur.executemany(
        """
        INSERT INTO highlights (id, book_id, user_id, page, selected_text, text_context, rects_json, color, visibility, epub_cfi, chapter_label, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        h_rows,
    )

    n_rows = []
    for i in range(n_notes):
        bid = "bench_book_0000" if i < 250 else f"bench_book_{i % n_books:04d}"
        suffix = " TecnicaCritica" if (i % 120 == 0) else ""
        n_rows.append((
            f"bc_n_{i:05d}",
            bid,
            user_id,
            (i % 150) + 1,
            f"bc_h_{i:05d}" if i < n_highlights else None,
            f"Nota analítica {i} comentando implicações filosóficas da técnica{suffix}.",
            "blue",
            "private",
            None,
            f"Cap. {(i % 12) + 1}",
            now_iso,
            now_iso,
        ))
    cur.executemany(
        """
        INSERT INTO notes (id, book_id, user_id, page, highlight_id, content, color, visibility, epub_cfi, chapter_label, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        n_rows,
    )

    # Conexões semânticas (hub bc_c_00000 com 150 vizinhos de 1º salto + restante distribuído)
    l_rows = []
    hub_count = min(150, max(10, n_concepts - 1), n_links)
    for i in range(hub_count):
        l_rows.append((
            f"bc_l_hub_{i:05d}",
            user_id,
            "concept",
            "bc_c_00000",
            "concept",
            f"bc_c_{i + 1:05d}",
            "grounds",
            f"Fundamentação direta do conceito {i + 1}.",
            now_iso,
            now_iso,
        ))
    rem = n_links - hub_count
    rel_types = ("complements", "develops", "references", "exemplifies", "relates_to")
    for i in range(rem):
        src_t = "reflection" if (i % 2 == 0 and n_reflections > 0) else "concept"
        src_id = f"bc_r_{i % n_reflections:05d}" if src_t == "reflection" else f"bc_c_{i % n_concepts:05d}"
        tgt_idx = (i + 1 + (i // max(1, n_concepts))) % n_concepts
        if src_t == "concept" and f"bc_c_{tgt_idx:05d}" == src_id:
            tgt_idx = (tgt_idx + 1) % n_concepts
        l_rows.append((
            f"bc_l_{i:06d}",
            user_id,
            src_t,
            src_id,
            "concept",
            f"bc_c_{tgt_idx:05d}",
            rel_types[i % len(rel_types)],
            "Conexão metodológica registrada no Atlas." if i % 10 == 0 else "",
            now_iso,
            now_iso,
        ))
    cur.executemany(
        """
        INSERT OR IGNORE INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        l_rows,
    )

    # Popula FTS5 em lote único e restaura triggers
    cur.execute("DELETE FROM knowledge_fts;")
    cur.execute(
        """
        INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
        SELECT 'concept', id, CAST(user_id AS TEXT), '', name, description, tags_json FROM concepts;
        """
    )
    cur.execute(
        """
        INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
        SELECT 'reflection', id, CAST(user_id AS TEXT), COALESCE(primary_book_id, ''), title, body_markdown, tags_json FROM reflections;
        """
    )
    cur.execute(
        """
        INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
        SELECT 'highlight', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''), COALESCE(selected_text, '') || ' ' || COALESCE(text_context, ''), '' FROM highlights;
        """
    )
    cur.execute(
        """
        INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
        SELECT 'note', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''), COALESCE(content, ''), '' FROM notes;
        """
    )
    cur.execute(
        """
        INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
        SELECT 'link', id, CAST(user_id AS TEXT), '', relation_type, rationale, '' FROM knowledge_links WHERE length(trim(COALESCE(rationale, ''))) > 0;
        """
    )
    for _, trig_sql in saved_triggers:
        cur.execute(trig_sql)
    cur.execute("PRAGMA optimize;")
    conn.commit()


def run_trigger_overhead_benchmark(tmp_dir: Path) -> Dict[str, Any]:
    """Mede o custo real de INSERT / UPDATE / DELETE unitário no SQLite com triggers FTS5 v13 ativos."""
    results: Dict[str, Any] = {}
    for rows_count in (1000, 10000, 50000):
        db_file = tmp_dir / f"trig_{rows_count}.db"
        if db_file.exists():
            db_file.unlink()
        conn = sqlite3.connect(str(db_file), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            "CREATE TABLE items (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT, body TEXT);"
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE items_fts USING fts5(
                entity_type UNINDEXED,
                entity_id UNINDEXED,
                user_id UNINDEXED,
                title,
                body,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        # Popula FTS sem triggers primeiro para simular tamanho real da tabela
        batch = [(f"it_{i:06d}", 1, f"Conceito {i}", f"Corpo descritivo item {i}") for i in range(rows_count)]
        conn.executemany("INSERT INTO items (id, user_id, title, body) VALUES (?, ?, ?, ?);", batch)
        conn.executemany(
            "INSERT INTO items_fts (entity_type, entity_id, user_id, title, body) VALUES ('concept', ?, ?, ?, ?);",
            batch,
        )
        conn.commit()
        # Instala exatamente o padrão do trigger da v13 (DELETE WHERE entity_type=? AND entity_id=? em UNINDEXED)
        conn.execute(
            """
            CREATE TRIGGER trg_items_ai AFTER INSERT ON items BEGIN
                DELETE FROM items_fts WHERE entity_type = 'concept' AND entity_id = NEW.id;
                INSERT INTO items_fts (entity_type, entity_id, user_id, title, body)
                VALUES ('concept', NEW.id, CAST(NEW.user_id AS TEXT), NEW.title, NEW.body);
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER trg_items_au AFTER UPDATE ON items BEGIN
                DELETE FROM items_fts WHERE entity_type = 'concept' AND entity_id = NEW.id;
                INSERT INTO items_fts (entity_type, entity_id, user_id, title, body)
                VALUES ('concept', NEW.id, CAST(NEW.user_id AS TEXT), NEW.title, NEW.body);
            END;
            """
        )
        conn.commit()

        ins_times = []
        upd_times = []
        for k in range(15):
            uid_s = f"probe_{k}"
            t0 = time.perf_counter()
            conn.execute("INSERT INTO items (id, user_id, title, body) VALUES (?, 1, 'Novo Conceito', 'Texto teste');", (uid_s,))
            conn.commit()
            ins_times.append((time.perf_counter() - t0) * 1000.0)

            t0 = time.perf_counter()
            conn.execute("UPDATE items SET title = 'Conceito Atualizado' WHERE id = ?;", (uid_s,))
            conn.commit()
            upd_times.append((time.perf_counter() - t0) * 1000.0)

        conn.close()
        for suf in ("", "-wal", "-shm"):
            p = db_file.with_name(db_file.name + suf)
            if p.exists():
                p.unlink()
        results[str(rows_count)] = {
            "insert_with_unindexed_delete_median_ms": percentile(sorted(ins_times), 50.0),
            "insert_with_unindexed_delete_p95_ms": percentile(sorted(ins_times), 95.0),
            "update_with_unindexed_delete_median_ms": percentile(sorted(upd_times), 50.0),
            "update_with_unindexed_delete_p95_ms": percentile(sorted(upd_times), 95.0),
        }
    return results


def run_explain_audit(conn: sqlite3.Connection, user_id: int) -> Dict[str, List[Dict[str, Any]]]:
    import knowledge_service

    queries = {
        "1_books_listing": (
            """
            SELECT b.id, b.title, s.name, p.current_page, br.rating
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p ON b.id = p.book_id AND p.user_id = ?
            LEFT JOIN book_reviews br ON b.id = br.book_id AND br.user_id = ?
            WHERE b.is_missing = 0 AND (s.is_active = 1 OR s.is_active IS NULL)
            ORDER BY b.title COLLATE NOCASE ASC;
            """,
            (user_id, user_id),
        ),
        "2_book_detail": (
            """
            SELECT b.id, b.title, s.name, p.current_page, br.rating
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p ON b.id = p.book_id AND p.user_id = ?
            LEFT JOIN book_reviews br ON b.id = br.book_id AND br.user_id = ?
            WHERE b.id = ?;
            """,
            (user_id, user_id, "bench_book_0000"),
        ),
        "3_highlights_and_notes": (
            """
            SELECT h.id, h.page, h.selected_text FROM highlights h
            WHERE h.book_id = ? AND (h.user_id = ? OR h.visibility = 'public')
            ORDER BY h.page ASC, h.created_at ASC;
            """,
            ("bench_book_0000", user_id),
        ),
        "4_atlas_summary_counts": (
            """
            SELECT COUNT(*) FROM concepts WHERE user_id = ? AND is_archived = 0;
            """,
            (user_id,),
        ),
        "5_concepts_listing": (
            """
            SELECT * FROM concepts c
            WHERE c.user_id = ? AND c.is_archived = 0
            ORDER BY c.updated_at DESC, c.id ASC LIMIT 50 OFFSET 0;
            """,
            (user_id,),
        ),
        "6_reflections_listing": (
            """
            SELECT * FROM reflections r
            WHERE r.user_id = ? AND r.is_archived = 0
            ORDER BY r.is_pinned DESC, r.updated_at DESC, r.id ASC LIMIT 50 OFFSET 0;
            """,
            (user_id,),
        ),
        "7_fts5_search": (
            """
            SELECT kf.entity_type, kf.entity_id
            FROM knowledge_fts kf
            WHERE knowledge_fts MATCH '"tecnica"*'
              AND (kf.entity_type = 'concept' AND EXISTS (
                  SELECT 1 FROM concepts c WHERE c.id = kf.entity_id AND c.user_id = ? AND c.is_archived = 0
              ))
            LIMIT 50;
            """,
            (user_id,),
        ),
        "8a_graph_hop1_or": (
            """
            SELECT * FROM knowledge_links
            WHERE user_id = ?
              AND relation_type IN ('complements','contradicts','develops','exemplifies','grounds','questions','references','relates_to')
              AND ((source_type = 'concept' AND source_id = 'bc_c_00000')
                   OR (target_type = 'concept' AND target_id = 'bc_c_00000'))
            ORDER BY created_at DESC, id ASC;
            """,
            (user_id,),
        ),
        "8b_graph_hop1_union_all": (
            """
            SELECT * FROM (
                SELECT * FROM knowledge_links
                WHERE user_id = ? AND source_type = 'concept' AND source_id = 'bc_c_00000'
                  AND relation_type IN ('complements','contradicts','develops','exemplifies','grounds','questions','references','relates_to')
                UNION ALL
                SELECT * FROM knowledge_links
                WHERE user_id = ? AND target_type = 'concept' AND target_id = 'bc_c_00000'
                  AND relation_type IN ('complements','contradicts','develops','exemplifies','grounds','questions','references','relates_to')
            ) ORDER BY created_at DESC, id ASC;
            """,
            (user_id, user_id),
        ),
        "8c_atlas_summary_recent_concepts_or": (
            """
            SELECT c.id,
                   (SELECT COUNT(*) FROM knowledge_links kl
                    WHERE kl.user_id = c.user_id
                      AND ((kl.source_type = 'concept' AND kl.source_id = c.id)
                           OR (kl.target_type = 'concept' AND kl.target_id = c.id))
                   ) AS links_count
            FROM concepts c
            WHERE c.user_id = ? AND c.is_archived = 0
            ORDER BY c.updated_at DESC, c.normalized_name ASC, c.id ASC
            LIMIT 6;
            """,
            (user_id,),
        ),
        "9_export_highlights_join": (
            """
            SELECT h.id, h.book_id FROM highlights h
            JOIN books b ON b.id = h.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            WHERE h.user_id = ? AND b.is_missing = 0 AND COALESCE(s.is_active, 1) = 1
            ORDER BY h.book_id ASC, h.page ASC, h.created_at ASC, h.id ASC;
            """,
            (user_id,),
        ),
        "10_reading_progress": (
            """
            SELECT current_page, total_pages, percentage FROM reading_progress
            WHERE user_id = ? AND book_id = ?;
            """,
            (user_id, "bench_book_0000"),
        ),
    }

    report: Dict[str, List[Dict[str, Any]]] = {}
    cur = conn.cursor()
    for name, (sql, params) in queries.items():
        cur.execute("EXPLAIN QUERY PLAN " + sql, params)
        report[name] = [dict(r) for r in cur.fetchall()]
    return report


def run_uvicorn_concurrency_benchmark(env: Dict[str, str], session_token: str, csrf_token: str) -> Dict[str, Any]:
    """Mede comportamento real do processo Uvicorn (--workers 1) em bloqueio do event loop e semáforo de exportação."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--workers", "1", "--log-level", "warning",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ready = False
        for _ in range(250):
            if proc.poll() is not None:
                return {"error": f"Uvicorn encerrou prematuramente com código {proc.returncode}."}
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)
        if not ready:
            return {"error": "Uvicorn não iniciou no tempo limite (25s)."}

        mem_idle = read_proc_status(proc.pid)

        # 1. Latência de /api/health em repouso
        idle_health_ms: List[float] = []
        for _ in range(25):
            t0 = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                r.read()
            idle_health_ms.append((time.perf_counter() - t0) * 1000.0)

        # 2. Exportação Markdown ZIP + sondagens concorrentes de /api/health e POST /api/books/bench_book_0000/progress
        export_res: Dict[str, Any] = {}
        health_during: List[float] = []
        progress_during: List[float] = []
        progress_errors = 0

        def _do_export():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/knowledge/export?format=markdown_zip",
                headers={"Cookie": f"lectoria_session={session_token}; lectoria_csrf={csrf_token}"},
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            export_res["status"] = resp.status
            export_res["ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            export_res["bytes"] = len(body)

        t_exp = threading.Thread(target=_do_export)
        t_exp.start()
        time.sleep(0.03)
        probe_idx = 0
        while t_exp.is_alive():
            probe_idx += 1
            t0 = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=60) as r:
                r.read()
            health_during.append((time.perf_counter() - t0) * 1000.0)

            preq = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/books/bench_book_0000/progress",
                data=json.dumps({"current_page": (probe_idx % 100) + 1, "total_pages": 180}).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Cookie": f"lectoria_session={session_token}; lectoria_csrf={csrf_token}",
                    "X-CSRF-Token": csrf_token,
                },
            )
            t0p = time.perf_counter()
            try:
                with urllib.request.urlopen(preq, timeout=60) as pr:
                    pr.read()
                progress_during.append((time.perf_counter() - t0p) * 1000.0)
            except Exception:
                progress_errors += 1
            time.sleep(0.02)
        t_exp.join(timeout=60)

        # 3. 6 exportações simultâneas para verificar contenção, BoundedSemaphore(4) e memória de pico
        def _fire_export(idx: int) -> Dict[str, Any]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/knowledge/export?format=markdown_zip",
                headers={"Cookie": f"lectoria_session={session_token}; lectoria_csrf={csrf_token}"},
            )
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    b = resp.read()
                return {"idx": idx, "status": resp.status, "ms": round((time.perf_counter() - t0) * 1000.0, 2), "bytes": len(b)}
            except urllib.error.HTTPError as he:
                return {"idx": idx, "status": he.code, "ms": round((time.perf_counter() - t0) * 1000.0, 2), "bytes": 0}
            except Exception as ex:
                return {"idx": idx, "status": -1, "error": str(ex), "ms": round((time.perf_counter() - t0) * 1000.0, 2)}

        t_conc0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=6) as pool:
            conc_results = list(pool.map(_fire_export, range(6)))
        conc_total_ms = round((time.perf_counter() - t_conc0) * 1000.0, 2)
        mem_peak = read_proc_status(proc.pid)

        status_counts: Dict[str, int] = {}
        for item in conc_results:
            st = str(item.get("status"))
            status_counts[st] = status_counts.get(st, 0) + 1

        return {
            "uvicorn_mem_idle": mem_idle,
            "uvicorn_mem_after_6x_concurrent_exports": mem_peak,
            "idle_health": {
                "samples": len(idle_health_ms),
                "median_ms": percentile(sorted(idle_health_ms), 50.0),
                "p95_ms": percentile(sorted(idle_health_ms), 95.0),
                "max_ms": round(max(idle_health_ms), 3),
            },
            "export_single": export_res,
            "health_during_export": {
                "probes": len(health_during),
                "median_ms": percentile(sorted(health_during), 50.0) if health_during else None,
                "p95_ms": percentile(sorted(health_during), 95.0) if health_during else None,
                "max_ms": round(max(health_during), 3) if health_during else None,
            },
            "progress_write_during_export": {
                "probes": len(progress_during),
                "errors": progress_errors,
                "median_ms": percentile(sorted(progress_during), 50.0) if progress_during else None,
                "p95_ms": percentile(sorted(progress_during), 95.0) if progress_during else None,
                "max_ms": round(max(progress_during), 3) if progress_during else None,
            },
            "concurrent_6x_exports": {
                "total_wall_ms": conc_total_ms,
                "status_counts": status_counts,
                "results": conc_results,
            },
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_benchmark_suite(quick: bool = False) -> Dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="lectoria_p8_bench_"))
    try:
        data_dir = tmp / "data"
        books_dir = tmp / "books"
        backups_dir = tmp / "backups"
        db_path = data_dir / "lectoria.db"
        data_dir.mkdir(parents=True, exist_ok=True)
        books_dir.mkdir(parents=True, exist_ok=True)
        backups_dir.mkdir(parents=True, exist_ok=True)

        os.environ["LECTORIA_DATA_DIR"] = str(data_dir)
        os.environ["LECTORIA_DB_PATH"] = str(db_path)
        os.environ["LECTORIA_LIBRARY_DIR"] = str(books_dir)
        os.environ["LECTORIA_BACKUPS_DIR"] = str(backups_dir)

        import config
        config.DATA_DIR = data_dir
        config.DB_PATH = db_path
        config.COVERS_DIR = data_dir / "covers"
        config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
        config.LIBRARY_DIR = books_dir
        config.BACKUPS_DIR = backups_dir

        import annotations_service
        import auth_service
        import database
        import knowledge_export_service
        import knowledge_service
        import library
        import progress

        report: Dict[str, Any] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "app_version": config.APP_VERSION,
            "schema_version": database.SCHEMA_VERSION,
            "quick_mode": quick,
            "environment": collect_environment_info(),
            "scenarios": {},
        }

        mem_before_init = read_proc_status()
        database.init_db()
        u_main = auth_service.create_user("atlas_bench", "BenchPass123!", role="admin", display_name="Atlas Bench")
        u_other = auth_service.create_user("atlas_other", "OtherPass123!", role="reader", display_name="Atlas Other")
        uid = u_main["id"]
        uid_other = u_other["id"]
        tok = auth_service.create_session(uid)
        csrf = "p8_bench_csrf_token"

        with database.get_db() as conn:
            pragmas = {}
            for p in ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "cache_size", "temp_store", "wal_autocheckpoint", "mmap_size", "page_size"):
                pragmas[p] = conn.execute(f"PRAGMA {p};").fetchone()[0]
        report["sqlite_effective_pragmas"] = pragmas

        mem_empty = read_proc_status()
        report["scenarios"]["0_empty_startup"] = {
            "mem_after_imports": mem_before_init,
            "mem_empty_db": mem_empty,
            "db_sizes": _db_file_sizes(db_path),
        }

        now_iso = knowledge_service._now_iso()
        it_fast = 10 if quick else 25
        it_med = 5 if quick else 15
        it_heavy = 3 if quick else 5

        # --- Cenário 1: 100 livros ---
        with database.get_db() as conn:
            _seed_books_batch(conn.cursor(), books_dir, 0, 100, uid, now_iso)
            conn.commit()
        report["scenarios"]["1_books_100"] = {
            "get_books": measure_op(lambda: library.get_books(user_id=uid), iterations=it_med, warmup=1),
            "get_discovery_shelves": measure_op(lambda: library.get_discovery_shelves(user_id=uid), iterations=it_med, warmup=1),
            "get_book": measure_op(lambda: library.get_book("bench_book_0000", user_id=uid), iterations=it_fast, warmup=1),
        }

        # --- Cenário 2: 1.000 livros ---
        with database.get_db() as conn:
            _seed_books_batch(conn.cursor(), books_dir, 100, 1000, uid, now_iso)
            conn.commit()
        report["scenarios"]["2_books_1000"] = {
            "get_books": measure_op(lambda: library.get_books(user_id=uid), iterations=it_heavy, warmup=1),
            "get_discovery_shelves": measure_op(lambda: library.get_discovery_shelves(user_id=uid), iterations=it_heavy, warmup=1),
            "get_book": measure_op(lambda: library.get_book("bench_book_0000", user_id=uid), iterations=it_fast, warmup=1),
            "db_sizes": _db_file_sizes(db_path),
        }

        # --- Cenário 3: Atlas 1.000 conceitos, 1.000 reflexões, 1.000 anotações, 10.000 conexões ---
        with database.get_db() as conn:
            _bulk_seed_atlas(
                conn,
                user_id=uid,
                other_user_id=uid_other,
                n_concepts=1000,
                n_reflections=1000,
                n_highlights=500,
                n_notes=500,
                n_links=10000,
                n_books=1000,
                now_iso=now_iso,
            )

        def _cleanup_export(res: Dict[str, Any]) -> Dict[str, Any]:
            fp = res.get("file_path")
            sz = Path(fp).stat().st_size if fp and Path(fp).exists() else 0
            if fp and Path(fp).exists():
                Path(fp).unlink()
            return {"format": res.get("format"), "file_bytes": sz, "counts": res.get("counts")}

        report["scenarios"]["3_atlas_1k_1k_10k_exports"] = {
            "list_concepts_p50": measure_op(lambda: knowledge_service.list_concepts(uid, limit=50, offset=0), iterations=it_fast, warmup=1),
            "list_concepts_sql_trace": count_sql_statements(db_path, lambda: knowledge_service.list_concepts(uid, limit=50, offset=0)),
            "list_reflections_p50": measure_op(lambda: knowledge_service.list_reflections(uid, limit=50, offset=0), iterations=it_fast, warmup=1),
            "list_reflections_sql_trace": count_sql_statements(db_path, lambda: knowledge_service.list_reflections(uid, limit=50, offset=0)),
            "atlas_summary": measure_op(lambda: knowledge_service.get_atlas_overview_summary(uid), iterations=it_fast, warmup=1),
            "export_markdown_zip": measure_op(lambda: _cleanup_export(knowledge_export_service.generate_atlas_export_file(uid, export_format="markdown_zip")), iterations=it_heavy, warmup=1),
            "export_json": measure_op(lambda: _cleanup_export(knowledge_export_service.generate_atlas_export_file(uid, export_format="json")), iterations=it_heavy, warmup=1),
            "export_csv": measure_op(lambda: _cleanup_export(knowledge_export_service.generate_atlas_export_file(uid, export_format="csv")), iterations=it_heavy, warmup=1),
            "export_snapshot_sql_trace": count_sql_statements(db_path, lambda: knowledge_export_service.build_atlas_export_snapshot(uid)),
            "db_sizes": _db_file_sizes(db_path),
        }

        # Testa Uvicorn real (--workers 1) antes de expandir para 50k links
        child_env = os.environ.copy()
        child_env["LECTORIA_DATA_DIR"] = str(data_dir)
        child_env["LECTORIA_DB_PATH"] = str(db_path)
        child_env["LECTORIA_LIBRARY_DIR"] = str(books_dir)
        child_env["LECTORIA_BACKUPS_DIR"] = str(backups_dir)
        report["scenarios"]["4_uvicorn_single_worker_concurrency"] = run_uvicorn_concurrency_benchmark(
            child_env, tok, csrf
        )

        # --- Cenário 5: Escala extrema — 10.000 conceitos, 1.000 reflexões, 10.000 anotações, 50.000 conexões ---
        with database.get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM knowledge_links;")
            cur.execute("DELETE FROM notes;")
            cur.execute("DELETE FROM highlights;")
            cur.execute("DELETE FROM reflections;")
            cur.execute("DELETE FROM concepts;")
            conn.commit()
            _bulk_seed_atlas(
                conn,
                user_id=uid,
                other_user_id=uid_other,
                n_concepts=10000,
                n_reflections=1000,
                n_highlights=5000,
                n_notes=5000,
                n_links=50000,
                n_books=1000,
                now_iso=now_iso,
            )

        with database.get_db() as conn:
            report["explain_query_plan"] = run_explain_audit(conn, uid)

        report["scenarios"]["5_atlas_10k_10k_50k"] = {
            "db_sizes_before_wal": _db_file_sizes(db_path),
            "list_concepts_p50": measure_op(lambda: knowledge_service.list_concepts(uid, limit=50, offset=0), iterations=it_med, warmup=1),
            "list_concepts_search_diacritics": measure_op(lambda: knowledge_service.list_concepts(uid, search="tecnica", limit=50, offset=0), iterations=it_med, warmup=1),
            "list_reflections_p50": measure_op(lambda: knowledge_service.list_reflections(uid, limit=50, offset=0), iterations=it_med, warmup=1),
            "get_reflection_detail": measure_op(lambda: knowledge_service.get_reflection("bc_r_00000", uid), iterations=it_fast, warmup=1),
            "atlas_summary": measure_op(lambda: knowledge_service.get_atlas_overview_summary(uid), iterations=it_fast, warmup=1),
            "book_annotations_summary_500_items": measure_op(
                lambda: annotations_service.get_book_annotations_summary("bench_book_0000", user_id=uid, limit=50, offset=0),
                iterations=it_fast,
                warmup=1,
            ),
            "search_fts5_diacritics": measure_op(
                lambda: knowledge_service.search_knowledge(uid, "tecnicacritica", limit=24, offset=0),
                iterations=it_fast,
                warmup=1,
            ),
            "search_fts5_multi_term": measure_op(
                lambda: knowledge_service.search_knowledge(uid, "técnica método", limit=24, offset=0),
                iterations=it_fast,
                warmup=1,
            ),
            "search_relational_fallback": measure_op(
                lambda: knowledge_service.search_knowledge(uid, "tecnicacritica", limit=24, offset=0, force_fallback=True),
                iterations=it_heavy,
                warmup=1,
            ),
            "graph_hub_hop1_80nodes": measure_op(
                lambda: knowledge_service.get_knowledge_graph(uid, "concept", "bc_c_00000", depth=1, max_nodes=80, max_edges=160),
                iterations=it_fast,
                warmup=1,
            ),
            "graph_hub_hop2_80nodes_160edges": measure_op(
                lambda: knowledge_service.get_knowledge_graph(uid, "concept", "bc_c_00000", depth=2, max_nodes=80, max_edges=160),
                iterations=it_fast,
                warmup=1,
            ),
            "graph_hop2_sql_trace": count_sql_statements(
                db_path,
                lambda: knowledge_service.get_knowledge_graph(uid, "concept", "bc_c_00000", depth=2, max_nodes=80, max_edges=160),
            ),
            "list_knowledge_links_p50": measure_op(
                lambda: knowledge_service.list_knowledge_links(uid, limit=50, offset=0),
                iterations=it_fast,
                warmup=1,
            ),
            "entity_connections_backlinks": measure_op(
                lambda: knowledge_service.get_entity_connections_and_backlinks("concept", "bc_c_00000", uid, limit=50, offset=0),
                iterations=it_fast,
                warmup=1,
            ),
            "save_progress_isolated": measure_op(
                lambda: progress.save_progress("bench_book_0000", 42, 180, user_id=uid),
                iterations=it_fast,
                warmup=2,
            ),
        }

        # --- Cenário 6: Escrita concorrente de progresso vs leituras FTS5/Grafo + Exportação + WAL checkpoint ---
        conc_errors: List[str] = []
        conc_prog_ms: List[float] = []
        conc_read_ms: List[float] = []

        def _writer_task(idx: int):
            for step in range(10):
                t0 = time.perf_counter()
                try:
                    progress.save_progress(f"bench_book_{(idx + step) % 50:04d}", step + 1, 180, user_id=uid)
                    conc_prog_ms.append((time.perf_counter() - t0) * 1000.0)
                except Exception as exc:
                    conc_errors.append(f"write:{type(exc).__name__}:{exc}")

        def _reader_task(idx: int):
            for _ in range(5):
                t0 = time.perf_counter()
                try:
                    if idx % 2 == 0:
                        knowledge_service.search_knowledge(uid, "tecnicacritica", limit=24, offset=0)
                    else:
                        knowledge_service.get_knowledge_graph(uid, "concept", "bc_c_00000", depth=2, max_nodes=80, max_edges=160)
                    conc_read_ms.append((time.perf_counter() - t0) * 1000.0)
                except Exception as exc:
                    conc_errors.append(f"read:{type(exc).__name__}:{exc}")

        def _export_task():
            try:
                _cleanup_export(knowledge_export_service.generate_atlas_export_file(uid, export_format="json"))
            except Exception as exc:
                conc_errors.append(f"export:{type(exc).__name__}:{exc}")

        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [
                pool.submit(_export_task),
                pool.submit(_writer_task, 1),
                pool.submit(_writer_task, 2),
                pool.submit(_reader_task, 0),
                pool.submit(_reader_task, 1),
                pool.submit(_reader_task, 2),
            ]
            for f in futs:
                f.result()

        sizes_before_ckpt = _db_file_sizes(db_path)
        with database.get_db() as conn:
            ckpt_res = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
            ckpt_tuple = list(ckpt_res) if ckpt_res else None
        sizes_after_ckpt = _db_file_sizes(db_path)

        report["scenarios"]["6_wal_concurrency_and_checkpoint"] = {
            "errors_count": len(conc_errors),
            "errors": conc_errors[:10],
            "progress_writes_median_ms": percentile(sorted(conc_prog_ms), 50.0) if conc_prog_ms else None,
            "progress_writes_p95_ms": percentile(sorted(conc_prog_ms), 95.0) if conc_prog_ms else None,
            "search_graph_reads_median_ms": percentile(sorted(conc_read_ms), 50.0) if conc_read_ms else None,
            "search_graph_reads_p95_ms": percentile(sorted(conc_read_ms), 95.0) if conc_read_ms else None,
            "sizes_before_checkpoint": sizes_before_ckpt,
            "wal_checkpoint_truncate_result": ckpt_tuple,
            "sizes_after_checkpoint": sizes_after_ckpt,
        }

        # --- Cenário 7: Custo unitário de triggers FTS5 com UNINDEXED ---
        report["scenarios"]["7_fts5_unindexed_trigger_overhead"] = run_trigger_overhead_benchmark(tmp)
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sintético reproduzível do Lectoria v1.9.0.")
    parser.add_argument("--output-json", type=str, default=None, help="Caminho para salvar o relatório JSON completo.")
    parser.add_argument("--quick", action="store_true", help="Reduz repetições por operação mantendo volumes de dados.")
    args = parser.parse_args()

    result = run_benchmark_suite(quick=args.quick)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output_json:
        Path(args.output_json).write_text(text, encoding="utf-8")
        print(f"Benchmark JSON salvo em: {args.output_json}")
    else:
        print(text)

    sc = result["scenarios"]
    print("\n=== RESUMO FASE 8 BENCHMARK ===")
    print("Host:", result["environment"]["cpu_model"], "| RAM:", result["environment"]["ram_total_mb"], "MB | Python:", result["environment"]["python_version"], "| SQLite:", result["environment"]["sqlite_version"])
    print("100 livros get_books mediana:", sc["1_books_100"]["get_books"]["median_ms"], "ms | 1000 livros:", sc["2_books_1000"]["get_books"]["median_ms"], "ms")
    print("Atlas 1k/1k/10k: list_concepts=", sc["3_atlas_1k_1k_10k_exports"]["list_concepts_p50"]["median_ms"], "ms (SQLs:", sc["3_atlas_1k_1k_10k_exports"]["list_concepts_sql_trace"]["statements"], "), list_reflections=", sc["3_atlas_1k_1k_10k_exports"]["list_reflections_p50"]["median_ms"], "ms (SQLs:", sc["3_atlas_1k_1k_10k_exports"]["list_reflections_sql_trace"]["statements"], ")")
    print("Exports 1k/1k/10k: md_zip=", sc["3_atlas_1k_1k_10k_exports"]["export_markdown_zip"]["median_ms"], "ms, json=", sc["3_atlas_1k_1k_10k_exports"]["export_json"]["median_ms"], "ms, csv=", sc["3_atlas_1k_1k_10k_exports"]["export_csv"]["median_ms"], "ms")
    uv = sc["4_uvicorn_single_worker_concurrency"]
    if "error" in uv:
        print("Uvicorn erro:", uv["error"])
    else:
        print("Uvicorn health idle mediana:", uv["idle_health"]["median_ms"], "ms | health durante export max:", uv["health_during_export"]["max_ms"], "ms | Uvicorn HWM MB:", uv["uvicorn_mem_after_6x_concurrent_exports"]["vm_hwm_mb"], "| 6x concurrent statuses:", uv["concurrent_6x_exports"]["status_counts"])
    s5 = sc["5_atlas_10k_10k_50k"]
    print("Escala 10k/10k/50k: FTS5 single=", s5["search_fts5_diacritics"]["median_ms"], "ms, multi=", s5["search_fts5_multi_term"]["median_ms"], "ms, Fallback=", s5["search_relational_fallback"]["median_ms"], "ms, Grafo 2 saltos=", s5["graph_hub_hop2_80nodes_160edges"]["median_ms"], "ms, Grafo SQLs=", s5["graph_hop2_sql_trace"]["statements"])
    print("Trigger overhead (median insert ms) 1k/10k/50k:", {k: v["insert_with_unindexed_delete_median_ms"] for k, v in sc["7_fts5_unindexed_trigger_overhead"].items()})


if __name__ == "__main__":
    main()
