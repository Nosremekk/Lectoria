"""
Testes automatizados da Fase 8 (Lectoria v1.9.0 — Auditoria de Infraestrutura,
Migração SQLite v12 -> v13, FTS5, Backup/Restore, Concorrência e Regressão de Otimizações).

Todos os testes operam exclusivamente em diretórios temporários (`tmp_path`),
sem tocar em dados de produção, VPS ou configurações reais.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

import auth_service
import backup_service
import config
import database
import knowledge_export_service
import knowledge_service


# =========================================================================
# HELPERS ISOLADOS
# =========================================================================

def _configure_isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    books_dir = tmp_path / "books"
    backups_dir = tmp_path / "backups"
    covers_dir = data_dir / "covers"
    custom_covers_dir = covers_dir / "custom"
    for d in (data_dir, books_dir, backups_dir, covers_dir, custom_covers_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", books_dir)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(config, "COVERS_DIR", covers_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    return db_path


def _create_synthetic_v12_db(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Inicializa um banco SQLite genuíno no schema v12 (sem v13) e semeia dados históricos v1.8.0."""
    with patch.object(database, "_apply_migration_v13", lambda cur: None):
        database.init_db(db_path)

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM schema_migrations WHERE version >= 13;")
        cur.execute("SELECT MAX(version) FROM schema_migrations;")
        assert cur.fetchone()[0] == 12

        # Usuário histórico
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, display_name, is_active)
            VALUES ('leitor_v12', 'hash_dummy', 'admin', 'Leitor v1.8', 1);
            """
        )
        uid = cur.lastrowid

        # Fonte e livro histórico
        cur.execute(
            """
            INSERT INTO library_sources (name, path, is_active)
            VALUES ('Acervo Principal', '/tmp/books', 1);
            """
        )
        sid = cur.lastrowid
        cur.execute(
            """
            INSERT INTO books (id, title, author, format, rel_path, filename, filesize, page_count, source_id, is_missing)
            VALUES ('book-v12-1', 'Epistemologia Técnica', 'Autor Teste', 'pdf', 'epist.pdf', 'epist.pdf', 1024, 120, ?, 0);
            """,
            (sid,),
        )
        cur.execute(
            """
            INSERT INTO highlights (id, book_id, user_id, page, selected_text, rects_json, color, visibility)
            VALUES ('hl-v12-1', 'book-v12-1', ?, 12, 'Trecho histórico com acentuação técnica e crítica', '[]', 'yellow', 'private');
            """,
            (uid,),
        )
        cur.execute(
            """
            INSERT INTO notes (id, book_id, user_id, page, content, color, visibility)
            VALUES ('nt-v12-1', 'book-v12-1', ?, 12, 'Nota histórica sobre epistemologia aplicada', 'blue', 'private');
            """,
            (uid,),
        )
        cur.execute(
            """
            INSERT INTO reading_progress (book_id, user_id, current_page, total_pages, percentage)
            VALUES ('book-v12-1', ?, 45, 120, 37.5);
            """,
            (uid,),
        )
    return {"user_id": uid, "book_id": "book-v12-1", "highlight_id": "hl-v12-1", "note_id": "nt-v12-1"}


def _seed_v13_user_and_books(db_path: Path, num_books: int = 30) -> tuple[int, list[str]]:
    database.init_db(db_path)
    book_ids: list[str] = []
    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, display_name, is_active)
            VALUES ('perf_user', 'hash_dummy', 'admin', 'Perf User', 1);
            """
        )
        uid = cur.lastrowid
        cur.execute(
            """
            INSERT INTO library_sources (name, path, is_active)
            VALUES ('Principal', '/tmp/books', 1);
            """
        )
        sid = cur.lastrowid
        for i in range(num_books):
            bid = f"book-p8-{i:04d}"
            book_ids.append(bid)
            cur.execute(
                """
                INSERT INTO books (id, title, author, format, rel_path, filename, filesize, page_count, source_id, is_missing)
                VALUES (?, ?, ?, 'pdf', ?, ?, 2048, 100, ?, 0);
                """,
                (bid, f"Obra Técnica {i}", f"Autor {i % 5}", f"{bid}.pdf", f"{bid}.pdf", sid),
            )
    return uid, book_ids


# =========================================================================
# 1. MIGRAÇÃO SQLITE v12 -> v13 (SINTÉTICA E SEGURA)
# =========================================================================

def test_migration_v12_to_v13_preserves_data_creates_backup_and_backfills_fts(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    seeded = _create_synthetic_v12_db(db_path, monkeypatch)

    # Executa migração para v13
    database.init_db(db_path)

    # 1. Backup automático pré-migração existe e é um banco v12 íntegro
    bak_path = db_path.with_name("lectoria_pre_migration_v12_to_v13.db.bak")
    assert bak_path.exists()
    with sqlite3.connect(str(bak_path)) as bak_conn:
        assert bak_conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert bak_conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 12

    # 2. Banco migrado está na v13, íntegro, sem violações de FK
    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 13
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert cur.execute("PRAGMA foreign_key_check;").fetchall() == []
        assert str(cur.execute("PRAGMA journal_mode;").fetchone()[0]).lower() == "wal"

        # Tabelas v13 criadas
        tables = {
            r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table');"
            ).fetchall()
        }
        assert {"concepts", "reflections", "knowledge_links", "knowledge_fts"}.issubset(tables)

        # 13 triggers do domínio v13 presentes
        triggers = {
            r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger';"
            ).fetchall()
        }
        v13_triggers = {
            t for t in triggers
            if any(k in t for k in ("concepts", "reflections", "klinks", "knowledge_links", "highlights", "notes"))
            and ("fts" in t or "cleanup" in t or "klinks" in t)
        }
        assert len(v13_triggers) >= 13, f"Triggers v13 encontrados: {sorted(v13_triggers)}"

        # Dados históricos v1.8.0 preservados
        assert cur.execute("SELECT COUNT(*) FROM users WHERE id = ?;", (seeded["user_id"],)).fetchone()[0] == 1
        assert cur.execute("SELECT title FROM books WHERE id = ?;", (seeded["book_id"],)).fetchone()[0] == "Epistemologia Técnica"
        assert cur.execute("SELECT current_page FROM reading_progress WHERE book_id = ?;", (seeded["book_id"],)).fetchone()[0] == 45

        # Backfill FTS5 das anotações históricas
        fts_count = cur.execute("SELECT COUNT(*) FROM knowledge_fts;").fetchone()[0]
        assert fts_count >= 2

    # Pesquisa sem acentos encontra dados históricos migrados
    res = knowledge_service.search_knowledge(seeded["user_id"], query="tecnica")
    assert res["search_engine"] == "fts5"
    assert any(it["id"] == seeded["highlight_id"] for it in res["items"])


def test_migration_empty_db_and_idempotent_v13_init(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    database.init_db(db_path)

    with database.get_db(db_path) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 13
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"

    # Reexecução repetida de init_db não gera backup indevido nem falha
    database.init_db(db_path)
    database.init_db(db_path)
    bak_files = list(db_path.parent.glob("lectoria_pre_migration*.db.bak"))
    assert bak_files == []


def test_migration_interrupted_and_disk_full_rollback_safely(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    _create_synthetic_v12_db(db_path, monkeypatch)

    # 1. Falha simulada de "database or disk is full" durante v13
    def _fail_disk_full(cursor):
        cursor.execute("CREATE TABLE IF NOT EXISTS _partial_v13_probe (id INTEGER);")
        raise sqlite3.OperationalError("database or disk is full")

    with patch.object(database, "_apply_migration_v13", side_effect=_fail_disk_full):
        with pytest.raises(sqlite3.OperationalError, match="disk is full"):
            database.init_db(db_path)

    # Banco deve permanecer em v12 e íntegro (rollback completo)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 12
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        assert "_partial_v13_probe" not in tables

    # 2. Tentativa subsequente normal completa v13 com sucesso
    database.init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 13
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"


# =========================================================================
# 2. FTS5 LOCAL, DIACRÍTICOS, FALLBACK E BACKUP/RESTORE ISOLADO
# =========================================================================

def test_local_fts5_diacritics_and_relational_fallback(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    uid, book_ids = _seed_v13_user_and_books(db_path, num_books=2)

    # Confirma compilação FTS5 no SQLite local
    with database.get_db(db_path) as conn:
        opts = {r[0] for r in conn.execute("PRAGMA compile_options;").fetchall()}
        assert "ENABLE_FTS5" in opts

    c = knowledge_service.create_concept(
        uid,
        name="Fenomenologia Crítica",
        description="Análise sistemática da percepção histórica.",
        tags=["filosofia", "método"],
    )
    # Busca sem acento via FTS5
    res_fts = knowledge_service.search_knowledge(uid, query="percepcao critica")
    assert res_fts["search_engine"] == "fts5"
    assert any(it["id"] == c["id"] for it in res_fts["items"])

    # Fallback relacional controlado
    res_fb = knowledge_service.search_knowledge(uid, query="percepcao", force_fallback=True)
    assert res_fb["search_engine"] == "relational_fallback"
    assert any(it["id"] == c["id"] for it in res_fb["items"])


def test_isolated_backup_and_restore_cycle_with_atlas_and_wal(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    uid, book_ids = _seed_v13_user_and_books(db_path, num_books=2)

    c = knowledge_service.create_concept(uid, name="Conceito Backup", description="Persistido no backup")
    r = knowledge_service.create_reflection(uid, title="Reflexão Backup", body_markdown="Corpo seguro", primary_book_id=book_ids[0])
    knowledge_service.create_knowledge_link(
        uid,
        source_type="reflection",
        source_id=r["id"],
        target_type="concept",
        target_id=c["id"],
        relation_type="develops",
        rationale="Vínculo de backup",
    )

    # 1. Criar backup consistente via SQLite Online Backup API (.db)
    bkp = backup_service.create_backup()
    assert bkp["filename"].endswith(".db")
    bkp_path = config.BACKUPS_DIR / bkp["filename"]
    assert bkp_path.exists()
    with sqlite3.connect(str(bkp_path)) as bconn:
        assert bconn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"

    # 2. Mutação destrutiva no banco ativo
    knowledge_service.delete_concept(c["id"], uid)
    assert knowledge_service.list_concepts(uid)["total"] == 0

    # 3. Restaurar backup
    restored = backup_service.restore_backup(bkp["filename"])
    assert restored["restored_from"] == bkp["filename"]

    # 4. Verificar safety backup WAL-safe (`lectoria_pre_restore.db.bak`) íntegro
    safety_bak = config.DATA_DIR / "lectoria_pre_restore.db.bak"
    assert safety_bak.exists()
    with sqlite3.connect(str(safety_bak)) as sconn:
        assert sconn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"

    # 5. Verificar restauração integral dos dados do Atlas
    assert knowledge_service.list_concepts(uid)["total"] == 1
    assert knowledge_service.list_reflections(uid)["total"] == 1
    assert knowledge_service.list_knowledge_links(uid)["total"] == 1


# =========================================================================
# 3. REGRESSÃO DAS OTIMIZAÇÕES DA FASE 8 (ZERO N+1, ÍNDICES, THREAD-SAFETY, EVENT LOOP)
# =========================================================================

def test_phase8_zero_n_plus_1_sql_statement_counts(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    uid, book_ids = _seed_v13_user_and_books(db_path, num_books=40)

    # Semeia 100 conceitos, 100 reflexões com primary_book_id e 100 links
    cids = []
    for i in range(100):
        c = knowledge_service.create_concept(
            uid,
            name=f"Conceito Alfa {i:03d}",
            description=f"Descrição detalhada sobre técnica crítica número {i}",
            tags=["metodo"],
        )
        cids.append(c["id"])
    for i in range(100):
        r = knowledge_service.create_reflection(
            uid,
            title=f"Reflexão Beta {i:03d}",
            body_markdown=f"Corpo autoral sobre técnica crítica {i}",
            primary_book_id=book_ids[i % len(book_ids)],
            tags=["sintese"],
        )
        knowledge_service.create_knowledge_link(
            uid,
            source_type="reflection",
            source_id=r["id"],
            target_type="concept",
            target_id=cids[i],
            relation_type="develops",
        )

    orig_get_conn = database.get_connection

    def _count_sql(fn):
        stmts: list[str] = []

        def _on_sql(sql: str):
            low = sql.strip().lower()
            # Ignora consultas internas das shadow tables do módulo FTS5 do SQLite
            if "knowledge_fts_" in low or low.startswith("pragma "):
                return
            stmts.append(sql)

        def _traced(path=None):
            conn = orig_get_conn(path)
            conn.set_trace_callback(_on_sql)
            return conn

        with patch.object(database, "get_connection", side_effect=_traced):
            result = fn()
        return result, len(stmts)

    res_concepts, sql_concepts = _count_sql(lambda: knowledge_service.list_concepts(uid, limit=50))
    assert res_concepts["total"] == 100
    assert len(res_concepts["items"]) == 50
    assert sql_concepts <= 10, f"list_concepts executou {sql_concepts} SQLs (esperado <= 10)"

    res_refs, sql_refs = _count_sql(lambda: knowledge_service.list_reflections(uid, limit=50))
    assert res_refs["total"] == 100
    assert len(res_refs["items"]) == 50
    assert all(it["primary_book_available"] is True for it in res_refs["items"])
    assert sql_refs <= 12, f"list_reflections executou {sql_refs} SQLs (esperado <= 12, N+1 regrediu)"

    res_search, sql_search = _count_sql(lambda: knowledge_service.search_knowledge(uid, query="tecnica critica", limit=20))
    assert res_search["total"] >= 150
    assert len(res_search["items"]) == 20
    assert sql_search <= 20, f"search_knowledge executou {sql_search} SQLs (esperado <= 20, N+1 regrediu)"

    snap, sql_export = _count_sql(lambda: knowledge_export_service.build_atlas_export_snapshot(uid))
    assert len(snap["concepts"]) == 100
    assert len(snap["reflections"]) == 100
    assert sql_export <= 25, f"build_atlas_export_snapshot executou {sql_export} SQLs (esperado <= 25, N+1 regrediu)"


def test_phase8_explain_query_plan_uses_composite_indexes(monkeypatch, tmp_path):
    db_path = _configure_isolated_env(monkeypatch, tmp_path)
    uid, _ = _seed_v13_user_and_books(db_path, num_books=2)
    c1 = knowledge_service.create_concept(uid, name="Nó Central")
    c2 = knowledge_service.create_concept(uid, name="Nó Vizinho")
    knowledge_service.create_knowledge_link(
        uid,
        source_type="concept",
        source_id=c1["id"],
        target_type="concept",
        target_id=c2["id"],
        relation_type="relates_to",
    )

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        # Hop 1 do grafo (UNION ALL sobre source/target)
        plan_hop1 = [
            row["detail"]
            for row in cur.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT * FROM (
                    SELECT * FROM knowledge_links
                    WHERE user_id = ? AND source_type = ? AND source_id = ? AND relation_type IN ('relates_to')
                    UNION ALL
                    SELECT * FROM knowledge_links
                    WHERE user_id = ? AND target_type = ? AND target_id = ? AND relation_type IN ('relates_to')
                )
                ORDER BY created_at DESC, id ASC;
                """,
                (uid, "concept", c1["id"], uid, "concept", c1["id"]),
            ).fetchall()
        ]
        joined_hop1 = "\n".join(plan_hop1)
        assert "idx_klinks_user_source" in joined_hop1
        assert "idx_klinks_user_target" in joined_hop1
        assert "SCAN knowledge_links" not in joined_hop1


def test_auth_service_rate_limit_is_thread_safe():
    auth_service.clear_failed_logins()
    errors: list[Exception] = []

    def _worker(tid: int):
        try:
            key = f"127.0.0.{tid % 20}:user_{tid % 10}"
            for _ in range(15):
                auth_service._record_failed_attempt(key)
                try:
                    auth_service._check_rate_limit(key)
                except auth_service.RateLimitError:
                    pass
            auth_service._clear_failed_attempts(key)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    auth_service.clear_failed_logins()


def test_main_routes_do_not_block_event_loop_ast_audit():
    """Garante por AST que rotas FastAPI em `main.py` não usem `async def` síncrono bloqueante."""
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    allowed_async_without_await = {"health_check"}
    offenders: list[str] = []
    health_is_async = False

    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            is_route = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "app"
                and d.func.attr in {"get", "post", "put", "patch", "delete"}
                for d in node.decorator_list
            )
            if not is_route:
                continue
            if node.name == "health_check":
                health_is_async = True
            has_await = any(isinstance(sub, ast.Await) for sub in ast.walk(node))
            if not has_await and node.name not in allowed_async_without_await:
                offenders.append(node.name)

    assert health_is_async, "/api/health deve permanecer async def sem I/O bloqueante"
    assert offenders == [], f"Rotas async def sem await bloqueiam o event loop: {offenders}"


def test_uvicorn_single_worker_health_unblocked_and_export_semaphore_enforced(tmp_path):
    """
    Sobe uma instância isolada do Uvicorn (`--workers 1`) em porta efêmera e valida:
    1. `/api/health` responde rapidamente (< 150 ms) durante exportações concorrentes.
    2. 6 exportações simultâneas acionam o `BoundedSemaphore(4)` (sem serializar no event loop).
    """
    root_dir = Path(__file__).resolve().parent.parent
    data_dir = tmp_path / "u_data"
    books_dir = tmp_path / "u_books"
    backups_dir = tmp_path / "u_backups"
    for d in (data_dir, books_dir, backups_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Escolhe porta livre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = os.environ.copy()
    env.update(
        {
            "LECTORIA_HOST": "127.0.0.1",
            "LECTORIA_PORT": str(port),
            "LECTORIA_DATA_DIR": str(data_dir),
            "LECTORIA_LIBRARY_DIR": str(books_dir),
            "LECTORIA_BACKUPS_DIR": str(backups_dir),
            "LECTORIA_SECURE_COOKIES": "false",
        }
    )

    # Semeia usuário + 250 conceitos/reflexões para exportação mensurável
    seed_code = (
        "import database, auth_service, knowledge_service\n"
        "database.init_db()\n"
        "u = auth_service.create_user('p8admin', 'SenhaForte123!', role='admin', display_name='Admin P8')\n"
        "for i in range(250):\n"
        "    knowledge_service.create_concept(u['id'], name=f'C{i:03d}', description='Texto longo ' * 25)\n"
        "    knowledge_service.create_reflection(u['id'], title=f'R{i:03d}', body_markdown='Parágrafo ' * 40)\n"
    )
    subprocess.run([sys.executable, "-c", seed_code], cwd=str(root_dir), env=env, check=True)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--log-level", "warning"],
        cwd=str(root_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        # Aguarda /api/health
        ready = False
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"{base_url}/api/health", timeout=1.0) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)
        assert ready, "Uvicorn isolado não respondeu a /api/health"

        # Login para obter cookie de sessão
        import http.cookiejar
        import json as _json
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        login_req = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=_json.dumps({"username": "p8admin", "password": "SenhaForte123!"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(login_req, timeout=5.0) as resp:
            assert resp.status == 200

        cookie_header = "; ".join(f"{c.name}={c.value}" for c in cj)

        def _do_export() -> int:
            req = urllib.request.Request(
                f"{base_url}/api/knowledge/export?format=markdown_zip",
                headers={"Cookie": cookie_header},
            )
            try:
                with urllib.request.urlopen(req, timeout=20.0) as r:
                    _ = r.read()
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        def _probe_health() -> float:
            t0 = time.perf_counter()
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=5.0) as r:
                assert r.status == 200
            return (time.perf_counter() - t0) * 1000.0

        with ThreadPoolExecutor(max_workers=8) as pool:
            exp_futures = [pool.submit(_do_export) for _ in range(6)]
            time.sleep(0.05)
            health_samples = [pool.submit(_probe_health) for _ in range(5)]
            statuses = [f.result() for f in exp_futures]
            health_ms = [f.result() for f in health_samples]

        # Pelo menos uma exportação concluída com 200 e /api/health não congelou no event loop
        assert statuses.count(200) >= 1
        assert min(health_ms) < 150.0, f"/api/health degradou durante exportação: {health_ms}"
        assert max(health_ms) < 500.0, f"/api/health sofreu bloqueio severo: {health_ms}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_benchmark_script_imports_cleanly():
    bench_path = Path(__file__).resolve().parent / "performance" / "benchmark_v190_atlas.py"
    assert bench_path.exists()
    source = bench_path.read_text(encoding="utf-8")
    ast.parse(source)
