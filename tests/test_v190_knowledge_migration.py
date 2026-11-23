"""
Suíte de Testes da Fase 1 da Lectoria v1.9.0 — Migração SQLite v12 -> v13 (`database.py`).

Cobre os 20 cenários obrigatórios da Seção 14.1:
1. Migração de banco v12 populado com usuários, livros, fontes, progresso, coleções, clubes, leituras coletivas,
   destaques, notas, desenhos e marcadores.
2. Inicialização limpa em banco novo (v0 -> v13).
3. Idempotência de `init_db` chamado repetidas vezes.
4. Criação de backup pré-migração (`lectoria_pre_migration_v12_to_v13.db.bak`).
5. Não sobrescrita de backup existente (criação com timestamp).
6. Rollback transacional limpo se uma falha for injetada durante `_apply_migration_v13`.
7. Verificação de `PRAGMA integrity_check == 'ok'`.
8. Verificação de `PRAGMA foreign_key_check == []`.
9. Preservação integral de todas as tabelas e linhas da v12.
10. Existência das tabelas `concepts`, `reflections`, `knowledge_links` e `knowledge_fts`.
11. Existência dos índices compostos esperados iniciados por `user_id`.
12. Funcionamento das constraints `CHECK` e `UNIQUE` em `concepts`.
13. Funcionamento das constraints `CHECK` em `reflections`.
14. Funcionamento das constraints `CHECK` e `UNIQUE` em `knowledge_links`.
15. Bloqueio de auto-referência (`source_type = target_type AND source_id = target_id`).
16. Bloqueio de tipos de entidade inválidos em `knowledge_links`.
17. Bloqueio de tipos de relação inválidos em `knowledge_links`.
18. Limpeza e integridade após exclusão de conceito próprio (`trg_concepts_ad_cleanup`).
19. Limpeza e integridade após exclusão de reflexão própria (`trg_reflections_ad_cleanup`).
20. Comportamento correto após exclusão de usuário (`ON DELETE CASCADE` + limpeza de FTS).
"""

from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
import pytest

import config
import database


@pytest.fixture
def v12_populated_db(monkeypatch):
    """
    Constrói um banco SQLite temporário exatamente no Schema v12,
    populado com todas as entidades representativas até a v1.8.0.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_mig_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    covers_dir = tmp_dir / "covers"
    backups_dir = tmp_dir / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    (lib_dir / "ellul_tecnica.pdf").write_bytes(b"%PDF-1.4 dummy pdf content")
    (lib_dir / "heidegger_tecnica.epub").write_bytes(b"PK\x03\x04 dummy epub content")

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", lib_dir)
    monkeypatch.setattr(config, "COVERS_DIR", covers_dir)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)

    conn = database.get_connection(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    for v, fn in (
        (1, database._apply_migration_v1),
        (2, database._apply_migration_v2),
        (3, database._apply_migration_v3),
        (4, database._apply_migration_v4),
        (5, database._apply_migration_v5),
        (6, database._apply_migration_v6),
        (7, database._apply_migration_v7),
        (8, database._apply_migration_v8),
        (9, database._apply_migration_v9),
        (10, database._apply_migration_v10),
        (11, database._apply_migration_v11),
        (12, database._apply_migration_v12),
    ):
        fn(cur)
        cur.execute("INSERT INTO schema_migrations (version) VALUES (?);", (v,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON;")

    # Usuários
    cur.execute(
        """
        INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
        (10, 'helena', 'scrypt$hash_helena', 'reader', 'Helena Costa', 1),
        (20, 'lucas', 'scrypt$hash_lucas', 'reader', 'Lucas Prado', 1);
        """
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
        (10, 'Helena Costa', '#d4a853', 'Filosofia da Técnica'),
        (20, 'Lucas Prado', '#2563eb', 'Análise Matemática');
        """
    )

    # Fonte e Livros
    cur.execute(
        "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Biblioteca Principal', ?, 1);",
        (str(lib_dir),),
    )
    cur.execute(
        """
        INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing) VALUES
        ('book-ellul', 1, 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul_tecnica.pdf', 'ellul_tecnica.pdf', 1024, 'pdf', 320, 0),
        ('book-heidegger', 1, 'A Questão da Técnica', 'Martin Heidegger', 'heidegger_tecnica.epub', 'heidegger_tecnica.epub', 2048, 'epub', 90, 0);
        """
    )

    # Progresso e Coleções
    cur.execute(
        """
        INSERT INTO reading_progress (book_id, user_id, current_page, percentage)
        VALUES ('book-ellul', 10, 42, 13.1);
        """
    )
    cur.execute(
        """
        INSERT INTO collections (id, name, description, user_id)
        VALUES (1, 'Filosofia Contemporânea', 'Obras de base', 10);
        """
    )
    cur.execute(
        """
        INSERT INTO collection_books (collection_id, book_id)
        VALUES (1, 'book-ellul');
        """
    )

    # Clube e Leitura Coletiva (v1.7.0)
    cur.execute(
        """
        INSERT INTO reading_clubs (id, name, description, owner_id, visibility, is_archived)
        VALUES ('club-1', 'Clube de Filosofia', 'Debate mensal', 10, 'public', 0);
        """
    )
    cur.execute(
        """
        INSERT INTO reading_club_members (club_id, user_id, role) VALUES
        ('club-1', 10, 'owner'),
        ('club-1', 20, 'member');
        """
    )
    cur.execute(
        """
        INSERT INTO collective_reads (id, club_id, book_id, title, description, status, created_by)
        VALUES ('read-1', 'club-1', 'book-ellul', 'Leitura Ellul', 'Capítulos 1 a 3', 'in_progress', 10);
        """
    )

    # Destaques, Notas, Desenhos e Marcadores (v1.8.0)
    cur.execute(
        """
        INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id, visibility, chapter_label)
        VALUES ('hl-ellul-42', 'book-ellul', 42, '#fef08a', '[{"x":0.1,"y":0.2,"w":0.5,"h":0.03}]',
                'A técnica tornou-se um meio autônomo', 'Capítulo 2', 10, 'private', 'Cap. 2');
        """
    )
    cur.execute(
        """
        INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id, visibility, chapter_label)
        VALUES ('note-ellul-42', 'book-ellul', 42, 'hl-ellul-42',
                'Ellul analisa a racionalização técnica moderna', '#fef08a', 10, 'private', 'Cap. 2');
        """
    )
    cur.execute(
        """
        INSERT INTO page_drawings (id, book_id, page, strokes_json, user_id, visibility)
        VALUES ('draw-ellul-42', 'book-ellul', 42, '[{"color":"#3d7cf4","width":3,"points":[{"x":0.1,"y":0.1},{"x":0.2,"y":0.2}]}]', 10, 'private');
        """
    )
    cur.execute(
        """
        INSERT INTO bookmarks (id, book_id, user_id, page, label)
        VALUES ('bm-ellul-42', 'book-ellul', 10, 42, 'Definição de autonomia técnica');
        """
    )

    conn.commit()
    conn.close()

    yield {
        "tmp_dir": tmp_dir,
        "db_path": db_path,
        "helena_id": 10,
        "lucas_id": 20,
    }

    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_migration_v12_to_v13_populated_backup_and_data_preservation(v12_populated_db):
    """
    Cenários 1, 3, 4, 7, 8, 9, 10, 11:
    - Migra um banco v12 populado para v13.
    - Cria backup pré-migração `lectoria_pre_migration_v12_to_v13.db.bak`.
    - Valida idempotência de `init_db`.
    - Valida `PRAGMA integrity_check` e `PRAGMA foreign_key_check`.
    - Preserva 100% dos registros da v12 e popula inicialmente `knowledge_fts` com destaques e notas existentes.
    """
    db_path = v12_populated_db["db_path"]
    tmp_dir = v12_populated_db["tmp_dir"]

    database.init_db(db_path)
    database.init_db(db_path)  # Idempotência (Cenário 3)

    bak_path = tmp_dir / "lectoria_pre_migration_v12_to_v13.db.bak"
    assert bak_path.exists(), "Backup pré-migração v12 -> v13 deve existir."

    bak_conn = sqlite3.connect(str(bak_path))
    try:
        assert bak_conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert bak_conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 12
    finally:
        bak_conn.close()

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 13
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert cur.execute("PRAGMA foreign_key_check;").fetchall() == []

        # Tabelas novas criadas (Cenário 10)
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        for required_table in ("concepts", "reflections", "knowledge_links", "knowledge_fts"):
            assert required_table in tables, f"Tabela '{required_table}' deve existir no schema v13."

        # Índices compostos iniciados por user_id (Cenário 11)
        indexes = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()}
        expected_indexes = {
            "uq_concepts_user_norm_name",
            "idx_concepts_user_updated",
            "idx_concepts_user_created",
            "idx_reflections_user_updated",
            "idx_reflections_user_created",
            "idx_reflections_user_book",
            "uq_knowledge_links_tuple",
            "idx_klinks_user_source",
            "idx_klinks_user_target",
            "idx_klinks_user_rel",
        }
        assert expected_indexes.issubset(indexes)

        # Preservação integral dos dados da v12 (Cenário 9 — usuário padrão id=1 + Helena id=10 + Lucas id=20)
        assert cur.execute("SELECT COUNT(*) FROM users WHERE id IN (10, 20);").fetchone()[0] == 2
        assert cur.execute("SELECT COUNT(*) FROM books;").fetchone()[0] == 2
        assert cur.execute("SELECT COUNT(*) FROM reading_progress;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collections;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM reading_clubs;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM collective_reads;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM highlights WHERE id = 'hl-ellul-42';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM notes WHERE id = 'note-ellul-42';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM page_drawings WHERE id = 'draw-ellul-42';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM bookmarks WHERE id = 'bm-ellul-42';").fetchone()[0] == 1

        # População inicial de knowledge_fts com anotações legadas da v12
        fts_hits = cur.execute(
            "SELECT entity_type, entity_id FROM knowledge_fts WHERE knowledge_fts MATCH '\"tecnica\"*';"
        ).fetchall()
        fts_ids = {(r["entity_type"], r["entity_id"]) for r in fts_hits}
        assert ("highlight", "hl-ellul-42") in fts_ids
        assert ("note", "note-ellul-42") in fts_ids
    finally:
        conn.close()


def test_clean_empty_db_initializes_directly_to_v13(monkeypatch):
    """Cenário 2: Inicialização limpa em banco novo (v0 -> v13)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v190_clean_"))
    try:
        db_path = tmp_dir / "clean.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        monkeypatch.setattr(config, "LIBRARY_DIR", tmp_dir)

        database.init_db(db_path)

        conn = database.get_connection(db_path)
        try:
            cur = conn.cursor()
            assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 13
            assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
            assert cur.execute("PRAGMA foreign_key_check;").fetchall() == []
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_migration_v13_does_not_overwrite_existing_backup(v12_populated_db):
    """Cenário 5: Se já existir um arquivo de backup padrão, cria um novo com timestamp sem sobrescrever."""
    db_path = v12_populated_db["db_path"]
    tmp_dir = v12_populated_db["tmp_dir"]

    existing_bak = tmp_dir / "lectoria_pre_migration_v12_to_v13.db.bak"
    sentinel = b"SENTINEL_BACKUP_DO_NOT_OVERWRITE"
    existing_bak.write_bytes(sentinel)

    database.init_db(db_path)

    assert existing_bak.read_bytes() == sentinel
    timestamped_baks = list(tmp_dir.glob("lectoria_pre_migration_v12_to_v13_*.db.bak"))
    assert len(timestamped_baks) == 1


def test_migration_v13_injected_failure_rolls_back_to_v12(v12_populated_db, monkeypatch):
    """Cenário 6: Falha injetada no meio de `_apply_migration_v13` reverte o banco de forma limpa para v12."""
    db_path = v12_populated_db["db_path"]

    def failing_v13(cursor):
        cursor.execute("CREATE TABLE concepts (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL);")
        raise sqlite3.OperationalError("Falha simulada na migração v13")

    monkeypatch.setattr(database, "_apply_migration_v13", failing_v13)

    with pytest.raises(sqlite3.OperationalError, match="Falha simulada na migração v13"):
        database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 12
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        assert "concepts" not in tables
        assert "reflections" not in tables
        assert "knowledge_links" not in tables
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_schema_v13_check_and_unique_constraints_and_triggers(v12_populated_db):
    """
    Cenários 12, 13, 14, 15, 16, 17, 18, 19, 20:
    - Constraints CHECK e UNIQUE em `concepts`, `reflections` e `knowledge_links`.
    - Bloqueio de auto-referência, tipos de entidade inválidos e tipos de relação inválidos.
    - Triggers de limpeza em exclusão de conceito, reflexão e usuário.
    """
    db_path = v12_populated_db["db_path"]
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()

        # Cenário 12: Constraints em concepts (nome vazio, duplicidade de normalized_name por usuário)
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO concepts (id, user_id, name, normalized_name) VALUES ('c-bad', 10, '   ', '   ');"
            )

        cur.execute(
            "INSERT INTO concepts (id, user_id, name, normalized_name, description) VALUES ('c-1', 10, 'Técnica', 'técnica', 'Definição 1');"
        )
        # Mesmo normalized_name para o mesmo usuário deve falhar
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO concepts (id, user_id, name, normalized_name) VALUES ('c-dup', 10, 'TÉCNICA', 'técnica');"
            )
        # Mesmo normalized_name para OUTRO usuário é permitido
        cur.execute(
            "INSERT INTO concepts (id, user_id, name, normalized_name) VALUES ('c-lucas', 20, 'Técnica', 'técnica');"
        )

        # Cenário 13: Constraints em reflections (título vazio, is_pinned inválido)
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO reflections (id, user_id, title, body_markdown) VALUES ('r-bad', 10, '  ', 'Texto');"
            )
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO reflections (id, user_id, title, is_pinned) VALUES ('r-bad2', 10, 'Título', 5);"
            )
        cur.execute(
            "INSERT INTO reflections (id, user_id, title, body_markdown, primary_book_id) VALUES ('r-1', 10, 'Ensaio sobre Ellul', 'Corpo em Markdown', 'book-ellul');"
        )

        # Cenários 14, 15, 16, 17: Constraints em knowledge_links
        # Auto-referência (Cenário 15)
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type)
                VALUES ('l-self', 10, 'concept', 'c-1', 'concept', 'c-1', 'relates_to');
                """
            )
        # Tipo de entidade inválido (Cenário 16)
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type)
                VALUES ('l-bad-ent', 10, 'invalid_type', 'c-1', 'book', 'book-ellul', 'relates_to');
                """
            )
        # Tipo de relação inválido (Cenário 17)
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type)
                VALUES ('l-bad-rel', 10, 'concept', 'c-1', 'book', 'book-ellul', 'invented_rel');
                """
            )

        # Inserção válida e bloqueio de duplicata exata (Cenário 14)
        cur.execute(
            """
            INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale)
            VALUES ('l-1', 10, 'reflection', 'r-1', 'concept', 'c-1', 'grounds', 'Justificativa autoral');
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                """
                INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type)
                VALUES ('l-dup', 10, 'reflection', 'r-1', 'concept', 'c-1', 'grounds');
                """
            )
        conn.commit()

        # Cenário 18: Exclusão de conceito próprio limpa seus links e sua entrada em knowledge_fts
        assert cur.execute("SELECT COUNT(*) FROM knowledge_links WHERE id = 'l-1';").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM knowledge_fts WHERE entity_type = 'concept' AND entity_id = 'c-1';").fetchone()[0] == 1
        cur.execute("DELETE FROM concepts WHERE id = 'c-1';")
        conn.commit()
        assert cur.execute("SELECT COUNT(*) FROM knowledge_links WHERE id = 'l-1';").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM knowledge_fts WHERE entity_type = 'concept' AND entity_id = 'c-1';").fetchone()[0] == 0

        # Cenário 19: Exclusão de reflexão própria limpa seus links e sua entrada em knowledge_fts
        cur.execute(
            """
            INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type)
            VALUES ('l-ref-book', 10, 'reflection', 'r-1', 'book', 'book-ellul', 'references');
            """
        )
        conn.commit()
        cur.execute("DELETE FROM reflections WHERE id = 'r-1';")
        conn.commit()
        assert cur.execute("SELECT COUNT(*) FROM knowledge_links WHERE id = 'l-ref-book';").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM knowledge_fts WHERE entity_type = 'reflection' AND entity_id = 'r-1';").fetchone()[0] == 0

        # Cenário 20: Exclusão de usuário remove conceitos, reflexões, links e entradas FTS daquele usuário
        assert cur.execute("SELECT COUNT(*) FROM concepts WHERE user_id = 20;").fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM knowledge_fts WHERE user_id = '20';").fetchone()[0] == 1
        cur.execute("DELETE FROM users WHERE id = 20;")
        conn.commit()
        assert cur.execute("SELECT COUNT(*) FROM concepts WHERE user_id = 20;").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM knowledge_fts WHERE user_id = '20';").fetchone()[0] == 0
    finally:
        conn.close()
