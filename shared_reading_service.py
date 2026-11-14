"""
Módulo de Leitura Compartilhada — Lectoria v1.7.0.
Gerencia as operações colaborativas dentro de leituras coletivas:
- Discussões de leitura (`collective_discussion_topics` e `collective_discussion_posts`)
- Metas e cronogramas coletivos (`collective_reading_goals`)
- Progresso voluntário individual (`collective_goal_progress`) — estritamente desacoplado do progresso privado
- Compartilhamento voluntário de citações (`collective_shared_quotes`) com limites e proteção anti-IDOR
- Controle de spoilers, moderação por papéis ('owner', 'moderator', 'member') e anti-enumeração
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from clubs_service import (
    ClubError,
    ClubPermissionError,
    CollectiveReadNotFoundError,
    InvalidOperationError,
    get_collective_read,
)
from database import get_db

logger = logging.getLogger("lectoria.shared_reading")


# =========================================================================
# LIMITES E CONSTANTES
# =========================================================================

MAX_TOPIC_TITLE_LEN = 200
MAX_TOPIC_CONTENT_LEN = 10000
MAX_POST_CONTENT_LEN = 5000
MAX_CHAPTER_REF_LEN = 150
MAX_EPUB_CFI_LEN = 500
MAX_GOAL_TITLE_LEN = 150
MAX_GOAL_DESC_LEN = 2000
MAX_QUOTE_TEXT_LEN = 1500
MAX_QUOTE_COMMENT_LEN = 2000
MAX_THREAD_DEPTH = 1

SPOILER_MASK_TEXT = "[Conteúdo marcado como spoiler]"
DELETED_CONTENT_TEXT = "[Conteúdo removido]"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# =========================================================================
# EXCEÇÕES ESPECÍFICAS
# =========================================================================

class TopicNotFoundError(ClubError):
    """Lançada quando um tópico de discussão não é encontrado ou não pertence à leitura."""
    pass


class PostNotFoundError(ClubError):
    """Lançada quando uma resposta não é encontrada ou não pertence ao tópico."""
    pass


class GoalNotFoundError(ClubError):
    """Lançada quando uma meta não é encontrada ou não pertence à leitura."""
    pass


class QuoteNotFoundError(ClubError):
    """Lançada quando uma citação não é encontrada ou não pertence à leitura."""
    pass


# =========================================================================
# HELPERS INTERNOS DE AUTORIZAÇÃO E VALIDAÇÃO
# =========================================================================

def _require_collective_read_access(
    cursor,
    read_id: str,
    user_id: int,
    require_club_membership: bool = True,
    require_moderator: bool = False,
    allow_archived_write: bool = False,
    is_write_op: bool = False,
) -> Dict[str, Any]:
    """
    Valida a existência da leitura coletiva e as permissões do usuário no clube organizador.
    - Clubes privados sem vínculo do usuário retornam CollectiveReadNotFoundError (404 anti-enumeração).
    - Clubes públicos sem vínculo do usuário retornam ClubPermissionError (403) para operações de leitura coletiva.
    """
    cursor.execute(
        """
        SELECT
            r.id, r.club_id, r.book_id, r.title, r.status,
            c.visibility AS club_visibility,
            c.is_archived AS club_is_archived
        FROM collective_reads r
        JOIN reading_clubs c ON r.club_id = c.id
        WHERE r.id = ?;
        """,
        (read_id,),
    )
    read_row = cursor.fetchone()
    if not read_row:
        raise CollectiveReadNotFoundError("Leitura coletiva não encontrada.")

    read_info = dict(read_row)

    cursor.execute(
        "SELECT role FROM reading_club_members WHERE club_id = ? AND user_id = ?;",
        (read_info["club_id"], user_id),
    )
    mem_row = cursor.fetchone()
    user_role = mem_row["role"] if mem_row else None
    is_club_member = mem_row is not None

    if read_info["club_visibility"] == "private" and not is_club_member:
        raise CollectiveReadNotFoundError("Leitura coletiva não encontrada.")

    if require_club_membership and not is_club_member:
        raise ClubPermissionError("Apenas membros do clube podem acessar esta leitura coletiva.")

    if require_moderator and user_role not in ("owner", "moderator"):
        raise ClubPermissionError("Apenas proprietários ou moderadores podem realizar esta operação.")

    if is_write_op and not allow_archived_write:
        if read_info["club_is_archived"]:
            raise InvalidOperationError("Este clube está arquivado e em modo somente leitura.")
        if read_info["status"] in ("cancelled", "completed"):
            state_label = "cancelada" if read_info["status"] == "cancelled" else "concluída"
            raise InvalidOperationError(f"Esta leitura coletiva foi {state_label} e está em modo somente leitura.")

    read_info["user_club_role"] = user_role
    read_info["is_club_member"] = is_club_member
    return read_info


def _validate_page_range(page_start: Optional[int], page_end: Optional[int]) -> None:
    if page_start is not None:
        if not isinstance(page_start, int) or page_start < 1:
            raise InvalidOperationError("A página inicial deve ser um número inteiro maior ou igual a 1.")
    if page_end is not None:
        if not isinstance(page_end, int) or page_end < 1:
            raise InvalidOperationError("A página final deve ser um número inteiro maior ou igual a 1.")
        if page_start is None:
            raise InvalidOperationError("Informe a página inicial antes da página final.")
        if page_end < page_start:
            raise InvalidOperationError("A página final não pode ser menor que a página inicial.")


def _validate_iso_date(date_str: Optional[str]) -> Optional[str]:
    if date_str is None:
        return None
    clean = date_str.strip()
    if not clean:
        return None
    if not DATE_RE.match(clean):
        raise InvalidOperationError("A data-alvo deve estar no formato YYYY-MM-DD.")
    try:
        datetime.strptime(clean, "%Y-%m-%d")
    except ValueError:
        raise InvalidOperationError("Data-alvo inválida.")
    return clean


def _make_preview(text: str, is_spoiler: bool, is_deleted: bool, max_len: int = 160) -> str:
    if is_deleted:
        return DELETED_CONTENT_TEXT
    if is_spoiler:
        return SPOILER_MASK_TEXT
    clean = (text or "").strip().replace("\n", " ")
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


def _normalize_pagination(page: int, limit: int, max_limit: int = 50) -> tuple[int, int, int]:
    p = max(1, int(page or 1))
    l = max(1, min(max_limit, int(limit or 20)))
    offset = (p - 1) * l
    return p, l, offset


# =========================================================================
# VISÃO GERAL DA LEITURA COMPARTILHADA
# =========================================================================

def get_shared_reading_overview(read_id: str, user_id: int) -> Dict[str, Any]:
    """
    Retorna o painel resumido da leitura coletiva (dados da obra, clube,
    contadores de tópicos/metas/citações e prévias seguras das atividades recentes).
    Exige vínculo com o clube organizador.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(cursor, read_id, user_id, require_club_membership=True)

    read_details = get_collective_read(read_id, user_id)

    with get_db() as conn:
        cursor = conn.cursor()

        topics_count = cursor.execute(
            "SELECT COUNT(*) FROM collective_discussion_topics WHERE read_id = ? AND is_deleted = 0;",
            (read_id,),
        ).fetchone()[0]

        goals_count = cursor.execute(
            "SELECT COUNT(*) FROM collective_reading_goals WHERE read_id = ? AND is_archived = 0;",
            (read_id,),
        ).fetchone()[0]

        quotes_count = cursor.execute(
            "SELECT COUNT(*) FROM collective_shared_quotes WHERE read_id = ? AND is_deleted = 0;",
            (read_id,),
        ).fetchone()[0]

        recent_topics_rows = cursor.execute(
            """
            SELECT
                t.id, t.title, t.is_spoiler, t.is_pinned, t.is_locked, t.created_at, t.updated_at,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_name,
                (SELECT COUNT(*) FROM collective_discussion_posts p WHERE p.topic_id = t.id AND p.is_deleted = 0) AS posts_count
            FROM collective_discussion_topics t
            LEFT JOIN users u ON t.user_id = u.id
            WHERE t.read_id = ? AND t.is_deleted = 0
            ORDER BY t.is_pinned DESC, t.updated_at DESC
            LIMIT 5;
            """,
            (read_id,),
        ).fetchall()

        upcoming_goals_rows = cursor.execute(
            """
            SELECT
                g.id, g.title, g.target_date, g.chapter_ref, g.target_page,
                COALESCE((SELECT status FROM collective_goal_progress gp WHERE gp.goal_id = g.id AND gp.user_id = ?), 'pending') AS my_status,
                (SELECT COUNT(*) FROM collective_goal_progress gp WHERE gp.goal_id = g.id AND gp.status = 'completed') AS completed_count
            FROM collective_reading_goals g
            WHERE g.read_id = ? AND g.is_archived = 0
            ORDER BY g.display_order ASC, COALESCE(g.target_date, '9999-12-31') ASC, g.created_at ASC
            LIMIT 3;
            """,
            (user_id, read_id),
        ).fetchall()

    return {
        "read": read_details,
        "counts": {
            "topics": topics_count,
            "goals": goals_count,
            "quotes": quotes_count,
        },
        "recent_topics": [dict(r) for r in recent_topics_rows],
        "upcoming_goals": [dict(r) for r in upcoming_goals_rows],
    }


# =========================================================================
# DISCUSSÕES: TÓPICOS
# =========================================================================

def create_topic(
    read_id: str,
    user_id: int,
    title: str,
    content: str,
    chapter_ref: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    epub_cfi: Optional[str] = None,
    is_spoiler: bool = False,
) -> Dict[str, Any]:
    """Cria um novo tópico de discussão em uma leitura coletiva."""
    clean_title = (title or "").strip()
    if not clean_title:
        raise InvalidOperationError("O título do tópico é obrigatório.")
    if len(clean_title) > MAX_TOPIC_TITLE_LEN:
        raise InvalidOperationError(f"O título deve ter no máximo {MAX_TOPIC_TITLE_LEN} caracteres.")

    clean_content = (content or "").strip()
    if not clean_content:
        raise InvalidOperationError("O conteúdo do tópico é obrigatório.")
    if len(clean_content) > MAX_TOPIC_CONTENT_LEN:
        raise InvalidOperationError(f"O conteúdo deve ter no máximo {MAX_TOPIC_CONTENT_LEN} caracteres.")

    clean_chapter = (chapter_ref or "").strip()[:MAX_CHAPTER_REF_LEN] or None
    clean_cfi = (epub_cfi or "").strip()[:MAX_EPUB_CFI_LEN] or None
    _validate_page_range(page_start, page_end)

    spoiler_flag = 1 if is_spoiler else 0
    topic_id = uuid.uuid4().hex

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )
        cursor.execute(
            """
            INSERT INTO collective_discussion_topics (
                id, read_id, user_id, title, content,
                chapter_ref, page_start, page_end, epub_cfi, is_spoiler
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                topic_id, read_id, user_id, clean_title, clean_content,
                clean_chapter, page_start, page_end, clean_cfi, spoiler_flag,
            ),
        )

    return get_topic_detail(read_id, topic_id, user_id)["topic"]


def list_topics(
    read_id: str,
    user_id: int,
    page: int = 1,
    limit: int = 20,
    filter_mode: str = "all",
) -> Dict[str, Any]:
    """
    Lista os tópicos da leitura coletiva com paginação e proteção de spoilers nas prévias.
    """
    p, l, offset = _normalize_pagination(page, limit, max_limit=50)
    mode = (filter_mode or "all").strip().lower()

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(cursor, read_id, user_id, require_club_membership=True)

        where_clauses = ["t.read_id = ?", "t.is_deleted = 0"]
        params: List[Any] = [read_id]

        if mode == "pinned":
            where_clauses.append("t.is_pinned = 1")
        elif mode == "mine":
            where_clauses.append("t.user_id = ?")
            params.append(user_id)

        where_sql = " AND ".join(where_clauses)

        total = cursor.execute(
            f"SELECT COUNT(*) FROM collective_discussion_topics t WHERE {where_sql};",
            params,
        ).fetchone()[0]

        rows = cursor.execute(
            f"""
            SELECT
                t.id, t.read_id, t.user_id, t.title, t.content,
                t.chapter_ref, t.page_start, t.page_end, t.epub_cfi,
                t.is_spoiler, t.is_pinned, t.is_locked, t.is_deleted,
                t.created_at, t.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(p.avatar_color, '#8b7355') AS author_avatar_color,
                (SELECT COUNT(*) FROM collective_discussion_posts dp WHERE dp.topic_id = t.id AND dp.is_deleted = 0) AS posts_count
            FROM collective_discussion_topics t
            LEFT JOIN users u ON t.user_id = u.id
            LEFT JOIN user_profiles p ON t.user_id = p.user_id
            WHERE {where_sql}
            ORDER BY t.is_pinned DESC, t.updated_at DESC, t.created_at DESC
            LIMIT ? OFFSET ?;
            """,
            params + [l, offset],
        ).fetchall()

        topics = []
        for row in rows:
            item = dict(row)
            is_sp = bool(item["is_spoiler"])
            is_del = bool(item["is_deleted"])
            item["is_spoiler"] = is_sp
            item["is_pinned"] = bool(item["is_pinned"])
            item["is_locked"] = bool(item["is_locked"])
            item["is_deleted"] = is_del
            item["content_preview"] = _make_preview(item["content"], is_sp, is_del)
            # Não expõe corpo completo na listagem quando é spoiler
            if is_sp:
                item.pop("content", None)
            topics.append(item)

        return {
            "topics": topics,
            "total": total,
            "page": p,
            "limit": l,
            "has_more": (offset + len(topics)) < total,
        }


def get_topic_detail(
    read_id: str,
    topic_id: str,
    user_id: int,
    page: int = 1,
    limit: int = 50,
) -> Dict[str, Any]:
    """Obtém os detalhes do tópico e suas respostas paginadas."""
    p, l, offset = _normalize_pagination(page, limit, max_limit=100)

    with get_db() as conn:
        cursor = conn.cursor()
        read_info = _require_collective_read_access(cursor, read_id, user_id, require_club_membership=True)

        topic_row = cursor.execute(
            """
            SELECT
                t.id, t.read_id, t.user_id, t.title, t.content,
                t.chapter_ref, t.page_start, t.page_end, t.epub_cfi,
                t.is_spoiler, t.is_pinned, t.is_locked, t.is_deleted,
                t.created_at, t.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(p.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_discussion_topics t
            LEFT JOIN users u ON t.user_id = u.id
            LEFT JOIN user_profiles p ON t.user_id = p.user_id
            WHERE t.id = ? AND t.read_id = ?;
            """,
            (topic_id, read_id),
        ).fetchone()

        if not topic_row:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")

        topic = dict(topic_row)
        topic["is_spoiler"] = bool(topic["is_spoiler"])
        topic["is_pinned"] = bool(topic["is_pinned"])
        topic["is_locked"] = bool(topic["is_locked"])
        topic["is_deleted"] = bool(topic["is_deleted"])

        if topic["is_deleted"]:
            topic["title"] = "[Tópico removido]"
            topic["content"] = DELETED_CONTENT_TEXT

        total_posts = cursor.execute(
            "SELECT COUNT(*) FROM collective_discussion_posts WHERE topic_id = ?;",
            (topic_id,),
        ).fetchone()[0]

        posts_rows = cursor.execute(
            """
            SELECT
                dp.id, dp.topic_id, dp.user_id, dp.parent_post_id, dp.content,
                dp.is_spoiler, dp.is_deleted, dp.created_at, dp.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(up.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_discussion_posts dp
            LEFT JOIN users u ON dp.user_id = u.id
            LEFT JOIN user_profiles up ON dp.user_id = up.user_id
            WHERE dp.topic_id = ?
            ORDER BY dp.created_at ASC
            LIMIT ? OFFSET ?;
            """,
            (topic_id, l, offset),
        ).fetchall()

        posts = []
        for prow in posts_rows:
            post = dict(prow)
            post["is_spoiler"] = bool(post["is_spoiler"])
            post["is_deleted"] = bool(post["is_deleted"])
            post["depth"] = 1 if post["parent_post_id"] is not None else 0
            if post["is_deleted"]:
                post["content"] = DELETED_CONTENT_TEXT
                post["is_spoiler"] = False
            posts.append(post)

        return {
            "topic": topic,
            "posts": posts,
            "total_posts": total_posts,
            "page": p,
            "limit": l,
            "has_more": (offset + len(posts)) < total_posts,
            "user_club_role": read_info["user_club_role"],
        }


def edit_topic(
    read_id: str,
    topic_id: str,
    user_id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
    chapter_ref: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    epub_cfi: Optional[str] = None,
    is_spoiler: Optional[bool] = None,
) -> Dict[str, Any]:
    """Permite ao autor editar o próprio tópico (desde que não esteja bloqueado ou excluído)."""
    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        row = cursor.execute(
            "SELECT * FROM collective_discussion_topics WHERE id = ? AND read_id = ?;",
            (topic_id, read_id),
        ).fetchone()
        if not row:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")

        topic = dict(row)
        if topic["is_deleted"]:
            raise InvalidOperationError("Não é possível editar um tópico excluído.")
        if topic["user_id"] != user_id:
            raise ClubPermissionError("Apenas o autor pode editar o conteúdo deste tópico.")
        if topic["is_locked"]:
            raise InvalidOperationError("Este tópico está bloqueado para edições.")

        new_title = topic["title"]
        if title is not None:
            clean_t = title.strip()
            if not clean_t:
                raise InvalidOperationError("O título do tópico não pode ficar vazio.")
            if len(clean_t) > MAX_TOPIC_TITLE_LEN:
                raise InvalidOperationError(f"O título deve ter no máximo {MAX_TOPIC_TITLE_LEN} caracteres.")
            new_title = clean_t

        new_content = topic["content"]
        if content is not None:
            clean_c = content.strip()
            if not clean_c:
                raise InvalidOperationError("O conteúdo do tópico não pode ficar vazio.")
            if len(clean_c) > MAX_TOPIC_CONTENT_LEN:
                raise InvalidOperationError(f"O conteúdo deve ter no máximo {MAX_TOPIC_CONTENT_LEN} caracteres.")
            new_content = clean_c

        new_chapter = topic["chapter_ref"] if chapter_ref is None else (chapter_ref.strip()[:MAX_CHAPTER_REF_LEN] or None)
        new_cfi = topic["epub_cfi"] if epub_cfi is None else (epub_cfi.strip()[:MAX_EPUB_CFI_LEN] or None)

        new_p_start = topic["page_start"] if page_start is None else page_start
        new_p_end = topic["page_end"] if page_end is None else page_end
        _validate_page_range(new_p_start, new_p_end)

        new_spoiler = topic["is_spoiler"] if is_spoiler is None else (1 if is_spoiler else 0)

        cursor.execute(
            """
            UPDATE collective_discussion_topics
            SET title = ?, content = ?, chapter_ref = ?, page_start = ?, page_end = ?,
                epub_cfi = ?, is_spoiler = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND read_id = ?;
            """,
            (new_title, new_content, new_chapter, new_p_start, new_p_end, new_cfi, new_spoiler, topic_id, read_id),
        )

    return get_topic_detail(read_id, topic_id, user_id)["topic"]


def moderate_topic(
    read_id: str,
    topic_id: str,
    user_id: int,
    is_pinned: Optional[bool] = None,
    is_locked: Optional[bool] = None,
    is_deleted: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Aplica ações de moderação em um tópico:
    - Fixar/desafixar (is_pinned) e bloquear/desbloquear (is_locked): apenas owner/moderator.
    - Exclusão lógica (is_deleted): autor do tópico OU owner/moderator.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        read_info = _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True, allow_archived_write=False
        )

        row = cursor.execute(
            "SELECT * FROM collective_discussion_topics WHERE id = ? AND read_id = ?;",
            (topic_id, read_id),
        ).fetchone()
        if not row:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")

        topic = dict(row)
        is_mod = read_info["user_club_role"] in ("owner", "moderator")
        is_author = topic["user_id"] == user_id

        if is_pinned is not None or is_locked is not None:
            if not is_mod:
                raise ClubPermissionError("Apenas proprietários ou moderadores podem fixar ou bloquear tópicos.")

        if is_deleted is not None:
            if not (is_mod or is_author):
                raise ClubPermissionError("Você não tem permissão para remover este tópico.")

        new_pinned = topic["is_pinned"] if is_pinned is None else (1 if is_pinned else 0)
        new_locked = topic["is_locked"] if is_locked is None else (1 if is_locked else 0)
        new_deleted = topic["is_deleted"] if is_deleted is None else (1 if is_deleted else 0)

        cursor.execute(
            """
            UPDATE collective_discussion_topics
            SET is_pinned = ?, is_locked = ?, is_deleted = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND read_id = ?;
            """,
            (new_pinned, new_locked, new_deleted, topic_id, read_id),
        )

    return get_topic_detail(read_id, topic_id, user_id)["topic"]


# =========================================================================
# DISCUSSÕES: RESPOSTAS (POSTS)
# =========================================================================

def create_post(
    read_id: str,
    topic_id: str,
    user_id: int,
    content: str,
    parent_post_id: Optional[str] = None,
    is_spoiler: bool = False,
) -> Dict[str, Any]:
    """Cria uma resposta em um tópico, com suporte a encadeamento de profundidade máxima = 1."""
    clean_content = (content or "").strip()
    if not clean_content:
        raise InvalidOperationError("O conteúdo da resposta é obrigatório.")
    if len(clean_content) > MAX_POST_CONTENT_LEN:
        raise InvalidOperationError(f"A resposta deve ter no máximo {MAX_POST_CONTENT_LEN} caracteres.")

    post_id = uuid.uuid4().hex
    spoiler_flag = 1 if is_spoiler else 0

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        topic = cursor.execute(
            "SELECT id, is_locked, is_deleted FROM collective_discussion_topics WHERE id = ? AND read_id = ?;",
            (topic_id, read_id),
        ).fetchone()
        if not topic:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")
        if topic["is_deleted"]:
            raise InvalidOperationError("Não é possível responder a um tópico excluído.")
        if topic["is_locked"]:
            raise InvalidOperationError("Este tópico está bloqueado para novas respostas.")

        clean_parent_id = (parent_post_id or "").strip() or None
        if clean_parent_id:
            parent_row = cursor.execute(
                "SELECT id, topic_id, parent_post_id FROM collective_discussion_posts WHERE id = ? AND topic_id = ?;",
                (clean_parent_id, topic_id),
            ).fetchone()
            if not parent_row:
                raise PostNotFoundError("A mensagem original citada na resposta não pertence a este tópico.")
            if parent_row["parent_post_id"] is not None:
                raise InvalidOperationError("Profundidade máxima de encadeamento atingida (máximo 1 nível).")

        cursor.execute(
            """
            INSERT INTO collective_discussion_posts (
                id, topic_id, user_id, parent_post_id, content, is_spoiler
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (post_id, topic_id, user_id, clean_parent_id, clean_content, spoiler_flag),
        )
        cursor.execute(
            "UPDATE collective_discussion_topics SET updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (topic_id,),
        )

        created = cursor.execute(
            """
            SELECT
                dp.id, dp.topic_id, dp.user_id, dp.parent_post_id, dp.content,
                dp.is_spoiler, dp.is_deleted, dp.created_at, dp.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(up.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_discussion_posts dp
            LEFT JOIN users u ON dp.user_id = u.id
            LEFT JOIN user_profiles up ON dp.user_id = up.user_id
            WHERE dp.id = ?;
            """,
            (post_id,),
        ).fetchone()

    res = dict(created)
    res["is_spoiler"] = bool(res["is_spoiler"])
    res["is_deleted"] = bool(res["is_deleted"])
    res["depth"] = 1 if res["parent_post_id"] is not None else 0
    return res


def edit_post(
    read_id: str,
    topic_id: str,
    post_id: str,
    user_id: int,
    content: Optional[str] = None,
    is_spoiler: Optional[bool] = None,
) -> Dict[str, Any]:
    """Permite ao autor editar a própria resposta enquanto o tópico não estiver bloqueado."""
    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        topic = cursor.execute(
            "SELECT id, is_locked, is_deleted FROM collective_discussion_topics WHERE id = ? AND read_id = ?;",
            (topic_id, read_id),
        ).fetchone()
        if not topic:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")
        if topic["is_locked"] or topic["is_deleted"]:
            raise InvalidOperationError("Este tópico não permite edição de mensagens.")

        post_row = cursor.execute(
            "SELECT * FROM collective_discussion_posts WHERE id = ? AND topic_id = ?;",
            (post_id, topic_id),
        ).fetchone()
        if not post_row:
            raise PostNotFoundError("Resposta não encontrada.")

        post = dict(post_row)
        if post["is_deleted"]:
            raise InvalidOperationError("Não é possível editar uma mensagem excluída.")
        if post["user_id"] != user_id:
            raise ClubPermissionError("Apenas o autor pode editar esta resposta.")

        new_content = post["content"]
        if content is not None:
            clean_c = content.strip()
            if not clean_c:
                raise InvalidOperationError("O conteúdo da resposta não pode ficar vazio.")
            if len(clean_c) > MAX_POST_CONTENT_LEN:
                raise InvalidOperationError(f"A resposta deve ter no máximo {MAX_POST_CONTENT_LEN} caracteres.")
            new_content = clean_c

        new_spoiler = post["is_spoiler"] if is_spoiler is None else (1 if is_spoiler else 0)

        cursor.execute(
            """
            UPDATE collective_discussion_posts
            SET content = ?, is_spoiler = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND topic_id = ?;
            """,
            (new_content, new_spoiler, post_id, topic_id),
        )

        updated = cursor.execute(
            """
            SELECT
                dp.id, dp.topic_id, dp.user_id, dp.parent_post_id, dp.content,
                dp.is_spoiler, dp.is_deleted, dp.created_at, dp.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(up.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_discussion_posts dp
            LEFT JOIN users u ON dp.user_id = u.id
            LEFT JOIN user_profiles up ON dp.user_id = up.user_id
            WHERE dp.id = ?;
            """,
            (post_id,),
        ).fetchone()

    res = dict(updated)
    res["is_spoiler"] = bool(res["is_spoiler"])
    res["is_deleted"] = bool(res["is_deleted"])
    res["depth"] = 1 if res["parent_post_id"] is not None else 0
    return res


def delete_post(
    read_id: str,
    topic_id: str,
    post_id: str,
    user_id: int,
) -> Dict[str, Any]:
    """Exclusão lógica de resposta por seu autor ou por moderador/proprietário."""
    with get_db() as conn:
        cursor = conn.cursor()
        read_info = _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        topic = cursor.execute(
            "SELECT id FROM collective_discussion_topics WHERE id = ? AND read_id = ?;",
            (topic_id, read_id),
        ).fetchone()
        if not topic:
            raise TopicNotFoundError("Tópico de discussão não encontrado.")

        post_row = cursor.execute(
            "SELECT * FROM collective_discussion_posts WHERE id = ? AND topic_id = ?;",
            (post_id, topic_id),
        ).fetchone()
        if not post_row:
            raise PostNotFoundError("Resposta não encontrada.")

        is_mod = read_info["user_club_role"] in ("owner", "moderator")
        is_author = post_row["user_id"] == user_id
        if not (is_mod or is_author):
            raise ClubPermissionError("Você não tem permissão para remover esta resposta.")

        cursor.execute(
            "UPDATE collective_discussion_posts SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND topic_id = ?;",
            (post_id, topic_id),
        )

    return {"id": post_id, "topic_id": topic_id, "is_deleted": True}


# =========================================================================
# METAS E CRONOGRAMAS COLETIVOS + PROGRESSO VOLUNTÁRIO
# =========================================================================

def create_goal(
    read_id: str,
    user_id: int,
    title: str,
    description: str = "",
    target_date: Optional[str] = None,
    chapter_ref: Optional[str] = None,
    target_page: Optional[int] = None,
    display_order: Optional[int] = None,
) -> Dict[str, Any]:
    """Cria uma meta coletiva de leitura (exclusivo para proprietário ou moderador)."""
    clean_title = (title or "").strip()
    if not clean_title:
        raise InvalidOperationError("O título da meta é obrigatório.")
    if len(clean_title) > MAX_GOAL_TITLE_LEN:
        raise InvalidOperationError(f"O título da meta deve ter no máximo {MAX_GOAL_TITLE_LEN} caracteres.")

    clean_desc = (description or "").strip()[:MAX_GOAL_DESC_LEN]
    clean_date = _validate_iso_date(target_date)
    clean_chapter = (chapter_ref or "").strip()[:MAX_CHAPTER_REF_LEN] or None

    if target_page is not None:
        if not isinstance(target_page, int) or target_page < 1:
            raise InvalidOperationError("A página-alvo deve ser um número inteiro maior ou igual a 1.")

    goal_id = uuid.uuid4().hex

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, require_moderator=True, is_write_op=True
        )

        if display_order is None:
            max_ord = cursor.execute(
                "SELECT COALESCE(MAX(display_order), 0) FROM collective_reading_goals WHERE read_id = ?;",
                (read_id,),
            ).fetchone()[0]
            order_val = max_ord + 1
        else:
            order_val = int(display_order)

        cursor.execute(
            """
            INSERT INTO collective_reading_goals (
                id, read_id, title, description, target_date,
                chapter_ref, target_page, display_order, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (goal_id, read_id, clean_title, clean_desc, clean_date, clean_chapter, target_page, order_val, user_id),
        )

    goals = list_goals(read_id, user_id, include_archived=True)["goals"]
    for g in goals:
        if g["id"] == goal_id:
            return g
    raise GoalNotFoundError("Meta não encontrada após criação.")


def update_goal(
    read_id: str,
    goal_id: str,
    user_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    target_date: Optional[str] = None,
    chapter_ref: Optional[str] = None,
    target_page: Optional[int] = None,
    display_order: Optional[int] = None,
    is_archived: Optional[bool] = None,
) -> Dict[str, Any]:
    """Atualiza, reordena ou arquiva uma meta de leitura (exclusivo para proprietário ou moderador)."""
    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, require_moderator=True, is_write_op=True
        )

        row = cursor.execute(
            "SELECT * FROM collective_reading_goals WHERE id = ? AND read_id = ?;",
            (goal_id, read_id),
        ).fetchone()
        if not row:
            raise GoalNotFoundError("Meta não encontrada.")

        goal = dict(row)

        new_title = goal["title"]
        if title is not None:
            clean_t = title.strip()
            if not clean_t:
                raise InvalidOperationError("O título da meta não pode ficar vazio.")
            if len(clean_t) > MAX_GOAL_TITLE_LEN:
                raise InvalidOperationError(f"O título da meta deve ter no máximo {MAX_GOAL_TITLE_LEN} caracteres.")
            new_title = clean_t

        new_desc = goal["description"] if description is None else description.strip()[:MAX_GOAL_DESC_LEN]
        new_date = goal["target_date"] if target_date is None else _validate_iso_date(target_date)
        new_chapter = goal["chapter_ref"] if chapter_ref is None else (chapter_ref.strip()[:MAX_CHAPTER_REF_LEN] or None)

        new_page = goal["target_page"]
        if target_page is not None:
            if not isinstance(target_page, int) or target_page < 1:
                raise InvalidOperationError("A página-alvo deve ser maior ou igual a 1.")
            new_page = target_page

        new_order = goal["display_order"] if display_order is None else int(display_order)
        new_archived = goal["is_archived"] if is_archived is None else (1 if is_archived else 0)

        cursor.execute(
            """
            UPDATE collective_reading_goals
            SET title = ?, description = ?, target_date = ?, chapter_ref = ?,
                target_page = ?, display_order = ?, is_archived = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND read_id = ?;
            """,
            (new_title, new_desc, new_date, new_chapter, new_page, new_order, new_archived, goal_id, read_id),
        )

    goals = list_goals(read_id, user_id, include_archived=True)["goals"]
    for g in goals:
        if g["id"] == goal_id:
            return g
    raise GoalNotFoundError("Meta não encontrada.")


def list_goals(read_id: str, user_id: int, include_archived: bool = False) -> Dict[str, Any]:
    """
    Lista as metas da leitura coletiva com os contadores de progresso voluntário.
    Nunca lê nem expõe o progresso privado do leitor PDF/EPUB.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(cursor, read_id, user_id, require_club_membership=True)

        where_sql = "g.read_id = ?" if include_archived else "g.read_id = ? AND g.is_archived = 0"

        rows = cursor.execute(
            f"""
            SELECT
                g.id, g.read_id, g.title, g.description, g.target_date,
                g.chapter_ref, g.target_page, g.display_order, g.is_archived,
                g.created_by, g.created_at, g.updated_at,
                (SELECT COUNT(*) FROM collective_goal_progress gp WHERE gp.goal_id = g.id AND gp.status = 'completed') AS completed_count,
                (SELECT COUNT(*) FROM collective_goal_progress gp WHERE gp.goal_id = g.id AND gp.status = 'in_progress') AS in_progress_count,
                COALESCE((SELECT status FROM collective_goal_progress gp WHERE gp.goal_id = g.id AND gp.user_id = ?), 'pending') AS my_status
            FROM collective_reading_goals g
            WHERE {where_sql}
            ORDER BY g.display_order ASC, COALESCE(g.target_date, '9999-12-31') ASC, g.created_at ASC;
            """,
            (user_id, read_id),
        ).fetchall()

        total_participants = cursor.execute(
            "SELECT COUNT(*) FROM collective_read_members WHERE read_id = ?;",
            (read_id,),
        ).fetchone()[0]

        goals = []
        for row in rows:
            item = dict(row)
            item["is_archived"] = bool(item["is_archived"])
            item["total_participants"] = total_participants
            goals.append(item)

        return {"goals": goals, "total_participants": total_participants}


def update_voluntary_goal_progress(
    read_id: str,
    goal_id: str,
    user_id: int,
    status: str,
) -> Dict[str, Any]:
    """
    Atualiza voluntariamente o status individual do usuário na meta.
    O usuário só pode alterar o próprio status.
    """
    clean_status = (status or "").strip().lower()
    if clean_status not in ("pending", "in_progress", "completed"):
        raise InvalidOperationError("Status inválido. Use 'pending', 'in_progress' ou 'completed'.")

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        goal_row = cursor.execute(
            "SELECT id, is_archived FROM collective_reading_goals WHERE id = ? AND read_id = ?;",
            (goal_id, read_id),
        ).fetchone()
        if not goal_row:
            raise GoalNotFoundError("Meta não encontrada.")
        if goal_row["is_archived"]:
            raise InvalidOperationError("Não é possível alterar progresso de uma meta arquivada.")

        cursor.execute(
            """
            INSERT INTO collective_goal_progress (goal_id, user_id, status, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(goal_id, user_id) DO UPDATE SET
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (goal_id, user_id, clean_status),
        )

    return {"goal_id": goal_id, "user_id": user_id, "status": clean_status}


# =========================================================================
# COMPARTILHAMENTO VOLUNTÁRIO DE CITAÇÕES
# =========================================================================

def share_quote(
    read_id: str,
    user_id: int,
    quote_text: Optional[str] = None,
    comment_text: Optional[str] = None,
    chapter_name: Optional[str] = None,
    page_number: Optional[int] = None,
    epub_cfi: Optional[str] = None,
    is_spoiler: bool = False,
    note_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publica voluntariamente uma citação na leitura coletiva.
    - Se `note_id` for fornecido, verifica estritamente se a nota pertence a `user_id` e ao livro da leitura coletiva.
    - Impõe limite de 1500 caracteres em `quote_text` para proteção de direitos autorais.
    """
    quote_id = uuid.uuid4().hex
    spoiler_flag = 1 if is_spoiler else 0

    with get_db() as conn:
        cursor = conn.cursor()
        read_info = _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )
        book_id = read_info["book_id"]

        resolved_quote = quote_text
        resolved_comment = comment_text
        resolved_page = page_number

        if note_id is not None:
            note_row = cursor.execute(
                """
                SELECT n.id, n.user_id, n.book_id, n.page, n.content, h.selected_text
                FROM notes n
                LEFT JOIN highlights h ON n.highlight_id = h.id
                WHERE n.id = ?;
                """,
                (str(note_id),),
            ).fetchone()
            if not note_row:
                raise InvalidOperationError("Anotação original não encontrada.")
            if note_row["user_id"] != user_id:
                raise ClubPermissionError("Acesso negado: não é possível compartilhar uma anotação privada de outro usuário.")
            if note_row["book_id"] != book_id:
                raise InvalidOperationError("A anotação selecionada pertence a um livro diferente desta leitura coletiva.")

            if resolved_quote is None:
                resolved_quote = note_row["selected_text"] or note_row["content"]
            if resolved_comment is None and note_row["selected_text"]:
                resolved_comment = note_row["content"]
            if resolved_page is None and note_row["page"] is not None:
                resolved_page = note_row["page"]

        clean_quote = (resolved_quote or "").strip()
        if not clean_quote:
            raise InvalidOperationError("O texto da citação é obrigatório.")
        if len(clean_quote) > MAX_QUOTE_TEXT_LEN:
            raise InvalidOperationError(f"A citação deve ter no máximo {MAX_QUOTE_TEXT_LEN} caracteres.")

        clean_comment = (resolved_comment or "").strip() or None
        if clean_comment and len(clean_comment) > MAX_QUOTE_COMMENT_LEN:
            raise InvalidOperationError(f"O comentário deve ter no máximo {MAX_QUOTE_COMMENT_LEN} caracteres.")

        clean_chapter = (chapter_name or "").strip()[:MAX_CHAPTER_REF_LEN] or None
        clean_cfi = (epub_cfi or "").strip()[:MAX_EPUB_CFI_LEN] or None

        if resolved_page is not None:
            if not isinstance(resolved_page, int) or resolved_page < 1:
                raise InvalidOperationError("O número da página deve ser maior ou igual a 1.")

        cursor.execute(
            """
            INSERT INTO collective_shared_quotes (
                id, read_id, book_id, user_id, quote_text, comment_text,
                chapter_name, page_number, epub_cfi, is_spoiler
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                quote_id, read_id, book_id, user_id, clean_quote, clean_comment,
                clean_chapter, resolved_page, clean_cfi, spoiler_flag,
            ),
        )

        row = cursor.execute(
            """
            SELECT
                q.id, q.read_id, q.book_id, q.user_id, q.quote_text, q.comment_text,
                q.chapter_name, q.page_number, q.epub_cfi, q.is_spoiler, q.is_deleted,
                q.created_at, q.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(up.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_shared_quotes q
            LEFT JOIN users u ON q.user_id = u.id
            LEFT JOIN user_profiles up ON q.user_id = up.user_id
            WHERE q.id = ?;
            """,
            (quote_id,),
        ).fetchone()

    item = dict(row)
    item["is_spoiler"] = bool(item["is_spoiler"])
    item["is_deleted"] = bool(item["is_deleted"])
    return item


def list_quotes(
    read_id: str,
    user_id: int,
    page: int = 1,
    limit: int = 20,
) -> Dict[str, Any]:
    """Lista citações compartilhadas da leitura coletiva com paginação e proteção de spoilers."""
    p, l, offset = _normalize_pagination(page, limit, max_limit=50)

    with get_db() as conn:
        cursor = conn.cursor()
        _require_collective_read_access(cursor, read_id, user_id, require_club_membership=True)

        total = cursor.execute(
            "SELECT COUNT(*) FROM collective_shared_quotes WHERE read_id = ? AND is_deleted = 0;",
            (read_id,),
        ).fetchone()[0]

        rows = cursor.execute(
            """
            SELECT
                q.id, q.read_id, q.book_id, q.user_id, q.quote_text, q.comment_text,
                q.chapter_name, q.page_number, q.epub_cfi, q.is_spoiler, q.is_deleted,
                q.created_at, q.updated_at,
                u.username AS author_username,
                COALESCE(u.display_name, u.username, 'Usuário removido') AS author_display_name,
                COALESCE(up.avatar_color, '#8b7355') AS author_avatar_color
            FROM collective_shared_quotes q
            LEFT JOIN users u ON q.user_id = u.id
            LEFT JOIN user_profiles up ON q.user_id = up.user_id
            WHERE q.read_id = ? AND q.is_deleted = 0
            ORDER BY q.created_at DESC
            LIMIT ? OFFSET ?;
            """,
            (read_id, l, offset),
        ).fetchall()

        quotes = []
        for row in rows:
            item = dict(row)
            is_sp = bool(item["is_spoiler"])
            item["is_spoiler"] = is_sp
            item["is_deleted"] = bool(item["is_deleted"])
            item["quote_preview"] = _make_preview(item["quote_text"], is_sp, False, max_len=140)
            quotes.append(item)

        return {
            "quotes": quotes,
            "total": total,
            "page": p,
            "limit": l,
            "has_more": (offset + len(quotes)) < total,
        }


def delete_quote(read_id: str, quote_id: str, user_id: int) -> Dict[str, Any]:
    """Exclusão lógica de citação pelo autor ou por moderador/proprietário."""
    with get_db() as conn:
        cursor = conn.cursor()
        read_info = _require_collective_read_access(
            cursor, read_id, user_id, require_club_membership=True, is_write_op=True
        )

        row = cursor.execute(
            "SELECT id, user_id FROM collective_shared_quotes WHERE id = ? AND read_id = ?;",
            (quote_id, read_id),
        ).fetchone()
        if not row:
            raise QuoteNotFoundError("Citação não encontrada.")

        is_mod = read_info["user_club_role"] in ("owner", "moderator")
        is_author = row["user_id"] == user_id
        if not (is_mod or is_author):
            raise ClubPermissionError("Você não tem permissão para remover esta citação.")

        cursor.execute(
            "UPDATE collective_shared_quotes SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND read_id = ?;",
            (quote_id, read_id),
        )

    return {"id": quote_id, "read_id": read_id, "is_deleted": True}
