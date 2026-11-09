"""
Módulo Multi-Usuário, Privacidade Granular e Biblioteca Social ("Nossa Biblioteca") — Lectoria v1.5.0.
Implementa:
- Perfis de usuário (`user_profiles`) com preferências e estatísticas pessoais.
- Controle de privacidade granular (`user_privacy_settings`) — 100% PRIVADO POR PADRÃO.
- Anotações e citações compartilhadas (`shared_annotations`) com proteção anti-IDOR
  (administradores NÃO têm acesso automático às anotações privadas de outros usuários).
- Comentários e respostas encadeadas em anotações compartilhadas (`annotation_comments`).
- Avaliações (1 a 5 estrelas), resenhas, favoritos e lista "Quero Ler" (`book_reviews`).
- Recomendações diretas de livros entre usuários (`book_recommendations`).
- Mural de atividade social consentida ("Nossa Biblioteca") respeitando estritamente
  as configurações de privacidade em tempo real de cada usuário.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from database import get_db

logger = logging.getLogger("lectoria.social")

PRIVACY_FIELDS = (
    "share_current_reading",
    "share_reading_history",
    "share_reviews",
    "share_lists",
    "share_annotations_default",
)


class SocialPermissionError(PermissionError):
    """Lançada quando um usuário tenta acessar ou modificar recurso privado de outro usuário."""
    pass


class SocialNotFoundError(Exception):
    """Lançada quando uma anotação compartilhada, comentário ou recomendação não existe."""
    pass


def ensure_user_profile_and_privacy(user_id: int, username: Optional[str] = None) -> None:
    """Garante que o usuário possua registros em `user_profiles` e `user_privacy_settings` (tudo privado por padrão)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT username, display_name FROM users WHERE id = ?;", (user_id,)
        ).fetchone()
        if not username:
            username = row["username"] if row else f"user_{user_id}"
        default_display = (
            row["display_name"]
            if (row and row["display_name"])
            else username
        )

        cursor.execute(
            """
            INSERT OR IGNORE INTO user_profiles (user_id, display_name, avatar_color, bio, reading_prefs_json)
            VALUES (?, ?, '#d4a853', '', '{}');
            """,
            (user_id, default_display),
        )
        cursor.execute(
            """
            UPDATE user_profiles
            SET display_name = ?
            WHERE user_id = ? AND (display_name IS NULL OR TRIM(display_name) = '');
            """,
            (default_display, user_id),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO user_privacy_settings (
                user_id, share_current_reading, share_reading_history,
                share_reviews, share_lists, share_annotations_default
            ) VALUES (?, 0, 0, 0, 0, 0);
            """,
            (user_id,),
        )


def get_privacy_settings(user_id: int) -> Dict[str, bool]:
    """Retorna as configurações de privacidade do usuário (todas False por padrão)."""
    ensure_user_profile_and_privacy(user_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_privacy_settings WHERE user_id = ?;", (user_id,)
        ).fetchone()
        if not row:
            return {f: False for f in PRIVACY_FIELDS}
        return {f: bool(row[f]) for f in PRIVACY_FIELDS}


def update_privacy_settings(user_id: int, updates: Dict[str, Any]) -> Dict[str, bool]:
    """Atualiza as configurações de privacidade do próprio usuário."""
    ensure_user_profile_and_privacy(user_id)
    current = get_privacy_settings(user_id)
    for k in PRIVACY_FIELDS:
        if k in updates:
            current[k] = bool(updates[k])

    with get_db() as conn:
        conn.execute(
            """
            UPDATE user_privacy_settings
            SET share_current_reading = ?,
                share_reading_history = ?,
                share_reviews = ?,
                share_lists = ?,
                share_annotations_default = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?;
            """,
            (
                int(current["share_current_reading"]),
                int(current["share_reading_history"]),
                int(current["share_reviews"]),
                int(current["share_lists"]),
                int(current["share_annotations_default"]),
                user_id,
            ),
        )
    return get_privacy_settings(user_id)


def get_user_profile(target_user_id: int, viewer_user_id: int) -> Dict[str, Any]:
    """
    Retorna o perfil de `target_user_id` visto por `viewer_user_id`.
    Se `target_user_id != viewer_user_id`, filtra rigorosamente os dados de leitura
    de acordo com `user_privacy_settings` do usuário alvo (mesmo se o viewer for admin).
    """
    ensure_user_profile_and_privacy(target_user_id)
    is_self = (target_user_id == viewer_user_id)
    privacy = get_privacy_settings(target_user_id)

    with get_db() as conn:
        cursor = conn.cursor()
        u_row = cursor.execute(
            """
            SELECT u.id, u.username, u.display_name as user_display_name, u.role, u.created_at,
                   p.display_name, p.avatar_color, p.bio, p.reading_prefs_json
            FROM users u
            LEFT JOIN user_profiles p ON u.id = p.user_id
            WHERE u.id = ?;
            """,
            (target_user_id,),
        ).fetchone()
        if not u_row:
            raise SocialNotFoundError("Usuário não encontrado.")
        u = dict(u_row)

        try:
            reading_prefs = json.loads(u.get("reading_prefs_json") or "{}")
        except Exception:
            reading_prefs = {}

        # Estatísticas básicas
        cursor.execute(
            """
            SELECT COUNT(*) FROM reading_progress rp
            JOIN books b ON rp.book_id = b.id
            WHERE rp.user_id = ? AND b.is_missing = 0
              AND (rp.percentage >= 100 OR (b.page_count > 0 AND rp.current_page >= b.page_count));
            """,
            (target_user_id,),
        )
        completed_count = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM reading_progress rp
            JOIN books b ON rp.book_id = b.id
            WHERE rp.user_id = ? AND b.is_missing = 0
              AND rp.current_page > 1 AND rp.percentage < 100;
            """,
            (target_user_id,),
        )
        in_progress_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM shared_annotations WHERE user_id = ? AND visibility IN ('public', 'users');",
            (target_user_id,),
        )
        shared_annotations_count = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM book_reviews WHERE user_id = ? AND rating IS NOT NULL;",
            (target_user_id,),
        )
        reviews_count = cursor.fetchone()[0]

        profile_data: Dict[str, Any] = {
            "user_id": u["id"],
            "username": u["username"],
            "role": u.get("role") or "user",
            "display_name": u.get("display_name") or u.get("user_display_name") or u["username"],
            "avatar_color": u.get("avatar_color") or "#d4a853",
            "bio": u.get("bio") or "",
            "created_at": u.get("created_at"),
            "is_self": is_self,
            "stats": {
                "completed_books": completed_count if (is_self or privacy["share_reading_history"]) else None,
                "in_progress_books": in_progress_count if (is_self or privacy["share_current_reading"]) else None,
                "shared_annotations": shared_annotations_count,
                "reviews_count": reviews_count if (is_self or privacy["share_reviews"]) else None,
            },
        }

        if is_self:
            cursor.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?;", (target_user_id,))
            profile_data["stats"]["private_notes_count"] = cursor.fetchone()[0]
            profile_data["reading_prefs"] = reading_prefs
            profile_data["privacy"] = privacy

        # Leituras atuais (somente se o próprio usuário ou share_current_reading = True)
        if is_self or privacy["share_current_reading"]:
            cursor.execute(
                """
                SELECT b.id, b.title, b.author, rp.current_page, rp.total_pages, rp.percentage, rp.updated_at
                FROM reading_progress rp
                JOIN books b ON rp.book_id = b.id
                WHERE rp.user_id = ? AND b.is_missing = 0
                  AND rp.current_page > 1 AND rp.percentage < 100
                ORDER BY rp.updated_at DESC LIMIT 10;
                """,
                (target_user_id,),
            )
            profile_data["current_reading"] = [dict(r) for r in cursor.fetchall()]
        else:
            profile_data["current_reading"] = []

        # Histórico de concluídos (somente se o próprio usuário ou share_reading_history = True)
        if is_self or privacy["share_reading_history"]:
            cursor.execute(
                """
                SELECT b.id, b.title, b.author, rp.percentage, rp.updated_at
                FROM reading_progress rp
                JOIN books b ON rp.book_id = b.id
                WHERE rp.user_id = ? AND b.is_missing = 0
                  AND (rp.percentage >= 100 OR (b.page_count > 0 AND rp.current_page >= b.page_count))
                ORDER BY rp.updated_at DESC LIMIT 15;
                """,
                (target_user_id,),
            )
            profile_data["completed_books"] = [dict(r) for r in cursor.fetchall()]
        else:
            profile_data["completed_books"] = []

        # Listas Favoritos e Quero Ler (somente se o próprio usuário ou share_lists = True)
        if is_self or privacy["share_lists"]:
            cursor.execute(
                """
                SELECT b.id, b.title, b.author, br.is_favorite, br.want_to_read
                FROM book_reviews br
                JOIN books b ON br.book_id = b.id
                WHERE br.user_id = ? AND b.is_missing = 0
                  AND (br.is_favorite = 1 OR br.want_to_read = 1)
                ORDER BY br.updated_at DESC;
                """,
                (target_user_id,),
            )
            list_rows = [dict(r) for r in cursor.fetchall()]
            profile_data["favorites"] = [r for r in list_rows if r.get("is_favorite")]
            profile_data["want_to_read"] = [r for r in list_rows if r.get("want_to_read")]
        else:
            profile_data["favorites"] = []
            profile_data["want_to_read"] = []

    return profile_data


def update_user_profile(user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Atualiza nome de exibição, cor de avatar, bio e preferências de leitura do próprio usuário."""
    ensure_user_profile_and_privacy(user_id)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?;", (user_id,)).fetchone()
        cur = dict(row) if row else {}

        display_name = str(updates.get("display_name", cur.get("display_name") or "")).strip()[:80]
        avatar_color = str(updates.get("avatar_color", cur.get("avatar_color") or "#d4a853")).strip()[:20]
        bio = str(updates.get("bio", cur.get("bio") or "")).strip()[:500]

        reading_prefs = updates.get("reading_prefs")
        if reading_prefs is None:
            prefs_json = cur.get("reading_prefs_json") or "{}"
        else:
            prefs_json = json.dumps(reading_prefs)

        conn.execute(
            """
            UPDATE user_profiles
            SET display_name = ?,
                avatar_color = ?,
                bio = ?,
                reading_prefs_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?;
            """,
            (display_name, avatar_color, bio, prefs_json, user_id),
        )

    return get_user_profile(user_id, viewer_user_id=user_id)


# ==============================================================================
# AVALIAÇÕES, RESENHAS, FAVORITOS E QUERO LER
# ==============================================================================

def upsert_book_review(
    user_id: int,
    book_id: str,
    rating: Optional[int] = None,
    review_text: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    want_to_read: Optional[bool] = None,
    is_public: Optional[bool] = None,
) -> Dict[str, Any]:
    """Cria ou atualiza a avaliação/resenha/favorito/quero-ler de um usuário para um livro."""
    ensure_user_profile_and_privacy(user_id)
    if is_public is not None:
        update_privacy_settings(user_id, {"share_reviews": bool(is_public)})
    if rating is not None:
        rating = int(rating)
        if rating == 0:
            rating = None
        elif rating < 1 or rating > 5:
            raise ValueError("A avaliação deve ser de 1 a 5 estrelas.")

    with get_db() as conn:
        cursor = conn.cursor()
        book_row = cursor.execute("SELECT id, title FROM books WHERE id = ?;", (book_id,)).fetchone()
        if not book_row:
            raise SocialNotFoundError("Livro não encontrado.")

        existing = cursor.execute(
            "SELECT * FROM book_reviews WHERE book_id = ? AND user_id = ?;",
            (book_id, user_id),
        ).fetchone()

        if existing:
            cur = dict(existing)
            final_rating = rating if rating is not None else cur.get("rating")
            final_text = review_text.strip()[:2000] if review_text is not None else cur.get("review_text")
            final_fav = int(bool(is_favorite)) if is_favorite is not None else int(cur.get("is_favorite") or 0)
            final_want = int(bool(want_to_read)) if want_to_read is not None else int(cur.get("want_to_read") or 0)

            cursor.execute(
                """
                UPDATE book_reviews
                SET rating = ?, review_text = ?, is_favorite = ?, want_to_read = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE book_id = ? AND user_id = ?;
                """,
                (final_rating, final_text, final_fav, final_want, book_id, user_id),
            )
        else:
            final_rating = rating
            final_text = review_text.strip()[:2000] if review_text else None
            final_fav = int(bool(is_favorite)) if is_favorite is not None else 0
            final_want = int(bool(want_to_read)) if want_to_read is not None else 0

            cursor.execute(
                """
                INSERT INTO book_reviews (book_id, user_id, rating, review_text, is_favorite, want_to_read)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (book_id, user_id, final_rating, final_text, final_fav, final_want),
            )

        if final_rating is not None or (final_text and len(final_text) > 0):
            _record_activity(
                cursor,
                user_id=user_id,
                book_id=book_id,
                activity_type="reviewed_book",
                payload={"rating": final_rating, "review_text": final_text or ""},
            )

    return get_book_reviews_summary(book_id, viewer_user_id=user_id)


def get_book_reviews_summary(book_id: str, viewer_user_id: int) -> Dict[str, Any]:
    """
    Retorna as avaliações e resenhas de um livro.
    Respeita rigorosamente `share_reviews`: avaliações de outros usuários só são exibidas
    se o respectivo autor tiver `share_reviews = 1` em `user_privacy_settings`.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT br.*, u.username, up.display_name, up.avatar_color,
                   COALESCE(ups.share_reviews, 0) as share_reviews
            FROM book_reviews br
            JOIN users u ON br.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            LEFT JOIN user_privacy_settings ups ON u.id = ups.user_id
            WHERE br.book_id = ?
            ORDER BY br.updated_at DESC;
            """,
            (book_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    visible_reviews = []
    my_review = None
    ratings = []
    viewer_privacy = get_privacy_settings(viewer_user_id) if viewer_user_id else {"share_reviews": False}

    for r in rows:
        is_mine = (r["user_id"] == viewer_user_id)
        can_view = is_mine or bool(r.get("share_reviews"))
        item = {
            "id": r["id"],
            "book_id": r["book_id"],
            "user_id": r["user_id"],
            "username": r["username"],
            "display_name": r.get("display_name") or r["username"],
            "avatar_color": r.get("avatar_color") or "#d4a853",
            "rating": r.get("rating"),
            "review_text": r.get("review_text") or "",
            "is_favorite": bool(r.get("is_favorite")),
            "want_to_read": bool(r.get("want_to_read")),
            "is_public": bool(r.get("share_reviews")),
            "updated_at": r.get("updated_at"),
            "is_mine": is_mine,
        }
        if is_mine:
            my_review = item
        if can_view and (r.get("rating") is not None or (r.get("review_text") and r["review_text"].strip())):
            visible_reviews.append(item)
            if r.get("rating") is not None:
                ratings.append(int(r["rating"]))

    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    return {
        "book_id": book_id,
        "average_rating": avg_rating,
        "ratings_count": len(ratings),
        "my_review": my_review,
        "user_review": my_review,
        "user_share_reviews_default": bool(viewer_privacy.get("share_reviews", False)),
        "reviews": visible_reviews,
    }


# ==============================================================================
# ANOTAÇÕES E CITAÇÕES COMPARTILHADAS + COMENTÁRIOS
# ==============================================================================

def share_annotation(
    user_id: int,
    book_id: str,
    quote_text: Optional[str] = None,
    comment_text: Optional[str] = None,
    content: Optional[str] = None,
    page_number: Optional[int] = None,
    chapter_name: Optional[str] = None,
    progress_locator: Optional[str] = None,
    color: str = "yellow",
    visibility: str = "public",
    note_id: Optional[str] = None,
    source_note_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compartilha uma anotação ou citação na biblioteca.
    Se `note_id` (ou `source_note_id`) for fornecido, verifica estritamente se a nota pertence a `user_id` (anti-IDOR).
    """
    ensure_user_profile_and_privacy(user_id)
    book_id = str(book_id).strip()
    if comment_text is None and content is not None:
        comment_text = content
    if note_id is None and source_note_id is not None:
        note_id = source_note_id
    if visibility == "all_users":
        visibility = "public"
    if visibility not in ("public", "users", "private"):
        visibility = "public"

    with get_db() as conn:
        cursor = conn.cursor()
        book_row = cursor.execute("SELECT id, title FROM books WHERE id = ?;", (book_id,)).fetchone()
        if not book_row:
            raise SocialNotFoundError("Livro não encontrado.")

        if note_id is not None:
            note_row = cursor.execute(
                """
                SELECT n.id, n.user_id, n.book_id, n.page, n.content, n.color, h.selected_text
                FROM notes n
                LEFT JOIN highlights h ON n.highlight_id = h.id
                WHERE n.id = ?;
                """,
                (str(note_id),),
            ).fetchone()
            if not note_row:
                raise SocialNotFoundError("Anotação original não encontrada.")
            if note_row["user_id"] != user_id:
                raise SocialPermissionError(
                    "Acesso negado: você não pode compartilhar uma anotação privada de outro usuário."
                )
            quote_text = quote_text if quote_text is not None else note_row["selected_text"]
            comment_text = comment_text if comment_text is not None else note_row["content"]
            page_number = page_number if page_number is not None else note_row["page"]
            color = color or note_row["color"] or "yellow"

        clean_quote = (quote_text or "").strip()[:3000]
        clean_comment = (comment_text or "").strip()[:3000]
        if not clean_quote and not clean_comment:
            raise ValueError("A anotação compartilhada deve conter um trecho destacado ou um comentário.")

        cursor.execute(
            """
            INSERT INTO shared_annotations (
                book_id, user_id, note_id, page_number, chapter_name,
                progress_locator, quote_text, comment_text, color, visibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                book_id,
                user_id,
                note_id,
                page_number,
                (chapter_name or "").strip()[:200] or None,
                (progress_locator or "").strip()[:500] or None,
                clean_quote or None,
                clean_comment or None,
                color,
                visibility,
            ),
        )
        shared_id = int(cursor.lastrowid)

        if visibility in ("public", "users"):
            _record_activity(
                cursor,
                user_id=user_id,
                book_id=book_id,
                activity_type="shared_annotation",
                payload={
                    "shared_annotation_id": shared_id,
                    "quote_text": clean_quote[:300],
                    "comment_text": clean_comment[:300],
                    "page_number": page_number,
                    "chapter_name": chapter_name,
                },
            )

    return get_shared_annotation(shared_id, viewer_user_id=user_id)


def get_shared_annotation(shared_id: int, viewer_user_id: int) -> Dict[str, Any]:
    """Obtém uma anotação compartilhada e seus comentários, respeitando a visibilidade."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT sa.*, u.username, up.display_name, up.avatar_color,
                   b.title as book_title, b.author as book_author, b.format as book_format
            FROM shared_annotations sa
            JOIN users u ON sa.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            JOIN books b ON sa.book_id = b.id
            WHERE sa.id = ?;
            """,
            (shared_id,),
        ).fetchone()
        if not row:
            raise SocialNotFoundError("Anotação compartilhada não encontrada.")
        item = dict(row)
        if item["visibility"] == "private" and item["user_id"] != viewer_user_id:
            raise SocialPermissionError("Esta anotação não está compartilhada publicamente.")

        item["display_name"] = item.get("display_name") or item["username"]
        item["avatar_color"] = item.get("avatar_color") or "#d4a853"
        item["content"] = item.get("comment_text") or ""
        item["is_mine"] = (item["user_id"] == viewer_user_id)
        item["comments"] = list_annotation_comments(shared_id, viewer_user_id=viewer_user_id, conn=conn)
        return item


def list_shared_annotations(
    viewer_user_id: int = 0,
    book_id: Optional[str] = None,
    author_user_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Lista anotações explicitamente compartilhadas na biblioteca.
    Anotações com `visibility = 'private'` só são retornadas ao próprio autor (`user_id == viewer_user_id`),
    mesmo quando o requisitante é administrador.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        sql = """
            SELECT sa.*, u.username, up.display_name, up.avatar_color,
                   b.title as book_title, b.author as book_author, b.format as book_format
            FROM shared_annotations sa
            JOIN users u ON sa.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            JOIN books b ON sa.book_id = b.id
            WHERE b.is_missing = 0
              AND (sa.visibility IN ('public', 'users') OR sa.user_id = ?)
        """
        params: List[Any] = [viewer_user_id]
        if book_id:
            sql += " AND sa.book_id = ?"
            params.append(book_id)
        if author_user_id is not None:
            sql += " AND sa.user_id = ?"
            params.append(author_user_id)

        sql += " ORDER BY sa.created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))

        cursor.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()]

        for r in rows:
            r["display_name"] = r.get("display_name") or r["username"]
            r["avatar_color"] = r.get("avatar_color") or "#d4a853"
            r["content"] = r.get("comment_text") or ""
            r["is_mine"] = (r["user_id"] == viewer_user_id)
            r["comments"] = list_annotation_comments(r["id"], viewer_user_id=viewer_user_id, conn=conn)

    return rows


def update_shared_annotation(
    shared_id: int,
    user_id: int,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """Permite ao autor atualizar o comentário ou a visibilidade (ex: tornar privada) de sua anotação compartilhada."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM shared_annotations WHERE id = ?;", (shared_id,)).fetchone()
        if not row:
            raise SocialNotFoundError("Anotação compartilhada não encontrada.")
        if row["user_id"] != user_id:
            raise SocialPermissionError("Apenas o autor da anotação pode alterá-la.")

        comment_text = updates.get("comment_text", row["comment_text"])
        if comment_text is not None:
            comment_text = str(comment_text).strip()[:3000]
        visibility = updates.get("visibility", row["visibility"])
        if visibility not in ("public", "users", "private"):
            visibility = row["visibility"]

        cursor.execute(
            """
            UPDATE shared_annotations
            SET comment_text = ?, visibility = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (comment_text, visibility, shared_id),
        )
    return get_shared_annotation(shared_id, viewer_user_id=user_id)


def delete_shared_annotation(shared_id: int, user_id: int, is_admin: bool = False) -> bool:
    """Remove o compartilhamento de uma anotação (apenas o próprio autor ou moderação admin em item público)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM shared_annotations WHERE id = ?;", (shared_id,)).fetchone()
        if not row:
            raise SocialNotFoundError("Anotação compartilhada não encontrada.")
        if row["user_id"] != user_id and not (is_admin and row["visibility"] != "private"):
            raise SocialPermissionError("Sem permissão para remover esta anotação compartilhada.")
        cursor.execute("DELETE FROM shared_annotations WHERE id = ?;", (shared_id,))
        return cursor.rowcount > 0


def list_annotation_comments(
    shared_annotation_id: int,
    viewer_user_id: int,
    conn=None,
) -> List[Dict[str, Any]]:
    """Lista comentários e respostas de uma anotação compartilhada."""
    def _query(c):
        cur = c.cursor()
        cur.execute(
            """
            SELECT ac.*, u.username, up.display_name, up.avatar_color
            FROM annotation_comments ac
            JOIN users u ON ac.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            WHERE ac.shared_annotation_id = ?
            ORDER BY ac.created_at ASC, ac.id ASC;
            """,
            (shared_annotation_id,),
        )
        items = [dict(r) for r in cur.fetchall()]
        for it in items:
            it["display_name"] = it.get("display_name") or it["username"]
            it["avatar_color"] = it.get("avatar_color") or "#d4a853"
            it["is_mine"] = (it["user_id"] == viewer_user_id)
        return items

    if conn is not None:
        return _query(conn)
    with get_db() as new_conn:
        return _query(new_conn)


def add_annotation_comment(
    shared_annotation_id: int,
    user_id: int,
    content: str,
    parent_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Adiciona um comentário ou resposta a uma anotação compartilhada."""
    ensure_user_profile_and_privacy(user_id)
    clean_content = (content or "").strip()[:2000]
    if not clean_content:
        raise ValueError("O comentário não pode ser vazio.")

    with get_db() as conn:
        cursor = conn.cursor()
        sa_row = cursor.execute(
            "SELECT id, book_id, user_id, visibility FROM shared_annotations WHERE id = ?;",
            (shared_annotation_id,),
        ).fetchone()
        if not sa_row:
            raise SocialNotFoundError("Anotação compartilhada não encontrada.")
        if sa_row["visibility"] == "private" and sa_row["user_id"] != user_id:
            raise SocialPermissionError("Não é possível comentar em uma anotação privada.")

        if parent_id is not None:
            p_row = cursor.execute(
                "SELECT id FROM annotation_comments WHERE id = ? AND shared_annotation_id = ?;",
                (parent_id, shared_annotation_id),
            ).fetchone()
            if not p_row:
                raise SocialNotFoundError("Comentário pai não encontrado.")

        cursor.execute(
            """
            INSERT INTO annotation_comments (shared_annotation_id, user_id, parent_id, content)
            VALUES (?, ?, ?, ?);
            """,
            (shared_annotation_id, user_id, parent_id, clean_content),
        )
        cid = int(cursor.lastrowid)

        _record_activity(
            cursor,
            user_id=user_id,
            book_id=sa_row["book_id"],
            activity_type="commented_annotation",
            payload={
                "shared_annotation_id": shared_annotation_id,
                "comment_id": cid,
                "content": clean_content[:250],
            },
        )

        row = cursor.execute(
            """
            SELECT ac.*, u.username, up.display_name, up.avatar_color
            FROM annotation_comments ac
            JOIN users u ON ac.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            WHERE ac.id = ?;
            """,
            (cid,),
        ).fetchone()
        res = dict(row)
        res["display_name"] = res.get("display_name") or res["username"]
        res["avatar_color"] = res.get("avatar_color") or "#d4a853"
        res["is_mine"] = True
        return res


def update_annotation_comment(comment_id: int, user_id: int, content: str) -> Dict[str, Any]:
    """Edita o próprio comentário em uma anotação compartilhada."""
    clean_content = (content or "").strip()[:2000]
    if not clean_content:
        raise ValueError("O comentário não pode ser vazio.")

    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM annotation_comments WHERE id = ?;", (comment_id,)).fetchone()
        if not row:
            raise SocialNotFoundError("Comentário não encontrado.")
        if row["user_id"] != user_id:
            raise SocialPermissionError("Apenas o autor do comentário pode editá-lo.")

        cursor.execute(
            """
            UPDATE annotation_comments
            SET content = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (clean_content, comment_id),
        )
        updated = cursor.execute(
            """
            SELECT ac.*, u.username, up.display_name, up.avatar_color
            FROM annotation_comments ac
            JOIN users u ON ac.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            WHERE ac.id = ?;
            """,
            (comment_id,),
        ).fetchone()
        res = dict(updated)
        res["display_name"] = res.get("display_name") or res["username"]
        res["avatar_color"] = res.get("avatar_color") or "#d4a853"
        res["is_mine"] = True
        return res


def delete_annotation_comment(comment_id: int, user_id: int, is_admin: bool = False) -> bool:
    """Exclui um comentário (próprio autor ou moderação de admin)."""
    with get_db() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM annotation_comments WHERE id = ?;", (comment_id,)).fetchone()
        if not row:
            raise SocialNotFoundError("Comentário não encontrado.")
        if row["user_id"] != user_id and not is_admin:
            raise SocialPermissionError("Sem permissão para excluir este comentário.")
        cursor.execute("DELETE FROM annotation_comments WHERE id = ?;", (comment_id,))
        return cursor.rowcount > 0


# ==============================================================================
# RECOMENDAÇÕES DE LIVROS ENTRE USUÁRIOS
# ==============================================================================

def create_recommendation(
    from_user_id: Optional[int] = None,
    to_user_id: Optional[int] = None,
    book_id: str = "",
    message: Optional[str] = None,
    sender_user_id: Optional[int] = None,
    recipient_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Envia uma recomendação de leitura para outro usuário ou para toda a comunidade da biblioteca."""
    if from_user_id is None and sender_user_id is not None:
        from_user_id = sender_user_id
    if to_user_id is None and recipient_user_id is not None:
        to_user_id = recipient_user_id
    if from_user_id is None:
        raise ValueError("Usuário remetente não informado.")

    book_id = str(book_id).strip()
    if not book_id:
        raise ValueError("Livro não informado.")

    ensure_user_profile_and_privacy(from_user_id)
    clean_msg = (message or "").strip()[:1000]

    with get_db() as conn:
        cursor = conn.cursor()
        book_row = cursor.execute("SELECT id, title, author FROM books WHERE id = ?;", (book_id,)).fetchone()
        if not book_row:
            raise SocialNotFoundError("Livro não encontrado.")

        sender_row = cursor.execute(
            """
            SELECT u.id, u.username,
                   COALESCE(NULLIF(up.display_name, ''), NULLIF(u.display_name, ''), u.username) as display_name
            FROM users u
            LEFT JOIN user_profiles up ON u.id = up.user_id
            WHERE u.id = ?;
            """,
            (from_user_id,),
        ).fetchone()
        sender_username = sender_row["username"] if sender_row else f"user_{from_user_id}"
        sender_display_name = (sender_row["display_name"] if sender_row and sender_row["display_name"] else sender_username)

        if to_user_id is not None:
            to_user_id = int(to_user_id)
            if from_user_id == to_user_id:
                raise ValueError("Você não pode enviar uma recomendação diretamente para si mesmo.")
            target_u = cursor.execute(
                """
                SELECT u.id, u.username,
                       COALESCE(NULLIF(up.display_name, ''), NULLIF(u.display_name, ''), u.username) as display_name
                FROM users u
                LEFT JOIN user_profiles up ON u.id = up.user_id
                WHERE u.id = ?;
                """,
                (to_user_id,),
            ).fetchone()
            if not target_u:
                raise SocialNotFoundError("Usuário destinatário não encontrado.")
            targets = [target_u]
        else:
            # Recomendação para toda a comunidade (Mural Geral): envia para os demais leitores ativos,
            # ou registra no mural geral caso exista apenas 1 usuário na instância.
            others = cursor.execute(
                """
                SELECT u.id, u.username,
                       COALESCE(NULLIF(up.display_name, ''), NULLIF(u.display_name, ''), u.username) as display_name
                FROM users u
                LEFT JOIN user_profiles up ON u.id = up.user_id
                WHERE u.id != ? AND COALESCE(u.is_active, 1) = 1
                ORDER BY u.id ASC;
                """,
                (from_user_id,),
            ).fetchall()
            targets = list(others) if others else [sender_row]

        first_rec_id = 0
        first_target = targets[0]
        for target_u in targets:
            tid = int(target_u["id"])
            cursor.execute(
                """
                INSERT INTO book_recommendations (from_user_id, to_user_id, book_id, message, is_read)
                VALUES (?, ?, ?, ?, 0);
                """,
                (from_user_id, tid, book_id, clean_msg),
            )
            if not first_rec_id:
                first_rec_id = int(cursor.lastrowid)

        _record_activity(
            cursor,
            user_id=from_user_id,
            book_id=book_id,
            activity_type="recommended_book",
            payload={
                "to_user_id": int(first_target["id"]),
                "to_username": first_target["username"],
                "message": clean_msg[:300],
            },
        )

        to_uname = first_target["username"]
        to_dname = first_target["display_name"] or to_uname
        return {
            "id": first_rec_id,
            "from_user_id": from_user_id,
            "to_user_id": int(first_target["id"]),
            "sender_username": sender_username,
            "sender_display_name": sender_display_name,
            "from_username": sender_username,
            "from_display_name": sender_display_name,
            "to_username": to_uname,
            "to_display_name": to_dname,
            "recipient_username": to_uname,
            "recipient_display_name": to_dname,
            "book_id": book_id,
            "book_title": book_row["title"],
            "book_author": book_row["author"],
            "message": clean_msg,
            "is_read": False,
        }


def list_user_recommendations(user_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Lista recomendações recebidas e enviadas pelo usuário."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.*, b.title as book_title, b.author as book_author,
                   u_from.username as from_username,
                   COALESCE(NULLIF(up_from.display_name, ''), NULLIF(u_from.display_name, ''), u_from.username) as from_display_name,
                   up_from.avatar_color as from_avatar_color,
                   u_to.username as to_username,
                   COALESCE(NULLIF(up_to.display_name, ''), NULLIF(u_to.display_name, ''), u_to.username) as to_display_name
            FROM book_recommendations r
            JOIN books b ON r.book_id = b.id
            JOIN users u_from ON r.from_user_id = u_from.id
            LEFT JOIN user_profiles up_from ON u_from.id = up_from.user_id
            JOIN users u_to ON r.to_user_id = u_to.id
            LEFT JOIN user_profiles up_to ON u_to.id = up_to.user_id
            WHERE r.to_user_id = ? AND b.is_missing = 0
            ORDER BY r.created_at DESC LIMIT 50;
            """,
            (user_id,),
        )
        received = [dict(row) for row in cursor.fetchall()]
        for item in received:
            item["from_display_name"] = item.get("from_display_name") or item["from_username"]
            item["to_display_name"] = item.get("to_display_name") or item["to_username"]
            item["sender_username"] = item["from_username"]
            item["sender_display_name"] = item["from_display_name"]
            item["recipient_username"] = item["to_username"]
            item["recipient_display_name"] = item["to_display_name"]
            item["is_read"] = bool(item.get("is_read"))

        cursor.execute(
            """
            SELECT r.*, b.title as book_title, b.author as book_author,
                   u_from.username as from_username,
                   COALESCE(NULLIF(up_from.display_name, ''), NULLIF(u_from.display_name, ''), u_from.username) as from_display_name,
                   up_from.avatar_color as from_avatar_color,
                   u_to.username as to_username,
                   COALESCE(NULLIF(up_to.display_name, ''), NULLIF(u_to.display_name, ''), u_to.username) as to_display_name
            FROM book_recommendations r
            JOIN books b ON r.book_id = b.id
            JOIN users u_from ON r.from_user_id = u_from.id
            LEFT JOIN user_profiles up_from ON u_from.id = up_from.user_id
            JOIN users u_to ON r.to_user_id = u_to.id
            LEFT JOIN user_profiles up_to ON u_to.id = up_to.user_id
            WHERE r.from_user_id = ? AND b.is_missing = 0
            ORDER BY r.created_at DESC LIMIT 50;
            """,
            (user_id,),
        )
        sent = [dict(row) for row in cursor.fetchall()]
        for item in sent:
            item["from_display_name"] = item.get("from_display_name") or item["from_username"]
            item["to_display_name"] = item.get("to_display_name") or item["to_username"]
            item["sender_username"] = item["from_username"]
            item["sender_display_name"] = item["from_display_name"]
            item["recipient_username"] = item["to_username"]
            item["recipient_display_name"] = item["to_display_name"]
            item["is_read"] = bool(item.get("is_read"))

    return {"received": received, "sent": sent}


# ==============================================================================
# MURAL "NOSSA BIBLIOTECA" (ATIVIDADE CONSENTIDA)
# ==============================================================================

def _record_activity(
    cursor,
    user_id: int,
    book_id: str,
    activity_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        cursor.execute(
            """
            INSERT INTO reading_activity (user_id, book_id, activity_type, payload_json)
            VALUES (?, ?, ?, ?);
            """,
            (user_id, book_id, activity_type, json.dumps(payload or {})),
        )
    except Exception as e:
        logger.debug("Falha ao registrar reading_activity: %s", e)


def record_reading_milestone(user_id: int, book_id: str, percentage: float, current_page: int) -> None:
    """Registra início ou conclusão de leitura no feed (exibido apenas se o usuário habilitar compartilhamento)."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if percentage >= 100.0:
                existing = cursor.execute(
                    """
                    SELECT id FROM reading_activity
                    WHERE user_id = ? AND book_id = ? AND activity_type = 'completed_book'
                    LIMIT 1;
                    """,
                    (user_id, book_id),
                ).fetchone()
                if not existing:
                    _record_activity(
                        cursor,
                        user_id=user_id,
                        book_id=book_id,
                        activity_type="completed_book",
                        payload={"percentage": 100.0},
                    )
            elif current_page > 1:
                existing = cursor.execute(
                    """
                    SELECT id FROM reading_activity
                    WHERE user_id = ? AND book_id = ? AND activity_type = 'started_reading'
                    LIMIT 1;
                    """,
                    (user_id, book_id),
                ).fetchone()
                if not existing:
                    _record_activity(
                        cursor,
                        user_id=user_id,
                        book_id=book_id,
                        activity_type="started_reading",
                        payload={"current_page": current_page, "percentage": round(percentage, 1)},
                    )
    except Exception:
        pass


def get_nossa_biblioteca_feed(viewer_user_id: int) -> Dict[str, Any]:
    """
    Constrói o painel consolidado "Nossa Biblioteca":
    - Leituras em andamento compartilhadas (`share_current_reading = 1` ou próprio usuário)
    - Livros concluídos compartilhados (`share_reading_history = 1` ou próprio usuário)
    - Anotações compartilhadas recentes e mais comentadas
    - Avaliações e resenhas recentes (`share_reviews = 1` ou próprio usuário)
    - Recomendações recebidas pelo usuário logado
    - Lista de membros da biblioteca para recomendação/interação
    """
    ensure_user_profile_and_privacy(viewer_user_id)

    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Leituras em andamento compartilhadas
        cursor.execute(
            """
            SELECT rp.book_id, rp.user_id, rp.current_page, rp.total_pages, rp.percentage, rp.updated_at,
                   b.title as book_title, b.author as book_author, b.cover_updated_at,
                   u.username, up.display_name, up.avatar_color
            FROM reading_progress rp
            JOIN books b ON rp.book_id = b.id
            JOIN users u ON rp.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            LEFT JOIN user_privacy_settings ups ON u.id = ups.user_id
            WHERE b.is_missing = 0
              AND rp.current_page > 1 AND rp.percentage < 100
              AND (rp.user_id = ? OR COALESCE(ups.share_current_reading, 0) = 1)
            ORDER BY rp.updated_at DESC
            LIMIT 20;
            """,
            (viewer_user_id,),
        )
        currently_reading = []
        for r in cursor.fetchall():
            d = dict(r)
            d["title"] = d.get("book_title") or ""
            d["display_name"] = d.get("display_name") or d["username"]
            d["avatar_color"] = d.get("avatar_color") or "#d4a853"
            ver = d.get("cover_updated_at") or ""
            d["cover_url"] = f"/api/books/{d['book_id']}/cover?v={ver}" if ver else f"/api/books/{d['book_id']}/cover"
            currently_reading.append(d)

        # 2. Livros concluídos recentemente (compartilhados)
        cursor.execute(
            """
            SELECT rp.book_id, rp.user_id, rp.percentage, rp.updated_at,
                   b.title as book_title, b.author as book_author, b.cover_updated_at,
                   u.username, up.display_name, up.avatar_color
            FROM reading_progress rp
            JOIN books b ON rp.book_id = b.id
            JOIN users u ON rp.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            LEFT JOIN user_privacy_settings ups ON u.id = ups.user_id
            WHERE b.is_missing = 0
              AND (rp.percentage >= 100 OR (b.page_count > 0 AND rp.current_page >= b.page_count))
              AND (rp.user_id = ? OR COALESCE(ups.share_reading_history, 0) = 1)
            ORDER BY rp.updated_at DESC
            LIMIT 20;
            """,
            (viewer_user_id,),
        )
        recently_completed = []
        for r in cursor.fetchall():
            d = dict(r)
            d["title"] = d.get("book_title") or ""
            d["display_name"] = d.get("display_name") or d["username"]
            d["avatar_color"] = d.get("avatar_color") or "#d4a853"
            ver = d.get("cover_updated_at") or ""
            d["cover_url"] = f"/api/books/{d['book_id']}/cover?v={ver}" if ver else f"/api/books/{d['book_id']}/cover"
            recently_completed.append(d)

        # 3. Avaliações e resenhas recentes (compartilhadas)
        cursor.execute(
            """
            SELECT br.*, b.title as book_title, b.author as book_author, b.cover_updated_at,
                   u.username, up.display_name, up.avatar_color
            FROM book_reviews br
            JOIN books b ON br.book_id = b.id
            JOIN users u ON br.user_id = u.id
            LEFT JOIN user_profiles up ON u.id = up.user_id
            LEFT JOIN user_privacy_settings ups ON u.id = ups.user_id
            WHERE b.is_missing = 0
              AND (br.rating IS NOT NULL OR (br.review_text IS NOT NULL AND TRIM(br.review_text) != ''))
              AND (br.user_id = ? OR COALESCE(ups.share_reviews, 0) = 1)
            ORDER BY br.updated_at DESC
            LIMIT 20;
            """,
            (viewer_user_id,),
        )
        recent_reviews = []
        for r in cursor.fetchall():
            d = dict(r)
            d["display_name"] = d.get("display_name") or d["username"]
            d["avatar_color"] = d.get("avatar_color") or "#d4a853"
            ver = d.get("cover_updated_at") or ""
            d["cover_url"] = f"/api/books/{d['book_id']}/cover?v={ver}" if ver else f"/api/books/{d['book_id']}/cover"
            recent_reviews.append(d)

        # 4. Membros ativos para recomendações
        cursor.execute(
            """
            SELECT u.id, u.username, up.display_name, up.avatar_color
            FROM users u
            LEFT JOIN user_profiles up ON u.id = up.user_id
            WHERE COALESCE(u.is_active, 1) = 1
            ORDER BY u.username COLLATE NOCASE ASC;
            """
        )
        members = [
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"] or r["username"],
                "avatar_color": r["avatar_color"] or "#d4a853",
            }
            for r in cursor.fetchall()
        ]

    shared_annotations = list_shared_annotations(viewer_user_id=viewer_user_id, limit=30)
    recs_by_dir = list_user_recommendations(viewer_user_id)
    seen_rec_ids = set()
    recommendations_list = []
    for rec_item in recs_by_dir["received"] + recs_by_dir["sent"]:
        if rec_item["id"] not in seen_rec_ids:
            seen_rec_ids.add(rec_item["id"])
            recommendations_list.append(rec_item)

    return {
        "currently_reading": currently_reading,
        "recently_completed": recently_completed,
        "shared_annotations": shared_annotations,
        "recent_reviews": recent_reviews,
        "recommendations": recommendations_list,
        "recommendations_by_direction": recs_by_dir,
        "members": members,
    }
