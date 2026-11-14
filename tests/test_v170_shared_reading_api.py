"""
Suíte de Testes Automatizados — Fase 3 da Lectoria v1.7.0 (API REST e Segurança).
Valida os endpoints /api/collective-reads/{read_id}/... em main.py com:
1. Autenticação de sessão e proteção CSRF (Double Submit Cookie).
2. Controle de papéis: Alice (owner), Bob (member), Carlos (não membro).
3. Anti-IDOR, anti-enumeração (404 em clube privado vs 403 em público) e isolamento entre leituras/clubes.
4. Validação estrita de payloads Pydantic (anti-mass-assignment, datas, enums, paginação, limites, CFIs, vazios).
5. Integridade de estados (soft-delete, tópicos trancados, leitura cancelada, clube arquivado, progresso desacoplado).
6. Proteção de spoilers nas listagens/overview vs revelação intencional no detalhe.
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
def v170_api_env(tmp_path, monkeypatch):
    """Ambiente isolado de API com TestClient, 3 usuários (Alice, Bob, Carlos), 2 livros e 2 clubes."""
    test_db = tmp_path / "test_lectoria_v170_api.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()

    alice = auth_service.create_user(username="alice_api", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_api", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos_api", password="password123", role="reader")

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES
                ('book170api01', 'Dom Casmurro', 'Machado de Assis', 'dom.pdf', 'dom.pdf', 1024000, 'pdf', 256, 0),
                ('book170api02', 'Quincas Borba', 'Machado de Assis', 'quincas.epub', 'quincas.epub', 512000, 'epub', 210, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage)
            VALUES (?, 'book170api01', 30, 256, 11.7);
            """,
            (bob["id"],),
        )
        cur.execute(
            """
            INSERT INTO notes (id, user_id, book_id, page, content)
            VALUES
                ('note_api_bob_b1', ?, 'book170api01', 99, 'Nota privada do Bob no livro 1.'),
                ('note_api_alice_b1', ?, 'book170api01', 50, 'Nota secreta da Alice.'),
                ('note_api_bob_b2', ?, 'book170api02', 12, 'Nota do Bob no livro 2.');
            """,
            (bob["id"], alice["id"], bob["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    pub_club = clubs_service.create_club(
        name="Clube Público API",
        description="Aberto para membros",
        visibility="public",
        owner_id=alice["id"],
    )
    clubs_service.join_club(pub_club["id"], bob["id"])

    pub_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170api01",
        title="Leitura Coletiva 1",
        status="in_progress",
    )
    other_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170api02",
        title="Leitura Coletiva 2",
        status="in_progress",
    )

    priv_club = clubs_service.create_club(
        name="Clube Privado API",
        description="Restrito",
        visibility="private",
        owner_id=alice["id"],
    )
    priv_read = clubs_service.create_collective_read(
        club_id=priv_club["id"],
        user_id=alice["id"],
        book_id="book170api01",
        title="Leitura Privada",
        status="in_progress",
    )

    def make_client(username: Optional[str]) -> tuple[TestClient, str]:
        client = TestClient(app)
        if not username:
            return client, ""
        # Obtém cookie CSRF inicial via página HTML
        client.get("/")
        csrf_token = client.cookies.get("lectoria_csrf", "")
        res = client.post(
            "/api/auth/login",
            json={"username": username, "password": "password123"},
            headers={"x-csrf-token": csrf_token} if csrf_token else {},
        )
        assert res.status_code == 200
        csrf_token = client.cookies.get("lectoria_csrf", "") or csrf_token
        return client, csrf_token

    return {
        "alice": alice,
        "bob": bob,
        "carlos": carlos,
        "pub_club": pub_club,
        "pub_read": pub_read,
        "other_read": other_read,
        "priv_club": priv_club,
        "priv_read": priv_read,
        "make_client": make_client,
    }


def test_auth_and_csrf_enforcement(v170_api_env):
    env = v170_api_env
    read_id = env["pub_read"]["id"]

    anon, _ = env["make_client"](None)
    assert anon.get(f"/api/collective-reads/{read_id}/overview").status_code == 401
    assert anon.post(f"/api/collective-reads/{read_id}/topics", json={"title": "A", "content": "B"}).status_code == 401

    bob_client, bob_csrf = env["make_client"]("bob_api")
    assert bob_csrf, "Cookie lectoria_csrf deve ser emitido após navegação/login"

    # Sessão válida em GET
    assert bob_client.get(f"/api/collective-reads/{read_id}/overview").status_code == 200

    # Token CSRF ausente -> 403
    res_no_csrf = bob_client.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Sem CSRF", "content": "Bloqueado"},
    )
    assert res_no_csrf.status_code == 403

    # Token CSRF inválido -> 403
    res_bad_csrf = bob_client.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "CSRF errado", "content": "Bloqueado"},
        headers={"x-csrf-token": "invalid-token-123"},
    )
    assert res_bad_csrf.status_code == 403

    # Token CSRF válido -> 201
    res_ok = bob_client.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Tópico válido", "content": "Com CSRF válido"},
        headers={"x-csrf-token": bob_csrf},
    )
    assert res_ok.status_code == 201
    assert res_ok.json()["topic"]["user_id"] == env["bob"]["id"]


def test_roles_permissions_and_spoiler_privacy(v170_api_env):
    env = v170_api_env
    read_id = env["pub_read"]["id"]
    alice_c, alice_csrf = env["make_client"]("alice_api")
    bob_c, bob_csrf = env["make_client"]("bob_api")
    carlos_c, carlos_csrf = env["make_client"]("carlos_api")

    # Bob cria tópico com spoiler
    r_topic = bob_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Final surpreendente", "content": "Segredo do capítulo final", "is_spoiler": True},
        headers={"x-csrf-token": bob_csrf},
    )
    assert r_topic.status_code == 201
    topic_id = r_topic.json()["topic"]["id"]

    # Overview e listagem NÃO expõem conteúdo de spoiler
    ov = alice_c.get(f"/api/collective-reads/{read_id}/overview").json()
    assert "Segredo do capítulo final" not in str(ov)
    listed = alice_c.get(f"/api/collective-reads/{read_id}/topics").json()
    t_item = listed["topics"][0]
    assert "content" not in t_item
    assert "Segredo" not in t_item["content_preview"]

    # Detalhe do tópico revela intencionalmente
    detail = alice_c.get(f"/api/collective-reads/{read_id}/topics/{topic_id}").json()
    assert detail["topic"]["content"] == "Segredo do capítulo final"

    # Alice NÃO pode editar o conteúdo do tópico de Bob (403)
    r_edit_other = alice_c.put(
        f"/api/collective-reads/{read_id}/topics/{topic_id}",
        json={"title": "Alterado por Alice"},
        headers={"x-csrf-token": alice_csrf},
    )
    assert r_edit_other.status_code == 403

    # Bob (member) NÃO pode moderar (fixar/trancar) -> 403
    r_mod_bob = bob_c.patch(
        f"/api/collective-reads/{read_id}/topics/{topic_id}/moderate",
        json={"is_pinned": True, "is_locked": True},
        headers={"x-csrf-token": bob_csrf},
    )
    assert r_mod_bob.status_code == 403

    # Alice (owner) pode fixar e trancar -> 200
    r_mod_alice = alice_c.patch(
        f"/api/collective-reads/{read_id}/topics/{topic_id}/moderate",
        json={"is_pinned": True, "is_locked": True},
        headers={"x-csrf-token": alice_csrf},
    )
    assert r_mod_alice.status_code == 200
    assert r_mod_alice.json()["topic"]["is_pinned"] is True
    assert r_mod_alice.json()["topic"]["is_locked"] is True

    # Tópico trancado impede edição e novas respostas -> 400
    assert bob_c.put(
        f"/api/collective-reads/{read_id}/topics/{topic_id}",
        json={"content": "Tentando editar trancado"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/topics/{topic_id}/posts",
        json={"content": "Comentário em trancado"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400

    # Bob não pode criar metas; Carlos não acessa clube público (403)
    assert bob_c.post(
        f"/api/collective-reads/{read_id}/goals",
        json={"title": "Meta não autorizada"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 403
    assert carlos_c.get(f"/api/collective-reads/{read_id}/overview").status_code == 403
    assert carlos_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Invasão", "content": "Texto"},
        headers={"x-csrf-token": carlos_csrf},
    ).status_code == 403


def test_idor_cross_read_and_anti_enumeration(v170_api_env):
    env = v170_api_env
    pub_read_id = env["pub_read"]["id"]
    other_read_id = env["other_read"]["id"]
    priv_read_id = env["priv_read"]["id"]

    alice_c, alice_csrf = env["make_client"]("alice_api")
    bob_c, bob_csrf = env["make_client"]("bob_api")
    carlos_c, carlos_csrf = env["make_client"]("carlos_api")

    # Anti-enumeração: Carlos em clube privado recebe sempre 404 (nunca 403)
    assert carlos_c.get(f"/api/collective-reads/{priv_read_id}/overview").status_code == 404
    assert carlos_c.get(f"/api/collective-reads/{priv_read_id}/topics").status_code == 404
    assert carlos_c.get(f"/api/collective-reads/{priv_read_id}/goals").status_code == 404
    assert carlos_c.get(f"/api/collective-reads/{priv_read_id}/quotes").status_code == 404
    assert carlos_c.post(
        f"/api/collective-reads/{priv_read_id}/topics",
        json={"title": "Probe", "content": "Probe"},
        headers={"x-csrf-token": carlos_csrf},
    ).status_code == 404

    # Cria recursos em pub_read e other_read para testar isolamento cruzado
    t_pub = alice_c.post(
        f"/api/collective-reads/{pub_read_id}/topics",
        json={"title": "Tópico 1", "content": "Conteúdo 1"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["topic"]
    t_other = alice_c.post(
        f"/api/collective-reads/{other_read_id}/topics",
        json={"title": "Tópico Outro", "content": "Conteúdo Outro"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["topic"]
    post_other = alice_c.post(
        f"/api/collective-reads/{other_read_id}/topics/{t_other['id']}/posts",
        json={"content": "Post em outro tópico"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["post"]
    goal_other = alice_c.post(
        f"/api/collective-reads/{other_read_id}/goals",
        json={"title": "Meta de outra leitura"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["goal"]
    quote_priv = alice_c.post(
        f"/api/collective-reads/{priv_read_id}/quotes",
        json={"quote_text": "Citação do clube privado"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["quote"]

    # Acesso a tópico/meta/citação usando read_id divergente -> 404
    assert bob_c.get(f"/api/collective-reads/{pub_read_id}/topics/{t_other['id']}").status_code == 404
    assert bob_c.put(
        f"/api/collective-reads/{pub_read_id}/goals/{goal_other['id']}/progress",
        json={"status": "completed"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 404
    assert bob_c.delete(
        f"/api/collective-reads/{pub_read_id}/quotes/{quote_priv['id']}",
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 404

    # Resposta cross-topic (parent_post_id de outro tópico) -> 404 (não revela existência em outro tópico)
    assert bob_c.post(
        f"/api/collective-reads/{pub_read_id}/topics/{t_pub['id']}/posts",
        json={"content": "Cross topic", "parent_post_id": post_other["id"]},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 404

    # Bob NÃO pode compartilhar nota privada da Alice -> 403
    assert bob_c.post(
        f"/api/collective-reads/{pub_read_id}/quotes",
        json={"note_id": "note_api_alice_b1"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 403

    # Bob NÃO pode compartilhar nota própria do livro 2 na leitura do livro 1 -> 400
    assert bob_c.post(
        f"/api/collective-reads/{pub_read_id}/quotes",
        json={"note_id": "note_api_bob_b2"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400


def test_payload_validation_and_mass_assignment(v170_api_env):
    env = v170_api_env
    read_id = env["pub_read"]["id"]
    alice_c, alice_csrf = env["make_client"]("alice_api")

    # Paginação negativa ou excessiva e filtro inválido -> 422
    assert alice_c.get(f"/api/collective-reads/{read_id}/topics?page=0").status_code == 422
    assert alice_c.get(f"/api/collective-reads/{read_id}/topics?limit=-5").status_code == 422
    assert alice_c.get(f"/api/collective-reads/{read_id}/topics?limit=500").status_code == 422
    assert alice_c.get(f"/api/collective-reads/{read_id}/topics?filter=unknown").status_code == 422

    # Conteúdo vazio / whitespace -> 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "   ", "content": "Texto"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422

    # Payload excessivo -> 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/quotes",
        json={"quote_text": "X" * 1501},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422

    # Localizador EPUB inválido e range de páginas invertido -> 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "CFI ruim", "content": "Texto", "epub_cfi": "not-a-cfi"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Páginas invertidas", "content": "Texto", "page_start": 50, "page_end": 10},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422

    # Data inválida na meta -> 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/goals",
        json={"title": "Meta data ruim", "target_date": "2026-02-31"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422

    # Mass assignment (campos privilegiados enviados pelo cliente) -> 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Mass", "content": "Assignment", "user_id": 999},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Mass", "content": "Assignment", "is_pinned": True},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422
    assert alice_c.post(
        f"/api/collective-reads/{read_id}/topics",
        json={"title": "Mass", "content": "Assignment", "is_deleted": True},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422

    # Enum desconhecido no progresso voluntário -> 422
    goal = alice_c.post(
        f"/api/collective-reads/{read_id}/goals",
        json={"title": "Meta válida", "target_date": "2026-12-01"},
        headers={"x-csrf-token": alice_csrf},
    ).json()["goal"]
    assert alice_c.put(
        f"/api/collective-reads/{read_id}/goals/{goal['id']}/progress",
        json={"status": "skipped"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 422


def test_integrity_soft_delete_goals_readonly_states(v170_api_env):
    env = v170_api_env
    pub_club_id = env["pub_club"]["id"]
    pub_read_id = env["pub_read"]["id"]
    other_read_id = env["other_read"]["id"]
    bob_id = env["bob"]["id"]

    alice_c, alice_csrf = env["make_client"]("alice_api")
    bob_c, bob_csrf = env["make_client"]("bob_api")

    # Tópico + resposta raiz + resposta filha (profundidade 1)
    topic = bob_c.post(
        f"/api/collective-reads/{pub_read_id}/topics",
        json={"title": "Discussão", "content": "Texto inicial", "epub_cfi": "epubcfi(/6/4!/2/2)"},
        headers={"x-csrf-token": bob_csrf},
    ).json()["topic"]
    p0 = bob_c.post(
        f"/api/collective-reads/{pub_read_id}/topics/{topic['id']}/posts",
        json={"content": "Raiz"},
        headers={"x-csrf-token": bob_csrf},
    ).json()["post"]
    p1 = alice_c.post(
        f"/api/collective-reads/{pub_read_id}/topics/{topic['id']}/posts",
        json={"content": "Filha", "parent_post_id": p0["id"]},
        headers={"x-csrf-token": alice_csrf},
    ).json()["post"]

    # Profundidade > 1 rejeitada -> 400
    assert bob_c.post(
        f"/api/collective-reads/{pub_read_id}/topics/{topic['id']}/posts",
        json={"content": "Neta", "parent_post_id": p1["id"]},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400

    # Soft delete do tópico preserva respostas no detalhe
    assert alice_c.delete(
        f"/api/collective-reads/{pub_read_id}/topics/{topic['id']}",
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 200
    detail_after = bob_c.get(f"/api/collective-reads/{pub_read_id}/topics/{topic['id']}").json()
    assert detail_after["topic"]["is_deleted"] is True
    assert len(detail_after["posts"]) == 2

    # Meta + progresso voluntário + arquivamento via DELETE
    goal = alice_c.post(
        f"/api/collective-reads/{pub_read_id}/goals",
        json={"title": "Cap 1-10", "target_page": 40},
        headers={"x-csrf-token": alice_csrf},
    ).json()["goal"]
    assert bob_c.put(
        f"/api/collective-reads/{pub_read_id}/goals/{goal['id']}/progress",
        json={"status": "completed"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 200

    # Progresso privado de Bob permanece intacto (30, 11.7%)
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT current_page, percentage FROM reading_progress WHERE user_id = ? AND book_id = 'book170api01';",
            (bob_id,),
        ).fetchone()
        assert row["current_page"] == 30
        assert row["percentage"] == pytest.approx(11.7)
    finally:
        conn.close()

    # DELETE meta -> arquiva sem excluir fisicamente
    del_goal = alice_c.delete(
        f"/api/collective-reads/{pub_read_id}/goals/{goal['id']}",
        headers={"x-csrf-token": alice_csrf},
    )
    assert del_goal.status_code == 200
    assert del_goal.json()["goal"]["is_archived"] is True
    assert len(bob_c.get(f"/api/collective-reads/{pub_read_id}/goals").json()["goals"]) == 0
    assert len(bob_c.get(f"/api/collective-reads/{pub_read_id}/goals?include_archived=true").json()["goals"]) == 1

    # Citação via nota privada do próprio Bob
    q = bob_c.post(
        f"/api/collective-reads/{pub_read_id}/quotes",
        json={"note_id": "note_api_bob_b1"},
        headers={"x-csrf-token": bob_csrf},
    )
    assert q.status_code == 201

    # Cancelar leitura -> modo somente-leitura
    clubs_service.update_collective_read(pub_read_id, env["alice"]["id"], status="cancelled")
    assert bob_c.post(
        f"/api/collective-reads/{pub_read_id}/topics",
        json={"title": "Bloqueado", "content": "Leitura cancelada"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 400
    assert bob_c.get(f"/api/collective-reads/{pub_read_id}/quotes").status_code == 200

    # Arquivar clube -> outra leitura também fica somente-leitura
    clubs_service.archive_club(pub_club_id, env["alice"]["id"])
    assert alice_c.post(
        f"/api/collective-reads/{other_read_id}/topics",
        json={"title": "Bloqueado", "content": "Clube arquivado"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 400
    assert bob_c.get(f"/api/collective-reads/{other_read_id}/overview").status_code == 200
