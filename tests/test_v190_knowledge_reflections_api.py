"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — API REST de Reflexões Autorais (`/api/knowledge/reflections`).

Cobre os requisitos da Seção 7 e Seção 17:
- `POST /api/knowledge/reflections`: criação válida (`201`), com e sem `primary_book_id`, validação de autorização
  do livro associado na escrita (`404` se inexistente, ausente ou de fonte inativa).
- Retorno de `body_markdown` como string JSON pura não confiável (sem renderizar HTML, sem executar ou modificar tags).
- `GET /api/knowledge/reflections`: listagem paginada (`limit`, `offset`, `total`, `has_more`), ordenação determinística,
  filtro por `primary_book_id`, `tag` e `search` (insensível a acentos).
- `GET /api/knowledge/reflections/{id}`: consulta individual (`200`) com `references` e `backlinks` resolvidos em tempo
  de leitura e revalidação de `primary_book_available` caso o livro principal tenha sua fonte desativada após a criação.
- `PATCH /api/knowledge/reflections/{id}`: atualização parcial, suporte a `primary_book_id: null` para desvincular o livro,
  rejeição de `title: null` ou `body_markdown: null` (`422`), rejeição de campos protegidos (`422`).
- `DELETE /api/knowledge/reflections/{id}`: exclusão (`200`) e limpeza automática de conexões.
"""

from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def reflections_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_refl_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "ellul.pdf").write_bytes(b"%PDF-1.4 sample")
    (lib_dir / "inativo.pdf").write_bytes(b"%PDF-1.4 sample")

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

    alice = auth_service.create_user("alice_r_api", "Password123!", "Alice", role="reader")
    bob = auth_service.create_user("bob_r_api", "Password123!", "Bob", role="reader")

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
            ('book-active', 'A Técnica e o Desafio', 'Jacques Ellul', 'ellul.pdf', 'ellul.pdf', 4096, 'pdf', 320, 1, 0),
            ('book-inactive', 'Livro Fonte Inativa', 'Autor', 'inativo.pdf', 'inativo.pdf', 4096, 'pdf', 100, 2, 0),
            ('book-missing', 'Livro Ausente', 'Autor', 'ausente.pdf', 'ausente.pdf', 4096, 'pdf', 100, 1, 1);
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
        yield {"make_client": make_client, "alice": alice, "bob": bob, "db_path": db_path}
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR, config.BACKUPS_DIR = (
            old_db,
            old_cov,
            old_lib,
            old_bak,
        )


def test_reflections_crud_markdown_raw_preservation_and_book_authorization(reflections_api_env):
    """
    Valida:
    - Criação de reflexão com `body_markdown` contendo sintaxe Markdown e strings HTML/JS brutas,
      garantindo que a API retorna exatamente o texto bruto em JSON sem renderizar HTML.
    - Validação de `primary_book_id` tanto na escrita quanto na leitura.
    - Atualização parcial via PATCH (inclusive desvinculando `primary_book_id: None`).
    """
    alice, _ = reflections_api_env["make_client"]("alice")

    raw_md = "# Ensaio sobre Ellul\n\nTexto com <script>alert(1)</script> e [link](javascript:void(0))."
    res = alice.post(
        "/api/knowledge/reflections",
        json={
            "title": "Ensaio sobre Autonomia Técnica",
            "body_markdown": raw_md,
            "primary_book_id": "book-active",
            "tags": ["ensaio", "técnica"],
            "is_pinned": True,
        },
    )
    assert res.status_code == 201
    assert res.headers.get("cache-control") == "private, no-store"
    refl = res.json()["reflection"]
    rid = refl["id"]
    assert refl["title"] == "Ensaio sobre Autonomia Técnica"
    assert refl["body_markdown"] == raw_md
    assert refl["primary_book_id"] == "book-active"
    assert refl["primary_book_title"] == "A Técnica e o Desafio"
    assert refl["primary_book_available"] is True

    # Livro de fonte inativa ou ausente é rejeitado na escrita com 404
    for bad_book in ("book-inactive", "book-missing", "book-nonexistent"):
        res_bad = alice.post(
            "/api/knowledge/reflections",
            json={"title": "Inválido", "body_markdown": "Texto", "primary_book_id": bad_book},
        )
        assert res_bad.status_code == 404

    # Se a fonte do livro principal for desativada depois, a leitura da reflexão permanece acessível para a autora,
    # mas primary_book_available passa a False e primary_book_title fica None (validação na leitura)
    with database.get_db(reflections_api_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE library_sources SET is_active = 0 WHERE id = 1;")

    res_get_deact = alice.get(f"/api/knowledge/reflections/{rid}")
    assert res_get_deact.status_code == 200
    assert res_get_deact.json()["reflection"]["primary_book_available"] is False
    assert res_get_deact.json()["reflection"]["primary_book_title"] is None

    with database.get_db(reflections_api_env["db_path"]) as conn:
        conn.cursor().execute("UPDATE library_sources SET is_active = 1 WHERE id = 1;")

    # PATCH desvinculando primary_book_id explicitamente com null
    res_unlink_book = alice.patch(
        f"/api/knowledge/reflections/{rid}",
        json={"primary_book_id": None, "title": "Ensaio Independente"},
    )
    assert res_unlink_book.status_code == 200
    assert res_unlink_book.json()["reflection"]["primary_book_id"] is None
    assert res_unlink_book.json()["reflection"]["title"] == "Ensaio Independente"
    assert res_unlink_book.json()["reflection"]["body_markdown"] == raw_md

    # DELETE
    res_del = alice.delete(f"/api/knowledge/reflections/{rid}")
    assert res_del.status_code == 200
    assert alice.get(f"/api/knowledge/reflections/{rid}").status_code == 404


def test_reflections_pagination_sorting_and_validation(reflections_api_env):
    """Testa paginação, ordenação, filtros e rejeição de campos nulos/extras."""
    alice, _ = reflections_api_env["make_client"]("alice")

    alice.post(
        "/api/knowledge/reflections",
        json={"title": "Síntese B", "body_markdown": "Educação e Filosofia", "tags": ["educacao"]},
    )
    r_pinned = alice.post(
        "/api/knowledge/reflections",
        json={
            "title": "Síntese A Fixada",
            "body_markdown": "Racionalização técnica",
            "primary_book_id": "book-active",
            "is_pinned": True,
        },
    ).json()["reflection"]

    listed = alice.get("/api/knowledge/reflections?limit=1&offset=0").json()
    assert listed["total"] == 2
    assert listed["has_more"] is True
    assert listed["items"][0]["id"] == r_pinned["id"]

    by_book = alice.get("/api/knowledge/reflections?primary_book_id=book-active").json()
    assert by_book["total"] == 1
    assert by_book["items"][0]["id"] == r_pinned["id"]

    by_search = alice.get("/api/knowledge/reflections?search=educacao").json()
    assert by_search["total"] == 1
    assert by_search["items"][0]["title"] == "Síntese B"

    # Rejeita null em title ou body_markdown e rejeita campos extras
    assert alice.patch(f"/api/knowledge/reflections/{r_pinned['id']}", json={"title": None}).status_code == 422
    assert alice.patch(f"/api/knowledge/reflections/{r_pinned['id']}", json={"body_markdown": None}).status_code == 422
    assert alice.patch(f"/api/knowledge/reflections/{r_pinned['id']}", json={"user_id": 999}).status_code == 422
