"""
Testes da Fase 1 da Lectoria v1.7.0 — Persistência e Migração SQLite v10 -> v11:
- Migração de banco v10 populado para v11
- Criação e preservação de backup pré-migração (nunca sobrescrevendo cópias existentes)
- Rollback transacional limpo em caso de falha injetada
- Preservação integral de clubes, membros, convites e leituras coletivas existentes
- Idempotência da inicialização
- Integridade de chaves estrangeiras simples e compostas
- Bloqueio de respostas cruzadas (cross-topic parent_post_id) via triggers
- Bloqueio de citações com livro divergente da leitura coletiva (cross-book quotes)
- Bloqueio de auto-referenciamento de respostas (parent_post_id == id)
- Preservação de histórico com arquivamento, soft delete e desassociação segura no delete de usuários (ON DELETE SET NULL)
- Validação de constraints de domínio (páginas, spoilers, status)
- Verificação de PRAGMA integrity_check e PRAGMA foreign_key_check
"""

import sqlite3
import tempfile
import shutil
from pathlib import Path
import pytest

import database


@pytest.fixture
def v10_database():
    """Cria um banco de dados de teste temporário no Schema v10 populado com dados de clubes e leituras coletivas."""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "lectoria.db"

    conn = database.get_connection(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    database._apply_migration_v1(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (1);")
    database._apply_migration_v2(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (2);")
    database._apply_migration_v3(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (3);")
    database._apply_migration_v4(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (4);")
    database._apply_migration_v5(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (5);")
    database._apply_migration_v6(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (6);")
    database._apply_migration_v7(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (7);")
    database._apply_migration_v8(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (8);")
    database._apply_migration_v9(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (9);")
    database._apply_migration_v10(cursor)
    cursor.execute("INSERT INTO schema_migrations (version) VALUES (10);")
    conn.commit()

    cursor.execute(
        "INSERT INTO users (id, username, password_hash, role, display_name) VALUES "
        "(10, 'alice', 'hash_alice', 'reader', 'Alice Silva'), "
        "(20, 'bob', 'hash_bob', 'reader', 'Bob Souza'), "
        "(30, 'carlos', 'hash_carlos', 'reader', 'Carlos Lima');"
    )

    cursor.execute(
        "INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count) VALUES "
        "('book-1', 'Dom Casmurro', 'Machado de Assis', 'dom.pdf', 'dom.pdf', 204800, 'pdf', 256), "
        "('book-2', 'Memórias Póstumas', 'Machado de Assis', 'memorias.epub', 'memorias.epub', 153600, 'epub', 210);"
    )

    cursor.execute(
        "INSERT INTO reading_clubs (id, name, description, owner_id, visibility, is_archived) VALUES "
        "('club-machado', 'Clube Machado de Assis', 'Clube de literatura clássica', 10, 'public', 0), "
        "('club-privado', 'Clube Secreto', 'Apenas convidados', 20, 'private', 0);"
    )

    cursor.execute(
        "INSERT INTO reading_club_members (club_id, user_id, role) VALUES "
        "('club-machado', 10, 'owner'), "
        "('club-machado', 20, 'moderator'), "
        "('club-machado', 30, 'member'), "
        "('club-privado', 20, 'owner');"
    )

    cursor.execute(
        "INSERT INTO reading_club_invites (id, club_id, inviter_id, invitee_id, status) VALUES "
        "('inv-1', 'club-privado', 20, 10, 'pending');"
    )

    cursor.execute(
        "INSERT INTO collective_reads (id, club_id, book_id, title, description, start_date, end_date, status, created_by) VALUES "
        "('read-casmurro', 'club-machado', 'book-1', 'Leitura Coletiva Dom Casmurro', 'Debate dos capítulos 1 a 30', '2026-11-01', '2026-11-30', 'in_progress', 10);"
    )

    cursor.execute(
        "INSERT INTO collective_read_members (read_id, user_id) VALUES "
        "('read-casmurro', 10), "
        "('read-casmurro', 20);"
    )

    conn.commit()
    conn.close()

    yield db_path, tmp_dir

    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_v10_to_v11_migration_and_backup(v10_database):
    """Testa a migração segura de v10 para v11, criação do backup e preservação de dados."""
    db_path, tmp_dir = v10_database

    database.init_db(db_path)

    bak_path = tmp_dir / f"lectoria_pre_migration_v10_to_v{database.SCHEMA_VERSION}.db.bak"
    assert bak_path.exists(), f"Backup pré-migração {bak_path.name} deve existir"

    bak_conn = sqlite3.connect(str(bak_path))
    bak_cur = bak_conn.cursor()
    assert bak_cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    assert bak_cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 10
    bak_conn.close()

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] >= 11

        new_tables = [
            "collective_discussion_topics",
            "collective_discussion_posts",
            "collective_reading_goals",
            "collective_goal_progress",
            "collective_shared_quotes"
        ]
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        for t in new_tables:
            assert t in tables, f"Tabela '{t}' deve existir no schema v11"

        assert cur.execute("SELECT COUNT(*) FROM reading_clubs;").fetchone()[0] == 2
        assert cur.execute("SELECT COUNT(*) FROM reading_club_members;").fetchone()[0] == 4
        assert cur.execute("SELECT COUNT(*) FROM reading_club_invites;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_reads;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_read_members;").fetchone()[0] == 2

        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        fk_violations = cur.execute("PRAGMA foreign_key_check;").fetchall()
        assert len(fk_violations) == 0, f"Nenhuma violação de chave estrangeira deve ocorrer: {fk_violations}"
    finally:
        conn.close()


def test_backup_never_overwrites_existing_backup(v10_database):
    """Garante que se já existir um arquivo de backup pré-migração, uma nova cópia com timestamp é criada sem sobrescrever a anterior."""
    db_path, tmp_dir = v10_database

    existing_bak = tmp_dir / f"lectoria_pre_migration_v10_to_v{database.SCHEMA_VERSION}.db.bak"
    sentinel_bytes = b"ORIGINAL_BACKUP_DO_NOT_OVERWRITE"
    existing_bak.write_bytes(sentinel_bytes)

    database.init_db(db_path)

    assert existing_bak.read_bytes() == sentinel_bytes

    baks = list(tmp_dir.glob(f"lectoria_pre_migration_v10_to_v{database.SCHEMA_VERSION}_*.db.bak"))
    assert len(baks) == 1, f"Esperado 1 backup com timestamp, encontrado: {baks}"

    bak_conn = sqlite3.connect(str(baks[0]))
    try:
        assert bak_conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert bak_conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 10
    finally:
        bak_conn.close()


def test_migration_v11_failure_triggers_rollback(v10_database, monkeypatch):
    """Injeta uma falha no meio da migração v11 e garante rollback completo, integridade do banco e schema_migrations preservado em v10."""
    db_path, tmp_dir = v10_database

    def failing_migration_v11(cursor):
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS collective_discussion_topics (id TEXT PRIMARY KEY, read_id TEXT NOT NULL);"
        )
        raise sqlite3.OperationalError("Falha simulada durante a migração v11")

    monkeypatch.setattr(database, "_apply_migration_v11", failing_migration_v11)

    with pytest.raises(sqlite3.OperationalError, match="Falha simulada"):
        database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 10

        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        assert "collective_discussion_topics" not in tables

        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert cur.execute("SELECT COUNT(*) FROM reading_clubs;").fetchone()[0] == 2
        assert cur.execute("SELECT COUNT(*) FROM collective_reads;").fetchone()[0] == 1
    finally:
        conn.close()


def test_idempotent_initialization(v10_database):
    """Garante que rodar init_db repetidamente é idempotente e seguro."""
    db_path, tmp_dir = v10_database

    database.init_db(db_path)
    database.init_db(db_path)
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] >= 11
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_discussion_topics_and_posts_integrity(v10_database):
    """Testa criação de tópicos, respostas, integridade de parent_post_id e restrições."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO collective_discussion_topics (
                id, read_id, user_id, title, content, chapter_ref, page_start, page_end, is_spoiler
            ) VALUES ('top-1', 'read-casmurro', 10, 'A traição de Capitu', 'Discussão sobre o capítulo 10', 'Capítulo 10', 45, 52, 1);
            """
        )
        conn.commit()

        cur.execute(
            """
            INSERT INTO collective_discussion_posts (
                id, topic_id, user_id, parent_post_id, content, is_spoiler
            ) VALUES ('post-1', 'top-1', 20, NULL, 'Na minha opinião, não houve traição.', 0);
            """
        )
        conn.commit()

        cur.execute(
            """
            INSERT INTO collective_discussion_posts (
                id, topic_id, user_id, parent_post_id, content, is_spoiler
            ) VALUES ('post-2', 'top-1', 10, 'post-1', 'Discordo, o olhar de ressaca indica o contrário.', 1);
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO collective_discussion_posts (
                    id, topic_id, user_id, parent_post_id, content
                ) VALUES ('post-loop', 'top-1', 10, 'post-loop', 'Post apontando para si mesmo');
                """
            )
            conn.commit()
        conn.rollback()

        cur.execute(
            """
            INSERT INTO collective_discussion_topics (
                id, read_id, user_id, title, content
            ) VALUES ('top-2', 'read-casmurro', 20, 'Personagem Escobar', 'Análise');
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO collective_discussion_posts (
                    id, topic_id, user_id, parent_post_id, content
                ) VALUES ('post-cross', 'top-2', 10, 'post-1', 'Tentativa de cruzar parent de top-1 para top-2');
                """
            )
            conn.commit()
        conn.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO collective_discussion_topics (
                    id, read_id, user_id, title, content, page_start, page_end
                ) VALUES ('top-bad-pages', 'read-casmurro', 10, 'Páginas inválidas', 'Erro', 100, 50);
                """
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()


def test_shared_quotes_cross_book_blocking(v10_database):
    """Testa se citações vinculadas a livro divergente da leitura coletiva são barradas pela composite foreign key."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO collective_shared_quotes (
                id, read_id, book_id, user_id, quote_text, comment_text, page_number
            ) VALUES ('quote-1', 'read-casmurro', 'book-1', 10, 'Capitu, apesar daqueles olhos...', 'Passagem memorável', 45);
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO collective_shared_quotes (
                    id, read_id, book_id, user_id, quote_text
                ) VALUES ('quote-divergent', 'read-casmurro', 'book-2', 10, 'Tentativa de quote com livro divergente');
                """
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()


def test_reading_goals_and_voluntary_progress(v10_database):
    """Testa metas coletivas, ordenação e isolamento de progresso voluntário."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO collective_reading_goals (
                id, read_id, title, description, target_date, chapter_ref, target_page, display_order, created_by
            ) VALUES
            ('goal-1', 'read-casmurro', 'Capítulos 1 a 10', 'Início da leitura', '2026-11-10', 'Capítulo 10', 50, 1, 10),
            ('goal-2', 'read-casmurro', 'Capítulos 11 a 20', 'Desenvolvimento', '2026-11-20', 'Capítulo 20', 100, 2, 10);
            """
        )
        conn.commit()

        cur.execute(
            """
            INSERT INTO collective_goal_progress (goal_id, user_id, status) VALUES
            ('goal-1', 10, 'completed'),
            ('goal-1', 20, 'in_progress'),
            ('goal-2', 10, 'in_progress'),
            ('goal-2', 20, 'pending');
            """
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO collective_goal_progress (goal_id, user_id, status) VALUES ('goal-1', 30, 'invalid_status');"
            )
            conn.commit()
        conn.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO collective_goal_progress (goal_id, user_id, status) VALUES ('goal-1', 10, 'pending');"
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()


def test_user_deletion_history_preservation(v10_database):
    """Garante que a exclusão de um usuário não apaga tópicos, respostas, metas nem citações (preservação via SET NULL)."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO collective_discussion_topics (id, read_id, user_id, title, content)
            VALUES ('top-del', 'read-casmurro', 10, 'Tópico de Alice', 'Conteúdo que deve persistir');
            """
        )
        cur.execute(
            """
            INSERT INTO collective_discussion_posts (id, topic_id, user_id, parent_post_id, content)
            VALUES ('post-del-1', 'top-del', 10, NULL, 'Resposta de Alice');
            """
        )
        cur.execute(
            """
            INSERT INTO collective_discussion_posts (id, topic_id, user_id, parent_post_id, content)
            VALUES ('post-del-2', 'top-del', 20, 'post-del-1', 'Resposta de Bob para Alice');
            """
        )
        cur.execute(
            """
            INSERT INTO collective_reading_goals (id, read_id, title, created_by)
            VALUES ('goal-del', 'read-casmurro', 'Meta de Alice', 10);
            """
        )
        cur.execute(
            """
            INSERT INTO collective_goal_progress (goal_id, user_id, status)
            VALUES ('goal-del', 10, 'completed'), ('goal-del', 20, 'in_progress');
            """
        )
        cur.execute(
            """
            INSERT INTO collective_shared_quotes (id, read_id, book_id, user_id, quote_text)
            VALUES ('quote-del', 'read-casmurro', 'book-1', 10, 'Citação de Alice');
            """
        )
        conn.commit()

        cur.execute("UPDATE collective_reads SET created_by = 20 WHERE created_by = 10;")
        cur.execute("UPDATE reading_clubs SET owner_id = 20 WHERE owner_id = 10;")
        cur.execute("DELETE FROM reading_club_members WHERE user_id = 10;")
        cur.execute("DELETE FROM collective_read_members WHERE user_id = 10;")
        cur.execute("DELETE FROM users WHERE id = 10;")
        conn.commit()

        topic = cur.execute("SELECT id, user_id, title FROM collective_discussion_topics WHERE id = 'top-del';").fetchone()
        assert topic is not None
        assert topic["user_id"] is None
        assert topic["title"] == "Tópico de Alice"

        post1 = cur.execute("SELECT id, user_id, content FROM collective_discussion_posts WHERE id = 'post-del-1';").fetchone()
        assert post1 is not None
        assert post1["user_id"] is None

        post2 = cur.execute("SELECT id, user_id, parent_post_id FROM collective_discussion_posts WHERE id = 'post-del-2';").fetchone()
        assert post2 is not None
        assert post2["user_id"] == 20
        assert post2["parent_post_id"] == "post-del-1"

        goal = cur.execute("SELECT id, created_by, title FROM collective_reading_goals WHERE id = 'goal-del';").fetchone()
        assert goal is not None
        assert goal["created_by"] is None

        progress = cur.execute("SELECT user_id, status FROM collective_goal_progress WHERE goal_id = 'goal-del';").fetchall()
        assert len(progress) == 1
        assert progress[0]["user_id"] == 20

        quote = cur.execute("SELECT id, user_id, quote_text FROM collective_shared_quotes WHERE id = 'quote-del';").fetchone()
        assert quote is not None
        assert quote["user_id"] is None

        cur.execute("DELETE FROM collective_discussion_posts WHERE id = 'post-del-1';")
        conn.commit()
        post2_after = cur.execute("SELECT id, parent_post_id FROM collective_discussion_posts WHERE id = 'post-del-2';").fetchone()
        assert post2_after is not None
        assert post2_after["parent_post_id"] is None
    finally:
        conn.close()


def test_missing_book_preserves_collective_read_and_quotes(v10_database):
    """Garante que marcar um livro como is_missing=1 preserva a leitura coletiva e citações."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO collective_shared_quotes (id, read_id, book_id, user_id, quote_text)
            VALUES ('quote-miss', 'read-casmurro', 'book-1', 20, 'Texto da citação');
            """
        )
        conn.commit()

        cur.execute("UPDATE books SET is_missing = 1 WHERE id = 'book-1';")
        conn.commit()

        assert cur.execute("SELECT COUNT(*) FROM collective_reads WHERE id = 'read-casmurro';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_shared_quotes WHERE id = 'quote-miss';").fetchone()[0] == 1

        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert len(cur.execute("PRAGMA foreign_key_check;").fetchall()) == 0
    finally:
        conn.close()


def test_update_post_parent_cross_topic_trigger(v10_database):
    """Testa se a trigger de UPDATE impede alterar parent_post_id para apontar para outro tópico."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute("INSERT INTO collective_discussion_topics (id, read_id, user_id, title, content) VALUES ('t1', 'read-casmurro', 20, 'T1', 'C1');")
        cur.execute("INSERT INTO collective_discussion_posts (id, topic_id, user_id, parent_post_id, content) VALUES ('p1', 't1', 20, NULL, 'Root');")

        cur.execute("INSERT INTO collective_discussion_topics (id, read_id, user_id, title, content) VALUES ('t2', 'read-casmurro', 20, 'T2', 'C2');")
        cur.execute("INSERT INTO collective_discussion_posts (id, topic_id, user_id, parent_post_id, content) VALUES ('p2', 't2', 20, NULL, 'Post em T2');")
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cur.execute("UPDATE collective_discussion_posts SET parent_post_id = 'p1' WHERE id = 'p2';")
            conn.commit()
        conn.rollback()
    finally:
        conn.close()


def test_archiving_club_or_cancelling_read_preserves_all_shared_data(v10_database):
    """Confirma que arquivar clube ou concluir/cancelar leitura coletiva não remove tópicos, respostas, metas ou citações."""
    db_path, tmp_dir = v10_database
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        cur.execute("INSERT INTO collective_discussion_topics (id, read_id, user_id, title, content) VALUES ('t-arc', 'read-casmurro', 10, 'Debate', 'Conteúdo');")
        cur.execute("INSERT INTO collective_discussion_posts (id, topic_id, user_id, content) VALUES ('p-arc', 't-arc', 20, 'Resposta');")
        cur.execute("INSERT INTO collective_reading_goals (id, read_id, title, created_by) VALUES ('g-arc', 'read-casmurro', 'Meta 1', 10);")
        cur.execute("INSERT INTO collective_goal_progress (goal_id, user_id, status) VALUES ('g-arc', 10, 'completed');")
        cur.execute("INSERT INTO collective_shared_quotes (id, read_id, book_id, user_id, quote_text) VALUES ('q-arc', 'read-casmurro', 'book-1', 10, 'Citação');")
        conn.commit()

        cur.execute("UPDATE reading_clubs SET is_archived = 1 WHERE id = 'club-machado';")
        cur.execute("UPDATE collective_reads SET status = 'cancelled' WHERE id = 'read-casmurro';")
        conn.commit()

        assert cur.execute("SELECT COUNT(*) FROM collective_discussion_topics WHERE id = 't-arc';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_discussion_posts WHERE id = 'p-arc';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_reading_goals WHERE id = 'g-arc';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_goal_progress WHERE goal_id = 'g-arc';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_shared_quotes WHERE id = 'q-arc';").fetchone()[0] == 1
    finally:
        conn.close()
