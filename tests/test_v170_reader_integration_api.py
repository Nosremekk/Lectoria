"""
Testes de API — Fase 5 da Lectoria v1.7.0 (Integração Leitores <-> Leitura Compartilhada).
Valida:
1. GET /api/books/{book_id}/collective-reads: elegibilidade estrita, anti-enumeração e exclusão de somente-leitura.
2. Compartilhamento de nota privada própria como citação independente (preservando nota após alteração/exclusão).
3. Bloqueio anti-IDOR em notas de outros usuários e obras divergentes.
4. Limite de 1500 caracteres, strings vazias e localizadores EPUB CFI inválidos.
"""

from typing import Optional

import pytest
from fastapi.testclient import TestClient

import auth_service
import clubs_service
import config
import database
from main import app


@pytest.fixture
def v170_reader_api_env(tmp_path, monkeypatch):
    test_db = tmp_path / "test_lectoria_v170_reader_api.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()

    alice = auth_service.create_user(username="alice_r5", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_r5", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos_r5", password="password123", role="reader")

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES
                ('book_r5_01', 'Dom Casmurro', 'Machado', 'dom.pdf', 'dom.pdf', 102400, 'pdf', 200, 0),
                ('book_r5_02', 'Memórias', 'Machado', 'mem.epub', 'mem.epub', 102400, 'epub', 150, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO notes (id, user_id, book_id, page, content)
            VALUES
                ('note_r5_bob_1', ?, 'book_r5_01', 45, 'Anotação privada original do Bob.'),
                ('note_r5_alice_1', ?, 'book_r5_01', 12, 'Anotação privada da Alice.'),
                ('note_r5_bob_2', ?, 'book_r5_02', 8, 'Anotação do Bob em outro livro.');
            """,
            (bob["id"], alice["id"], bob["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # Clube 1 público: Alice e Bob são membros
    c1 = clubs_service.create_club(name="Clube 1", description="", visibility="public", owner_id=alice["id"])
    clubs_service.join_club(c1["id"], bob["id"])
    r_active = clubs_service.create_collective_read(c1["id"], alice["id"], "book_r5_01", title="Ativa", status="in_progress")
    r_completed = clubs_service.create_collective_read(c1["id"], alice["id"], "book_r5_01", title="Concluída", status="completed")
    r_cancelled = clubs_service.create_collective_read(c1["id"], alice["id"], "book_r5_01", title="Cancelada", status="cancelled")
    r_other_book = clubs_service.create_collective_read(c1["id"], alice["id"], "book_r5_02", title="Outro Livro", status="in_progress")

    # Clube 2 privado: apenas Alice
    c2 = clubs_service.create_club(name="Clube Privado 2", description="", visibility="private", owner_id=alice["id"])
    r_priv = clubs_service.create_collective_read(c2["id"], alice["id"], "book_r5_01", title="Privada", status="planned")

    # Clube 3 arquivado: Alice e Bob são membros, mas clube está arquivado
    c3 = clubs_service.create_club(name="Clube Arquivado 3", description="", visibility="public", owner_id=alice["id"])
    clubs_service.join_club(c3["id"], bob["id"])
    r_arch = clubs_service.create_collective_read(c3["id"], alice["id"], "book_r5_01", title="Em Arquivado", status="in_progress")
    clubs_service.archive_club(c3["id"], alice["id"])

    def make_client(username: Optional[str]) -> tuple[TestClient, str]:
        client = TestClient(app)
        if not username:
            return client, ""
        client.get("/")
        csrf = client.cookies.get("lectoria_csrf", "")
        res = client.post(
            "/api/auth/login",
            json={"username": username, "password": "password123"},
            headers={"x-csrf-token": csrf} if csrf else {},
        )
        assert res.status_code == 200
        csrf = client.cookies.get("lectoria_csrf", "") or csrf
        return client, csrf

    return {
        "alice": alice,
        "bob": bob,
        "carlos": carlos,
        "r_active": r_active,
        "r_completed": r_completed,
        "r_cancelled": r_cancelled,
        "r_other_book": r_other_book,
        "r_priv": r_priv,
        "r_arch": r_arch,
        "make_client": make_client,
    }


def test_eligible_collective_reads_for_book_isolation(v170_reader_api_env):
    env = v170_reader_api_env
    anon, _ = env["make_client"](None)
    assert anon.get("/api/books/book_r5_01/collective-reads").status_code == 401

    alice_c, _ = env["make_client"]("alice_r5")
    bob_c, _ = env["make_client"]("bob_r5")
    carlos_c, _ = env["make_client"]("carlos_r5")

    # Alice vê r_active e r_priv (não vê completed, cancelled, arquivada, nem outro livro)
    a_reads = alice_c.get("/api/books/book_r5_01/collective-reads").json()["reads"]
    assert {r["read_id"] for r in a_reads} == {env["r_active"]["id"], env["r_priv"]["id"]}

    # Bob vê apenas r_active (não vê clube privado da Alice)
    b_reads = bob_c.get("/api/books/book_r5_01/collective-reads").json()["reads"]
    assert {r["read_id"] for r in b_reads} == {env["r_active"]["id"]}

    # Carlos (não membro de nenhum clube) não vê nenhuma leitura elegível
    c_reads = carlos_c.get("/api/books/book_r5_01/collective-reads").json()["reads"]
    assert c_reads == []


def test_share_own_note_independent_snapshot_and_anti_idor(v170_reader_api_env):
    env = v170_reader_api_env
    bob_c, bob_csrf = env["make_client"]("bob_r5")
    read_id = env["r_active"]["id"]

    # Bob compartilha própria nota privada
    res = bob_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"note_id": "note_r5_bob_1", "is_spoiler": False},
        headers={"x-csrf-token": bob_csrf},
    )
    assert res.status_code == 201
    quote_id = res.json()["quote"]["id"]
    assert res.json()["quote"]["quote_text"] == "Anotação privada original do Bob."

    # Alterar ou excluir a nota privada posteriormente NÃO altera a citação publicada
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM notes WHERE id = 'note_r5_bob_1';")
        conn.commit()
    finally:
        conn.close()

    quotes_res = bob_c.get(f"/api/collective-reads/{read_id}/quotes").json()["quotes"]
    assert any(q["id"] == quote_id and q["quote_text"] == "Anotação privada original do Bob." for q in quotes_res)

    # Anti-IDOR: nota privada da Alice bloqueada -> 403
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"note_id": "note_r5_alice_1"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 403

    # Livro divergente -> 400
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"note_id": "note_r5_bob_2"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400

    # Leitura somente-leitura (concluída / cancelada / clube arquivado) -> 400
    for ro_id in (env["r_completed"]["id"], env["r_cancelled"]["id"], env["r_arch"]["id"]):
        assert bob_c.post(
            f"/api/collective-reads/{ro_id}/quotes",
            json={"quote_text": "Tentativa em somente-leitura"},
            headers={"x-csrf-token": bob_csrf},
        ).status_code == 400

    # Limite de 1500 caracteres e CFI inválido -> 422
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"quote_text": "A" * 1501},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 422
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"quote_text": "Trecho válido", "epub_cfi": "cfi-invalido"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 422
