"""
Módulo de banco de dados SQLite para o Lectoria.
Gerencia conexão, modo WAL, chaves estrangeiras e controle de migrações de schema.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
import config

SCHEMA_VERSION = 4


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
    """Inicializa as tabelas do banco de dados e executa migrações necessárias."""
    with get_db(db_path) as conn:
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



