"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — API REST de Localização Assistida (`/api/knowledge/lookup`).

Cobre os requisitos da Seção 10 e Seção 17:
- Localização assistida para todos os 5 tipos aprovados (`concept`, `reflection`, `highlight`, `note`, `book`).
- Filtro opcional por `entity_type` e `book_id`.
- Correspondência insensível a acentuação e caixa em português (Unicode NFC/NFD).
- Autorização obrigatória: nunca retorna conceitos, reflexões ou anotações privadas de outros usuários,
  nem livros ausentes ou de fontes desativadas.
- Controle de consultas vazias (retorna itens autorizados dentro do limite) e bloqueio de consultas
  acima de `MAX_SEARCH_QUERY_LEN` (`422`) ou `limit` fora de `1..50` (`422`).
"""

from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def lookup_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_lookup_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "ellul.pdf").write_bytes(b"%PDF-1.4 sample 1")
    (lib_dir / "heidegger.epub").write_bytes(b"PK\x03\x04 sample 2")
    (lib_dir / "inativo.pdf").write_bytes(b"%PDF-1.4 sample 3")

    old_db, old_cov, old_lib, old_bak = (
        config.DB_PATH,
        config.COVERS_DIR,
        config.LIBRARY_DIR,
        config.BACKUPS_DIR,
    )
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    config.BACKUPS_DIR = backups_dir

    database.init_db(db_path)

    alice = auth_service.create_user("alice_lk_api", "Password123!", "Alice", role="reader")
    bob = auth_service.create_user("bob_lk_api", "Password123!", "Bob", role="reader")

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES
            (1, 'Ativa', ?, 1),
            (2, 'Inativa', ?, 0);
            """,
            (str(lib_dir), str(lib_dir / "inativa")),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book-ellul', 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul.pdf', 'ellul.pdf', 4096, 'pdf', 320, 1, 0),
            ('book-heidegger', 'A Questão da Técnica', 'Martin Heidegger', 'heidegger.epub', 'heidegger.epub', 4096, 'epub', 90, 1, 0),
            ('book-inactive', 'Técnica Secreta Inativa', 'Autor Oculto', 'inativo.pdf', 'inativo.pdf', 4096, 'pdf', 100, 2, 0);
            """
        )

    user_map = {"alice": alice["id"], "bob": bob["id"]}

    def make_client(user_key: Optional[str], include_csrf: bool = True) -> Tuple[TestClient, str]:
        client = TestClient(app_main.app)
        if not user_key:
            return client, ""
        token = auth_service.create_session(user_map[user_key])
        client.cookies.set(auth_service.SESSION_COOKIE_NAME, token)
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        if include_csrf:
            client.headers["x-csrf-token"] = csrf
        return client, csrf

    try:
        yield {"make_client": make_client, "alice": alice, "bob": bob}
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR, config.BACKUPS_DIR = (
            old_db,
            old_cov,
            old_lib,
            old_bak,
        )


def test_lookup_assisted_across_all_five_entity_types_and_isolation(lookup_api_env):
    """
    Valida que `/api/knowledge/lookup` encontra entidades referenciáveis dos 5 tipos
    para o usuário autenticado (com e sem acentos), respeita `entity_type` e `book_id`,
    e jamais retorna dados privados de outros usuários ou de fontes inativas.
    """
    alice, _ = lookup_api_env["make_client"]("alice")
    bob, _ = lookup_api_env["make_client"]("bob")

    # Alice cria conceito, reflexão, destaque e nota contendo "Racionalização"
    c_alice = alice.post(
        "/api/knowledge/concepts",
        json={"name": "Racionalização Técnica", "description": "Definição de Ellul"},
    ).json()["concept"]
    r_alice = alice.post(
        "/api/knowledge/reflections",
        json={"title": "Síntese sobre Racionalização", "body_markdown": "Ensaio", "primary_book_id": "book-ellul"},
    ).json()["reflection"]
    hl_alice = alice.post(
        "/api/books/book-ellul/highlights",
        json={
            "page": 42,
            "selected_text": "A racionalização transforma todos os meios.",
            "rects": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.03}],
        },
    ).json()["highlight"]
    nt_alice = alice.post(
        "/api/books/book-ellul/notes",
        json={"page": 42, "content": "Nota de margem sobre racionalização."},
    ).json()["note"]

    # Bob cria itens privados e 1 nota pública
    bob.post("/api/knowledge/concepts", json={"name": "Racionalização Privada do Bob"})
    bob.post("/api/books/book-ellul/notes", json={"page": 50, "content": "Racionalização secreta do Bob", "visibility": "private"})
    nt_bob_pub = bob.post(
        "/api/books/book-ellul/notes",
        json={"page": 55, "content": "Racionalização pública compartilhada pelo Bob", "visibility": "public"},
    ).json()["note"]

    # Busca sem acento por "racionalizacao" para Alice
    res = alice.get("/api/knowledge/lookup?q=racionalizacao")
    assert res.status_code == 200
    assert res.headers.get("cache-control") == "private, no-store"
    data = res.json()
    found_ids = {(item["type"], item["id"]) for item in data["items"]}

    assert ("concept", c_alice["id"]) in found_ids
    assert ("reflection", r_alice["id"]) in found_ids
    assert ("highlight", hl_alice["id"]) in found_ids
    assert ("note", nt_alice["id"]) in found_ids
    assert ("note", nt_bob_pub["id"]) in found_ids
    assert "Privada do Bob" not in res.text
    assert "secreta do Bob" not in res.text

    # Busca filtrando por entity_type=book e q=tecnica (não deve retornar book-inactive)
    res_books = alice.get("/api/knowledge/lookup?q=tecnica&entity_type=book").json()
    book_ids = {b["id"] for b in res_books["items"]}
    assert book_ids == {"book-ellul", "book-heidegger"}
    assert "book-inactive" not in book_ids

    # Filtro por book_id inexistente ou inativo retorna 404
    assert alice.get("/api/knowledge/lookup?book_id=book-inactive").status_code == 404
    assert alice.get("/api/knowledge/lookup?book_id=book-nonexistent").status_code == 404

    # Limites e tipos inválidos retornam 422
    assert alice.get("/api/knowledge/lookup?limit=0").status_code == 422
    assert alice.get("/api/knowledge/lookup?limit=51").status_code == 422
    assert alice.get("/api/knowledge/lookup?entity_type=invalid_type").status_code == 422
    assert alice.get(f"/api/knowledge/lookup?q={'a' * 301}").status_code == 422
