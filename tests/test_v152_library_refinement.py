import pytest
import sqlite3
import os
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
import config
import database
import taxonomy_service
import library
import metadata_service
import auth_service


@pytest.fixture
def v152_env(tmp_path, monkeypatch):
    """Fixture with isolated test database with v9 schema."""
    test_db = tmp_path / "test_lectoria_v152.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    # Initialize schema
    database.init_db()

    # Create admin user for tests
    auth_service.create_user(username="admin", password="password", role="admin")

    with TestClient(app) as test_client:
        yield test_client, test_db, test_library


def test_schema_v9_tables_and_columns(v152_env):
    test_client, test_db, test_library = v152_env
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_migrations")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 9

        # Verify categories table
        cur.execute("PRAGMA table_info(categories)")
        cols = {c["name"] for c in cur.fetchall()}
        assert {"id", "name", "description", "display_order", "is_custom"}.issubset(cols)

        # Verify book_categories table
        cur.execute("PRAGMA table_info(book_categories)")
        bc_cols = {c["name"] for c in cur.fetchall()}
        assert {"book_id", "category_id", "is_manual"}.issubset(bc_cols)

        # Verify reading_progress.is_completed_manual
        cur.execute("PRAGMA table_info(reading_progress)")
        rp_cols = {c["name"] for c in cur.fetchall()}
        assert "is_completed_manual" in rp_cols
    finally:
        conn.close()


def test_categories_crud_and_reorder_merge_delete_safety(v152_env):
    test_client, test_db, test_library = v152_env

    # Login as admin
    login_res = test_client.post("/api/auth/login", json={"username": "admin", "password": "password"})
    assert login_res.status_code == 200

    # 1. List default categories
    res = test_client.get("/api/categories")
    assert res.status_code == 200
    cats = res.json()["categories"]
    assert len(cats) >= 5
    cat_names = [c["name"] for c in cats]
    assert "Ciência e tecnologia" in cat_names
    assert "Filosofia" in cat_names

    # 2. Create new category
    create_res = test_client.post("/api/categories", json={
        "name": "Inteligência Artificial",
        "description": "Obras sobre redes neurais e machine learning",
        "display_order": 99
    })
    assert create_res.status_code == 200
    new_cat = create_res.json()
    assert new_cat["name"] == "Inteligência Artificial"
    new_cat_id = new_cat["id"]

    # 3. Update category
    up_res = test_client.put(f"/api/categories/{new_cat_id}", json={
        "name": "IA e Aprendizado de Máquina",
        "description": "Atualizado",
        "display_order": 45
    })
    assert up_res.status_code == 200
    assert up_res.json()["name"] == "IA e Aprendizado de Máquina"

    # 4. Insert a test book into test database
    (test_library / "dl.pdf").write_bytes(b"%PDF-1.4 dummy")
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO books (id, title, author, format, filesize, filename, rel_path)
            VALUES ('book-test-1', 'Deep Learning', 'Ian Goodfellow', 'pdf', 1048576, 'dl.pdf', 'dl.pdf')
        """)
        conn.commit()
    finally:
        conn.close()

    # Assign category to book
    cat_assign_res = test_client.put("/api/books/book-test-1/categories", json={
        "categories": ["IA e Aprendizado de Máquina"],
        "is_manual": True
    })
    assert cat_assign_res.status_code == 200
    assert "IA e Aprendizado de Máquina" in cat_assign_res.json()["categories"]

    # Verify book count in category
    res_after = test_client.get("/api/categories")
    target_c = next(c for c in res_after.json()["categories"] if c["id"] == new_cat_id)
    assert target_c["book_count"] == 1

    # 5. Merge category into "Ciência e tecnologia"
    science_cat = next(c for c in res_after.json()["categories"] if c["name"] == "Ciência e tecnologia")
    merge_res = test_client.post("/api/categories/merge", json={
        "source_category_id": new_cat_id,
        "target_category_id": science_cat["id"]
    })
    assert merge_res.status_code == 200
    assert merge_res.json()["merged_books"] == 1

    # Verify source category was removed and book now belongs to Ciência e tecnologia
    b_res = test_client.get("/api/books/book-test-1")
    assert b_res.status_code == 200
    assert "Ciência e tecnologia" in b_res.json()["taxonomy"]["categories"]

    # 6. Delete category test: deleting a category does NOT delete books
    temp_cat_res = test_client.post("/api/categories", json={"name": "Temp Cat"})
    temp_id = temp_cat_res.json()["id"]
    test_client.put("/api/books/book-test-1/categories", json={"categories": ["Temp Cat"], "is_manual": True})

    del_res = test_client.delete(f"/api/categories/{temp_id}")
    assert del_res.status_code == 200
    # Book must still exist!
    book_check = test_client.get("/api/books/book-test-1")
    assert book_check.status_code == 200
    assert book_check.json()["title"] == "Deep Learning"


from tests.conftest import create_sample_pdf


def test_reading_status_and_multiuser_isolation(v152_env):
    test_client, test_db, test_library = v152_env

    # Create two users: user1 and user2
    test_client.post("/api/auth/login", json={"username": "admin", "password": "password"})
    test_client.post("/api/users", json={"username": "leitor_alpha", "password": "secretpassword1", "role": "reader"})
    test_client.post("/api/users", json={"username": "leitor_beta", "password": "secretpassword2", "role": "reader"})

    # Create a 1-page sample document
    conto_file = test_library / "conto.pdf"
    create_sample_pdf(conto_file, "Conto Curto", "Machado de Assis", pages=1)

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO books (id, title, author, format, filesize, filename, rel_path, page_count)
            VALUES ('book-short-1', 'Conto Curto', 'Machado de Assis', 'pdf', 50000, 'conto.pdf', 'conto.pdf', 1)
        """)
        conn.commit()
    finally:
        conn.close()

    # Log in as leitor_alpha
    alpha_client = TestClient(app)
    res_login_a = alpha_client.post("/api/auth/login", json={"username": "leitor_alpha", "password": "secretpassword1"})
    assert res_login_a.status_code == 200

    # User Alpha opens document on page 1 (simulating progress update via /api/books/{id}/progress)
    prog_res = alpha_client.post("/api/books/book-short-1/progress", json={"current_page": 1, "total_pages": 1})
    assert prog_res.status_code == 200

    # Check book status for Alpha: opening 1-page document should NOT falsely mark it as completed without explicit finished flag
    alpha_books = alpha_client.get("/api/books").json()["books"]
    alpha_b1 = next((b for b in alpha_books if b["id"] == "book-short-1"), None)
    assert alpha_b1 is not None
    assert alpha_b1["is_completed"] is False or alpha_b1["is_completed"] == 0

    # Alpha explicitly marks book as completed
    status_res = alpha_client.put("/api/books/book-short-1/reading-status", json={"status": "completed"})
    assert status_res.status_code == 200
    assert status_res.json()["book"]["is_completed_manual"] == 1

    # Verify Alpha sees it as completed
    alpha_books_completed = alpha_client.get("/api/books?status=completed").json()["books"]
    assert any(b["id"] == "book-short-1" for b in alpha_books_completed)

    # Multi-user isolation: User Beta MUST NOT see book-short-1 as completed!
    beta_client = TestClient(app)
    res_login_b = beta_client.post("/api/auth/login", json={"username": "leitor_beta", "password": "secretpassword2"})
    assert res_login_b.status_code == 200

    beta_books_completed = beta_client.get("/api/books?status=completed").json()["books"]
    assert not any(b["id"] == "book-short-1" for b in beta_books_completed)

    # Beta sees it under unread
    beta_books_unread = beta_client.get("/api/books?status=unread").json()["books"]
    assert any(b["id"] == "book-short-1" for b in beta_books_unread)

    # Alpha marks as unread again
    reset_res = alpha_client.put("/api/books/book-short-1/reading-status", json={"status": "unread"})
    assert reset_res.status_code == 200
    assert reset_res.json()["book"]["is_completed_manual"] == 0


def test_contextual_classification_rules():
    # 1. Tanenbaum operating systems must be classified under Ciência e tecnologia (not Arts & Culture)
    tax_os = taxonomy_service.map_raw_subjects_to_taxonomy(
        raw_subjects=["Operating Systems", "Computer Science"],
        title="Sistemas Operacionais Modernos",
        author="Andrew S. Tanenbaum, Herbert Bos",
        description="Conceitos de processos, memória virtual, sistemas de arquivos e escalonamento.",
    )
    assert "Ciência e tecnologia" in tax_os["categories"]
    assert "Artes e cultura" not in tax_os["categories"]

    # 2. Tanenbaum Computer Networks
    tax_net = taxonomy_service.map_raw_subjects_to_taxonomy(
        raw_subjects=[],
        title="Redes de Computadores",
        author="Tanenbaum, Andrew S.",
        description="Protocolos TCP/IP, Ethernet, roteamento e segurança de redes."
    )
    assert "Ciência e tecnologia" in tax_net["categories"]
    assert "Artes e cultura" not in tax_net["categories"]

    # 3. Platão -> Filosofia
    tax_plato = taxonomy_service.map_raw_subjects_to_taxonomy(
        raw_subjects=[],
        title="A República",
        author="Platão",
        description="Diálogo socrático sobre a justiça e a ordem da cidade-estado."
    )
    assert "Filosofia" in tax_plato["categories"]

    # 4. Guidorizzi -> Ciência e tecnologia / Matemática
    tax_calc = taxonomy_service.map_raw_subjects_to_taxonomy(
        raw_subjects=[],
        title="Um Curso de Cálculo - Volume 1",
        author="Hamilton Luiz Guidorizzi",
        description="Limites, derivadas, integrais e aplicações de funções de uma variável."
    )
    assert "Ciência e tecnologia" in tax_calc["categories"]


def test_brasilapi_client_and_author_normalization():
    # Author normalization
    assert metadata_service.normalize_author_name("Tanenbaum, Andrew S.") == "Andrew S. Tanenbaum"
    assert metadata_service.normalize_author_name("Machado de Assis, Joaquim Maria") == "Joaquim Maria Machado de Assis"
    assert metadata_service.normalize_author_name("Platão") == "Platão"

    # BrasilAPI client instantiation and endpoint building
    assert metadata_service.BRASILAPI_BASE_URL == "https://brasilapi.com.br/api/isbn/v1"
    assert hasattr(metadata_service.BrasilApiClient, "lookup_isbn")


def test_discovery_shelves_with_custom_categories_and_deduplication(v152_env):
    test_client, test_db, test_library = v152_env

    # Login as admin
    login_res = test_client.post("/api/auth/login", json={"username": "admin", "password": "password"})
    assert login_res.status_code == 200

    # Write dummy files
    (test_library / "redes.pdf").write_bytes(b"%PDF-1.4 dummy")
    (test_library / "leis.pdf").write_bytes(b"%PDF-1.4 dummy")
    (test_library / "notas.pdf").write_bytes(b"%PDF-1.4 dummy")

    # Insert sample books with different categories
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO books (id, title, author, format, filesize, filename, rel_path, description)
            VALUES 
            ('b-sci-1', 'Redes de Computadores', 'Andrew S. Tanenbaum', 'pdf', 1000, 'redes.pdf', 'redes.pdf', 'Redes de dados'),
            ('b-phil-1', 'As Leis', 'Platão', 'pdf', 1000, 'leis.pdf', 'leis.pdf', 'Diálogo socrático'),
            ('b-uncat-1', 'Anotações Avulsas', 'Desconhecido', 'pdf', 500, 'notas.pdf', 'notas.pdf', '')
        """)
        conn.commit()
    finally:
        conn.close()

    # Assign categories using set_book_categories
    taxonomy_service.set_book_categories("b-sci-1", ["Ciência e tecnologia"], is_manual=True)
    taxonomy_service.set_book_categories("b-phil-1", ["Filosofia"], is_manual=True)

    res = test_client.get("/api/discovery/shelves")
    assert res.status_code == 200
    data = res.json()
    shelves = data["shelves"]
    assert len(shelves) >= 2

    # Verify shelves contain filter_action
    for s in shelves:
        assert "filter_action" in s
        assert "type" in s["filter_action"]

    # Verify "Sem categoria" filter on library books
    uncat_res = test_client.get("/api/books?uncategorized=true")
    assert uncat_res.status_code == 200
    uncat_books = uncat_res.json()["books"]
    assert any(b["id"] == "b-uncat-1" for b in uncat_books)
    assert not any(b["id"] == "b-sci-1" for b in uncat_books)
