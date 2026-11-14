"""
Suíte de Testes Automatizados — Fase 2 da Lectoria v1.7.0 (Leitura Compartilhada).
Valida o módulo shared_reading_service.py com 3 usuários fictícios:
- Alice (owner do clube)
- Bob (member do clube)
- Carlos (non-member / externo)

Cobre:
1. Visão geral da leitura coletiva e controle de acesso / anti-enumeração (403 vs 404).
2. Tópicos de discussão: criação, edição, paginação, moderação (pin/lock/delete) e spoiler masking.
3. Respostas encadeadas: limite de profundidade (MAX_THREAD_DEPTH = 1), bloqueio cross-topic, edição e soft-delete.
4. Metas coletivas e progresso individual voluntário desacoplado de reading_progress.
5. Citações compartilhadas: manual, via nota privada (anti-IDOR e mesmo livro), limite de tamanho (1500 chars) e soft-delete.
6. Modo somente-leitura e preservação de histórico em clubes arquivados e leituras canceladas/concluídas.
"""

import pytest

import auth_service
import clubs_service
import config
import database
import shared_reading_service as srs


@pytest.fixture
def v170_service_env(tmp_path, monkeypatch):
    """Ambiente isolado com SQLite migrado para v11, 3 usuários, 2 livros e 2 clubes."""
    test_db = tmp_path / "test_lectoria_v170_service.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()

    alice = auth_service.create_user(username="alice", password="password123", role="admin")
    bob = auth_service.create_user(username="bob", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos", password="password123", role="reader")

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES
                ('book170svc01', 'Dom Casmurro', 'Machado de Assis', 'dom_casmurro.pdf', 'dom_casmurro.pdf', 1024000, 'pdf', 256, 0),
                ('book170svc02', 'Memórias Póstumas', 'Machado de Assis', 'memorias.epub', 'memorias.epub', 512000, 'epub', 200, 0);
            """
        )
        # Progresso privado inicial de Bob em book170svc01
        cur.execute(
            """
            INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage)
            VALUES (?, 'book170svc01', 42, 256, 16.4);
            """,
            (bob["id"],),
        )
        # Notas privadas: uma de Bob no livro 1, uma de Alice no livro 1, uma de Bob no livro 2
        cur.execute(
            """
            INSERT INTO notes (id, user_id, book_id, page, content)
            VALUES
                ('note_bob_b1', ?, 'book170svc01', 120, 'Capitu tinha olhos de ressaca.'),
                ('note_alice_b1', ?, 'book170svc01', 88, 'Nota privada secreta da Alice.'),
                ('note_bob_b2', ?, 'book170svc02', 15, 'Ao verme que primeiro roeu.');
            """,
            (bob["id"], alice["id"], bob["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # Clube público: Alice owner, Bob member
    pub_club = clubs_service.create_club(
        name="Clube Público Machado",
        description="Debates abertos",
        visibility="public",
        owner_id=alice["id"],
    )
    clubs_service.join_club(pub_club["id"], bob["id"])

    pub_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170svc01",
        title="Leitura de Dom Casmurro",
        status="in_progress",
    )
    other_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170svc02",
        title="Leitura de Memórias",
        status="planned",
    )

    # Clube privado: apenas Alice (Carlos e Bob não são membros)
    priv_club = clubs_service.create_club(
        name="Clube Privado Secreto",
        description="Apenas convidados",
        visibility="private",
        owner_id=alice["id"],
    )
    priv_read = clubs_service.create_collective_read(
        club_id=priv_club["id"],
        user_id=alice["id"],
        book_id="book170svc01",
        title="Leitura Privada Dom Casmurro",
        status="in_progress",
    )

    return {
        "alice": alice,
        "bob": bob,
        "carlos": carlos,
        "pub_club": pub_club,
        "pub_read": pub_read,
        "other_read": other_read,
        "priv_club": priv_club,
        "priv_read": priv_read,
    }


def test_overview_and_anti_enumeration_access_control(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    carlos_id = env["carlos"]["id"]
    pub_read_id = env["pub_read"]["id"]
    priv_read_id = env["priv_read"]["id"]

    # Alice e Bob acessam visão geral do clube público
    ov_alice = srs.get_shared_reading_overview(pub_read_id, alice_id)
    ov_bob = srs.get_shared_reading_overview(pub_read_id, bob_id)
    assert ov_alice["read"]["id"] == pub_read_id
    assert ov_bob["counts"]["topics"] == 0
    assert ov_bob["read"]["user_club_role"] == "member"

    # Carlos (externo) recebe 403 em clube público e 404 (anti-enumeração) em clube privado
    with pytest.raises(srs.ClubPermissionError):
        srs.get_shared_reading_overview(pub_read_id, carlos_id)
    with pytest.raises(srs.CollectiveReadNotFoundError):
        srs.get_shared_reading_overview(priv_read_id, carlos_id)

    # Carlos também é bloqueado em listagens e criações
    with pytest.raises(srs.ClubPermissionError):
        srs.list_topics(pub_read_id, carlos_id)
    with pytest.raises(srs.CollectiveReadNotFoundError):
        srs.list_topics(priv_read_id, carlos_id)
    with pytest.raises(srs.ClubPermissionError):
        srs.create_topic(pub_read_id, carlos_id, title="Invasão", content="Texto")


def test_topic_lifecycle_and_pagination(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_read_id = env["pub_read"]["id"]

    t1 = srs.create_topic(
        pub_read_id, bob_id,
        title="Primeiras impressões",
        content="O narrador já começa melancólico.",
        chapter_ref="Capítulo 1",
        page_start=1,
        page_end=10,
    )
    t2 = srs.create_topic(
        pub_read_id, alice_id,
        title="Análise do estilo",
        content="Ironia machadiana em destaque.",
    )
    t3 = srs.create_topic(
        pub_read_id, bob_id,
        title="Dúvida sobre o capítulo 5",
        content="Alguém notou a referência?",
    )

    assert t1["user_id"] == bob_id
    assert t1["chapter_ref"] == "Capítulo 1"
    assert t1["page_start"] == 1

    # Bob edita próprio tópico; Alice não pode editar conteúdo de tópico de Bob
    edited = srs.edit_topic(pub_read_id, t1["id"], bob_id, title="Primeiras impressões (atualizado)")
    assert edited["title"] == "Primeiras impressões (atualizado)"

    with pytest.raises(srs.ClubPermissionError):
        srs.edit_topic(pub_read_id, t1["id"], alice_id, title="Tentativa da Alice")

    # Paginação
    p1 = srs.list_topics(pub_read_id, bob_id, page=1, limit=2)
    p2 = srs.list_topics(pub_read_id, bob_id, page=2, limit=2)
    assert p1["total"] == 3
    assert len(p1["topics"]) == 2
    assert len(p2["topics"]) == 1

    # Detalhes do tópico
    detail = srs.get_topic_detail(pub_read_id, t2["id"], bob_id)
    assert detail["topic"]["id"] == t2["id"]
    assert detail["topic"]["content"] == "Ironia machadiana em destaque."
    assert detail["total_posts"] == 0


def test_topic_spoiler_masking_and_moderation(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_club_id = env["pub_club"]["id"]
    pub_read_id = env["pub_read"]["id"]

    spoiler_topic = srs.create_topic(
        pub_read_id, alice_id,
        title="Sobre o final do livro",
        content="Ezequiel aparece no final adulto.",
        is_spoiler=True,
    )
    normal_topic = srs.create_topic(
        pub_read_id, bob_id,
        title="Ritmo de leitura",
        content="Estou lendo 15 páginas por dia.",
        is_spoiler=False,
    )

    # Na listagem, preview de spoiler é mascarado
    listed = srs.list_topics(pub_read_id, bob_id)
    by_id = {item["id"]: item for item in listed["topics"]}
    assert by_id[spoiler_topic["id"]]["content_preview"] == srs.SPOILER_MASK_TEXT
    assert "Ezequiel" not in by_id[spoiler_topic["id"]]["content_preview"]
    assert "content" not in by_id[spoiler_topic["id"]]
    assert "15 páginas" in by_id[normal_topic["id"]]["content_preview"]

    # No detalhe do tópico, conteúdo completo fica disponível
    detail = srs.get_topic_detail(pub_read_id, spoiler_topic["id"], bob_id)
    assert "Ezequiel" in detail["topic"]["content"]

    # Bob (member comum) não pode moderar
    with pytest.raises(srs.ClubPermissionError):
        srs.moderate_topic(pub_read_id, normal_topic["id"], bob_id, is_pinned=True)

    # Alice (owner) fixa e tranca o tópico normal
    pinned = srs.moderate_topic(pub_read_id, normal_topic["id"], alice_id, is_pinned=True, is_locked=True)
    assert pinned["is_pinned"] is True
    assert pinned["is_locked"] is True

    # Tópico trancado bloqueia edição e novas respostas
    with pytest.raises(srs.InvalidOperationError):
        srs.edit_topic(pub_read_id, normal_topic["id"], bob_id, content="Editando trancado")
    with pytest.raises(srs.InvalidOperationError):
        srs.create_post(pub_read_id, normal_topic["id"], bob_id, content="Comentário em trancado")

    # Promovendo Bob a moderator permite moderar (inclusive soft-delete)
    clubs_service.update_member_role(pub_club_id, alice_id, bob_id, "moderator")
    deleted = srs.moderate_topic(pub_read_id, spoiler_topic["id"], bob_id, is_deleted=True)
    assert deleted["is_deleted"] is True

    # Tópico deletado não aparece em list_topics, mascara conteúdo no detalhe e bloqueia mutações
    after_del = srs.list_topics(pub_read_id, alice_id)
    assert spoiler_topic["id"] not in {i["id"] for i in after_del["topics"]}
    del_detail = srs.get_topic_detail(pub_read_id, spoiler_topic["id"], alice_id)
    assert del_detail["topic"]["is_deleted"] is True
    assert del_detail["topic"]["content"] == srs.DELETED_CONTENT_TEXT
    with pytest.raises(srs.InvalidOperationError):
        srs.create_post(pub_read_id, spoiler_topic["id"], alice_id, content="Post em tópico removido")


def test_threaded_replies_depth_limit_and_cross_topic_isolation(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_read_id = env["pub_read"]["id"]

    topic_a = srs.create_topic(pub_read_id, alice_id, title="Tópico A", content="Debate A")
    topic_b = srs.create_topic(pub_read_id, alice_id, title="Tópico B", content="Debate B")

    root_post = srs.create_post(pub_read_id, topic_a["id"], alice_id, content="Comentário raiz")
    assert root_post["depth"] == 0

    reply_1 = srs.create_post(
        pub_read_id, topic_a["id"], bob_id,
        content="Resposta ao raiz",
        parent_post_id=root_post["id"],
    )
    assert reply_1["depth"] == 1
    assert reply_1["parent_post_id"] == root_post["id"]

    # Profundidade > MAX_THREAD_DEPTH (1) é rejeitada
    with pytest.raises(srs.InvalidOperationError):
        srs.create_post(
            pub_read_id, topic_a["id"], alice_id,
            content="Resposta de nível 2",
            parent_post_id=reply_1["id"],
        )

    # Cross-topic parent_post_id é rejeitado
    with pytest.raises((srs.InvalidOperationError, srs.PostNotFoundError)):
        srs.create_post(
            pub_read_id, topic_b["id"], bob_id,
            content="Tentando referenciar post do tópico A no B",
            parent_post_id=root_post["id"],
        )

    # Bob edita próprio post; Alice não pode editar post de Bob
    edited_post = srs.edit_post(pub_read_id, topic_a["id"], reply_1["id"], bob_id, content="Resposta editada")
    assert edited_post["content"] == "Resposta editada"
    with pytest.raises(srs.ClubPermissionError):
        srs.edit_post(pub_read_id, topic_a["id"], reply_1["id"], alice_id, content="Alice editando Bob")

    # Alice (owner) pode fazer soft-delete no post de Bob
    del_res = srs.delete_post(pub_read_id, topic_a["id"], reply_1["id"], alice_id)
    assert del_res["is_deleted"] is True

    detail = srs.get_topic_detail(pub_read_id, topic_a["id"], bob_id)
    posts_by_id = {p["id"]: p for p in detail["posts"]}
    assert posts_by_id[reply_1["id"]]["content"] == srs.DELETED_CONTENT_TEXT


def test_goals_and_voluntary_individual_progress_decoupled(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_read_id = env["pub_read"]["id"]

    # Bob (member) não pode criar metas
    with pytest.raises(srs.ClubPermissionError):
        srs.create_goal(pub_read_id, bob_id, title="Meta do Bob")

    g1 = srs.create_goal(
        pub_read_id, alice_id,
        title="Semana 1: Capítulos 1 a 20",
        description="Leitura inicial",
        chapter_ref="Cap. 1–20",
        target_page=50,
    )
    g2 = srs.create_goal(
        pub_read_id, alice_id,
        title="Semana 2: Capítulos 21 a 50",
        target_page=120,
    )

    # Bob não pode editar meta
    with pytest.raises(srs.ClubPermissionError):
        srs.update_goal(pub_read_id, g1["id"], bob_id, title="Hack")

    # Alice reordena e arquiva g2
    srs.update_goal(pub_read_id, g1["id"], alice_id, display_order=2)
    srs.update_goal(pub_read_id, g2["id"], alice_id, is_archived=True)

    active_goals = srs.list_goals(pub_read_id, bob_id, include_archived=False)
    all_goals = srs.list_goals(pub_read_id, bob_id, include_archived=True)
    assert len(active_goals["goals"]) == 1
    assert len(all_goals["goals"]) == 2

    # Bob atualiza seu progresso voluntário em g1
    p_prog = srs.update_voluntary_goal_progress(pub_read_id, g1["id"], bob_id, "in_progress")
    assert p_prog["status"] == "in_progress"
    p_done = srs.update_voluntary_goal_progress(pub_read_id, g1["id"], bob_id, "completed")
    assert p_done["status"] == "completed"

    # Status inválido ou meta arquivada devem falhar
    with pytest.raises(srs.InvalidOperationError):
        srs.update_voluntary_goal_progress(pub_read_id, g1["id"], bob_id, "abandoned")
    with pytest.raises(srs.InvalidOperationError):
        srs.update_voluntary_goal_progress(pub_read_id, g2["id"], bob_id, "completed")

    # Progresso privado em reading_progress permanece intacto (desacoplado)
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT current_page, percentage FROM reading_progress WHERE user_id = ? AND book_id = 'book170svc01';",
            (bob_id,),
        ).fetchone()
        assert row["current_page"] == 42
        assert row["percentage"] == pytest.approx(16.4)
    finally:
        conn.close()


def test_quotes_manual_and_from_private_note_anti_idor(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_read_id = env["pub_read"]["id"]

    # Citação manual da Alice
    q_alice = srs.share_quote(
        pub_read_id, alice_id,
        quote_text="Não consultes dicionários.",
        comment_text="Abertura clássica.",
        chapter_name="Capítulo I",
        page_number=10,
        is_spoiler=False,
    )
    assert q_alice["user_id"] == alice_id

    # Bob compartilha a partir da própria nota privada do livro 1
    q_bob = srs.share_quote(
        pub_read_id, bob_id,
        note_id="note_bob_b1",
        is_spoiler=True,
    )
    assert "ressaca" in q_bob["quote_text"]
    assert q_bob["page_number"] == 120

    # Anti-IDOR: Bob NÃO pode compartilhar nota privada da Alice
    with pytest.raises(srs.ClubPermissionError):
        srs.share_quote(pub_read_id, bob_id, note_id="note_alice_b1")

    # Nota de outro livro (book170svc02) não pode ser compartilhada em leitura do livro 1
    with pytest.raises(srs.InvalidOperationError):
        srs.share_quote(pub_read_id, bob_id, note_id="note_bob_b2")

    # Limite de direitos autorais (MAX_QUOTE_TEXT_LEN = 1500)
    with pytest.raises(srs.InvalidOperationError):
        srs.share_quote(pub_read_id, bob_id, quote_text="A" * (srs.MAX_QUOTE_TEXT_LEN + 1))

    # Spoiler masking na listagem de citações
    listed = srs.list_quotes(pub_read_id, alice_id)
    by_id = {q["id"]: q for q in listed["quotes"]}
    assert by_id[q_bob["id"]]["quote_preview"] == srs.SPOILER_MASK_TEXT

    # Bob não pode apagar citação da Alice; Alice (autora/owner) pode apagar
    with pytest.raises(srs.ClubPermissionError):
        srs.delete_quote(pub_read_id, q_alice["id"], bob_id)
    del_q = srs.delete_quote(pub_read_id, q_alice["id"], alice_id)
    assert del_q["is_deleted"] is True


def test_archived_club_and_cancelled_read_are_readonly(v170_service_env):
    env = v170_service_env
    alice_id = env["alice"]["id"]
    bob_id = env["bob"]["id"]
    pub_club_id = env["pub_club"]["id"]
    pub_read_id = env["pub_read"]["id"]
    other_read_id = env["other_read"]["id"]

    # Cria conteúdo antes de cancelar/arquivar
    t = srs.create_topic(pub_read_id, bob_id, title="Tópico antes do cancelamento", content="Conteúdo")
    g = srs.create_goal(pub_read_id, alice_id, title="Meta antes")
    q = srs.share_quote(pub_read_id, bob_id, quote_text="Citação antes")

    # Cancela pub_read -> mutações bloqueadas, leitura preservada
    clubs_service.update_collective_read(pub_read_id, alice_id, status="cancelled")
    with pytest.raises(srs.InvalidOperationError):
        srs.create_topic(pub_read_id, bob_id, title="Novo", content="Bloqueado")
    with pytest.raises(srs.InvalidOperationError):
        srs.create_post(pub_read_id, t["id"], bob_id, content="Bloqueado")
    with pytest.raises(srs.InvalidOperationError):
        srs.update_voluntary_goal_progress(pub_read_id, g["id"], bob_id, "completed")
    with pytest.raises(srs.InvalidOperationError):
        srs.share_quote(pub_read_id, bob_id, quote_text="Bloqueado")

    assert srs.list_topics(pub_read_id, bob_id)["total"] == 1
    assert len(srs.list_goals(pub_read_id, bob_id)["goals"]) == 1
    assert srs.list_quotes(pub_read_id, bob_id)["total"] == 1
    assert q["id"] == srs.list_quotes(pub_read_id, bob_id)["quotes"][0]["id"]

    # Arquiva clube -> other_read também entra em somente-leitura
    clubs_service.archive_club(pub_club_id, alice_id)
    with pytest.raises(srs.InvalidOperationError):
        srs.create_topic(other_read_id, alice_id, title="Em clube arquivado", content="Bloqueado")
    ov_other = srs.get_shared_reading_overview(other_read_id, bob_id)
    assert bool(ov_other["read"]["club_is_archived"]) is True
