"""
Serviço de Domínio de Conhecimento Conectado (Seção Atlas) para o Lectoria (v1.9.0 — Schema SQLite v13).

Implementa as regras de negócio, persistência e autorização em tempo de leitura para:
- Conceitos pessoais (`concepts` — `KD-002`: entidade própria, privada e isolada por `user_id`).
- Reflexões autorais (`reflections` — `KD-005`: entidade própria, privada por `user_id`, corpo `body_markdown`).
- Conexões semânticas e referências internas (`knowledge_links` — `KD-003` e `KD-004`: 8 tipos fixos de relação
  direcionada com justificativa opcional e identidade por UUIDs estáveis).
- Resolução centralizada e segura de entidades (`resolve_knowledge_entity`), aplicando *Read-Time Authorization*
  para nunca vazar dados de terceiros, anotações revogadas (`public -> private`), livros ausentes ou fontes inativas.
- Sincronização e pesquisa de candidatos no índice SQLite FTS5 (`knowledge_fts` — `KD-008`:
  `tokenize='unicode61 remove_diacritics 2'`), onde o FTS5 atua estritamente como localizador de candidatos e toda
  permissão é validada nas tabelas relacionais originais.
"""

from datetime import datetime, timezone
import json
import re
import sqlite3
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from annotations_service import (
    AnnotationPermissionError,
    _verify_book_access as _canonical_verify_book_access,
)
from database import get_db


# =========================================================================
# EXCEÇÕES DE DOMÍNIO
# =========================================================================

class KnowledgeError(Exception):
    """Erro base de domínio do Conhecimento Conectado (Atlas)."""
    status_code: int = 400


class KnowledgeNotFoundError(KnowledgeError):
    """Recurso inexistente ou inacessível ao usuário autenticado (HTTP 404 anti-enumeração)."""
    status_code: int = 404


class KnowledgePermissionError(KnowledgeError):
    """Operação proibida para o usuário autenticado (HTTP 403)."""
    status_code: int = 403


class KnowledgeValidationError(KnowledgeError):
    """Dados de entrada inválidos, fora de formato ou fora dos limites (HTTP 400)."""
    status_code: int = 400


class KnowledgeConflictError(KnowledgeError):
    """Conflito de unicidade (ex.: nome de conceito duplicado ou relação idêntica já existente — HTTP 409)."""
    status_code: int = 409


# =========================================================================
# CONSTANTES, LIMITES E VOCABULÁRIO CANÔNICO (KD-002, KD-003, KD-004, KD-005)
# =========================================================================

MAX_CONCEPT_NAME_LEN = 160
MAX_CONCEPT_DESC_LEN = 10000
MAX_REFLECTION_TITLE_LEN = 300
MAX_REFLECTION_BODY_LEN = 50000
MAX_LINK_RATIONALE_LEN = 2000
MAX_TAGS_COUNT = 30
MAX_TAG_LEN = 64
MAX_COLOR_LEN = 32

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
AUX_SEARCH_LIMIT = 50
MAX_SEARCH_QUERY_LEN = 300
_SQL_IN_CHUNK_SIZE = 500


def _chunked(seq: Sequence[Any], size: int = _SQL_IN_CHUNK_SIZE):
    """Divide uma sequência em blocos de tamanho fixo para consultas SQL `IN (...)` seguras."""
    for idx in range(0, len(seq), size):
        yield seq[idx : idx + size]

VALID_CONCEPT_SORTS: Tuple[str, ...] = ("updated_desc", "created_desc", "name_asc")
VALID_REFLECTION_SORTS: Tuple[str, ...] = ("updated_desc", "created_desc", "title_asc")
VALID_LINK_SORTS: Tuple[str, ...] = ("created_desc", "updated_desc")
VALID_DIRECTIONS: Tuple[str, ...] = ("outgoing", "incoming", "both")

VALID_ENTITY_TYPES: Tuple[str, ...] = (
    "concept",
    "reflection",
    "highlight",
    "note",
    "book",
)

VALID_RELATION_TYPES: Tuple[str, ...] = (
    "complements",
    "contradicts",
    "exemplifies",
    "grounds",
    "develops",
    "references",
    "questions",
    "relates_to",
)

RELATION_LABELS_PT: Dict[str, Dict[str, str]] = {
    "complements": {
        "outgoing": "Complementa",
        "incoming": "Complementado por",
    },
    "contradicts": {
        "outgoing": "Contradiz",
        "incoming": "Contradito por",
    },
    "exemplifies": {
        "outgoing": "Exemplifica",
        "incoming": "Exemplificado por",
    },
    "grounds": {
        "outgoing": "Fundamenta",
        "incoming": "Fundamentado por",
    },
    "develops": {
        "outgoing": "Desenvolve",
        "incoming": "Desenvolvido por",
    },
    "references": {
        "outgoing": "Referencia",
        "incoming": "Referenciado por",
    },
    "questions": {
        "outgoing": "Questiona",
        "incoming": "Questionado por",
    },
    "relates_to": {
        "outgoing": "Relaciona-se com",
        "incoming": "Relacionado a",
    },
}

DEFAULT_CONCEPT_COLOR = "#d4a853"
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_WHITESPACE_RE = re.compile(r"\s+")
_FTS_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


# =========================================================================
# UTILITÁRIOS DE NORMALIZAÇÃO UNICODE E VALIDAÇÃO (Seção 13.1)
# =========================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _next_monotonic_iso(previous_iso: Optional[str]) -> str:
    now = _now_iso()
    if previous_iso and str(previous_iso).strip() and now <= str(previous_iso).strip():
        try:
            prev_str = str(previous_iso).strip()
            fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in prev_str else "%Y-%m-%d %H:%M:%S"
            dt = datetime.strptime(prev_str, fmt).replace(tzinfo=timezone.utc)
            from datetime import timedelta
            return (dt + timedelta(microseconds=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
        except Exception:
            pass
    return now


def normalize_unicode_nfc(value: Any) -> str:
    """
    Normaliza uma string para a forma canônica composta Unicode NFC,
    rejeitando bytes nulos (\\x00) e codepoints substitutos UTF-16 órfãos (surrogates).
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise KnowledgeValidationError("Valor textual inválido.")
    if "\x00" in value:
        raise KnowledgeValidationError("Caracteres nulos não são permitidos.")
    for ch in value:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise KnowledgeValidationError("Sequência Unicode malformada (surrogate inválido).")
    return unicodedata.normalize("NFC", value)


def normalize_concept_name(name: Any) -> Tuple[str, str]:
    """
    Valida e normaliza o nome de um conceito:
    - Aplica Unicode NFC.
    - Remove espaços excedentes nas extremidades e colapsa espaços internos múltiplos.
    - Gera `normalized_name` com `.casefold()` em NFC para comparação e unicidade por usuário.
    """
    raw = normalize_unicode_nfc(name)
    cleaned = _WHITESPACE_RE.sub(" ", raw.strip())
    if not cleaned:
        raise KnowledgeValidationError("O nome do conceito não pode ser vazio.")
    if len(cleaned) > MAX_CONCEPT_NAME_LEN:
        raise KnowledgeValidationError(
            f"O nome do conceito excede o limite de {MAX_CONCEPT_NAME_LEN} caracteres."
        )
    normalized = unicodedata.normalize("NFC", cleaned.casefold())
    return cleaned, normalized


def strip_diacritics_casefold(text: str) -> str:
    """
    Converte texto para minúsculas (`casefold`) e remove marcas diacríticas (acentos e cedilha)
    via decomposição Unicode NFD. Usado para busca auxiliar em memória e fallback seguro.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", stripped)


def _validate_and_normalize_tags(tags: Any) -> List[str]:
    """Valida, normaliza em NFC e deduplica uma lista de etiquetas leves."""
    if tags is None:
        return []
    if isinstance(tags, str):
        try:
            parsed = json.loads(tags)
            if isinstance(parsed, list):
                tags = parsed
            else:
                tags = [t.strip() for t in tags.split(",") if t.strip()]
        except Exception:
            tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, (list, tuple)):
        raise KnowledgeValidationError("A lista de etiquetas (tags) deve ser um array.")
    if len(tags) > MAX_TAGS_COUNT:
        raise KnowledgeValidationError(
            f"Número máximo de etiquetas excedido (máximo: {MAX_TAGS_COUNT})."
        )

    result: List[str] = []
    seen: set = set()
    for item in tags:
        if not isinstance(item, str):
            raise KnowledgeValidationError("Cada etiqueta deve ser uma string.")
        cleaned = _WHITESPACE_RE.sub(" ", normalize_unicode_nfc(item).strip())
        if cleaned.startswith("#"):
            cleaned = cleaned[1:].strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_TAG_LEN:
            raise KnowledgeValidationError(
                f"A etiqueta '{cleaned[:20]}...' excede o limite de {MAX_TAG_LEN} caracteres."
            )
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _parse_tags_json(tags_json: Optional[str]) -> List[str]:
    if not tags_json:
        return []
    try:
        parsed = json.loads(tags_json)
        return [str(t) for t in parsed if isinstance(t, str)] if isinstance(parsed, list) else []
    except Exception:
        return []


def _validate_color(color: Optional[str]) -> str:
    if color is None or not str(color).strip():
        return DEFAULT_CONCEPT_COLOR
    cleaned = normalize_unicode_nfc(str(color)).strip()
    if len(cleaned) > MAX_COLOR_LEN or not _HEX_COLOR_RE.match(cleaned):
        raise KnowledgeValidationError("Cor inválida. Utilize formato hexadecimal (ex.: #d4a853).")
    return cleaned.lower()


def _validate_pagination(limit: Any = DEFAULT_PAGE_LIMIT, offset: Any = 0) -> Tuple[int, int]:
    try:
        lim = int(DEFAULT_PAGE_LIMIT if limit is None else limit)
        off = int(0 if offset is None else offset)
    except (TypeError, ValueError):
        raise KnowledgeValidationError("Parâmetros de paginação (limit/offset) devem ser inteiros.")
    if lim < 1 or lim > MAX_PAGE_LIMIT:
        raise KnowledgeValidationError(
            f"O parâmetro 'limit' deve estar entre 1 e {MAX_PAGE_LIMIT}."
        )
    if off < 0:
        raise KnowledgeValidationError("O parâmetro 'offset' não pode ser negativo.")
    return lim, off


def _validate_entity_type(entity_type: Any) -> str:
    if not isinstance(entity_type, str) or entity_type.strip() not in VALID_ENTITY_TYPES:
        raise KnowledgeValidationError(
            f"Tipo de entidade inválido: '{entity_type}'. Valores permitidos: {', '.join(VALID_ENTITY_TYPES)}."
        )
    return entity_type.strip()


def _validate_relation_type(relation_type: Any) -> str:
    if relation_type is None or (isinstance(relation_type, str) and not relation_type.strip()):
        return "relates_to"
    if not isinstance(relation_type, str) or relation_type.strip() not in VALID_RELATION_TYPES:
        raise KnowledgeValidationError(
            f"Tipo de relação inválido: '{relation_type}'. Valores permitidos: {', '.join(VALID_RELATION_TYPES)}."
        )
    return relation_type.strip()


# =========================================================================
# VERIFICAÇÃO DE USUÁRIO, LIVRO E RESOLUÇÃO CENTRALIZADA DE ENTIDADES
# =========================================================================

def _verify_active_user(cursor: sqlite3.Cursor, user_id: Any) -> int:
    """Garante que `user_id` refere-se a um usuário existente e ativo."""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise KnowledgePermissionError("Identificador de usuário inválido.")
    cursor.execute(
        "SELECT id, is_active FROM users WHERE id = ?;",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row or not bool(row["is_active"]):
        raise KnowledgePermissionError("Usuário inexistente ou desativado.")
    return int(row["id"])


def _check_book_accessible_row(
    cursor: sqlite3.Cursor,
    book_id: str,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Reutiliza obrigatoriamente o mecanismo canônico `_verify_book_access` de `annotations_service.py`
    para verificar se o livro existe, se o arquivo está disponível (`is_missing == 0`), se a fonte
    está ativa (`library_sources.is_active == 1`) e se o usuário está ativo, retornando os metadados
    básicos do livro ou `None` quando o livro estiver indisponível/inexistente.
    """
    if not isinstance(book_id, str) or not book_id.strip():
        return None
    clean_bid = book_id.strip()
    try:
        _canonical_verify_book_access(cursor, clean_bid, user_id=user_id)
    except AnnotationPermissionError as exc:
        # Se a conta do usuário foi desativada, propaga KnowledgePermissionError;
        # se for fonte de livro desativada, o livro é considerado inacessível (None).
        if "usuário desativada" in str(exc).lower():
            raise KnowledgePermissionError(str(exc)) from exc
        return None
    except ValueError:
        return None

    cursor.execute(
        """
        SELECT b.id, b.title, b.author, b.format, b.page_count, b.is_missing,
               COALESCE(s.is_active, 1) AS source_active
        FROM books b
        LEFT JOIN library_sources s ON s.id = b.source_id
        WHERE b.id = ?;
        """,
        (clean_bid,),
    )
    row = cursor.fetchone()
    if not row or bool(row["is_missing"]) or not bool(row["source_active"]):
        return None
    return dict(row)


def _unavailable_entity_sentinel(entity_type: str, entity_id: str) -> Dict[str, Any]:
    """
    Representação neutra, genérica e segura para uma referência cujo extremo foi excluído,
    teve visibilidade pública revogada para privada, ou pertence a um livro/fonte desativada.
    Não expõe título protegido, conteúdo, autor, página, CFI, identificadores protegidos,
    estado atual de publicação nem motivo específico da indisponibilidade.
    """
    return {
        "type": entity_type,
        "id": entity_id,
        "available": False,
        "status": "unavailable",
        "label": "Referência indisponível",
        "is_own": None,
        "user_id": None,
        "author_display_name": None,
        "title": None,
        "excerpt": None,
        "color": None,
        "tags": [],
        "book_id": None,
        "book_title": None,
        "book_format": None,
        "page": None,
        "epub_cfi": None,
        "chapter_label": None,
        "visibility": None,
        "created_at": None,
        "updated_at": None,
    }


def resolve_knowledge_entity(
    entity_type: str,
    entity_id: str,
    user_id: int,
    *,
    cursor: Optional[sqlite3.Cursor] = None,
    for_link_endpoint: bool = False,
) -> Dict[str, Any]:
    """
    Resolve e autoriza uma entidade do domínio de Conhecimento Conectado em tempo de leitura.

    Regras estritas de autorização (*Read-Time Authorization*):
    1. `concept`: estritamente privado do proprietário (`concept.user_id == user_id`).
    2. `reflection`: estritamente privada do proprietário (`reflection.user_id == user_id`).
    3. `book`: deve existir, estar presente no disco (`is_missing = 0`) e pertencer a uma fonte ativa (`is_active = 1`).
    4. `highlight`: o livro deve estar acessível (`_check_book_accessible_row`) E
       `(highlight.user_id == user_id OR highlight.visibility == 'public')`.
    5. `note`: o livro deve estar acessível (`_check_book_accessible_row`) E
       `(note.user_id == user_id OR note.visibility == 'public')`.

    Comportamento quando a entidade não existe ou não está autorizada:
    - Se `for_link_endpoint=False` (ex.: criação de vínculo ou acesso direto): levanta `KnowledgeNotFoundError` (HTTP 404).
    - Se `for_link_endpoint=True` (ex.: leitura de conexão/backlink existente): retorna o sentinela neutro
      `_unavailable_entity_sentinel(entity_type, entity_id)` com `available=False`.
    """
    etype = _validate_entity_type(entity_type)
    if not isinstance(entity_id, str) or not entity_id.strip():
        if for_link_endpoint:
            return _unavailable_entity_sentinel(etype, str(entity_id or ""))
        raise KnowledgeValidationError("Identificador da entidade não pode ser vazio.")
    eid = normalize_unicode_nfc(entity_id).strip()

    if cursor is None:
        with get_db() as conn:
            cur = conn.cursor()
            _verify_active_user(cur, user_id)
            return _resolve_entity_with_cursor(cur, etype, eid, user_id, for_link_endpoint=for_link_endpoint)

    _verify_active_user(cursor, user_id)
    return _resolve_entity_with_cursor(cursor, etype, eid, user_id, for_link_endpoint=for_link_endpoint)


def _resolve_entity_with_cursor(
    cursor: sqlite3.Cursor,
    etype: str,
    eid: str,
    user_id: int,
    *,
    for_link_endpoint: bool = False,
) -> Dict[str, Any]:
    if etype == "concept":
        cursor.execute(
            """
            SELECT id, user_id, name, normalized_name, description, tags_json, color,
                   is_archived, created_at, updated_at
            FROM concepts
            WHERE id = ? AND user_id = ?;
            """,
            (eid, user_id),
        )
        row = cursor.fetchone()
        if not row:
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Conceito não encontrado.")
        return {
            "type": "concept",
            "id": row["id"],
            "available": True,
            "status": "available",
            "is_own": True,
            "user_id": row["user_id"],
            "label": row["name"],
            "title": row["name"],
            "excerpt": (row["description"] or "")[:280],
            "color": row["color"],
            "tags": _parse_tags_json(row["tags_json"]),
            "is_archived": bool(row["is_archived"]),
            "book_id": None,
            "book_title": None,
            "page": None,
            "epub_cfi": None,
            "chapter_label": None,
            "visibility": "private",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    if etype == "reflection":
        cursor.execute(
            """
            SELECT r.id, r.user_id, r.title, r.body_markdown, r.primary_book_id,
                   r.tags_json, r.is_pinned, r.is_archived, r.created_at, r.updated_at
            FROM reflections r
            WHERE r.id = ? AND r.user_id = ?;
            """,
            (eid, user_id),
        )
        row = cursor.fetchone()
        if not row:
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Reflexão não encontrada.")
        book_row = _check_book_accessible_row(cursor, row["primary_book_id"]) if row["primary_book_id"] else None
        return {
            "type": "reflection",
            "id": row["id"],
            "available": True,
            "status": "available",
            "is_own": True,
            "user_id": row["user_id"],
            "label": row["title"],
            "title": row["title"],
            "excerpt": (row["body_markdown"] or "")[:280],
            "tags": _parse_tags_json(row["tags_json"]),
            "is_pinned": bool(row["is_pinned"]),
            "is_archived": bool(row["is_archived"]),
            "book_id": book_row["id"] if book_row else None,
            "book_title": book_row["title"] if book_row else None,
            "primary_book_available": bool(book_row) if row["primary_book_id"] else None,
            "page": None,
            "epub_cfi": None,
            "chapter_label": None,
            "visibility": "private",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    if etype == "book":
        book_row = _check_book_accessible_row(cursor, eid)
        if not book_row:
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Livro não encontrado ou indisponível.")
        return {
            "type": "book",
            "id": book_row["id"],
            "available": True,
            "status": "available",
            "is_own": False,
            "label": book_row["title"],
            "title": book_row["title"],
            "author": book_row["author"],
            "format": book_row["format"],
            "page_count": book_row["page_count"],
            "excerpt": book_row["author"] or "",
            "book_id": book_row["id"],
            "book_title": book_row["title"],
            "page": None,
            "epub_cfi": None,
            "chapter_label": None,
            "visibility": "public",
        }

    if etype == "highlight":
        cursor.execute(
            """
            SELECT h.id, h.book_id, h.page, h.color, h.selected_text, h.text_context,
                   h.user_id, h.visibility, h.epub_cfi, h.chapter_label,
                   h.created_at, h.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM highlights h
            LEFT JOIN users u ON u.id = h.user_id
            LEFT JOIN user_profiles up ON up.user_id = h.user_id
            WHERE h.id = ?;
            """,
            (eid,),
        )
        row = cursor.fetchone()
        if not row:
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Destaque não encontrado.")

        book_row = _check_book_accessible_row(cursor, row["book_id"])
        is_own = int(row["user_id"]) == int(user_id)
        is_public = row["visibility"] == "public"
        if not book_row or not (is_own or is_public):
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Destaque não encontrado.")

        text_preview = (row["selected_text"] or row["text_context"] or "").strip()
        return {
            "type": "highlight",
            "id": row["id"],
            "available": True,
            "status": "available",
            "is_own": is_own,
            "user_id": row["user_id"],
            "author_display_name": row["author_display_name"],
            "label": text_preview[:120] or f"Destaque (p. {row['page']})",
            "title": book_row["title"],
            "excerpt": text_preview[:280],
            "color": row["color"],
            "book_id": book_row["id"],
            "book_title": book_row["title"],
            "book_format": book_row["format"],
            "page": row["page"],
            "epub_cfi": row["epub_cfi"],
            "chapter_label": row["chapter_label"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    if etype == "note":
        cursor.execute(
            """
            SELECT n.id, n.book_id, n.page, n.highlight_id, n.content, n.color,
                   n.user_id, n.visibility, n.epub_cfi, n.chapter_label,
                   n.created_at, n.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM notes n
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN user_profiles up ON up.user_id = n.user_id
            WHERE n.id = ?;
            """,
            (eid,),
        )
        row = cursor.fetchone()
        if not row:
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Nota não encontrada.")

        book_row = _check_book_accessible_row(cursor, row["book_id"])
        is_own = int(row["user_id"]) == int(user_id)
        is_public = row["visibility"] == "public"
        if not book_row or not (is_own or is_public):
            if for_link_endpoint:
                return _unavailable_entity_sentinel(etype, eid)
            raise KnowledgeNotFoundError("Nota não encontrada.")

        content_preview = (row["content"] or "").strip()
        return {
            "type": "note",
            "id": row["id"],
            "available": True,
            "status": "available",
            "is_own": is_own,
            "user_id": row["user_id"],
            "author_display_name": row["author_display_name"],
            "highlight_id": row["highlight_id"],
            "label": content_preview[:120] or f"Nota (p. {row['page']})",
            "title": book_row["title"],
            "excerpt": content_preview[:280],
            "color": row["color"],
            "book_id": book_row["id"],
            "book_title": book_row["title"],
            "book_format": book_row["format"],
            "page": row["page"],
            "epub_cfi": row["epub_cfi"],
            "chapter_label": row["chapter_label"],
            "visibility": row["visibility"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    if for_link_endpoint:
        return _unavailable_entity_sentinel(etype, eid)
    raise KnowledgeValidationError(f"Tipo de entidade não suportado: '{etype}'.")


# =========================================================================
# OPERAÇÕES DE CONCEITOS (`concepts` — KD-002)
# =========================================================================

def _format_concept_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    tags = _parse_tags_json(d.pop("tags_json", "[]"))
    d["tags"] = tags
    d["is_archived"] = bool(d.get("is_archived", 0))
    return d


def create_concept(
    user_id: int,
    name: str,
    description: str = "",
    tags: Optional[Sequence[str]] = None,
    color: Optional[str] = None,
    is_archived: bool = False,
) -> Dict[str, Any]:
    """Cria um novo conceito privado para o usuário autenticado."""
    cleaned_name, normalized_name = normalize_concept_name(name)
    cleaned_desc = normalize_unicode_nfc(description or "").strip()
    if len(cleaned_desc) > MAX_CONCEPT_DESC_LEN:
        raise KnowledgeValidationError(
            f"A descrição do conceito excede o limite de {MAX_CONCEPT_DESC_LEN} caracteres."
        )
    normalized_tags = _validate_and_normalize_tags(tags)
    validated_color = _validate_color(color)
    archived_flag = 1 if bool(is_archived) else 0

    concept_id = str(uuid.uuid4())
    now = _now_iso()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        cursor.execute(
            "SELECT id FROM concepts WHERE user_id = ? AND normalized_name = ?;",
            (user_id, normalized_name),
        )
        if cursor.fetchone():
            raise KnowledgeConflictError(
                f"Já existe um conceito com o nome '{cleaned_name}' no seu Atlas."
            )

        try:
            cursor.execute(
                """
                INSERT INTO concepts (
                    id, user_id, name, normalized_name, description,
                    tags_json, color, is_archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    concept_id,
                    user_id,
                    cleaned_name,
                    normalized_name,
                    cleaned_desc,
                    json.dumps(normalized_tags, ensure_ascii=False),
                    validated_color,
                    archived_flag,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeConflictError(
                f"Já existe um conceito com o nome '{cleaned_name}' no seu Atlas."
            ) from exc

        cursor.execute("SELECT * FROM concepts WHERE id = ? AND user_id = ?;", (concept_id, user_id))
        created = _format_concept_row(cursor.fetchone())
        created["links_count"] = 0
        return created


def get_concept(concept_id: str, user_id: int) -> Dict[str, Any]:
    """Obtém um conceito do próprio usuário com contagem de conexões."""
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise KnowledgeValidationError("Identificador do conceito inválido.")
    cid = normalize_unicode_nfc(concept_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM concepts WHERE id = ? AND user_id = ?;",
            (cid, user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise KnowledgeNotFoundError("Conceito não encontrado.")

        concept = _format_concept_row(row)
        cursor.execute(
            """
            SELECT COUNT(*) FROM knowledge_links
            WHERE user_id = ?
              AND ((source_type = 'concept' AND source_id = ?)
                   OR (target_type = 'concept' AND target_id = ?));
            """,
            (user_id, cid, cid),
        )
        concept["links_count"] = int(cursor.fetchone()[0])
        return concept


def list_concepts(
    user_id: int,
    *,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    include_archived: bool = False,
    sort_by: str = "updated_desc",
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Lista conceitos do usuário autenticado com paginação, ordenação determinística
    e contagem de vínculos em lote (zero N+1).
    """
    lim, off = _validate_pagination(limit, offset)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        where_clauses = ["c.user_id = ?"]
        params: List[Any] = [user_id]

        if not include_archived:
            where_clauses.append("c.is_archived = 0")

        where_sql = " AND ".join(where_clauses)

        if sort_by not in VALID_CONCEPT_SORTS:
            raise KnowledgeValidationError(
                f"Modo de ordenação inválido para conceitos: '{sort_by}'. Valores permitidos: {', '.join(VALID_CONCEPT_SORTS)}."
            )
        if sort_by == "name_asc":
            order_sql = "c.normalized_name ASC, c.id ASC"
        elif sort_by == "created_desc":
            order_sql = "c.created_at DESC, c.id ASC"
        else:
            order_sql = "c.updated_at DESC, c.id ASC"

        has_tag_filter = tag is not None and bool(str(tag).strip())
        has_search_filter = search is not None and bool(str(search).strip())

        if not has_tag_filter and not has_search_filter:
            cursor.execute(
                f"SELECT COUNT(*) FROM concepts c WHERE {where_sql};",
                tuple(params),
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT c.*
                FROM concepts c
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?;
                """,
                tuple([*params, lim, off]),
            )
            page_items = [_format_concept_row(r) for r in cursor.fetchall()]
        else:
            cursor.execute(
                f"""
                SELECT c.*
                FROM concepts c
                WHERE {where_sql}
                ORDER BY {order_sql};
                """,
                tuple(params),
            )
            all_rows = [_format_concept_row(r) for r in cursor.fetchall()]

            # Filtragem textual e por etiqueta em Python com Unicode NFD (insensível a acentos e caixa)
            if has_tag_filter:
                norm_tag = normalize_unicode_nfc(str(tag)).strip().lstrip("#").casefold()
                all_rows = [
                    c for c in all_rows
                    if any(t.casefold() == norm_tag for t in c["tags"])
                ]

            if has_search_filter:
                q_folded = strip_diacritics_casefold(normalize_unicode_nfc(str(search)).strip())
                if q_folded:
                    filtered = []
                    for c in all_rows:
                        haystack = " ".join(
                            [c["name"], c.get("description", ""), " ".join(c["tags"])]
                        )
                        if q_folded in strip_diacritics_casefold(haystack):
                            filtered.append(c)
                    all_rows = filtered

            total = len(all_rows)
            page_items = all_rows[off : off + lim]

        # Contagem de conexões em lote (zero N+1)
        if page_items:
            cids = [c["id"] for c in page_items]
            placeholders = ",".join("?" for _ in cids)
            counts_map: Dict[str, int] = {cid: 0 for cid in cids}
            cursor.execute(
                f"""
                SELECT source_id AS cid, COUNT(*) AS cnt
                FROM knowledge_links
                WHERE user_id = ? AND source_type = 'concept' AND source_id IN ({placeholders})
                GROUP BY source_id;
                """,
                (user_id, *cids),
            )
            for r in cursor.fetchall():
                counts_map[r["cid"]] = counts_map.get(r["cid"], 0) + int(r["cnt"])

            cursor.execute(
                f"""
                SELECT target_id AS cid, COUNT(*) AS cnt
                FROM knowledge_links
                WHERE user_id = ? AND target_type = 'concept' AND target_id IN ({placeholders})
                GROUP BY target_id;
                """,
                (user_id, *cids),
            )
            for r in cursor.fetchall():
                counts_map[r["cid"]] = counts_map.get(r["cid"], 0) + int(r["cnt"])

            for item in page_items:
                item["links_count"] = counts_map.get(item["id"], 0)

        return {
            "items": page_items,
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(page_items)) < total,
        }


def update_concept(
    concept_id: str,
    user_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    color: Optional[str] = None,
    is_archived: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Atualiza campos de um conceito do próprio usuário, preservando seu UUID (`id`)
    e mantendo todas as conexões existentes intactas.
    """
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise KnowledgeValidationError("Identificador do conceito inválido.")
    cid = normalize_unicode_nfc(concept_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM concepts WHERE id = ? AND user_id = ?;",
            (cid, user_id),
        )
        existing = cursor.fetchone()
        if not existing:
            raise KnowledgeNotFoundError("Conceito não encontrado.")

        new_name = existing["name"]
        new_norm_name = existing["normalized_name"]
        if name is not None:
            new_name, new_norm_name = normalize_concept_name(name)
            cursor.execute(
                "SELECT id FROM concepts WHERE user_id = ? AND normalized_name = ? AND id != ?;",
                (user_id, new_norm_name, cid),
            )
            if cursor.fetchone():
                raise KnowledgeConflictError(
                    f"Já existe outro conceito com o nome '{new_name}' no seu Atlas."
                )

        new_desc = existing["description"]
        if description is not None:
            new_desc = normalize_unicode_nfc(description).strip()
            if len(new_desc) > MAX_CONCEPT_DESC_LEN:
                raise KnowledgeValidationError(
                    f"A descrição do conceito excede o limite de {MAX_CONCEPT_DESC_LEN} caracteres."
                )

        new_tags_json = existing["tags_json"]
        if tags is not None:
            new_tags_json = json.dumps(_validate_and_normalize_tags(tags), ensure_ascii=False)

        new_color = existing["color"]
        if color is not None:
            new_color = _validate_color(color)

        new_archived = int(existing["is_archived"])
        if is_archived is not None:
            new_archived = 1 if bool(is_archived) else 0

        now = _now_iso()
        try:
            cursor.execute(
                """
                UPDATE concepts
                SET name = ?,
                    normalized_name = ?,
                    description = ?,
                    tags_json = ?,
                    color = ?,
                    is_archived = ?,
                    updated_at = ?
                WHERE id = ? AND user_id = ?;
                """,
                (
                    new_name,
                    new_norm_name,
                    new_desc,
                    new_tags_json,
                    new_color,
                    new_archived,
                    now,
                    cid,
                    user_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeConflictError(
                f"Já existe outro conceito com o nome '{new_name}' no seu Atlas."
            ) from exc

        cursor.execute("SELECT * FROM concepts WHERE id = ? AND user_id = ?;", (cid, user_id))
        updated = _format_concept_row(cursor.fetchone())
        cursor.execute(
            """
            SELECT COUNT(*) FROM knowledge_links
            WHERE user_id = ?
              AND ((source_type = 'concept' AND source_id = ?)
                   OR (target_type = 'concept' AND target_id = ?));
            """,
            (user_id, cid, cid),
        )
        updated["links_count"] = int(cursor.fetchone()[0])
        return updated


def delete_concept(concept_id: str, user_id: int) -> Dict[str, Any]:
    """
    Exclui um conceito do próprio usuário.
    O trigger `trg_concepts_ad_cleanup` remove automaticamente seus vínculos em `knowledge_links`
    e sua entrada em `knowledge_fts`.
    """
    if not isinstance(concept_id, str) or not concept_id.strip():
        raise KnowledgeValidationError("Identificador do conceito inválido.")
    cid = normalize_unicode_nfc(concept_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT id FROM concepts WHERE id = ? AND user_id = ?;",
            (cid, user_id),
        )
        if not cursor.fetchone():
            raise KnowledgeNotFoundError("Conceito não encontrado.")

        cursor.execute("DELETE FROM concepts WHERE id = ? AND user_id = ?;", (cid, user_id))
        return {"deleted": True, "id": cid}


def search_concepts_for_lookup(
    user_id: int,
    query: str = "",
    *,
    limit: int = AUX_SEARCH_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Busca auxiliar rápida de conceitos do próprio usuário para seletores assistidos na UI,
    limitada a no máximo `AUX_SEARCH_LIMIT` (50) itens.
    """
    effective_limit = max(1, min(int(limit or AUX_SEARCH_LIMIT), AUX_SEARCH_LIMIT))
    res = list_concepts(
        user_id,
        search=query,
        include_archived=False,
        sort_by="name_asc",
        limit=effective_limit,
        offset=0,
    )
    return [
        {
            "id": item["id"],
            "type": "concept",
            "name": item["name"],
            "color": item["color"],
            "tags": item["tags"],
            "links_count": item.get("links_count", 0),
        }
        for item in res["items"]
    ]


# =========================================================================
# OPERAÇÕES DE REFLEXÕES AUTORAIS (`reflections` — KD-005)
# =========================================================================

def _format_reflection_row(row: sqlite3.Row, cursor: Optional[sqlite3.Cursor] = None) -> Dict[str, Any]:
    d = dict(row)
    tags = _parse_tags_json(d.pop("tags_json", "[]"))
    d["tags"] = tags
    d["is_pinned"] = bool(d.get("is_pinned", 0))
    d["is_archived"] = bool(d.get("is_archived", 0))
    if cursor is not None and d.get("primary_book_id"):
        book = _check_book_accessible_row(cursor, d["primary_book_id"])
        d["primary_book_title"] = book["title"] if book else None
        d["primary_book_available"] = bool(book)
    else:
        d["primary_book_title"] = None
        d["primary_book_available"] = None
    return d


def _validate_reflection_title(title: Any) -> str:
    raw = normalize_unicode_nfc(title)
    cleaned = _WHITESPACE_RE.sub(" ", raw.strip())
    if not cleaned:
        raise KnowledgeValidationError("O título da reflexão não pode ser vazio.")
    if len(cleaned) > MAX_REFLECTION_TITLE_LEN:
        raise KnowledgeValidationError(
            f"O título da reflexão excede o limite de {MAX_REFLECTION_TITLE_LEN} caracteres."
        )
    return cleaned


def _validate_reflection_body(body_markdown: Any) -> str:
    raw = normalize_unicode_nfc("" if body_markdown is None else body_markdown)
    if len(raw) > MAX_REFLECTION_BODY_LEN:
        raise KnowledgeValidationError(
            f"O corpo da reflexão excede o limite de {MAX_REFLECTION_BODY_LEN} caracteres."
        )
    return raw


def create_reflection(
    user_id: int,
    title: str,
    body_markdown: str = "",
    *,
    primary_book_id: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    is_pinned: bool = False,
    is_archived: bool = False,
) -> Dict[str, Any]:
    """Cria uma nova reflexão autoral privada em Markdown para o usuário autenticado."""
    cleaned_title = _validate_reflection_title(title)
    cleaned_body = _validate_reflection_body(body_markdown)
    normalized_tags = _validate_and_normalize_tags(tags)
    pinned_flag = 1 if bool(is_pinned) else 0
    archived_flag = 1 if bool(is_archived) else 0

    reflection_id = str(uuid.uuid4())
    now = _now_iso()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        validated_book_id: Optional[str] = None
        if primary_book_id is not None and str(primary_book_id).strip():
            b_id = normalize_unicode_nfc(str(primary_book_id)).strip()
            book = _check_book_accessible_row(cursor, b_id)
            if not book:
                raise KnowledgeNotFoundError("Livro principal indicado não existe ou não está disponível.")
            validated_book_id = book["id"]

        cursor.execute(
            """
            INSERT INTO reflections (
                id, user_id, title, body_markdown, primary_book_id,
                tags_json, is_pinned, is_archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                reflection_id,
                user_id,
                cleaned_title,
                cleaned_body,
                validated_book_id,
                json.dumps(normalized_tags, ensure_ascii=False),
                pinned_flag,
                archived_flag,
                now,
                now,
            ),
        )

        cursor.execute("SELECT * FROM reflections WHERE id = ? AND user_id = ?;", (reflection_id, user_id))
        created = _format_reflection_row(cursor.fetchone(), cursor)
        created["references_count"] = 0
        created["backlinks_count"] = 0
        return created


def get_reflection(reflection_id: str, user_id: int) -> Dict[str, Any]:
    """Obtém uma reflexão autoral do próprio usuário."""
    if not isinstance(reflection_id, str) or not reflection_id.strip():
        raise KnowledgeValidationError("Identificador da reflexão inválido.")
    rid = normalize_unicode_nfc(reflection_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM reflections WHERE id = ? AND user_id = ?;",
            (rid, user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise KnowledgeNotFoundError("Reflexão não encontrada.")

        reflection = _format_reflection_row(row, cursor)
        cursor.execute(
            """
            SELECT COUNT(*) FROM knowledge_links
            WHERE user_id = ? AND source_type = 'reflection' AND source_id = ?;
            """,
            (user_id, rid),
        )
        reflection["references_count"] = int(cursor.fetchone()[0])

        cursor.execute(
            """
            SELECT COUNT(*) FROM knowledge_links
            WHERE user_id = ? AND target_type = 'reflection' AND target_id = ?;
            """,
            (user_id, rid),
        )
        reflection["backlinks_count"] = int(cursor.fetchone()[0])
        return reflection


def list_reflections(
    user_id: int,
    *,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    primary_book_id: Optional[str] = None,
    include_archived: bool = False,
    sort_by: str = "updated_desc",
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """Lista reflexões autorais do próprio usuário com paginação e ordenação determinística."""
    lim, off = _validate_pagination(limit, offset)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        where_clauses = ["r.user_id = ?"]
        params: List[Any] = [user_id]

        if not include_archived:
            where_clauses.append("r.is_archived = 0")

        if primary_book_id is not None and str(primary_book_id).strip():
            where_clauses.append("r.primary_book_id = ?")
            params.append(normalize_unicode_nfc(str(primary_book_id)).strip())

        where_sql = " AND ".join(where_clauses)

        if sort_by not in VALID_REFLECTION_SORTS:
            raise KnowledgeValidationError(
                f"Modo de ordenação inválido para reflexões: '{sort_by}'. Valores permitidos: {', '.join(VALID_REFLECTION_SORTS)}."
            )
        if sort_by == "title_asc":
            order_sql = "r.title COLLATE NOCASE ASC, r.id ASC"
        elif sort_by == "created_desc":
            order_sql = "r.is_pinned DESC, r.created_at DESC, r.id ASC"
        else:
            order_sql = "r.is_pinned DESC, r.updated_at DESC, r.id ASC"

        has_tag_filter = tag is not None and bool(str(tag).strip())
        has_search_filter = search is not None and bool(str(search).strip())

        if not has_tag_filter and not has_search_filter:
            cursor.execute(
                f"SELECT COUNT(*) FROM reflections r WHERE {where_sql};",
                tuple(params),
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT r.*
                FROM reflections r
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?;
                """,
                tuple([*params, lim, off]),
            )
            page_items = [_format_reflection_row(r, None) for r in cursor.fetchall()]
        else:
            cursor.execute(
                f"""
                SELECT r.*
                FROM reflections r
                WHERE {where_sql}
                ORDER BY {order_sql};
                """,
                tuple(params),
            )
            all_rows = [_format_reflection_row(r, None) for r in cursor.fetchall()]

            if has_tag_filter:
                norm_tag = normalize_unicode_nfc(str(tag)).strip().lstrip("#").casefold()
                all_rows = [
                    ref for ref in all_rows
                    if any(t.casefold() == norm_tag for t in ref["tags"])
                ]

            if has_search_filter:
                q_folded = strip_diacritics_casefold(normalize_unicode_nfc(str(search)).strip())
                if q_folded:
                    filtered = []
                    for ref in all_rows:
                        haystack = " ".join(
                            [ref["title"], ref.get("body_markdown", ""), " ".join(ref["tags"])]
                        )
                        if q_folded in strip_diacritics_casefold(haystack):
                            filtered.append(ref)
                    all_rows = filtered

            total = len(all_rows)
            page_items = all_rows[off : off + lim]

        # Hidratação em lote de `primary_book_title` / `primary_book_available` (zero N+1)
        if page_items:
            pbook_ids = sorted({r["primary_book_id"] for r in page_items if r.get("primary_book_id")})
            accessible_books: Dict[str, str] = {}
            for chunk in _chunked(pbook_ids):
                bph = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT b.id, b.title
                    FROM books b
                    LEFT JOIN library_sources s ON s.id = b.source_id
                    WHERE b.id IN ({bph})
                      AND b.is_missing = 0
                      AND COALESCE(s.is_active, 1) = 1;
                    """,
                    tuple(chunk),
                )
                for brow in cursor.fetchall():
                    accessible_books[brow["id"]] = brow["title"]

            for item in page_items:
                pbid = item.get("primary_book_id")
                if pbid:
                    btitle = accessible_books.get(pbid)
                    item["primary_book_title"] = btitle
                    item["primary_book_available"] = btitle is not None

        # Contagens de referências e backlinks em lote (zero N+1)
        if page_items:
            rids = [r["id"] for r in page_items]
            placeholders = ",".join("?" for _ in rids)
            ref_counts: Dict[str, int] = {rid: 0 for rid in rids}
            back_counts: Dict[str, int] = {rid: 0 for rid in rids}

            cursor.execute(
                f"""
                SELECT source_id AS rid, COUNT(*) AS cnt
                FROM knowledge_links
                WHERE user_id = ? AND source_type = 'reflection' AND source_id IN ({placeholders})
                GROUP BY source_id;
                """,
                (user_id, *rids),
            )
            for row in cursor.fetchall():
                ref_counts[row["rid"]] = int(row["cnt"])

            cursor.execute(
                f"""
                SELECT target_id AS rid, COUNT(*) AS cnt
                FROM knowledge_links
                WHERE user_id = ? AND target_type = 'reflection' AND target_id IN ({placeholders})
                GROUP BY target_id;
                """,
                (user_id, *rids),
            )
            for row in cursor.fetchall():
                back_counts[row["rid"]] = int(row["cnt"])

            for item in page_items:
                item["references_count"] = ref_counts.get(item["id"], 0)
                item["backlinks_count"] = back_counts.get(item["id"], 0)

        return {
            "items": page_items,
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(page_items)) < total,
        }


def update_reflection(
    reflection_id: str,
    user_id: int,
    *,
    title: Optional[str] = None,
    body_markdown: Optional[str] = None,
    primary_book_id: Any = ...,
    tags: Optional[Sequence[str]] = None,
    is_pinned: Optional[bool] = None,
    is_archived: Optional[bool] = None,
    expected_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atualiza uma reflexão autoral do próprio usuário preservando seu UUID (`id`).
    Quando `expected_updated_at` é informado, aplica controle otimista de concorrência
    por atualização condicional atômica (`WHERE id = ? AND user_id = ? AND updated_at = ?`),
    levantando `KnowledgeConflictError` (HTTP 409) caso a reflexão tenha sido modificada
    concorrentemente em outra aba ou sessão.
    """
    if not isinstance(reflection_id, str) or not reflection_id.strip():
        raise KnowledgeValidationError("Identificador da reflexão inválido.")
    rid = normalize_unicode_nfc(reflection_id).strip()
    clean_expected_ts: Optional[str] = None
    if expected_updated_at is not None:
        if not isinstance(expected_updated_at, str) or not expected_updated_at.strip():
            raise KnowledgeValidationError("O parâmetro 'expected_updated_at' deve ser uma string não vazia.")
        clean_expected_ts = normalize_unicode_nfc(expected_updated_at).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM reflections WHERE id = ? AND user_id = ?;",
            (rid, user_id),
        )
        existing = cursor.fetchone()
        if not existing:
            raise KnowledgeNotFoundError("Reflexão não encontrada.")

        new_title = existing["title"] if title is None else _validate_reflection_title(title)
        new_body = (
            existing["body_markdown"]
            if body_markdown is None
            else _validate_reflection_body(body_markdown)
        )

        new_book_id = existing["primary_book_id"]
        if primary_book_id is not ...:
            if primary_book_id is None or not str(primary_book_id).strip():
                new_book_id = None
            else:
                b_id = normalize_unicode_nfc(str(primary_book_id)).strip()
                book = _check_book_accessible_row(cursor, b_id)
                if not book:
                    raise KnowledgeNotFoundError("Livro principal indicado não existe ou não está disponível.")
                new_book_id = book["id"]

        new_tags_json = existing["tags_json"]
        if tags is not None:
            new_tags_json = json.dumps(_validate_and_normalize_tags(tags), ensure_ascii=False)

        new_pinned = int(existing["is_pinned"]) if is_pinned is None else (1 if bool(is_pinned) else 0)
        new_archived = int(existing["is_archived"]) if is_archived is None else (1 if bool(is_archived) else 0)
        now = _next_monotonic_iso(existing["updated_at"])

        if clean_expected_ts is not None:
            cursor.execute(
                """
                UPDATE reflections
                SET title = ?,
                    body_markdown = ?,
                    primary_book_id = ?,
                    tags_json = ?,
                    is_pinned = ?,
                    is_archived = ?,
                    updated_at = ?
                WHERE id = ? AND user_id = ? AND updated_at = ?;
                """,
                (
                    new_title,
                    new_body,
                    new_book_id,
                    new_tags_json,
                    new_pinned,
                    new_archived,
                    now,
                    rid,
                    user_id,
                    clean_expected_ts,
                ),
            )
            if cursor.rowcount == 0:
                raise KnowledgeConflictError(
                    "Conflito de concorrência: esta reflexão foi modificada em outra aba ou sessão. "
                    "Seu conteúdo local foi preservado; clique em 'Salvar reflexão' novamente para confirmar a sobrescrita."
                )
        else:
            cursor.execute(
                """
                UPDATE reflections
                SET title = ?,
                    body_markdown = ?,
                    primary_book_id = ?,
                    tags_json = ?,
                    is_pinned = ?,
                    is_archived = ?,
                    updated_at = ?
                WHERE id = ? AND user_id = ?;
                """,
                (
                    new_title,
                    new_body,
                    new_book_id,
                    new_tags_json,
                    new_pinned,
                    new_archived,
                    now,
                    rid,
                    user_id,
                ),
            )

    return get_reflection(rid, user_id)


def delete_reflection(reflection_id: str, user_id: int) -> Dict[str, Any]:
    """
    Exclui uma reflexão do próprio usuário.
    O trigger `trg_reflections_ad_cleanup` remove automaticamente suas conexões em `knowledge_links`
    e sua entrada em `knowledge_fts`.
    """
    if not isinstance(reflection_id, str) or not reflection_id.strip():
        raise KnowledgeValidationError("Identificador da reflexão inválido.")
    rid = normalize_unicode_nfc(reflection_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT id FROM reflections WHERE id = ? AND user_id = ?;",
            (rid, user_id),
        )
        if not cursor.fetchone():
            raise KnowledgeNotFoundError("Reflexão não encontrada.")

        cursor.execute("DELETE FROM reflections WHERE id = ? AND user_id = ?;", (rid, user_id))
        return {"deleted": True, "id": rid}


def get_reflection_references(reflection_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Lista todas as referências estruturadas que saem de uma reflexão (`source_type = 'reflection'`),
    resolvendo cada entidade alvo com autorização em tempo de leitura.
    """
    get_reflection(reflection_id, user_id)
    return list_outgoing_links("reflection", reflection_id, user_id)


def get_reflection_backlinks(reflection_id: str, user_id: int) -> List[Dict[str, Any]]:
    """
    Lista todos os backlinks que apontam para uma reflexão (`target_type = 'reflection'`),
    resolvendo cada entidade de origem com autorização em tempo de leitura.
    """
    get_reflection(reflection_id, user_id)
    return list_incoming_links("reflection", reflection_id, user_id)


def search_reflections_for_lookup(
    user_id: int,
    query: str = "",
    *,
    limit: int = AUX_SEARCH_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Busca auxiliar rápida de reflexões do próprio usuário para seletores assistidos na UI,
    limitada a no máximo `AUX_SEARCH_LIMIT` (50) itens.
    """
    effective_limit = max(1, min(int(limit or AUX_SEARCH_LIMIT), AUX_SEARCH_LIMIT))
    res = list_reflections(
        user_id,
        search=query,
        include_archived=False,
        sort_by="updated_desc",
        limit=effective_limit,
        offset=0,
    )
    return [
        {
            "id": item["id"],
            "type": "reflection",
            "title": item["title"],
            "primary_book_id": item.get("primary_book_id"),
            "tags": item["tags"],
        }
        for item in res["items"]
    ]


# =========================================================================
# OPERAÇÕES DE CONEXÕES E BACKLINKS (`knowledge_links` — KD-003 e KD-004)
# =========================================================================

def _validate_rationale(rationale: Any) -> str:
    raw = normalize_unicode_nfc("" if rationale is None else rationale).strip()
    if len(raw) > MAX_LINK_RATIONALE_LEN:
        raise KnowledgeValidationError(
            f"A justificativa da conexão excede o limite de {MAX_LINK_RATIONALE_LEN} caracteres."
        )
    return raw


def _hydrate_link_row(
    cursor: sqlite3.Cursor,
    row: sqlite3.Row,
    user_id: int,
    *,
    perspective_type: Optional[str] = None,
    perspective_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enriquece um registro de `knowledge_links` resolvendo `source` e `target` em tempo de leitura.
    Se `perspective_type` e `perspective_id` forem informados, calcula a direção (`outgoing` vs `incoming`)
    e o rótulo em português correspondente.
    """
    d = dict(row)
    rel = d["relation_type"]
    labels = RELATION_LABELS_PT.get(rel, RELATION_LABELS_PT["relates_to"])

    source_resolved = resolve_knowledge_entity(
        d["source_type"],
        d["source_id"],
        user_id,
        cursor=cursor,
        for_link_endpoint=True,
    )
    target_resolved = resolve_knowledge_entity(
        d["target_type"],
        d["target_id"],
        user_id,
        cursor=cursor,
        for_link_endpoint=True,
    )

    direction = "outgoing"
    relation_label = labels["outgoing"]
    counterpart = target_resolved
    if (
        perspective_type is not None
        and perspective_id is not None
        and d["target_type"] == perspective_type
        and d["target_id"] == perspective_id
    ):
        direction = "incoming"
        relation_label = labels["incoming"]
        counterpart = source_resolved

    return {
        "id": d["id"],
        "user_id": d["user_id"],
        "source_type": d["source_type"],
        "source_id": d["source_id"],
        "source": source_resolved,
        "target_type": d["target_type"],
        "target_id": d["target_id"],
        "target": target_resolved,
        "relation_type": rel,
        "relation_label": relation_label,
        "outgoing_label": labels["outgoing"],
        "incoming_label": labels["incoming"],
        "direction": direction,
        "counterpart": counterpart,
        "rationale": d["rationale"] or "",
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }


def create_knowledge_link(
    user_id: int,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation_type: str = "relates_to",
    rationale: str = "",
) -> Dict[str, Any]:
    """
    Cria uma conexão direcionada e opcionalmente justificada entre duas entidades autorizadas.
    - Bloqueia auto-referência (`source == target`).
    - Valida que ambas as entidades existem e que o usuário tem permissão de leitura sobre ambas.
    - Bloqueia duplicatas exatas `(user_id, source_type, source_id, target_type, target_id, relation_type)`.
    """
    stype = _validate_entity_type(source_type)
    ttype = _validate_entity_type(target_type)
    rel_type = _validate_relation_type(relation_type)
    cleaned_rationale = _validate_rationale(rationale)

    if not isinstance(source_id, str) or not source_id.strip():
        raise KnowledgeValidationError("Identificador da entidade de origem não pode ser vazio.")
    if not isinstance(target_id, str) or not target_id.strip():
        raise KnowledgeValidationError("Identificador da entidade de destino não pode ser vazio.")

    sid = normalize_unicode_nfc(source_id).strip()
    tid = normalize_unicode_nfc(target_id).strip()

    if stype == ttype and sid == tid:
        raise KnowledgeValidationError(
            "Não é permitido criar uma conexão de uma entidade consigo mesma (auto-referência)."
        )

    link_id = str(uuid.uuid4())
    now = _now_iso()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        # Valida existência e autorização em tempo real dos dois extremos (levanta 404 se privado/inexistente)
        resolve_knowledge_entity(stype, sid, user_id, cursor=cursor, for_link_endpoint=False)
        resolve_knowledge_entity(ttype, tid, user_id, cursor=cursor, for_link_endpoint=False)

        cursor.execute(
            """
            SELECT id FROM knowledge_links
            WHERE user_id = ?
              AND source_type = ? AND source_id = ?
              AND target_type = ? AND target_id = ?
              AND relation_type = ?;
            """,
            (user_id, stype, sid, ttype, tid, rel_type),
        )
        if cursor.fetchone():
            raise KnowledgeConflictError(
                "Esta conexão com o mesmo tipo de relação já existe no seu Atlas."
            )

        try:
            cursor.execute(
                """
                INSERT INTO knowledge_links (
                    id, user_id, source_type, source_id,
                    target_type, target_id, relation_type, rationale,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    link_id,
                    user_id,
                    stype,
                    sid,
                    ttype,
                    tid,
                    rel_type,
                    cleaned_rationale,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise KnowledgeConflictError(
                "Esta conexão com o mesmo tipo de relação já existe no seu Atlas."
            ) from exc

        cursor.execute(
            "SELECT * FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (link_id, user_id),
        )
        return _hydrate_link_row(cursor, cursor.fetchone(), user_id)


def get_knowledge_link(link_id: str, user_id: int) -> Dict[str, Any]:
    """Obtém uma conexão do próprio usuário com seus extremos resolvidos em tempo de leitura."""
    if not isinstance(link_id, str) or not link_id.strip():
        raise KnowledgeValidationError("Identificador da conexão inválido.")
    lid = normalize_unicode_nfc(link_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (lid, user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise KnowledgeNotFoundError("Conexão não encontrada.")
        return _hydrate_link_row(cursor, row, user_id)


def update_knowledge_link(
    link_id: str,
    user_id: int,
    *,
    relation_type: Optional[str] = None,
    rationale: Optional[str] = None,
    **extra_kwargs: Any,
) -> Dict[str, Any]:
    """
    Atualiza exclusivamente `relation_type` e/ou `rationale` de uma conexão pertencente ao usuário.
    Rejeita qualquer tentativa de alterar `user_id`, `source_type`, `source_id`, `target_type` ou `target_id`.
    """
    forbidden_fields = {
        "user_id",
        "owner_user_id",
        "source_type",
        "source_id",
        "target_type",
        "target_id",
        "id",
    }
    attempted_forbidden = forbidden_fields.intersection(extra_kwargs.keys())
    if attempted_forbidden:
        raise KnowledgeValidationError(
            f"Não é permitido alterar os extremos ou o proprietário de uma conexão existente ({', '.join(sorted(attempted_forbidden))})."
        )
    if extra_kwargs:
        raise KnowledgeValidationError(
            f"Campos não reconhecidos na atualização de conexão: {', '.join(sorted(extra_kwargs.keys()))}."
        )

    if not isinstance(link_id, str) or not link_id.strip():
        raise KnowledgeValidationError("Identificador da conexão inválido.")
    lid = normalize_unicode_nfc(link_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT * FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (lid, user_id),
        )
        existing = cursor.fetchone()
        if not existing:
            raise KnowledgeNotFoundError("Conexão não encontrada.")

        new_rel = (
            existing["relation_type"]
            if relation_type is None
            else _validate_relation_type(relation_type)
        )
        new_rationale = (
            existing["rationale"]
            if rationale is None
            else _validate_rationale(rationale)
        )

        if new_rel != existing["relation_type"]:
            cursor.execute(
                """
                SELECT id FROM knowledge_links
                WHERE user_id = ?
                  AND source_type = ? AND source_id = ?
                  AND target_type = ? AND target_id = ?
                  AND relation_type = ?
                  AND id != ?;
                """,
                (
                    user_id,
                    existing["source_type"],
                    existing["source_id"],
                    existing["target_type"],
                    existing["target_id"],
                    new_rel,
                    lid,
                ),
            )
            if cursor.fetchone():
                raise KnowledgeConflictError(
                    "Já existe outra conexão entre estes itens com o tipo de relação selecionado."
                )

        now = _now_iso()
        cursor.execute(
            """
            UPDATE knowledge_links
            SET relation_type = ?,
                rationale = ?,
                updated_at = ?
            WHERE id = ? AND user_id = ?;
            """,
            (new_rel, new_rationale, now, lid, user_id),
        )

        cursor.execute(
            "SELECT * FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (lid, user_id),
        )
        return _hydrate_link_row(cursor, cursor.fetchone(), user_id)


def delete_knowledge_link(link_id: str, user_id: int) -> Dict[str, Any]:
    """Remove uma conexão pertencente ao próprio usuário."""
    if not isinstance(link_id, str) or not link_id.strip():
        raise KnowledgeValidationError("Identificador da conexão inválido.")
    lid = normalize_unicode_nfc(link_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        cursor.execute(
            "SELECT id FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (lid, user_id),
        )
        if not cursor.fetchone():
            raise KnowledgeNotFoundError("Conexão não encontrada.")

        cursor.execute(
            "DELETE FROM knowledge_links WHERE id = ? AND user_id = ?;",
            (lid, user_id),
        )
        return {"deleted": True, "id": lid}


def list_outgoing_links(
    entity_type: str,
    entity_id: str,
    user_id: int,
    *,
    relation_type: Optional[str] = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Lista conexões de saída (`source_type = entity_type AND source_id = entity_id`) do usuário."""
    etype = _validate_entity_type(entity_type)
    eid = normalize_unicode_nfc(entity_id).strip()
    lim, off = _validate_pagination(limit, offset)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        where = ["user_id = ?", "source_type = ?", "source_id = ?"]
        params: List[Any] = [user_id, etype, eid]
        if relation_type is not None:
            where.append("relation_type = ?")
            params.append(_validate_relation_type(relation_type))

        cursor.execute(
            f"""
            SELECT * FROM knowledge_links
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id ASC
            LIMIT ? OFFSET ?;
            """,
            (*params, lim, off),
        )
        return [
            _hydrate_link_row(cursor, r, user_id, perspective_type=etype, perspective_id=eid)
            for r in cursor.fetchall()
        ]


def list_incoming_links(
    entity_type: str,
    entity_id: str,
    user_id: int,
    *,
    relation_type: Optional[str] = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Lista conexões de entrada / backlinks (`target_type = entity_type AND target_id = entity_id`)
    pertencentes exclusivamente à rede privada do usuário autenticado (`user_id`).
    """
    etype = _validate_entity_type(entity_type)
    eid = normalize_unicode_nfc(entity_id).strip()
    lim, off = _validate_pagination(limit, offset)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        where = ["user_id = ?", "target_type = ?", "target_id = ?"]
        params: List[Any] = [user_id, etype, eid]
        if relation_type is not None:
            where.append("relation_type = ?")
            params.append(_validate_relation_type(relation_type))

        cursor.execute(
            f"""
            SELECT * FROM knowledge_links
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id ASC
            LIMIT ? OFFSET ?;
            """,
            (*params, lim, off),
        )
        return [
            _hydrate_link_row(cursor, r, user_id, perspective_type=etype, perspective_id=eid)
            for r in cursor.fetchall()
        ]


def get_entity_connections_and_backlinks(
    entity_type: str,
    entity_id: str,
    user_id: int,
    *,
    direction: str = "both",
    relation_type: Optional[str] = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Retorna as conexões de saída (`outgoing_links`) e os backlinks recebidos (`incoming_backlinks`)
    de uma entidade autorizada, sem duplicar arestas persistidas.
    Valida primeiramente a autorização sobre a entidade consultada (retornando 404 anti-enumeração
    se a entidade não existir ou não estiver acessível ao usuário).
    """
    etype = _validate_entity_type(entity_type)
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise KnowledgeValidationError("Identificador da entidade não pode ser vazio.")
    eid = normalize_unicode_nfc(entity_id).strip()
    if direction not in VALID_DIRECTIONS:
        raise KnowledgeValidationError(
            f"Direção inválida: '{direction}'. Valores permitidos: {', '.join(VALID_DIRECTIONS)}."
        )
    rel_filter = _validate_relation_type(relation_type) if relation_type is not None else None
    lim, off = _validate_pagination(limit, offset)

    with get_db() as conn:
        cursor = conn.cursor()
        entity = resolve_knowledge_entity(etype, eid, user_id, cursor=cursor, for_link_endpoint=False)

        out_where = ["user_id = ?", "source_type = ?", "source_id = ?"]
        out_params: List[Any] = [user_id, etype, eid]
        in_where = ["user_id = ?", "target_type = ?", "target_id = ?"]
        in_params: List[Any] = [user_id, etype, eid]

        if rel_filter is not None:
            out_where.append("relation_type = ?")
            out_params.append(rel_filter)
            in_where.append("relation_type = ?")
            in_params.append(rel_filter)

        total_outgoing = 0
        outgoing: List[Dict[str, Any]] = []
        if direction in ("outgoing", "both"):
            cursor.execute(
                f"SELECT COUNT(*) FROM knowledge_links WHERE {' AND '.join(out_where)};",
                tuple(out_params),
            )
            total_outgoing = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT * FROM knowledge_links
                WHERE {' AND '.join(out_where)}
                ORDER BY created_at DESC, id ASC
                LIMIT ? OFFSET ?;
                """,
                (*out_params, lim, off),
            )
            outgoing = [
                _hydrate_link_row(cursor, r, user_id, perspective_type=etype, perspective_id=eid)
                for r in cursor.fetchall()
            ]

        total_incoming = 0
        incoming: List[Dict[str, Any]] = []
        if direction in ("incoming", "both"):
            cursor.execute(
                f"SELECT COUNT(*) FROM knowledge_links WHERE {' AND '.join(in_where)};",
                tuple(in_params),
            )
            total_incoming = int(cursor.fetchone()[0])
            cursor.execute(
                f"""
                SELECT * FROM knowledge_links
                WHERE {' AND '.join(in_where)}
                ORDER BY created_at DESC, id ASC
                LIMIT ? OFFSET ?;
                """,
                (*in_params, lim, off),
            )
            incoming = [
                _hydrate_link_row(cursor, r, user_id, perspective_type=etype, perspective_id=eid)
                for r in cursor.fetchall()
            ]

    return {
        "entity": entity,
        "direction": direction,
        "outgoing_links": outgoing,
        "incoming_backlinks": incoming,
        "total_outgoing": total_outgoing,
        "total_incoming": total_incoming,
        "limit": lim,
        "offset": off,
        "has_more_outgoing": (off + len(outgoing)) < total_outgoing,
        "has_more_incoming": (off + len(incoming)) < total_incoming,
    }


def list_knowledge_links(
    user_id: int,
    *,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    direction: str = "both",
    relation_type: Optional[str] = None,
    sort_by: str = "created_desc",
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Lista conexões pessoais do usuário autenticado com paginação, ordenação determinística,
    `has_more` e filtros opcionais por origem, destino, entidade, direção e tipo de relação.
    Quando filtrada por uma `entity_type` e `entity_id` específica, valida primeiramente
    a autorização do usuário sobre aquela entidade.
    """
    lim, off = _validate_pagination(limit, offset)
    if sort_by not in VALID_LINK_SORTS:
        raise KnowledgeValidationError(
            f"Modo de ordenação inválido para conexões: '{sort_by}'. Valores permitidos: {', '.join(VALID_LINK_SORTS)}."
        )
    if direction not in VALID_DIRECTIONS:
        raise KnowledgeValidationError(
            f"Direção inválida: '{direction}'. Valores permitidos: {', '.join(VALID_DIRECTIONS)}."
        )

    where_clauses = ["user_id = ?"]
    params: List[Any] = [user_id]

    p_type: Optional[str] = None
    p_id: Optional[str] = None

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        if entity_type is not None or entity_id is not None:
            has_id = bool(entity_id and str(entity_id).strip())
            if has_id and not entity_type:
                raise KnowledgeValidationError(
                    "Os parâmetros 'entity_type' e 'entity_id' devem ser informados em conjunto."
                )
            p_type = _validate_entity_type(entity_type)
            if has_id:
                p_id = normalize_unicode_nfc(str(entity_id)).strip()
                # Valida autorização sobre a entidade focal antes de listar suas conexões
                resolve_knowledge_entity(p_type, p_id, user_id, cursor=cursor, for_link_endpoint=False)

                if direction == "outgoing":
                    where_clauses.append("source_type = ? AND source_id = ?")
                    params.extend([p_type, p_id])
                elif direction == "incoming":
                    where_clauses.append("target_type = ? AND target_id = ?")
                    params.extend([p_type, p_id])
                else:
                    where_clauses.append("((source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?))")
                    params.extend([p_type, p_id, p_type, p_id])
            else:
                # Filtro apenas por tipo de entidade (ex.: explorador textual de conexões do Atlas)
                if direction == "outgoing":
                    where_clauses.append("source_type = ?")
                    params.append(p_type)
                elif direction == "incoming":
                    where_clauses.append("target_type = ?")
                    params.append(p_type)
                else:
                    where_clauses.append("(source_type = ? OR target_type = ?)")
                    params.extend([p_type, p_type])
                p_type = None

        if source_type is not None:
            where_clauses.append("source_type = ?")
            params.append(_validate_entity_type(source_type))
        if source_id is not None and str(source_id).strip():
            where_clauses.append("source_id = ?")
            params.append(normalize_unicode_nfc(str(source_id)).strip())
        if target_type is not None:
            where_clauses.append("target_type = ?")
            params.append(_validate_entity_type(target_type))
        if target_id is not None and str(target_id).strip():
            where_clauses.append("target_id = ?")
            params.append(normalize_unicode_nfc(str(target_id)).strip())
        if relation_type is not None:
            where_clauses.append("relation_type = ?")
            params.append(_validate_relation_type(relation_type))

        where_sql = " AND ".join(where_clauses)
        order_sql = (
            "updated_at DESC, id ASC"
            if sort_by == "updated_desc"
            else "created_at DESC, id ASC"
        )

        cursor.execute(f"SELECT COUNT(*) FROM knowledge_links WHERE {where_sql};", tuple(params))
        total = int(cursor.fetchone()[0])

        cursor.execute(
            f"""
            SELECT * FROM knowledge_links
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?;
            """,
            (*params, lim, off),
        )
        items = [
            _hydrate_link_row(cursor, r, user_id, perspective_type=p_type, perspective_id=p_id)
            for r in cursor.fetchall()
        ]

        return {
            "items": items,
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(items)) < total,
        }


# =========================================================================
# ÍNDICE TEXTUAL SQLite FTS5 (`knowledge_fts` — KD-008 e Seções 12 e 13)
# =========================================================================

def sync_entity_fts(
    entity_type: str,
    entity_id: str,
    *,
    cursor: Optional[sqlite3.Cursor] = None,
) -> None:
    """
    Sincroniza explicitamente uma entidade específica com a tabela virtual `knowledge_fts`.
    Útil para ressincronização sob demanda além dos triggers SQLite automáticos.
    """
    etype = _validate_entity_type(entity_type) if entity_type != "link" else "link"
    eid = normalize_unicode_nfc(entity_id).strip()

    if cursor is None:
        with get_db() as conn:
            _sync_entity_fts_cursor(conn.cursor(), etype, eid)
        return
    _sync_entity_fts_cursor(cursor, etype, eid)


def _sync_entity_fts_cursor(cursor: sqlite3.Cursor, etype: str, eid: str) -> None:
    cursor.execute(
        "DELETE FROM knowledge_fts WHERE entity_type = ? AND entity_id = ?;",
        (etype, eid),
    )
    if etype == "concept":
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'concept', id, CAST(user_id AS TEXT), '', name, description, tags_json
            FROM concepts WHERE id = ?;
            """,
            (eid,),
        )
    elif etype == "reflection":
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'reflection', id, CAST(user_id AS TEXT), COALESCE(primary_book_id, ''), title, body_markdown, tags_json
            FROM reflections WHERE id = ?;
            """,
            (eid,),
        )
    elif etype == "highlight":
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'highlight', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''),
                   COALESCE(selected_text, '') || ' ' || COALESCE(text_context, ''), ''
            FROM highlights WHERE id = ?;
            """,
            (eid,),
        )
    elif etype == "note":
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'note', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''),
                   COALESCE(content, ''), ''
            FROM notes WHERE id = ?;
            """,
            (eid,),
        )
    elif etype == "link":
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'link', id, CAST(user_id AS TEXT), '', relation_type, rationale, ''
            FROM knowledge_links
            WHERE id = ? AND length(trim(COALESCE(rationale, ''))) > 0;
            """,
            (eid,),
        )


def delete_entity_fts(
    entity_type: str,
    entity_id: str,
    *,
    cursor: Optional[sqlite3.Cursor] = None,
) -> None:
    """Remove explicitamente uma entrada de `knowledge_fts`."""
    eid = normalize_unicode_nfc(entity_id).strip()
    if cursor is None:
        with get_db() as conn:
            conn.cursor().execute(
                "DELETE FROM knowledge_fts WHERE entity_type = ? AND entity_id = ?;",
                (entity_type, eid),
            )
        return
    cursor.execute(
        "DELETE FROM knowledge_fts WHERE entity_type = ? AND entity_id = ?;",
        (entity_type, eid),
    )


def rebuild_knowledge_fts() -> Dict[str, int]:
    """
    Reconstrói integralmente o índice `knowledge_fts` a partir das tabelas relacionais canônicas
    (`concepts`, `reflections`, `highlights`, `notes` e `knowledge_links`).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM knowledge_fts;")
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'concept', id, CAST(user_id AS TEXT), '', name, description, tags_json
            FROM concepts;
            """
        )
        concepts_cnt = cursor.rowcount
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'reflection', id, CAST(user_id AS TEXT), COALESCE(primary_book_id, ''), title, body_markdown, tags_json
            FROM reflections;
            """
        )
        reflections_cnt = cursor.rowcount
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'highlight', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''),
                   COALESCE(selected_text, '') || ' ' || COALESCE(text_context, ''), ''
            FROM highlights;
            """
        )
        highlights_cnt = cursor.rowcount
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'note', id, CAST(user_id AS TEXT), book_id, COALESCE(chapter_label, ''),
                   COALESCE(content, ''), ''
            FROM notes;
            """
        )
        notes_cnt = cursor.rowcount
        cursor.execute(
            """
            INSERT INTO knowledge_fts (entity_type, entity_id, user_id, book_id, title, body, tags)
            SELECT 'link', id, CAST(user_id AS TEXT), '', relation_type, rationale, ''
            FROM knowledge_links
            WHERE length(trim(COALESCE(rationale, ''))) > 0;
            """
        )
        links_cnt = cursor.rowcount
        cursor.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('optimize');")

        return {
            "concepts": max(0, concepts_cnt),
            "reflections": max(0, reflections_cnt),
            "highlights": max(0, highlights_cnt),
            "notes": max(0, notes_cnt),
            "links": max(0, links_cnt),
        }


def _build_safe_fts_query(raw_query: str) -> str:
    """
    Constrói uma expressão FTS5 segura a partir da consulta do usuário,
    extraindo tokens alfanuméricos Unicode e aplicando correspondência por prefixo (`"token"*`).
    Neutraliza operadores especiais FTS5, aspas desbalanceadas e tentativas de injeção.
    """
    cleaned = normalize_unicode_nfc(raw_query or "").strip()
    if len(cleaned) > MAX_SEARCH_QUERY_LEN:
        raise KnowledgeValidationError(
            f"O termo de pesquisa excede o limite de {MAX_SEARCH_QUERY_LEN} caracteres."
        )
    tokens = _FTS_TOKEN_RE.findall(cleaned)
    if not tokens:
        return ""
    safe_parts = [f'"{tok}"*' for tok in tokens[:16]]
    return " AND ".join(safe_parts)


MAX_SEARCH_TOKENS = 16
MAX_SNIPPET_LEN = 220
DEFAULT_SEARCH_LIMIT = 24
MAX_SEARCH_LIMIT = 100
VALID_SEARCH_ENTITY_TYPES: Tuple[str, ...] = (
    "concept",
    "reflection",
    "highlight",
    "note",
    "link",
    "book",
)

DEFAULT_GRAPH_DEPTH = 1
MAX_GRAPH_DEPTH = 2
DEFAULT_GRAPH_MAX_NODES = 40
HARD_MAX_GRAPH_NODES = 80
DEFAULT_GRAPH_MAX_EDGES = 120
HARD_MAX_GRAPH_EDGES = 160


def _extract_folded_tokens(raw_query: str) -> List[str]:
    """
    Extrai os tokens alfanuméricos da consulta após normalização NFC,
    casefolding e remoção de diacríticos (`strip_diacritics_casefold`),
    limitando a `MAX_SEARCH_TOKENS`.
    """
    cleaned = normalize_unicode_nfc(raw_query or "").strip()
    if len(cleaned) > MAX_SEARCH_QUERY_LEN:
        raise KnowledgeValidationError(
            f"O termo de pesquisa excede o limite de {MAX_SEARCH_QUERY_LEN} caracteres."
        )
    raw_tokens = _FTS_TOKEN_RE.findall(cleaned)[:MAX_SEARCH_TOKENS]
    folded: List[str] = []
    for tok in raw_tokens:
        ft = strip_diacritics_casefold(tok)
        if ft and ft not in folded:
            folded.append(ft)
    return folded


def _text_matches_all_folded_tokens(text: str, folded_tokens: Sequence[str]) -> bool:
    """
    Verifica correspondência por prefixo de palavra (compatível com FTS5 `"token"*`)
    no texto relacional ao vivo para cada token da consulta.
    """
    if not folded_tokens:
        return False
    if not text:
        return False
    folded_words = _FTS_TOKEN_RE.findall(strip_diacritics_casefold(text))
    if not folded_words:
        return False
    for ftok in folded_tokens:
        if not any(w.startswith(ftok) for w in folded_words):
            return False
    return True


def _build_safe_snippet_segments(
    raw_text: str,
    folded_tokens: Sequence[str],
    max_len: int = MAX_SNIPPET_LEN,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Gera um excerto contextual seguro (`snippet`, limitado a `max_len` caracteres)
    e uma lista estruturada de segmentos (`[{"text": str, "highlight": bool}]`)
    exclusivamente a partir do texto relacional autorizado, sem produzir HTML bruto.
    """
    collapsed = _WHITESPACE_RE.sub(" ", normalize_unicode_nfc(raw_text or "").strip())
    if not collapsed:
        return "", []

    # Localiza o primeiro token correspondente para centralizar a janela do snippet
    first_match_start = 0
    for match in _FTS_TOKEN_RE.finditer(collapsed):
        w_folded = strip_diacritics_casefold(match.group(0))
        if any(w_folded.startswith(ft) for ft in folded_tokens):
            first_match_start = match.start()
            break

    prefix_ellipsis = False
    suffix_ellipsis = False

    if len(collapsed) <= max_len:
        window = collapsed
    else:
        half = max(30, (max_len - 20) // 2)
        start_idx = max(0, first_match_start - half)
        if start_idx > 0:
            space_pos = collapsed.find(" ", start_idx)
            if 0 <= space_pos < first_match_start:
                start_idx = space_pos + 1
            prefix_ellipsis = True
        budget = max_len - (1 if prefix_ellipsis else 0) - 1
        end_idx = min(len(collapsed), start_idx + budget)
        if end_idx < len(collapsed):
            last_space = collapsed.rfind(" ", start_idx + 20, end_idx)
            if last_space > first_match_start:
                end_idx = last_space
            suffix_ellipsis = True
        window = collapsed[start_idx:end_idx].strip()

    segments: List[Dict[str, Any]] = []
    if prefix_ellipsis:
        segments.append({"text": "…", "highlight": False})

    cursor_pos = 0
    for match in _FTS_TOKEN_RE.finditer(window):
        m_start, m_end = match.span()
        word = match.group(0)
        w_folded = strip_diacritics_casefold(word)
        is_hit = bool(folded_tokens) and any(w_folded.startswith(ft) for ft in folded_tokens)
        if is_hit:
            if m_start > cursor_pos:
                segments.append({"text": window[cursor_pos:m_start], "highlight": False})
            segments.append({"text": word, "highlight": True})
            cursor_pos = m_end

    if cursor_pos < len(window):
        segments.append({"text": window[cursor_pos:], "highlight": False})

    if suffix_ellipsis:
        segments.append({"text": "…", "highlight": False})

    # Mescla segmentos adjacentes com mesmo estado de highlight
    merged: List[Dict[str, Any]] = []
    for seg in segments:
        if not seg["text"]:
            continue
        if merged and merged[-1]["highlight"] == seg["highlight"]:
            merged[-1]["text"] += seg["text"]
        else:
            merged.append({"text": seg["text"], "highlight": bool(seg["highlight"])})

    plain_snippet = "".join(s["text"] for s in merged)
    return plain_snippet, merged


def _compute_authorized_relevance_score(
    *,
    primary_title: str,
    secondary_text: str,
    tags_list: Sequence[str],
    folded_tokens: Sequence[str],
    is_own: bool,
) -> float:
    """
    Calcula uma pontuação de relevância determinística baseada exclusivamente
    nos campos relacionais do próprio item autorizado e nos tokens da consulta.
    Garante isolamento absoluto contra vazamento estatístico entre usuários
    (as estatísticas globais IDF de `bm25(knowledge_fts)` nunca influenciam o ranking).
    """
    if not folded_tokens:
        return 0.0

    title_folded = strip_diacritics_casefold(primary_title or "")
    title_words = _FTS_TOKEN_RE.findall(title_folded)
    body_folded = strip_diacritics_casefold(secondary_text or "")
    body_words = _FTS_TOKEN_RE.findall(body_folded)
    tags_folded = [strip_diacritics_casefold(t) for t in (tags_list or [])]

    score = 0.0
    full_query_folded = " ".join(folded_tokens)
    if full_query_folded and title_folded == full_query_folded:
        score += 120.0
    elif full_query_folded and title_folded.startswith(full_query_folded):
        score += 75.0
    elif full_query_folded and full_query_folded in title_folded:
        score += 45.0

    for tok in folded_tokens:
        exact_title_hits = sum(1 for w in title_words if w == tok)
        prefix_title_hits = sum(1 for w in title_words if w != tok and w.startswith(tok))
        score += exact_title_hits * 35.0 + prefix_title_hits * 22.0

        exact_tag_hits = sum(1 for t in tags_folded if t == tok)
        prefix_tag_hits = sum(1 for t in tags_folded if t != tok and tok in t)
        score += exact_tag_hits * 25.0 + prefix_tag_hits * 14.0

        exact_body_hits = min(6, sum(1 for w in body_words if w == tok))
        prefix_body_hits = min(6, sum(1 for w in body_words if w != tok and w.startswith(tok)))
        score += exact_body_hits * 8.0 + prefix_body_hits * 4.0

    if is_own:
        score += 1.5

    return round(score, 4)


def search_knowledge_candidates(
    user_id: int,
    query: str,
    *,
    entity_types: Optional[Sequence[str]] = None,
    book_id: Optional[str] = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Pesquisa candidatos no índice `knowledge_fts` integrando a verificação relacional de autorização
    diretamente na consulta SQL antes da paginação e revalidando o conteúdo ao vivo nas tabelas
    relacionais originais (Seções 12, 13.2 e 15–17).

    Garante que:
    - Mesmo milhões de registros inacessíveis de terceiros no `knowledge_fts` nunca esgotem os slots
      de candidatos antes da paginação;
    - Entradas obsoletas no `knowledge_fts` cujo texto relacional já foi alterado para outro termo
      sejam descartadas pela validação ao vivo dos tokens;
    - A ordenação seja determinística e independente das estatísticas globais IDF de outros usuários.
    """
    lim, off = _validate_pagination(limit, offset)
    fts_expr = _build_safe_fts_query(query)
    folded_tokens = _extract_folded_tokens(query)
    if not fts_expr or not folded_tokens:
        return {
            "items": [],
            "total": 0,
            "limit": lim,
            "offset": off,
            "has_more": False,
        }

    allowed_types = {"concept", "reflection", "highlight", "note", "link"}
    if entity_types is not None:
        filter_types = {str(t).strip() for t in entity_types if str(t).strip()}
        invalid = filter_types - allowed_types
        if invalid:
            raise KnowledgeValidationError(
                f"Tipos de entidade inválidos para busca: {', '.join(sorted(invalid))}."
            )
    else:
        filter_types = allowed_types

    filter_book_id: Optional[str] = None
    if book_id is not None and str(book_id).strip():
        filter_book_id = normalize_unicode_nfc(str(book_id)).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        sorted_types = sorted(filter_types)
        type_ph = ",".join("?" for _ in sorted_types)

        cursor.execute(
            f"""
            SELECT kf.entity_type, kf.entity_id
            FROM knowledge_fts kf
            WHERE knowledge_fts MATCH ?
              AND kf.entity_type IN ({type_ph})
              AND (
                  (kf.entity_type = 'concept' AND EXISTS (
                      SELECT 1 FROM concepts c
                      WHERE c.id = kf.entity_id AND c.user_id = ?
                  ))
                  OR (kf.entity_type = 'reflection' AND EXISTS (
                      SELECT 1 FROM reflections r
                      WHERE r.id = kf.entity_id AND r.user_id = ?
                  ))
                  OR (kf.entity_type = 'link' AND EXISTS (
                      SELECT 1 FROM knowledge_links kl
                      WHERE kl.id = kf.entity_id AND kl.user_id = ?
                  ))
                  OR (kf.entity_type = 'highlight' AND EXISTS (
                      SELECT 1 FROM highlights h
                      JOIN books b ON b.id = h.book_id
                      LEFT JOIN library_sources s ON s.id = b.source_id
                      WHERE h.id = kf.entity_id
                        AND b.is_missing = 0
                        AND COALESCE(s.is_active, 1) = 1
                        AND (h.user_id = ? OR h.visibility = 'public')
                  ))
                  OR (kf.entity_type = 'note' AND EXISTS (
                      SELECT 1 FROM notes n
                      JOIN books b ON b.id = n.book_id
                      LEFT JOIN library_sources s ON s.id = b.source_id
                      WHERE n.id = kf.entity_id
                        AND b.is_missing = 0
                        AND COALESCE(s.is_active, 1) = 1
                        AND (n.user_id = ? OR n.visibility = 'public')
                  ))
              )
            ORDER BY kf.entity_type ASC, kf.entity_id ASC
            LIMIT 2000;
            """,
            (
                fts_expr,
                *sorted_types,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
            ),
        )
        raw_candidates = cursor.fetchall()

        scored_items: List[Tuple[float, str, str, Dict[str, Any]]] = []
        seen_keys: set[Tuple[str, str]] = set()

        for cand in raw_candidates:
            ctype = cand["entity_type"]
            cid = cand["entity_id"]
            key = (ctype, cid)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            if ctype == "link":
                cursor.execute(
                    "SELECT * FROM knowledge_links WHERE id = ? AND user_id = ?;",
                    (cid, user_id),
                )
                lrow = cursor.fetchone()
                if not lrow:
                    continue
                live_haystack = f"{lrow['relation_type'] or ''} {lrow['rationale'] or ''}"
                if not _text_matches_all_folded_tokens(live_haystack, folded_tokens):
                    continue
                hydrated_link = _hydrate_link_row(cursor, lrow, user_id)
                if filter_book_id:
                    s_book = hydrated_link["source"].get("book_id")
                    t_book = hydrated_link["target"].get("book_id")
                    if filter_book_id not in (s_book, t_book):
                        continue
                rel_score = _compute_authorized_relevance_score(
                    primary_title=hydrated_link["relation_label"],
                    secondary_text=hydrated_link["rationale"] or "",
                    tags_list=[],
                    folded_tokens=folded_tokens,
                    is_own=True,
                )
                item_dict = {
                    "type": "link",
                    "id": hydrated_link["id"],
                    "label": hydrated_link["relation_label"],
                    "title": hydrated_link["relation_label"],
                    "excerpt": hydrated_link["rationale"],
                    "relation_type": hydrated_link["relation_type"],
                    "source": hydrated_link["source"],
                    "target": hydrated_link["target"],
                    "created_at": hydrated_link["created_at"],
                    "updated_at": hydrated_link["updated_at"],
                }
                scored_items.append((-rel_score, ctype, cid, item_dict))
            else:
                try:
                    resolved = resolve_knowledge_entity(
                        ctype,
                        cid,
                        user_id,
                        cursor=cursor,
                        for_link_endpoint=False,
                    )
                except KnowledgeError:
                    continue

                if filter_book_id and resolved.get("book_id") != filter_book_id:
                    continue

                # Valida correspondência ao vivo contra dados relacionais atuais (proteção contra índice FTS obsoleto)
                if ctype == "concept":
                    cursor.execute("SELECT name, description, tags_json FROM concepts WHERE id = ? AND user_id = ?;", (cid, user_id))
                    crow = cursor.fetchone()
                    if not crow:
                        continue
                    c_tags = _parse_tags_json(crow["tags_json"])
                    live_text = f"{crow['name']} {crow['description'] or ''} {' '.join(c_tags)}"
                    if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                        continue
                    rel_score = _compute_authorized_relevance_score(
                        primary_title=crow["name"],
                        secondary_text=crow["description"] or "",
                        tags_list=c_tags,
                        folded_tokens=folded_tokens,
                        is_own=True,
                    )
                elif ctype == "reflection":
                    cursor.execute("SELECT title, body_markdown, tags_json FROM reflections WHERE id = ? AND user_id = ?;", (cid, user_id))
                    rrow = cursor.fetchone()
                    if not rrow:
                        continue
                    r_tags = _parse_tags_json(rrow["tags_json"])
                    live_text = f"{rrow['title']} {rrow['body_markdown'] or ''} {' '.join(r_tags)}"
                    if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                        continue
                    rel_score = _compute_authorized_relevance_score(
                        primary_title=rrow["title"],
                        secondary_text=rrow["body_markdown"] or "",
                        tags_list=r_tags,
                        folded_tokens=folded_tokens,
                        is_own=True,
                    )
                elif ctype == "highlight":
                    cursor.execute("SELECT selected_text, text_context, chapter_label FROM highlights WHERE id = ?;", (cid,))
                    hrow = cursor.fetchone()
                    if not hrow:
                        continue
                    body_txt = f"{hrow['selected_text'] or ''} {hrow['text_context'] or ''}".strip()
                    live_text = f"{hrow['chapter_label'] or ''} {body_txt}"
                    if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                        continue
                    rel_score = _compute_authorized_relevance_score(
                        primary_title=hrow["chapter_label"] or resolved.get("title") or "",
                        secondary_text=body_txt,
                        tags_list=[],
                        folded_tokens=folded_tokens,
                        is_own=bool(resolved.get("is_own")),
                    )
                elif ctype == "note":
                    cursor.execute("SELECT content, chapter_label FROM notes WHERE id = ?;", (cid,))
                    nrow = cursor.fetchone()
                    if not nrow:
                        continue
                    body_txt = (nrow["content"] or "").strip()
                    live_text = f"{nrow['chapter_label'] or ''} {body_txt}"
                    if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                        continue
                    rel_score = _compute_authorized_relevance_score(
                        primary_title=nrow["chapter_label"] or resolved.get("title") or "",
                        secondary_text=body_txt,
                        tags_list=[],
                        folded_tokens=folded_tokens,
                        is_own=bool(resolved.get("is_own")),
                    )
                else:
                    rel_score = 1.0

                scored_items.append((-rel_score, ctype, cid, resolved))

        scored_items.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
        authorized_items = [entry[3] for entry in scored_items]

        total = len(authorized_items)
        page_items = authorized_items[off : off + lim]
        return {
            "items": page_items,
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(page_items)) < total,
        }


def lookup_referencable_entities(
    user_id: int,
    *,
    query: str = "",
    entity_type: Optional[str] = None,
    book_id: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Localização assistida de entidades referenciáveis (`concept`, `reflection`, `highlight`, `note`, `book`)
    para criação de conexões e inserção assistida de referências (Seção 10).

    Requisitos atendidos:
    - Autorização obrigatória em tempo de leitura para todos os itens retornados.
    - Normalização Unicode NFC e busca insensível a acentos/caixa via NFD (`strip_diacritics_casefold`).
    - Controle de consultas vazias (retorna itens recentes autorizados) e bloqueio de consultas muito longas
      (`len(q) <= MAX_SEARCH_QUERY_LEN`).
    - Limite máximo estrito (`1 <= limit <= AUX_SEARCH_LIMIT = 50`).
    - Resultados determinísticos contendo apenas metadados estritamente necessários para seleção assistida,
      sem vazamentos por snippets ou contagens não autorizadas.
    """
    try:
        lim = int(20 if limit is None else limit)
    except (TypeError, ValueError):
        raise KnowledgeValidationError("O parâmetro 'limit' deve ser um inteiro.")
    if lim < 1 or lim > AUX_SEARCH_LIMIT:
        raise KnowledgeValidationError(
            f"O parâmetro 'limit' na localização assistida deve estar entre 1 e {AUX_SEARCH_LIMIT}."
        )

    cleaned_q = normalize_unicode_nfc(query or "").strip()
    if len(cleaned_q) > MAX_SEARCH_QUERY_LEN:
        raise KnowledgeValidationError(
            f"O termo de pesquisa excede o limite de {MAX_SEARCH_QUERY_LEN} caracteres."
        )
    q_folded = strip_diacritics_casefold(cleaned_q)

    target_types: Tuple[str, ...]
    if entity_type is not None and str(entity_type).strip():
        target_types = (_validate_entity_type(entity_type),)
    else:
        target_types = VALID_ENTITY_TYPES

    clean_book_filter: Optional[str] = None
    if book_id is not None and str(book_id).strip():
        clean_book_filter = normalize_unicode_nfc(str(book_id)).strip()

    results: List[Dict[str, Any]] = []

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        # Se um book_id específico foi filtrado, valida que o livro existe e está acessível
        if clean_book_filter is not None:
            if not _check_book_accessible_row(cursor, clean_book_filter, user_id=user_id):
                raise KnowledgeNotFoundError("Livro filtrado não encontrado ou indisponível.")

        # 1. Conceitos próprios (quando não filtrado por book_id ou quando o conceito tem vínculo com o livro)
        if "concept" in target_types:
            if clean_book_filter:
                cursor.execute(
                    """
                    SELECT DISTINCT c.id, c.name, c.description, c.color, c.tags_json, c.updated_at
                    FROM concepts c
                    JOIN knowledge_links kl ON kl.user_id = c.user_id
                      AND ((kl.source_type = 'concept' AND kl.source_id = c.id)
                           OR (kl.target_type = 'concept' AND kl.target_id = c.id))
                    LEFT JOIN highlights h ON (
                        (kl.source_type = 'highlight' AND kl.source_id = h.id)
                        OR (kl.target_type = 'highlight' AND kl.target_id = h.id)
                    )
                    LEFT JOIN notes n ON (
                        (kl.source_type = 'note' AND kl.source_id = n.id)
                        OR (kl.target_type = 'note' AND kl.target_id = n.id)
                    )
                    WHERE c.user_id = ? AND c.is_archived = 0
                      AND (
                          (kl.source_type = 'book' AND kl.source_id = ?)
                          OR (kl.target_type = 'book' AND kl.target_id = ?)
                          OR h.book_id = ?
                          OR n.book_id = ?
                      )
                    ORDER BY c.normalized_name ASC, c.id ASC;
                    """,
                    (user_id, clean_book_filter, clean_book_filter, clean_book_filter, clean_book_filter),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, name, description, color, tags_json, updated_at
                    FROM concepts
                    WHERE user_id = ? AND is_archived = 0
                    ORDER BY normalized_name ASC, id ASC;
                    """,
                    (user_id,),
                )
            for row in cursor.fetchall():
                tags_list = _parse_tags_json(row["tags_json"])
                haystack = f"{row['name']} {row['description'] or ''} {' '.join(tags_list)}"
                if not q_folded or q_folded in strip_diacritics_casefold(haystack):
                    results.append(
                        {
                            "type": "concept",
                            "id": row["id"],
                            "label": row["name"],
                            "title": row["name"],
                            "subtitle": ", ".join(f"#{t}" for t in tags_list[:3]) if tags_list else "Conceito pessoal",
                            "excerpt": (row["description"] or "")[:160],
                            "color": row["color"],
                            "book_id": None,
                            "book_title": None,
                            "page": None,
                            "epub_cfi": None,
                            "chapter_label": None,
                            "is_own": True,
                            "visibility": "private",
                        }
                    )
                    if len(results) >= lim and len(target_types) == 1:
                        break

        # 2. Reflexões próprias
        if "reflection" in target_types and len(results) < lim:
            if clean_book_filter:
                cursor.execute(
                    """
                    SELECT id, title, body_markdown, primary_book_id, tags_json, updated_at
                    FROM reflections
                    WHERE user_id = ? AND is_archived = 0 AND primary_book_id = ?
                    ORDER BY is_pinned DESC, updated_at DESC, id ASC;
                    """,
                    (user_id, clean_book_filter),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, title, body_markdown, primary_book_id, tags_json, updated_at
                    FROM reflections
                    WHERE user_id = ? AND is_archived = 0
                    ORDER BY is_pinned DESC, updated_at DESC, id ASC;
                    """,
                    (user_id,),
                )
            for row in cursor.fetchall():
                tags_list = _parse_tags_json(row["tags_json"])
                haystack = f"{row['title']} {row['body_markdown'] or ''} {' '.join(tags_list)}"
                if not q_folded or q_folded in strip_diacritics_casefold(haystack):
                    b_row = (
                        _check_book_accessible_row(cursor, row["primary_book_id"], user_id=user_id)
                        if row["primary_book_id"]
                        else None
                    )
                    results.append(
                        {
                            "type": "reflection",
                            "id": row["id"],
                            "label": row["title"],
                            "title": row["title"],
                            "subtitle": b_row["title"] if b_row else "Reflexão autoral",
                            "excerpt": (row["body_markdown"] or "")[:160],
                            "color": None,
                            "book_id": b_row["id"] if b_row else None,
                            "book_title": b_row["title"] if b_row else None,
                            "page": None,
                            "epub_cfi": None,
                            "chapter_label": None,
                            "is_own": True,
                            "visibility": "private",
                        }
                    )
                    if len(results) >= lim:
                        break

        # 3. Livros acessíveis
        if "book" in target_types and len(results) < lim:
            book_where = ["b.is_missing = 0", "COALESCE(s.is_active, 1) = 1"]
            book_params: List[Any] = []
            if clean_book_filter:
                book_where.append("b.id = ?")
                book_params.append(clean_book_filter)
            cursor.execute(
                f"""
                SELECT b.id, b.title, b.author, b.format, b.page_count
                FROM books b
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE {' AND '.join(book_where)}
                ORDER BY b.title COLLATE NOCASE ASC, b.id ASC;
                """,
                tuple(book_params),
            )
            for row in cursor.fetchall():
                haystack = f"{row['title']} {row['author'] or ''}"
                if not q_folded or q_folded in strip_diacritics_casefold(haystack):
                    results.append(
                        {
                            "type": "book",
                            "id": row["id"],
                            "label": row["title"],
                            "title": row["title"],
                            "subtitle": row["author"] or "Autor desconhecido",
                            "excerpt": row["author"] or "",
                            "color": None,
                            "book_id": row["id"],
                            "book_title": row["title"],
                            "page": None,
                            "epub_cfi": None,
                            "chapter_label": None,
                            "is_own": False,
                            "visibility": "public",
                        }
                    )
                    if len(results) >= lim:
                        break

        # 4. Destaques autorizados (próprios ou públicos em livros ativos)
        if "highlight" in target_types and len(results) < lim:
            hl_where = [
                "b.is_missing = 0",
                "COALESCE(s.is_active, 1) = 1",
                "(h.user_id = ? OR h.visibility = 'public')",
            ]
            hl_params: List[Any] = [user_id]
            if clean_book_filter:
                hl_where.append("h.book_id = ?")
                hl_params.append(clean_book_filter)
            cursor.execute(
                f"""
                SELECT h.id, h.book_id, h.page, h.color, h.selected_text, h.text_context,
                       h.user_id, h.visibility, h.epub_cfi, h.chapter_label,
                       b.title AS book_title
                FROM highlights h
                JOIN books b ON b.id = h.book_id
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE {' AND '.join(hl_where)}
                ORDER BY h.created_at DESC, h.id ASC
                LIMIT 300;
                """,
                tuple(hl_params),
            )
            for row in cursor.fetchall():
                text_prev = (row["selected_text"] or row["text_context"] or "").strip()
                haystack = f"{text_prev} {row['chapter_label'] or ''} {row['book_title'] or ''}"
                if not q_folded or q_folded in strip_diacritics_casefold(haystack):
                    results.append(
                        {
                            "type": "highlight",
                            "id": row["id"],
                            "label": text_prev[:100] or f"Destaque (p. {row['page']})",
                            "title": row["book_title"],
                            "subtitle": f"{row['book_title']} · p. {row['page']}",
                            "excerpt": text_prev[:160],
                            "color": row["color"],
                            "book_id": row["book_id"],
                            "book_title": row["book_title"],
                            "page": row["page"],
                            "epub_cfi": row["epub_cfi"],
                            "chapter_label": row["chapter_label"],
                            "is_own": int(row["user_id"]) == int(user_id),
                            "visibility": row["visibility"],
                        }
                    )
                    if len(results) >= lim:
                        break

        # 5. Notas autorizadas (próprias ou públicas em livros ativos)
        if "note" in target_types and len(results) < lim:
            nt_where = [
                "b.is_missing = 0",
                "COALESCE(s.is_active, 1) = 1",
                "(n.user_id = ? OR n.visibility = 'public')",
            ]
            nt_params: List[Any] = [user_id]
            if clean_book_filter:
                nt_where.append("n.book_id = ?")
                nt_params.append(clean_book_filter)
            cursor.execute(
                f"""
                SELECT n.id, n.book_id, n.page, n.color, n.content,
                       n.user_id, n.visibility, n.epub_cfi, n.chapter_label,
                       b.title AS book_title
                FROM notes n
                JOIN books b ON b.id = n.book_id
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE {' AND '.join(nt_where)}
                ORDER BY n.created_at DESC, n.id ASC
                LIMIT 300;
                """,
                tuple(nt_params),
            )
            for row in cursor.fetchall():
                content_prev = (row["content"] or "").strip()
                haystack = f"{content_prev} {row['chapter_label'] or ''} {row['book_title'] or ''}"
                if not q_folded or q_folded in strip_diacritics_casefold(haystack):
                    results.append(
                        {
                            "type": "note",
                            "id": row["id"],
                            "label": content_prev[:100] or f"Nota (p. {row['page']})",
                            "title": row["book_title"],
                            "subtitle": f"{row['book_title']} · p. {row['page']}",
                            "excerpt": content_prev[:160],
                            "color": row["color"],
                            "book_id": row["book_id"],
                            "book_title": row["book_title"],
                            "page": row["page"],
                            "epub_cfi": row["epub_cfi"],
                            "chapter_label": row["chapter_label"],
                            "is_own": int(row["user_id"]) == int(user_id),
                            "visibility": row["visibility"],
                        }
                    )
                    if len(results) >= lim:
                        break

    sliced = results[:lim]
    return {
        "items": sliced,
        "count": len(sliced),
        "limit": lim,
        "query": cleaned_q,
        "entity_type": entity_type.strip() if (entity_type and str(entity_type).strip()) else None,
        "book_id": clean_book_filter,
    }


# =========================================================================
# 8. RESUMOS AGREGADOS SEM N+1 (CADERNO DE ESTUDOS E DETALHES DA OBRA — FASE 4)
# =========================================================================

MAX_SUMMARY_BATCH_SIZE = 200
SUMMARY_ELIGIBLE_ENTITY_TYPES = ("highlight", "note", "book")


def get_book_knowledge_summary(book_id: str, user_id: int) -> Dict[str, Any]:
    """
    Retorna indicadores agregados estritamente pessoais do usuário autenticado
    para um livro acessível, sem dupla contagem e sem expor vínculos de terceiros.

    Semântica das contagens:
    - `distinct_concepts_count`: conceitos distintos do próprio usuário associados ao livro
      ou a qualquer destaque/nota acessível do livro.
    - `personal_links_count`: conexões distintas (`knowledge_links.id`) pertencentes ao usuário
      cujo extremo de origem ou destino seja o livro ou um destaque/nota acessível do livro.
    - `connected_highlights_count`: destaques acessíveis do livro com ao menos 1 conexão pessoal.
    - `connected_notes_count`: notas acessíveis do livro com ao menos 1 conexão pessoal.
    - `connected_annotations_count`: soma de destaques e notas acessíveis conectados.
    - `book_direct_links_count`: conexões pessoais distintas ligadas diretamente à entidade `book`.
    """
    if not isinstance(book_id, str) or not book_id.strip():
        raise KnowledgeValidationError("Identificador do livro inválido.")
    clean_book_id = normalize_unicode_nfc(book_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)
        book_row = _check_book_accessible_row(cursor, clean_book_id)
        if not book_row:
            raise KnowledgeNotFoundError("Livro não encontrado ou indisponível.")

        # 1. Destaques acessíveis do livro com ao menos 1 vínculo pessoal do usuário
        cursor.execute(
            """
            SELECT COUNT(DISTINCT h.id)
            FROM highlights h
            WHERE h.book_id = ?
              AND (h.user_id = ? OR h.visibility = 'public')
              AND EXISTS (
                  SELECT 1 FROM knowledge_links kl
                  WHERE kl.user_id = ?
                    AND (
                        (kl.source_type = 'highlight' AND kl.source_id = h.id)
                        OR (kl.target_type = 'highlight' AND kl.target_id = h.id)
                    )
              );
            """,
            (clean_book_id, user_id, user_id),
        )
        connected_highlights_count = int(cursor.fetchone()[0])

        # 2. Notas acessíveis do livro com ao menos 1 vínculo pessoal do usuário
        cursor.execute(
            """
            SELECT COUNT(DISTINCT n.id)
            FROM notes n
            WHERE n.book_id = ?
              AND (n.user_id = ? OR n.visibility = 'public')
              AND EXISTS (
                  SELECT 1 FROM knowledge_links kl
                  WHERE kl.user_id = ?
                    AND (
                        (kl.source_type = 'note' AND kl.source_id = n.id)
                        OR (kl.target_type = 'note' AND kl.target_id = n.id)
                    )
              );
            """,
            (clean_book_id, user_id, user_id),
        )
        connected_notes_count = int(cursor.fetchone()[0])

        # 3. Conexões pessoais distintas que tocam o livro ou suas anotações acessíveis
        cursor.execute(
            """
            SELECT kl.id, kl.source_type, kl.source_id, kl.target_type, kl.target_id
            FROM knowledge_links kl
            WHERE kl.user_id = ?
              AND (
                  (kl.source_type = 'book' AND kl.source_id = ?)
                  OR (kl.target_type = 'book' AND kl.target_id = ?)
                  OR (kl.source_type = 'highlight' AND EXISTS (
                      SELECT 1 FROM highlights h
                      WHERE h.id = kl.source_id AND h.book_id = ?
                        AND (h.user_id = ? OR h.visibility = 'public')
                  ))
                  OR (kl.target_type = 'highlight' AND EXISTS (
                      SELECT 1 FROM highlights h
                      WHERE h.id = kl.target_id AND h.book_id = ?
                        AND (h.user_id = ? OR h.visibility = 'public')
                  ))
                  OR (kl.source_type = 'note' AND EXISTS (
                      SELECT 1 FROM notes n
                      WHERE n.id = kl.source_id AND n.book_id = ?
                        AND (n.user_id = ? OR n.visibility = 'public')
                  ))
                  OR (kl.target_type = 'note' AND EXISTS (
                      SELECT 1 FROM notes n
                      WHERE n.id = kl.target_id AND n.book_id = ?
                        AND (n.user_id = ? OR n.visibility = 'public')
                  ))
              );
            """,
            (
                user_id,
                clean_book_id,
                clean_book_id,
                clean_book_id,
                user_id,
                clean_book_id,
                user_id,
                clean_book_id,
                user_id,
                clean_book_id,
                user_id,
            ),
        )
        link_rows = cursor.fetchall()

        distinct_link_ids: set[str] = set()
        book_direct_link_ids: set[str] = set()
        candidate_concept_ids: set[str] = set()

        for r in link_rows:
            lid = r["id"]
            distinct_link_ids.add(lid)
            if (r["source_type"] == "book" and r["source_id"] == clean_book_id) or (
                r["target_type"] == "book" and r["target_id"] == clean_book_id
            ):
                book_direct_link_ids.add(lid)
            if r["source_type"] == "concept":
                candidate_concept_ids.add(r["source_id"])
            if r["target_type"] == "concept":
                candidate_concept_ids.add(r["target_id"])

        distinct_concepts_count = 0
        if candidate_concept_ids:
            placeholders = ",".join("?" for _ in candidate_concept_ids)
            cursor.execute(
                f"SELECT COUNT(*) FROM concepts WHERE user_id = ? AND id IN ({placeholders});",
                (user_id, *sorted(candidate_concept_ids)),
            )
            distinct_concepts_count = int(cursor.fetchone()[0])

        return {
            "book_id": clean_book_id,
            "distinct_concepts_count": distinct_concepts_count,
            "personal_links_count": len(distinct_link_ids),
            "connected_highlights_count": connected_highlights_count,
            "connected_notes_count": connected_notes_count,
            "connected_annotations_count": connected_highlights_count + connected_notes_count,
            "book_direct_links_count": len(book_direct_link_ids),
        }


def get_entities_knowledge_summary(
    user_id: int,
    entities: Sequence[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Retorna resumos pessoais em lote (sem consultas N+1) para até 200 entidades
    (`highlight`, `note`, `book`), validando individualmente a autorização de cada
    identificador e omitindo silenciosamente itens não autorizados ou inexistentes.
    """
    if not isinstance(entities, (list, tuple)):
        raise KnowledgeValidationError("Lista de entidades inválida.")
    if len(entities) > MAX_SUMMARY_BATCH_SIZE:
        raise KnowledgeValidationError(
            f"O lote excede o limite máximo de {MAX_SUMMARY_BATCH_SIZE} entidades por requisição."
        )

    requested_by_type: Dict[str, set[str]] = {"highlight": set(), "note": set(), "book": set()}
    for entry in entities:
        if not isinstance(entry, dict):
            raise KnowledgeValidationError("Item de entidade inválido no lote.")
        etype = str(entry.get("entity_type") or "").strip().lower()
        eid = normalize_unicode_nfc(str(entry.get("entity_id") or "")).strip()
        if etype not in SUMMARY_ELIGIBLE_ENTITY_TYPES or not eid or len(eid) > 128:
            raise KnowledgeValidationError(
                "Cada item do lote deve conter entity_type ('highlight', 'note' ou 'book') e entity_id válidos."
            )
        requested_by_type[etype].add(eid)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        authorized_keys: set[tuple[str, str]] = set()

        # 1. Validação em lote de destaques autorizados (próprios ou públicos em livro ativo)
        if requested_by_type["highlight"]:
            hl_ids = sorted(requested_by_type["highlight"])
            ph = ",".join("?" for _ in hl_ids)
            cursor.execute(
                f"""
                SELECT h.id
                FROM highlights h
                JOIN books b ON b.id = h.book_id
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE h.id IN ({ph})
                  AND b.is_missing = 0
                  AND COALESCE(s.is_active, 1) = 1
                  AND (h.user_id = ? OR h.visibility = 'public');
                """,
                (*hl_ids, user_id),
            )
            for row in cursor.fetchall():
                authorized_keys.add(("highlight", row["id"]))

        # 2. Validação em lote de notas autorizadas (próprias ou públicas em livro ativo)
        if requested_by_type["note"]:
            nt_ids = sorted(requested_by_type["note"])
            ph = ",".join("?" for _ in nt_ids)
            cursor.execute(
                f"""
                SELECT n.id
                FROM notes n
                JOIN books b ON b.id = n.book_id
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE n.id IN ({ph})
                  AND b.is_missing = 0
                  AND COALESCE(s.is_active, 1) = 1
                  AND (n.user_id = ? OR n.visibility = 'public');
                """,
                (*nt_ids, user_id),
            )
            for row in cursor.fetchall():
                authorized_keys.add(("note", row["id"]))

        # 3. Validação em lote de livros acessíveis
        if requested_by_type["book"]:
            bk_ids = sorted(requested_by_type["book"])
            ph = ",".join("?" for _ in bk_ids)
            cursor.execute(
                f"""
                SELECT b.id
                FROM books b
                LEFT JOIN library_sources s ON s.id = b.source_id
                WHERE b.id IN ({ph})
                  AND b.is_missing = 0
                  AND COALESCE(s.is_active, 1) = 1;
                """,
                tuple(bk_ids),
            )
            for row in cursor.fetchall():
                authorized_keys.add(("book", row["id"]))

        if not authorized_keys:
            return {"summaries": {}, "count": 0}

        # Inicializa mapa de resumos apenas para entidades autorizadas
        summaries: Dict[str, Dict[str, Any]] = {}
        seen_links_per_key: Dict[str, set[str]] = {}
        concept_ids_per_key: Dict[str, set[str]] = {}
        all_concept_ids: set[str] = set()

        for etype, eid in sorted(authorized_keys):
            k = f"{etype}:{eid}"
            summaries[k] = {
                "entity_type": etype,
                "entity_id": eid,
                "concepts": [],
                "concept_links_count": 0,
                "outgoing_count": 0,
                "incoming_count": 0,
                "total_links_count": 0,
                "is_connected": False,
            }
            seen_links_per_key[k] = set()
            concept_ids_per_key[k] = set()

        # 4. Consulta em lote única dos knowledge_links pessoais que tocam qualquer entidade autorizada
        or_clauses: List[str] = []
        link_params: List[Any] = [user_id]
        for etype in SUMMARY_ELIGIBLE_ENTITY_TYPES:
            ids_for_type = sorted(eid for (et, eid) in authorized_keys if et == etype)
            if not ids_for_type:
                continue
            ph = ",".join("?" for _ in ids_for_type)
            or_clauses.append(f"(kl.source_type = ? AND kl.source_id IN ({ph}))")
            link_params.extend([etype, *ids_for_type])
            or_clauses.append(f"(kl.target_type = ? AND kl.target_id IN ({ph}))")
            link_params.extend([etype, *ids_for_type])

        cursor.execute(
            f"""
            SELECT kl.id, kl.source_type, kl.source_id, kl.target_type, kl.target_id, kl.relation_type
            FROM knowledge_links kl
            WHERE kl.user_id = ?
              AND ({' OR '.join(or_clauses)});
            """,
            tuple(link_params),
        )
        for row in cursor.fetchall():
            lid = row["id"]
            src_key = f"{row['source_type']}:{row['source_id']}"
            tgt_key = f"{row['target_type']}:{row['target_id']}"

            if src_key in summaries:
                summaries[src_key]["outgoing_count"] += 1
                seen_links_per_key[src_key].add(lid)
                if row["target_type"] == "concept":
                    summaries[src_key]["concept_links_count"] += 1
                    concept_ids_per_key[src_key].add(row["target_id"])
                    all_concept_ids.add(row["target_id"])

            if tgt_key in summaries:
                summaries[tgt_key]["incoming_count"] += 1
                seen_links_per_key[tgt_key].add(lid)
                if row["source_type"] == "concept":
                    summaries[tgt_key]["concept_links_count"] += 1
                    concept_ids_per_key[tgt_key].add(row["source_id"])
                    all_concept_ids.add(row["source_id"])

        # 5. Hidratação em lote única dos conceitos próprios associados
        concepts_by_id: Dict[str, Dict[str, Any]] = {}
        if all_concept_ids:
            sorted_cids = sorted(all_concept_ids)
            ph = ",".join("?" for _ in sorted_cids)
            cursor.execute(
                f"""
                SELECT id, name, color
                FROM concepts
                WHERE user_id = ? AND id IN ({ph})
                ORDER BY normalized_name ASC, id ASC;
                """,
                (user_id, *sorted_cids),
            )
            for crow in cursor.fetchall():
                concepts_by_id[crow["id"]] = {
                    "id": crow["id"],
                    "name": crow["name"],
                    "color": crow["color"],
                }

        for k, summary in summaries.items():
            summary["total_links_count"] = len(seen_links_per_key[k])
            summary["is_connected"] = summary["total_links_count"] > 0
            c_list = [
                concepts_by_id[cid]
                for cid in concept_ids_per_key[k]
                if cid in concepts_by_id
            ]
            c_list.sort(key=lambda c: (c["name"].lower(), c["id"]))
            summary["concepts"] = c_list

        return {"summaries": summaries, "count": len(summaries)}


def get_atlas_overview_summary(user_id: int, recent_limit: int = 6) -> Dict[str, Any]:
    """
    Retorna a visão geral agregada e estritamente pessoal do Atlas do usuário autenticado:
    - contagens exatas de conceitos ativos/arquivados, reflexões ativas/fixadas, conexões e obras conectadas;
    - lista enxuta de conceitos recentes (com `links_count`);
    - lista enxuta de reflexões recentes (com `excerpt` limitado a 200 caracteres, sem carregar
      o corpo Markdown integral de toda a coleção).
    """
    lim = max(1, min(int(recent_limit or 6), 20))

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN is_archived = 0 THEN 1 ELSE 0 END), 0) AS active_count,
                COALESCE(SUM(CASE WHEN is_archived = 1 THEN 1 ELSE 0 END), 0) AS archived_count
            FROM concepts
            WHERE user_id = ?;
            """,
            (user_id,),
        )
        c_row = cursor.fetchone()
        concepts_count = int(c_row["active_count"]) if c_row else 0
        archived_concepts_count = int(c_row["archived_count"]) if c_row else 0

        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN is_archived = 0 THEN 1 ELSE 0 END), 0) AS active_count,
                COALESCE(SUM(CASE WHEN is_archived = 0 AND is_pinned = 1 THEN 1 ELSE 0 END), 0) AS pinned_count
            FROM reflections
            WHERE user_id = ?;
            """,
            (user_id,),
        )
        r_row = cursor.fetchone()
        reflections_count = int(r_row["active_count"]) if r_row else 0
        pinned_reflections_count = int(r_row["pinned_count"]) if r_row else 0

        cursor.execute(
            "SELECT COUNT(*) FROM knowledge_links WHERE user_id = ?;",
            (user_id,),
        )
        links_count = int(cursor.fetchone()[0])

        # Obras acessíveis distintas tocadas por conexões ou reflexões do usuário
        # (subconsultas separadas por source/target para explorar índices compostos sem SCAN)
        cursor.execute(
            """
            SELECT COUNT(DISTINCT b.id)
            FROM books b
            LEFT JOIN library_sources s ON s.id = b.source_id
            WHERE b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1
              AND (
                  EXISTS (
                      SELECT 1 FROM knowledge_links kl
                      WHERE kl.user_id = ?
                        AND kl.source_type = 'book' AND kl.source_id = b.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM knowledge_links kl
                      WHERE kl.user_id = ?
                        AND kl.target_type = 'book' AND kl.target_id = b.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM reflections r
                      WHERE r.user_id = ? AND r.is_archived = 0 AND r.primary_book_id = b.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM highlights h
                      JOIN knowledge_links kl
                        ON kl.user_id = ? AND kl.source_type = 'highlight' AND kl.source_id = h.id
                      WHERE h.book_id = b.id AND (h.user_id = ? OR h.visibility = 'public')
                  )
                  OR EXISTS (
                      SELECT 1 FROM highlights h
                      JOIN knowledge_links kl
                        ON kl.user_id = ? AND kl.target_type = 'highlight' AND kl.target_id = h.id
                      WHERE h.book_id = b.id AND (h.user_id = ? OR h.visibility = 'public')
                  )
                  OR EXISTS (
                      SELECT 1 FROM notes n
                      JOIN knowledge_links kl
                        ON kl.user_id = ? AND kl.source_type = 'note' AND kl.source_id = n.id
                      WHERE n.book_id = b.id AND (n.user_id = ? OR n.visibility = 'public')
                  )
                  OR EXISTS (
                      SELECT 1 FROM notes n
                      JOIN knowledge_links kl
                        ON kl.user_id = ? AND kl.target_type = 'note' AND kl.target_id = n.id
                      WHERE n.book_id = b.id AND (n.user_id = ? OR n.visibility = 'public')
                  )
              );
            """,
            (
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
                user_id,
            ),
        )
        connected_books_count = int(cursor.fetchone()[0])

        # Conceitos ativos recentes (contagem indexada de source + target sem OR)
        cursor.execute(
            """
            SELECT c.*,
                   (
                       (SELECT COUNT(*) FROM knowledge_links kl
                        WHERE kl.user_id = c.user_id
                          AND kl.source_type = 'concept' AND kl.source_id = c.id)
                       +
                       (SELECT COUNT(*) FROM knowledge_links kl
                        WHERE kl.user_id = c.user_id
                          AND kl.target_type = 'concept' AND kl.target_id = c.id)
                   ) AS links_count
            FROM concepts c
            WHERE c.user_id = ? AND c.is_archived = 0
            ORDER BY c.updated_at DESC, c.normalized_name ASC, c.id ASC
            LIMIT ?;
            """,
            (user_id, lim),
        )
        recent_concepts = []
        for row in cursor.fetchall():
            formatted = _format_concept_row(row)
            formatted["links_count"] = int(row["links_count"])
            recent_concepts.append(formatted)

        # Reflexões ativas recentes (sem retornar body_markdown completo)
        cursor.execute(
            """
            SELECT r.id, r.user_id, r.title,
                   SUBSTR(COALESCE(r.body_markdown, ''), 1, 240) AS body_preview,
                   r.primary_book_id, r.tags_json, r.is_pinned, r.is_archived,
                   r.created_at, r.updated_at,
                   (SELECT COUNT(*) FROM knowledge_links kl
                    WHERE kl.user_id = r.user_id AND kl.source_type = 'reflection' AND kl.source_id = r.id
                   ) AS references_count,
                   (SELECT COUNT(*) FROM knowledge_links kl
                    WHERE kl.user_id = r.user_id AND kl.target_type = 'reflection' AND kl.target_id = r.id
                   ) AS backlinks_count
            FROM reflections r
            WHERE r.user_id = ? AND r.is_archived = 0
            ORDER BY r.is_pinned DESC, r.updated_at DESC, r.id ASC
            LIMIT ?;
            """,
            (user_id, lim),
        )
        recent_reflections = []
        for row in cursor.fetchall():
            try:
                tags = json.loads(row["tags_json"] or "[]")
                if not isinstance(tags, list):
                    tags = []
            except Exception:
                tags = []
            p_book_id = row["primary_book_id"]
            p_book_title = None
            p_book_avail = False
            if p_book_id:
                b_row = _check_book_accessible_row(cursor, p_book_id)
                if b_row:
                    p_book_title = b_row["title"]
                    p_book_avail = True
            raw_prev = " ".join(str(row["body_preview"] or "").split())
            excerpt = (raw_prev[:199].rstrip() + "…") if len(raw_prev) > 200 else raw_prev
            recent_reflections.append(
                {
                    "id": row["id"],
                    "user_id": int(row["user_id"]),
                    "title": row["title"],
                    "excerpt": excerpt,
                    "primary_book_id": p_book_id if p_book_avail else None,
                    "primary_book_title": p_book_title,
                    "primary_book_available": p_book_avail,
                    "tags": tags,
                    "is_pinned": bool(row["is_pinned"]),
                    "is_archived": bool(row["is_archived"]),
                    "references_count": int(row["references_count"]),
                    "backlinks_count": int(row["backlinks_count"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

        return {
            "concepts_count": concepts_count,
            "archived_concepts_count": archived_concepts_count,
            "reflections_count": reflections_count,
            "pinned_reflections_count": pinned_reflections_count,
            "links_count": links_count,
            "connected_books_count": connected_books_count,
            "recent_concepts": recent_concepts,
            "recent_reflections": recent_reflections,
        }


# =========================================================================
# 9. PESQUISA INTEGRADA DO ATLAS (`search_knowledge` — KD-008, FASE 6)
# =========================================================================

ENTITY_TYPE_LABELS_PT: Dict[str, str] = {
    "concept": "Conceito",
    "reflection": "Reflexão",
    "highlight": "Destaque",
    "note": "Nota",
    "link": "Conexão",
    "book": "Livro",
}

ENTITY_TYPE_PREFIXES_PT: Dict[str, str] = {
    "concept": "[C]",
    "reflection": "[R]",
    "highlight": "[D]",
    "note": "[N]",
    "link": "[V]",
    "book": "[L]",
}


def _validate_search_pagination(limit: Any = DEFAULT_SEARCH_LIMIT, offset: Any = 0) -> Tuple[int, int]:
    try:
        lim = int(DEFAULT_SEARCH_LIMIT if limit is None else limit)
        off = int(0 if offset is None else offset)
    except (TypeError, ValueError):
        raise KnowledgeValidationError("Parâmetros de paginação (limit/offset) devem ser inteiros.")
    if lim < 1 or lim > MAX_SEARCH_LIMIT:
        raise KnowledgeValidationError(
            f"O parâmetro 'limit' na pesquisa integrada deve estar entre 1 e {MAX_SEARCH_LIMIT}."
        )
    if off < 0:
        raise KnowledgeValidationError("O parâmetro 'offset' não pode ser negativo.")
    return lim, off


def _parse_search_entity_types(entity_types: Optional[Any]) -> set[str]:
    if entity_types is None:
        return set(VALID_SEARCH_ENTITY_TYPES)
    if isinstance(entity_types, str):
        raw_parts = [p.strip().lower() for p in entity_types.split(",") if p.strip()]
    elif isinstance(entity_types, (list, tuple, set)):
        raw_parts = [str(p).strip().lower() for p in entity_types if str(p).strip()]
    else:
        raise KnowledgeValidationError("Filtro 'entity_types' inválido.")
    if not raw_parts:
        return set(VALID_SEARCH_ENTITY_TYPES)
    filter_set = set(raw_parts)
    invalid = filter_set - set(VALID_SEARCH_ENTITY_TYPES)
    if invalid:
        raise KnowledgeValidationError(
            f"Tipos de entidade inválidos para pesquisa: {', '.join(sorted(invalid))}. "
            f"Valores permitidos: {', '.join(VALID_SEARCH_ENTITY_TYPES)}."
        )
    return filter_set


def _format_search_result_item(
    *,
    entity_type: str,
    entity_id: str,
    title: str,
    subtitle: str,
    raw_snippet_source: str,
    folded_tokens: Sequence[str],
    index_source: str,
    is_own: bool,
    visibility: str,
    color: Optional[str] = None,
    tags: Optional[List[str]] = None,
    book_id: Optional[str] = None,
    book_title: Optional[str] = None,
    book_format: Optional[str] = None,
    page: Optional[int] = None,
    epub_cfi: Optional[str] = None,
    chapter_label: Optional[str] = None,
    author_display_name: Optional[str] = None,
    relation_type: Optional[str] = None,
    relation_label: Optional[str] = None,
    source_summary: Optional[Dict[str, Any]] = None,
    target_summary: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    snippet_text, snippet_segments = _build_safe_snippet_segments(
        raw_snippet_source,
        folded_tokens,
        max_len=MAX_SNIPPET_LEN,
    )
    return {
        "type": entity_type,
        "id": entity_id,
        "type_label": ENTITY_TYPE_LABELS_PT.get(entity_type, entity_type),
        "type_prefix": ENTITY_TYPE_PREFIXES_PT.get(entity_type, ""),
        "title": title,
        "label": title,
        "subtitle": subtitle,
        "snippet": snippet_text,
        "snippet_segments": snippet_segments,
        "index_source": index_source,
        "is_own": bool(is_own),
        "visibility": visibility,
        "color": color,
        "tags": tags or [],
        "book_id": book_id,
        "book_title": book_title,
        "book_format": book_format,
        "page": page,
        "epub_cfi": epub_cfi,
        "chapter_label": chapter_label,
        "author_display_name": author_display_name,
        "relation_type": relation_type,
        "relation_label": relation_label,
        "source": source_summary,
        "target": target_summary,
        "can_open_in_graph": entity_type in VALID_ENTITY_TYPES,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _endpoint_public_summary(ep: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resumo seguro de um extremo de conexão para resultados de pesquisa,
    preservando integralmente a proteção de referências indisponíveis.
    """
    if not ep.get("available"):
        return {
            "type": ep.get("type"),
            "id": ep.get("id"),
            "available": False,
            "status": "unavailable",
            "label": "Referência indisponível",
            "title": None,
            "book_id": None,
            "book_title": None,
            "page": None,
            "epub_cfi": None,
        }
    return {
        "type": ep.get("type"),
        "id": ep.get("id"),
        "available": True,
        "status": "available",
        "label": ep.get("label") or ep.get("title") or "Item",
        "title": ep.get("title"),
        "book_id": ep.get("book_id"),
        "book_title": ep.get("book_title"),
        "page": ep.get("page"),
        "epub_cfi": ep.get("epub_cfi"),
    }


def _search_books_metadata_with_cursor(
    cursor: sqlite3.Cursor,
    *,
    folded_tokens: Sequence[str],
    filter_book_id: Optional[str],
    include_archived: bool,
) -> List[Tuple[float, str, str, Dict[str, Any]]]:
    """
    Pesquisa complementar nos metadados de obras acessíveis (`books.title`, `books.author`),
    documentada explicitamente com `index_source = 'books_metadata'` (Seção 12).
    """
    book_where = ["b.is_missing = 0", "COALESCE(s.is_active, 1) = 1"]
    book_params: List[Any] = []
    if filter_book_id:
        book_where.append("b.id = ?")
        book_params.append(filter_book_id)

    cursor.execute(
        f"""
        SELECT b.id, b.title, b.author, b.format, b.page_count, b.created_at, b.updated_at
        FROM books b
        LEFT JOIN library_sources s ON s.id = b.source_id
        WHERE {' AND '.join(book_where)}
        ORDER BY b.title COLLATE NOCASE ASC, b.id ASC
        LIMIT 800;
        """,
        tuple(book_params),
    )
    results: List[Tuple[float, str, str, Dict[str, Any]]] = []
    for row in cursor.fetchall():
        b_title = row["title"] or ""
        b_author = row["author"] or ""
        haystack = f"{b_title} {b_author}"
        if not _text_matches_all_folded_tokens(haystack, folded_tokens):
            continue
        score = _compute_authorized_relevance_score(
            primary_title=b_title,
            secondary_text=b_author,
            tags_list=[],
            folded_tokens=folded_tokens,
            is_own=False,
        )
        fmt_upper = str(row["format"] or "").upper()
        subtitle_parts = [p for p in [b_author or "Autor desconhecido", fmt_upper] if p]
        snippet_source = f"{b_title} — {b_author}" if b_author else b_title
        item = _format_search_result_item(
            entity_type="book",
            entity_id=row["id"],
            title=b_title,
            subtitle=" · ".join(subtitle_parts),
            raw_snippet_source=snippet_source,
            folded_tokens=folded_tokens,
            index_source="books_metadata",
            is_own=False,
            visibility="public",
            book_id=row["id"],
            book_title=b_title,
            book_format=row["format"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        results.append((-score, "book", row["id"], item))
    return results


def _search_knowledge_relational_fallback(
    cursor: sqlite3.Cursor,
    *,
    user_id: int,
    folded_tokens: Sequence[str],
    filter_types: set[str],
    filter_book_id: Optional[str],
    include_archived: bool,
) -> List[Tuple[float, str, str, Dict[str, Any]]]:
    """
    Fallback relacional estritamente limitado e paginado, acionado exclusivamente
    quando o módulo FTS5 não está disponível no ambiente ou quando solicitado em modo de teste.
    Mantém exatamente as mesmas regras de autorização, relevância determinística e geração de snippets.
    """
    scored: List[Tuple[float, str, str, Dict[str, Any]]] = []

    # 1. Conceitos próprios
    if "concept" in filter_types:
        arch_sql = "" if include_archived else "AND c.is_archived = 0"
        if filter_book_id:
            cursor.execute(
                f"""
                SELECT DISTINCT c.*
                FROM concepts c
                JOIN knowledge_links kl ON kl.user_id = c.user_id
                  AND ((kl.source_type = 'concept' AND kl.source_id = c.id)
                       OR (kl.target_type = 'concept' AND kl.target_id = c.id))
                LEFT JOIN highlights h ON (
                    (kl.source_type = 'highlight' AND kl.source_id = h.id)
                    OR (kl.target_type = 'highlight' AND kl.target_id = h.id)
                )
                LEFT JOIN notes n ON (
                    (kl.source_type = 'note' AND kl.source_id = n.id)
                    OR (kl.target_type = 'note' AND kl.target_id = n.id)
                )
                WHERE c.user_id = ? {arch_sql}
                  AND (
                      (kl.source_type = 'book' AND kl.source_id = ?)
                      OR (kl.target_type = 'book' AND kl.target_id = ?)
                      OR h.book_id = ?
                      OR n.book_id = ?
                  )
                ORDER BY c.updated_at DESC, c.id ASC
                LIMIT 600;
                """,
                (user_id, filter_book_id, filter_book_id, filter_book_id, filter_book_id),
            )
        else:
            cursor.execute(
                f"""
                SELECT c.*
                FROM concepts c
                WHERE c.user_id = ? {arch_sql}
                ORDER BY c.updated_at DESC, c.id ASC
                LIMIT 600;
                """,
                (user_id,),
            )
        for crow in cursor.fetchall():
            c_tags = _parse_tags_json(crow["tags_json"])
            live_text = f"{crow['name']} {crow['description'] or ''} {' '.join(c_tags)}"
            if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                continue
            score = _compute_authorized_relevance_score(
                primary_title=crow["name"],
                secondary_text=crow["description"] or "",
                tags_list=c_tags,
                folded_tokens=folded_tokens,
                is_own=True,
            )
            tag_sub = ", ".join(f"#{t}" for t in c_tags[:4]) if c_tags else "Conceito pessoal"
            snippet_src = crow["description"] if (crow["description"] and crow["description"].strip()) else live_text
            item = _format_search_result_item(
                entity_type="concept",
                entity_id=crow["id"],
                title=crow["name"],
                subtitle=tag_sub,
                raw_snippet_source=snippet_src,
                folded_tokens=folded_tokens,
                index_source="relational_fallback",
                is_own=True,
                visibility="private",
                color=crow["color"],
                tags=c_tags,
                created_at=crow["created_at"],
                updated_at=crow["updated_at"],
            )
            scored.append((-score, "concept", crow["id"], item))

    # 2. Reflexões próprias
    if "reflection" in filter_types:
        r_where = ["r.user_id = ?"]
        r_params: List[Any] = [user_id]
        if not include_archived:
            r_where.append("r.is_archived = 0")
        if filter_book_id:
            r_where.append("r.primary_book_id = ?")
            r_params.append(filter_book_id)
        cursor.execute(
            f"""
            SELECT r.*
            FROM reflections r
            WHERE {' AND '.join(r_where)}
            ORDER BY r.is_pinned DESC, r.updated_at DESC, r.id ASC
            LIMIT 600;
            """,
            tuple(r_params),
        )
        for rrow in cursor.fetchall():
            r_tags = _parse_tags_json(rrow["tags_json"])
            live_text = f"{rrow['title']} {rrow['body_markdown'] or ''} {' '.join(r_tags)}"
            if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                continue
            b_row = _check_book_accessible_row(cursor, rrow["primary_book_id"]) if rrow["primary_book_id"] else None
            score = _compute_authorized_relevance_score(
                primary_title=rrow["title"],
                secondary_text=rrow["body_markdown"] or "",
                tags_list=r_tags,
                folded_tokens=folded_tokens,
                is_own=True,
            )
            sub_parts = ["Reflexão autoral"]
            if b_row:
                sub_parts.append(b_row["title"])
            snippet_src = rrow["body_markdown"] if (rrow["body_markdown"] and rrow["body_markdown"].strip()) else live_text
            item = _format_search_result_item(
                entity_type="reflection",
                entity_id=rrow["id"],
                title=rrow["title"],
                subtitle=" · ".join(sub_parts),
                raw_snippet_source=snippet_src,
                folded_tokens=folded_tokens,
                index_source="relational_fallback",
                is_own=True,
                visibility="private",
                tags=r_tags,
                book_id=b_row["id"] if b_row else None,
                book_title=b_row["title"] if b_row else None,
                book_format=b_row["format"] if b_row else None,
                created_at=rrow["created_at"],
                updated_at=rrow["updated_at"],
            )
            scored.append((-score, "reflection", rrow["id"], item))

    # 3. Destaques autorizados
    if "highlight" in filter_types:
        h_where = [
            "b.is_missing = 0",
            "COALESCE(s.is_active, 1) = 1",
            "(h.user_id = ? OR h.visibility = 'public')",
        ]
        h_params: List[Any] = [user_id]
        if filter_book_id:
            h_where.append("h.book_id = ?")
            h_params.append(filter_book_id)
        cursor.execute(
            f"""
            SELECT h.*, b.title AS book_title, b.format AS book_format,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM highlights h
            JOIN books b ON b.id = h.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = h.user_id
            LEFT JOIN user_profiles up ON up.user_id = h.user_id
            WHERE {' AND '.join(h_where)}
            ORDER BY h.created_at DESC, h.id ASC
            LIMIT 600;
            """,
            tuple(h_params),
        )
        for hrow in cursor.fetchall():
            body_txt = (hrow["selected_text"] or hrow["text_context"] or "").strip()
            full_body = f"{hrow['selected_text'] or ''} {hrow['text_context'] or ''}".strip()
            live_text = f"{hrow['chapter_label'] or ''} {full_body}"
            if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                continue
            is_own_hl = int(hrow["user_id"]) == int(user_id)
            score = _compute_authorized_relevance_score(
                primary_title=hrow["chapter_label"] or hrow["book_title"] or "",
                secondary_text=full_body,
                tags_list=[],
                folded_tokens=folded_tokens,
                is_own=is_own_hl,
            )
            loc_parts = [hrow["book_title"]]
            if hrow["chapter_label"]:
                loc_parts.append(hrow["chapter_label"])
            if hrow["page"]:
                loc_parts.append(f"p. {hrow['page']}")
            item = _format_search_result_item(
                entity_type="highlight",
                entity_id=hrow["id"],
                title=(body_txt[:90] + "…") if len(body_txt) > 90 else (body_txt or f"Destaque (p. {hrow['page']})"),
                subtitle=" · ".join(p for p in loc_parts if p),
                raw_snippet_source=body_txt or live_text,
                folded_tokens=folded_tokens,
                index_source="relational_fallback",
                is_own=is_own_hl,
                visibility=hrow["visibility"],
                color=hrow["color"],
                book_id=hrow["book_id"],
                book_title=hrow["book_title"],
                book_format=hrow["book_format"],
                page=hrow["page"],
                epub_cfi=hrow["epub_cfi"],
                chapter_label=hrow["chapter_label"],
                author_display_name=hrow["author_display_name"],
                created_at=hrow["created_at"],
                updated_at=hrow["updated_at"],
            )
            scored.append((-score, "highlight", hrow["id"], item))

    # 4. Notas autorizadas
    if "note" in filter_types:
        n_where = [
            "b.is_missing = 0",
            "COALESCE(s.is_active, 1) = 1",
            "(n.user_id = ? OR n.visibility = 'public')",
        ]
        n_params: List[Any] = [user_id]
        if filter_book_id:
            n_where.append("n.book_id = ?")
            n_params.append(filter_book_id)
        cursor.execute(
            f"""
            SELECT n.*, b.title AS book_title, b.format AS book_format,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM notes n
            JOIN books b ON b.id = n.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN user_profiles up ON up.user_id = n.user_id
            WHERE {' AND '.join(n_where)}
            ORDER BY n.created_at DESC, n.id ASC
            LIMIT 600;
            """,
            tuple(n_params),
        )
        for nrow in cursor.fetchall():
            body_txt = (nrow["content"] or "").strip()
            live_text = f"{nrow['chapter_label'] or ''} {body_txt}"
            if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                continue
            is_own_nt = int(nrow["user_id"]) == int(user_id)
            score = _compute_authorized_relevance_score(
                primary_title=nrow["chapter_label"] or nrow["book_title"] or "",
                secondary_text=body_txt,
                tags_list=[],
                folded_tokens=folded_tokens,
                is_own=is_own_nt,
            )
            loc_parts = [nrow["book_title"]]
            if nrow["chapter_label"]:
                loc_parts.append(nrow["chapter_label"])
            if nrow["page"]:
                loc_parts.append(f"p. {nrow['page']}")
            item = _format_search_result_item(
                entity_type="note",
                entity_id=nrow["id"],
                title=(body_txt[:90] + "…") if len(body_txt) > 90 else (body_txt or f"Nota (p. {nrow['page']})"),
                subtitle=" · ".join(p for p in loc_parts if p),
                raw_snippet_source=body_txt or live_text,
                folded_tokens=folded_tokens,
                index_source="relational_fallback",
                is_own=is_own_nt,
                visibility=nrow["visibility"],
                color=nrow["color"],
                book_id=nrow["book_id"],
                book_title=nrow["book_title"],
                book_format=nrow["book_format"],
                page=nrow["page"],
                epub_cfi=nrow["epub_cfi"],
                chapter_label=nrow["chapter_label"],
                author_display_name=nrow["author_display_name"],
                created_at=nrow["created_at"],
                updated_at=nrow["updated_at"],
            )
            scored.append((-score, "note", nrow["id"], item))

    # 5. Conexões próprias (com justificativa/tipo de relação)
    if "link" in filter_types:
        cursor.execute(
            """
            SELECT * FROM knowledge_links
            WHERE user_id = ?
            ORDER BY created_at DESC, id ASC
            LIMIT 600;
            """,
            (user_id,),
        )
        for lrow in cursor.fetchall():
            live_haystack = f"{lrow['relation_type'] or ''} {lrow['rationale'] or ''}"
            if not _text_matches_all_folded_tokens(live_haystack, folded_tokens):
                continue
            hydrated = _hydrate_link_row(cursor, lrow, user_id)
            s_pub = _endpoint_public_summary(hydrated["source"])
            t_pub = _endpoint_public_summary(hydrated["target"])
            if filter_book_id and filter_book_id not in (s_pub.get("book_id"), t_pub.get("book_id")):
                continue
            score = _compute_authorized_relevance_score(
                primary_title=hydrated["relation_label"],
                secondary_text=hydrated["rationale"] or "",
                tags_list=[],
                folded_tokens=folded_tokens,
                is_own=True,
            )
            link_title = f"{s_pub['label']} → {hydrated['outgoing_label']} → {t_pub['label']}"
            item = _format_search_result_item(
                entity_type="link",
                entity_id=hydrated["id"],
                title=link_title,
                subtitle=f"Conexão semântica · {hydrated['outgoing_label']}",
                raw_snippet_source=hydrated["rationale"] or hydrated["outgoing_label"],
                folded_tokens=folded_tokens,
                index_source="relational_fallback",
                is_own=True,
                visibility="private",
                book_id=s_pub.get("book_id") or t_pub.get("book_id"),
                book_title=s_pub.get("book_title") or t_pub.get("book_title"),
                relation_type=hydrated["relation_type"],
                relation_label=hydrated["outgoing_label"],
                source_summary=s_pub,
                target_summary=t_pub,
                created_at=hydrated["created_at"],
                updated_at=hydrated["updated_at"],
            )
            scored.append((-score, "link", hydrated["id"], item))

    # 6. Obras acessíveis
    if "book" in filter_types:
        scored.extend(
            _search_books_metadata_with_cursor(
                cursor,
                folded_tokens=folded_tokens,
                filter_book_id=filter_book_id,
                include_archived=include_archived,
            )
        )

    return scored


def search_knowledge(
    user_id: int,
    query: str,
    *,
    entity_types: Optional[Any] = None,
    book_id: Optional[str] = None,
    include_archived: bool = False,
    limit: int = DEFAULT_SEARCH_LIMIT,
    offset: int = 0,
    force_fallback: bool = False,
) -> Dict[str, Any]:
    """
    Pesquisa integrada do Atlas (`GET /api/knowledge/search` — KD-008, Seções 12–22).

    Propriedades garantidas:
    1. Reutiliza `knowledge_fts` (`unicode61 remove_diacritics 2`) para `concept`, `reflection`,
       `highlight`, `note` e `link`, complementando com busca em metadados de obras acessíveis
       (`book`, marcada com `index_source='books_metadata'`).
    2. Integra a validação relacional de permissões (`EXISTS (...)`) diretamente na consulta SQL de
       candidatos antes de qualquer truncamento ou paginação (Seções 15 e 16).
    3. Revalida em tempo de leitura a correspondência real dos tokens no texto relacional ao vivo,
       protegendo contra entradas obsoletas no índice FTS5 e contra referências indisponíveis.
    4. Calcula a relevância exclusivamente sobre os dados do próprio item autorizado
       (`_compute_authorized_relevance_score`), eliminando qualquer vazamento estatístico de IDF
       de `bm25(knowledge_fts)` entre usuários distintos (Seção 17).
    5. Gera snippets seguros e segmentos estruturados (`snippet_segments`) a partir do texto relacional
       autorizado, sem HTML bruto (`innerHTML` proibido).
    6. Distingue ausência de suporte FTS5 (acionando `_search_knowledge_relational_fallback`) de
       corrupção estrutural do banco/índice (propagando erro diagnosticável).
    """
    lim, off = _validate_search_pagination(limit, offset)
    cleaned_q = normalize_unicode_nfc(query or "").strip()
    if len(cleaned_q) > MAX_SEARCH_QUERY_LEN:
        raise KnowledgeValidationError(
            f"O termo de pesquisa excede o limite de {MAX_SEARCH_QUERY_LEN} caracteres."
        )
    filter_types = _parse_search_entity_types(entity_types)

    filter_book_id: Optional[str] = None
    if book_id is not None and str(book_id).strip():
        filter_book_id = normalize_unicode_nfc(str(book_id)).strip()

    fts_expr = _build_safe_fts_query(cleaned_q)
    folded_tokens = _extract_folded_tokens(cleaned_q)

    if not fts_expr or not folded_tokens:
        return {
            "items": [],
            "total": 0,
            "limit": lim,
            "offset": off,
            "has_more": False,
            "query": cleaned_q,
            "entity_types": sorted(filter_types),
            "book_id": filter_book_id,
            "search_engine": "fts5",
        }

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        if filter_book_id is not None:
            if not _check_book_accessible_row(cursor, filter_book_id, user_id=user_id):
                raise KnowledgeNotFoundError("Livro filtrado não encontrado ou indisponível.")

        fts_target_types = sorted(filter_types & {"concept", "reflection", "highlight", "note", "link"})
        scored_items: List[Tuple[float, str, str, Dict[str, Any]]] = []
        search_engine_used = "fts5"

        if force_fallback:
            search_engine_used = "relational_fallback"
            scored_items = _search_knowledge_relational_fallback(
                cursor,
                user_id=user_id,
                folded_tokens=folded_tokens,
                filter_types=filter_types,
                filter_book_id=filter_book_id,
                include_archived=include_archived,
            )
        else:
            if fts_target_types:
                type_ph = ",".join("?" for _ in fts_target_types)
                concept_arch_sql = "" if include_archived else "AND c.is_archived = 0"
                refl_arch_sql = "" if include_archived else "AND r.is_archived = 0"
                try:
                    cursor.execute(
                        f"""
                        SELECT kf.entity_type, kf.entity_id
                        FROM knowledge_fts kf
                        WHERE knowledge_fts MATCH ?
                          AND kf.entity_type IN ({type_ph})
                          AND (
                              (kf.entity_type = 'concept' AND EXISTS (
                                  SELECT 1 FROM concepts c
                                  WHERE c.id = kf.entity_id AND c.user_id = ? {concept_arch_sql}
                              ))
                              OR (kf.entity_type = 'reflection' AND EXISTS (
                                  SELECT 1 FROM reflections r
                                  WHERE r.id = kf.entity_id AND r.user_id = ? {refl_arch_sql}
                              ))
                              OR (kf.entity_type = 'link' AND EXISTS (
                                  SELECT 1 FROM knowledge_links kl
                                  WHERE kl.id = kf.entity_id AND kl.user_id = ?
                              ))
                              OR (kf.entity_type = 'highlight' AND EXISTS (
                                  SELECT 1 FROM highlights h
                                  JOIN books b ON b.id = h.book_id
                                  LEFT JOIN library_sources s ON s.id = b.source_id
                                  WHERE h.id = kf.entity_id
                                    AND b.is_missing = 0
                                    AND COALESCE(s.is_active, 1) = 1
                                    AND (h.user_id = ? OR h.visibility = 'public')
                              ))
                              OR (kf.entity_type = 'note' AND EXISTS (
                                  SELECT 1 FROM notes n
                                  JOIN books b ON b.id = n.book_id
                                  LEFT JOIN library_sources s ON s.id = b.source_id
                                  WHERE n.id = kf.entity_id
                                    AND b.is_missing = 0
                                    AND COALESCE(s.is_active, 1) = 1
                                    AND (n.user_id = ? OR n.visibility = 'public')
                              ))
                          )
                        ORDER BY kf.entity_type ASC, kf.entity_id ASC
                        LIMIT 2000;
                        """,
                        (
                            fts_expr,
                            *fts_target_types,
                            user_id,
                            user_id,
                            user_id,
                            user_id,
                            user_id,
                        ),
                    )
                    raw_candidates = cursor.fetchall()
                except sqlite3.DatabaseError as exc:
                    err_msg = str(exc).lower()
                    if "malformed" in err_msg or "corrupt" in err_msg:
                        raise
                    if "no such module: fts5" in err_msg or "no such table: knowledge_fts" in err_msg:
                        search_engine_used = "relational_fallback"
                        scored_items = _search_knowledge_relational_fallback(
                            cursor,
                            user_id=user_id,
                            folded_tokens=folded_tokens,
                            filter_types=filter_types,
                            filter_book_id=filter_book_id,
                            include_archived=include_archived,
                        )
                        raw_candidates = []
                    else:
                        raise

                if search_engine_used == "fts5":
                    seen_keys: set[Tuple[str, str]] = set()
                    cand_ids_by_type: Dict[str, List[str]] = {
                        "concept": [],
                        "reflection": [],
                        "highlight": [],
                        "note": [],
                        "link": [],
                    }
                    for cand in raw_candidates:
                        ctype = cand["entity_type"]
                        cid = cand["entity_id"]
                        key = (ctype, cid)
                        if key in seen_keys or ctype not in cand_ids_by_type:
                            continue
                        seen_keys.add(key)
                        cand_ids_by_type[ctype].append(cid)

                    # 1) Conceitos em lote (zero N+1 + formatação adiada)
                    if cand_ids_by_type["concept"]:
                        matched_concepts: List[Tuple[sqlite3.Row, List[str], str]] = []
                        arch_clause = "" if include_archived else " AND is_archived = 0"
                        for chunk in _chunked(cand_ids_by_type["concept"]):
                            ph = ",".join("?" for _ in chunk)
                            cursor.execute(
                                f"SELECT * FROM concepts WHERE user_id = ?{arch_clause} AND id IN ({ph});",
                                (user_id, *chunk),
                            )
                            for crow in cursor.fetchall():
                                c_tags = _parse_tags_json(crow["tags_json"])
                                live_text = f"{crow['name']} {crow['description'] or ''} {' '.join(c_tags)}"
                                if _text_matches_all_folded_tokens(live_text, folded_tokens):
                                    matched_concepts.append((crow, c_tags, live_text))

                        if filter_book_id and matched_concepts:
                            allowed_cids: set[str] = set()
                            mcids = [crow["id"] for crow, _, _ in matched_concepts]
                            for chunk in _chunked(mcids):
                                ph = ",".join("?" for _ in chunk)
                                cursor.execute(
                                    f"""
                                    SELECT DISTINCT kl.source_id AS cid
                                    FROM knowledge_links kl
                                    LEFT JOIN highlights h ON kl.target_type = 'highlight' AND kl.target_id = h.id
                                    LEFT JOIN notes n ON kl.target_type = 'note' AND kl.target_id = n.id
                                    WHERE kl.user_id = ? AND kl.source_type = 'concept' AND kl.source_id IN ({ph})
                                      AND ((kl.target_type = 'book' AND kl.target_id = ?) OR h.book_id = ? OR n.book_id = ?)
                                    UNION
                                    SELECT DISTINCT kl.target_id AS cid
                                    FROM knowledge_links kl
                                    LEFT JOIN highlights h ON kl.source_type = 'highlight' AND kl.source_id = h.id
                                    LEFT JOIN notes n ON kl.source_type = 'note' AND kl.source_id = n.id
                                    WHERE kl.user_id = ? AND kl.target_type = 'concept' AND kl.target_id IN ({ph})
                                      AND ((kl.source_type = 'book' AND kl.source_id = ?) OR h.book_id = ? OR n.book_id = ?);
                                    """,
                                    (
                                        user_id, *chunk, filter_book_id, filter_book_id, filter_book_id,
                                        user_id, *chunk, filter_book_id, filter_book_id, filter_book_id,
                                    ),
                                )
                                for row_cid in cursor.fetchall():
                                    allowed_cids.add(row_cid["cid"])
                            matched_concepts = [
                                item for item in matched_concepts if item[0]["id"] in allowed_cids
                            ]

                        for crow, c_tags, live_text in matched_concepts:
                            score = _compute_authorized_relevance_score(
                                primary_title=crow["name"],
                                secondary_text=crow["description"] or "",
                                tags_list=c_tags,
                                folded_tokens=folded_tokens,
                                is_own=True,
                            )
                            tag_sub = ", ".join(f"#{t}" for t in c_tags[:4]) if c_tags else "Conceito pessoal"
                            snippet_src = (
                                crow["description"]
                                if (crow["description"] and _text_matches_all_folded_tokens(crow["description"], folded_tokens))
                                else live_text
                            )
                            scored_items.append((
                                -score,
                                "concept",
                                crow["id"],
                                (
                                    _format_search_result_item,
                                    {
                                        "entity_type": "concept",
                                        "entity_id": crow["id"],
                                        "title": crow["name"],
                                        "subtitle": tag_sub,
                                        "raw_snippet_source": snippet_src,
                                        "folded_tokens": folded_tokens,
                                        "index_source": "knowledge_fts",
                                        "is_own": True,
                                        "visibility": "private",
                                        "color": crow["color"],
                                        "tags": c_tags,
                                        "created_at": crow["created_at"],
                                        "updated_at": crow["updated_at"],
                                    },
                                ),
                            ))

                    # 2) Reflexões em lote (zero N+1 + hidratação de livros em lote)
                    if cand_ids_by_type["reflection"]:
                        matched_refs: List[Tuple[sqlite3.Row, List[str], str]] = []
                        arch_clause = "" if include_archived else " AND is_archived = 0"
                        book_clause = " AND primary_book_id = ?" if filter_book_id else ""
                        for chunk in _chunked(cand_ids_by_type["reflection"]):
                            ph = ",".join("?" for _ in chunk)
                            q_params: List[Any] = [user_id]
                            if filter_book_id:
                                q_params.append(filter_book_id)
                            q_params.extend(chunk)
                            cursor.execute(
                                f"SELECT * FROM reflections WHERE user_id = ?{arch_clause}{book_clause} AND id IN ({ph});",
                                tuple(q_params),
                            )
                            for rrow in cursor.fetchall():
                                r_tags = _parse_tags_json(rrow["tags_json"])
                                live_text = f"{rrow['title']} {rrow['body_markdown'] or ''} {' '.join(r_tags)}"
                                if _text_matches_all_folded_tokens(live_text, folded_tokens):
                                    matched_refs.append((rrow, r_tags, live_text))

                        ref_books_map: Dict[str, Dict[str, Any]] = {}
                        ref_book_ids = sorted({
                            rrow["primary_book_id"] for rrow, _, _ in matched_refs if rrow["primary_book_id"]
                        })
                        for chunk in _chunked(ref_book_ids):
                            bph = ",".join("?" for _ in chunk)
                            cursor.execute(
                                f"""
                                SELECT b.id, b.title, b.format
                                FROM books b
                                LEFT JOIN library_sources s ON s.id = b.source_id
                                WHERE b.id IN ({bph})
                                  AND b.is_missing = 0
                                  AND COALESCE(s.is_active, 1) = 1;
                                """,
                                tuple(chunk),
                            )
                            for brow in cursor.fetchall():
                                ref_books_map[brow["id"]] = dict(brow)

                        for rrow, r_tags, live_text in matched_refs:
                            b_row = ref_books_map.get(rrow["primary_book_id"]) if rrow["primary_book_id"] else None
                            score = _compute_authorized_relevance_score(
                                primary_title=rrow["title"],
                                secondary_text=rrow["body_markdown"] or "",
                                tags_list=r_tags,
                                folded_tokens=folded_tokens,
                                is_own=True,
                            )
                            sub_parts = ["Reflexão autoral"]
                            if b_row:
                                sub_parts.append(b_row["title"])
                            snippet_src = (
                                rrow["body_markdown"]
                                if (rrow["body_markdown"] and _text_matches_all_folded_tokens(rrow["body_markdown"], folded_tokens))
                                else live_text
                            )
                            scored_items.append((
                                -score,
                                "reflection",
                                rrow["id"],
                                (
                                    _format_search_result_item,
                                    {
                                        "entity_type": "reflection",
                                        "entity_id": rrow["id"],
                                        "title": rrow["title"],
                                        "subtitle": " · ".join(sub_parts),
                                        "raw_snippet_source": snippet_src,
                                        "folded_tokens": folded_tokens,
                                        "index_source": "knowledge_fts",
                                        "is_own": True,
                                        "visibility": "private",
                                        "tags": r_tags,
                                        "book_id": b_row["id"] if b_row else None,
                                        "book_title": b_row["title"] if b_row else None,
                                        "book_format": b_row["format"] if b_row else None,
                                        "created_at": rrow["created_at"],
                                        "updated_at": rrow["updated_at"],
                                    },
                                ),
                            ))

                    # 3) Destaques em lote com Read-Time Authorization completa (zero N+1)
                    if cand_ids_by_type["highlight"]:
                        book_clause = " AND h.book_id = ?" if filter_book_id else ""
                        for chunk in _chunked(cand_ids_by_type["highlight"]):
                            ph = ",".join("?" for _ in chunk)
                            q_params = [*chunk, user_id]
                            if filter_book_id:
                                q_params.append(filter_book_id)
                            cursor.execute(
                                f"""
                                SELECT h.id, h.book_id, h.page, h.color, h.selected_text, h.text_context,
                                       h.user_id, h.visibility, h.epub_cfi, h.chapter_label,
                                       h.created_at, h.updated_at,
                                       b.title AS book_title, b.format AS book_format,
                                       COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
                                FROM highlights h
                                JOIN books b ON b.id = h.book_id
                                LEFT JOIN library_sources s ON s.id = b.source_id
                                LEFT JOIN users u ON u.id = h.user_id
                                LEFT JOIN user_profiles up ON up.user_id = h.user_id
                                WHERE h.id IN ({ph})
                                  AND b.is_missing = 0
                                  AND COALESCE(s.is_active, 1) = 1
                                  AND (h.user_id = ? OR h.visibility = 'public')
                                  {book_clause};
                                """,
                                tuple(q_params),
                            )
                            for hrow in cursor.fetchall():
                                body_txt = (hrow["selected_text"] or hrow["text_context"] or "").strip()
                                full_body = f"{hrow['selected_text'] or ''} {hrow['text_context'] or ''}".strip()
                                live_text = f"{hrow['chapter_label'] or ''} {full_body}"
                                if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                                    continue
                                is_own_hl = int(hrow["user_id"]) == int(user_id)
                                score = _compute_authorized_relevance_score(
                                    primary_title=hrow["chapter_label"] or hrow["book_title"] or "",
                                    secondary_text=full_body,
                                    tags_list=[],
                                    folded_tokens=folded_tokens,
                                    is_own=is_own_hl,
                                )
                                loc_parts = [hrow["book_title"]]
                                if hrow["chapter_label"]:
                                    loc_parts.append(hrow["chapter_label"])
                                if hrow["page"]:
                                    loc_parts.append(f"p. {hrow['page']}")
                                scored_items.append((
                                    -score,
                                    "highlight",
                                    hrow["id"],
                                    (
                                        _format_search_result_item,
                                        {
                                            "entity_type": "highlight",
                                            "entity_id": hrow["id"],
                                            "title": (body_txt[:90] + "…") if len(body_txt) > 90 else (body_txt or f"Destaque (p. {hrow['page']})"),
                                            "subtitle": " · ".join(str(p) for p in loc_parts if p),
                                            "raw_snippet_source": body_txt or live_text,
                                            "folded_tokens": folded_tokens,
                                            "index_source": "knowledge_fts",
                                            "is_own": is_own_hl,
                                            "visibility": str(hrow["visibility"] or "private"),
                                            "color": hrow["color"],
                                            "book_id": hrow["book_id"],
                                            "book_title": hrow["book_title"],
                                            "book_format": hrow["book_format"],
                                            "page": hrow["page"],
                                            "epub_cfi": hrow["epub_cfi"],
                                            "chapter_label": hrow["chapter_label"],
                                            "author_display_name": hrow["author_display_name"],
                                            "created_at": hrow["created_at"],
                                            "updated_at": hrow["updated_at"],
                                        },
                                    ),
                                ))

                    # 4) Notas em lote com Read-Time Authorization completa (zero N+1)
                    if cand_ids_by_type["note"]:
                        book_clause = " AND n.book_id = ?" if filter_book_id else ""
                        for chunk in _chunked(cand_ids_by_type["note"]):
                            ph = ",".join("?" for _ in chunk)
                            q_params = [*chunk, user_id]
                            if filter_book_id:
                                q_params.append(filter_book_id)
                            cursor.execute(
                                f"""
                                SELECT n.id, n.book_id, n.page, n.content, n.color,
                                       n.user_id, n.visibility, n.epub_cfi, n.chapter_label,
                                       n.created_at, n.updated_at,
                                       b.title AS book_title, b.format AS book_format,
                                       COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
                                FROM notes n
                                JOIN books b ON b.id = n.book_id
                                LEFT JOIN library_sources s ON s.id = b.source_id
                                LEFT JOIN users u ON u.id = n.user_id
                                LEFT JOIN user_profiles up ON up.user_id = n.user_id
                                WHERE n.id IN ({ph})
                                  AND b.is_missing = 0
                                  AND COALESCE(s.is_active, 1) = 1
                                  AND (n.user_id = ? OR n.visibility = 'public')
                                  {book_clause};
                                """,
                                tuple(q_params),
                            )
                            for nrow in cursor.fetchall():
                                body_txt = (nrow["content"] or "").strip()
                                live_text = f"{nrow['chapter_label'] or ''} {body_txt}"
                                if not _text_matches_all_folded_tokens(live_text, folded_tokens):
                                    continue
                                is_own_nt = int(nrow["user_id"]) == int(user_id)
                                score = _compute_authorized_relevance_score(
                                    primary_title=nrow["chapter_label"] or nrow["book_title"] or "",
                                    secondary_text=body_txt,
                                    tags_list=[],
                                    folded_tokens=folded_tokens,
                                    is_own=is_own_nt,
                                )
                                loc_parts = [nrow["book_title"]]
                                if nrow["chapter_label"]:
                                    loc_parts.append(nrow["chapter_label"])
                                if nrow["page"]:
                                    loc_parts.append(f"p. {nrow['page']}")
                                scored_items.append((
                                    -score,
                                    "note",
                                    nrow["id"],
                                    (
                                        _format_search_result_item,
                                        {
                                            "entity_type": "note",
                                            "entity_id": nrow["id"],
                                            "title": (body_txt[:90] + "…") if len(body_txt) > 90 else (body_txt or f"Nota (p. {nrow['page']})"),
                                            "subtitle": " · ".join(str(p) for p in loc_parts if p),
                                            "raw_snippet_source": body_txt or live_text,
                                            "folded_tokens": folded_tokens,
                                            "index_source": "knowledge_fts",
                                            "is_own": is_own_nt,
                                            "visibility": str(nrow["visibility"] or "private"),
                                            "color": nrow["color"],
                                            "book_id": nrow["book_id"],
                                            "book_title": nrow["book_title"],
                                            "book_format": nrow["book_format"],
                                            "page": nrow["page"],
                                            "epub_cfi": nrow["epub_cfi"],
                                            "chapter_label": nrow["chapter_label"],
                                            "author_display_name": nrow["author_display_name"],
                                            "created_at": nrow["created_at"],
                                            "updated_at": nrow["updated_at"],
                                        },
                                    ),
                                ))

                    # 5) Conexões em lote
                    if cand_ids_by_type["link"]:
                        for chunk in _chunked(cand_ids_by_type["link"]):
                            ph = ",".join("?" for _ in chunk)
                            cursor.execute(
                                f"SELECT * FROM knowledge_links WHERE user_id = ? AND id IN ({ph});",
                                (user_id, *chunk),
                            )
                            for lrow in cursor.fetchall():
                                live_haystack = f"{lrow['relation_type'] or ''} {lrow['rationale'] or ''}"
                                if not _text_matches_all_folded_tokens(live_haystack, folded_tokens):
                                    continue
                                hydrated = _hydrate_link_row(cursor, lrow, user_id)
                                s_pub = _endpoint_public_summary(hydrated["source"])
                                t_pub = _endpoint_public_summary(hydrated["target"])
                                if filter_book_id and filter_book_id not in (s_pub.get("book_id"), t_pub.get("book_id")):
                                    continue
                                score = _compute_authorized_relevance_score(
                                    primary_title=hydrated["relation_label"],
                                    secondary_text=hydrated["rationale"] or "",
                                    tags_list=[],
                                    folded_tokens=folded_tokens,
                                    is_own=True,
                                )
                                link_title = f"{s_pub['label']} → {hydrated['outgoing_label']} → {t_pub['label']}"
                                scored_items.append((
                                    -score,
                                    "link",
                                    hydrated["id"],
                                    (
                                        _format_search_result_item,
                                        {
                                            "entity_type": "link",
                                            "entity_id": hydrated["id"],
                                            "title": link_title,
                                            "subtitle": f"Conexão semântica · {hydrated['outgoing_label']}",
                                            "raw_snippet_source": hydrated["rationale"] or hydrated["outgoing_label"],
                                            "folded_tokens": folded_tokens,
                                            "index_source": "knowledge_fts",
                                            "is_own": True,
                                            "visibility": "private",
                                            "book_id": s_pub.get("book_id") or t_pub.get("book_id"),
                                            "book_title": s_pub.get("book_title") or t_pub.get("book_title"),
                                            "relation_type": hydrated["relation_type"],
                                            "relation_label": hydrated["outgoing_label"],
                                            "source_summary": s_pub,
                                            "target_summary": t_pub,
                                            "created_at": hydrated["created_at"],
                                            "updated_at": hydrated["updated_at"],
                                        },
                                    ),
                                ))

            if search_engine_used == "fts5" and "book" in filter_types:
                scored_items.extend(
                    _search_books_metadata_with_cursor(
                        cursor,
                        folded_tokens=folded_tokens,
                        filter_book_id=filter_book_id,
                        include_archived=include_archived,
                    )
                )

        scored_items.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
        total = len(scored_items)
        page_slice = scored_items[off : off + lim]
        page_items = [
            entry[3][0](**entry[3][1]) if isinstance(entry[3], tuple) else entry[3]
            for entry in page_slice
        ]

        return {
            "items": page_items,
            "total": total,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(page_items)) < total,
            "query": cleaned_q,
            "entity_types": sorted(filter_types),
            "book_id": filter_book_id,
            "search_engine": search_engine_used,
        }


# =========================================================================
# 10. GRAFO LOCAL DE CONHECIMENTO (`get_knowledge_graph` — KD-006, FASE 6)
# =========================================================================

def _parse_graph_entity_types(entity_types: Optional[Any]) -> set[str]:
    if entity_types is None:
        return set(VALID_ENTITY_TYPES)
    if isinstance(entity_types, str):
        parts = [p.strip().lower() for p in entity_types.split(",") if p.strip()]
    elif isinstance(entity_types, (list, tuple, set)):
        parts = [str(p).strip().lower() for p in entity_types if str(p).strip()]
    else:
        raise KnowledgeValidationError("Filtro 'entity_types' inválido para o grafo.")
    if not parts:
        return set(VALID_ENTITY_TYPES)
    result = set(parts)
    invalid = result - set(VALID_ENTITY_TYPES)
    if invalid:
        raise KnowledgeValidationError(
            f"Tipos de entidade inválidos para o grafo: {', '.join(sorted(invalid))}. "
            f"Valores permitidos: {', '.join(VALID_ENTITY_TYPES)}."
        )
    return result


def _parse_graph_relation_types(relation_types: Optional[Any]) -> set[str]:
    if relation_types is None:
        return set(VALID_RELATION_TYPES)
    if isinstance(relation_types, str):
        parts = [p.strip().lower() for p in relation_types.split(",") if p.strip()]
    elif isinstance(relation_types, (list, tuple, set)):
        parts = [str(p).strip().lower() for p in relation_types if str(p).strip()]
    else:
        raise KnowledgeValidationError("Filtro 'relation_types' inválido para o grafo.")
    if not parts:
        return set(VALID_RELATION_TYPES)
    result = set(parts)
    invalid = result - set(VALID_RELATION_TYPES)
    if invalid:
        raise KnowledgeValidationError(
            f"Tipos de relação inválidos para o grafo: {', '.join(sorted(invalid))}. "
            f"Valores permitidos: {', '.join(VALID_RELATION_TYPES)}."
        )
    return result


def _batch_resolve_graph_entities(
    cursor: sqlite3.Cursor,
    user_id: int,
    entity_keys: Sequence[Tuple[str, str]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Resolve um lote de entidades `(entity_type, entity_id)` em no máximo 1 consulta por tipo
    (zero N+1), aplicando estritamente as mesmas regras de autorização em tempo de leitura de
    `resolve_knowledge_entity(..., for_link_endpoint=True)`.
    Entidades inacessíveis, revogadas ou inexistentes retornam o sentinela neutro
    `_unavailable_entity_sentinel(etype, eid)`.
    """
    by_type: Dict[str, List[str]] = {t: [] for t in VALID_ENTITY_TYPES}
    unique_keys: set[Tuple[str, str]] = set()
    for etype, eid in entity_keys:
        if etype in by_type and eid and (etype, eid) not in unique_keys:
            unique_keys.add((etype, eid))
            by_type[etype].append(eid)

    resolved_map: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # 1. Conceitos próprios
    if by_type["concept"]:
        cids = sorted(by_type["concept"])
        ph = ",".join("?" for _ in cids)
        cursor.execute(
            f"""
            SELECT id, user_id, name, description, tags_json, color, is_archived, created_at, updated_at
            FROM concepts
            WHERE user_id = ? AND id IN ({ph});
            """,
            (user_id, *cids),
        )
        for row in cursor.fetchall():
            resolved_map[("concept", row["id"])] = {
                "type": "concept",
                "id": row["id"],
                "available": True,
                "status": "available",
                "is_own": True,
                "user_id": int(row["user_id"]),
                "label": row["name"],
                "title": row["name"],
                "subtitle": "Conceito pessoal",
                "excerpt": (row["description"] or "")[:240],
                "color": row["color"],
                "tags": _parse_tags_json(row["tags_json"]),
                "is_archived": bool(row["is_archived"]),
                "book_id": None,
                "book_title": None,
                "book_format": None,
                "page": None,
                "epub_cfi": None,
                "chapter_label": None,
                "visibility": "private",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    # 2. Reflexões próprias (sem carregar body_markdown integral)
    if by_type["reflection"]:
        rids = sorted(by_type["reflection"])
        ph = ",".join("?" for _ in rids)
        cursor.execute(
            f"""
            SELECT r.id, r.user_id, r.title,
                   SUBSTR(COALESCE(r.body_markdown, ''), 1, 260) AS body_preview,
                   r.primary_book_id, r.tags_json, r.is_pinned, r.is_archived,
                   r.created_at, r.updated_at,
                   b.id AS acc_book_id, b.title AS acc_book_title, b.format AS acc_book_format
            FROM reflections r
            LEFT JOIN books b ON b.id = r.primary_book_id AND b.is_missing = 0
            LEFT JOIN library_sources s ON s.id = b.source_id
            WHERE r.user_id = ? AND r.id IN ({ph});
            """,
            (user_id, *rids),
        )
        for row in cursor.fetchall():
            # Verifica se o livro associado existe e pertence a fonte ativa
            b_avail = False
            if row["primary_book_id"] and row["acc_book_id"]:
                b_check = _check_book_accessible_row(cursor, row["primary_book_id"])
                b_avail = bool(b_check)
            resolved_map[("reflection", row["id"])] = {
                "type": "reflection",
                "id": row["id"],
                "available": True,
                "status": "available",
                "is_own": True,
                "user_id": int(row["user_id"]),
                "label": row["title"],
                "title": row["title"],
                "subtitle": row["acc_book_title"] if b_avail else "Reflexão autoral",
                "excerpt": _WHITESPACE_RE.sub(" ", (row["body_preview"] or "").strip())[:240],
                "color": None,
                "tags": _parse_tags_json(row["tags_json"]),
                "is_pinned": bool(row["is_pinned"]),
                "is_archived": bool(row["is_archived"]),
                "book_id": row["acc_book_id"] if b_avail else None,
                "book_title": row["acc_book_title"] if b_avail else None,
                "book_format": row["acc_book_format"] if b_avail else None,
                "page": None,
                "epub_cfi": None,
                "chapter_label": None,
                "visibility": "private",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    # 3. Livros acessíveis
    if by_type["book"]:
        bids = sorted(by_type["book"])
        ph = ",".join("?" for _ in bids)
        cursor.execute(
            f"""
            SELECT b.id, b.title, b.author, b.format, b.page_count
            FROM books b
            LEFT JOIN library_sources s ON s.id = b.source_id
            WHERE b.id IN ({ph})
              AND b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1;
            """,
            tuple(bids),
        )
        for row in cursor.fetchall():
            if not _check_book_accessible_row(cursor, row["id"]):
                continue
            resolved_map[("book", row["id"])] = {
                "type": "book",
                "id": row["id"],
                "available": True,
                "status": "available",
                "is_own": False,
                "user_id": None,
                "label": row["title"],
                "title": row["title"],
                "subtitle": row["author"] or "Autor desconhecido",
                "excerpt": row["author"] or "",
                "color": None,
                "tags": [],
                "book_id": row["id"],
                "book_title": row["title"],
                "book_format": row["format"],
                "page": None,
                "epub_cfi": None,
                "chapter_label": None,
                "visibility": "public",
                "created_at": None,
                "updated_at": None,
            }

    # 4. Destaques autorizados (próprios ou públicos em livros acessíveis)
    if by_type["highlight"]:
        hids = sorted(by_type["highlight"])
        ph = ",".join("?" for _ in hids)
        cursor.execute(
            f"""
            SELECT h.id, h.book_id, h.page, h.color, h.selected_text, h.text_context,
                   h.user_id, h.visibility, h.epub_cfi, h.chapter_label,
                   h.created_at, h.updated_at,
                   b.title AS book_title, b.format AS book_format,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM highlights h
            JOIN books b ON b.id = h.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = h.user_id
            LEFT JOIN user_profiles up ON up.user_id = h.user_id
            WHERE h.id IN ({ph})
              AND b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1
              AND (h.user_id = ? OR h.visibility = 'public');
            """,
            (*hids, user_id),
        )
        for row in cursor.fetchall():
            text_prev = _WHITESPACE_RE.sub(" ", (row["selected_text"] or row["text_context"] or "").strip())
            is_own = int(row["user_id"]) == int(user_id)
            resolved_map[("highlight", row["id"])] = {
                "type": "highlight",
                "id": row["id"],
                "available": True,
                "status": "available",
                "is_own": is_own,
                "user_id": int(row["user_id"]),
                "author_display_name": row["author_display_name"],
                "label": (text_prev[:80] + "…") if len(text_prev) > 80 else (text_prev or f"Destaque (p. {row['page']})"),
                "title": row["book_title"],
                "subtitle": f"{row['book_title']} · p. {row['page']}" if row["page"] else row["book_title"],
                "excerpt": text_prev[:240],
                "color": row["color"],
                "tags": [],
                "book_id": row["book_id"],
                "book_title": row["book_title"],
                "book_format": row["book_format"],
                "page": row["page"],
                "epub_cfi": row["epub_cfi"],
                "chapter_label": row["chapter_label"],
                "visibility": row["visibility"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    # 5. Notas autorizadas (próprias ou públicas em livros acessíveis)
    if by_type["note"]:
        nids = sorted(by_type["note"])
        ph = ",".join("?" for _ in nids)
        cursor.execute(
            f"""
            SELECT n.id, n.book_id, n.page, n.color, n.content,
                   n.user_id, n.visibility, n.epub_cfi, n.chapter_label,
                   n.created_at, n.updated_at,
                   b.title AS book_title, b.format AS book_format,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM notes n
            JOIN books b ON b.id = n.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN user_profiles up ON up.user_id = n.user_id
            WHERE n.id IN ({ph})
              AND b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1
              AND (n.user_id = ? OR n.visibility = 'public');
            """,
            (*nids, user_id),
        )
        for row in cursor.fetchall():
            content_prev = _WHITESPACE_RE.sub(" ", (row["content"] or "").strip())
            is_own = int(row["user_id"]) == int(user_id)
            resolved_map[("note", row["id"])] = {
                "type": "note",
                "id": row["id"],
                "available": True,
                "status": "available",
                "is_own": is_own,
                "user_id": int(row["user_id"]),
                "author_display_name": row["author_display_name"],
                "label": (content_prev[:80] + "…") if len(content_prev) > 80 else (content_prev or f"Nota (p. {row['page']})"),
                "title": row["book_title"],
                "subtitle": f"{row['book_title']} · p. {row['page']}" if row["page"] else row["book_title"],
                "excerpt": content_prev[:240],
                "color": row["color"],
                "tags": [],
                "book_id": row["book_id"],
                "book_title": row["book_title"],
                "book_format": row["book_format"],
                "page": row["page"],
                "epub_cfi": row["epub_cfi"],
                "chapter_label": row["chapter_label"],
                "visibility": row["visibility"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    # Preenche sentinelas neutros para qualquer chave não disponível
    for key in unique_keys:
        if key not in resolved_map:
            resolved_map[key] = _unavailable_entity_sentinel(key[0], key[1])

    return resolved_map


def _format_graph_node(
    entity: Dict[str, Any],
    *,
    hop: int,
    is_center: bool,
    degree_in_subgraph: int,
) -> Dict[str, Any]:
    """
    Formata um nó leve para o payload do grafo local (Seção 26),
    garantindo que referências indisponíveis jamais exponham metadados protegidos
    e tenham `expandable=False` e `navigable=False`.
    """
    etype = entity["type"]
    eid = entity["id"]
    node_key = f"{etype}:{eid}"
    available = bool(entity.get("available"))
    if not available:
        return {
            "key": node_key,
            "type": etype,
            "id": eid,
            "type_label": ENTITY_TYPE_LABELS_PT.get(etype, etype),
            "type_prefix": "[Indisponível]",
            "available": False,
            "status": "unavailable",
            "label": "Referência indisponível",
            "short_label": "Referência indisponível",
            "title": None,
            "subtitle": "Item removido ou sem permissão de acesso",
            "excerpt": None,
            "color": None,
            "tags": [],
            "book_id": None,
            "book_title": None,
            "book_format": None,
            "page": None,
            "epub_cfi": None,
            "chapter_label": None,
            "is_own": None,
            "visibility": None,
            "hop": hop,
            "is_center": is_center,
            "degree": degree_in_subgraph,
            "expandable": False,
            "navigable": False,
        }

    raw_label = str(entity.get("label") or entity.get("title") or eid).strip()
    short_label = (raw_label[:34].rstrip() + "…") if len(raw_label) > 35 else raw_label
    return {
        "key": node_key,
        "type": etype,
        "id": eid,
        "type_label": ENTITY_TYPE_LABELS_PT.get(etype, etype),
        "type_prefix": ENTITY_TYPE_PREFIXES_PT.get(etype, ""),
        "available": True,
        "status": "available",
        "label": raw_label,
        "short_label": short_label,
        "title": entity.get("title") or raw_label,
        "subtitle": entity.get("subtitle") or ENTITY_TYPE_LABELS_PT.get(etype, etype),
        "excerpt": (entity.get("excerpt") or "")[:240],
        "color": entity.get("color"),
        "tags": (entity.get("tags") or [])[:10],
        "book_id": entity.get("book_id"),
        "book_title": entity.get("book_title"),
        "book_format": entity.get("book_format"),
        "page": entity.get("page"),
        "epub_cfi": entity.get("epub_cfi"),
        "chapter_label": entity.get("chapter_label"),
        "is_own": entity.get("is_own"),
        "visibility": entity.get("visibility"),
        "hop": hop,
        "is_center": is_center,
        "degree": degree_in_subgraph,
        "expandable": True,
        "navigable": True,
    }


def get_knowledge_graph(
    user_id: int,
    entity_type: str,
    entity_id: str,
    *,
    depth: int = DEFAULT_GRAPH_DEPTH,
    max_nodes: int = DEFAULT_GRAPH_MAX_NODES,
    max_edges: int = DEFAULT_GRAPH_MAX_EDGES,
    entity_types: Optional[Any] = None,
    relation_types: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Constrói o subgrafo local de conhecimento centrado em `(entity_type, entity_id)`
    com profundidade limitada a 1 ou 2 saltos (`GET /api/knowledge/graph` — KD-006, Seções 23–35).

    Regras estritas de segurança, limites e determinismo:
    1. Valida e autoriza primeiramente a entidade central (`for_link_endpoint=False`), levantando
       `KnowledgeNotFoundError` (HTTP 404 anti-enumeração) se não existir ou não estiver acessível.
    2. Impõe limites rígidos:
       - `depth in (1, 2)`
       - `1 <= max_nodes <= HARD_MAX_GRAPH_NODES (80)` (incluindo o nó central)
       - `1 <= max_edges <= HARD_MAX_GRAPH_EDGES (160)`
    3. Executa busca em largura (BFS) em lote (zero N+1), deduplicando nós por `(type, id)`
       e arestas por `knowledge_links.id` (sem duplicar arestas inversas nem entrar em loop em ciclos).
    4. Referências indisponíveis (`available=False`):
       - São representadas por sentinelas neutros (`label='Referência indisponível'`), sem metadados protegidos;
       - Nunca podem ser expandidas para o 2º salto (`expandable=False`).
    5. Quando o número de nós ou conexões elegíveis excede `max_nodes` ou `max_edges`, sinaliza
       `truncated=True` e `truncation_reasons` de forma determinística.
    6. Gera também `accessible_text_list`, representando exatamente o mesmo subconjunto autorizado
       do subgrafo para navegação acessível por teclado e leitores de tela (Seção 32).
    """
    center_type = _validate_entity_type(entity_type)
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise KnowledgeValidationError("Identificador da entidade central inválido.")
    center_id = normalize_unicode_nfc(entity_id).strip()

    try:
        d_val = int(DEFAULT_GRAPH_DEPTH if depth is None else depth)
    except (TypeError, ValueError):
        raise KnowledgeValidationError("O parâmetro 'depth' deve ser 1 ou 2.")
    if d_val not in (1, 2):
        raise KnowledgeValidationError("O parâmetro 'depth' do mapa local deve ser 1 ou 2.")

    try:
        m_nodes = int(DEFAULT_GRAPH_MAX_NODES if max_nodes is None else max_nodes)
        m_edges = int(DEFAULT_GRAPH_MAX_EDGES if max_edges is None else max_edges)
    except (TypeError, ValueError):
        raise KnowledgeValidationError("Os parâmetros 'max_nodes' e 'max_edges' devem ser inteiros.")

    if m_nodes < 1 or m_nodes > HARD_MAX_GRAPH_NODES:
        raise KnowledgeValidationError(
            f"O parâmetro 'max_nodes' deve estar entre 1 e {HARD_MAX_GRAPH_NODES}."
        )
    if m_edges < 1 or m_edges > HARD_MAX_GRAPH_EDGES:
        raise KnowledgeValidationError(
            f"O parâmetro 'max_edges' deve estar entre 1 e {HARD_MAX_GRAPH_EDGES}."
        )

    allowed_entity_types = _parse_graph_entity_types(entity_types)
    allowed_relation_types = _parse_graph_relation_types(relation_types)

    with get_db() as conn:
        cursor = conn.cursor()
        _verify_active_user(cursor, user_id)

        # 1. Valida e autoriza a entidade central (HTTP 404 se não autorizada/inexistente)
        center_resolved = resolve_knowledge_entity(
            center_type,
            center_id,
            user_id,
            cursor=cursor,
            for_link_endpoint=False,
        )
        # Enriquece subtitle da entidade central caso venha de resolve_knowledge_entity
        center_batch = _batch_resolve_graph_entities(cursor, user_id, [(center_type, center_id)])
        center_entity = center_batch.get((center_type, center_id), center_resolved)

        center_key = (center_type, center_id)
        node_hop: Dict[Tuple[str, str], int] = {center_key: 0}
        selected_link_rows: Dict[str, sqlite3.Row] = {}
        edge_hop: Dict[str, int] = {}

        truncated = False
        truncation_reasons: List[str] = []

        rel_sorted = sorted(allowed_relation_types)
        rel_ph = ",".join("?" for _ in rel_sorted)

        # --- SALTO 1 (Hop 1 — UNION ALL sobre índices compostos de source e target) ---
        cursor.execute(
            f"""
            SELECT * FROM (
                SELECT *
                FROM knowledge_links
                WHERE user_id = ?
                  AND source_type = ?
                  AND source_id = ?
                  AND relation_type IN ({rel_ph})
                UNION ALL
                SELECT *
                FROM knowledge_links
                WHERE user_id = ?
                  AND target_type = ?
                  AND target_id = ?
                  AND relation_type IN ({rel_ph})
            )
            ORDER BY created_at DESC, id ASC;
            """,
            (
                user_id, center_type, center_id, *rel_sorted,
                user_id, center_type, center_id, *rel_sorted,
            ),
        )
        hop1_links = cursor.fetchall()

        hop1_neighbor_keys: List[Tuple[str, str]] = []
        for lrow in hop1_links:
            lid = lrow["id"]
            s_key = (lrow["source_type"], lrow["source_id"])
            t_key = (lrow["target_type"], lrow["target_id"])
            other_key = t_key if s_key == center_key else s_key

            # Filtra pelo tipo do vizinho (o nó central está sempre presente)
            if other_key != center_key and other_key[0] not in allowed_entity_types:
                continue

            needs_new_node = other_key not in node_hop
            if needs_new_node and len(node_hop) >= m_nodes:
                truncated = True
                if "max_nodes" not in truncation_reasons:
                    truncation_reasons.append("max_nodes")
                continue

            if len(selected_link_rows) >= m_edges:
                truncated = True
                if "max_edges" not in truncation_reasons:
                    truncation_reasons.append("max_edges")
                continue

            if needs_new_node:
                node_hop[other_key] = 1
                hop1_neighbor_keys.append(other_key)

            selected_link_rows[lid] = lrow
            edge_hop[lid] = 1

        # Resolve em lote todos os nós de 1º salto para saber quais estão disponíveis (`available=True`)
        resolved_entities = _batch_resolve_graph_entities(
            cursor,
            user_id,
            list(node_hop.keys()),
        )
        resolved_entities[center_key] = center_entity

        # --- SALTO 2 (Hop 2 — somente a partir de vizinhos de 1º salto disponíveis) ---
        if d_val == 2 and hop1_neighbor_keys:
            expandable_hop1 = [
                k for k in hop1_neighbor_keys
                if resolved_entities.get(k, {}).get("available") is True
            ]
            if expandable_hop1:
                by_h1_type: Dict[str, List[str]] = {}
                for et, eid in expandable_hop1:
                    by_h1_type.setdefault(et, []).append(eid)

                union_branches: List[str] = []
                q_params: List[Any] = []
                for et, eids in sorted(by_h1_type.items()):
                    for chunk in _chunked(eids, 400):
                        ph = ",".join("?" for _ in chunk)
                        union_branches.append(
                            f"""
                            SELECT * FROM knowledge_links
                            WHERE user_id = ?
                              AND source_type = ?
                              AND source_id IN ({ph})
                              AND relation_type IN ({rel_ph})
                            """
                        )
                        q_params.extend([user_id, et, *chunk, *rel_sorted])
                        union_branches.append(
                            f"""
                            SELECT * FROM knowledge_links
                            WHERE user_id = ?
                              AND target_type = ?
                              AND target_id IN ({ph})
                              AND relation_type IN ({rel_ph})
                            """
                        )
                        q_params.extend([user_id, et, *chunk, *rel_sorted])

                cursor.execute(
                    f"""
                    SELECT * FROM (
                        {' UNION '.join(union_branches)}
                    )
                    ORDER BY created_at DESC, id ASC;
                    """,
                    tuple(q_params),
                )
                hop2_candidates = cursor.fetchall()

                expandable_set = set(expandable_hop1)
                new_hop2_keys: List[Tuple[str, str]] = []

                for lrow in hop2_candidates:
                    lid = lrow["id"]
                    if lid in selected_link_rows:
                        continue
                    s_key = (lrow["source_type"], lrow["source_id"])
                    t_key = (lrow["target_type"], lrow["target_id"])

                    # Ao menos um extremo deve ser um nó de 1º salto disponível (`expandable_set`)
                    if s_key in expandable_set:
                        other_key = t_key
                    elif t_key in expandable_set:
                        other_key = s_key
                    else:
                        continue

                    if other_key != center_key and other_key[0] not in allowed_entity_types:
                        continue

                    # Se o outro extremo já está no grafo e é indisponível, não expandimos através dele
                    if other_key in resolved_entities and not resolved_entities[other_key].get("available"):
                        continue

                    needs_new_node = other_key not in node_hop
                    if needs_new_node and len(node_hop) >= m_nodes:
                        truncated = True
                        if "max_nodes" not in truncation_reasons:
                            truncation_reasons.append("max_nodes")
                        continue

                    if len(selected_link_rows) >= m_edges:
                        truncated = True
                        if "max_edges" not in truncation_reasons:
                            truncation_reasons.append("max_edges")
                        continue

                    if needs_new_node:
                        node_hop[other_key] = 2
                        new_hop2_keys.append(other_key)

                    selected_link_rows[lid] = lrow
                    edge_hop[lid] = 2

                if new_hop2_keys:
                    hop2_resolved = _batch_resolve_graph_entities(cursor, user_id, new_hop2_keys)
                    resolved_entities.update(hop2_resolved)

        # Calcula grau de cada nó no subgrafo selecionado
        degrees: Dict[Tuple[str, str], int] = {k: 0 for k in node_hop}
        for lid, lrow in selected_link_rows.items():
            sk = (lrow["source_type"], lrow["source_id"])
            tk = (lrow["target_type"], lrow["target_id"])
            if sk in degrees:
                degrees[sk] += 1
            if tk in degrees:
                degrees[tk] += 1

        # Formata nós em ordem determinística: centro primeiro (hop 0), depois hop 1, depois hop 2,
        # desempate por tipo, rótulo e id.
        formatted_nodes: List[Dict[str, Any]] = []
        nodes_by_key_str: Dict[str, Dict[str, Any]] = {}

        for n_key, hop_val in node_hop.items():
            ent = resolved_entities.get(n_key) or _unavailable_entity_sentinel(n_key[0], n_key[1])
            node_obj = _format_graph_node(
                ent,
                hop=hop_val,
                is_center=(n_key == center_key),
                degree_in_subgraph=degrees.get(n_key, 0),
            )
            formatted_nodes.append(node_obj)
            nodes_by_key_str[node_obj["key"]] = node_obj

        formatted_nodes.sort(
            key=lambda n: (
                0 if n["is_center"] else 1,
                n["hop"],
                0 if n["available"] else 1,
                n["type"],
                (n["label"] or "").lower(),
                n["id"],
            )
        )

        # Formata arestas direcionadas em ordem determinística
        formatted_edges: List[Dict[str, Any]] = []
        for lid, lrow in selected_link_rows.items():
            rel_type = lrow["relation_type"]
            rel_labels = RELATION_LABELS_PT.get(rel_type, RELATION_LABELS_PT["relates_to"])
            src_key_str = f"{lrow['source_type']}:{lrow['source_id']}"
            tgt_key_str = f"{lrow['target_type']}:{lrow['target_id']}"
            src_node = nodes_by_key_str.get(src_key_str)
            tgt_node = nodes_by_key_str.get(tgt_key_str)
            rationale_clean = (lrow["rationale"] or "").strip()
            formatted_edges.append(
                {
                    "id": lid,
                    "source_key": src_key_str,
                    "source_type": lrow["source_type"],
                    "source_id": lrow["source_id"],
                    "source_label": src_node["label"] if src_node else "Item",
                    "source_available": bool(src_node["available"]) if src_node else False,
                    "target_key": tgt_key_str,
                    "target_type": lrow["target_type"],
                    "target_id": lrow["target_id"],
                    "target_label": tgt_node["label"] if tgt_node else "Item",
                    "target_available": bool(tgt_node["available"]) if tgt_node else False,
                    "relation_type": rel_type,
                    "relation_label": rel_labels["outgoing"],
                    "outgoing_label": rel_labels["outgoing"],
                    "incoming_label": rel_labels["incoming"],
                    "rationale": rationale_clean[:280],
                    "hop": edge_hop.get(lid, 1),
                    "created_at": lrow["created_at"],
                    "updated_at": lrow["updated_at"],
                }
            )

        formatted_edges.sort(key=lambda e: (e["hop"], e["relation_type"], e["id"]))

        # Constrói a Alternativa Textual Acessível obrigatória (Seção 32),
        # representando exatamente o mesmo subconjunto autorizado de nós e arestas
        accessible_text_list: List[Dict[str, Any]] = []
        center_key_str = f"{center_type}:{center_id}"
        for e in formatted_edges:
            if e["source_key"] == center_key_str:
                direction_rel = "outgoing"
                anchor_key = center_key_str
                counterpart_node = nodes_by_key_str.get(e["target_key"])
                rel_display = e["outgoing_label"]
            elif e["target_key"] == center_key_str:
                direction_rel = "incoming"
                anchor_key = center_key_str
                counterpart_node = nodes_by_key_str.get(e["source_key"])
                rel_display = e["incoming_label"]
            else:
                direction_rel = "indirect"
                anchor_key = e["source_key"]
                counterpart_node = nodes_by_key_str.get(e["target_key"])
                rel_display = e["outgoing_label"]

            src_node = nodes_by_key_str.get(e["source_key"])
            tgt_node = nodes_by_key_str.get(e["target_key"])
            accessible_text_list.append(
                {
                    "edge_id": e["id"],
                    "hop": e["hop"],
                    "direction": direction_rel,
                    "relation_type": e["relation_type"],
                    "relation_label": rel_display,
                    "rationale": e["rationale"],
                    "anchor_key": anchor_key,
                    "source": {
                        "key": e["source_key"],
                        "type": e["source_type"],
                        "id": e["source_id"],
                        "type_label": src_node["type_label"] if src_node else e["source_type"],
                        "type_prefix": src_node["type_prefix"] if src_node else "",
                        "label": src_node["label"] if src_node else "Referência indisponível",
                        "available": bool(src_node["available"]) if src_node else False,
                        "book_id": src_node.get("book_id") if (src_node and src_node["available"]) else None,
                        "book_title": src_node.get("book_title") if (src_node and src_node["available"]) else None,
                        "page": src_node.get("page") if (src_node and src_node["available"]) else None,
                        "epub_cfi": src_node.get("epub_cfi") if (src_node and src_node["available"]) else None,
                    },
                    "target": {
                        "key": e["target_key"],
                        "type": e["target_type"],
                        "id": e["target_id"],
                        "type_label": tgt_node["type_label"] if tgt_node else e["target_type"],
                        "type_prefix": tgt_node["type_prefix"] if tgt_node else "",
                        "label": tgt_node["label"] if tgt_node else "Referência indisponível",
                        "available": bool(tgt_node["available"]) if tgt_node else False,
                        "book_id": tgt_node.get("book_id") if (tgt_node and tgt_node["available"]) else None,
                        "book_title": tgt_node.get("book_title") if (tgt_node and tgt_node["available"]) else None,
                        "page": tgt_node.get("page") if (tgt_node and tgt_node["available"]) else None,
                        "epub_cfi": tgt_node.get("epub_cfi") if (tgt_node and tgt_node["available"]) else None,
                    },
                    "counterpart_key": counterpart_node["key"] if counterpart_node else None,
                }
            )

        center_node_obj = nodes_by_key_str[center_key_str]
        return {
            "center": center_node_obj,
            "nodes": formatted_nodes,
            "edges": formatted_edges,
            "accessible_text_list": accessible_text_list,
            "node_count": len(formatted_nodes),
            "edge_count": len(formatted_edges),
            "depth": d_val,
            "max_nodes": m_nodes,
            "max_edges": m_edges,
            "truncated": truncated,
            "truncation_reasons": truncation_reasons,
            "entity_types": sorted(allowed_entity_types),
            "relation_types": sorted(allowed_relation_types),
        }

