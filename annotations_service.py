"""
Módulo de serviço de anotações, marcações e desenhos do Lectoria.
Gerencia persistência de destaques coloridos, notas textuais e traços vetoriais no SQLite.
"""

import json
import uuid
from typing import Any, Dict, List, Optional
from database import get_db


class AnnotationNotFoundError(Exception):
    """Lançada quando uma anotação, destaque ou desenho não for encontrado."""
    pass


# =========================================================================
# MARCA-TEXTO / DESTAQUES (HIGHLIGHTS)
# =========================================================================

def create_highlight(
    book_id: str,
    page: int,
    color: str,
    rects: List[Dict[str, float]],
    selected_text: str,
    text_context: Optional[str] = None,
    user_id: int = 1,
) -> Dict[str, Any]:
    """Cria uma nova marcação de texto colorida com retângulos normalizados."""
    if page < 1:
        raise ValueError("Número de página inválido.")
    if not rects:
        raise ValueError("Pelo menos um retângulo de seleção deve ser fornecido.")
    if not selected_text or not selected_text.strip():
        raise ValueError("O texto selecionado não pode ser vazio.")

    highlight_id = str(uuid.uuid4())
    rects_json = json.dumps(rects)

    with get_db() as conn:
        cursor = conn.cursor()
        # Valida existência do livro
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro '{book_id}' não encontrado.")

        cursor.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (highlight_id, book_id, page, color or "#fef08a", rects_json, selected_text.strip(), text_context, user_id),
        )

        cursor.execute(
            """
            SELECT id, book_id, page, color, rects_json, selected_text, text_context, user_id, created_at
            FROM highlights WHERE id = ?
            """,
            (highlight_id,),
        )
        row = dict(cursor.fetchone())
        row["rects"] = json.loads(row["rects_json"])
        return row


def get_highlights(book_id: str, page: Optional[int] = None, user_id: int = 1) -> List[Dict[str, Any]]:
    """Retorna os destaques de um documento, opcionalmente filtrados por página e usuário."""
    with get_db() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT id, book_id, page, color, rects_json, selected_text, text_context, user_id, created_at
            FROM highlights
            WHERE book_id = ? AND user_id = ?
        """
        params: List[Any] = [book_id, user_id]
        if page is not None:
            sql += " AND page = ?"
            params.append(page)

        sql += " ORDER BY page ASC, created_at ASC"
        cursor.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            r["rects"] = json.loads(r["rects_json"])
        return rows


def delete_highlight(highlight_id: str, user_id: Optional[int] = None) -> bool:
    """Remove uma marcação de texto do banco de dados, opcionalmente checando o dono."""
    with get_db() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute("DELETE FROM highlights WHERE id = ? AND user_id = ?", (highlight_id, user_id))
        else:
            cursor.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
        return cursor.rowcount > 0


# =========================================================================
# ANOTAÇÕES TEXTUAIS (NOTES)
# =========================================================================

def create_note(
    book_id: str,
    page: int,
    content: str,
    color: Optional[str] = "#fef08a",
    highlight_id: Optional[str] = None,
    user_id: int = 1,
) -> Dict[str, Any]:
    """Cria uma anotação textual vinculada a um destaque ou a uma página específica."""
    if page < 1:
        raise ValueError("Número de página inválido.")
    clean_content = content.strip() if content else ""
    if not clean_content:
        raise ValueError("O conteúdo da anotação não pode ser vazio.")

    note_id = str(uuid.uuid4())

    with get_db() as conn:
        cursor = conn.cursor()
        # Valida existência do livro
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro '{book_id}' não encontrado.")

        cursor.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (note_id, book_id, page, highlight_id, clean_content, color or "#fef08a", user_id),
        )

        return get_note_by_id(note_id, cursor=cursor)


def get_note_by_id(note_id: str, cursor=None) -> Dict[str, Any]:
    """Retorna os dados de uma única anotação."""
    def _fetch(c):
        c.execute(
            """
            SELECT n.id, n.book_id, n.page, n.highlight_id, n.content, n.color, n.user_id, n.created_at, n.updated_at,
                   h.selected_text as highlight_text, h.color as highlight_color
            FROM notes n
            LEFT JOIN highlights h ON n.highlight_id = h.id
            WHERE n.id = ?
            """,
            (note_id,),
        )
        row = c.fetchone()
        if not row:
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")
        return dict(row)

    if cursor:
        return _fetch(cursor)
    with get_db() as conn:
        return _fetch(conn.cursor())


def get_notes(book_id: str, page: Optional[int] = None, user_id: int = 1) -> List[Dict[str, Any]]:
    """Retorna todas as anotações do livro para o usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT n.id, n.book_id, n.page, n.highlight_id, n.content, n.color, n.user_id, n.created_at, n.updated_at,
                   h.selected_text as highlight_text, h.color as highlight_color
            FROM notes n
            LEFT JOIN highlights h ON n.highlight_id = h.id
            WHERE n.book_id = ? AND n.user_id = ?
        """
        params: List[Any] = [book_id, user_id]
        if page is not None:
            sql += " AND n.page = ?"
            params.append(page)

        sql += " ORDER BY n.page ASC, n.created_at ASC"
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]


def update_note(note_id: str, content: str, color: Optional[str] = None, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Atualiza o conteúdo de uma anotação textual existente."""
    clean_content = content.strip() if content else ""
    if not clean_content:
        raise ValueError("O conteúdo da anotação não pode ser vazio.")

    with get_db() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute("SELECT id FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        else:
            cursor.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
        if not cursor.fetchone():
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        if color:
            cursor.execute(
                """
                UPDATE notes
                SET content = ?, color = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (clean_content, color, note_id),
            )
        else:
            cursor.execute(
                """
                UPDATE notes
                SET content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (clean_content, note_id),
            )

        return get_note_by_id(note_id, cursor=cursor)


def delete_note(note_id: str, user_id: Optional[int] = None) -> bool:
    """Remove uma anotação do banco de dados."""
    with get_db() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        else:
            cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cursor.rowcount > 0


# =========================================================================
# DESENHO LIVRE VETORIAL (PAGE DRAWINGS)
# =========================================================================

def get_page_drawing(book_id: str, page: int, user_id: int = 1) -> Dict[str, Any]:
    """Retorna os traços vetoriais de desenho para uma página do livro e usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, book_id, page, strokes_json, user_id, updated_at
            FROM page_drawings
            WHERE book_id = ? AND page = ? AND user_id = ?
            """,
            (book_id, page, user_id),
        )
        row = cursor.fetchone()
        if not row:
            return {"page": page, "strokes": []}

        data = dict(row)
        data["strokes"] = json.loads(data["strokes_json"])
        return data


def save_page_drawing(book_id: str, page: int, strokes: List[Dict[str, Any]], user_id: int = 1) -> Dict[str, Any]:
    """Salva ou atualiza a coleção de traços vetoriais de uma página para o usuário indicado."""
    if page < 1:
        raise ValueError("Número de página inválido.")

    strokes_json = json.dumps(strokes or [])
    drawing_id = str(uuid.uuid4())

    with get_db() as conn:
        cursor = conn.cursor()
        # Valida existência do livro
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro '{book_id}' não encontrado.")

        cursor.execute(
            """
            INSERT INTO page_drawings (id, book_id, page, strokes_json, user_id, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, page, user_id) DO UPDATE SET
                strokes_json = excluded.strokes_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (drawing_id, book_id, page, strokes_json, user_id),
        )

        return {"status": "success", "page": page, "stroke_count": len(strokes)}


def delete_page_drawing(book_id: str, page: int, user_id: int = 1) -> bool:
    """Limpa todos os traços de desenho de uma página para o usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM page_drawings WHERE book_id = ? AND page = ? AND user_id = ?",
            (book_id, page, user_id),
        )
        return cursor.rowcount > 0


# =========================================================================
# RESUMO DAS ANOTAÇÕES DO LIVRO (PARA O PAINEL DE ESTUDO)
# =========================================================================

def get_book_annotations_summary(book_id: str, user_id: int = 1) -> Dict[str, Any]:
    """Retorna todas as marcações, anotações e páginas com desenhos para o painel de leitura."""
    highlights = get_highlights(book_id, user_id=user_id)
    notes = get_notes(book_id, user_id=user_id)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT page, updated_at FROM page_drawings WHERE book_id = ? AND user_id = ? ORDER BY page ASC",
            (book_id, user_id),
        )
        drawing_pages = [dict(r) for r in cursor.fetchall()]

    return {
        "highlights": highlights,
        "notes": notes,
        "drawing_pages": drawing_pages,
        "counts": {
            "highlights": len(highlights),
            "notes": len(notes),
            "drawings": len(drawing_pages),
        },
    }

