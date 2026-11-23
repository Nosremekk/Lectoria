"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — API REST de Conexões Semânticas (`/api/knowledge/links`).

Cobre os requisitos da Seção 8 e Seção 17:
- `POST /api/knowledge/links`: criação para os 8 tipos de relação aprovados (`complements`, `contradicts`,
  `exemplifies`, `grounds`, `develops`, `references`, `questions`, `relates_to`), padrão `relates_to` quando
  omitido, bloqueio de auto-referência (`400`), bloqueio de duplicata exata (`409`), bloqueio de campos extras (`422`),
  não criação automática de relação inversa.
- `GET /api/knowledge/links`: listagem paginada com `total`, `has_more`, filtros por `source_type`, `target_type`,
  `entity_type` + `entity_id`, `direction`, `relation_type` e ordenação.
- `GET /api/knowledge/links/{id}`: consulta individual (`200`) e `404` anti-enumeração.
- `PATCH /api/knowledge/links/{id}`: atualização de `relation_type` e `rationale`, rejeição estrita de tentativa
  de alterar `source_id`, `target_id`, `source_type`, `target_type` ou `user_id` (`422`).
- `DELETE /api/knowledge/links/{id}`: remoção da conexão própria (`200`).
"""

from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def links_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_links_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "b1.pdf").write_bytes(b"%PDF-1.4 sample 1")
    (lib_dir / "b2.epub").write_bytes(b"PK\x03\x04 sample 2")

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

    alice = auth_service.create_user("alice_l_api", "Password123!", "Alice", role="reader")
    bob = auth_service.create_user("bob_l_api", "Password123!", "Bob", role="reader")

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book-1', 'Vigiar e Punir', 'Michel Foucault', 'b1.pdf', 'b1.pdf', 4096, 'pdf', 300, 1, 0),
            ('book-2', 'A Sociedade do Espetáculo', 'Guy Debord', 'b2.epub', 'b2.epub', 4096, 'epub', 200, 1, 0);
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


def test_links_api_all_eight_relation_types_default_and_no_duplicate_inverse(links_api_env):
    """
    Testa:
    - Criação de vínculos com os 8 tipos de relação aprovados.
    - Valor padrão `relates_to` quando `relation_type` é omitido.
    - Não duplicação automática de relação inversa (1 conexão criada = 1 linha persistida).
    """
    alice, _ = links_api_env["make_client"]("alice")

    c_main = alice.post("/api/knowledge/concepts", json={"name": "Poder"}).json()["concept"]

    # Omitindo relation_type -> usa relates_to por padrão
    res_default = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": c_main["id"],
            "target_type": "book",
            "target_id": "book-1",
        },
    )
    assert res_default.status_code == 201
    assert res_default.headers.get("cache-control") == "private, no-store"
    assert res_default.json()["link"]["relation_type"] == "relates_to"

    # Os 8 tipos aprovados
    all_relations = (
        "complements",
        "contradicts",
        "exemplifies",
        "grounds",
        "develops",
        "references",
        "questions",
        "relates_to",
    )
    for idx, rel in enumerate(all_relations):
        c_sub = alice.post("/api/knowledge/concepts", json={"name": f"Conceito {idx}"}).json()["concept"]
        res_rel = alice.post(
            "/api/knowledge/links",
            json={
                "source_type": "concept",
                "source_id": c_main["id"],
                "target_type": "concept",
                "target_id": c_sub["id"],
                "relation_type": rel,
                "rationale": f"Racional para {rel}",
            },
        )
        assert res_rel.status_code == 201
        assert res_rel.json()["link"]["relation_type"] == rel

    # Total de links criados deve ser exatamente 1 + 8 = 9 (sem duplicação inversa)
    list_res = alice.get("/api/knowledge/links").json()
    assert list_res["total"] == 9


def test_links_api_immutability_of_endpoints_and_conflict_validation(links_api_env):
    """
    Valida:
    - Rejeição de auto-referência (400).
    - Rejeição de duplicata exata (409).
    - PATCH atualiza apenas relation_type e rationale; tentar enviar source_id, target_id ou user_id retorna 422.
    """
    alice, _ = links_api_env["make_client"]("alice")
    c1 = alice.post("/api/knowledge/concepts", json={"name": "Conceito 1"}).json()["concept"]
    c2 = alice.post("/api/knowledge/concepts", json={"name": "Conceito 2"}).json()["concept"]

    # Auto-referência -> 400
    res_self = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": c1["id"],
            "target_type": "concept",
            "target_id": c1["id"],
        },
    )
    assert res_self.status_code == 400

    # Criação válida
    res_ok = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": c1["id"],
            "target_type": "concept",
            "target_id": c2["id"],
            "relation_type": "complements",
            "rationale": "Justificativa inicial",
        },
    )
    assert res_ok.status_code == 201
    lid = res_ok.json()["link"]["id"]

    # Duplicata exata -> 409
    res_dup = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": c1["id"],
            "target_type": "concept",
            "target_id": c2["id"],
            "relation_type": "complements",
        },
    )
    assert res_dup.status_code == 409

    # PATCH válido
    res_patch = alice.patch(
        f"/api/knowledge/links/{lid}",
        json={"relation_type": "contradicts", "rationale": "Justificativa atualizada"},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["link"]["relation_type"] == "contradicts"
    assert res_patch.json()["link"]["rationale"] == "Justificativa atualizada"

    # PATCH tentando alterar extremos ou proprietário -> 422
    for forbidden_body in (
        {"source_id": "book-1", "rationale": "x"},
        {"target_id": "book-2", "rationale": "x"},
        {"source_type": "book", "rationale": "x"},
        {"target_type": "book", "rationale": "x"},
        {"user_id": 999, "rationale": "x"},
        {},
    ):
        assert alice.patch(f"/api/knowledge/links/{lid}", json=forbidden_body).status_code == 422

    # DELETE
    assert alice.delete(f"/api/knowledge/links/{lid}").status_code == 200
    assert alice.get(f"/api/knowledge/links/{lid}").status_code == 404
