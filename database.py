"""
Módulo de banco de dados SQLite para o Lectoria.
Gerencia conexão, modo WAL, chaves estrangeiras e controle de migrações de schema.
"""

import sqlite3
from contextlib import contextmanager
from typing import Generator
import config

SCHEMA_VERSION = 3


def get_connection() -> sqlite3.Connection:
    """Cria e configura uma conexão com o SQLite."""
    conn = sqlite3.connect(
        str(config.DB_PATH),
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
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager para operações de banco de dados com commit/rollback seguro."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Inicializa as tabelas do banco de dados e executa migrações necessárias."""
    with get_db() as conn:
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


