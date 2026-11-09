"""
Módulo de gerenciamento da biblioteca do Lectoria.
Responsável por escaneamento de arquivos PDF e EPUB, extração avançada de metadados,
proteção contra path traversal, consultas com filtros multidimensionais, busca FTS5,
geração de prateleiras de descoberta e sincronização taxonômica.
"""

from datetime import datetime
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import pypdf

import config
from database import get_db
from metadata_service import (
    extract_epub_metadata_advanced,
    extract_pdf_metadata_advanced,
    normalize_for_comparison,
    record_metadata_provenance,
)
from taxonomy_service import (
    map_raw_subjects_to_taxonomy,
    sync_book_taxonomy,
    get_book_taxonomy,
    list_categories,
    TAXONOMY_TREE,
)

logger = logging.getLogger("lectoria.library")

_scan_lock = threading.Lock()


class SecurityError(Exception):
    """Lançada em caso de tentativa de acesso indevido fora da biblioteca."""
    pass


class BookNotFoundError(Exception):
    """Lançada quando o livro não existe no banco ou seu arquivo foi removido."""
    pass


def update_book_fts(
    cursor,
    book_id: str,
    title: Optional[str],
    author: Optional[str],
    description: Optional[str] = None,
    publisher: Optional[str] = None,
    subjects: Optional[str] = None,
) -> None:
    """Atualiza o índice de busca textual FTS5 de forma resiliente."""
    try:
        cursor.execute("DELETE FROM books_fts WHERE book_id = ?", (book_id,))
        cursor.execute(
            """
            INSERT INTO books_fts (book_id, title, author, description, publisher, subjects)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                title or "",
                author or "",
                description or "",
                publisher or "",
                subjects or "",
            ),
        )
    except Exception:
        pass


def _extract_pdf_metadata(file_path: Path) -> Dict[str, Any]:
    """Extrai metadados delegando para o extrator avançado resiliente."""
    return extract_pdf_metadata_advanced(file_path)


def scan_library(source_id: Optional[int] = None) -> Dict[str, int]:
    """
    Varre os diretórios das fontes da biblioteca em busca de novos PDFs/EPUBs ou modificações.
    Se source_id for fornecido, varre exclusivamente a fonte informada.
    Caso contrário, varre todas as fontes ativas cadastradas.
    Popula metadados locais (ISBN, editora, ano, descrição, taxonomia) e atualiza o índice FTS5.
    Protegido por lock de thread para impedir execuções concorrentes destrutivas.
    """
    with _scan_lock:
        return _scan_library_locked(source_id=source_id)


def _scan_library_locked(source_id: Optional[int] = None) -> Dict[str, int]:
    stats = {"added": 0, "updated": 0, "missing": 0, "total": 0}

    with get_db() as conn:
        cursor = conn.cursor()

        # Seleciona as fontes a serem varridas
        if source_id is not None:
            cursor.execute(
                "SELECT id, name, path, is_active FROM library_sources WHERE id = ?",
                (source_id,),
            )
        else:
            cursor.execute(
                "SELECT id, name, path, is_active FROM library_sources WHERE is_active = 1"
            )

        sources = [dict(r) for r in cursor.fetchall()]

        # Se não há fontes cadastradas (ex: banco inicial ou testes), usa LIBRARY_DIR padrão
        if not sources:
            default_path = str(config.LIBRARY_DIR.resolve())
            sources = [{"id": 1, "name": "Biblioteca Principal", "path": default_path, "is_active": 1}]

        for src in sources:
            src_id = src["id"]
            src_path = Path(src["path"]).resolve()

            if not src_path.exists() or not src_path.is_dir() or not os.access(src_path, os.R_OK):
                err_msg = f"Diretório inacessível ou sem permissão de leitura: {src_path}"
                cursor.execute(
                    "UPDATE library_sources SET last_error = ?, last_scanned_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (err_msg, src_id),
                )
                cursor.execute(
                    "UPDATE books SET is_missing = 1, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
                    (src_id,),
                )
                continue

            # Mapeia livros existentes desta fonte por rel_path
            cursor.execute(
                """
                SELECT id, rel_path, filesize, file_hash, is_missing, cover_type,
                       title, author, subtitle, volume, isbn10, isbn13, publisher, publish_year, language,
                       description, edition, manual_lock_fields
                FROM books WHERE source_id = ?
                """,
                (src_id,),
            )
            existing_books = {row["rel_path"]: dict(row) for row in cursor.fetchall()}
            found_rel_paths = set()

            for root, dirs, files in os.walk(src_path):
                # Ignora pastas ocultas
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                for filename in files:
                    if filename.startswith("."):
                        continue
                    ext = filename.lower().split(".")[-1] if "." in filename else ""
                    if ext not in ("pdf", "epub"):
                        continue

                    file_format = "epub" if ext == "epub" else "pdf"
                    full_path = (Path(root) / filename).resolve()

                    # Verificação de segurança: garante que o arquivo está contido na fonte autorizada
                    try:
                        rel_path = full_path.relative_to(src_path).as_posix()
                    except ValueError:
                        continue

                    # Checagem rigorosa de links simbólicos
                    try:
                        real_path = full_path.resolve(strict=True)
                        real_path.relative_to(src_path)
                    except (ValueError, FileNotFoundError):
                        continue

                    found_rel_paths.add(rel_path)

                    try:
                        stat_info = full_path.stat()
                        current_size = stat_info.st_size
                        # Hash rápido baseado no tamanho e mtime para evitar reprocessamento
                        current_hash = f"{current_size}:{int(stat_info.st_mtime)}"
                    except OSError:
                        continue

                    existing = existing_books.get(rel_path)

                    # Reconciliação inteligente de arquivos movidos/renomeados na mesma fonte:
                    # se rel_path é novo mas existe um registro cujo caminho antigo desapareceu do disco
                    # com o mesmo file_hash e filesize, preserva o book_id, progresso e anotações!
                    if not existing:
                        for old_rel, old_data in list(existing_books.items()):
                            if old_rel in found_rel_paths:
                                continue
                            if (
                                old_data.get("filesize") == current_size
                                and old_data.get("file_hash") == current_hash
                                and not (src_path / old_rel).exists()
                            ):
                                existing = old_data
                                existing_books.pop(old_rel, None)
                                existing_books[rel_path] = existing
                                cursor.execute(
                                    """
                                    UPDATE books
                                    SET rel_path = ?, filename = ?, is_missing = 0, updated_at = CURRENT_TIMESTAMP
                                    WHERE id = ?
                                    """,
                                    (rel_path, filename, existing["id"]),
                                )
                                stats["updated"] += 1
                                break

                    if existing:
                        # Se o arquivo já existe e nada mudou, apenas garante is_missing = 0
                        if existing["filesize"] == current_size and existing["file_hash"] == current_hash:
                            if existing["is_missing"] == 1:
                                cursor.execute(
                                    "UPDATE books SET is_missing = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                                    (existing["id"],),
                                )
                                stats["updated"] += 1
                            continue

                        # Arquivo foi alterado no disco: extrai novos metadados
                        if file_format == "epub":
                            meta = extract_epub_metadata_advanced(full_path)
                        else:
                            meta = extract_pdf_metadata_advanced(full_path)

                        # Respeita campos com trava manual (manual_lock_fields)
                        locked_fields = set()
                        if existing.get("manual_lock_fields"):
                            try:
                                locked_fields = set(json.loads(existing["manual_lock_fields"]))
                            except Exception:
                                pass

                        title_to_set = existing["title"] if ("title" in locked_fields and existing["title"]) else meta["title"]
                        author_to_set = existing["author"] if ("author" in locked_fields and existing["author"]) else meta["author"]
                        subtitle_to_set = existing.get("subtitle") if "subtitle" in locked_fields else meta.get("subtitle")
                        volume_to_set = existing.get("volume") if "volume" in locked_fields else meta.get("volume")
                        isbn10_to_set = existing["isbn10"] if "isbn10" in locked_fields else meta.get("isbn10")
                        isbn13_to_set = existing["isbn13"] if "isbn13" in locked_fields else meta.get("isbn13")
                        publisher_to_set = existing["publisher"] if "publisher" in locked_fields else meta.get("publisher")
                        year_to_set = existing["publish_year"] if "publish_year" in locked_fields else meta.get("publish_year")
                        lang_to_set = existing["language"] if "language" in locked_fields else meta.get("language")
                        desc_to_set = existing["description"] if "description" in locked_fields else meta.get("description")
                        edition_to_set = existing["edition"] if "edition" in locked_fields else meta.get("edition")

                        if existing.get("cover_type") != "custom":
                            # Invalida cache de capa extraída quando o arquivo original mudou
                            if config.COVERS_DIR.exists():
                                for cov_file in config.COVERS_DIR.glob(f"{existing['id']}_extracted*"):
                                    try:
                                        cov_file.unlink()
                                    except OSError:
                                        pass
                            cursor.execute(
                                """
                                UPDATE books
                                SET title = ?, author = ?, subtitle = ?, volume = ?, filename = ?, filesize = ?,
                                    page_count = ?, file_hash = ?, format = ?, is_missing = 0,
                                    isbn10 = ?, isbn13 = ?, publisher = ?, publish_year = ?,
                                    language = ?, description = ?, edition = ?,
                                    cover_type = 'none', cover_path = NULL, cover_source_hash = NULL,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                                """,
                                (
                                    title_to_set,
                                    author_to_set,
                                    subtitle_to_set,
                                    volume_to_set,
                                    filename,
                                    current_size,
                                    meta["page_count"],
                                    current_hash,
                                    file_format,
                                    isbn10_to_set,
                                    isbn13_to_set,
                                    publisher_to_set,
                                    year_to_set,
                                    lang_to_set,
                                    desc_to_set,
                                    edition_to_set,
                                    existing["id"],
                                ),
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE books
                                SET title = ?, author = ?, subtitle = ?, volume = ?, filename = ?, filesize = ?,
                                    page_count = ?, file_hash = ?, format = ?, is_missing = 0,
                                    isbn10 = ?, isbn13 = ?, publisher = ?, publish_year = ?,
                                    language = ?, description = ?, edition = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                                """,
                                (
                                    title_to_set,
                                    author_to_set,
                                    subtitle_to_set,
                                    volume_to_set,
                                    filename,
                                    current_size,
                                    meta["page_count"],
                                    current_hash,
                                    file_format,
                                    isbn10_to_set,
                                    isbn13_to_set,
                                    publisher_to_set,
                                    year_to_set,
                                    lang_to_set,
                                    desc_to_set,
                                    edition_to_set,
                                    existing["id"],
                                ),
                            )

                        update_book_fts(
                            cursor,
                            existing["id"],
                            title_to_set,
                            author_to_set,
                            desc_to_set,
                            publisher_to_set,
                            isbn13_to_set,
                        )
                        tax = map_raw_subjects_to_taxonomy(
                            title=title_to_set,
                            author=author_to_set,
                            raw_subjects=meta.get("subjects", []),
                            language=lang_to_set,
                        )
                        sync_book_taxonomy(
                            existing["id"],
                            tax,
                            is_manual=False,
                            conn=conn,
                        )
                        stats["updated"] += 1
                    else:
                        # Novo livro encontrado nesta fonte
                        book_id = str(uuid.uuid4())
                        if file_format == "epub":
                            meta = extract_epub_metadata_advanced(full_path)
                        else:
                            meta = extract_pdf_metadata_advanced(full_path)

                        cursor.execute(
                            """
                            INSERT INTO books (
                                id, source_id, title, author, subtitle, volume, filename, rel_path, filesize,
                                page_count, file_hash, format, is_missing, isbn10, isbn13,
                                publisher, publish_year, language, description, edition,
                                identification_status, identification_score, metadata_source,
                                manual_lock_fields
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 'pending', 0.0, 'embedded', '[]')
                            """,
                            (
                                book_id,
                                src_id,
                                meta["title"],
                                meta["author"],
                                meta.get("subtitle"),
                                meta.get("volume"),
                                filename,
                                rel_path,
                                current_size,
                                meta["page_count"],
                                current_hash,
                                file_format,
                                meta.get("isbn10"),
                                meta.get("isbn13"),
                                meta.get("publisher"),
                                meta.get("publish_year"),
                                meta.get("language"),
                                meta.get("description"),
                                meta.get("edition"),
                            ),
                        )
                        record_metadata_provenance(
                            cursor,
                            book_id,
                            {
                                "title": meta.get("title"),
                                "author": meta.get("author"),
                                "subtitle": meta.get("subtitle"),
                                "volume": meta.get("volume"),
                                "edition": meta.get("edition"),
                                "isbn10": meta.get("isbn10"),
                                "isbn13": meta.get("isbn13"),
                                "publisher": meta.get("publisher"),
                                "publish_year": meta.get("publish_year"),
                            },
                            source="embedded",
                            confidence=60.0,
                        )
                        update_book_fts(
                            cursor,
                            book_id,
                            meta["title"],
                            meta["author"],
                            meta.get("description"),
                            meta.get("publisher"),
                            meta.get("isbn13"),
                        )
                        tax = map_raw_subjects_to_taxonomy(
                            title=meta.get("title"),
                            author=meta.get("author"),
                            raw_subjects=meta.get("subjects", []),
                            language=meta.get("language"),
                            description=meta.get("description"),
                            publisher=meta.get("publisher"),
                        )
                        sync_book_taxonomy(
                            book_id,
                            tax,
                            is_manual=False,
                            conn=conn,
                        )
                        stats["added"] += 1

            # Marca como ausente qualquer livro desta fonte que não foi encontrado
            for rel_path, data in existing_books.items():
                if rel_path not in found_rel_paths and data["is_missing"] == 0:
                    cursor.execute(
                        "UPDATE books SET is_missing = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (data["id"],),
                    )
                    stats["missing"] += 1

            cursor.execute(
                "UPDATE library_sources SET last_error = NULL, last_scanned_at = CURRENT_TIMESTAMP WHERE id = ?",
                (src_id,),
            )

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0")
        stats["total"] = cursor.fetchone()[0]

    return stats


def get_books(
    search: Optional[str] = None,
    sort_by: str = "title",
    order: str = "asc",
    status_filter: Optional[str] = None,
    collection_id: Optional[int] = None,
    source_id: Optional[int] = None,
    format_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    subcategory_filter: Optional[str] = None,
    genre_filter: Optional[str] = None,
    origin_filter: Optional[str] = None,
    language_filter: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    identification_status: Optional[str] = None,
    fts_query: Optional[str] = None,
    favorite_only: bool = False,
    want_to_read_only: bool = False,
    uncategorized: bool = False,
    user_id: int = 1,
) -> List[Dict[str, Any]]:
    """
    Retorna a lista de livros disponíveis, enriquecida com progresso, coleções,
    avaliações e taxonomia multidimensional. Suporta filtros combinados e busca FTS5.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        sql = """
            SELECT
                b.id,
                b.source_id,
                b.title,
                b.author,
                b.subtitle,
                b.volume,
                b.filename,
                b.filesize,
                b.page_count,
                b.format,
                b.cover_type,
                b.cover_path,
                b.cover_updated_at,
                b.isbn10,
                b.isbn13,
                b.publisher,
                b.publish_year,
                b.language,
                b.description,
                b.original_title,
                b.work_id,
                b.edition,
                b.identification_status,
                b.identification_score,
                b.metadata_source,
                b.metadata_updated_at,
                b.manual_lock_fields,
                b.reviewed_at,
                b.reviewed_by,
                b.last_error_reason,
                b.created_at,
                s.name as source_name,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.progress_locator,
                COALESCE(p.is_completed_manual, 0) as is_completed_manual,
                p.updated_at as progress_updated_at,
                br.rating as user_rating,
                COALESCE(br.is_favorite, 0) as is_favorite,
                COALESCE(br.want_to_read, 0) as want_to_read
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p
                ON b.id = p.book_id AND p.user_id = ?
            LEFT JOIN book_reviews br
                ON b.id = br.book_id AND br.user_id = ?
            WHERE b.is_missing = 0 AND (s.is_active = 1 OR s.is_active IS NULL)
        """
        params: List[Any] = [user_id, user_id]

        # Busca textual simples ou compatível
        if search and search.strip():
            query_term = f"%{search.strip()}%"
            sql += " AND (b.title LIKE ? OR b.author LIKE ? OR b.isbn13 LIKE ? OR b.isbn10 LIKE ?)"
            params.extend([query_term, query_term, query_term, query_term])

        # Busca por indexador FTS5
        if fts_query and fts_query.strip():
            clean_terms = [t for t in fts_query.strip().split() if t.isalnum() or len(t) > 1]
            if clean_terms:
                match_str = " ".join(f'"{t}"*' for t in clean_terms)
                sql += " AND b.id IN (SELECT fts.book_id FROM books_fts fts WHERE books_fts MATCH ?)"
                params.append(match_str)

        # Filtro de livros sem categoria ou por categoria específica
        is_uncat_query = uncategorized or (
            category_filter is not None
            and category_filter.strip() in ("__uncategorized__", "uncategorized", "Sem categoria")
        )
        if is_uncat_query:
            sql += """ AND NOT EXISTS (
                SELECT 1 FROM book_categories bc WHERE bc.book_id = b.id
            ) AND NOT EXISTS (
                SELECT 1 FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE bs.book_id = b.id
                  AND s.category IS NOT NULL
                  AND TRIM(s.category) != ''
                  AND LOWER(TRIM(s.category)) != 'geral'
            )"""
        elif category_filter and category_filter.strip():
            cat_val = category_filter.strip()
            sql += """ AND (
                EXISTS (
                    SELECT 1 FROM book_categories bc
                    JOIN categories c ON bc.category_id = c.id
                    WHERE bc.book_id = b.id AND c.name = ? COLLATE NOCASE
                )
                OR EXISTS (
                    SELECT 1 FROM book_subjects bs
                    JOIN subjects s ON bs.subject_id = s.id
                    WHERE bs.book_id = b.id AND s.category = ? COLLATE NOCASE
                )
            )"""
            params.extend([cat_val, cat_val])

        if subcategory_filter and subcategory_filter.strip():
            sub_val = subcategory_filter.strip()
            sql += """ AND (
                EXISTS (
                    SELECT 1 FROM book_categories bc
                    WHERE bc.book_id = b.id AND bc.subcategory = ? COLLATE NOCASE
                )
                OR EXISTS (
                    SELECT 1 FROM book_subjects bs
                    JOIN subjects s ON bs.subject_id = s.id
                    WHERE bs.book_id = b.id AND s.subcategory = ? COLLATE NOCASE
                )
            )"""
            params.extend([sub_val, sub_val])

        if genre_filter:
            sql += """ AND b.id IN (
                SELECT bs.book_id FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE s.genre = ?
            )"""
            params.append(genre_filter)

        if origin_filter:
            sql += """ AND b.id IN (
                SELECT bs.book_id FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE s.origin = ?
            )"""
            params.append(origin_filter)

        # Filtro por idioma
        if language_filter:
            sql += " AND b.language = ?"
            params.append(language_filter)

        # Filtro por ano de publicação
        if year_from is not None:
            sql += " AND b.publish_year >= ?"
            params.append(year_from)

        if year_to is not None:
            sql += " AND b.publish_year <= ?"
            params.append(year_to)

        # Filtro por status de identificação
        if identification_status:
            sql += " AND b.identification_status = ?"
            params.append(identification_status)

        # Filtro por favoritos ou quero ler (isolado por user_id no JOIN de book_reviews)
        if favorite_only or status_filter == "favorites":
            sql += " AND COALESCE(br.is_favorite, 0) = 1"

        if want_to_read_only or status_filter == "want_to_read":
            sql += " AND COALESCE(br.want_to_read, 0) = 1"

        # Filtro por coleção
        if collection_id is not None:
            sql += (
                " AND b.id IN ("
                "   SELECT cb.book_id FROM collection_books cb"
                "   INNER JOIN collections c ON cb.collection_id = c.id"
                "   WHERE c.id = ? AND c.user_id = ?"
                " )"
            )
            params.extend([collection_id, user_id])

        # Filtro por fonte
        if source_id is not None:
            sql += " AND b.source_id = ?"
            params.append(source_id)

        # Filtro por formato (pdf / epub)
        if format_filter in ("pdf", "epub"):
            sql += " AND b.format = ?"
            params.append(format_filter)

        # Predicados de status de leitura (partição mutuamente exclusiva por usuário)
        completed_expr = (
            "(COALESCE(p.is_completed_manual, 0) = 1 "
            "OR (b.page_count > 1 AND COALESCE(p.percentage, 0) >= 99.9) "
            "OR (b.page_count > 1 AND COALESCE(p.current_page, 1) >= b.page_count))"
        )
        if status_filter == "completed":
            sql += f" AND {completed_expr}"
        elif status_filter == "in_progress":
            sql += f" AND ((COALESCE(p.current_page, 1) > 1 OR COALESCE(p.percentage, 0) > 0) AND NOT {completed_expr})"
        elif status_filter == "unread":
            sql += (
                " AND (p.id IS NULL OR ("
                "COALESCE(p.current_page, 1) <= 1 "
                "AND COALESCE(p.percentage, 0) <= 0 "
                "AND COALESCE(p.is_completed_manual, 0) = 0))"
            )

        # Ordenação
        order_clause = "DESC" if order.lower() == "desc" else "ASC"
        if sort_by == "recent":
            sql += f" ORDER BY b.created_at {order_clause}"
        elif sort_by == "author":
            sql += f" ORDER BY COALESCE(b.author, '') COLLATE NOCASE {order_clause}, b.title COLLATE NOCASE ASC"
        elif sort_by == "year":
            sql += f" ORDER BY CASE WHEN b.publish_year IS NULL THEN 1 ELSE 0 END, b.publish_year {order_clause}, b.title ASC"
        elif sort_by == "last_read":
            if order_clause == "DESC":
                sql += " ORDER BY CASE WHEN p.updated_at IS NULL THEN 1 ELSE 0 END, p.updated_at DESC, b.title ASC"
            else:
                sql += " ORDER BY CASE WHEN p.updated_at IS NULL THEN 0 ELSE 1 END, p.updated_at ASC, b.title ASC"
        elif sort_by == "progress":
            sql += f" ORDER BY COALESCE(p.percentage, 0) {order_clause}, b.title ASC"
        else:
            sql += f" ORDER BY b.title COLLATE NOCASE {order_clause}"

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        books = [dict(r) for r in rows]

        for b in books:
            b["format"] = b.get("format") or ("epub" if b["filename"].lower().endswith(".epub") else "pdf")
            ctype = b.get("cover_type") or "none"
            b["cover_type"] = ctype
            b["has_custom_cover"] = ctype == "custom"
            ver = b.get("cover_updated_at") or ""
            b["cover_url"] = f"/api/books/{b['id']}/cover?v={ver}" if ver else f"/api/books/{b['id']}/cover"
            b["is_favorite"] = bool(b.get("is_favorite"))
            b["want_to_read"] = bool(b.get("want_to_read"))
            b["is_completed_manual"] = bool(b.get("is_completed_manual"))

            cur_pg = b.get("current_page") or 0
            pct = float(b.get("progress_percentage") or 0.0)
            pg_cnt = b.get("page_count") or 0
            is_comp = bool(
                b["is_completed_manual"]
                or (pg_cnt > 1 and pct >= 99.9)
                or (pg_cnt > 1 and cur_pg >= pg_cnt)
            )
            b["is_completed"] = is_comp
            if is_comp:
                b["reading_status"] = "completed"
                if pct < 100.0:
                    b["progress_percentage"] = 100.0
            elif cur_pg > 1 or pct > 0:
                b["reading_status"] = "in_progress"
            else:
                b["reading_status"] = "unread"

            locks = b.get("manual_lock_fields")
            if isinstance(locks, str):
                try:
                    b["manual_lock_fields"] = json.loads(locks)
                except Exception:
                    b["manual_lock_fields"] = []
            elif not locks:
                b["manual_lock_fields"] = []

        if books:
            book_ids = [b["id"] for b in books]
            placeholders = ",".join("?" for _ in book_ids)

            # Anexa coleções
            cursor.execute(
                f"""
                SELECT cb.book_id, c.id, c.name
                FROM collections c
                INNER JOIN collection_books cb ON c.id = cb.collection_id
                WHERE cb.book_id IN ({placeholders}) AND c.user_id = ?
                ORDER BY c.name COLLATE NOCASE ASC
                """,
                (*book_ids, user_id),
            )
            coll_map: Dict[str, List[Dict[str, Any]]] = {}
            for row in cursor.fetchall():
                b_id = row["book_id"]
                if b_id not in coll_map:
                    coll_map[b_id] = []
                coll_map[b_id].append({"id": row["id"], "name": row["name"]})

            # Anexa categorias gerenciadas em lote (book_categories + categories)
            cursor.execute(
                f"""
                SELECT bc.book_id, c.name AS category, bc.subcategory, bc.is_manual, c.display_order
                FROM book_categories bc
                JOIN categories c ON bc.category_id = c.id
                WHERE bc.book_id IN ({placeholders})
                ORDER BY c.display_order ASC, c.name COLLATE NOCASE ASC
                """,
                book_ids,
            )
            managed_cat_map: Dict[str, Dict[str, Any]] = {
                bid: {"categories": [], "subcategories": [], "is_manual": False}
                for bid in book_ids
            }
            for row in cursor.fetchall():
                bid = row["book_id"]
                cat_name = row["category"]
                if cat_name and cat_name not in managed_cat_map[bid]["categories"]:
                    managed_cat_map[bid]["categories"].append(cat_name)
                if row["subcategory"] and row["subcategory"] not in managed_cat_map[bid]["subcategories"]:
                    managed_cat_map[bid]["subcategories"].append(row["subcategory"])
                if row["is_manual"]:
                    managed_cat_map[bid]["is_manual"] = True

            # Anexa assuntos bibliográficos em lote (evita N+1 queries)
            cursor.execute(
                f"""
                SELECT bs.book_id, s.name, s.category, s.subcategory, s.genre, s.origin, s.period, bs.is_manual
                FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE bs.book_id IN ({placeholders})
                ORDER BY s.category, s.name
                """,
                book_ids,
            )
            tax_map: Dict[str, Dict[str, Any]] = {
                bid: {"categories": set(), "subcategories": set(), "genres": set(), "origins": set(), "subjects": [], "is_manual": False}
                for bid in book_ids
            }
            for row in cursor.fetchall():
                bid = row["book_id"]
                if row["category"] and row["category"].lower() != "geral":
                    tax_map[bid]["categories"].add(row["category"])
                if row["subcategory"]:
                    tax_map[bid]["subcategories"].add(row["subcategory"])
                if row["genre"]:
                    tax_map[bid]["genres"].add(row["genre"])
                if row["origin"]:
                    tax_map[bid]["origins"].add(row["origin"])
                if row["is_manual"]:
                    tax_map[bid]["is_manual"] = True
                tax_map[bid]["subjects"].append(row["name"])

            for b in books:
                bid = b["id"]
                b["collections"] = coll_map.get(bid, [])
                m_info = managed_cat_map.get(bid, {"categories": [], "subcategories": [], "is_manual": False})
                tinfo = tax_map.get(
                    bid,
                    {"categories": set(), "subcategories": set(), "genres": set(), "origins": set(), "subjects": [], "is_manual": False},
                )
                cats_list = m_info["categories"] if m_info["categories"] else sorted(list(tinfo["categories"]))
                subcats_list = sorted(list(set(m_info["subcategories"]) | tinfo["subcategories"]))
                b["taxonomy"] = {
                    "categories": cats_list,
                    "subcategories": subcats_list,
                    "is_manual": bool(m_info["is_manual"] or tinfo["is_manual"]),
                    "genres": sorted(list(tinfo["genres"])),
                    "origins": sorted(list(tinfo["origins"])),
                    "genre": next(iter(tinfo["genres"]), None),
                    "origin": next(iter(tinfo["origins"]), None),
                    "subjects": tinfo["subjects"],
                }

        return books


def get_book(book_id: str, user_id: int = 1) -> Dict[str, Any]:
    """Retorna detalhes completos de um único livro pelo seu ID estável para o usuário indicado."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                b.id,
                b.source_id,
                b.title,
                b.author,
                b.subtitle,
                b.volume,
                b.filename,
                b.rel_path,
                b.filesize,
                b.page_count,
                b.format,
                b.cover_type,
                b.cover_path,
                b.cover_updated_at,
                b.isbn10,
                b.isbn13,
                b.publisher,
                b.publish_year,
                b.language,
                b.description,
                b.original_title,
                b.work_id,
                b.edition,
                b.identification_status,
                b.identification_score,
                b.metadata_source,
                b.metadata_updated_at,
                b.manual_lock_fields,
                b.reviewed_at,
                b.reviewed_by,
                b.last_error_reason,
                b.is_missing,
                b.created_at,
                s.name as source_name,
                s.is_active as source_active,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.progress_locator,
                COALESCE(p.is_completed_manual, 0) as is_completed_manual,
                p.updated_at as progress_updated_at,
                br.rating as user_rating,
                br.review_text as user_review_text,
                COALESCE(br.is_favorite, 0) as is_favorite,
                COALESCE(br.want_to_read, 0) as want_to_read
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p
                ON b.id = p.book_id AND p.user_id = ?
            LEFT JOIN book_reviews br
                ON b.id = br.book_id AND br.user_id = ?
            WHERE b.id = ?
            """,
            (user_id, user_id, book_id),
        )
        row = cursor.fetchone()
        if not row:
            raise BookNotFoundError(f"Livro '{book_id}' não encontrado.")
        data = dict(row)
        if data["source_active"] is not None and not data["source_active"]:
            raise SecurityError(f"A fonte do livro '{book_id}' está desativada.")
        if data["is_missing"]:
            raise BookNotFoundError(f"O arquivo do livro '{book_id}' não está disponível no disco.")

        cursor.execute(
            """
            SELECT c.id, c.name, c.description
            FROM collections c
            INNER JOIN collection_books cb ON c.id = cb.collection_id
            WHERE cb.book_id = ? AND c.user_id = ?
            ORDER BY c.name COLLATE NOCASE ASC
            """,
            (book_id, user_id),
        )
        data["collections"] = [dict(c) for c in cursor.fetchall()]
        data["format"] = data.get("format") or ("epub" if data["filename"].lower().endswith(".epub") else "pdf")
        if data["format"] == "epub":
            try:
                from epub_service import extract_epub_metadata
                epub_meta = extract_epub_metadata(resolve_book_path(book_id))
                data["is_drm"] = bool(epub_meta.get("is_drm"))
                data["drm_type"] = epub_meta.get("drm_type")
            except Exception:
                data["is_drm"] = False
                data["drm_type"] = None
        else:
            data["is_drm"] = False
            data["drm_type"] = None

        ctype = data.get("cover_type") or "none"
        data["cover_type"] = ctype
        data["has_custom_cover"] = ctype == "custom"
        ver = data.get("cover_updated_at") or ""
        data["cover_url"] = f"/api/books/{data['id']}/cover?v={ver}" if ver else f"/api/books/{data['id']}/cover"
        data["is_favorite"] = bool(data.get("is_favorite"))
        data["want_to_read"] = bool(data.get("want_to_read"))
        data["is_completed_manual"] = bool(data.get("is_completed_manual"))
        cur_pg = data.get("current_page") or 0
        pct = float(data.get("progress_percentage") or 0.0)
        pg_cnt = data.get("page_count") or 0
        is_comp = bool(
            data["is_completed_manual"]
            or (pg_cnt > 1 and pct >= 99.9)
            or (pg_cnt > 1 and cur_pg >= pg_cnt)
        )
        data["is_completed"] = is_comp
        if is_comp:
            data["reading_status"] = "completed"
            if pct < 100.0:
                data["progress_percentage"] = 100.0
        elif cur_pg > 1 or pct > 0:
            data["reading_status"] = "in_progress"
        else:
            data["reading_status"] = "unread"

        locks = data.get("manual_lock_fields")
        if isinstance(locks, str):
            try:
                data["manual_lock_fields"] = json.loads(locks)
            except Exception:
                data["manual_lock_fields"] = []
        elif not locks:
            data["manual_lock_fields"] = []

        # Anexa proveniência de metadados por campo
        cursor.execute(
            """
            SELECT field_name, field_value, source, confidence, updated_at
            FROM metadata_provenance
            WHERE book_id = ?
            ORDER BY field_name ASC
            """,
            (book_id,),
        )
        data["provenance"] = {
            r["field_name"]: {
                "value": r["field_value"],
                "source": r["source"],
                "confidence": r["confidence"],
                "updated_at": r["updated_at"],
            }
            for r in cursor.fetchall()
        }

        data["taxonomy"] = get_book_taxonomy(book_id)
        return data


def set_book_reading_status(book_id: str, user_id: int, status: str) -> Dict[str, Any]:
    """
    Permite ao usuário marcar explicitamente um livro como:
    - 'completed': concluído (percentage = 100.0, is_completed_manual = 1)
    - 'in_progress': em andamento (is_completed_manual = 0, preserva página ou inicia na página 2 / 5%)
    - 'unread': não iniciado (remove registro de reading_progress do usuário para o livro)
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, page_count FROM books WHERE id = ?;", (book_id,))
        b_row = cursor.fetchone()
        if not b_row:
            raise BookNotFoundError(f"Livro '{book_id}' não encontrado.")
        total_pages = max(int(b_row["page_count"] or 1), 1)

        if status == "unread":
            cursor.execute(
                "DELETE FROM reading_progress WHERE user_id = ? AND book_id = ?;",
                (user_id, book_id),
            )
        elif status == "completed":
            cursor.execute(
                """
                INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage, is_completed_manual, updated_at)
                VALUES (?, ?, ?, ?, 100.0, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, book_id) DO UPDATE SET
                    current_page = MAX(reading_progress.current_page, excluded.total_pages),
                    total_pages = MAX(reading_progress.total_pages, excluded.total_pages),
                    percentage = 100.0,
                    is_completed_manual = 1,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (user_id, book_id, total_pages, total_pages),
            )
        elif status == "in_progress":
            cursor.execute(
                "SELECT current_page, total_pages, percentage FROM reading_progress WHERE user_id = ? AND book_id = ?;",
                (user_id, book_id),
            )
            existing = cursor.fetchone()
            cur_p = int(existing["current_page"]) if existing else 1
            tot_p = max(int(existing["total_pages"]) if existing else total_pages, 1)
            pct = float(existing["percentage"]) if existing else 0.0
            if pct >= 99.9 or (cur_p <= 1 and pct <= 0.0):
                cur_p = min(2, tot_p) if tot_p > 1 else 1
                pct = round((cur_p / tot_p) * 100.0, 1) if tot_p > 1 else 5.0
                if pct >= 100.0:
                    pct = 50.0
            cursor.execute(
                """
                INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage, is_completed_manual, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, book_id) DO UPDATE SET
                    current_page = excluded.current_page,
                    total_pages = excluded.total_pages,
                    percentage = excluded.percentage,
                    is_completed_manual = 0,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (user_id, book_id, cur_p, tot_p, pct),
            )
        else:
            raise ValueError(f"Status de leitura inválido: '{status}'.")

    return get_book(book_id, user_id=user_id)


def get_discovery_shelves(user_id: int = 1) -> List[Dict[str, Any]]:
    """
    Constrói prateleiras dinâmicas horizontais inspiradas em plataformas de streaming.
    - Respeita a ordem de categorias definida pelo administrador (`categories.display_order`).
    - Inclui categorias personalizadas com livros e oculta categorias vazias.
    - Evita repetição excessiva dos mesmos livros entre prateleiras de categorias.
    - Inclui metadados de `total_count` e `filter_action` para navegação direta ("Ver todos").
    """
    shelves: List[Dict[str, Any]] = []
    try:
        all_books = get_books(sort_by="title", order="asc", user_id=user_id)
    except Exception as e:
        logger.error("Erro ao consultar livros para prateleiras de descoberta: %s", e)
        return []

    if not all_books:
        return []

    # 1. Continuar Lendo
    try:
        in_progress = [b for b in all_books if b.get("reading_status") == "in_progress"]
        in_progress.sort(key=lambda x: str(x.get("progress_updated_at") or ""), reverse=True)
        if in_progress:
            shelves.append({
                "id": "continue_reading",
                "title": "Continuar Lendo",
                "subtitle": "Retome suas leituras recentes",
                "type": "continue_reading",
                "total_count": len(in_progress),
                "filter_action": {"type": "status", "value": "in_progress"},
                "books": in_progress[:15],
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleira continue_reading: %s", e)

    # 2. Seus Favoritos (se houver)
    try:
        favs = [b for b in all_books if b.get("is_favorite")]
        if favs:
            shelves.append({
                "id": "favorites",
                "title": "Seus Favoritos",
                "subtitle": "Obras marcadas como favoritas por você",
                "type": "favorites",
                "total_count": len(favs),
                "filter_action": {"type": "status", "value": "favorites"},
                "books": favs[:15],
            })
    except Exception:
        pass

    # 3. Quero Ler (se houver)
    try:
        want_list = [b for b in all_books if b.get("want_to_read")]
        if want_list:
            shelves.append({
                "id": "want_to_read",
                "title": "Quero Ler",
                "subtitle": "Sua lista de próximas leituras",
                "type": "want_to_read",
                "total_count": len(want_list),
                "filter_action": {"type": "status", "value": "want_to_read"},
                "books": want_list[:15],
            })
    except Exception:
        pass

    # 4. Adicionados Recentemente
    try:
        recent = sorted(all_books, key=lambda x: str(x.get("created_at") or ""), reverse=True)
        if recent:
            shelves.append({
                "id": "recently_added",
                "title": "Adicionados Recentemente",
                "subtitle": "Novas aquisições da sua biblioteca",
                "type": "recently_added",
                "total_count": len(recent),
                "filter_action": {"type": "sort", "value": "recent:desc"},
                "books": recent[:15],
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleira recently_added: %s", e)

    # 5. Categorias da Biblioteca (respeitando display_order, incluindo personalizadas e evitando repetição excessiva)
    try:
        ordered_cats = list_categories(include_empty=False)
        ordered_cat_names = [c["name"] for c in ordered_cats]
        # Inclui eventuais categorias residuais que não estejam em list_categories
        for fallback_cat in TAXONOMY_TREE.keys():
            if not any(fallback_cat.lower() == c.lower() for c in ordered_cat_names):
                ordered_cat_names.append(fallback_cat)

        shown_in_category_shelves: set = set()
        for cat_name in ordered_cat_names:
            cat_books = [
                b for b in all_books
                if any(c.lower() == cat_name.lower() for c in (b.get("taxonomy") or {}).get("categories", []))
            ]
            if not cat_books:
                continue

            # Prioriza livros que ainda não apareceram em prateleiras de categorias anteriores
            # para evitar repetição excessiva nas fileiras horizontais
            unseen_books = [b for b in cat_books if b["id"] not in shown_in_category_shelves]
            seen_books = [b for b in cat_books if b["id"] in shown_in_category_shelves]
            prioritized_books = unseen_books + seen_books if unseen_books else cat_books
            shelf_selection = prioritized_books[:15]
            for sb in shelf_selection:
                shown_in_category_shelves.add(sb["id"])

            slug = re.sub(r"[^a-z0-9]+", "_", cat_name.lower()).strip("_") or "cat"
            cat_meta = next((c for c in ordered_cats if c["name"].lower() == cat_name.lower()), None)
            subtitle_text = (
                cat_meta["description"]
                if (cat_meta and cat_meta.get("description"))
                else f"{len(cat_books)} {'obra' if len(cat_books) == 1 else 'obras'} em {cat_name}"
            )
            shelves.append({
                "id": f"category_{slug}",
                "title": cat_name,
                "subtitle": subtitle_text,
                "type": "category",
                "category": cat_name,
                "is_custom": bool(cat_meta["is_custom"]) if cat_meta else False,
                "total_count": len(cat_books),
                "filter_action": {"type": "category", "value": cat_name},
                "books": shelf_selection,
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleiras de categorias: %s", e)

    # 6. Autores com mais obras no acervo (mínimo 2 obras)
    try:
        author_groups: Dict[str, List[Dict[str, Any]]] = {}
        for b in all_books:
            auth = (b.get("author") or "").strip()
            if auth and auth.lower() not in ("autor desconhecido", "autor não informado", "desconhecido"):
                author_groups.setdefault(auth, []).append(b)
        top_authors = sorted(
            [(auth, bks) for auth, bks in author_groups.items() if len(bks) >= 2],
            key=lambda item: (-len(item[1]), item[0].lower()),
        )[:3]
        for author_name, exact_author_books in top_authors:
            slug = re.sub(r"[^a-z0-9]+", "_", author_name.lower()).strip("_")[:20]
            shelves.append({
                "id": f"author_{slug}",
                "title": f"Obras de {author_name}",
                "subtitle": f"{len(exact_author_books)} livros disponíveis no acervo",
                "type": "author",
                "author": author_name,
                "total_count": len(exact_author_books),
                "filter_action": {"type": "search", "value": author_name},
                "books": exact_author_books[:15],
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleiras de autores: %s", e)

    # 7. Livros Não Iniciados
    try:
        unread = [b for b in all_books if b.get("reading_status") == "unread"]
        unread.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        if unread:
            shelves.append({
                "id": "unread",
                "title": "Para Começar a Ler",
                "subtitle": "Livros ainda não iniciados",
                "type": "unread",
                "total_count": len(unread),
                "filter_action": {"type": "status", "value": "unread"},
                "books": unread[:15],
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleira unread: %s", e)

    # 8. Livros Concluídos
    try:
        completed = [b for b in all_books if b.get("reading_status") == "completed"]
        completed.sort(key=lambda x: str(x.get("progress_updated_at") or ""), reverse=True)
        if completed:
            shelves.append({
                "id": "completed",
                "title": "Leituras Concluídas",
                "subtitle": "Livros que você já finalizou",
                "type": "completed",
                "total_count": len(completed),
                "filter_action": {"type": "status", "value": "completed"},
                "books": completed[:15],
            })
    except Exception as e:
        logger.debug("Falha ao construir prateleira completed: %s", e)

    # 9. Coleções do Usuário
    try:
        collections_map: Dict[int, Dict[str, Any]] = {}
        for b in all_books:
            for col in b.get("collections") or []:
                cid = col["id"]
                if cid not in collections_map:
                    collections_map[cid] = {"id": cid, "name": col["name"], "books": []}
                collections_map[cid]["books"].append(b)
        for cid, cdata in sorted(collections_map.items(), key=lambda x: x[1]["name"].lower()):
            if cdata["books"]:
                shelves.append({
                    "id": f"collection_{cid}",
                    "title": f"Coleção: {cdata['name']}",
                    "subtitle": f"{len(cdata['books'])} itens nesta coleção",
                    "type": "collection",
                    "collection_id": cid,
                    "total_count": len(cdata["books"]),
                    "filter_action": {"type": "collection", "value": str(cid)},
                    "books": cdata["books"][:15],
                })
    except Exception as e:
        logger.debug("Falha ao construir prateleiras de coleções: %s", e)

    # Filtro defensivo: retorna apenas prateleiras com livros
    return [s for s in shelves if s.get("books") and len(s["books"]) > 0]


def update_book_metadata(
    book_id: str,
    updates: Dict[str, Any],
    user_id: int = 1,
) -> Dict[str, Any]:
    """
    Atualiza os metadados de um livro com proteção de campos travados,
    registro de proveniência, atualização no FTS5 e persistência taxonômica.
    """
    allowed_fields = {
        "title", "author", "subtitle", "volume", "isbn10", "isbn13",
        "publisher", "publish_year", "language", "description",
        "original_title", "work_id", "edition", "identification_status",
        "reviewed_at", "reviewed_by", "last_error_reason",
    }
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
        book_row = cursor.fetchone()
        if not book_row:
            raise BookNotFoundError(f"Livro '{book_id}' não encontrado.")

        existing = dict(book_row)
        current_locks = set()
        if existing.get("manual_lock_fields"):
            try:
                current_locks = set(json.loads(existing["manual_lock_fields"]))
            except Exception:
                pass

        if "lock_fields" in updates and isinstance(updates["lock_fields"], list):
            current_locks = set(updates["lock_fields"])

        fields_to_update = {}
        for k, v in updates.items():
            if k in allowed_fields:
                fields_to_update[k] = v
                if k not in ("identification_status", "reviewed_at", "reviewed_by", "last_error_reason"):
                    current_locks.add(k)

        if "identification_status" not in updates and fields_to_update:
            fields_to_update["identification_status"] = "manual_override"

        fields_to_update["manual_lock_fields"] = json.dumps(sorted(list(current_locks)))
        source_label = updates.get("metadata_source", "manual")
        fields_to_update["metadata_source"] = source_label
        fields_to_update["metadata_updated_at"] = datetime.now().isoformat()

        set_clauses = [f"{k} = ?" for k in fields_to_update.keys()]
        values = list(fields_to_update.values())
        values.append(book_id)

        cursor.execute(
            f"UPDATE books SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )

        record_metadata_provenance(
            cursor,
            book_id,
            fields_to_update,
            source=source_label,
            confidence=100.0,
        )

        # Atualiza FTS
        final_title = fields_to_update.get("title", existing["title"])
        final_author = fields_to_update.get("author", existing["author"])
        final_desc = fields_to_update.get("description", existing["description"])
        final_pub = fields_to_update.get("publisher", existing["publisher"])
        final_isbn = fields_to_update.get("isbn13", existing["isbn13"])
        update_book_fts(cursor, book_id, final_title, final_author, final_desc, final_pub, final_isbn)

        # Atualização explícita de taxonomia se fornecida
        if "taxonomy" in updates and isinstance(updates["taxonomy"], dict):
            sync_book_taxonomy(book_id, updates["taxonomy"], is_manual=True, conn=conn)

    return get_book(book_id, user_id=user_id)


def get_library_diagnostics() -> Dict[str, Any]:
    """
    Inspeciona a biblioteca para o Painel Administrativo:
    - Livros ausentes no disco (is_missing = 1)
    - Livros com metadados incompletos (sem autor, ano, ISBN ou descrição)
    - Possíveis duplicatas (mesmo ISBN-13 ou título+autor normalizados idênticos)
    - Arquivos não reconhecidos ou corrompidos nas pastas das fontes ativas
    - Livros que requerem revisão bibliográfica
    """
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, title, author, filename, rel_path, filesize, format,
                   isbn13, publish_year, description, identification_status,
                   identification_score, last_error_reason, is_missing, source_id
            FROM books
            ORDER BY title COLLATE NOCASE ASC;
            """
        )
        all_rows = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT id, name, path, is_active, last_scanned_at, last_error FROM library_sources;")
        sources = [dict(r) for r in cursor.fetchall()]

    missing_books = [b for b in all_rows if b.get("is_missing") == 1]
    active_books = [b for b in all_rows if b.get("is_missing") == 0]

    incomplete_metadata = []
    needs_review_books = []
    by_isbn: Dict[str, List[Dict[str, Any]]] = {}
    by_norm_key: Dict[str, List[Dict[str, Any]]] = {}

    for b in active_books:
        missing_fields = []
        if not b.get("author") or not str(b["author"]).strip():
            missing_fields.append("author")
        if not b.get("isbn13"):
            missing_fields.append("isbn13")
        if not b.get("publish_year"):
            missing_fields.append("publish_year")
        if not b.get("description"):
            missing_fields.append("description")
        if missing_fields:
            incomplete_metadata.append({
                "id": b["id"],
                "title": b["title"],
                "author": b.get("author"),
                "filename": b["filename"],
                "missing_fields": missing_fields,
                "identification_status": b.get("identification_status"),
            })

        st = b.get("identification_status") or "pending"
        if st in ("needs_review", "ambiguous", "not_found", "rate_limited", "technical_error", "pending"):
            needs_review_books.append(b)

        isbn13 = (b.get("isbn13") or "").strip()
        if isbn13:
            by_isbn.setdefault(isbn13, []).append(b)

        norm_t = normalize_for_comparison(b.get("title") or "")
        norm_a = normalize_for_comparison(b.get("author") or "")
        if norm_t:
            key = f"{norm_t}::{norm_a}"
            by_norm_key.setdefault(key, []).append(b)

    duplicate_groups = []
    seen_dup_ids = set()
    for isbn, group in by_isbn.items():
        if len(group) > 1:
            ids = tuple(sorted(x["id"] for x in group))
            if ids not in seen_dup_ids:
                seen_dup_ids.add(ids)
                duplicate_groups.append({
                    "reason": f"Mesmo ISBN-13 ({isbn})",
                    "books": [{"id": x["id"], "title": x["title"], "filename": x["filename"], "filesize": x["filesize"]} for x in group],
                })
    for key, group in by_norm_key.items():
        if len(group) > 1:
            ids = tuple(sorted(x["id"] for x in group))
            if ids not in seen_dup_ids:
                seen_dup_ids.add(ids)
                duplicate_groups.append({
                    "reason": "Mesmo título e autor normalizados",
                    "books": [{"id": x["id"], "title": x["title"], "filename": x["filename"], "filesize": x["filesize"]} for x in group],
                })

    # Verifica arquivos não reconhecidos ou vazios nas pastas ativas
    unrecognized_files = []
    for src in sources:
        if not src.get("is_active"):
            continue
        src_path = Path(src["path"])
        if not src_path.exists() or not src_path.is_dir():
            continue
        try:
            for root, dirs, files in os.walk(src_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    if fname.startswith("."):
                        continue
                    ext = fname.lower().split(".")[-1] if "." in fname else ""
                    fpath = Path(root) / fname
                    try:
                        rel_p = fpath.relative_to(src_path).as_posix()
                        sz = fpath.stat().st_size
                    except OSError:
                        continue
                    if ext not in ("pdf", "epub"):
                        unrecognized_files.append({
                            "source_id": src["id"],
                            "source_name": src["name"],
                            "rel_path": rel_p,
                            "filename": fname,
                            "filesize": sz,
                            "reason": f"Formato não suportado (.{ext or 'sem_extensao'})",
                        })
                    elif sz == 0:
                        unrecognized_files.append({
                            "source_id": src["id"],
                            "source_name": src["name"],
                            "rel_path": rel_p,
                            "filename": fname,
                            "filesize": 0,
                            "reason": "Arquivo vazio (0 bytes)",
                        })
        except Exception:
            pass

    return {
        "summary": {
            "total_active_books": len(active_books),
            "missing_files_count": len(missing_books),
            "incomplete_metadata_count": len(incomplete_metadata),
            "duplicate_groups_count": len(duplicate_groups),
            "unrecognized_files_count": len(unrecognized_files),
            "needs_review_count": len(needs_review_books),
        },
        "total_active_books": len(active_books),
        "missing_books_count": len(missing_books),
        "missing_books": missing_books[:100],
        "incomplete_metadata_count": len(incomplete_metadata),
        "incomplete_metadata": incomplete_metadata[:100],
        "duplicate_groups_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups[:50],
        "unrecognized_files_count": len(unrecognized_files),
        "unrecognized_files": unrecognized_files[:100],
        "needs_review_count": len(needs_review_books),
        "needs_review_books": needs_review_books[:100],
    }


get_book_by_id = get_book


def resolve_book_path(book_id: str) -> Path:
    """
    Localiza o arquivo físico no disco a partir da fonte à qual o livro pertence.
    Aplica validações rigorosas de segurança contra path traversal e fuga por links simbólicos,
    garantindo que o arquivo está estritamente contido na fonte cadastrada e ativa.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT b.rel_path, b.is_missing, b.source_id, s.path as source_path, s.is_active as source_active
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            WHERE b.id = ?
            """,
            (book_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise BookNotFoundError(f"Livro '{book_id}' não cadastrado.")
        if row["is_missing"]:
            raise BookNotFoundError(f"Livro '{book_id}' marcado como ausente do disco.")
        if row["source_active"] is not None and not row["source_active"]:
            raise SecurityError("A fonte desta obra está temporariamente desativada.")

        rel_path = row["rel_path"]
        source_dir = Path(row["source_path"]).resolve() if row["source_path"] else config.LIBRARY_DIR.resolve()

    target_path = (source_dir / rel_path).resolve()

    # Proteção de segurança contra Path Traversal
    try:
        target_path.relative_to(source_dir)
    except ValueError:
        raise SecurityError("Tentativa de acesso ilegal a caminho fora da fonte autorizada.")

    # Revalidação rigorosa de links simbólicos: resolve(strict=True) deve estar dentro da fonte
    try:
        real_path = target_path.resolve(strict=True)
        real_path.relative_to(source_dir)
    except (ValueError, FileNotFoundError):
        with get_db() as conn:
            conn.execute("UPDATE books SET is_missing = 1 WHERE id = ?", (book_id,))
        raise BookNotFoundError("O arquivo PDF do livro não pôde ser encontrado no disco.")

    if not real_path.is_file():
        # Atualiza status no banco se o arquivo desapareceu
        with get_db() as conn:
            conn.execute("UPDATE books SET is_missing = 1 WHERE id = ?", (book_id,))
        raise BookNotFoundError("O arquivo PDF do livro não pôde ser encontrado no disco.")

    return real_path
