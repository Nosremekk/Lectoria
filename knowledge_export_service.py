"""
Serviço de Portabilidade e Exportação do Atlas (`knowledge_export_service.py` — Lectoria v1.9.0, Fase 7).

Implementa a exportação portátil, verificável e segura do conhecimento pessoal do usuário autenticado:
- Formatos suportados (`KD-009`):
  1. `markdown_zip`: pacote ZIP com `indice.md`, `manifest.json` (com checksums SHA-256),
     `conceitos/<uuid>.md`, `reflexoes/<uuid>.md`, `livros/<uuid>.md` e `anotacoes/<uuid>.md`,
     com frontmatter YAML seguro, links relativos multiplataforma e opção de wikilinks `[[...]]`.
  2. `json`: envelope JSON estruturado v2.0 (`format: "lectoria-atlas"`, `format_version: "2.0"`).
  3. `csv`: pacote ZIP tabular (`concepts.csv`, `reflections.csv`, `links.csv`, `books.csv`,
     `annotations.csv` e `manifest.json`) com proteção contra CSV Formula Injection.

Garantias de segurança, privacidade e recursos:
- Autorização estrita em tempo de leitura sob a identidade autenticada (`user_id` da sessão).
- Snapshot transacional consistente no SQLite sem manter transações longas abertas durante I/O.
- Proteção rigorosa de referências indisponíveis (sentinelas neutros sem metadados nem IDs protegidos).
- Política conservadora para anotações públicas de terceiros referenciadas: apenas metadados
  estruturados mínimos, sem redistribuição integral de conteúdo de terceiros.
- Proteção contra Zip Slip, colisões de nomes, caminhos absolutos e links simbólicos.
- Streaming via arquivo temporário em disco e trava de concorrência limitada para baixo uso de RAM.
"""

from datetime import datetime, timezone
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import zipfile

import config
from database import get_db
from knowledge_service import (
    ENTITY_TYPE_LABELS_PT,
    RELATION_LABELS_PT,
    KnowledgeError,
    KnowledgeValidationError,
    _check_book_accessible_row,
    _parse_tags_json,
    _verify_active_user,
)


VALID_EXPORT_FORMATS: Tuple[str, ...] = ("markdown_zip", "json", "csv")

EXPORT_FORMAT_VERSION = "2.0"
EXPORT_ROOT_DIR = "lectoria-atlas-export"

MAX_EXPORT_CONCEPTS = 50000
MAX_EXPORT_REFLECTIONS = 20000
MAX_EXPORT_LINKS = 100000
MAX_EXPORT_ANNOTATIONS = 100000
MAX_EXPORT_BOOKS = 20000

_EXPORT_CONCURRENCY_LIMIT = 4
_EXPORT_SEMAPHORE = threading.BoundedSemaphore(_EXPORT_CONCURRENCY_LIMIT)
_SAFE_UUID_FILENAME_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


class KnowledgeExportBusyError(KnowledgeError):
    """Muitas exportações simultâneas em andamento (HTTP 503)."""
    status_code: int = 503


class KnowledgeExportLimitError(KnowledgeError):
    """Volume de dados excede o teto seguro de exportação (HTTP 413/400)."""
    status_code: int = 400


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunked(seq: Sequence[Any], size: int = 500):
    """Divide uma sequência em blocos seguros contra SQLITE_MAX_VARIABLE_NUMBER."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _safe_filename_stem(raw_id: str) -> str:
    """
    Garante um nome de arquivo seguro e portátil baseado no UUID/ID canônico,
    bloqueando qualquer travessia de caminho (`..`, `/`, `\\`, bytes nulos).
    """
    cleaned = str(raw_id or "").strip()
    if not cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned or "\x00" in cleaned:
        return hashlib.sha256(cleaned.encode("utf-8", errors="replace")).hexdigest()[:24]
    if _SAFE_UUID_FILENAME_RE.match(cleaned):
        return cleaned
    return hashlib.sha256(cleaned.encode("utf-8", errors="replace")).hexdigest()[:24]


def _safe_zip_entry_path(relative_path: str, seen_paths: Optional[Set[str]] = None) -> str:
    """
    Valida e normaliza um caminho interno de entrada ZIP contra Zip Slip,
    caminhos absolutos, separadores inválidos e entradas duplicadas.
    """
    norm = str(relative_path or "").replace("\\", "/").strip()
    if not norm or norm.startswith("/") or "\x00" in norm:
        raise KnowledgeValidationError("Caminho interno ZIP inválido.")
    parts = norm.split("/")
    for part in parts:
        if not part or part in (".", "..") or ":" in part:
            raise KnowledgeValidationError("Segmento de caminho ZIP inseguro detectado.")
    if len(norm) > 240:
        raise KnowledgeValidationError("Caminho interno ZIP excede o comprimento máximo portátil.")
    if seen_paths is not None:
        if norm in seen_paths:
            raise KnowledgeValidationError(f"Entrada ZIP duplicada detectada: {norm}")
        seen_paths.add(norm)
    return norm


def _yaml_quote_scalar(value: Any) -> str:
    """Serializa um valor escalar para YAML seguro com aspas duplas e escape completo."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _dump_yaml_frontmatter(fields: List[Tuple[str, Any]]) -> str:
    """Gera frontmatter YAML determinístico e seguro contra injeção de delimitadores `---`."""
    lines = ["---"]
    for key, val in fields:
        if isinstance(val, (list, tuple)):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in val:
                    lines.append(f"  - {_yaml_quote_scalar(item)}")
        else:
            lines.append(f"{key}: {_yaml_quote_scalar(val)}")
    lines.append("---")
    return "\n".join(lines)


def _sanitize_csv_cell(value: Any) -> str:
    """
    Serializa e protege células CSV contra CSV Formula Injection (DDE / Excel / Calc).
    Neutraliza apenas a representação tabular sem modificar dados canônicos no banco.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text:
        return ""
    stripped_leading = text.lstrip(" \t\r\n\x00\x0b\x0c")
    if text[0] in _CSV_FORMULA_PREFIXES or (stripped_leading and stripped_leading[0] in _CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def _build_md_link(label: str, rel_path: str, *, wikilinks: bool = False, wikilink_target: Optional[str] = None) -> str:
    clean_label = str(label or "Item").replace("[", "(").replace("]", ")").replace("\n", " ").strip()
    std_link = f"[{clean_label}]({rel_path})"
    if wikilinks and wikilink_target:
        safe_wiki = str(wikilink_target).replace("[[", "").replace("]]", "").replace("|", "-").strip()
        return f"{std_link} (`[[{safe_wiki}]]`)"
    return std_link


def build_atlas_export_snapshot(
    user_id: int,
    *,
    include_archived: bool = True,
) -> Dict[str, Any]:
    """
    Constrói uma representação intermediária canônica e transacionalmente consistente
    de todos os dados autorizados do Atlas para o usuário autenticado.

    Regras de autorização e privacidade:
    - Conceitos, reflexões e conexões: estritamente do próprio `user_id`.
    - Livros: validados em tempo de leitura (`is_missing = 0` e fonte `is_active = 1`),
      incluindo apenas obras efetivamente referenciadas pelo conjunto exportável.
    - Anotações (`highlight`, `note`):
      - Próprias (`user_id == user_id`) em livros acessíveis: exportadas com conteúdo próprio.
      - Públicas de terceiros (`visibility == 'public'`) referenciadas por `knowledge_links`:
        exportadas apenas com metadados estruturais mínimos (sem copiar texto integral de terceiro).
      - Privadas ou revogadas de terceiros, ou em livros/fontes inativas: nunca incluídas;
        seus vínculos apontam para sentinela neutro (`available: False`, `entity_id: None`).
    """
    exported_at = _now_utc_iso()

    with get_db() as conn:
        cursor = conn.cursor()
        # Inicia leitura consistente (snapshot SQLite) por curto intervalo de tempo
        _verify_active_user(cursor, user_id)

        # 1. Conceitos do usuário
        c_sql = """
            SELECT id, user_id, name, normalized_name, description, tags_json, color,
                   is_archived, created_at, updated_at
            FROM concepts
            WHERE user_id = ?
        """
        if not include_archived:
            c_sql += " AND is_archived = 0"
        c_sql += " ORDER BY normalized_name ASC, id ASC LIMIT ?;"
        cursor.execute(c_sql, (user_id, MAX_EXPORT_CONCEPTS + 1))
        c_rows = cursor.fetchall()
        if len(c_rows) > MAX_EXPORT_CONCEPTS:
            raise KnowledgeExportLimitError(
                f"A exportação excede o limite de {MAX_EXPORT_CONCEPTS} conceitos."
            )

        concepts: List[Dict[str, Any]] = []
        concepts_by_id: Dict[str, Dict[str, Any]] = {}
        for r in c_rows:
            c_obj = {
                "id": r["id"],
                "type": "concept",
                "name": r["name"],
                "normalized_name": r["normalized_name"],
                "description": r["description"] or "",
                "tags": _parse_tags_json(r["tags_json"]),
                "color": r["color"] or "#d4a853",
                "is_archived": bool(r["is_archived"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            concepts.append(c_obj)
            concepts_by_id[r["id"]] = c_obj

        # 2. Reflexões do usuário
        r_sql = """
            SELECT id, user_id, title, body_markdown, primary_book_id, tags_json,
                   is_pinned, is_archived, created_at, updated_at
            FROM reflections
            WHERE user_id = ?
        """
        if not include_archived:
            r_sql += " AND is_archived = 0"
        r_sql += " ORDER BY is_pinned DESC, updated_at DESC, id ASC LIMIT ?;"
        cursor.execute(r_sql, (user_id, MAX_EXPORT_REFLECTIONS + 1))
        r_rows = cursor.fetchall()
        if len(r_rows) > MAX_EXPORT_REFLECTIONS:
            raise KnowledgeExportLimitError(
                f"A exportação excede o limite de {MAX_EXPORT_REFLECTIONS} reflexões."
            )

        # 3. Conexões pessoais do usuário
        cursor.execute(
            """
            SELECT id, user_id, source_type, source_id, target_type, target_id,
                   relation_type, rationale, created_at, updated_at
            FROM knowledge_links
            WHERE user_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?;
            """,
            (user_id, MAX_EXPORT_LINKS + 1),
        )
        l_rows = cursor.fetchall()
        if len(l_rows) > MAX_EXPORT_LINKS:
            raise KnowledgeExportLimitError(
                f"A exportação excede o limite de {MAX_EXPORT_LINKS} conexões."
            )

        # Coleta IDs de livros, destaques e notas referenciados para resolução em lote (zero N+1)
        referenced_book_ids: Set[str] = set()
        referenced_highlight_ids: Set[str] = set()
        referenced_note_ids: Set[str] = set()

        for rr in r_rows:
            if rr["primary_book_id"]:
                referenced_book_ids.add(rr["primary_book_id"])

        for lr in l_rows:
            for side_t, side_id in (
                (lr["source_type"], lr["source_id"]),
                (lr["target_type"], lr["target_id"]),
            ):
                if side_t == "book":
                    referenced_book_ids.add(side_id)
                elif side_t == "highlight":
                    referenced_highlight_ids.add(side_id)
                elif side_t == "note":
                    referenced_note_ids.add(side_id)

        # 4. Anotações próprias em livros acessíveis + anotações públicas de terceiros referenciadas
        highlights_by_id: Dict[str, Dict[str, Any]] = {}
        cursor.execute(
            """
            SELECT h.id, h.book_id, h.user_id, h.page, h.selected_text, h.text_context,
                   h.color, h.visibility, h.epub_cfi, h.chapter_label, h.created_at, h.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM highlights h
            JOIN books b ON b.id = h.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = h.user_id
            LEFT JOIN user_profiles up ON up.user_id = h.user_id
            WHERE h.user_id = ?
              AND b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1
            ORDER BY h.book_id ASC, h.page ASC, h.created_at ASC, h.id ASC;
            """,
            (user_id,),
        )
        for hr in cursor.fetchall():
            referenced_book_ids.add(hr["book_id"])
            highlights_by_id[hr["id"]] = {
                "id": hr["id"],
                "type": "highlight",
                "book_id": hr["book_id"],
                "is_own": True,
                "visibility": hr["visibility"],
                "page": hr["page"],
                "epub_cfi": hr["epub_cfi"],
                "chapter_label": hr["chapter_label"],
                "color": hr["color"],
                "text": (hr["selected_text"] or "").strip(),
                "text_context": (hr["text_context"] or "").strip() or None,
                "content_omitted": False,
                "author_display_name": hr["author_display_name"],
                "created_at": hr["created_at"],
                "updated_at": hr["updated_at"],
            }

        missing_hl_ids = sorted(hid for hid in referenced_highlight_ids if hid not in highlights_by_id)
        if missing_hl_ids:
            for chunk in _chunked(missing_hl_ids, 500):
                ph = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT h.id, h.book_id, h.user_id, h.page, h.color, h.visibility,
                           h.epub_cfi, h.chapter_label, h.created_at, h.updated_at,
                           COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
                    FROM highlights h
                    JOIN books b ON b.id = h.book_id
                    LEFT JOIN library_sources s ON s.id = b.source_id
                    LEFT JOIN users u ON u.id = h.user_id
                    LEFT JOIN user_profiles up ON up.user_id = h.user_id
                    WHERE h.id IN ({ph})
                      AND h.visibility = 'public'
                      AND b.is_missing = 0
                      AND COALESCE(s.is_active, 1) = 1;
                    """,
                    tuple(chunk),
                )
                for hr in cursor.fetchall():
                    referenced_book_ids.add(hr["book_id"])
                    # Política conservadora: terceiro público exporta apenas referência estruturada mínima
                    highlights_by_id[hr["id"]] = {
                        "id": hr["id"],
                        "type": "highlight",
                        "book_id": hr["book_id"],
                        "is_own": False,
                        "visibility": "public",
                        "page": hr["page"],
                        "epub_cfi": hr["epub_cfi"],
                        "chapter_label": hr["chapter_label"],
                        "color": hr["color"],
                        "text": None,
                        "text_context": None,
                        "content_omitted": True,
                        "omission_reason": "third_party_public_reference_only",
                        "author_display_name": hr["author_display_name"],
                        "created_at": hr["created_at"],
                        "updated_at": hr["updated_at"],
                    }

        notes_by_id: Dict[str, Dict[str, Any]] = {}
        cursor.execute(
            """
            SELECT n.id, n.book_id, n.user_id, n.page, n.highlight_id, n.content,
                   n.color, n.visibility, n.epub_cfi, n.chapter_label, n.created_at, n.updated_at,
                   COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
            FROM notes n
            JOIN books b ON b.id = n.book_id
            LEFT JOIN library_sources s ON s.id = b.source_id
            LEFT JOIN users u ON u.id = n.user_id
            LEFT JOIN user_profiles up ON up.user_id = n.user_id
            WHERE n.user_id = ?
              AND b.is_missing = 0
              AND COALESCE(s.is_active, 1) = 1
            ORDER BY n.book_id ASC, n.page ASC, n.created_at ASC, n.id ASC;
            """,
            (user_id,),
        )
        for nr in cursor.fetchall():
            referenced_book_ids.add(nr["book_id"])
            notes_by_id[nr["id"]] = {
                "id": nr["id"],
                "type": "note",
                "book_id": nr["book_id"],
                "highlight_id": nr["highlight_id"] if nr["highlight_id"] in highlights_by_id else None,
                "is_own": True,
                "visibility": nr["visibility"],
                "page": nr["page"],
                "epub_cfi": nr["epub_cfi"],
                "chapter_label": nr["chapter_label"],
                "color": nr["color"],
                "text": (nr["content"] or "").strip(),
                "content_omitted": False,
                "author_display_name": nr["author_display_name"],
                "created_at": nr["created_at"],
                "updated_at": nr["updated_at"],
            }

        missing_nt_ids = sorted(nid for nid in referenced_note_ids if nid not in notes_by_id)
        if missing_nt_ids:
            for chunk in _chunked(missing_nt_ids, 500):
                ph = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT n.id, n.book_id, n.user_id, n.page, n.color, n.visibility,
                           n.epub_cfi, n.chapter_label, n.created_at, n.updated_at,
                           COALESCE(NULLIF(TRIM(up.display_name), ''), NULLIF(TRIM(u.display_name), ''), u.username, 'Leitor') AS author_display_name
                    FROM notes n
                    JOIN books b ON b.id = n.book_id
                    LEFT JOIN library_sources s ON s.id = b.source_id
                    LEFT JOIN users u ON u.id = n.user_id
                    LEFT JOIN user_profiles up ON up.user_id = n.user_id
                    WHERE n.id IN ({ph})
                      AND n.visibility = 'public'
                      AND b.is_missing = 0
                      AND COALESCE(s.is_active, 1) = 1;
                    """,
                    tuple(chunk),
                )
                for nr in cursor.fetchall():
                    referenced_book_ids.add(nr["book_id"])
                    notes_by_id[nr["id"]] = {
                        "id": nr["id"],
                        "type": "note",
                        "book_id": nr["book_id"],
                        "highlight_id": None,
                        "is_own": False,
                        "visibility": "public",
                        "page": nr["page"],
                        "epub_cfi": nr["epub_cfi"],
                        "chapter_label": nr["chapter_label"],
                        "color": nr["color"],
                        "text": None,
                        "content_omitted": True,
                        "omission_reason": "third_party_public_reference_only",
                        "author_display_name": nr["author_display_name"],
                        "created_at": nr["created_at"],
                        "updated_at": nr["updated_at"],
                    }

        all_annotations: List[Dict[str, Any]] = list(highlights_by_id.values()) + list(notes_by_id.values())
        if len(all_annotations) > MAX_EXPORT_ANNOTATIONS:
            raise KnowledgeExportLimitError(
                f"A exportação excede o limite de {MAX_EXPORT_ANNOTATIONS} anotações."
            )
        all_annotations.sort(key=lambda a: (a["book_id"], a.get("page") or 0, a["type"], a["id"]))

        # 5. Validação em lote de livros acessíveis efetivamente referenciados (zero N+1)
        books_by_id: Dict[str, Dict[str, Any]] = {}
        if referenced_book_ids:
            sorted_bids = sorted(referenced_book_ids)
            if len(sorted_bids) > MAX_EXPORT_BOOKS:
                raise KnowledgeExportLimitError(
                    f"A exportação excede o limite de {MAX_EXPORT_BOOKS} livros referenciados."
                )
            fetched_book_rows: List[sqlite3.Row] = []
            for chunk in _chunked(sorted_bids, 500):
                ph = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT b.id, b.title, b.author, b.format, b.page_count,
                           b.publisher, b.publish_year, b.isbn13, b.isbn10, b.language
                    FROM books b
                    LEFT JOIN library_sources s ON s.id = b.source_id
                    WHERE b.id IN ({ph})
                      AND b.is_missing = 0
                      AND COALESCE(s.is_active, 1) = 1;
                    """,
                    tuple(chunk),
                )
                fetched_book_rows.extend(cursor.fetchall())
            fetched_book_rows.sort(key=lambda br: ((br["title"] or "").lower(), br["id"]))
            for br in fetched_book_rows:
                books_by_id[br["id"]] = {
                    "id": br["id"],
                    "type": "book",
                    "title": br["title"],
                    "author": br["author"] or None,
                    "format": br["format"],
                    "page_count": br["page_count"],
                    "publisher": br["publisher"] or None,
                    "publish_year": br["publish_year"] or None,
                    "isbn13": br["isbn13"] or None,
                    "isbn10": br["isbn10"] or None,
                    "language": br["language"] or None,
                }

        # Constrói reflexões após saber quais livros estão acessíveis
        reflections: List[Dict[str, Any]] = []
        reflections_by_id: Dict[str, Dict[str, Any]] = {}
        for rr in r_rows:
            pb_id = rr["primary_book_id"]
            pb_book = books_by_id.get(pb_id) if pb_id else None
            r_obj = {
                "id": rr["id"],
                "type": "reflection",
                "title": rr["title"],
                "body_markdown": rr["body_markdown"] or "",
                "primary_book_id": pb_book["id"] if pb_book else None,
                "primary_book_title": pb_book["title"] if pb_book else None,
                "primary_book_available": bool(pb_book) if pb_id else None,
                "tags": _parse_tags_json(rr["tags_json"]),
                "is_pinned": bool(rr["is_pinned"]),
                "is_archived": bool(rr["is_archived"]),
                "created_at": rr["created_at"],
                "updated_at": rr["updated_at"],
            }
            reflections.append(r_obj)
            reflections_by_id[rr["id"]] = r_obj

        # 6. Monta conexões com resolução canônica segura e sentinelas neutros para indisponíveis
        links: List[Dict[str, Any]] = []
        unavailable_endpoints_count = 0

        def _describe_endpoint(etype: str, eid: str) -> Dict[str, Any]:
            if etype == "concept" and eid in concepts_by_id:
                c = concepts_by_id[eid]
                return {"type": "concept", "id": c["id"], "available": True, "label": c["name"]}
            if etype == "reflection" and eid in reflections_by_id:
                rf = reflections_by_id[eid]
                return {"type": "reflection", "id": rf["id"], "available": True, "label": rf["title"]}
            if etype == "book" and eid in books_by_id:
                bk = books_by_id[eid]
                return {"type": "book", "id": bk["id"], "available": True, "label": bk["title"]}
            if etype == "highlight" and eid in highlights_by_id:
                hl = highlights_by_id[eid]
                bk = books_by_id.get(hl["book_id"])
                lbl = (
                    (hl["text"][:80] + "…") if (hl["text"] and len(hl["text"]) > 80)
                    else (hl["text"] or f"Destaque ({bk['title'] if bk else 'Livro'} · p. {hl['page']})")
                )
                return {
                    "type": "highlight",
                    "id": hl["id"],
                    "available": True,
                    "label": lbl,
                    "book_id": hl["book_id"],
                    "page": hl["page"],
                    "epub_cfi": hl["epub_cfi"],
                    "is_own": hl["is_own"],
                }
            if etype == "note" and eid in notes_by_id:
                nt = notes_by_id[eid]
                bk = books_by_id.get(nt["book_id"])
                lbl = (
                    (nt["text"][:80] + "…") if (nt["text"] and len(nt["text"]) > 80)
                    else (nt["text"] or f"Nota ({bk['title'] if bk else 'Livro'} · p. {nt['page']})")
                )
                return {
                    "type": "note",
                    "id": nt["id"],
                    "available": True,
                    "label": lbl,
                    "book_id": nt["book_id"],
                    "page": nt["page"],
                    "epub_cfi": nt["epub_cfi"],
                    "is_own": nt["is_own"],
                }
            # Referência indisponível: nunca expõe identificador protegido nem metadados
            return {
                "type": etype,
                "id": None,
                "available": False,
                "label": "Referência indisponível",
            }

        for lr in l_rows:
            src_desc = _describe_endpoint(lr["source_type"], lr["source_id"])
            tgt_desc = _describe_endpoint(lr["target_type"], lr["target_id"])
            if not src_desc["available"]:
                unavailable_endpoints_count += 1
            if not tgt_desc["available"]:
                unavailable_endpoints_count += 1

            rel_type = lr["relation_type"]
            rel_labels = RELATION_LABELS_PT.get(rel_type, RELATION_LABELS_PT["relates_to"])
            links.append(
                {
                    "id": lr["id"],
                    "source_type": src_desc["type"],
                    "source_id": src_desc["id"],
                    "source_available": src_desc["available"],
                    "source_label": src_desc["label"],
                    "target_type": tgt_desc["type"],
                    "target_id": tgt_desc["id"],
                    "target_available": tgt_desc["available"],
                    "target_label": tgt_desc["label"],
                    "relation_type": rel_type,
                    "outgoing_label": rel_labels["outgoing"],
                    "incoming_label": rel_labels["incoming"],
                    "rationale": lr["rationale"] or "",
                    "created_at": lr["created_at"],
                    "updated_at": lr["updated_at"],
                }
            )

        books = list(books_by_id.values())

    return {
        "format": "lectoria-atlas",
        "format_version": EXPORT_FORMAT_VERSION,
        "app_version": config.APP_VERSION,
        "exported_at": exported_at,
        "include_archived": bool(include_archived),
        "policies": {
            "unavailable_references": "neutral_sentinel_without_protected_metadata",
            "third_party_public_annotations": "minimal_structured_reference_without_full_content",
        },
        "counts": {
            "concepts": len(concepts),
            "reflections": len(reflections),
            "links": len(links),
            "books": len(books),
            "annotations": len(all_annotations),
            "unavailable_link_endpoints": unavailable_endpoints_count,
        },
        "concepts": concepts,
        "reflections": reflections,
        "links": links,
        "books": books,
        "annotations": all_annotations,
    }


def _write_json_export_to_tempfile(snapshot: Dict[str, Any]) -> str:
    """Serializa o snapshot JSON v2.0 diretamente para arquivo temporário em disco."""
    fd, tmp_path = tempfile.mkstemp(prefix="lectoria_atlas_json_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(snapshot, fp, ensure_ascii=False, indent=2)
        return tmp_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_csv_zip_export_to_tempfile(snapshot: Dict[str, Any]) -> str:
    """
    Gera um pacote ZIP contendo arquivos CSV UTF-8 (com BOM para planilhas)
    e `manifest.json`, com proteção estrita contra CSV Formula Injection e Zip Slip.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="lectoria_atlas_csv_", suffix=".zip")
    os.close(fd)
    seen_paths: Set[str] = set()
    manifest_files: List[Dict[str, Any]] = []

    def _add_csv_entry(zf: zipfile.ZipFile, rel_name: str, headers: List[str], rows: List[List[Any]]) -> None:
        entry_path = _safe_zip_entry_path(f"{EXPORT_ROOT_DIR}/{rel_name}", seen_paths)
        csv_fd, csv_tmp = tempfile.mkstemp(prefix="lectoria_csv_part_", suffix=".csv")
        try:
            with os.fdopen(csv_fd, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                writer.writerow([_sanitize_csv_cell(h) for h in headers])
                for row in rows:
                    writer.writerow([_sanitize_csv_cell(col) for col in row])
            digest = hashlib.sha256()
            size_b = 0
            with open(csv_tmp, "rb") as rf:
                for chunk in iter(lambda: rf.read(65536), b""):
                    digest.update(chunk)
                    size_b += len(chunk)
            zf.write(csv_tmp, arcname=entry_path)
            manifest_files.append(
                {
                    "path": entry_path,
                    "sha256": digest.hexdigest(),
                    "size_bytes": size_b,
                }
            )
        finally:
            try:
                os.unlink(csv_tmp)
            except OSError:
                pass

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            _add_csv_entry(
                zf,
                "concepts.csv",
                ["id", "name", "normalized_name", "description", "tags", "color", "is_archived", "created_at", "updated_at"],
                [
                    [
                        c["id"],
                        c["name"],
                        c["normalized_name"],
                        c["description"],
                        "; ".join(c["tags"]),
                        c["color"],
                        c["is_archived"],
                        c["created_at"],
                        c["updated_at"],
                    ]
                    for c in snapshot["concepts"]
                ],
            )
            _add_csv_entry(
                zf,
                "reflections.csv",
                [
                    "id", "title", "body_markdown", "primary_book_id", "primary_book_title",
                    "tags", "is_pinned", "is_archived", "created_at", "updated_at",
                ],
                [
                    [
                        r["id"],
                        r["title"],
                        r["body_markdown"],
                        r["primary_book_id"] or "",
                        r["primary_book_title"] or "",
                        "; ".join(r["tags"]),
                        r["is_pinned"],
                        r["is_archived"],
                        r["created_at"],
                        r["updated_at"],
                    ]
                    for r in snapshot["reflections"]
                ],
            )
            _add_csv_entry(
                zf,
                "links.csv",
                [
                    "id", "source_type", "source_id", "source_available", "source_label",
                    "relation_type", "outgoing_label", "target_type", "target_id",
                    "target_available", "target_label", "rationale", "created_at", "updated_at",
                ],
                [
                    [
                        lnk["id"],
                        lnk["source_type"],
                        lnk["source_id"] or "",
                        lnk["source_available"],
                        lnk["source_label"],
                        lnk["relation_type"],
                        lnk["outgoing_label"],
                        lnk["target_type"],
                        lnk["target_id"] or "",
                        lnk["target_available"],
                        lnk["target_label"],
                        lnk["rationale"],
                        lnk["created_at"],
                        lnk["updated_at"],
                    ]
                    for lnk in snapshot["links"]
                ],
            )
            _add_csv_entry(
                zf,
                "books.csv",
                ["id", "title", "author", "format", "page_count", "publisher", "publish_year", "isbn13", "isbn10", "language"],
                [
                    [
                        b["id"],
                        b["title"],
                        b["author"] or "",
                        b["format"],
                        b["page_count"] if b["page_count"] is not None else "",
                        b["publisher"] or "",
                        b["publish_year"] if b["publish_year"] is not None else "",
                        b["isbn13"] or "",
                        b["isbn10"] or "",
                        b["language"] or "",
                    ]
                    for b in snapshot["books"]
                ],
            )
            _add_csv_entry(
                zf,
                "annotations.csv",
                [
                    "id", "type", "book_id", "is_own", "visibility", "page", "epub_cfi",
                    "chapter_label", "color", "text", "content_omitted", "author_display_name",
                    "created_at", "updated_at",
                ],
                [
                    [
                        a["id"],
                        a["type"],
                        a["book_id"],
                        a["is_own"],
                        a["visibility"],
                        a["page"] if a["page"] is not None else "",
                        a["epub_cfi"] or "",
                        a["chapter_label"] or "",
                        a["color"] or "",
                        a["text"] or "",
                        a["content_omitted"],
                        a["author_display_name"] or "",
                        a["created_at"],
                        a["updated_at"],
                    ]
                    for a in snapshot["annotations"]
                ],
            )

            manifest = {
                "format": "lectoria-atlas-csv-bundle",
                "format_version": EXPORT_FORMAT_VERSION,
                "app_version": snapshot["app_version"],
                "exported_at": snapshot["exported_at"],
                "csv_encoding": "utf-8-sig",
                "formula_injection_protection": "leading_single_quote_neutralization",
                "policies": snapshot["policies"],
                "counts": snapshot["counts"],
                "files": manifest_files,
            }
            m_path = _safe_zip_entry_path(f"{EXPORT_ROOT_DIR}/manifest.json", seen_paths)
            zf.writestr(m_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))

        return tmp_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_markdown_zip_export_to_tempfile(
    snapshot: Dict[str, Any],
    *,
    wikilinks: bool = False,
) -> str:
    """
    Gera o pacote ZIP Markdown interligado (`KD-009`) diretamente em arquivo temporário,
    com frontmatter YAML seguro, caminhos baseados em UUID, links relativos válidos,
    `indice.md` e `manifest.json` com checksums SHA-256.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="lectoria_atlas_md_", suffix=".zip")
    os.close(fd)
    seen_paths: Set[str] = set()
    manifest_files: List[Dict[str, Any]] = []

    # Mapa canônico de caminhos relativos dentro de EXPORT_ROOT_DIR
    entity_rel_paths: Dict[Tuple[str, str], str] = {}
    entity_titles: Dict[Tuple[str, str], str] = {}

    for c in snapshot["concepts"]:
        stem = _safe_filename_stem(c["id"])
        entity_rel_paths[("concept", c["id"])] = f"conceitos/{stem}.md"
        entity_titles[("concept", c["id"])] = c["name"]
    for r in snapshot["reflections"]:
        stem = _safe_filename_stem(r["id"])
        entity_rel_paths[("reflection", r["id"])] = f"reflexoes/{stem}.md"
        entity_titles[("reflection", r["id"])] = r["title"]
    for b in snapshot["books"]:
        stem = _safe_filename_stem(b["id"])
        entity_rel_paths[("book", b["id"])] = f"livros/{stem}.md"
        entity_titles[("book", b["id"])] = b["title"]
    for a in snapshot["annotations"]:
        stem = _safe_filename_stem(a["id"])
        entity_rel_paths[(a["type"], a["id"])] = f"anotacoes/{stem}.md"
        type_pt = ENTITY_TYPE_LABELS_PT.get(a["type"], a["type"])
        entity_titles[(a["type"], a["id"])] = f"{type_pt} ({stem[:8]})"

    # Pré-indexa conexões de saída e entrada por entidade disponível
    outgoing_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    incoming_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for lnk in snapshot["links"]:
        if lnk["source_available"] and lnk["source_id"]:
            outgoing_by_key.setdefault((lnk["source_type"], lnk["source_id"]), []).append(lnk)
        if lnk["target_available"] and lnk["target_id"]:
            incoming_by_key.setdefault((lnk["target_type"], lnk["target_id"]), []).append(lnk)

    def _add_text_entry(zf: zipfile.ZipFile, sub_path: str, content: str) -> None:
        arcname = _safe_zip_entry_path(f"{EXPORT_ROOT_DIR}/{sub_path}", seen_paths)
        raw_bytes = content.encode("utf-8")
        zf.writestr(arcname, raw_bytes)
        manifest_files.append(
            {
                "path": arcname,
                "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "size_bytes": len(raw_bytes),
            }
        )

    def _format_link_line(
        *,
        from_dir: str,
        rel_label: str,
        other_available: bool,
        other_type: str,
        other_id: Optional[str],
        other_label: str,
        rationale: str,
    ) -> str:
        if not other_available or not other_id or (other_type, other_id) not in entity_rel_paths:
            line = f"- **{rel_label}**: Referência indisponível"
        else:
            target_sub = entity_rel_paths[(other_type, other_id)]
            rel_href = f"../{target_sub}" if from_dir else target_sub
            type_lbl = ENTITY_TYPE_LABELS_PT.get(other_type, other_type)
            link_md = _build_md_link(
                other_label,
                rel_href,
                wikilinks=wikilinks,
                wikilink_target=entity_titles.get((other_type, other_id)),
            )
            line = f"- **{rel_label}** ({type_lbl}): {link_md}"
        if rationale.strip():
            line += f" — *Justificativa:* {rationale.strip()}"
        return line

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Conceitos
            for c in snapshot["concepts"]:
                c_key = ("concept", c["id"])
                out_links = outgoing_by_key.get(c_key, [])
                in_links = incoming_by_key.get(c_key, [])
                rel_types = sorted({l["relation_type"] for l in out_links + in_links})
                fm = _dump_yaml_frontmatter([
                    ("id", c["id"]),
                    ("type", "concept"),
                    ("title", c["name"]),
                    ("tags", c["tags"]),
                    ("color", c["color"]),
                    ("is_archived", c["is_archived"]),
                    ("created_at", c["created_at"]),
                    ("updated_at", c["updated_at"]),
                    ("relation_types", rel_types),
                ])
                parts = [fm, "", f"# {c['name']}", ""]
                if c["description"].strip():
                    parts.append(c["description"].strip())
                    parts.append("")
                parts.append("## Conexões de Saída")
                if out_links:
                    for l in out_links:
                        parts.append(
                            _format_link_line(
                                from_dir="conceitos",
                                rel_label=l["outgoing_label"],
                                other_available=l["target_available"],
                                other_type=l["target_type"],
                                other_id=l["target_id"],
                                other_label=l["target_label"],
                                rationale=l["rationale"],
                            )
                        )
                else:
                    parts.append("*Nenhuma conexão de saída.*")
                parts.append("")
                parts.append("## Backlinks Recebidos")
                if in_links:
                    for l in in_links:
                        parts.append(
                            _format_link_line(
                                from_dir="conceitos",
                                rel_label=l["incoming_label"],
                                other_available=l["source_available"],
                                other_type=l["source_type"],
                                other_id=l["source_id"],
                                other_label=l["source_label"],
                                rationale=l["rationale"],
                            )
                        )
                else:
                    parts.append("*Nenhum backlink recebido.*")
                parts.append("")
                _add_text_entry(zf, entity_rel_paths[c_key], "\n".join(parts))

            # 2. Reflexões (preserva body_markdown original intacto!)
            for r in snapshot["reflections"]:
                r_key = ("reflection", r["id"])
                out_links = outgoing_by_key.get(r_key, [])
                in_links = incoming_by_key.get(r_key, [])
                rel_types = sorted({l["relation_type"] for l in out_links + in_links})
                fm = _dump_yaml_frontmatter([
                    ("id", r["id"]),
                    ("type", "reflection"),
                    ("title", r["title"]),
                    ("primary_book_id", r["primary_book_id"]),
                    ("tags", r["tags"]),
                    ("is_pinned", r["is_pinned"]),
                    ("is_archived", r["is_archived"]),
                    ("created_at", r["created_at"]),
                    ("updated_at", r["updated_at"]),
                    ("relation_types", rel_types),
                ])
                parts = [fm, "", f"# {r['title']}", ""]
                if r["primary_book_id"] and ("book", r["primary_book_id"]) in entity_rel_paths:
                    bk_link = _build_md_link(
                        r["primary_book_title"] or r["primary_book_id"],
                        f"../{entity_rel_paths[('book', r['primary_book_id'])]}",
                        wikilinks=wikilinks,
                        wikilink_target=r["primary_book_title"],
                    )
                    parts.append(f"**Obra principal:** {bk_link}")
                    parts.append("")
                # Texto autoral original preservado literalmente sem modificação
                parts.append(r["body_markdown"])
                parts.extend(["", "---", "", "## Conexões e Referências", ""])
                if out_links:
                    parts.append("### Referências e Conexões de Saída")
                    for l in out_links:
                        parts.append(
                            _format_link_line(
                                from_dir="reflexoes",
                                rel_label=l["outgoing_label"],
                                other_available=l["target_available"],
                                other_type=l["target_type"],
                                other_id=l["target_id"],
                                other_label=l["target_label"],
                                rationale=l["rationale"],
                            )
                        )
                    parts.append("")
                if in_links:
                    parts.append("### Backlinks Recebidos")
                    for l in in_links:
                        parts.append(
                            _format_link_line(
                                from_dir="reflexoes",
                                rel_label=l["incoming_label"],
                                other_available=l["source_available"],
                                other_type=l["source_type"],
                                other_id=l["source_id"],
                                other_label=l["source_label"],
                                rationale=l["rationale"],
                            )
                        )
                    parts.append("")
                if not out_links and not in_links:
                    parts.append("*Nenhuma conexão registrada.*")
                    parts.append("")
                _add_text_entry(zf, entity_rel_paths[r_key], "\n".join(parts))

            # 3. Livros referenciados (metadados mínimos autorizados)
            for b in snapshot["books"]:
                b_key = ("book", b["id"])
                out_links = outgoing_by_key.get(b_key, [])
                in_links = incoming_by_key.get(b_key, [])
                rel_types = sorted({l["relation_type"] for l in out_links + in_links})
                fm = _dump_yaml_frontmatter([
                    ("id", b["id"]),
                    ("type", "book"),
                    ("title", b["title"]),
                    ("author", b["author"]),
                    ("format", b["format"]),
                    ("page_count", b["page_count"]),
                    ("publisher", b["publisher"]),
                    ("publish_year", b["publish_year"]),
                    ("isbn13", b["isbn13"]),
                    ("relation_types", rel_types),
                ])
                parts = [fm, "", f"# {b['title']}", ""]
                if b["author"]:
                    parts.append(f"- **Autor:** {b['author']}")
                parts.append(f"- **Formato:** {str(b['format'] or '').upper()}")
                if b["page_count"]:
                    parts.append(f"- **Páginas:** {b['page_count']}")
                if b["isbn13"]:
                    parts.append(f"- **ISBN-13:** {b['isbn13']}")
                parts.append("")
                parts.append("## Conexões e Referências")
                if out_links or in_links:
                    for l in out_links:
                        parts.append(
                            _format_link_line(
                                from_dir="livros",
                                rel_label=l["outgoing_label"],
                                other_available=l["target_available"],
                                other_type=l["target_type"],
                                other_id=l["target_id"],
                                other_label=l["target_label"],
                                rationale=l["rationale"],
                            )
                        )
                    for l in in_links:
                        parts.append(
                            _format_link_line(
                                from_dir="livros",
                                rel_label=l["incoming_label"],
                                other_available=l["source_available"],
                                other_type=l["source_type"],
                                other_id=l["source_id"],
                                other_label=l["source_label"],
                                rationale=l["rationale"],
                            )
                        )
                else:
                    parts.append("*Nenhuma conexão direta.*")
                parts.append("")
                _add_text_entry(zf, entity_rel_paths[b_key], "\n".join(parts))

            # 4. Anotações autorizadas
            books_lookup = {b["id"]: b for b in snapshot["books"]}
            for a in snapshot["annotations"]:
                a_key = (a["type"], a["id"])
                out_links = outgoing_by_key.get(a_key, [])
                in_links = incoming_by_key.get(a_key, [])
                rel_types = sorted({l["relation_type"] for l in out_links + in_links})
                type_pt = ENTITY_TYPE_LABELS_PT.get(a["type"], a["type"])
                bk = books_lookup.get(a["book_id"])
                fm = _dump_yaml_frontmatter([
                    ("id", a["id"]),
                    ("type", a["type"]),
                    ("source_book_id", a["book_id"]),
                    ("page", a["page"]),
                    ("epub_cfi", a["epub_cfi"]),
                    ("chapter_label", a["chapter_label"]),
                    ("is_own", a["is_own"]),
                    ("visibility", a["visibility"]),
                    ("created_at", a["created_at"]),
                    ("updated_at", a["updated_at"]),
                    ("relation_types", rel_types),
                ])
                parts = [fm, "", f"# {type_pt} — {bk['title'] if bk else a['book_id']}", ""]
                if bk and ("book", bk["id"]) in entity_rel_paths:
                    bk_link = _build_md_link(
                        bk["title"],
                        f"../{entity_rel_paths[('book', bk['id'])]}",
                        wikilinks=wikilinks,
                        wikilink_target=bk["title"],
                    )
                    parts.append(f"- **Obra:** {bk_link}")
                if a["chapter_label"]:
                    parts.append(f"- **Capítulo:** {a['chapter_label']}")
                if a["page"] is not None:
                    parts.append(f"- **Página (PDF):** {a['page']}")
                if a["epub_cfi"]:
                    parts.append(f"- **Localização (EPUB CFI):** `{a['epub_cfi']}`")
                parts.append("")
                if a["content_omitted"]:
                    parts.append(
                        "*Referência pública de terceiro: conteúdo integral omitido por política conservadora de privacidade e redistribuição.*"
                    )
                elif a["text"]:
                    if a["type"] == "highlight":
                        parts.append(f"> {a['text']}")
                    else:
                        parts.append(a["text"])
                parts.append("")
                parts.append("## Conexões")
                if out_links or in_links:
                    for l in out_links:
                        parts.append(
                            _format_link_line(
                                from_dir="anotacoes",
                                rel_label=l["outgoing_label"],
                                other_available=l["target_available"],
                                other_type=l["target_type"],
                                other_id=l["target_id"],
                                other_label=l["target_label"],
                                rationale=l["rationale"],
                            )
                        )
                    for l in in_links:
                        parts.append(
                            _format_link_line(
                                from_dir="anotacoes",
                                rel_label=l["incoming_label"],
                                other_available=l["source_available"],
                                other_type=l["source_type"],
                                other_id=l["source_id"],
                                other_label=l["source_label"],
                                rationale=l["rationale"],
                            )
                        )
                else:
                    parts.append("*Nenhuma conexão.*")
                parts.append("")
                _add_text_entry(zf, entity_rel_paths[a_key], "\n".join(parts))

            # 5. Índice principal (`indice.md`)
            idx_lines = [
                _dump_yaml_frontmatter([
                    ("format", "lectoria-atlas-markdown-zip"),
                    ("format_version", EXPORT_FORMAT_VERSION),
                    ("app_version", snapshot["app_version"]),
                    ("exported_at", snapshot["exported_at"]),
                    ("wikilinks", wikilinks),
                ]),
                "",
                "# Índice da Exportação — Atlas Lectoria",
                "",
                f"- **Data da exportação (UTC):** {snapshot['exported_at']}",
                f"- **Versão do formato:** {EXPORT_FORMAT_VERSION}",
                f"- **Conceitos exportados:** {snapshot['counts']['concepts']}",
                f"- **Reflexões exportadas:** {snapshot['counts']['reflections']}",
                f"- **Conexões exportadas:** {snapshot['counts']['links']}",
                f"- **Livros referenciados:** {snapshot['counts']['books']}",
                f"- **Anotações referenciadas/próprias:** {snapshot['counts']['annotations']}",
                "",
                "## Convenções e Privacidade",
                "- As identidades canônicas (`id`) preservam os UUIDs originais do Atlas.",
                "- Localizações PDF (`page`) e EPUB (`epub_cfi`) são mantidas separadamente sem conversão artificial.",
                "- Referências indisponíveis (removidas, revogadas ou pertencentes a fontes inativas) aparecem como `Referência indisponível` sem exposição de metadados protegidos.",
                "- Anotações públicas de terceiros referenciadas mantêm apenas metadados estruturados mínimos, sem cópia do texto integral de terceiro.",
                "",
                "## Conceitos",
            ]
            if snapshot["concepts"]:
                for c in snapshot["concepts"]:
                    idx_lines.append(
                        f"- {_build_md_link(c['name'], entity_rel_paths[('concept', c['id'])], wikilinks=wikilinks, wikilink_target=c['name'])}"
                    )
            else:
                idx_lines.append("*Nenhum conceito exportado.*")

            idx_lines.extend(["", "## Reflexões Autorais"])
            if snapshot["reflections"]:
                for r in snapshot["reflections"]:
                    idx_lines.append(
                        f"- {_build_md_link(r['title'], entity_rel_paths[('reflection', r['id'])], wikilinks=wikilinks, wikilink_target=r['title'])}"
                    )
            else:
                idx_lines.append("*Nenhuma reflexão exportada.*")

            idx_lines.extend(["", "## Obras Referenciadas"])
            if snapshot["books"]:
                for b in snapshot["books"]:
                    idx_lines.append(
                        f"- {_build_md_link(b['title'], entity_rel_paths[('book', b['id'])], wikilinks=wikilinks, wikilink_target=b['title'])}"
                    )
            else:
                idx_lines.append("*Nenhuma obra referenciada.*")
            idx_lines.append("")

            _add_text_entry(zf, "indice.md", "\n".join(idx_lines))

            # 6. Manifesto estruturado (`manifest.json`)
            manifest = {
                "format": "lectoria-atlas-markdown-zip",
                "format_version": EXPORT_FORMAT_VERSION,
                "app_version": snapshot["app_version"],
                "exported_at": snapshot["exported_at"],
                "wikilinks_enabled": bool(wikilinks),
                "location_conventions": {
                    "pdf": "page (integer >= 1)",
                    "epub": "epub_cfi (EPUB Canonical Fragment Identifier)",
                },
                "policies": snapshot["policies"],
                "counts": snapshot["counts"],
                "files": manifest_files,
            }
            _add_text_entry(zf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return tmp_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def generate_atlas_export_file(
    user_id: int,
    *,
    export_format: str = "markdown_zip",
    wikilinks: bool = False,
    include_archived: bool = True,
) -> Dict[str, Any]:
    """
    Orquestra a geração segura e limitada de um arquivo de exportação em disco temporário.
    O chamador (rota HTTP) é responsável por remover `file_path` após o streaming.
    """
    fmt = str(export_format or "").strip().lower()
    if fmt not in VALID_EXPORT_FORMATS:
        raise KnowledgeValidationError(
            f"Formato de exportação inválido: '{export_format}'. Valores permitidos: {', '.join(VALID_EXPORT_FORMATS)}."
        )

    acquired = _EXPORT_SEMAPHORE.acquire(timeout=2.0)
    if not acquired:
        raise KnowledgeExportBusyError(
            "Muitas exportações simultâneas em processamento. Tente novamente em instantes."
        )

    try:
        snapshot = build_atlas_export_snapshot(user_id, include_archived=include_archived)
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

        if fmt == "json":
            tmp_path = _write_json_export_to_tempfile(snapshot)
            return {
                "file_path": tmp_path,
                "filename": f"lectoria-atlas-{date_stamp}.json",
                "media_type": "application/json; charset=utf-8",
                "format": fmt,
                "counts": snapshot["counts"],
            }
        if fmt == "csv":
            tmp_path = _write_csv_zip_export_to_tempfile(snapshot)
            return {
                "file_path": tmp_path,
                "filename": f"lectoria-atlas-csv-{date_stamp}.zip",
                "media_type": "application/zip",
                "format": fmt,
                "counts": snapshot["counts"],
            }

        tmp_path = _write_markdown_zip_export_to_tempfile(snapshot, wikilinks=bool(wikilinks))
        return {
            "file_path": tmp_path,
            "filename": f"lectoria-atlas-markdown-{date_stamp}.zip",
            "media_type": "application/zip",
            "format": fmt,
            "counts": snapshot["counts"],
        }
    finally:
        _EXPORT_SEMAPHORE.release()
