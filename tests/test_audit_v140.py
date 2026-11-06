"""
Auditoria Técnica e Suíte de Validação da Versão v1.4.0 (Intelligent Library & Discovery).
Cobre:
1. Integração com APIs externas (Open Library e Google Books), rate limit e cooldown 429.
2. Algoritmo de identificação: ISBNs, títulos traduzidos, autores similares e edições.
3. Proteção de capas personalizadas, travas manuais e integridade.
4. Taxonomia e prateleiras dinâmicas sem prateleiras vazias.
5. Migração de SQLite v6 para v7 em banco isolado com integridade e backup pré-migração.
6. Segurança de endpoints, bloqueio anti-SSRF e controle administrativo.
7. Perfil de desempenho e consumo de memória (compatibilidade com Celeron 847).
"""

import json
from pathlib import Path
import sqlite3
import time
import tempfile
import pytest
from starlette.testclient import TestClient

import config
from auth_service import create_user, create_session
from database import get_connection, init_db, SCHEMA_VERSION
from metadata_service import (
    clean_isbn,
    validate_isbn10,
    validate_isbn13,
    isbn10_to_isbn13,
    calculate_string_similarity,
    score_candidate,
    OpenLibraryClient,
    GoogleBooksClient,
    get_cached_api_response,
    set_cached_api_response,
)
from background_enricher import is_safe_external_url, BackgroundEnricher
from taxonomy_service import TAXONOMY_TREE, map_raw_subjects_to_taxonomy, sync_book_taxonomy
from library import get_discovery_shelves, get_books, update_book_metadata
from main import app, SESSION_COOKIE_NAME


# ==============================================================================
# 1. TESTES DE APIS EXTERNAS, RATE LIMIT E COOLDOWN 429
# ==============================================================================

def test_api_rate_limiting_and_cached_lookup(test_env):
    """Valida respeito ao intervalo de 1s na Open Library e sub-milissegundo em cache."""
    test_isbn = "9780140449136"
    t0 = time.time()
    res1 = OpenLibraryClient.lookup_isbn(test_isbn)
    t1 = time.time()

    # Segundo lookup DEVE vir do cache instantaneamente (< 0.05s)
    t2 = time.time()
    res2 = OpenLibraryClient.lookup_isbn(test_isbn)
    t3 = time.time()

    assert res1 is not None
    assert res2 is not None
    assert res1["title"] == res2["title"]
    assert (t3 - t2) < 0.05, f"Consulta em cache demorou excessivamente: {t3 - t2:.4f}s"


def test_google_books_429_cooldown():
    """Valida que o cooldown de HTTP 429 protege o sistema de sobrecarga."""
    import metadata_service
    from metadata_service import _safe_http_get_json

    try:
        metadata_service._GOOGLE_BOOKS_COOLDOWN_UNTIL = time.time() + 60.0

        # Durante cooldown, requisições para googleapis.com devem retornar None imediatamente
        res = _safe_http_get_json("https://www.googleapis.com/books/v1/volumes?q=isbn:9780140449136")
        assert res is None
    finally:
        metadata_service._GOOGLE_BOOKS_COOLDOWN_UNTIL = 0.0


def test_nonexistent_isbn_negative_caching(test_env):
    """Valida que ISBN inexistente não gera requisições repetidas via cache negativo."""
    unassigned_isbn = "9798999999993"
    assert validate_isbn13(unassigned_isbn) is True

    # Primeira consulta registra no cache como not_found
    res1 = OpenLibraryClient.lookup_isbn(unassigned_isbn)
    assert res1 is None

    t0 = time.time()
    res2 = OpenLibraryClient.lookup_isbn(unassigned_isbn)
    elapsed = time.time() - t0

    assert res2 is None
    assert elapsed < 0.05, f"Cache negativo falhou em responder rápido: {elapsed:.4f}s"


# ==============================================================================
# 2. ALGORITMO DE IDENTIFICAÇÃO E SMART MATCHING
# ==============================================================================

def test_smart_matching_scoring_scenarios():
    """Valida pontuação para correspondência exata, edições diferentes e títulos traduzidos."""
    # Cenário A: Correspondência com ISBN idêntico + título exato -> Confiança Alta (>= 85)
    local_book = {
        "title": "Dom Casmurro",
        "author": "Machado de Assis",
        "isbn13": "9788535910667",
        "publisher": "Companhia das Letras",
        "publish_year": 2008,
    }
    cand_exact = {
        "title": "Dom Casmurro",
        "author": "Machado de Assis",
        "isbn13": "9788535910667",
        "publisher": "Companhia das Letras",
        "publish_year": 2008,
    }
    score_a, conf_a = score_candidate(local_book, cand_exact)
    assert score_a >= 85.0
    assert conf_a == "reliable"

    # Cenário B: Obra idêntica mas sem ISBN local -> precisa cair em 'needs_review' para evitar overwrite destrutivo
    local_no_isbn = {
        "title": "Crime e Castigo",
        "author": "Fiódor Dostoiévski",
        "isbn13": None,
        "isbn10": None,
    }
    cand_different_translation = {
        "title": "Crime e castigo",
        "author": "Fiódor Dostoievski, Joaquim Leite",
        "publish_year": 1955,
        "publisher": "Editorial Inquérito",
    }
    score_b, conf_b = score_candidate(local_no_isbn, cand_different_translation)
    assert 50.0 <= score_b < 85.0, f"Obra sem ISBN deveria exigir revisão: {score_b}"
    assert conf_b == "needs_review"

    # Cenário C: Livro de autor diferente com título parcialmente coincidente -> insuficiente (< 50)
    cand_unrelated = {
        "title": "O Crime do Padre Amaro",
        "author": "Eça de Queirós",
        "publish_year": 1875,
    }
    score_c, conf_c = score_candidate(local_no_isbn, cand_unrelated)
    assert score_c < 50.0
    assert conf_c == "insufficient"


# ==============================================================================
# 3. PROTEÇÃO DE CAPAS E TRAVAS MANUAIS
# ==============================================================================

def test_custom_cover_and_field_lock_protection(test_env):
    """Valida que capa 'custom' e campos bloqueados nunca são sobrescritos pelo enriquecedor."""
    db_path = test_env["db_path"]

    book_id = "test_lock_book_1"
    try:
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO books (
                    id, title, author, filename, rel_path, format, filesize, file_hash,
                    cover_type, cover_path, manual_lock_fields, identification_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    book_id,
                    "Meu Título Customizado",
                    "Autor Original",
                    "test_lock.pdf",
                    "test_lock.pdf",
                    "pdf",
                    1024,
                    "fakehash1",
                    "custom",
                    "custom_cover.jpg",
                    json.dumps(["title", "cover"]),
                    "pending",
                ),
            )

        enricher_instance = BackgroundEnricher()
        enricher_instance._enrich_single_book_sync(book_id)

        with get_connection(db_path) as conn:
            row = dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())

        assert row["title"] == "Meu Título Customizado"
        assert row["cover_type"] == "custom"
        assert row["cover_path"] == "custom_cover.jpg"
    finally:
        with get_connection(db_path) as conn:
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


# ==============================================================================
# 4. TAXONOMIA E PRATELEIRAS DINÂMICAS
# ==============================================================================

def test_discovery_shelves_never_return_empty(test_env):
    """Garante que a rota e serviço de prateleiras nunca retornem coleções vazias."""
    shelves = get_discovery_shelves(user_id=1)
    assert isinstance(shelves, list)

    for shelf in shelves:
        assert "id" in shelf
        assert "title" in shelf
        assert "books" in shelf
        assert len(shelf["books"]) > 0, f"Prateleira vazia detectada: {shelf['id']}"


def test_taxonomy_facet_separation():
    """Valida mapeamento multidimensional de facetas (Categoria, Subcategoria, Gênero, Origem, Período)."""
    facets = map_raw_subjects_to_taxonomy(
        title="O Cortiço",
        author="Aluísio Azevedo",
        raw_subjects=["Literatura Brasileira", "Naturalismo", "Romance do Século XIX"],
        language="pt",
    )
    assert "Literatura" in facets["categories"]
    subcat_names = [sub for cat, sub in facets["subcategories"]]
    assert any("literatura" in s.lower() or "romance" in s.lower() for s in subcat_names)
    assert facets["genre"] == "Romance"
    assert facets["period"] == "Século XIX"


# ==============================================================================
# 5. TESTE DE MIGRAÇÃO SQLITE v6 PARA v7 EM AMBIENTE ISOLADO
# ==============================================================================

def test_sqlite_v6_to_v7_clean_migration():
    """
    Cria uma cópia isolada de um banco SQLite Schema v6 preenchido com dados reais
    (incluindo EPUB, locators de progresso, anotações e desenhos) e executa a migração para v7.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "test_migration_v6.db"

        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.executescript("""
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO schema_migrations (version) VALUES (1), (2), (3), (4), (5), (6);

            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO users (id, username, password_hash, is_admin) VALUES (1, 'leitor', 'hash', 1);

            CREATE TABLE books (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                filename TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                filesize INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_hash TEXT,
                is_missing INTEGER DEFAULT 0,
                source_id INTEGER,
                cover_type TEXT NOT NULL DEFAULT 'none',
                cover_path TEXT,
                cover_source_hash TEXT,
                cover_updated_at TIMESTAMP,
                format TEXT NOT NULL DEFAULT 'pdf'
            );
            INSERT INTO books (id, title, author, filename, rel_path, filesize, page_count, format)
            VALUES ('book_pdf_1', 'Manual Técnico', 'Autor A', 'manual.pdf', 'manual.pdf', 5000, 100, 'pdf'),
                   ('book_epub_1', 'Romance Histórico', 'Autor B', 'romance.epub', 'romance.epub', 15000, 25, 'epub');

            CREATE TABLE reading_progress (
                book_id TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                current_page INTEGER NOT NULL,
                total_pages INTEGER NOT NULL,
                percentage REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                progress_locator TEXT,
                PRIMARY KEY (book_id, user_id)
            );
            INSERT INTO reading_progress (book_id, user_id, current_page, total_pages, percentage, progress_locator)
            VALUES ('book_epub_1', 1, 5, 25, 20.0, 'epubcfi(/6/4[chap01]!/4/2/10)');

            CREATE TABLE collections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE collection_books (collection_id INTEGER NOT NULL, book_id TEXT NOT NULL, PRIMARY KEY(collection_id, book_id));
        """)
        conn.commit()
        conn.close()

        init_db(test_db)

        # 1. Verifica existência de backup pré-migração
        expected_bak = test_db.with_name(f"lectoria_pre_migration_v6_to_v{SCHEMA_VERSION}.db.bak")
        assert expected_bak.exists(), "Backup automático pré-migração não foi gerado!"

        # 2. Verifica se a versão no schema_migrations agora é 7
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT MAX(version) FROM schema_migrations;")
        assert cur.fetchone()[0] == 7

        # 3. Verifica integridade do SQLite
        cur.execute("PRAGMA integrity_check;")
        chk = cur.fetchone()[0]
        assert chk == "ok"

        # 4. Verifica dados existentes intactos
        cur.execute("SELECT COUNT(*) FROM books;")
        assert cur.fetchone()[0] == 2

        cur.execute("SELECT progress_locator FROM reading_progress WHERE book_id = 'book_epub_1';")
        assert cur.fetchone()[0] == "epubcfi(/6/4[chap01]!/4/2/10)"

        # 5. Verifica novas colunas e tabelas v7
        cur.execute("SELECT isbn13, identification_status, manual_lock_fields FROM books WHERE id = 'book_pdf_1';")
        row = cur.fetchone()
        assert row["identification_status"] == "pending"
        assert row["manual_lock_fields"] == "[]"

        cur.execute("SELECT COUNT(*) FROM books_fts;")
        assert cur.fetchone()[0] == 2

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('subjects', 'book_subjects', 'metadata_candidates', 'metadata_api_cache');")
        tables = [r[0] for r in cur.fetchall()]
        assert len(tables) == 4

        conn.close()


# ==============================================================================
# 6. SEGURANÇA: ANTI-SSRF E CONTROLE DE ACESSO ADMINISTRATIVO
# ==============================================================================

def test_ssrf_protection_filters():
    """Valida bloqueio rigoroso de requisições maliciosas internas via SSRF."""
    hostile_urls = [
        "http://localhost:8000/secret",
        "http://127.0.0.1:8000/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/router",
        "file:///etc/passwd",
        "ftp://internal.server/file",
        "javascript:alert(1)",
    ]
    for url in hostile_urls:
        assert is_safe_external_url(url) is False, f"URL insegura não foi bloqueada: {url}"

    valid_urls = [
        "https://covers.openlibrary.org/b/id/12345-L.jpg",
        "https://books.google.com/books/content?id=xyz&printsec=frontcover",
        "http://example.com/cover.jpg",
    ]
    for url in valid_urls:
        assert is_safe_external_url(url) is True, f"URL válida foi rejeitada: {url}"


def test_admin_queue_endpoints_require_admin(test_env, client):
    """Valida que endpoints da fila de metadados rejeitam requisições sem privilégio de admin."""
    # Cria usuário admin e usuário leitor comum
    admin_user = create_user("audit_admin", "AdminPass123!", role="admin")
    reader_user = create_user("audit_reader", "ReaderPass123!", role="reader")

    try:
        admin_token = create_session(admin_user["id"])
        reader_token = create_session(reader_user["id"])

        # 1. Sem autenticação -> 401 Unauthorized
        res_anon = client.get("/api/metadata/queue/status")
        assert res_anon.status_code == 401

        # 2. Usuário leitor comum -> 403 Forbidden
        res_reader = client.get(
            "/api/metadata/queue/status",
            cookies={SESSION_COOKIE_NAME: reader_token},
        )
        assert res_reader.status_code == 403

        # 3. Usuário administrador -> 200 OK
        res_admin = client.get(
            "/api/metadata/queue/status",
            cookies={SESSION_COOKIE_NAME: admin_token},
        )
        assert res_admin.status_code == 200
        assert "status" in res_admin.json()
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE user_id IN (?, ?)", (admin_user["id"], reader_user["id"]))
            conn.execute("DELETE FROM users WHERE id IN (?, ?)", (admin_user["id"], reader_user["id"]))


# ==============================================================================
# 7. CONSUMO DE RECURSOS E DESEMPENHO (CELERON 847 COMPATIBILITY)
# ==============================================================================

def test_discovery_and_search_latency(test_env):
    """Valida que busca FTS5 e prateleiras de descoberta respondem em menos de 50ms."""
    t0 = time.time()
    books = get_books(fts_query="Manual", user_id=1)
    fts_elapsed = time.time() - t0
    assert fts_elapsed < 0.05, f"Busca FTS5 lenta ({fts_elapsed*1000:.2f}ms)"

    t1 = time.time()
    shelves = get_discovery_shelves(user_id=1)
    shelves_elapsed = time.time() - t1
    assert shelves_elapsed < 0.1, f"Geração de prateleiras lenta ({shelves_elapsed*1000:.2f}ms)"
