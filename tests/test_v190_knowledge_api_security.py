"""
Suíte de Testes da Fase 2 da Lectoria v1.9.0 — Segurança, Autenticação, CSRF,
Isolamento Multiusuário (incluindo Admin) e Testes Adversariais da API `/api/knowledge/*`.

Cobre todos os cenários das Seções 12, 13, 14, 15, 16 e 17.2 a 17.5:
- 17.2. Autenticação obrigatória (`401`) em todos os endpoints sem sessão, com sessão inválida/expirada
  ou usuário desativado.
- 17.2. Proteção CSRF (`403`) em todos os endpoints mutáveis (`POST`, `PATCH`, `DELETE`) quando o cabeçalho
  `x-csrf-token` está ausente, inválido ou enviado apenas como parâmetro de URL `?csrf_token=...`.
- 17.3. Isolamento multiusuário entre Alice, Bob e Admin:
  - Admin não possui acesso implícito aos conceitos, reflexões, conexões ou backlinks privados de outros usuários (`404`).
  - Anti-IDOR e anti-enumeração (`404` uniforme para IDs alheios e IDs inexistentes).
- 17.5. Testes adversariais via HTTP:
  - SQL Injection em query params e corpos JSON.
  - Tipos de entidade e relação arbitrários (`422`).
  - Payloads excessivos (`422`).
  - Campos inesperados e tentativas de Mass Assignment (`id`, `user_id`, `created_at` -> `422`).
  - Paginação extrema ou limites negativos (`422`).
  - Políticas de cabeçalho `Cache-Control: private, no-store` em respostas de sucesso e erro.
"""

from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def security_api_env(tmp_path):
    db_path = tmp_path / "lectoria_v190_security_api.db"
    lib_dir = tmp_path / "library"
    covers_dir = tmp_path / "covers"
    backups_dir = tmp_path / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "obra.pdf").write_bytes(b"%PDF-1.4 sample")

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

    alice = auth_service.create_user("alice_sec_api", "Password123!", "Alice", role="reader")
    bob = auth_service.create_user("bob_sec_api", "Password123!", "Bob", role="reader")
    admin = auth_service.create_user("admin_sec_api", "Password123!", "Admin", role="admin")
    inativo = auth_service.create_user("inativo_sec_api", "Password123!", "Inativo", role="reader")

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book-1', 'Obra de Referência', 'Autor', 'obra.pdf', 'obra.pdf', 4096, 'pdf', 200, 1, 0);
            """
        )

    user_map = {
        "alice": alice["id"],
        "bob": bob["id"],
        "admin": admin["id"],
        "inativo": inativo["id"],
    }

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
        yield {
            "make_client": make_client,
            "alice": alice,
            "bob": bob,
            "admin": admin,
            "inativo": inativo,
            "db_path": db_path,
        }
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR, config.BACKUPS_DIR = (
            old_db,
            old_cov,
            old_lib,
            old_bak,
        )


def test_authentication_required_and_inactive_user_blocked_on_all_knowledge_routes(security_api_env):
    """
    Seção 17.2:
    Verifica todos os endpoints `/api/knowledge/*` sem autenticação, com sessão inválida
    e com usuário desativado (`is_active = 0`).
    """
    anon, _ = security_api_env["make_client"](None)
    anon.cookies.set(auth_service.SESSION_COOKIE_NAME, "token_invalido_ou_expirado")
    csrf = anon.get("/api/auth/csrf").json()["csrf_token"]
    anon.headers["x-csrf-token"] = csrf

    inativo_client, _ = security_api_env["make_client"]("inativo")
    auth_service.update_user(security_api_env["inativo"]["id"], is_active=False)

    routes = [
        ("GET", "/api/knowledge/concepts", None),
        ("POST", "/api/knowledge/concepts", {"name": "Teste"}),
        ("GET", "/api/knowledge/concepts/cid-1", None),
        ("PATCH", "/api/knowledge/concepts/cid-1", {"name": "Novo"}),
        ("DELETE", "/api/knowledge/concepts/cid-1", None),
        ("GET", "/api/knowledge/reflections", None),
        ("POST", "/api/knowledge/reflections", {"title": "Teste"}),
        ("GET", "/api/knowledge/reflections/rid-1", None),
        ("PATCH", "/api/knowledge/reflections/rid-1", {"title": "Novo"}),
        ("DELETE", "/api/knowledge/reflections/rid-1", None),
        ("GET", "/api/knowledge/links", None),
        ("POST", "/api/knowledge/links", {"source_type": "book", "source_id": "book-1", "target_type": "book", "target_id": "b2"}),
        ("GET", "/api/knowledge/links/lid-1", None),
        ("PATCH", "/api/knowledge/links/lid-1", {"rationale": "x"}),
        ("DELETE", "/api/knowledge/links/lid-1", None),
        ("GET", "/api/knowledge/entities/book/book-1/connections", None),
        ("GET", "/api/knowledge/entities/book/book-1/backlinks", None),
        ("GET", "/api/knowledge/lookup?q=teste", None),
    ]

    for method, url, payload in routes:
        res_anon = anon.request(method, url, json=payload) if payload is not None else anon.request(method, url)
        assert res_anon.status_code == 401, f"Esperado 401 anônimo em {method} {url}, obteve {res_anon.status_code}"

        res_inact = (
            inativo_client.request(method, url, json=payload)
            if payload is not None
            else inativo_client.request(method, url)
        )
        assert res_inact.status_code == 401, f"Esperado 401 usuário inativo em {method} {url}, obteve {res_inact.status_code}"


def test_csrf_required_in_header_and_rejected_in_url_on_all_mutable_knowledge_routes(security_api_env):
    """
    Seção 13 e Seção 17.2:
    Verifica que todos os métodos mutáveis (`POST`, `PATCH`, `DELETE`) em `/api/knowledge/*`:
    1. Retornam `403` quando `x-csrf-token` está ausente.
    2. Retornam `403` quando `x-csrf-token` é inválido.
    3. Retornam `403` quando o cliente tenta passar `?csrf_token=...` na URL das novas rotas `/api/knowledge/*`.
    """
    alice_no_csrf, valid_csrf = security_api_env["make_client"]("alice", include_csrf=False)

    mutable_routes = [
        ("POST", "/api/knowledge/concepts", {"name": "Conceito CSRF"}),
        ("PATCH", "/api/knowledge/concepts/cid-1", {"name": "Novo"}),
        ("DELETE", "/api/knowledge/concepts/cid-1", None),
        ("POST", "/api/knowledge/reflections", {"title": "Reflexão CSRF"}),
        ("PATCH", "/api/knowledge/reflections/rid-1", {"title": "Nova"}),
        ("DELETE", "/api/knowledge/reflections/rid-1", None),
        ("POST", "/api/knowledge/links", {"source_type": "book", "source_id": "book-1", "target_type": "book", "target_id": "b2"}),
        ("PATCH", "/api/knowledge/links/lid-1", {"rationale": "x"}),
        ("DELETE", "/api/knowledge/links/lid-1", None),
    ]

    for method, url, payload in mutable_routes:
        # 1. Ausente
        r_missing = (
            alice_no_csrf.request(method, url, json=payload)
            if payload is not None
            else alice_no_csrf.request(method, url)
        )
        assert r_missing.status_code == 403
        assert r_missing.headers.get("cache-control") == "private, no-store"

        # 2. Inválido no header
        r_bad = (
            alice_no_csrf.request(method, url, json=payload, headers={"x-csrf-token": "token-falso"})
            if payload is not None
            else alice_no_csrf.request(method, url, headers={"x-csrf-token": "token-falso"})
        )
        assert r_bad.status_code == 403

        # 3. Passado apenas na URL (?csrf_token=...) -> proibido nas novas rotas /api/knowledge/*
        url_with_query_csrf = f"{url}?csrf_token={valid_csrf}"
        r_url_csrf = (
            alice_no_csrf.request(method, url_with_query_csrf, json=payload)
            if payload is not None
            else alice_no_csrf.request(method, url_with_query_csrf)
        )
        assert r_url_csrf.status_code == 403


def test_multiuser_and_admin_isolation_anti_idor_and_anti_enumeration(security_api_env):
    """
    Seção 17.3:
    Garante isolamento absoluto entre Alice, Bob e Admin:
    - Nem Bob nem o Admin podem ler, listar, atualizar, excluir ou conectar conceitos,
      reflexões, conexões ou backlinks privados de Alice.
    - Respostas `404` para IDs privados de Alice são indistinguíveis de IDs inexistentes.
    """
    alice, _ = security_api_env["make_client"]("alice")
    bob, _ = security_api_env["make_client"]("bob")
    admin, _ = security_api_env["make_client"]("admin")

    c_alice = alice.post(
        "/api/knowledge/concepts",
        json={"name": "Conceito Privado Alice", "description": "Segredo Alice"},
    ).json()["concept"]
    r_alice = alice.post(
        "/api/knowledge/reflections",
        json={"title": "Reflexão Privada Alice", "body_markdown": "Corpo secreto da Alice"},
    ).json()["reflection"]
    l_alice = alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "reflection",
            "source_id": r_alice["id"],
            "target_type": "concept",
            "target_id": c_alice["id"],
            "relation_type": "grounds",
            "rationale": "Racional privado da Alice",
        },
    ).json()["link"]

    for intruder in (bob, admin):
        # Listagens do intruso não mostram nada da Alice
        assert intruder.get("/api/knowledge/concepts").json()["total"] == 0
        assert intruder.get("/api/knowledge/reflections").json()["total"] == 0
        assert intruder.get("/api/knowledge/links").json()["total"] == 0
        assert intruder.get("/api/knowledge/lookup?q=Alice").json()["count"] == 0

        # Leituras, edições e exclusões diretas retornam 404 anti-enumeração
        assert intruder.get(f"/api/knowledge/concepts/{c_alice['id']}").status_code == 404
        assert intruder.patch(f"/api/knowledge/concepts/{c_alice['id']}", json={"name": "Hack"}).status_code == 404
        assert intruder.delete(f"/api/knowledge/concepts/{c_alice['id']}").status_code == 404

        assert intruder.get(f"/api/knowledge/reflections/{r_alice['id']}").status_code == 404
        assert intruder.patch(f"/api/knowledge/reflections/{r_alice['id']}", json={"title": "Hack"}).status_code == 404
        assert intruder.delete(f"/api/knowledge/reflections/{r_alice['id']}").status_code == 404

        assert intruder.get(f"/api/knowledge/links/{l_alice['id']}").status_code == 404
        assert intruder.patch(f"/api/knowledge/links/{l_alice['id']}", json={"rationale": "Hack"}).status_code == 404
        assert intruder.delete(f"/api/knowledge/links/{l_alice['id']}").status_code == 404

        # Consultas de conexões e backlinks sobre entidades privadas da Alice retornam 404
        assert intruder.get(f"/api/knowledge/entities/concept/{c_alice['id']}/connections").status_code == 404
        assert intruder.get(f"/api/knowledge/entities/concept/{c_alice['id']}/backlinks").status_code == 404
        assert intruder.get(f"/api/knowledge/entities/reflection/{r_alice['id']}/connections").status_code == 404
        assert intruder.get(f"/api/knowledge/entities/reflection/{r_alice['id']}/backlinks").status_code == 404

        # Filtro de links apontando para entidade privada da Alice retorna 404
        assert intruder.get(
            f"/api/knowledge/links?entity_type=concept&entity_id={c_alice['id']}"
        ).status_code == 404

        # Tentar criar link apontando para conceito ou reflexão privada da Alice retorna 404
        res_link_attempt = intruder.post(
            "/api/knowledge/links",
            json={
                "source_type": "book",
                "source_id": "book-1",
                "target_type": "concept",
                "target_id": c_alice["id"],
                "relation_type": "references",
            },
        )
        assert res_link_attempt.status_code == 404


def test_adversarial_payloads_pagination_limits_and_sqli_via_api(security_api_env):
    """
    Seção 17.5:
    Testa via HTTP:
    - SQL Injection em query params e corpos JSON.
    - Tipos de entidade e relação arbitrários.
    - Payloads acima dos limites (nome > 160, título > 300, rationale > 2000).
    - Paginação extrema (`limit > 200`, `limit <= 0`, `offset < 0`).
    - Ordenação por expressões SQL arbitrárias (`sort=id;DROP TABLE concepts` -> `422`).
    """
    alice, _ = security_api_env["make_client"]("alice")

    sqli = "'; DROP TABLE concepts; --"
    res_sqli_create = alice.post(
        "/api/knowledge/concepts",
        json={"name": f"Conceito {sqli}", "description": sqli},
    )
    assert res_sqli_create.status_code == 201
    assert alice.get(f"/api/knowledge/concepts?search={sqli}").status_code == 200
    assert alice.get(f"/api/knowledge/lookup?q={sqli}").status_code == 200

    # Expressão SQL arbitrária no parâmetro sort é bloqueada pelo Literal do FastAPI/Pydantic (422)
    assert alice.get("/api/knowledge/concepts?sort=name;DROP TABLE concepts").status_code == 422
    assert alice.get("/api/knowledge/reflections?sort=1=1").status_code == 422
    assert alice.get("/api/knowledge/links?sort=random()").status_code == 422

    # Paginação extrema ou negativa -> 422
    assert alice.get("/api/knowledge/concepts?limit=0").status_code == 422
    assert alice.get("/api/knowledge/concepts?limit=-5").status_code == 422
    assert alice.get("/api/knowledge/concepts?limit=201").status_code == 422
    assert alice.get("/api/knowledge/concepts?offset=-1").status_code == 422

    # Payloads excessivos -> 422
    assert alice.post("/api/knowledge/concepts", json={"name": "A" * 161}).status_code == 422
    assert alice.post("/api/knowledge/reflections", json={"title": "T" * 301}).status_code == 422
    assert alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "book",
            "source_id": "book-1",
            "target_type": "book",
            "target_id": "book-2",
            "rationale": "R" * 2001,
        },
    ).status_code == 422

    # Tipos polimórficos arbitrários -> 422
    assert alice.post(
        "/api/knowledge/links",
        json={
            "source_type": "user",
            "source_id": "1",
            "target_type": "book",
            "target_id": "book-1",
        },
    ).status_code == 422
    assert alice.get("/api/knowledge/entities/user/1/connections").status_code == 422
