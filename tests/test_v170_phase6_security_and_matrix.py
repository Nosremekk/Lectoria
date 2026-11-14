"""
Suíte de Validação de Segurança, Matriz de Permissões, Migração e Desempenho — Fase 6 da Lectoria v1.7.0.
"""

import sqlite3
import time
from typing import Optional

import pytest
from fastapi.testclient import TestClient

import auth_service
import clubs_service
import config
import database
import main as app_main
import shared_reading_service as srs


@pytest.fixture
def phase6_env(tmp_path):
    db_path = tmp_path / "lectoria_phase6.db"
    covers_dir = tmp_path / "covers"
    lib_dir = tmp_path / "library"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    old_db, old_cov, old_lib = config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    database.init_db(db_path)

    alice = auth_service.create_user(username="alice_p6", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_p6", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos_p6", password="password123", role="reader")

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('book_p6_01', 'Memórias Póstumas', 'Machado de Assis', 'mp.pdf', 'mp.pdf', 102400, 'pdf', 300, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Clube público (Alice owner, Bob member, Carlos não membro)
    c_pub = clubs_service.create_club("Clube Público P6", "", alice["id"], "public")
    clubs_service.join_club(c_pub["id"], bob["id"])

    # Clube privado (Alice owner, Carlos não membro)
    c_priv = clubs_service.create_club("Clube Privado P6", "", alice["id"], "private")

    # Clube arquivado (Alice owner, Bob member)
    c_arch = clubs_service.create_club("Clube Arquivado P6", "", alice["id"], "public")
    clubs_service.join_club(c_arch["id"], bob["id"])

    r_planned = clubs_service.create_collective_read(c_pub["id"], alice["id"], "book_p6_01", title="Planejada", status="planned")
    r_active = clubs_service.create_collective_read(c_pub["id"], alice["id"], "book_p6_01", title="Em Andamento", status="in_progress")
    r_other = clubs_service.create_collective_read(c_pub["id"], alice["id"], "book_p6_01", title="Outra Leitura", status="in_progress")
    r_completed = clubs_service.create_collective_read(c_pub["id"], alice["id"], "book_p6_01", title="Concluída", status="in_progress")
    r_cancelled = clubs_service.create_collective_read(c_pub["id"], alice["id"], "book_p6_01", title="Cancelada", status="in_progress")
    r_priv = clubs_service.create_collective_read(c_priv["id"], alice["id"], "book_p6_01", title="Privada", status="in_progress")
    r_arch = clubs_service.create_collective_read(c_arch["id"], alice["id"], "book_p6_01", title="Arquivada", status="in_progress")

    # Cria dados antes de concluir/cancelar/arquivar para garantir preservação de histórico
    seed_ids = {}
    for label, r_obj in [("completed", r_completed), ("cancelled", r_cancelled), ("archived", r_arch), ("active", r_active)]:
        t = srs.create_topic(r_obj["id"], bob["id"], f"Tópico {label}", "Conteúdo histórico")
        p = srs.create_post(r_obj["id"], t["id"], bob["id"], "Resposta histórica")
        g = srs.create_goal(r_obj["id"], alice["id"], f"Meta {label}", "Descrição")
        q = srs.share_quote(r_obj["id"], bob["id"], quote_text=f"Citação {label}")
        seed_ids[label] = {"topic": t["id"], "post": p["id"], "goal": g["id"], "quote": q["id"]}

    # Transiciona para completed / cancelled / archived
    clubs_service.update_collective_read(r_completed["id"], alice["id"], status="completed")
    clubs_service.update_collective_read(r_cancelled["id"], alice["id"], status="cancelled")
    clubs_service.archive_club(c_arch["id"], alice["id"])

    def make_client(username: Optional[str]) -> tuple[TestClient, str]:
        client = TestClient(app_main.app)
        if not username:
            return client, ""
        uid = {"alice_p6": alice["id"], "bob_p6": bob["id"], "carlos_p6": carlos["id"]}[username]
        token = auth_service.create_session(uid)
        client.cookies.set("lectoria_session", token)
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        return client, csrf

    try:
        yield {
            "make_client": make_client,
            "alice": alice,
            "bob": bob,
            "carlos": carlos,
            "r_planned": r_planned,
            "r_active": r_active,
            "r_other": r_other,
            "r_completed": r_completed,
            "r_cancelled": r_cancelled,
            "r_priv": r_priv,
            "r_arch": r_arch,
            "seed_ids": seed_ids,
            "db_path": db_path,
        }
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR = old_db, old_cov, old_lib


def test_completed_cancelled_archived_readonly_policy_across_all_writes(phase6_env):
    """
    Garante que leituras concluídas, canceladas e em clubes arquivados:
    1) Permanecem consultáveis (200) para membros autorizados;
    2) Bloqueiam TODAS as alterações colaborativas no backend (400), inclusive moderação e progresso.
    """
    env = phase6_env
    alice_c, alice_csrf = env["make_client"]("alice_p6")
    bob_c, bob_csrf = env["make_client"]("bob_p6")

    for state_key, read_obj in [
        ("completed", env["r_completed"]),
        ("cancelled", env["r_cancelled"]),
        ("archived", env["r_arch"]),
    ]:
        rid = read_obj["id"]
        ids = env["seed_ids"][state_key]

        # Leitura preservada (200)
        for get_url in [
            f"/api/collective-reads/{rid}/overview",
            f"/api/collective-reads/{rid}/topics",
            f"/api/collective-reads/{rid}/topics/{ids['topic']}",
            f"/api/collective-reads/{rid}/goals",
            f"/api/collective-reads/{rid}/quotes",
        ]:
            assert bob_c.get(get_url).status_code == 200, f"GET falhou para {state_key}: {get_url}"

        # Escritas bloqueadas (400) para membro e owner
        writes = [
            (bob_c, bob_csrf, "POST", f"/api/collective-reads/{rid}/topics", {"title": "Novo", "content": "Texto"}),
            (bob_c, bob_csrf, "PUT", f"/api/collective-reads/{rid}/topics/{ids['topic']}", {"title": "Edit", "content": "Texto"}),
            (alice_c, alice_csrf, "PATCH", f"/api/collective-reads/{rid}/topics/{ids['topic']}/moderate", {"is_pinned": True}),
            (alice_c, alice_csrf, "DELETE", f"/api/collective-reads/{rid}/topics/{ids['topic']}", None),
            (bob_c, bob_csrf, "POST", f"/api/collective-reads/{rid}/topics/{ids['topic']}/posts", {"content": "Nova resp"}),
            (bob_c, bob_csrf, "PUT", f"/api/collective-reads/{rid}/topics/{ids['topic']}/posts/{ids['post']}", {"content": "Edit resp"}),
            (bob_c, bob_csrf, "DELETE", f"/api/collective-reads/{rid}/topics/{ids['topic']}/posts/{ids['post']}", None),
            (alice_c, alice_csrf, "POST", f"/api/collective-reads/{rid}/goals", {"title": "Nova meta"}),
            (alice_c, alice_csrf, "PUT", f"/api/collective-reads/{rid}/goals/{ids['goal']}", {"title": "Meta editada"}),
            (alice_c, alice_csrf, "DELETE", f"/api/collective-reads/{rid}/goals/{ids['goal']}", None),
            (bob_c, bob_csrf, "PUT", f"/api/collective-reads/{rid}/goals/{ids['goal']}/progress", {"status": "completed"}),
            (bob_c, bob_csrf, "POST", f"/api/collective-reads/{rid}/quotes", {"quote_text": "Nova citação"}),
            (bob_c, bob_csrf, "DELETE", f"/api/collective-reads/{rid}/quotes/{ids['quote']}", None),
        ]
        for client, csrf, method, url, payload in writes:
            resp = client.request(method, url, json=payload, headers={"x-csrf-token": csrf})
            assert resp.status_code == 400, f"{method} {url} em {state_key} retornou {resp.status_code} ({resp.text})"

    # Leituras planned e in_progress aceitam publicação normalmente
    for writable_id in (env["r_planned"]["id"], env["r_active"]["id"]):
        res = bob_c.post(
            f"/api/collective-reads/{writable_id}/quotes",
            json={"quote_text": "Citação válida"},
            headers={"x-csrf-token": bob_csrf},
        )
        assert res.status_code == 201


def test_security_matrix_anti_idor_anti_enumeration_csrf_and_mass_assignment(phase6_env):
    """
    Valida matriz completa de segurança:
    - Sem sessão -> 401
    - Sem CSRF -> 403
    - Carlos em clube privado -> 404 (anti-enumeração)
    - Carlos em clube público -> 403
    - Bob (member) em rotas de moderação/metas -> 403
    - IDOR cruzado entre leituras/tópicos diferentes -> 404
    - Mass assignment -> 422
    - Respostas de erro sem vazamento de stack/SQLite
    """
    env = phase6_env
    anon_c, _ = env["make_client"](None)
    alice_c, alice_csrf = env["make_client"]("alice_p6")
    bob_c, bob_csrf = env["make_client"]("bob_p6")
    carlos_c, carlos_csrf = env["make_client"]("carlos_p6")

    r_act = env["r_active"]["id"]
    r_oth = env["r_other"]["id"]
    r_priv = env["r_priv"]["id"]
    act_ids = env["seed_ids"]["active"]

    # 1. Sem sessão -> 401
    assert anon_c.get(f"/api/collective-reads/{r_act}/overview").status_code == 401
    assert anon_c.get("/api/books/book_p6_01/collective-reads").status_code == 401

    # 2. Sem CSRF -> 403
    assert bob_c.post(f"/api/collective-reads/{r_act}/topics", json={"title": "A", "content": "B"}).status_code == 403

    # 3. Anti-enumeração (Carlos em privado -> 404; em público -> 403)
    assert carlos_c.get(f"/api/collective-reads/{r_priv}/overview").status_code == 404
    assert carlos_c.post(
        f"/api/collective-reads/{r_priv}/topics",
        json={"title": "Sonda", "content": "Sonda"},
        headers={"x-csrf-token": carlos_csrf},
    ).status_code == 404
    assert carlos_c.get(f"/api/collective-reads/{r_act}/overview").status_code == 403
    assert carlos_c.post(
        f"/api/collective-reads/{r_act}/quotes",
        json={"quote_text": "Invasão"},
        headers={"x-csrf-token": carlos_csrf},
    ).status_code == 403

    # 4. Autorização por papel: Bob não pode moderar nem gerenciar metas
    assert bob_c.patch(
        f"/api/collective-reads/{r_act}/topics/{act_ids['topic']}/moderate",
        json={"is_pinned": True},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 403
    assert bob_c.post(
        f"/api/collective-reads/{r_act}/goals",
        json={"title": "Meta Bob"},
        headers={"x-csrf-token": bob_csrf},
    ).status_code == 403

    # 5. IDOR cruzado entre leituras diferentes -> 404
    assert alice_c.get(f"/api/collective-reads/{r_oth}/topics/{act_ids['topic']}").status_code == 404
    assert alice_c.put(
        f"/api/collective-reads/{r_oth}/goals/{act_ids['goal']}",
        json={"title": "Cruzada"},
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 404
    assert alice_c.delete(
        f"/api/collective-reads/{r_oth}/quotes/{act_ids['quote']}",
        headers={"x-csrf-token": alice_csrf},
    ).status_code == 404

    # 6. Mass assignment rejeitado (422) e ausência de vazamento interno
    bad_payloads = [
        (f"/api/collective-reads/{r_act}/topics", {"title": "T", "content": "C", "user_id": 999, "is_pinned": True}),
        (f"/api/collective-reads/{r_act}/topics/{act_ids['topic']}/posts", {"content": "R", "is_deleted": 1}),
        (f"/api/collective-reads/{r_act}/goals", {"title": "G", "read_id": r_oth}),
        (f"/api/collective-reads/{r_act}/quotes", {"quote_text": "Q", "club_id": "abc"}),
    ]
    for url, payload in bad_payloads:
        r = bob_c.post(url, json=payload, headers={"x-csrf-token": bob_csrf})
        assert r.status_code == 422
        body_lower = r.text.lower()
        assert "traceback" not in body_lower
        assert "sqlite" not in body_lower
        assert "/home/" not in body_lower


def test_sqlite_v11_indexes_foreign_keys_and_performance_budget(phase6_env):
    """
    Valida índices SQLite da v11, integridade de foreign_key_check e latência com volume sintético realista
    (200 tópicos, 600 respostas, 50 metas, 150 citações) visando hardware modesto (Intel Celeron 847).
    """
    env = phase6_env
    rid = env["r_active"]["id"]
    uid_bob = env["bob"]["id"]
    uid_alice = env["alice"]["id"]

    conn = sqlite3.connect(env["db_path"])
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        fk_issues = conn.execute("PRAGMA foreign_key_check;").fetchall()
        assert fk_issues == []

        indexes = {row[1] for row in conn.execute("SELECT type, name FROM sqlite_master WHERE type='index';").fetchall()}
        expected_indexes = {
            "idx_col_disc_topics_read",
            "idx_col_disc_topics_user",
            "idx_col_disc_posts_topic",
            "idx_col_disc_posts_parent",
            "idx_col_disc_posts_user",
            "idx_col_goals_read",
            "idx_col_goal_prog_user",
            "idx_col_quotes_read",
            "idx_col_quotes_book",
        }
        assert expected_indexes.issubset(indexes)

        # Popula volume sintético realista diretamente no SQLite para medir consultas de leitura
        cur = conn.cursor()
        for i in range(200):
            tid = f"perf_topic_{i:03d}"
            cur.execute(
                """
                INSERT INTO collective_discussion_topics (id, read_id, user_id, title, content, is_spoiler)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (tid, rid, uid_bob, f"Tópico Perf {i}", f"Conteúdo de discussão {i}", 1 if i % 5 == 0 else 0),
            )
            for j in range(3):
                cur.execute(
                    """
                    INSERT INTO collective_discussion_posts (id, topic_id, user_id, content)
                    VALUES (?, ?, ?, ?);
                    """,
                    (f"perf_post_{i:03d}_{j}", tid, uid_alice, f"Resposta {j}"),
                )
        for i in range(50):
            cur.execute(
                """
                INSERT INTO collective_reading_goals (id, read_id, created_by, title, display_order)
                VALUES (?, ?, ?, ?, ?);
                """,
                (f"perf_goal_{i:03d}", rid, uid_alice, f"Meta {i}", i + 1),
            )
        for i in range(150):
            cur.execute(
                """
                INSERT INTO collective_shared_quotes (id, read_id, book_id, user_id, quote_text)
                VALUES (?, ?, 'book_p6_01', ?, ?);
                """,
                (f"perf_quote_{i:03d}", rid, uid_bob, f"Citação de desempenho número {i}"),
            )
        conn.commit()
    finally:
        conn.close()

    bob_c, _ = env["make_client"]("bob_p6")
    timings = {}
    for label, url in [
        ("overview", f"/api/collective-reads/{rid}/overview"),
        ("topics_p1", f"/api/collective-reads/{rid}/topics?page=1&limit=20"),
        ("goals", f"/api/collective-reads/{rid}/goals"),
        ("quotes_p1", f"/api/collective-reads/{rid}/quotes?page=1&limit=20"),
    ]:
        t0 = time.perf_counter()
        resp = bob_c.get(url)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert resp.status_code == 200
        timings[label] = elapsed_ms
        # Orçamento confortável (< 250ms por endpoint mesmo em ambiente de teste com TestClient)
        assert elapsed_ms < 250.0, f"{label} levou {elapsed_ms:.1f}ms"
