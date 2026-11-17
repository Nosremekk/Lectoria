"""
Suíte de Testes da Fase 1 da Lectoria v1.8.0 — Persistência SQLite (v11 -> v12)
e Serviços de Anotações Colaborativas (`annotations_service.py` e `export_service.py`).

Cobre os 18 requisitos obrigatórios da Fase 1:
1. Migração v11 -> v12 (backup pré-migração, rollback em falha injetada, idempotência, FKs).
2. Preservação integral de dados antigos (anotações, desenhos, categorias/taxonomia, clubes).
3. Visibilidade privada por padrão ('private').
4. Publicação explícita ('private' -> 'public').
5. Revogação de visibilidade ('public' -> 'private').
6. Escopo de consulta `mine`.
7. Escopo de consulta `public`.
8. Escopo de consulta `all_visible` (sem duplicações).
9. Isolamento entre usuários e anti-IDOR (autoria pública segura sem vazar credenciais).
10. Acesso a livros autorizados vs. não autorizados (inexistente, ausente, fonte inativa, usuário inativo).
11. Destaques PDF (rects normalizados).
12. Notas PDF (vinculadas e avulsas).
13. Destaques EPUB CFI (sem exigir rects PDF, validação de CFI e capítulo).
14. Notas EPUB CFI (diretas e herdando CFI de destaque vinculado).
15. Desenhos próprios e públicos (`get_page_drawings_bundle`, preservação de geometria e visibilidade).
16. Marcadores pessoais (`bookmarks` PDF e EPUB CFI, não-duplicação, isolamento).
17. Importação e exportação seguras (exclusão de dados de terceiros na exportação, força 'private' na importação, anti-spoofing de user_id/highlight_id).
18. Regressão das funcionalidades da v1.7.0 (Leitura Compartilhada).
"""

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import pytest

import annotations_service as ann
import clubs_service
import config
import database
import export_service
import shared_reading_service as srs
import social_service
import taxonomy_service


@pytest.fixture
def v11_populated_db(monkeypatch):
    """
    Cria um banco SQLite temporário na versão 11 populado com:
    - 3 usuários (Alice, Bob, Carlos)
    - 2 fontes (Fonte 1 ativa, Fonte 2 inativa)
    - 3 livros (PDF ativo, EPUB ativo, PDF em fonte inativa)
    - Classificação taxonômica manual e automática
    - Destaques, notas e desenhos legados da v11
    - Clube de leitura e leitura coletiva da v1.7.0
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="lectoria_v180_p1_"))
    db_path = tmp_dir / "lectoria.db"
    lib_dir = tmp_dir / "books"
    covers_dir = tmp_dir / "covers"
    backups_dir = tmp_dir / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    (lib_dir / "dom_casmurro.pdf").write_bytes(b"%PDF-1.4 dummy pdf content")
    (lib_dir / "meditacoes.epub").write_bytes(b"PK\x03\x04 dummy epub content")
    (lib_dir / "inativo.pdf").write_bytes(b"%PDF-1.4 inactive source pdf")

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
    ):
        fn(cur)
        cur.execute("INSERT INTO schema_migrations (version) VALUES (?);", (v,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON;")

    # Popula usuários e perfis
    cur.execute(
        """
        INSERT INTO users (id, username, password_hash, role, display_name, is_active) VALUES
        (10, 'alice', 'scrypt$secret_hash_alice', 'reader', 'Alice Lemos', 1),
        (20, 'bob', 'scrypt$secret_hash_bob', 'reader', 'Bob Ferreira', 1),
        (30, 'carlos', 'scrypt$secret_hash_carlos', 'reader', 'Carlos Dias', 1),
        (40, 'inativo', 'scrypt$secret_hash_inativo', 'reader', 'Usuário Inativo', 0);
        """
    )
    cur.execute(
        """
        INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
        (10, 'Alice Lemos', '#e11d48', 'Leitora de clássicos'),
        (20, 'Bob Ferreira', '#2563eb', 'Estudante de filosofia'),
        (30, 'Carlos Dias', '#16a34a', 'Leitor assíduo');
        """
    )

    # Fontes
    cur.execute(
        "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Biblioteca Principal', ?, 1);",
        (str(lib_dir),),
    )
    cur.execute(
        "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (2, 'Fonte Desativada', ?, 0);",
        (str(lib_dir / "inativa"),),
    )

    # Livros
    cur.execute(
        """
        INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, format, page_count, is_missing) VALUES
        ('book-pdf-1', 1, 'Dom Casmurro', 'Machado de Assis', 'dom_casmurro.pdf', 'dom_casmurro.pdf', 1024, 'pdf', 200, 0),
        ('book-epub-1', 1, 'Meditações Metafísicas', 'René Descartes', 'meditacoes.epub', 'meditacoes.epub', 2048, 'epub', 120, 0),
        ('book-inactive-src', 2, 'Obra de Fonte Inativa', 'Autor Oculto', 'inativo.pdf', 'inativo.pdf', 512, 'pdf', 50, 0),
        ('book-missing-disk', 1, 'Obra Ausente', 'Autor Perdido', 'ausente.pdf', 'ausente.pdf', 512, 'pdf', 80, 1);
        """
    )

    # Anotações legadas da v11 (sem coluna visibility/epub_cfi)
    cur.execute(
        """
        INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id)
        VALUES ('hl-legacy-alice', 'book-pdf-1', 5, '#fef08a', '[{"x":0.1,"y":0.2,"w":0.4,"h":0.03}]', 'Olhos de ressaca', 'Cap. XXXII', 10);
        """
    )
    cur.execute(
        """
        INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id)
        VALUES ('note-legacy-alice', 'book-pdf-1', 5, 'hl-legacy-alice', 'Nota privada antiga da Alice', '#fef08a', 10);
        """
    )
    cur.execute(
        """
        INSERT INTO page_drawings (id, book_id, page, strokes_json, user_id)
        VALUES ('draw-legacy-alice', 'book-pdf-1', 5, '[{"color":"#3d7cf4","width":4,"points":[{"x":0.1,"y":0.1},{"x":0.2,"y":0.2}]}]', 10);
        """
    )

    # Clube e leitura coletiva da v1.7.0
    cur.execute(
        """
        INSERT INTO reading_clubs (id, name, description, owner_id, visibility, is_archived)
        VALUES ('club-v170', 'Clube Literatura Brasileira', 'Debates mensais', 10, 'public', 0);
        """
    )
    cur.execute(
        """
        INSERT INTO reading_club_members (club_id, user_id, role) VALUES
        ('club-v170', 10, 'owner'),
        ('club-v170', 20, 'member');
        """
    )
    cur.execute(
        """
        INSERT INTO collective_reads (id, club_id, book_id, title, description, status, created_by)
        VALUES ('read-v170', 'club-v170', 'book-pdf-1', 'Leitura Dom Casmurro', 'Debate', 'in_progress', 10);
        """
    )
    cur.execute(
        """
        INSERT INTO collective_read_members (read_id, user_id) VALUES
        ('read-v170', 10),
        ('read-v170', 20);
        """
    )

    conn.commit()
    conn.close()

    # Taxonomia manual no livro 1 antes da migração v12
    taxonomy_service.sync_book_taxonomy(
        "book-pdf-1",
        {"categories": ["Literatura"], "subcategories": [("Literatura", "Romance clássico")], "origin": "Brasil"},
        is_manual=True,
    )

    yield {
        "tmp_dir": tmp_dir,
        "db_path": db_path,
        "alice_id": 10,
        "bob_id": 20,
        "carlos_id": 30,
        "inactive_user_id": 40,
    }

    shutil.rmtree(tmp_dir, ignore_errors=True)


# =========================================================================
# 1 & 2. MIGRAÇÃO v11 -> v12, BACKUP, ROLLBACK, IDEMPOTÊNCIA E PRESERVAÇÃO
# =========================================================================

def test_migration_v11_to_v12_backup_preservation_and_idempotency(v11_populated_db):
    """
    Valida a migração v11 -> v12:
    - Criação de backup pré-migração íntegro na v11 sem sobrescrever existentes.
    - Todas as anotações, destaques e desenhos legados permanecem 100% 'private'.
    - Traços geométricos e classificações taxonômicas dos livros são preservados.
    - Tabela `bookmarks` e novos índices são criados.
    - Execuções subsequentes de `init_db` são idempotentes.
    """
    db_path = v11_populated_db["db_path"]
    tmp_dir = v11_populated_db["tmp_dir"]

    # Executa migração v11 -> v12
    database.init_db(db_path)
    database.init_db(db_path)  # Testa idempotência

    bak_path = tmp_dir / "lectoria_pre_migration_v11_to_v12.db.bak"
    assert bak_path.exists(), "Backup pré-migração v11 -> v12 deve ser criado automaticamente."

    bak_conn = sqlite3.connect(str(bak_path))
    try:
        assert bak_conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert bak_conn.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 11
    finally:
        bak_conn.close()

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 12
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert cur.execute("PRAGMA foreign_key_check;").fetchall() == []

        # Verifica preservação e visibilidade privada padrão dos itens antigos
        hl = dict(cur.execute("SELECT * FROM highlights WHERE id = 'hl-legacy-alice';").fetchone())
        assert hl["visibility"] == "private"
        assert hl["selected_text"] == "Olhos de ressaca"
        assert hl["epub_cfi"] is None
        assert hl["updated_at"] is not None

        nt = dict(cur.execute("SELECT * FROM notes WHERE id = 'note-legacy-alice';").fetchone())
        assert nt["visibility"] == "private"
        assert nt["content"] == "Nota privada antiga da Alice"
        assert nt["epub_cfi"] is None

        dr = dict(cur.execute("SELECT * FROM page_drawings WHERE id = 'draw-legacy-alice';").fetchone())
        assert dr["visibility"] == "private"
        assert json.loads(dr["strokes_json"])[0]["color"] == "#3d7cf4"
        assert dr["created_at"] is not None

        # Verifica tabela bookmarks e índices da v12
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
        assert "bookmarks" in tables

        indexes = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()}
        expected_indexes = {
            "idx_highlights_book_vis_page",
            "idx_highlights_book_user_page",
            "idx_highlights_book_cfi",
            "idx_notes_book_vis_page",
            "idx_notes_book_user_page",
            "idx_notes_book_cfi",
            "idx_drawings_book_vis_page",
            "idx_drawings_book_user_page",
            "idx_bookmarks_book_user_page",
            "uq_bookmarks_book_user_pos",
        }
        assert expected_indexes.issubset(indexes)
    finally:
        conn.close()

    # Verifica que classificação taxonômica do livro permaneceu intacta
    tax = taxonomy_service.get_book_taxonomy("book-pdf-1")
    assert tax["is_manual"] is True
    assert tax["categories"] == ["Literatura"]


def test_migration_v12_injected_failure_rolls_back_cleanly(v11_populated_db, monkeypatch):
    """Garante que se ocorrer erro durante `_apply_migration_v12`, o banco faz rollback para a v11."""
    db_path = v11_populated_db["db_path"]

    def broken_v12(cursor):
        cursor.execute("ALTER TABLE highlights ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private';")
        raise sqlite3.OperationalError("Erro simulado no meio da migração v12")

    monkeypatch.setattr(database, "_apply_migration_v12", broken_v12)

    with pytest.raises(sqlite3.OperationalError, match="Erro simulado"):
        database.init_db(db_path)

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0] == 11
        hl_cols = {r["name"] for r in cur.execute("PRAGMA table_info(highlights);").fetchall()}
        assert "visibility" not in hl_cols
        assert cur.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        conn.close()


# =========================================================================
# 3, 4, 5, 6, 7, 8 & 9. VISIBILIDADE, ESCOPOS E ISOLAMENTO ENTRE USUÁRIOS
# =========================================================================

def test_visibility_lifecycle_scopes_and_user_isolation(v11_populated_db):
    """
    Testa:
    - Criação privada por padrão (highlights, notes, drawings).
    - Publicação explícita e revogação ('public' -> 'private').
    - Escopos `mine`, `public` e `all_visible` sem duplicações.
    - Isolamento anti-IDOR e metadados públicos seguros de autoria (`author_display_name`, `author_avatar_color`).
    """
    db_path = v11_populated_db["db_path"]
    database.init_db(db_path)

    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]
    carlos = v11_populated_db["carlos_id"]
    book_id = "book-pdf-1"

    # 1. Novas anotações nascem privadas por padrão
    h_alice_priv = ann.create_highlight(
        book_id=book_id,
        page=10,
        color="#a7f3d0",
        rects=[{"x": 0.1, "y": 0.3, "w": 0.5, "h": 0.02}],
        selected_text="Destaque privado da Alice",
        user_id=alice,
    )
    assert h_alice_priv["visibility"] == "private"
    assert h_alice_priv["is_own"] is True
    assert h_alice_priv["author_display_name"] == "Alice Lemos"
    assert h_alice_priv["author_avatar_color"] == "#e11d48"
    assert "password_hash" not in h_alice_priv

    h_alice_pub = ann.create_highlight(
        book_id=book_id,
        page=12,
        color="#bae6fd",
        rects=[{"x": 0.2, "y": 0.4, "w": 0.6, "h": 0.02}],
        selected_text="Destaque da Alice que será tornado público",
        user_id=alice,
    )
    assert h_alice_pub["visibility"] == "private"

    # Bob cria um destaque e o publica explicitamente
    h_bob_pub = ann.create_highlight(
        book_id=book_id,
        page=15,
        color="#fbcfe8",
        rects=[{"x": 0.1, "y": 0.5, "w": 0.4, "h": 0.03}],
        selected_text="Destaque público do Bob",
        user_id=bob,
    )
    h_bob_pub = ann.set_highlight_visibility(h_bob_pub["id"], "public", user_id=bob)
    assert h_bob_pub["visibility"] == "public"

    # Alice publica explicitamente seu segundo destaque
    h_alice_pub = ann.set_highlight_visibility(h_alice_pub["id"], "public", user_id=alice)
    assert h_alice_pub["visibility"] == "public"

    # 2. Validação dos escopos `mine`, `public` e `all_visible` para Alice
    # Alice tem: hl-legacy-alice (private, p.5), h_alice_priv (private, p.10), h_alice_pub (public, p.12)
    # Bob tem: h_bob_pub (public, p.15)
    alice_mine = ann.get_highlights(book_id, user_id=alice, scope="mine")
    assert [h["id"] for h in alice_mine] == ["hl-legacy-alice", h_alice_priv["id"], h_alice_pub["id"]]
    assert all(h["is_own"] is True for h in alice_mine)

    alice_public = ann.get_highlights(book_id, user_id=alice, scope="public")
    assert [h["id"] for h in alice_public] == [h_alice_pub["id"], h_bob_pub["id"]]

    alice_all_visible = ann.get_highlights(book_id, user_id=alice, scope="all_visible")
    assert [h["id"] for h in alice_all_visible] == [
        "hl-legacy-alice",
        h_alice_priv["id"],
        h_alice_pub["id"],
        h_bob_pub["id"],
    ]
    # Confirma ausência de duplicatas em all_visible mesmo para h_alice_pub (que é próprio E público)
    assert len(alice_all_visible) == len({h["id"] for h in alice_all_visible})

    # Verifica identificação de autoria vista por Carlos (terceiro leitor)
    carlos_visible = ann.get_highlights(book_id, user_id=carlos, scope="all_visible")
    assert [h["id"] for h in carlos_visible] == [h_alice_pub["id"], h_bob_pub["id"]]
    assert carlos_visible[0]["author_display_name"] == "Alice Lemos"
    assert carlos_visible[0]["is_own"] is False
    assert carlos_visible[1]["author_display_name"] == "Bob Ferreira"
    assert carlos_visible[1]["is_own"] is False

    # 3. Anti-IDOR e proibição de mutação por terceiros
    # Bob não pode consultar destaque privado da Alice por ID direto
    with pytest.raises(ann.AnnotationNotFoundError):
        ann.get_highlight_by_id(h_alice_priv["id"], user_id=bob)

    # Bob não pode alterar visibilidade de destaque privado da Alice (retorna NotFound para anti-enumeração)
    with pytest.raises(ann.AnnotationNotFoundError):
        ann.set_highlight_visibility(h_alice_priv["id"], "public", user_id=bob)

    # Bob não pode alterar visibilidade de destaque público da Alice (retorna PermissionError)
    with pytest.raises(ann.AnnotationPermissionError):
        ann.set_highlight_visibility(h_alice_pub["id"], "private", user_id=bob)

    # Bob não pode excluir destaque público da Alice
    assert ann.delete_highlight(h_alice_pub["id"], user_id=bob) is False

    # 4. Revogação de visibilidade pela própria autora ('public' -> 'private')
    revoked = ann.set_highlight_visibility(h_alice_pub["id"], "private", user_id=alice)
    assert revoked["visibility"] == "private"
    carlos_after_revoke = ann.get_highlights(book_id, user_id=carlos, scope="all_visible")
    assert [h["id"] for h in carlos_after_revoke] == [h_bob_pub["id"]]


def test_notes_visibility_scopes_and_anti_idor(v11_populated_db):
    """Testa criação, publicação, revogação, escopos e proteção anti-IDOR em notas textuais."""
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]
    book_id = "book-pdf-1"

    note_alice = ann.create_note(
        book_id=book_id,
        page=20,
        content="Reflexão da Alice sobre o narrador Bento Santiago.",
        user_id=alice,
    )
    assert note_alice["visibility"] == "private"

    # Bob não vê a nota privada da Alice
    assert ann.get_notes(book_id, user_id=bob, scope="all_visible") == []
    with pytest.raises(ann.AnnotationNotFoundError):
        ann.get_note_by_id(note_alice["id"], user_id=bob, allow_public=True)

    # Alice torna a nota pública
    pub_note = ann.set_note_visibility(note_alice["id"], "public", user_id=alice)
    assert pub_note["visibility"] == "public"

    # Agora Bob vê a nota em `public` e `all_visible`, mas não em `mine`
    assert ann.get_notes(book_id, user_id=bob, scope="mine") == []
    bob_pub_notes = ann.get_notes(book_id, user_id=bob, scope="public")
    assert len(bob_pub_notes) == 1
    assert bob_pub_notes[0]["id"] == note_alice["id"]
    assert bob_pub_notes[0]["author_display_name"] == "Alice Lemos"
    assert bob_pub_notes[0]["is_own"] is False

    # Bob não pode editar, despublicar nem excluir a nota pública da Alice
    with pytest.raises(ann.AnnotationNotFoundError):
        ann.update_note(note_alice["id"], content="Tentativa de adulteração", user_id=bob)
    with pytest.raises(ann.AnnotationPermissionError):
        ann.set_note_visibility(note_alice["id"], "private", user_id=bob)
    assert ann.delete_note(note_alice["id"], user_id=bob) is False

    # Alice revoga visibilidade pública
    ann.set_note_visibility(note_alice["id"], "private", user_id=alice)
    assert ann.get_notes(book_id, user_id=bob, scope="all_visible") == []


# =========================================================================
# 10. ACESSO A LIVROS AUTORIZADOS E NÃO AUTORIZADOS
# =========================================================================

def test_book_access_authorization_enforcement(v11_populated_db):
    """
    Garante que usuários não possam consultar ou criar anotações em:
    - Livros inexistentes.
    - Livros ausentes do disco (`is_missing = 1`).
    - Livros pertencentes a fontes desativadas (`library_sources.is_active = 0`).
    - Contas de usuário desativadas (`users.is_active = 0`).
    """
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    inactive_user = v11_populated_db["inactive_user_id"]

    # Livro inexistente
    with pytest.raises(ValueError, match="não encontrado"):
        ann.get_highlights("livro-inexistente", user_id=alice, scope="all_visible")

    # Livro ausente do disco
    with pytest.raises(ValueError, match="não está disponível"):
        ann.get_highlights("book-missing-disk", user_id=alice, scope="all_visible")

    # Livro em fonte desativada
    with pytest.raises(ann.AnnotationPermissionError, match="desativada"):
        ann.get_highlights("book-inactive-src", user_id=alice, scope="all_visible")

    with pytest.raises(ann.AnnotationPermissionError, match="desativada"):
        ann.create_note("book-inactive-src", page=1, content="Nota bloqueada", user_id=alice)

    # Usuário desativado
    with pytest.raises(ann.AnnotationPermissionError, match="desativada"):
        ann.get_highlights("book-pdf-1", user_id=inactive_user, scope="all_visible")


# =========================================================================
# 13 & 14. DESTAQUES E NOTAS EPUB CFI
# =========================================================================

def test_epub_cfi_highlights_and_notes_persistence_and_validation(v11_populated_db):
    """
    Valida:
    - Criação de destaques EPUB vinculados a `epub_cfi` sem exigir coordenadas `rects` de PDF.
    - Notas vinculadas a `epub_cfi` (diretamente ou herdadas do destaque EPUB).
    - Rótulo opcional de capítulo (`chapter_label`).
    - Filtro por `epub_cfi` e validação contra CFIs malformados ou excessivamente longos.
    """
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]
    epub_id = "book-epub-1"
    sample_cfi = "epubcfi(/6/4[meditacao1]!/4/2/1:0,/4/2/1:58)"

    # 1. Cria destaque EPUB sem rects PDF
    hl_epub = ann.create_highlight(
        book_id=epub_id,
        page=12,
        color="#fef08a",
        rects=None,
        selected_text="Penso, logo existo (Cogito, ergo sum).",
        epub_cfi=sample_cfi,
        chapter_label="Primeira Meditação",
        user_id=alice,
    )
    assert hl_epub["epub_cfi"] == sample_cfi
    assert hl_epub["chapter_label"] == "Primeira Meditação"
    assert hl_epub["rects"] == []
    assert hl_epub["visibility"] == "private"

    # 2. Cria nota vinculada ao destaque EPUB (deve herdar epub_cfi e chapter_label automaticamente)
    note_epub = ann.create_note(
        book_id=epub_id,
        page=12,
        content="Fundamento indubitável da epistemologia cartesiana.",
        highlight_id=hl_epub["id"],
        user_id=alice,
    )
    assert note_epub["epub_cfi"] == sample_cfi
    assert note_epub["chapter_label"] == "Primeira Meditação"
    assert note_epub["highlight_text"] == "Penso, logo existo (Cogito, ergo sum)."

    # 3. Publica destaque e nota EPUB e consulta por CFI como Bob
    ann.set_highlight_visibility(hl_epub["id"], "public", user_id=alice)
    ann.set_note_visibility(note_epub["id"], "public", user_id=alice)

    found_hl = ann.get_highlights(epub_id, user_id=bob, scope="public", epub_cfi=sample_cfi)
    assert len(found_hl) == 1
    assert found_hl[0]["id"] == hl_epub["id"]

    found_notes = ann.get_notes(epub_id, user_id=bob, scope="public", epub_cfi=sample_cfi)
    assert len(found_notes) == 1
    assert found_notes[0]["id"] == note_epub["id"]

    # 4. Validação de CFIs inválidos
    with pytest.raises(ValueError, match="Formato de EPUB CFI inválido"):
        ann.create_highlight(
            book_id=epub_id,
            page=1,
            selected_text="Trecho",
            epub_cfi="cfi_invalido_sem_prefixo",
            user_id=alice,
        )

    with pytest.raises(ValueError, match="Formato de EPUB CFI inválido"):
        ann.create_note(
            book_id=epub_id,
            page=1,
            content="Nota",
            epub_cfi="epubcfi(<script>alert(1)</script>)",
            user_id=alice,
        )

    with pytest.raises(ValueError, match="excede o tamanho máximo"):
        ann.create_note(
            book_id=epub_id,
            page=1,
            content="Nota",
            epub_cfi="epubcfi(" + ("/4" * 600) + ")",
            user_id=alice,
        )


# =========================================================================
# 15. DESENHOS PRÓPRIOS E PÚBLICOS
# =========================================================================

def test_shared_drawings_own_and_public_bundle_and_isolation(v11_populated_db):
    """
    Valida:
    - Desenhos nascem privados por padrão.
    - Alteração explícita de visibilidade do próprio desenho ('private' <-> 'public').
    - Atualização de traços preserva a visibilidade atual do desenho.
    - `get_page_drawings_bundle` separa `own_drawing` de `public_drawings` de outros leitores.
    - Outros usuários não podem alterar visibilidade nem apagar desenhos alheios.
    """
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]
    carlos = v11_populated_db["carlos_id"]
    book_id = "book-pdf-1"
    page_num = 30

    strokes_alice = [{"color": "#3d7cf4", "width": 4, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.3, "y": 0.3}]}]
    strokes_bob = [{"color": "#f87171", "width": 8, "points": [{"x": 0.5, "y": 0.5}, {"x": 0.7, "y": 0.7}]}]

    # Alice e Bob desenham na página 30 (ambos privados por padrão)
    res_a = ann.save_page_drawing(book_id, page_num, strokes_alice, user_id=alice)
    res_b = ann.save_page_drawing(book_id, page_num, strokes_bob, user_id=bob)
    assert res_a["visibility"] == "private"
    assert res_b["visibility"] == "private"

    # Enquanto privados, o bundle de Bob contém apenas seu próprio desenho e zero public_drawings
    bundle_bob_initial = ann.get_page_drawings_bundle(book_id, page_num, user_id=bob)
    assert len(bundle_bob_initial["own_drawing"]["strokes"]) == 1
    assert bundle_bob_initial["public_drawings"] == []

    # Alice torna seu desenho na página 30 público
    pub_draw_a = ann.set_page_drawing_visibility(book_id, page_num, "public", user_id=alice)
    assert pub_draw_a["visibility"] == "public"
    assert pub_draw_a["author_display_name"] == "Alice Lemos"

    # Alice adiciona mais um traço sem passar `visibility`: deve preservar 'public'
    strokes_alice_v2 = strokes_alice + [{"color": "#34d399", "width": 2, "points": [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.4}]}]
    res_a_v2 = ann.save_page_drawing(book_id, page_num, strokes_alice_v2, user_id=alice)
    assert res_a_v2["visibility"] == "public"
    assert res_a_v2["stroke_count"] == 2

    # Bob consulta o bundle da página 30: vê seu próprio desenho privado + o desenho público da Alice
    bundle_bob = ann.get_page_drawings_bundle(book_id, page_num, user_id=bob)
    assert len(bundle_bob["own_drawing"]["strokes"]) == 1
    assert len(bundle_bob["public_drawings"]) == 1
    assert bundle_bob["public_drawings"][0]["user_id"] == alice
    assert bundle_bob["public_drawings"][0]["author_display_name"] == "Alice Lemos"
    assert bundle_bob["public_drawings"][0]["is_own"] is False
    assert len(bundle_bob["public_drawings"][0]["strokes"]) == 2

    # Carlos tenta modificar a visibilidade do desenho da Alice pelo drawing_id -> bloqueado
    alice_drawing_id = bundle_bob["public_drawings"][0]["id"]
    with pytest.raises(ann.AnnotationPermissionError):
        ann.set_page_drawing_visibility(book_id, page_num, "private", user_id=carlos, drawing_id=alice_drawing_id)

    # Bob limpa seu próprio desenho na página 30: o desenho da Alice permanece intacto!
    assert ann.delete_page_drawing(book_id, page_num, user_id=bob) is True
    alice_after_bob_clear = ann.get_page_drawing(book_id, page_num, user_id=alice)
    assert len(alice_after_bob_clear["strokes"]) == 2


# =========================================================================
# 16. MARCADORES PESSOAIS DE LEITURA (BOOKMARKS)
# =========================================================================

def test_personal_bookmarks_pdf_and_epub_deduplication_and_isolation(v11_populated_db):
    """
    Valida:
    - Marcadores pessoais para PDF (página) e EPUB (CFI).
    - Prevenção de duplicatas na mesma posição para o mesmo usuário e livro.
    - Isolamento absoluto: marcadores nunca aparecem para outros usuários e não podem ser excluídos por terceiros.
    """
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]

    # Marcador PDF
    bm1 = ann.create_bookmark("book-pdf-1", user_id=alice, page=42, label="Capítulo da denúncia")
    assert bm1["page"] == 42
    assert bm1["label"] == "Capítulo da denúncia"

    # Criar novamente na mesma página 42 atualiza o rótulo sem duplicar o registro
    bm1_upd = ann.create_bookmark("book-pdf-1", user_id=alice, page=42, label="Capítulo revisado")
    assert bm1_upd["id"] == bm1["id"]
    assert bm1_upd["label"] == "Capítulo revisado"
    assert len(ann.list_bookmarks("book-pdf-1", user_id=alice)) == 1

    # Marcador EPUB com CFI
    cfi_a = "epubcfi(/6/4[chap1]!/4/2/1:0)"
    cfi_b = "epubcfi(/6/6[chap2]!/4/2/1:0)"
    bm_epub_1 = ann.create_bookmark("book-epub-1", user_id=alice, page=10, epub_cfi=cfi_a, chapter_label="Meditação I", label="Início")
    bm_epub_2 = ann.create_bookmark("book-epub-1", user_id=alice, page=10, epub_cfi=cfi_b, chapter_label="Meditação II", label="Cogito")
    assert bm_epub_1["id"] != bm_epub_2["id"]
    assert len(ann.list_bookmarks("book-epub-1", user_id=alice)) == 2

    # Isolamento: Bob não vê os marcadores da Alice nem em list_bookmarks nem em get_book_annotations_summary(scope='all_visible')
    assert ann.list_bookmarks("book-pdf-1", user_id=bob) == []
    bob_summary = ann.get_book_annotations_summary("book-pdf-1", user_id=bob, scope="all_visible")
    assert bob_summary["bookmarks"] == []

    # Bob não consegue excluir o marcador da Alice
    assert ann.delete_bookmark(bm1["id"], user_id=bob) is False
    assert ann.delete_bookmark(bm1["id"], user_id=alice) is True
    assert ann.list_bookmarks("book-pdf-1", user_id=alice) == []


# =========================================================================
# 17 & 18. IMPORTAÇÃO/EXPORTAÇÃO SEGURA E REGRESSÃO DA v1.7.0
# =========================================================================

def test_export_and_import_security_privacy_and_v170_regression(v11_populated_db):
    """
    Valida:
    - Exportação pessoal (JSON e Markdown) inclui apenas anotações do próprio usuário (`scope='mine'`),
      nunca incluindo anotações públicas ou privadas de terceiros.
    - Importação JSON ignora `visibility='public'` e `user_id` externo, gravando tudo como `'private'`
      para o usuário autenticado e bloqueando vinculação a `highlight_id` de outro usuário.
    - Regressão v1.7.0: compartilhamento de citação na leitura coletiva continua íntegro.
    """
    database.init_db(v11_populated_db["db_path"])
    alice = v11_populated_db["alice_id"]
    bob = v11_populated_db["bob_id"]
    book_id = "book-pdf-1"

    # Alice cria e publica um destaque na página 50
    hl_alice_pub = ann.create_highlight(
        book_id=book_id,
        page=50,
        color="#fef08a",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.03}],
        selected_text="Destaque público da Alice na pág 50",
        user_id=alice,
        visibility="public",
    )

    # Bob exporta seu caderno de estudos de book-pdf-1: NÃO deve conter nada da Alice (nem público nem privado)
    bob_json_export = export_service.export_book_annotations_json(book_id, user_id=bob)
    assert bob_json_export["highlights"] == []
    assert bob_json_export["notes"] == []
    assert bob_json_export["drawings"] == []

    bob_md_export = export_service.export_book_annotations_markdown(book_id, user_id=bob)
    assert "Olhos de ressaca" not in bob_md_export
    assert "Destaque público da Alice" not in bob_md_export

    # Bob importa um JSON malicioso tentando:
    # 1) Definir visibility='public' e user_id=alice
    # 2) Vincular uma nota ao highlight_id da Alice (hl-legacy-alice)
    malicious_import_payload = {
        "version": "1.0",
        "format": "lectoria_study_export",
        "highlights": [
            {
                "id": "forged-hl-id",
                "page": 77,
                "color": "#a7f3d0",
                "rects": [{"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.03}],
                "selected_text": "Trecho importado pelo Bob",
                "visibility": "public",
                "user_id": alice,
            }
        ],
        "notes": [
            {
                "id": "forged-note-id",
                "page": 77,
                "highlight_id": "hl-legacy-alice",  # Pertence à Alice!
                "content": "Nota importada pelo Bob tentando referenciar destaque da Alice",
                "visibility": "public",
                "user_id": alice,
            }
        ],
        "drawings": [
            {
                "page": 5,  # Alice já tem desenho na página 5 ('draw-legacy-alice')
                "strokes": [{"color": "#1e293b", "width": 2, "points": [{"x": 0.4, "y": 0.4}, {"x": 0.5, "y": 0.5}]}],
                "visibility": "public",
                "user_id": alice,
            }
        ],
        "bookmarks": [
            {"page": 88, "label": "Marcador importado"}
        ],
    }

    res_imp = export_service.apply_import_annotations(book_id, malicious_import_payload, user_id=bob)
    assert res_imp["imported_counts"]["total"] == 3

    # Verifica que tudo que Bob importou pertence a Bob (user_id=bob) e nasceu 'private'
    bob_hls = ann.get_highlights(book_id, user_id=bob, scope="mine")
    assert len(bob_hls) == 1
    assert bob_hls[0]["user_id"] == bob
    assert bob_hls[0]["visibility"] == "private"

    bob_notes = ann.get_notes(book_id, user_id=bob, scope="mine")
    assert len(bob_notes) == 1
    assert bob_notes[0]["user_id"] == bob
    assert bob_notes[0]["visibility"] == "private"
    # O vínculo com 'hl-legacy-alice' da Alice foi neutralizado para None!
    assert bob_notes[0]["highlight_id"] is None

    bob_draw = ann.get_page_drawing(book_id, page=5, user_id=bob)
    assert bob_draw["visibility"] == "private"
    assert len(bob_draw["strokes"]) == 1

    # O desenho original da Alice na página 5 não foi tocado
    alice_draw = ann.get_page_drawing(book_id, page=5, user_id=alice)
    assert len(alice_draw["strokes"]) == 1
    assert alice_draw["strokes"][0]["color"] == "#3d7cf4"

    # Regressão v1.7.0: compartilhar nota privada própria na Leitura Coletiva continua funcionando
    quote_created = srs.share_quote(
        read_id="read-v170",
        user_id=bob,
        note_id=bob_notes[0]["id"],
        comment_text="Compartilhando minha nota importada na leitura coletiva",
        is_spoiler=False,
    )
    assert quote_created["quote_text"] == "Nota importada pelo Bob tentando referenciar destaque da Alice"
    assert quote_created["page_number"] == 77
