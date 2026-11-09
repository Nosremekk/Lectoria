"""
Módulo de banco de dados SQLite para o Lectoria.
Gerencia conexão, modo WAL, chaves estrangeiras e controle de migrações de schema.
"""

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
import config

SCHEMA_VERSION = 9


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Cria e configura uma conexão com o SQLite."""
    conn = sqlite3.connect(
        str(db_path or config.DB_PATH),
        timeout=30.0,
    )
    # Permite acessar colunas como dicionário: row['title']
    conn.row_factory = sqlite3.Row
    # Otimizações de desempenho e concorrência para leitura/escrita
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_db(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager para operações de banco de dados com commit/rollback seguro."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    """
    Inicializa as tabelas do banco de dados e executa migrações necessárias.
    Quando há migrações pendentes em um banco preexistente, gera automaticamente
    uma cópia de segurança pré-migração e valida a integridade antes e depois.
    """
    target_db = Path(db_path or config.DB_PATH).resolve()
    target_db.parent.mkdir(parents=True, exist_ok=True)

    with get_db(target_db) as conn:
        cursor = conn.cursor()

        # Tabela de controle de versão de migrações
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        cursor.execute("SELECT MAX(version) FROM schema_migrations;")
        current_version = cursor.fetchone()[0] or 0
        conn.commit()

        # Se o banco já possui dados (current_version > 0) e precisa migrar para versão superior,
        # cria um backup automático pré-migração e verifica integridade
        pre_migration_bak: Optional[Path] = None
        if 0 < current_version < SCHEMA_VERSION and target_db.exists():
            cursor.execute("PRAGMA integrity_check;")
            chk = cursor.fetchone()
            if not chk or chk[0] != "ok":
                raise sqlite3.DatabaseError(
                    f"Banco de dados '{target_db}' falhou na verificação de integridade antes da migração."
                )

            pre_migration_bak = target_db.with_name(
                f"lectoria_pre_migration_v{current_version}_to_v{SCHEMA_VERSION}.db.bak"
            )
            bak_conn = sqlite3.connect(str(pre_migration_bak))
            try:
                conn.backup(bak_conn)
            finally:
                bak_conn.close()

        if current_version < SCHEMA_VERSION:
            # Desativa temporariamente PRAGMA foreign_keys fora de transação durante
            # reconstruções de tabelas (ex: books -> books_v4) para evitar que DROP TABLE books
            # dispare ON DELETE CASCADE nas tabelas filhas (reading_progress, highlights, notes, etc.)
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF;")

        try:
            if current_version < 1:
                _apply_migration_v1(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (1);")

            if current_version < 2:
                _apply_migration_v2(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (2);")

            if current_version < 3:
                _apply_migration_v3(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (3);")

            if current_version < 4:
                _apply_migration_v4(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (4);")

            if current_version < 5:
                _apply_migration_v5(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (5);")

            if current_version < 6:
                _apply_migration_v6(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (6);")

            if current_version < 7:
                _apply_migration_v7(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (7);")

            if current_version < 8:
                _apply_migration_v8(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (8);")

            if current_version < 9:
                _apply_migration_v9(cursor)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (9);")

            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys=ON;")

        # Reconcilia o caminho da fonte padrão #1 ('Biblioteca Principal') caso o projeto
        # tenha sido transferido para outro diretório ou computador (ex: deploy no Positivo C1260)
        _reconcile_default_library_source(cursor)

        if pre_migration_bak is not None:
            cursor.execute("PRAGMA integrity_check;")
            post_chk = cursor.fetchone()
            if not post_chk or post_chk[0] != "ok":
                raise sqlite3.DatabaseError(
                    "Falha de integridade detectada após aplicar migração de schema."
                )


def _reconcile_default_library_source(cursor: sqlite3.Cursor) -> None:
    """
    Se a fonte inicial #1 ('Biblioteca Principal') apontar para uma pasta 'books'
    de outro computador/caminho que não existe no host atual, atualiza-a para config.LIBRARY_DIR.
    """
    try:
        cursor.execute(
            "SELECT id, name, path FROM library_sources WHERE id = 1;"
        )
        row = cursor.fetchone()
        if not row:
            return
        stored_path = Path(row["path"])
        current_lib_dir = config.LIBRARY_DIR.resolve()
        if (
            row["name"] == "Biblioteca Principal"
            and not stored_path.exists()
            and stored_path.name == "books"
            and current_lib_dir.exists()
        ):
            cursor.execute(
                "SELECT id FROM library_sources WHERE path = ? AND id != 1;",
                (str(current_lib_dir),),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "UPDATE library_sources SET path = ?, last_error = NULL WHERE id = 1;",
                    (str(current_lib_dir),),
                )
    except sqlite3.Error:
        pass


def _apply_migration_v1(cursor: sqlite3.Cursor) -> None:
    """
    Schema V1:
    - users: preparado para futuros perfis de leitura
    - books: metadados, identificador UUID estável, caminho relativo e integridade
    - reading_progress: última página lida por usuário e livro
    """
    # Tabela de usuários (futura expansão para múltiplos leitores na família)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Usuário padrão
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (id, username, display_name)
        VALUES (1, 'default', 'Leitor');
        """
    )

    # Tabela de livros
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            filename TEXT NOT NULL,
            rel_path TEXT NOT NULL UNIQUE,
            filesize INTEGER NOT NULL,
            page_count INTEGER DEFAULT 0,
            file_hash TEXT,
            is_missing INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_books_rel_path ON books(rel_path);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_books_is_missing ON books(is_missing);"
    )

    # Tabela de progresso de leitura
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reading_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1,
            book_id TEXT NOT NULL,
            current_page INTEGER NOT NULL DEFAULT 1,
            total_pages INTEGER NOT NULL DEFAULT 1,
            percentage REAL NOT NULL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
            UNIQUE (user_id, book_id)
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_progress_user_book ON reading_progress(user_id, book_id);"
    )


def _apply_migration_v2(cursor: sqlite3.Cursor) -> None:
    """
    Schema V2:
    - collections: suporte a coleções/categorias personalizadas
    - collection_books: relacionamento muitos-para-muitos entre coleções e livros
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_collections_name ON collections(name COLLATE NOCASE);"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_books (
            collection_id INTEGER NOT NULL,
            book_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_id, book_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_col_books_coll ON collection_books(collection_id);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_col_books_book ON collection_books(book_id);"
    )


def _apply_migration_v3(cursor: sqlite3.Cursor) -> None:
    """
    Schema V3:
    - highlights: marcações de texto coloridas com retângulos normalizados
    - notes: anotações e comentários vinculados a destaques ou a páginas
    - page_drawings: traços vetoriais de desenho livre por página
    """
    # Destaques / Marca-texto
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS highlights (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            page INTEGER NOT NULL,
            color TEXT NOT NULL DEFAULT '#fef08a',
            rects_json TEXT NOT NULL,
            selected_text TEXT NOT NULL,
            text_context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_highlights_book ON highlights(book_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_highlights_page ON highlights(book_id, page);")

    # Anotações textuais
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            page INTEGER NOT NULL,
            highlight_id TEXT,
            content TEXT NOT NULL,
            color TEXT DEFAULT '#fef08a',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY (highlight_id) REFERENCES highlights(id) ON DELETE SET NULL
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_book ON notes(book_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_page ON notes(book_id, page);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_highlight ON notes(highlight_id);")

    # Desenho livre vetorial por página
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS page_drawings (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            page INTEGER NOT NULL,
            strokes_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
            UNIQUE(book_id, page)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_drawings_book_page ON page_drawings(book_id, page);")


def _apply_migration_v4(cursor: sqlite3.Cursor) -> None:
    """
    Schema V4:
    - library_sources: múltiplas fontes de diretórios locais configuráveis
    - books.source_id: vínculo entre livros e sua fonte de origem
    - users: suporte a perfis de leitura com roles ('admin', 'reader') e senhas com hash seguro
    - sessions: tokens de sessão criptográficos para autenticação de usuários
    - isolamento de dados por usuário em collections, highlights, notes e page_drawings
    """
    # 1. Tabela de Fontes de Biblioteca
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS library_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_scanned_at TIMESTAMP,
            last_error TEXT
        );
        """
    )
    # Cadastra a biblioteca padrão como fonte inicial (#1)
    canonical_lib = str(config.LIBRARY_DIR.resolve())
    cursor.execute(
        """
        INSERT OR IGNORE INTO library_sources (id, name, path, is_active)
        VALUES (1, 'Biblioteca Principal', ?, 1);
        """,
        (canonical_lib,),
    )

    # 2. Atualização de books para incluir source_id e unicidade por (source_id, rel_path)
    cursor.execute("PRAGMA table_info(books);")
    book_cols = [r["name"] for r in cursor.fetchall()]
    if "source_id" not in book_cols:
        cursor.execute(
            """
            CREATE TABLE books_v4 (
                id TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL DEFAULT 1 REFERENCES library_sources(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                author TEXT,
                filename TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                filesize INTEGER NOT NULL,
                page_count INTEGER DEFAULT 0,
                file_hash TEXT,
                is_missing INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source_id, rel_path)
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO books_v4 (id, source_id, title, author, filename, rel_path, filesize, page_count, file_hash, is_missing, created_at, updated_at)
            SELECT id, 1, title, author, filename, rel_path, filesize, page_count, file_hash, is_missing, created_at, updated_at FROM books;
            """
        )
        cursor.execute("DROP TABLE books;")
        cursor.execute("ALTER TABLE books_v4 RENAME TO books;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_source ON books(source_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_source_rel ON books(source_id, rel_path);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_is_missing ON books(is_missing);")

    # 3. Atualização de users para suportar autenticação e papéis
    cursor.execute("PRAGMA table_info(users);")
    user_cols = [r["name"] for r in cursor.fetchall()]
    if "password_hash" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT;")
    if "role" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'reader';")
    if "is_active" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;")

    cursor.execute(
        """
        INSERT OR IGNORE INTO users (id, username, display_name, role, is_active)
        VALUES (1, 'default', 'Leitor Padrão', 'reader', 1);
        """
    )

    # 4. Tabela de Sessões Autenticadas
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);")

    # 5. Adiciona user_id às coleções
    cursor.execute("PRAGMA table_info(collections);")
    col_cols = [r["name"] for r in cursor.fetchall()]
    if "user_id" not in col_cols:
        cursor.execute("ALTER TABLE collections ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collections_user ON collections(user_id);")

    # 6. Adiciona user_id aos destaques (highlights)
    cursor.execute("PRAGMA table_info(highlights);")
    hl_cols = [r["name"] for r in cursor.fetchall()]
    if "user_id" not in hl_cols:
        cursor.execute("ALTER TABLE highlights ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_highlights_user ON highlights(user_id);")

    # 7. Adiciona user_id às notas
    cursor.execute("PRAGMA table_info(notes);")
    note_cols = [r["name"] for r in cursor.fetchall()]
    if "user_id" not in note_cols:
        cursor.execute("ALTER TABLE notes ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id);")

    # 8. Atualiza page_drawings para garantir chave única por (book_id, page, user_id)
    cursor.execute("PRAGMA table_info(page_drawings);")
    draw_cols = [r["name"] for r in cursor.fetchall()]
    if "user_id" not in draw_cols:
        cursor.execute(
            """
            CREATE TABLE page_drawings_v4 (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                page INTEGER NOT NULL,
                strokes_json TEXT NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(book_id, page, user_id)
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO page_drawings_v4 (id, book_id, page, strokes_json, user_id, updated_at)
            SELECT id, book_id, page, strokes_json, 1, updated_at FROM page_drawings;
            """
        )
        cursor.execute("DROP TABLE page_drawings;")
        cursor.execute("ALTER TABLE page_drawings_v4 RENAME TO page_drawings;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drawings_book_page_user ON page_drawings(book_id, page, user_id);")


def _apply_migration_v5(cursor: sqlite3.Cursor) -> None:
    """
    Schema V5:
    - Suporte a capas reais (extraídas do PDF), capas personalizadas e cache inteligente na tabela books.
    """
    cursor.execute("PRAGMA table_info(books);")
    book_cols = [r["name"] for r in cursor.fetchall()]

    if "cover_type" not in book_cols:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_type TEXT NOT NULL DEFAULT 'none';")
    if "cover_path" not in book_cols:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_path TEXT;")
    if "cover_source_hash" not in book_cols:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_source_hash TEXT;")
    if "cover_updated_at" not in book_cols:
        cursor.execute("ALTER TABLE books ADD COLUMN cover_updated_at TIMESTAMP;")


def _apply_migration_v6(cursor: sqlite3.Cursor) -> None:
    """
    Schema V6:
    - Suporte a formato de arquivo ('pdf' ou 'epub') na tabela books.
    - Suporte a localizador de progresso (CFI / locator) na tabela reading_progress.
    """
    cursor.execute("PRAGMA table_info(books);")
    book_cols = [r["name"] for r in cursor.fetchall()]
    if "format" not in book_cols:
        cursor.execute("ALTER TABLE books ADD COLUMN format TEXT NOT NULL DEFAULT 'pdf';")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_format ON books(format);")

    cursor.execute("PRAGMA table_info(reading_progress);")
    prog_cols = [r["name"] for r in cursor.fetchall()]
    if "progress_locator" not in prog_cols:
        cursor.execute("ALTER TABLE reading_progress ADD COLUMN progress_locator TEXT;")


def _apply_migration_v7(cursor: sqlite3.Cursor) -> None:
    """
    Schema V7 — Intelligent Library & Metadata Discovery:
    - Metadados bibliográficos estruturados na tabela books:
      isbn10, isbn13, publisher, publish_year, language, description,
      original_title, work_id, edition, identification_status, identification_score,
      metadata_source, metadata_updated_at, manual_lock_fields.
    - Taxonomia e classificação bibliográfica:
      tabela 'subjects' (id, name, category, subcategory, genre, origin, period)
      tabela 'book_subjects' (book_id, subject_id, is_manual)
    - Tabela 'metadata_candidates' para candidatos e auditoria de correspondência externa.
    - Tabela 'metadata_api_cache' para cache com TTL de requisições Open Library e Google Books.
    - Tabela virtual 'books_fts' (FTS5) para busca textual instantânea de título, autor, descrição, editora e assuntos.
    """
    cursor.execute("PRAGMA table_info(books);")
    book_cols = [r["name"] for r in cursor.fetchall()]

    new_book_cols = [
        ("isbn10", "TEXT"),
        ("isbn13", "TEXT"),
        ("publisher", "TEXT"),
        ("publish_year", "INTEGER"),
        ("language", "TEXT"),
        ("description", "TEXT"),
        ("original_title", "TEXT"),
        ("work_id", "TEXT"),
        ("edition", "TEXT"),
        ("identification_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("identification_score", "REAL DEFAULT 0.0"),
        ("metadata_source", "TEXT"),
        ("metadata_updated_at", "TIMESTAMP"),
        ("manual_lock_fields", "TEXT DEFAULT '[]'"),
    ]

    for col_name, col_type in new_book_cols:
        if col_name not in book_cols:
            cursor.execute(f"ALTER TABLE books ADD COLUMN {col_name} {col_type};")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_isbn13 ON books(isbn13);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_isbn10 ON books(isbn10);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_work_id ON books(work_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_id_status ON books(identification_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_publish_year ON books(publish_year);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_books_language ON books(language);")

    # 2. Taxonomia e Classificação
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            category TEXT NOT NULL,
            subcategory TEXT,
            genre TEXT,
            origin TEXT,
            period TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_cat ON subjects(category, subcategory);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_genre ON subjects(genre);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subjects_origin ON subjects(origin);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_subjects (
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            is_manual INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (book_id, subject_id)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_subjects_subject ON book_subjects(subject_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_subjects_book ON book_subjects(book_id);")

    # 3. Candidatos de Correspondência
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            publisher TEXT,
            publish_year INTEGER,
            isbn10 TEXT,
            isbn13 TEXT,
            cover_url TEXT,
            raw_json TEXT NOT NULL,
            score REAL NOT NULL,
            confidence_level TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_book ON metadata_candidates(book_id);")

    # 4. Cache de APIs bibliográficas
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_api_cache (
            cache_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            response_json TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_expires ON metadata_api_cache(expires_at);")

    # 5. Tabela Virtual FTS5 (com suporte a fallback se FTS5 não estiver compilado)
    try:
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                book_id UNINDEXED,
                title,
                author,
                description,
                publisher,
                subjects
            );
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO books_fts (book_id, title, author, description, publisher, subjects)
            SELECT id, title, COALESCE(author, ''), COALESCE(description, ''), COALESCE(publisher, ''), ''
            FROM books;
            """
        )
    except sqlite3.OperationalError:
        pass


def _apply_migration_v8(cursor: sqlite3.Cursor) -> None:
    """
    Schema V8 — Lectoria v1.5.0 (Biblioteca Viva):
    - Novos atributos na tabela books: subtitle, volume, reviewed_at, reviewed_by, last_error_reason.
    - Proveniência e diagnóstico de metadados:
      * metadata_provenance (origem e confiança por campo)
      * metadata_provider_stats (métricas de chamadas, sucessos, falhas, rate limits por provedor)
    - Automação e fila persistente de tarefas:
      * scheduled_jobs (agendamento configurável sobrevivendo a reinicializações)
      * background_jobs (fila e histórico de tarefas com recuperação de interrupção)
    - Multiusuário, privacidade por padrão e recursos sociais ('Nossa Biblioteca'):
      * user_profiles (avatar, bio, preferências de leitura)
      * user_privacy_settings (controles explícitos, tudo privado=0 por padrão)
      * book_reviews (avaliações 1-5 estrelas, resenhas, favoritos e lista 'quero ler')
      * book_recommendations (recomendações diretas entre usuários)
      * shared_annotations (anotações, destaques, citações e desenhos compartilhados)
      * annotation_comments (comentários e respostas em anotações compartilhadas)
      * reading_activity (feed de atividades consentidas)
    - Administração e auditoria:
      * admin_audit_log (registro de ações administrativas)
      * system_settings (configurações persistentes da instância)
    """
    # 1. Colunas adicionais na tabela books
    cursor.execute("PRAGMA table_info(books);")
    book_cols = [r["name"] for r in cursor.fetchall()]

    v8_book_cols = [
        ("subtitle", "TEXT"),
        ("volume", "TEXT"),
        ("reviewed_at", "TIMESTAMP"),
        ("reviewed_by", "INTEGER"),
        ("last_error_reason", "TEXT"),
    ]
    for col_name, col_type in v8_book_cols:
        if col_name not in book_cols:
            cursor.execute(f"ALTER TABLE books ADD COLUMN {col_name} {col_type};")

    # 2. Proveniência de metadados e estatísticas de provedores
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL,
            field_value TEXT,
            source TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 100.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_id, field_name)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_provenance_book ON metadata_provenance(book_id);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_provider_stats (
            provider TEXT PRIMARY KEY,
            total_requests INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            not_found_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            rate_limit_count INTEGER NOT NULL DEFAULT 0,
            parse_error_count INTEGER NOT NULL DEFAULT 0,
            last_status TEXT NOT NULL DEFAULT 'healthy',
            last_error TEXT,
            cooldown_until TIMESTAMP,
            last_call_at TIMESTAMP
        );
        """
    )
    for prov in ("openlibrary", "googlebooks", "crossref"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO metadata_provider_stats (provider, last_status)
            VALUES (?, 'healthy');
            """,
            (prov,),
        )

    # 3. Agendamento e fila persistente de tarefas (Automação)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            schedule_mode TEXT NOT NULL DEFAULT 'manual',
            interval_minutes INTEGER NOT NULL DEFAULT 360,
            days_of_week TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]',
            run_time TEXT NOT NULL DEFAULT '03:00',
            timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
            is_enabled INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL DEFAULT '{}',
            last_run_at TIMESTAMP,
            last_status TEXT,
            next_run_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    default_schedules = [
        ("scan_library", "Varredura de novos livros", "interval", 60, "[0,1,2,3,4,5,6]", "02:00", 0),
        ("enrich_metadata", "Enriquecimento bibliográfico", "daily", 1440, "[0,1,2,3,4,5,6]", "03:00", 0),
        ("update_missing_covers", "Atualização de capas ausentes", "daily", 1440, "[0,1,2,3,4,5,6]", "03:30", 0),
        ("rebuild_search_index", "Reconstrução de índices de busca", "weekly", 10080, "[6]", "04:00", 0),
        ("db_maintenance", "Manutenção do banco SQLite", "weekly", 10080, "[6]", "04:30", 0),
        ("auto_backup", "Backups automáticos", "daily", 1440, "[0,1,2,3,4,5,6]", "05:00", 0),
    ]
    for j_type, j_name, s_mode, int_min, days, r_time, enabled in default_schedules:
        cursor.execute(
            """
            INSERT OR IGNORE INTO scheduled_jobs (
                job_type, name, schedule_mode, interval_minutes, days_of_week, run_time, is_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (j_type, j_name, s_mode, int_min, days, r_time, enabled),
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS background_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            trigger_source TEXT NOT NULL DEFAULT 'manual',
            triggered_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            total_items INTEGER NOT NULL DEFAULT 0,
            processed_items INTEGER NOT NULL DEFAULT 0,
            success_items INTEGER NOT NULL DEFAULT 0,
            review_items INTEGER NOT NULL DEFAULT 0,
            not_found_items INTEGER NOT NULL DEFAULT 0,
            failed_items INTEGER NOT NULL DEFAULT 0,
            progress_pct REAL NOT NULL DEFAULT 0.0,
            details_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bg_jobs_status ON background_jobs(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bg_jobs_type_status ON background_jobs(job_type, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bg_jobs_created ON background_jobs(created_at DESC);")

    # 4. Perfis de usuário e configurações de privacidade (Privado por padrão)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            display_name TEXT,
            avatar_color TEXT NOT NULL DEFAULT '#d4a853',
            avatar_url TEXT,
            bio TEXT NOT NULL DEFAULT '',
            reading_prefs_json TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_privacy_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            share_current_reading INTEGER NOT NULL DEFAULT 0,
            share_reading_history INTEGER NOT NULL DEFAULT 0,
            share_reviews INTEGER NOT NULL DEFAULT 0,
            share_lists INTEGER NOT NULL DEFAULT 0,
            share_annotations_default INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Inicializa perfil e privacidade (100% privada por padrão) para todos os usuários existentes
    try:
        cursor.execute("SELECT id, username FROM users;")
        for u_row in cursor.fetchall():
            uid = u_row["id"]
            uname = u_row["username"]
            cursor.execute(
                "INSERT OR IGNORE INTO user_profiles (user_id, display_name) VALUES (?, ?);",
                (uid, uname),
            )
            cursor.execute("INSERT OR IGNORE INTO user_privacy_settings (user_id) VALUES (?);", (uid,))
    except sqlite3.OperationalError:
        pass

    # 5. Avaliações, resenhas, favoritos, lista 'Quero Ler' e recomendações
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
            review_text TEXT,
            is_favorite INTEGER NOT NULL DEFAULT 0,
            want_to_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(book_id, user_id)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_book ON book_reviews(book_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_user ON book_reviews(user_id);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            message TEXT NOT NULL DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rec_recipient ON book_recommendations(to_user_id, is_read);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rec_book ON book_recommendations(book_id);")

    # 6. Anotações compartilhadas, comentários e feed de atividades ('Nossa Biblioteca')
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            note_id TEXT,
            page_number INTEGER,
            chapter_name TEXT,
            progress_locator TEXT,
            quote_text TEXT,
            comment_text TEXT,
            color TEXT NOT NULL DEFAULT 'yellow',
            visibility TEXT NOT NULL DEFAULT 'public',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_ann_book ON shared_annotations(book_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shared_ann_user ON shared_annotations(user_id);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS annotation_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shared_annotation_id INTEGER NOT NULL REFERENCES shared_annotations(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES annotation_comments(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ann_comments_shared ON annotation_comments(shared_annotation_id);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reading_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id TEXT REFERENCES books(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON reading_activity(created_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON reading_activity(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_book ON reading_activity(book_id);")

    # 7. Auditoria administrativa e configurações do sistema
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_log(created_at DESC);")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    default_settings = [
        ("max_concurrent_jobs", "1"),
        ("backup_retention_count", "10"),
        ("allow_social_sharing", "true"),
        ("auto_scan_on_startup", "true"),
    ]
    for s_key, s_val in default_settings:
        cursor.execute(
            "INSERT OR IGNORE INTO system_settings (key, value_json) VALUES (?, ?);",
            (s_key, s_val),
        )


def _apply_migration_v9(cursor: sqlite3.Cursor) -> None:
    """
    Schema V9 — Lectoria v1.5.2 (Refinamento da Biblioteca & Categorias Personalizáveis):
    - Tabela `categories` para gerenciamento de categorias principais e personalizadas
      (ordenação, descrição, renomeação, mesclagem e exclusão segura sem apagar livros).
    - Tabela `book_categories` para associação entre livros e categorias gerenciadas,
      separada dos assuntos bibliográficos importados (`subjects`/`book_subjects`),
      com controle de prioridade manual (`is_manual`), confiança e justificativa.
    - Migração automática de categorias existentes em `subjects`/`book_subjects` para `categories`/`book_categories`.
    - Coluna `is_completed_manual` em `reading_progress` para suportar marcação explícita de conclusão.
    - Registro do provedor bibliográfico `brasilapi` em `metadata_provider_stats`.
    """
    # 1. Tabela de Categorias Gerenciáveis (padrão + personalizadas)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT NOT NULL DEFAULT '',
            display_order INTEGER NOT NULL DEFAULT 0,
            is_custom INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_categories_order ON categories(display_order ASC, name COLLATE NOCASE ASC);"
    )

    default_categories = [
        ("Filosofia", "Obras de filosofia antiga, medieval, moderna, contemporânea, ética, lógica e política.", 10, 0),
        ("Literatura", "Romances, contos, poesia, teatro, ensaios literários e clássicos da literatura mundial.", 20, 0),
        ("Ciências humanas", "História, sociologia, antropologia, psicologia, educação, direito e economia.", 30, 0),
        ("Ciência e tecnologia", "Computação, matemática, física, engenharia, ciências naturais e tecnologia.", 40, 0),
        ("Artes e cultura", "Música, cinema, artes visuais, arquitetura, design e estudos culturais.", 50, 0),
    ]
    for c_name, c_desc, c_order, c_custom in default_categories:
        cursor.execute(
            """
            INSERT OR IGNORE INTO categories (name, description, display_order, is_custom)
            VALUES (?, ?, ?, ?);
            """,
            (c_name, c_desc, c_order, c_custom),
        )

    # 2. Tabela de Vínculo Livro <-> Categoria (separada de subjects bibliográficos)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_categories (
            book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            subcategory TEXT,
            is_manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 100.0,
            classification_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (book_id, category_id)
        );
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_categories_book ON book_categories(book_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_categories_cat ON book_categories(category_id);")

    # 3. Migra categorias já presentes em subjects / book_subjects
    try:
        cursor.execute(
            """
            SELECT DISTINCT TRIM(category) AS cat_name
            FROM subjects
            WHERE category IS NOT NULL AND TRIM(category) != '' AND LOWER(TRIM(category)) != 'geral';
            """
        )
        existing_cats = [r["cat_name"] for r in cursor.fetchall()]
        next_order = 60
        for cat_name in existing_cats:
            cursor.execute("SELECT id FROM categories WHERE name = ? COLLATE NOCASE;", (cat_name,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO categories (name, description, display_order, is_custom)
                    VALUES (?, '', ?, 1);
                    """,
                    (cat_name, next_order),
                )
                next_order += 10

        cursor.execute(
            """
            SELECT bs.book_id, c.id AS category_id, MAX(s.subcategory) AS subcategory, MAX(bs.is_manual) AS is_manual
            FROM book_subjects bs
            JOIN subjects s ON bs.subject_id = s.id
            JOIN categories c ON c.name = s.category COLLATE NOCASE
            GROUP BY bs.book_id, c.id;
            """
        )
        for row in cursor.fetchall():
            cursor.execute(
                """
                INSERT OR IGNORE INTO book_categories (book_id, category_id, subcategory, is_manual, confidence, classification_reason)
                VALUES (?, ?, ?, ?, 100.0, 'migrated_from_v8');
                """,
                (row["book_id"], row["category_id"], row["subcategory"], row["is_manual"] or 0),
            )
    except sqlite3.OperationalError:
        pass

    # 4. Coluna is_completed_manual em reading_progress (se a tabela existir)
    try:
        cursor.execute("PRAGMA table_info(reading_progress);")
        prog_cols = [r["name"] for r in cursor.fetchall()]
        if prog_cols and "is_completed_manual" not in prog_cols:
            cursor.execute("ALTER TABLE reading_progress ADD COLUMN is_completed_manual INTEGER NOT NULL DEFAULT 0;")
    except sqlite3.OperationalError:
        pass

    # 5. Provedor BrasilAPI ISBN em metadata_provider_stats
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO metadata_provider_stats (provider, last_status)
            VALUES ('brasilapi', 'healthy');
            """
        )
    except sqlite3.OperationalError:
        pass








