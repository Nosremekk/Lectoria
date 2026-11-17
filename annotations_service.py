"""
Módulo de serviço de anotações, marcações, desenhos e marcadores do Lectoria (v1.8.0).
Gerencia persistência de destaques coloridos (PDF e EPUB CFI), notas textuais,
traços vetoriais por página e marcadores pessoais no SQLite, com controle estrito
de visibilidade ('private' por padrão, 'public' por ação explícita do autor).
"""

import json
import math
import uuid
from typing import Any, Dict, List, Optional
from database import get_db


class AnnotationNotFoundError(Exception):
    """Lançada quando uma anotação, destaque, desenho ou marcador não for encontrado ou for inacessível."""
    pass


class AnnotationPermissionError(PermissionError):
    """Lançada quando um usuário tenta acessar livro de fonte inativa ou modificar anotação de terceiros."""
    pass


VALID_VISIBILITIES = ("private", "public")
VALID_SCOPES = ("mine", "public", "all_visible")
VALID_SORT_MODES = ("position", "recent")

MAX_EPUB_CFI_LEN = 1024
MAX_CHAPTER_LABEL_LEN = 300
MAX_BOOKMARK_LABEL_LEN = 300
MAX_HIGHLIGHT_TEXT_LEN = 10000
MAX_SELECTED_TEXT_LEN = MAX_HIGHLIGHT_TEXT_LEN
MAX_TEXT_CONTEXT_LEN = MAX_HIGHLIGHT_TEXT_LEN
MAX_NOTE_CONTENT_LEN = 20000
MAX_RECTS_PER_HIGHLIGHT = 100
MAX_STROKES_PER_DRAWING = 500
MAX_POINTS_PER_STROKE = 5000


def _validate_rects(rects: List[Dict[str, Any]]) -> None:
    """Valida limites e coordenadas dos retângulos de destaque PDF."""
    if len(rects) > MAX_RECTS_PER_HIGHLIGHT:
        raise ValueError(f"Número excessivo de retângulos no destaque (máx: {MAX_RECTS_PER_HIGHLIGHT}).")
    for idx, r in enumerate(rects):
        if not isinstance(r, dict):
            raise ValueError(f"Retângulo inválido na posição {idx}.")
        for coord_key in ("x1", "y1", "x2", "y2", "x", "y", "w", "h", "width", "height"):
            if coord_key in r:
                val = r[coord_key]
                if (
                    not isinstance(val, (int, float))
                    or isinstance(val, bool)
                    or math.isnan(val)
                    or math.isinf(val)
                    or val < -0.5
                    or val > 10000.0
                ):
                    raise ValueError(
                        f"Coordenada fora dos limites permitidos ({coord_key}={val}) no retângulo {idx}."
                    )


def _validate_strokes(strokes: List[Dict[str, Any]], page: int = 1) -> None:
    """Valida limites de traços, pontos por traço, espessura e coordenadas de desenhos."""
    if len(strokes) > MAX_STROKES_PER_DRAWING:
        raise ValueError(f"Quantidade de traços excede o limite na página {page} (máx: {MAX_STROKES_PER_DRAWING}).")
    for s_idx, s in enumerate(strokes):
        if not isinstance(s, dict):
            raise ValueError(f"Traço inválido na posição {s_idx} da página {page}.")
        width = s.get("width")
        if width is not None and (
            not isinstance(width, (int, float))
            or isinstance(width, bool)
            or math.isnan(width)
            or math.isinf(width)
            or width < 0.5
            or width > 64
        ):
            raise ValueError(f"Espessura do traço inválida ({width}) no desenho da página {page}.")
        points = s.get("points", [])
        if not isinstance(points, list):
            raise ValueError(f"Lista de pontos inválida no traço {s_idx} da página {page}.")
        if len(points) > MAX_POINTS_PER_STROKE:
            raise ValueError(f"Traço com pontos excessivos na página {page} (máx: {MAX_POINTS_PER_STROKE}).")
        for pt in points:
            if not isinstance(pt, dict):
                raise ValueError(f"Ponto inválido no traço {s_idx} da página {page}.")
            px = pt.get("x")
            py = pt.get("y")
            if px is not None and (
                not isinstance(px, (int, float))
                or isinstance(px, bool)
                or math.isnan(px)
                or math.isinf(px)
                or px < -0.5
                or px > 10000.0
            ):
                raise ValueError(f"Ponto X fora dos limites permitidos ({px}) na página {page}.")
            if py is not None and (
                not isinstance(py, (int, float))
                or isinstance(py, bool)
                or math.isnan(py)
                or math.isinf(py)
                or py < -0.5
                or py > 10000.0
            ):
                raise ValueError(f"Ponto Y fora dos limites permitidos ({py}) na página {page}.")


def _validate_visibility(visibility: Optional[str]) -> str:
    """Valida e normaliza o estado de visibilidade ('private' por padrão)."""
    if visibility is None:
        return "private"
    clean = str(visibility).strip().lower()
    if clean not in VALID_VISIBILITIES:
        raise ValueError(f"Visibilidade inválida: '{visibility}'. Valores permitidos: 'private', 'public'.")
    return clean


def _validate_scope(scope: Optional[str]) -> str:
    """Valida o escopo de consulta ('mine', 'public', 'all_visible')."""
    if scope is None:
        return "mine"
    clean = str(scope).strip().lower()
    if clean not in VALID_SCOPES:
        raise ValueError(f"Escopo de consulta inválido: '{scope}'. Valores permitidos: 'mine', 'public', 'all_visible'.")
    return clean


def _validate_epub_cfi(epub_cfi: Optional[str]) -> Optional[str]:
    """Valida tamanho e formato básico de um localizador EPUB CFI."""
    if epub_cfi is None:
        return None
    clean = str(epub_cfi).strip()
    if not clean:
        return None
    if len(clean) > MAX_EPUB_CFI_LEN:
        raise ValueError(f"EPUB CFI excede o tamanho máximo permitido ({MAX_EPUB_CFI_LEN} caracteres).")
    if not (clean.startswith("epubcfi(") and clean.endswith(")")) or "<" in clean or ">" in clean:
        raise ValueError("Formato de EPUB CFI inválido (esperado 'epubcfi(...)').")
    return clean


def _validate_chapter_label(chapter_label: Optional[str]) -> Optional[str]:
    """Valida e higieniza o rótulo opcional de capítulo."""
    if chapter_label is None:
        return None
    clean = str(chapter_label).strip()
    if not clean:
        return None
    if len(clean) > MAX_CHAPTER_LABEL_LEN:
        raise ValueError(f"Rótulo de capítulo excede o limite de {MAX_CHAPTER_LABEL_LEN} caracteres.")
    return clean


def _verify_book_access(cursor, book_id: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Verifica pelo identificador canônico do livro se ele existe, não está ausente do disco
    e pertence a uma fonte ativa, além de validar se o usuário está ativo.
    """
    clean_book_id = str(book_id or "").strip()
    if not clean_book_id:
        raise ValueError("Identificador do livro inválido.")

    if user_id is not None:
        cursor.execute("SELECT id, is_active FROM users WHERE id = ?;", (user_id,))
        u_row = cursor.fetchone()
        if u_row and "is_active" in u_row.keys() and not u_row["is_active"]:
            raise AnnotationPermissionError("Conta de usuário desativada.")

    cursor.execute(
        """
        SELECT b.id, b.title, b.format, b.filename, b.is_missing, s.is_active AS source_active
        FROM books b
        LEFT JOIN library_sources s ON b.source_id = s.id
        WHERE b.id = ?;
        """,
        (clean_book_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Livro '{clean_book_id}' não encontrado.")
    if row["source_active"] is not None and not row["source_active"]:
        raise AnnotationPermissionError(f"A fonte do livro '{clean_book_id}' está desativada.")
    if row["is_missing"]:
        raise ValueError(f"O arquivo do livro '{clean_book_id}' não está disponível no disco.")
    return dict(row)


def _format_author_metadata(
    row_dict: Dict[str, Any],
    viewer_user_id: Optional[int],
    annotation_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Formata metadados públicos de autoria sem expor dados sensíveis do usuário."""
    author_uid = row_dict.get("user_id")
    display_name = (
        row_dict.pop("author_display_name", None)
        or "Leitor"
    )
    avatar_color = row_dict.pop("author_avatar_color", None) or "#d4a853"
    is_own = bool(viewer_user_id is not None and author_uid == viewer_user_id)
    if annotation_type and "type" not in row_dict:
        row_dict["type"] = annotation_type
    row_dict["author_user_id"] = author_uid
    row_dict["author_display_name"] = display_name
    row_dict["author_avatar_color"] = avatar_color
    row_dict["is_own"] = is_own
    row_dict["can_edit"] = is_own
    row_dict["can_delete"] = is_own
    row_dict["can_toggle_visibility"] = is_own
    row_dict["visibility"] = row_dict.get("visibility") or "private"
    return row_dict


def _build_scope_clause(table_alias: str, scope: str, user_id: int) -> tuple[str, List[Any]]:
    """Retorna a cláusula SQL de visibilidade/autoria para o escopo informado."""
    if scope == "mine":
        return f"{table_alias}.user_id = ?", [user_id]
    if scope == "public":
        return f"{table_alias}.visibility = 'public'", []
    # all_visible: combina próprias (privadas e públicas) com públicas de outros usuários sem duplicação
    return f"({table_alias}.user_id = ? OR {table_alias}.visibility = 'public')", [user_id]


# =========================================================================
# MARCA-TEXTO / DESTAQUES (HIGHLIGHTS — PDF & EPUB CFI)
# =========================================================================

def create_highlight(
    book_id: str,
    page: int = 1,
    color: str = "#fef08a",
    rects: Optional[List[Dict[str, float]]] = None,
    selected_text: str = "",
    text_context: Optional[str] = None,
    user_id: int = 1,
    epub_cfi: Optional[str] = None,
    chapter_label: Optional[str] = None,
    visibility: str = "private",
) -> Dict[str, Any]:
    """
    Cria uma nova marcação de texto colorida (com retângulos normalizados para PDF
    ou EPUB CFI para livros EPUB). Privada por padrão.
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("Número de página inválido.")
    clean_cfi = _validate_epub_cfi(epub_cfi)
    clean_chapter = _validate_chapter_label(chapter_label)
    clean_vis = _validate_visibility(visibility)

    if rects is None:
        rects = []
    if not isinstance(rects, list):
        raise ValueError("Formato de retângulos inválido.")
    if not clean_cfi and len(rects) == 0:
        raise ValueError("Pelo menos um retângulo de seleção ou EPUB CFI deve ser fornecido.")
    _validate_rects(rects)

    clean_text = selected_text.strip() if selected_text else ""
    if not clean_text:
        raise ValueError("O texto selecionado não pode ser vazio.")
    if len(clean_text) > MAX_HIGHLIGHT_TEXT_LEN:
        raise ValueError(f"O texto selecionado excede o limite de {MAX_HIGHLIGHT_TEXT_LEN} caracteres.")

    highlight_id = str(uuid.uuid4())
    rects_json = json.dumps(rects)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        cursor.execute(
            """
            INSERT INTO highlights (
                id, book_id, page, color, rects_json, selected_text, text_context,
                user_id, visibility, epub_cfi, chapter_label, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                highlight_id,
                book_id,
                page,
                color or "#fef08a",
                rects_json,
                clean_text,
                text_context,
                user_id,
                clean_vis,
                clean_cfi,
                clean_chapter,
            ),
        )

        return get_highlight_by_id(highlight_id, user_id=user_id, cursor=cursor)


def get_highlight_by_id(
    highlight_id: str,
    user_id: Optional[int] = None,
    cursor=None,
) -> Dict[str, Any]:
    """
    Retorna um destaque pelo ID, garantindo que pertença a um livro acessível e que seja
    do próprio usuário ou público (nunca expondo destaques privados de terceiros).
    """
    def _fetch(c) -> Dict[str, Any]:
        c.execute(
            """
            SELECT h.id, h.book_id, h.page, h.color, h.rects_json, h.selected_text, h.text_context,
                   h.user_id, h.visibility, h.epub_cfi, h.chapter_label, h.created_at, h.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM highlights h
            LEFT JOIN users u ON h.user_id = u.id
            LEFT JOIN user_profiles up ON h.user_id = up.user_id
            WHERE h.id = ?;
            """,
            (highlight_id,),
        )
        row = c.fetchone()
        if not row:
            raise AnnotationNotFoundError(f"Destaque '{highlight_id}' não encontrado.")
        data = dict(row)
        _verify_book_access(c, data["book_id"], user_id=user_id)

        if user_id is not None and data["user_id"] != user_id and data.get("visibility") != "public":
            raise AnnotationNotFoundError(f"Destaque '{highlight_id}' não encontrado.")

        try:
            data["rects"] = json.loads(data["rects_json"] or "[]")
        except Exception:
            data["rects"] = []
        return _format_author_metadata(data, user_id, annotation_type="highlight")

    if cursor is not None:
        return _fetch(cursor)
    with get_db() as conn:
        return _fetch(conn.cursor())


def get_highlights(
    book_id: str,
    page: Optional[int] = None,
    user_id: int = 1,
    scope: str = "mine",
    epub_cfi: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "position",
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Retorna os destaques de um livro respeitando o escopo ('mine', 'public', 'all_visible'),
    com filtros opcionais por página, EPUB CFI, busca textual, ordenação e paginação.
    """
    clean_scope = _validate_scope(scope)
    if sort_by not in VALID_SORT_MODES:
        raise ValueError(f"Modo de ordenação inválido: '{sort_by}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        scope_sql, scope_params = _build_scope_clause("h", clean_scope, user_id)
        sql = f"""
            SELECT h.id, h.book_id, h.page, h.color, h.rects_json, h.selected_text, h.text_context,
                   h.user_id, h.visibility, h.epub_cfi, h.chapter_label, h.created_at, h.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM highlights h
            LEFT JOIN users u ON h.user_id = u.id
            LEFT JOIN user_profiles up ON h.user_id = up.user_id
            WHERE h.book_id = ? AND {scope_sql}
        """
        params: List[Any] = [book_id, *scope_params]

        if page is not None:
            sql += " AND h.page = ?"
            params.append(page)

        if epub_cfi is not None:
            sql += " AND h.epub_cfi = ?"
            params.append(epub_cfi.strip())

        if search and search.strip():
            term = f"%{search.strip()}%"
            sql += (
                " AND (h.selected_text LIKE ? COLLATE NOCASE"
                " OR COALESCE(h.text_context, '') LIKE ? COLLATE NOCASE"
                " OR COALESCE(h.chapter_label, '') LIKE ? COLLATE NOCASE"
                " OR COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, '') LIKE ? COLLATE NOCASE)"
            )
            params.extend([term, term, term, term])

        if sort_by == "recent":
            sql += " ORDER BY h.created_at DESC, h.id ASC"
        else:
            sql += " ORDER BY h.page ASC, h.created_at ASC, h.id ASC"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(1, int(limit)), max(0, int(offset))])

        cursor.execute(sql, params)
        rows = []
        for r in cursor.fetchall():
            item = dict(r)
            try:
                item["rects"] = json.loads(item["rects_json"] or "[]")
            except Exception:
                item["rects"] = []
            rows.append(_format_author_metadata(item, user_id, annotation_type="highlight"))
        return rows


def set_highlight_visibility(
    highlight_id: str,
    visibility: str,
    user_id: int,
    book_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Altera explicitamente a visibilidade ('private' | 'public') de um destaque do próprio usuário."""
    clean_vis = _validate_visibility(visibility)
    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
        cursor.execute(
            "SELECT id, book_id, user_id, visibility FROM highlights WHERE id = ?;",
            (highlight_id,),
        )
        row = cursor.fetchone()
        if not row or (book_id is not None and row["book_id"] != book_id):
            raise AnnotationNotFoundError(f"Destaque '{highlight_id}' não encontrado.")

        _verify_book_access(cursor, row["book_id"], user_id=user_id)

        if row["user_id"] != user_id:
            if row["visibility"] == "private":
                raise AnnotationNotFoundError(f"Destaque '{highlight_id}' não encontrado.")
            raise AnnotationPermissionError("Apenas o autor pode alterar a visibilidade deste destaque.")

        cursor.execute(
            """
            UPDATE highlights
            SET visibility = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?;
            """,
            (clean_vis, highlight_id, user_id),
        )
        return get_highlight_by_id(highlight_id, user_id=user_id, cursor=cursor)


def delete_highlight(
    highlight_id: str,
    user_id: Optional[int] = None,
    book_id: Optional[str] = None,
) -> bool:
    """Remove uma marcação de texto do banco de dados, garantindo que pertença ao autor."""
    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None and user_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
        if user_id is not None and book_id is not None:
            cursor.execute(
                "DELETE FROM highlights WHERE id = ? AND user_id = ? AND book_id = ?",
                (highlight_id, user_id, book_id),
            )
        elif user_id is not None:
            cursor.execute("DELETE FROM highlights WHERE id = ? AND user_id = ?", (highlight_id, user_id))
        else:
            cursor.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
        return cursor.rowcount > 0


# =========================================================================
# ANOTAÇÕES TEXTUAIS (NOTES — PDF & EPUB CFI)
# =========================================================================

def create_note(
    book_id: str,
    page: int = 1,
    content: str = "",
    color: Optional[str] = "#fef08a",
    highlight_id: Optional[str] = None,
    user_id: int = 1,
    epub_cfi: Optional[str] = None,
    chapter_label: Optional[str] = None,
    visibility: str = "private",
) -> Dict[str, Any]:
    """
    Cria uma anotação textual vinculada a um destaque, a uma página PDF ou a um EPUB CFI.
    Privada por padrão.
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("Número de página inválido.")
    clean_content = content.strip() if content else ""
    if not clean_content:
        raise ValueError("O conteúdo da anotação não pode ser vazio.")
    if len(clean_content) > MAX_NOTE_CONTENT_LEN:
        raise ValueError(f"O conteúdo da anotação excede o limite de {MAX_NOTE_CONTENT_LEN} caracteres.")

    clean_cfi = _validate_epub_cfi(epub_cfi)
    clean_chapter = _validate_chapter_label(chapter_label)
    clean_vis = _validate_visibility(visibility)

    note_id = str(uuid.uuid4())

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        if highlight_id:
            cursor.execute(
                """
                SELECT id, epub_cfi, chapter_label
                FROM highlights
                WHERE id = ? AND book_id = ? AND user_id = ?;
                """,
                (highlight_id, book_id, user_id),
            )
            hl_row = cursor.fetchone()
            if not hl_row:
                raise ValueError("Destaque vinculado não encontrado ou não pertence a este usuário.")
            if not clean_cfi and hl_row["epub_cfi"]:
                clean_cfi = hl_row["epub_cfi"]
            if not clean_chapter and hl_row["chapter_label"]:
                clean_chapter = hl_row["chapter_label"]

        cursor.execute(
            """
            INSERT INTO notes (
                id, book_id, page, highlight_id, content, color, user_id,
                visibility, epub_cfi, chapter_label, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                note_id,
                book_id,
                page,
                highlight_id,
                clean_content,
                color or "#fef08a",
                user_id,
                clean_vis,
                clean_cfi,
                clean_chapter,
            ),
        )

        return get_note_by_id(note_id, user_id=user_id, cursor=cursor)


def get_note_by_id(
    note_id: str,
    user_id: Optional[int] = None,
    cursor=None,
    allow_public: bool = False,
) -> Dict[str, Any]:
    """
    Retorna os dados de uma única anotação.
    Por padrão (`allow_public=False`), exige que a nota pertença a `user_id` (quando fornecido).
    Quando `allow_public=True`, permite consultar notas públicas de outros usuários em livros acessíveis,
    nunca expondo notas privadas de terceiros.
    """
    def _fetch(c) -> Dict[str, Any]:
        c.execute(
            """
            SELECT n.id, n.book_id, n.page, n.highlight_id, n.content, n.color, n.user_id,
                   n.visibility, n.epub_cfi, n.chapter_label, n.created_at, n.updated_at,
                   h.selected_text AS highlight_text, h.color AS highlight_color,
                   h.user_id AS highlight_user_id, h.visibility AS highlight_visibility,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM notes n
            LEFT JOIN highlights h ON n.highlight_id = h.id
            LEFT JOIN users u ON n.user_id = u.id
            LEFT JOIN user_profiles up ON n.user_id = up.user_id
            WHERE n.id = ?;
            """,
            (note_id,),
        )
        row = c.fetchone()
        if not row:
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        data = dict(row)
        _verify_book_access(c, data["book_id"], user_id=user_id)

        if user_id is not None:
            if allow_public:
                if data["user_id"] != user_id and data.get("visibility") != "public":
                    raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")
            else:
                if data["user_id"] != user_id:
                    raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        # Nunca vaza texto de um destaque privado caso pertencesse a outro usuário
        hl_uid = data.pop("highlight_user_id", None)
        hl_vis = data.pop("highlight_visibility", None)
        if hl_uid is not None and user_id is not None and hl_uid != user_id and hl_vis != "public" and data["user_id"] != hl_uid:
            data["highlight_text"] = None
            data["highlight_color"] = None

        return _format_author_metadata(data, user_id, annotation_type="note")

    if cursor is not None:
        return _fetch(cursor)
    with get_db() as conn:
        return _fetch(conn.cursor())


def get_notes(
    book_id: str,
    page: Optional[int] = None,
    user_id: int = 1,
    scope: str = "mine",
    epub_cfi: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "position",
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Retorna as anotações do livro respeitando o escopo ('mine', 'public', 'all_visible'),
    com filtros opcionais por página, EPUB CFI, busca textual, ordenação e paginação.
    """
    clean_scope = _validate_scope(scope)
    if sort_by not in VALID_SORT_MODES:
        raise ValueError(f"Modo de ordenação inválido: '{sort_by}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        scope_sql, scope_params = _build_scope_clause("n", clean_scope, user_id)
        sql = f"""
            SELECT n.id, n.book_id, n.page, n.highlight_id, n.content, n.color, n.user_id,
                   n.visibility, n.epub_cfi, n.chapter_label, n.created_at, n.updated_at,
                   h.selected_text AS highlight_text, h.color AS highlight_color,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM notes n
            LEFT JOIN highlights h ON n.highlight_id = h.id
            LEFT JOIN users u ON n.user_id = u.id
            LEFT JOIN user_profiles up ON n.user_id = up.user_id
            WHERE n.book_id = ? AND {scope_sql}
        """
        params: List[Any] = [book_id, *scope_params]

        if page is not None:
            sql += " AND n.page = ?"
            params.append(page)

        if epub_cfi is not None:
            sql += " AND n.epub_cfi = ?"
            params.append(epub_cfi.strip())

        if search and search.strip():
            term = f"%{search.strip()}%"
            sql += (
                " AND (n.content LIKE ? COLLATE NOCASE"
                " OR COALESCE(h.selected_text, '') LIKE ? COLLATE NOCASE"
                " OR COALESCE(n.chapter_label, '') LIKE ? COLLATE NOCASE"
                " OR COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, '') LIKE ? COLLATE NOCASE)"
            )
            params.extend([term, term, term, term])

        if sort_by == "recent":
            sql += " ORDER BY n.created_at DESC, n.id ASC"
        else:
            sql += " ORDER BY n.page ASC, n.created_at ASC, n.id ASC"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(1, int(limit)), max(0, int(offset))])

        cursor.execute(sql, params)
        return [_format_author_metadata(dict(r), user_id, annotation_type="note") for r in cursor.fetchall()]


def update_note(
    note_id: str,
    content: str,
    color: Optional[str] = None,
    user_id: Optional[int] = None,
    visibility: Optional[str] = None,
    chapter_label: Optional[str] = None,
    book_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Atualiza o conteúdo (e opcionalmente cor/visibilidade/capítulo) de uma nota pertencente ao autor."""
    clean_content = content.strip() if content else ""
    if not clean_content:
        raise ValueError("O conteúdo da anotação não pode ser vazio.")
    if len(clean_content) > MAX_NOTE_CONTENT_LEN:
        raise ValueError(f"O conteúdo da anotação excede o limite de {MAX_NOTE_CONTENT_LEN} caracteres.")

    clean_vis = _validate_visibility(visibility) if visibility is not None else None
    clean_chapter = _validate_chapter_label(chapter_label) if chapter_label is not None else None

    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
        cursor.execute("SELECT id, book_id, user_id, color, visibility, chapter_label FROM notes WHERE id = ?;", (note_id,))
        row = cursor.fetchone()
        if not row or (book_id is not None and row["book_id"] != book_id):
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        if user_id is not None and row["user_id"] != user_id:
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        _verify_book_access(cursor, row["book_id"], user_id=user_id)

        final_color = color if color is not None else row["color"]
        final_vis = clean_vis if clean_vis is not None else (row["visibility"] or "private")
        final_chapter = clean_chapter if chapter_label is not None else row["chapter_label"]

        cursor.execute(
            """
            UPDATE notes
            SET content = ?,
                color = ?,
                visibility = ?,
                chapter_label = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (clean_content, final_color, final_vis, final_chapter, note_id),
        )

        return get_note_by_id(note_id, user_id=user_id, cursor=cursor)


def set_note_visibility(
    note_id: str,
    visibility: str,
    user_id: int,
    book_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Altera explicitamente a visibilidade ('private' | 'public') de uma nota do próprio usuário."""
    clean_vis = _validate_visibility(visibility)
    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
        cursor.execute("SELECT id, book_id, user_id, visibility FROM notes WHERE id = ?;", (note_id,))
        row = cursor.fetchone()
        if not row or (book_id is not None and row["book_id"] != book_id):
            raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")

        _verify_book_access(cursor, row["book_id"], user_id=user_id)

        if row["user_id"] != user_id:
            if row["visibility"] == "private":
                raise AnnotationNotFoundError(f"Anotação '{note_id}' não encontrada.")
            raise AnnotationPermissionError("Apenas o autor pode alterar a visibilidade desta nota.")

        cursor.execute(
            """
            UPDATE notes
            SET visibility = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?;
            """,
            (clean_vis, note_id, user_id),
        )
        return get_note_by_id(note_id, user_id=user_id, cursor=cursor)


def delete_note(
    note_id: str,
    user_id: Optional[int] = None,
    book_id: Optional[str] = None,
) -> bool:
    """Remove uma anotação do banco de dados, garantindo que pertença ao autor."""
    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None and user_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
        if user_id is not None and book_id is not None:
            cursor.execute(
                "DELETE FROM notes WHERE id = ? AND user_id = ? AND book_id = ?",
                (note_id, user_id, book_id),
            )
        elif user_id is not None:
            cursor.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        else:
            cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cursor.rowcount > 0


# =========================================================================
# DESENHO LIVRE VETORIAL (PAGE DRAWINGS — PRÓPRIOS E PÚBLICOS)
# =========================================================================

def get_page_drawing(book_id: str, page: int, user_id: int = 1) -> Dict[str, Any]:
    """Retorna os traços vetoriais de desenho de uma página pertencentes ao usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)
        cursor.execute(
            """
            SELECT d.id, d.book_id, d.page, d.strokes_json, d.user_id, d.visibility, d.created_at, d.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM page_drawings d
            LEFT JOIN users u ON d.user_id = u.id
            LEFT JOIN user_profiles up ON d.user_id = up.user_id
            WHERE d.book_id = ? AND d.page = ? AND d.user_id = ?
            """,
            (book_id, page, user_id),
        )
        row = cursor.fetchone()
        if not row:
            return {"type": "drawing", "page": page, "strokes": [], "visibility": "private", "is_own": True}

        data = dict(row)
        try:
            data["strokes"] = json.loads(data["strokes_json"] or "[]")
        except Exception:
            data["strokes"] = []
        return _format_author_metadata(data, user_id, annotation_type="drawing")


def get_page_drawings(
    book_id: str,
    page: Optional[int] = None,
    user_id: int = 1,
    scope: str = "mine",
    sort_by: str = "position",
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Lista os desenhos vetoriais de um livro (e opcionalmente de uma página específica)
    de acordo com o escopo ('mine', 'public', 'all_visible').
    """
    clean_scope = _validate_scope(scope)
    if sort_by not in VALID_SORT_MODES:
        raise ValueError(f"Modo de ordenação inválido: '{sort_by}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        scope_sql, scope_params = _build_scope_clause("d", clean_scope, user_id)
        sql = f"""
            SELECT d.id, d.book_id, d.page, d.strokes_json, d.user_id, d.visibility, d.created_at, d.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM page_drawings d
            LEFT JOIN users u ON d.user_id = u.id
            LEFT JOIN user_profiles up ON d.user_id = up.user_id
            WHERE d.book_id = ? AND {scope_sql}
        """
        params: List[Any] = [book_id, *scope_params]

        if page is not None:
            sql += " AND d.page = ?"
            params.append(page)

        if sort_by == "recent":
            sql += " ORDER BY d.updated_at DESC, d.id ASC"
        else:
            sql += " ORDER BY d.page ASC, d.updated_at ASC, d.id ASC"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(1, int(limit)), max(0, int(offset))])

        cursor.execute(sql, params)
        results = []
        for r in cursor.fetchall():
            item = dict(r)
            try:
                item["strokes"] = json.loads(item["strokes_json"] or "[]")
            except Exception:
                item["strokes"] = []
            item["stroke_count"] = len(item["strokes"])
            results.append(_format_author_metadata(item, user_id, annotation_type="drawing"))
        return results


def get_page_drawings_bundle(book_id: str, page: int, user_id: int = 1) -> Dict[str, Any]:
    """
    Retorna o pacote completo de desenhos de uma página PDF para renderização em camadas:
    - `own_drawing`: o desenho pertencente ao próprio usuário (editável).
    - `public_drawings`: lista de desenhos públicos de outros leitores na mesma página (somente-leitura).
    """
    own = get_page_drawing(book_id=book_id, page=page, user_id=user_id)
    all_public_on_page = get_page_drawings(book_id=book_id, page=page, user_id=user_id, scope="public")
    other_public = [
        d for d in all_public_on_page
        if d.get("user_id") != user_id and len(d.get("strokes") or []) > 0
    ]
    return {
        "page": page,
        "own_drawing": own,
        "public_drawings": other_public,
    }


def save_page_drawing(
    book_id: str,
    page: int,
    strokes: List[Dict[str, Any]],
    user_id: int = 1,
    visibility: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Salva ou atualiza a coleção de traços vetoriais de uma página para o usuário indicado.
    Novos desenhos nascem sempre como 'private' por padrão. Em atualizações de traços,
    preserva a visibilidade atual caso `visibility` não seja informada explicitamente.
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("Número de página inválido.")
    if strokes is not None and not isinstance(strokes, list):
        raise ValueError("A coleção de traços deve ser uma lista.")

    clean_strokes = strokes or []
    _validate_strokes(clean_strokes, page=page)
    strokes_json = json.dumps(clean_strokes)
    drawing_id = str(uuid.uuid4())
    explicit_vis = _validate_visibility(visibility) if visibility is not None else None

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        cursor.execute(
            "SELECT id, visibility FROM page_drawings WHERE book_id = ? AND page = ? AND user_id = ?;",
            (book_id, page, user_id),
        )
        existing = cursor.fetchone()
        final_vis = explicit_vis if explicit_vis is not None else (existing["visibility"] if existing else "private")

        cursor.execute(
            """
            INSERT INTO page_drawings (id, book_id, page, strokes_json, user_id, visibility, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, page, user_id) DO UPDATE SET
                strokes_json = excluded.strokes_json,
                visibility = excluded.visibility,
                updated_at = CURRENT_TIMESTAMP
            """,
            (drawing_id, book_id, page, strokes_json, user_id, final_vis),
        )

        return {
            "status": "success",
            "page": page,
            "stroke_count": len(clean_strokes),
            "visibility": final_vis,
        }


def set_page_drawing_visibility(
    book_id: str,
    page: int,
    visibility: str,
    user_id: int,
    drawing_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Altera explicitamente a visibilidade ('private' | 'public') de um desenho do próprio usuário.
    Rejeita qualquer tentativa de alterar desenhos pertencentes a terceiros.
    """
    clean_vis = _validate_visibility(visibility)
    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        if drawing_id is not None:
            cursor.execute(
                "SELECT id, book_id, page, user_id, visibility FROM page_drawings WHERE id = ?;",
                (drawing_id,),
            )
            row = cursor.fetchone()
            if not row or row["book_id"] != book_id:
                raise AnnotationNotFoundError("Desenho não encontrado.")
            if row["user_id"] != user_id:
                if row["visibility"] == "private":
                    raise AnnotationNotFoundError("Desenho não encontrado.")
                raise AnnotationPermissionError("Apenas o autor pode alterar a visibilidade deste desenho.")
            target_page = row["page"]
        else:
            cursor.execute(
                "SELECT id, user_id, visibility FROM page_drawings WHERE book_id = ? AND page = ? AND user_id = ?;",
                (book_id, page, user_id),
            )
            row = cursor.fetchone()
            if not row:
                raise AnnotationNotFoundError(f"Nenhum desenho encontrado na página {page} para este usuário.")
            target_page = page

        cursor.execute(
            """
            UPDATE page_drawings
            SET visibility = ?, updated_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND page = ? AND user_id = ?;
            """,
            (clean_vis, book_id, target_page, user_id),
        )

    return get_page_drawing(book_id=book_id, page=target_page, user_id=user_id)


def delete_page_drawing(book_id: str, page: int, user_id: int = 1) -> bool:
    """Limpa todos os traços de desenho de uma página pertencentes exclusivamente ao usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM page_drawings WHERE book_id = ? AND page = ? AND user_id = ?",
            (book_id, page, user_id),
        )
        return cursor.rowcount > 0


# =========================================================================
# MARCADORES PESSOAIS DE LEITURA (BOOKMARKS — SEMPRE PRIVADOS)
# =========================================================================

def create_bookmark(
    book_id: str,
    user_id: int,
    page: int = 1,
    epub_cfi: Optional[str] = None,
    chapter_label: Optional[str] = None,
    label: str = "",
) -> Dict[str, Any]:
    """
    Cria ou atualiza (sem duplicar na mesma posição) um marcador pessoal de leitura
    para uma página PDF ou posição EPUB CFI. Marcadores são estritamente privados.
    """
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("Número de página inválido.")
    clean_cfi = _validate_epub_cfi(epub_cfi)
    clean_chapter = _validate_chapter_label(chapter_label)
    clean_label = str(label or "").strip()
    if len(clean_label) > MAX_BOOKMARK_LABEL_LEN:
        raise ValueError(f"O rótulo do marcador excede o limite de {MAX_BOOKMARK_LABEL_LEN} caracteres.")

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        cursor.execute(
            """
            SELECT id FROM bookmarks
            WHERE book_id = ? AND user_id = ? AND page = ? AND COALESCE(epub_cfi, '') = ?;
            """,
            (book_id, user_id, page, clean_cfi or ""),
        )
        existing = cursor.fetchone()
        if existing:
            bm_id = existing["id"]
            cursor.execute(
                """
                UPDATE bookmarks
                SET label = ?, chapter_label = COALESCE(?, chapter_label), updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?;
                """,
                (clean_label, clean_chapter, bm_id, user_id),
            )
        else:
            bm_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO bookmarks (id, book_id, user_id, page, epub_cfi, chapter_label, label, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """,
                (bm_id, book_id, user_id, page, clean_cfi, clean_chapter, clean_label),
            )

        cursor.execute(
            """
            SELECT id, book_id, user_id, page, epub_cfi, chapter_label, label, created_at, updated_at
            FROM bookmarks WHERE id = ? AND user_id = ?;
            """,
            (bm_id, user_id),
        )
        bm_data = dict(cursor.fetchone())
        bm_data["type"] = "bookmark"
        return bm_data


def list_bookmarks(
    book_id: str,
    user_id: int,
    limit: Optional[int] = None,
    offset: int = 0,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Lista os marcadores pessoais do usuário autenticado para o livro."""
    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)

        sql = """
            SELECT id, book_id, user_id, page, epub_cfi, chapter_label, label, created_at, updated_at
            FROM bookmarks
            WHERE book_id = ? AND user_id = ?
        """
        params: List[Any] = [book_id, user_id]
        if search and search.strip():
            term = f"%{search.strip()}%"
            sql += " AND (COALESCE(label, '') LIKE ? COLLATE NOCASE OR COALESCE(chapter_label, '') LIKE ? COLLATE NOCASE OR CAST(page AS TEXT) LIKE ?)"
            params.extend([term, term, term])

        sql += " ORDER BY page ASC, created_at ASC, id ASC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([max(1, int(limit)), max(0, int(offset))])

        cursor.execute(sql, params)
        rows = []
        for r in cursor.fetchall():
            item = dict(r)
            item["type"] = "bookmark"
            rows.append(item)
        return rows


def delete_bookmark(
    bookmark_id: str,
    user_id: int,
    book_id: Optional[str] = None,
) -> bool:
    """Remove um marcador pessoal pertencente estritamente ao usuário autenticado."""
    with get_db() as conn:
        cursor = conn.cursor()
        if book_id is not None:
            _verify_book_access(cursor, book_id, user_id=user_id)
            cursor.execute(
                "DELETE FROM bookmarks WHERE id = ? AND user_id = ? AND book_id = ?;",
                (bookmark_id, user_id, book_id),
            )
        else:
            cursor.execute(
                "DELETE FROM bookmarks WHERE id = ? AND user_id = ?;",
                (bookmark_id, user_id),
            )
        return cursor.rowcount > 0


# =========================================================================
# RESUMO DAS ANOTAÇÕES DO LIVRO (PARA O CADERNO DE ESTUDOS COLABORATIVO)
# =========================================================================

def get_book_annotations_summary(
    book_id: str,
    user_id: int = 1,
    scope: str = "mine",
    epub_cfi: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "position",
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Retorna as marcações, anotações, páginas com desenhos (sem carregar `strokes_json` pesado)
    e marcadores pessoais para o painel de estudos do leitor, respeitando o escopo solicitado.
    """
    clean_scope = _validate_scope(scope)
    highlights = get_highlights(
        book_id,
        user_id=user_id,
        scope=clean_scope,
        epub_cfi=epub_cfi,
        search=search,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    notes = get_notes(
        book_id,
        user_id=user_id,
        scope=clean_scope,
        epub_cfi=epub_cfi,
        search=search,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_book_access(cursor, book_id, user_id=user_id)
        scope_sql, scope_params = _build_scope_clause("d", clean_scope, user_id)

        order_clause = "d.updated_at DESC, d.id ASC" if sort_by == "recent" else "d.page ASC, d.updated_at ASC, d.id ASC"
        draw_where = f"d.book_id = ? AND {scope_sql} AND d.strokes_json != '[]'"
        draw_params: List[Any] = [book_id, *scope_params]
        if search and search.strip():
            term = f"%{search.strip()}%"
            draw_where += (
                " AND (CAST(d.page AS TEXT) LIKE ?"
                " OR COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, '') LIKE ? COLLATE NOCASE)"
            )
            draw_params.extend([term, term])

        draw_sql = f"""
            SELECT d.id, d.book_id, d.page, d.user_id, d.visibility, d.created_at, d.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name,
                   COALESCE(NULLIF(TRIM(up.avatar_color), ''), '#d4a853') AS author_avatar_color
            FROM page_drawings d
            LEFT JOIN users u ON d.user_id = u.id
            LEFT JOIN user_profiles up ON d.user_id = up.user_id
            WHERE {draw_where}
            ORDER BY {order_clause}
        """
        if limit is not None:
            draw_sql += " LIMIT ? OFFSET ?"
            draw_params.extend([max(1, int(limit)), max(0, int(offset))])

        cursor.execute(draw_sql, draw_params)
        drawing_pages = [_format_author_metadata(dict(r), user_id, annotation_type="drawing") for r in cursor.fetchall()]

        bookmarks = list_bookmarks(book_id, user_id=user_id, limit=limit, offset=offset, search=search)

        if limit is not None:
            # Contagem total consistente com o escopo e busca para paginação incremental
            hl_scope_sql, hl_scope_params = _build_scope_clause("h", clean_scope, user_id)
            hl_count_sql = f"""
                SELECT COUNT(*) FROM highlights h
                LEFT JOIN users u ON h.user_id = u.id
                LEFT JOIN user_profiles up ON h.user_id = up.user_id
                WHERE h.book_id = ? AND {hl_scope_sql}
            """
            hl_count_params: List[Any] = [book_id, *hl_scope_params]
            if epub_cfi is not None:
                hl_count_sql += " AND h.epub_cfi = ?"
                hl_count_params.append(epub_cfi.strip())
            if search and search.strip():
                term = f"%{search.strip()}%"
                hl_count_sql += (
                    " AND (h.selected_text LIKE ? COLLATE NOCASE"
                    " OR COALESCE(h.text_context, '') LIKE ? COLLATE NOCASE"
                    " OR COALESCE(h.chapter_label, '') LIKE ? COLLATE NOCASE"
                    " OR COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, '') LIKE ? COLLATE NOCASE)"
                )
                hl_count_params.extend([term, term, term, term])
            cursor.execute(hl_count_sql, hl_count_params)
            total_hl = int(cursor.fetchone()[0])

            nt_scope_sql, nt_scope_params = _build_scope_clause("n", clean_scope, user_id)
            nt_count_sql = f"""
                SELECT COUNT(*) FROM notes n
                LEFT JOIN highlights h ON n.highlight_id = h.id
                LEFT JOIN users u ON n.user_id = u.id
                LEFT JOIN user_profiles up ON n.user_id = up.user_id
                WHERE n.book_id = ? AND {nt_scope_sql}
            """
            nt_count_params: List[Any] = [book_id, *nt_scope_params]
            if epub_cfi is not None:
                nt_count_sql += " AND n.epub_cfi = ?"
                nt_count_params.append(epub_cfi.strip())
            if search and search.strip():
                term = f"%{search.strip()}%"
                nt_count_sql += (
                    " AND (n.content LIKE ? COLLATE NOCASE"
                    " OR COALESCE(h.selected_text, '') LIKE ? COLLATE NOCASE"
                    " OR COALESCE(n.chapter_label, '') LIKE ? COLLATE NOCASE"
                    " OR COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, '') LIKE ? COLLATE NOCASE)"
                )
                nt_count_params.extend([term, term, term, term])
            cursor.execute(nt_count_sql, nt_count_params)
            total_nt = int(cursor.fetchone()[0])

            dr_count_sql = f"""
                SELECT COUNT(*) FROM page_drawings d
                LEFT JOIN users u ON d.user_id = u.id
                LEFT JOIN user_profiles up ON d.user_id = up.user_id
                WHERE {draw_where}
            """
            dr_count_params = [book_id, *scope_params]
            if search and search.strip():
                term = f"%{search.strip()}%"
                dr_count_params.extend([term, term])
            cursor.execute(dr_count_sql, dr_count_params)
            total_dr = int(cursor.fetchone()[0])

            bm_count_sql = "SELECT COUNT(*) FROM bookmarks WHERE book_id = ? AND user_id = ?"
            bm_count_params: List[Any] = [book_id, user_id]
            if search and search.strip():
                term = f"%{search.strip()}%"
                bm_count_sql += " AND (COALESCE(label, '') LIKE ? COLLATE NOCASE OR COALESCE(chapter_label, '') LIKE ? COLLATE NOCASE OR CAST(page AS TEXT) LIKE ?)"
                bm_count_params.extend([term, term, term])
            cursor.execute(bm_count_sql, bm_count_params)
            total_bm = int(cursor.fetchone()[0])

            effective_offset = max(0, int(offset))
            has_more = (
                (effective_offset + len(highlights) < total_hl)
                or (effective_offset + len(notes) < total_nt)
                or (effective_offset + len(drawing_pages) < total_dr)
                or (effective_offset + len(bookmarks) < total_bm)
            )
            total_counts = {
                "highlights": total_hl,
                "notes": total_nt,
                "drawings": total_dr,
                "bookmarks": total_bm,
            }
        else:
            has_more = False
            total_counts = {
                "highlights": len(highlights),
                "notes": len(notes),
                "drawings": len(drawing_pages),
                "bookmarks": len(bookmarks),
            }

    return {
        "scope": clean_scope,
        "highlights": highlights,
        "notes": notes,
        "drawing_pages": drawing_pages,
        "bookmarks": bookmarks,
        "has_more": has_more,
        "total_counts": total_counts,
        "counts": {
            "highlights": len(highlights),
            "notes": len(notes),
            "drawings": len(drawing_pages),
            "bookmarks": len(bookmarks),
        },
    }
