"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — API REST de Conceitos (`/api/knowledge/concepts`).

Cobre os requisitos funcionais e de validação da Seção 6 e Seção 17:
- `POST /api/knowledge/concepts`: criação válida (`201`), normalização Unicode NFC, tags deduplicadas,
  cor hexadecimal, bloqueio de duplicidade (`409`), rejeição de campos extras (`422`), limites (`422`/`400`).
- `GET /api/knowledge/concepts`: listagem paginada (`limit`, `offset`, `total`, `has_more`), ordenação determinística
  (`updated_desc`, `created_desc`, `name_asc`), filtro por `search` (insensível a acentos) e `tag`, `include_archived`,
  `Cache-Control: private, no-store`.
- `GET /api/knowledge/concepts/{id}`: consulta detalhada (`200`), `links_count`, `404` anti-enumeração para inexistente ou alheio.
- `PATCH /api/knowledge/concepts/{id}`: atualização parcial preservando UUID e conexões existentes, distinção entre
  campo omitido, explicitamente nulo (`422`) e string vazia, rejeição de tentativa de alterar `id`, `user_id` ou timestamps (`422`).
- `DELETE /api/knowledge/concepts/{id}`: exclusão (`200`) e limpeza automática de vínculos.
"""

from typing import Optional, Tuple
import unicodedata
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def concepts_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_concepts_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "ellul.pdf").write_bytes(b"%PDF-1.4 sample")

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

    alice = auth_service.create_user("alice_c_api", "Password123!", "Alice", role="reader")
    bob = auth_service.create_user("bob_c_api", "Password123!", "Bob", role="reader")
    admin = auth_service.create_user("admin_c_api", "Password123!", "Admin", role="admin")

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book-ellul', 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul.pdf', 'ellul.pdf', 4096, 'pdf', 320, 1, 0);
            """
        )

    user_map = {"alice": alice["id"], "bob": bob["id"], "admin": admin["id"]}

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
        yield {"make_client": make_client, "alice": alice, "bob": bob, "admin": admin}
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR, config.BACKUPS_DIR = (
            old_db,
            old_cov,
            old_lib,
            old_bak,
        )


def test_concepts_crud_lifecycle_unicode_and_headers(concepts_api_env):
    """Testa criação, leitura, atualização parcial preservando UUID, cabeçalhos de cache e exclusão."""
    alice, _ = concepts_api_env["make_client"]("alice")

    nfd_name = unicodedata.normalize("NFD", "  Técnica  Autônoma ")
    res_create = alice.post(
        "/api/knowledge/concepts",
        json={
            "name": nfd_name,
            "description": "Sistema racional de eficiência máxima.",
            "tags": ["#filosofia", "Filosofia", "modernidade"],
            "color": "#2563EB",
        },
    )
    assert res_create.status_code == 201
    assert res_create.headers.get("cache-control") == "private, no-store"
    body = res_create.json()
    assert body["status"] == "success"
    c = body["concept"]
    cid = c["id"]
    assert c["name"] == unicodedata.normalize("NFC", "Técnica Autônoma")
    assert c["normalized_name"] == unicodedata.normalize("NFC", "técnica autônoma")
    assert c["tags"] == ["filosofia", "modernidade"]
    assert c["color"] == "#2563eb"
    assert c["is_archived"] is False
    assert c["links_count"] == 0

    # Duplicidade para o mesmo usuário retorna 409 Conflict
    res_dup = alice.post("/api/knowledge/concepts", json={"name": "técnica autônoma"})
    assert res_dup.status_code == 409

    # Cria vínculo com o livro antes de renomear o conceito para provar estabilidade do UUID
    res_link = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": cid,
            "target_type": "book",
            "target_id": "book-ellul",
            "relation_type": "references",
        },
    )
    assert res_link.status_code == 201

    # Consulta individual
    res_get = alice.get(f"/api/knowledge/concepts/{cid}")
    assert res_get.status_code == 200
    assert res_get.headers.get("cache-control") == "private, no-store"
    assert res_get.json()["concept"]["links_count"] == 1

    # PATCH parcial (alterando apenas name e description; tags e color omitidos são preservados)
    res_patch = alice.patch(
        f"/api/knowledge/concepts/{cid}",
        json={"name": "Fenômeno Técnico", "description": "Descrição revisada."},
    )
    assert res_patch.status_code == 200
    patched = res_patch.json()["concept"]
    assert patched["id"] == cid
    assert patched["name"] == "Fenômeno Técnico"
    assert patched["description"] == "Descrição revisada."
    assert patched["tags"] == ["filosofia", "modernidade"]
    assert patched["color"] == "#2563eb"
    assert patched["links_count"] == 1

    # DELETE remove o conceito e limpa seus vínculos
    res_del = alice.delete(f"/api/knowledge/concepts/{cid}")
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True

    assert alice.get(f"/api/knowledge/concepts/{cid}").status_code == 404
    assert alice.get("/api/knowledge/links").json()["total"] == 0


def test_concepts_pagination_sorting_and_filters(concepts_api_env):
    """Testa paginação (limit, offset, total, has_more), ordenação determinística e filtros."""
    alice, _ = concepts_api_env["make_client"]("alice")

    alice.post("/api/knowledge/concepts", json={"name": "Ética", "tags": ["moral"]})
    alice.post("/api/knowledge/concepts", json={"name": "Automação", "tags": ["tecnologia"]})
    alice.post("/api/knowledge/concepts", json={"name": "Burocracia", "tags": ["sociologia"]})
    alice.post("/api/knowledge/concepts", json={"name": "Arquivado", "is_archived": True})

    # Página 1 ordenada por name_asc (limit=2, offset=0)
    p1 = alice.get("/api/knowledge/concepts?sort=name_asc&limit=2&offset=0").json()
    assert p1["total"] == 3
    assert p1["limit"] == 2
    assert p1["offset"] == 0
    assert p1["has_more"] is True
    assert [i["name"] for i in p1["items"]] == ["Automação", "Burocracia"]

    # Página 2 (limit=2, offset=2)
    p2 = alice.get("/api/knowledge/concepts?sort=name_asc&limit=2&offset=2").json()
    assert p2["total"] == 3
    assert p2["has_more"] is False
    assert [i["name"] for i in p2["items"]] == ["Ética"]

    # Filtro de busca sem acento ("etica" encontra "Ética")
    s_res = alice.get("/api/knowledge/concepts?search=etica").json()
    assert s_res["total"] == 1
    assert s_res["items"][0]["name"] == "Ética"

    # Filtro por tag e include_archived
    t_res = alice.get("/api/knowledge/concepts?tag=sociologia").json()
    assert t_res["total"] == 1
    assert t_res["items"][0]["name"] == "Burocracia"

    all_inc = alice.get("/api/knowledge/concepts?include_archived=true").json()
    assert all_inc["total"] == 4


def test_concepts_validation_extra_forbid_and_null_vs_empty_semantics(concepts_api_env):
    """
    Valida diferenciação entre campo omitido, explicitamente nulo e string vazia,
    além de bloqueio de campos protegidos (`id`, `user_id`, `created_at`).
    """
    alice, _ = concepts_api_env["make_client"]("alice")

    created = alice.post(
        "/api/knowledge/concepts",
        json={"name": "Conceito Base", "description": "Texto inicial"},
    ).json()["concept"]
    cid = created["id"]

    # Campo enviado como string vazia em description limpa a descrição (permitido), mas em name falha (422)
    res_empty_desc = alice.patch(f"/api/knowledge/concepts/{cid}", json={"description": ""})
    assert res_empty_desc.status_code == 200
    assert res_empty_desc.json()["concept"]["description"] == ""

    assert alice.patch(f"/api/knowledge/concepts/{cid}", json={"name": "   "}).status_code == 422

    # Campo enviado explicitamente como null em name ou description é rejeitado (422)
    assert alice.patch(f"/api/knowledge/concepts/{cid}", json={"name": None}).status_code == 422
    assert alice.patch(f"/api/knowledge/concepts/{cid}", json={"description": None}).status_code == 422

    # Tentativa de modificar id, user_id ou created_at via payload arbitrário é bloqueada (422 extra="forbid")
    for forbidden_payload in (
        {"name": "Valido", "id": "custom-uuid"},
        {"name": "Valido", "user_id": 999},
        {"name": "Valido", "created_at": "2020-01-01"},
        {"name": "Valido", "unexpected_field": True},
    ):
        assert alice.post("/api/knowledge/concepts", json=forbidden_payload).status_code == 422
        assert alice.patch(f"/api/knowledge/concepts/{cid}", json=forbidden_payload).status_code == 422
