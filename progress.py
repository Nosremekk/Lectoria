"""
Módulo de gerenciamento do progresso de leitura do Lectoria.
Registra e recupera a última página visitada e porcentagem concluída no SQLite.
"""

from typing import Any, Dict, Optional
from database import get_db


def save_progress(
    book_id: str,
    current_page: int,
    total_pages: int,
    user_id: int = 1,
) -> Dict[str, Any]:
    """
    Salva ou atualiza o progresso de leitura para um usuário e livro específicos.
    Ajusta limites para garantir integridade matemática.
    """
    if current_page < 1:
        current_page = 1

    if total_pages < 1:
        total_pages = 1

    if current_page > total_pages:
        current_page = total_pages

    percentage = round((current_page / total_pages) * 100.0, 1)

    with get_db() as conn:
        cursor = conn.cursor()

        # Garante que o livro existe antes de salvar o progresso
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro com ID '{book_id}' não existe.")

        cursor.execute(
            """
            INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                current_page = excluded.current_page,
                total_pages = excluded.total_pages,
                percentage = excluded.percentage,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, book_id, current_page, total_pages, percentage),
        )

        cursor.execute(
            """
            SELECT user_id, book_id, current_page, total_pages, percentage, updated_at
            FROM reading_progress
            WHERE user_id = ? AND book_id = ?
            """,
            (user_id, book_id),
        )
        row = cursor.fetchone()
        return dict(row)


def get_progress(book_id: str, user_id: int = 1) -> Optional[Dict[str, Any]]:
    """Recupera o progresso de leitura de um livro para o usuário informado."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id, book_id, current_page, total_pages, percentage, updated_at
            FROM reading_progress
            WHERE user_id = ? AND book_id = ?
            """,
            (user_id, book_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

