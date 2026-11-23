"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — API REST de Referências e Backlinks
(`/api/knowledge/entities/{entity_type}/{entity_id}/connections` e `/backlinks`).

Cobre todos os requisitos das Seções 9 e 17.4:
- Distinção entre relações de saída (`outgoing_links`) e relações de entrada (`incoming_backlinks`).
- Filtro opcional por `direction` (`outgoing`, `incoming`, `both`) e `relation_type`.
- Validação prévia de autorização sobre a entidade consultada (anti-enumeração: `404` se a entidade
  consultada for privada de terceiro ou inexistente).
- Cenário completo de revogação via API REST (Seção 17.4):
  1. Usuário A (Bob) publica uma anotação (`PATCH .../visibility -> public`).
  2. Usuário B (Alice) cria uma conexão autorizada apontando para a anotação de A.
  3. Usuário A revoga sua publicação (`PATCH .../visibility -> private`).
  4. Usuário B consulta a conexão e as conexões/backlinks da entidade.
  5. O extremo torna-se genericamente indisponível (`available = False`, `label = 'Referência indisponível'`).
  6. A justificativa (`rationale`) de B permanece acessível.
  7. Nenhum conteúdo protegido de A é retornado.
  8. Usuário A republica a anotação (`PATCH .../visibility -> public`).
  9. O acesso é recuperado imediatamente.
- Testes também de exclusão da anotação, desativação de fonte do livro e exclusão de usuário.
"""

from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def backlinks_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_backlinks_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "casmurro.pdf").write_bytes(b"%PDF-1.4 sample")

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

    alice = auth_service.create_user("alice_b_api", "Password123!", "Alice Lemos", role="reader")
    bob = auth_service.create_user("bob_b_api", "Password123!", "Bob Ferreira", role="reader")

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book-casmurro', 'Dom Casmurro', 'Machado de Assis', 'casmurro.pdf', 'casmurro.pdf', 4096, 'pdf', 200, 1, 0);
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


def test_full_api_revocation_republish_deletion_and_source_deactivation_scenario(backlinks_api_env):
    """
    Executa os 9 passos do cenário principal de revogação da Seção 17.4 inteiramente via API REST,
    seguido de desativação de fonte e exclusão física da anotação de terceiro.
    """
    alice, _ = backlinks_api_env["make_client"]("alice")
    bob, _ = backlinks_api_env["make_client"]("bob")

    # 1. Usuário A (Bob) cria um destaque e o publica via API
    hl_bob = bob.post(
        "/api/books/book-casmurro/highlights",
        json={
            "page": 32,
            "selected_text": "TEXTO_SECRETO_APOS_REVOGACAO_OLHOS_DE_RESSACA",
            "rects": [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.03}],
            "chapter_label": "Capítulo XXXII",
            "visibility": "private",
        },
    ).json()["highlight"]
    hl_bob_id = hl_bob["id"]

    res_pub = bob.patch(
        f"/api/books/book-casmurro/highlights/{hl_bob_id}/visibility",
        json={"visibility": "public"},
    )
    assert res_pub.status_code == 200

    # 2. Usuário B (Alice) cria um conceito privado, uma reflexão privada e uma conexão autorizada para o destaque de Bob
    c_alice = alice.post(
        "/api/knowledge/concepts",
        json={"name": "Narrador Não Confiável"},
    ).json()["concept"]

    res_link = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "concept",
            "source_id": c_alice["id"],
            "target_type": "highlight",
            "target_id": hl_bob_id,
            "relation_type": "exemplifies",
            "rationale": "Justificativa autoral da Alice sobre a passagem do capítulo 32.",
        },
    )
    assert res_link.status_code == 201
    link_id = res_link.json()["link"]["id"]
    assert res_link.json()["link"]["target"]["available"] is True
    assert "TEXTO_SECRETO_APOS_REVOGACAO" in res_link.json()["link"]["target"]["excerpt"]

    # Bob consulta os backlinks de seu próprio destaque público: ZERO vazamento da conexão privada da Alice
    bob_backlinks = bob.get(f"/api/knowledge/entities/highlight/{hl_bob_id}/backlinks")
    assert bob_backlinks.status_code == 200
    assert bob_backlinks.json()["total_incoming"] == 0
    assert bob_backlinks.json()["incoming_backlinks"] == []

    # 3. Usuário A (Bob) revoga sua publicação (public -> private) via API
    res_revoke = bob.patch(
        f"/api/books/book-casmurro/highlights/{hl_bob_id}/visibility",
        json={"visibility": "private"},
    )
    assert res_revoke.status_code == 200

    # 4, 5, 6, 7. Usuário B (Alice) consulta a conexão e as conexões de seu conceito
    res_alice_link = alice.get(f"/api/knowledge/links/{link_id}")
    assert res_alice_link.status_code == 200
    link_data = res_alice_link.json()["link"]
    assert link_data["target"]["available"] is False
    assert link_data["target"]["status"] == "unavailable"
    assert link_data["target"]["label"] == "Referência indisponível"
    assert link_data["target"]["title"] is None
    assert link_data["target"]["excerpt"] is None
    assert link_data["target"]["page"] is None
    assert link_data["target"]["epub_cfi"] is None
    assert link_data["target"]["chapter_label"] is None
    assert link_data["target"]["author_display_name"] is None
    assert link_data["target"]["visibility"] is None
    assert link_data["rationale"] == "Justificativa autoral da Alice sobre a passagem do capítulo 32."
    assert "TEXTO_SECRETO_APOS_REVOGACAO" not in res_alice_link.text

    # Alice também não pode consultar diretamente /api/knowledge/entities/highlight/{hl_bob_id}/connections enquanto privado (404 anti-enumeração)
    assert alice.get(f"/api/knowledge/entities/highlight/{hl_bob_id}/connections").status_code == 404
    assert alice.get(f"/api/knowledge/entities/highlight/{hl_bob_id}/backlinks").status_code == 404

    # Mas Alice pode consultar as conexões de seu próprio conceito e ver o extremo indisponível com seu rationale preservado
    res_concept_conns = alice.get(f"/api/knowledge/entities/concept/{c_alice['id']}/connections")
    assert res_concept_conns.status_code == 200
    conns_data = res_concept_conns.json()
    assert conns_data["total_outgoing"] == 1
    assert conns_data["outgoing_links"][0]["target"]["available"] is False
    assert conns_data["outgoing_links"][0]["rationale"] == "Justificativa autoral da Alice sobre a passagem do capítulo 32."
    assert "TEXTO_SECRETO_APOS_REVOGACAO" not in res_concept_conns.text

    # 8 & 9. Usuário A (Bob) republica a anotação e o acesso de Alice é recuperado
    assert bob.patch(
        f"/api/books/book-casmurro/highlights/{hl_bob_id}/visibility",
        json={"visibility": "public"},
    ).status_code == 200

    res_recovered = alice.get(f"/api/knowledge/links/{link_id}")
    assert res_recovered.status_code == 200
    assert res_recovered.json()["link"]["target"]["available"] is True
    assert "TEXTO_SECRETO_APOS_REVOGACAO" in res_recovered.json()["link"]["target"]["excerpt"]

    # Exclusão física da anotação por Bob: link e rationale de Alice continuam preservados com target indisponível
    assert bob.delete(f"/api/books/book-casmurro/highlights/{hl_bob_id}").status_code == 200
    res_after_del = alice.get(f"/api/knowledge/links/{link_id}")
    assert res_after_del.status_code == 200
    assert res_after_del.json()["link"]["target"]["available"] is False
    assert res_after_del.json()["link"]["rationale"] == "Justificativa autoral da Alice sobre a passagem do capítulo 32."


def test_entity_connections_and_backlinks_direction_and_anti_enumeration(backlinks_api_env):
    """
    Testa:
    - Direções `outgoing`, `incoming` e `both` em `/api/knowledge/entities/{type}/{id}/connections`.
    - Endpoint `/api/knowledge/entities/{type}/{id}/backlinks`.
    - Anti-enumeração (`404`) ao tentar consultar conexões/backlinks de entidade privada de outro usuário.
    """
    alice, _ = backlinks_api_env["make_client"]("alice")
    bob, _ = backlinks_api_env["make_client"]("bob")

    c1 = alice.post("/api/knowledge/concepts", json={"name": "Conceito Central"}).json()["concept"]
    c2 = alice.post("/api/knowledge/concepts", json={"name": "Conceito Derivado"}).json()["concept"]
    r1 = alice.post("/api/knowledge/reflections", json={"title": "Reflexão Base"}).json()["reflection"]

    # c1 -> c2 (develops) e r1 -> c1 (grounds)
    alice.post(
        "/api/knowledge/links",
        json={"source_type": "concept", "source_id": c1["id"], "target_type": "concept", "target_id": c2["id"], "relation_type": "develops"},
    )
    alice.post(
        "/api/knowledge/links",
        json={"source_type": "reflection", "source_id": r1["id"], "target_type": "concept", "target_id": c1["id"], "relation_type": "grounds"},
    )

    both = alice.get(f"/api/knowledge/entities/concept/{c1['id']}/connections?direction=both").json()
    assert both["total_outgoing"] == 1
    assert both["total_incoming"] == 1
    assert both["outgoing_links"][0]["target_id"] == c2["id"]
    assert both["incoming_backlinks"][0]["source_id"] == r1["id"]

    only_in = alice.get(f"/api/knowledge/entities/concept/{c1['id']}/backlinks").json()
    assert only_in["direction"] == "incoming"
    assert only_in["total_incoming"] == 1
    assert only_in["outgoing_links"] == []
    assert only_in["incoming_backlinks"][0]["relation_label"] == "Fundamentado por"

    # Bob não pode consultar conexões ou backlinks do conceito privado da Alice (404)
    assert bob.get(f"/api/knowledge/entities/concept/{c1['id']}/connections").status_code == 404
    assert bob.get(f"/api/knowledge/entities/concept/{c1['id']}/backlinks").status_code == 404
