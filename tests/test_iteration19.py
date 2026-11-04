"""
Testes automatizados da Iteração 19 — Lectoria:
Validação Real, Hardening de Segurança, Migração v5 -> v6,
Cabeçalhos HTTP de Proteção e Cenários Sintéticos de 5, 50 e 200 páginas.
"""

from pathlib import Path
import sqlite3
import pytest
from starlette.testclient import TestClient

import config
from database import init_db, get_db, SCHEMA_VERSION
from epub_service import (
    validate_epub_archive,
    extract_epub_metadata,
    EpubValidationError,
)
from library import scan_library, get_books, get_book
from progress import save_progress, get_progress
import main


class TestDatabaseMigrationV5toV6:
    """Valida migração idempotente do esquema v5 para v6 e integridade de dados pré-existentes."""

    def test_migration_v5_to_v6_preserves_data(self, tmp_path, monkeypatch):
        test_db = tmp_path / "legacy_v5.db"
        monkeypatch.setattr("config.DB_PATH", test_db)

        # 1. Cria esquema sintético versão 5 manualmente
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
        for v in range(1, 6):
            cursor.execute("INSERT INTO schema_migrations (version) VALUES (?);", (v,))

        cursor.execute("""
            CREATE TABLE library_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_scanned_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("INSERT INTO library_sources (id, name, path) VALUES (1, 'Biblioteca Principal', ?);", (str(tmp_path),))

        cursor.execute("""
            CREATE TABLE books (
                id TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES library_sources(id),
                title TEXT NOT NULL,
                author TEXT,
                filename TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                filesize INTEGER NOT NULL,
                page_count INTEGER,
                file_hash TEXT,
                cover_type TEXT NOT NULL DEFAULT 'none',
                cover_path TEXT,
                cover_source_hash TEXT,
                cover_updated_at TIMESTAMP,
                is_missing INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("""
            CREATE TABLE reading_progress (
                user_id INTEGER NOT NULL,
                book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                current_page INTEGER NOT NULL,
                total_pages INTEGER NOT NULL,
                percentage REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, book_id)
            );
        """)

        # Insere dados existentes da versão 5
        cursor.execute("""
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, page_count)
            VALUES ('book-pdf-123', 1, 'Manual Antigo', 'Engenheiro', 'manual.pdf', 'manual.pdf', 1048576, 50);
        """)
        cursor.execute("""
            INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage)
            VALUES (1, 'book-pdf-123', 25, 50, 50.0);
        """)
        conn.commit()
        conn.close()

        # 2. Executa init_db() para disparar migração v6
        init_db(test_db)

        # 3. Valida resultado pós-migração
        with get_db(test_db) as c:
            cur = c.cursor()
            cur.execute("SELECT MAX(version) FROM schema_migrations;")
            assert cur.fetchone()[0] == 6

            # Valida que books possui coluna format com padrão 'pdf'
            cur.execute("SELECT format, title FROM books WHERE id = 'book-pdf-123';")
            row = cur.fetchone()
            assert row["format"] == "pdf"
            assert row["title"] == "Manual Antigo"

            # Valida que reading_progress possui coluna progress_locator e preservou página
            cur.execute("SELECT current_page, percentage, progress_locator FROM reading_progress WHERE book_id = 'book-pdf-123';")
            prog = cur.fetchone()
            assert prog["current_page"] == 25
            assert prog["percentage"] == 50.0
            assert prog["progress_locator"] is None

        # 4. Valida idempotência: executar novamente não causa erro
        init_db(test_db)


class TestSecurityHardeningAndHeaders:
    """Valida cabeçalhos de segurança HTTP e restrições na API."""

    def test_security_headers_present_on_all_responses(self, monkeypatch):
        client = TestClient(main.app)
        monkeypatch.setattr(main, "require_user", lambda r: {"id": 1, "username": "admin", "role": "admin"})

        res = client.get("/")
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("x-frame-options") == "SAMEORIGIN"
        assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_reader_html_uses_safe_script_loading(self):
        """Valida que o reader.html não inclui scripts de forma pesada ou insegura."""
        html = Path("static/reader.html").read_text(encoding="utf-8")
        assert "epub.min.js" not in html
        assert "jszip.min.js" not in html
        assert "reader.js" in html


class TestSyntheticPdfDocuments:
    """Valida leitura e indexação em documentos PDF com 5, 50 e 200 páginas."""

    @pytest.fixture
    def setup_synthetic_env(self, tmp_path, monkeypatch):
        test_db = tmp_path / "synthetic.db"
        test_lib = tmp_path / "books"
        test_lib.mkdir()
        covers_dir = tmp_path / "covers"
        covers_dir.mkdir()

        monkeypatch.setattr("config.DB_PATH", test_db)
        monkeypatch.setattr("config.LIBRARY_DIR", test_lib)
        monkeypatch.setattr("config.COVERS_DIR", covers_dir)

        init_db(test_db)
        return {"lib": test_lib, "db": test_db}

    def _generate_synthetic_pdf(self, path: Path, num_pages: int):
        import pypdf
        writer = pypdf.PdfWriter()
        for i in range(num_pages):
            writer.add_blank_page(width=400, height=600)
        with open(path, "wb") as f:
            writer.write(f)

    @pytest.mark.parametrize("page_count", [5, 50, 200])
    def test_synthetic_pdf_scanned_and_progress_tracked(self, setup_synthetic_env, page_count):
        lib = setup_synthetic_env["lib"]
        pdf_file = lib / f"doc_{page_count}_pages.pdf"
        self._generate_synthetic_pdf(pdf_file, page_count)

        scan_library()

        books = get_books(format_filter="pdf")
        matching = [b for b in books if b["filename"] == f"doc_{page_count}_pages.pdf"]
        assert len(matching) == 1
        book = matching[0]
        assert book["page_count"] == page_count
        assert book["format"] == "pdf"

        # Simula leitura até metade do documento
        target_page = max(1, page_count // 2)
        save_progress(book["id"], current_page=target_page, total_pages=page_count, user_id=1)

        prog = get_progress(book["id"], user_id=1)
        assert prog["current_page"] == target_page
        expected_pct = round((target_page / page_count) * 100, 1)
        assert abs(prog["percentage"] - expected_pct) < 0.2

