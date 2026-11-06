"""
Módulo de gerenciamento da biblioteca do Lectoria.
Responsável por escaneamento de arquivos PDF e EPUB, extração avançada de metadados,
proteção contra path traversal, consultas com filtros multidimensionais, busca FTS5,
geração de prateleiras de descoberta e sincronização taxonômica.
"""

from datetime import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import pypdf

import config
from database import get_db
from metadata_service import (
    extract_epub_metadata_advanced,
    extract_pdf_metadata_advanced,
)
from taxonomy_service import (
    map_raw_subjects_to_taxonomy,
    sync_book_taxonomy,
    get_book_taxonomy,
    TAXONOMY_TREE,
)


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
    """
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
                       title, author, isbn10, isbn13, publisher, publish_year, language,
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
                                SET title = ?, author = ?, filename = ?, filesize = ?,
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
                                SET title = ?, author = ?, filename = ?, filesize = ?,
                                    page_count = ?, file_hash = ?, format = ?, is_missing = 0,
                                    isbn10 = ?, isbn13 = ?, publisher = ?, publish_year = ?,
                                    language = ?, description = ?, edition = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                                """,
                                (
                                    title_to_set,
                                    author_to_set,
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
                                id, source_id, title, author, filename, rel_path, filesize,
                                page_count, file_hash, format, is_missing, isbn10, isbn13,
                                publisher, publish_year, language, description, edition,
                                identification_status, identification_score, metadata_source,
                                manual_lock_fields
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 'pending', 0.0, 'embedded', '[]')
                            """,
                            (
                                book_id,
                                src_id,
                                meta["title"],
                                meta["author"],
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
    user_id: int = 1,
) -> List[Dict[str, Any]]:
    """
    Retorna a lista de livros disponíveis, enriquecida com progresso, coleções
    e taxonomia multidimensional. Suporta filtros combinados e busca FTS5.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        sql = """
            SELECT
                b.id,
                b.source_id,
                b.title,
                b.author,
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
                b.created_at,
                s.name as source_name,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.progress_locator,
                p.updated_at as progress_updated_at
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p
                ON b.id = p.book_id AND p.user_id = ?
            WHERE b.is_missing = 0 AND (s.is_active = 1 OR s.is_active IS NULL)
        """
        params: List[Any] = [user_id]

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

        # Filtros de taxonomia
        if category_filter:
            sql += """ AND b.id IN (
                SELECT bs.book_id FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE s.category = ?
            )"""
            params.append(category_filter)

        if subcategory_filter:
            sql += """ AND b.id IN (
                SELECT bs.book_id FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE s.subcategory = ?
            )"""
            params.append(subcategory_filter)

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

        # Filtro por status de leitura
        if status_filter == "in_progress":
            sql += " AND (p.current_page > 1 AND COALESCE(p.percentage, 0) < 100)"
        elif status_filter == "unread":
            sql += " AND (p.current_page IS NULL OR (p.current_page = 1 AND COALESCE(p.percentage, 0) = 0))"
        elif status_filter == "completed":
            sql += " AND (COALESCE(p.percentage, 0) >= 100 OR (b.page_count > 0 AND p.current_page >= b.page_count))"

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

            # Anexa taxonomia em lote (evita N+1 queries)
            cursor.execute(
                f"""
                SELECT bs.book_id, s.name, s.category, s.subcategory, s.genre, s.origin, s.period
                FROM book_subjects bs
                JOIN subjects s ON bs.subject_id = s.id
                WHERE bs.book_id IN ({placeholders})
                ORDER BY s.category, s.name
                """,
                book_ids,
            )
            tax_map: Dict[str, Dict[str, Any]] = {
                bid: {"categories": set(), "subcategories": set(), "genres": set(), "origins": set(), "subjects": []}
                for bid in book_ids
            }
            for row in cursor.fetchall():
                bid = row["book_id"]
                if row["category"]:
                    tax_map[bid]["categories"].add(row["category"])
                if row["subcategory"]:
                    tax_map[bid]["subcategories"].add(row["subcategory"])
                if row["genre"]:
                    tax_map[bid]["genres"].add(row["genre"])
                if row["origin"]:
                    tax_map[bid]["origins"].add(row["origin"])
                tax_map[bid]["subjects"].append(row["name"])

            for b in books:
                b["collections"] = coll_map.get(b["id"], [])
                tinfo = tax_map.get(
                    b["id"],
                    {"categories": set(), "subcategories": set(), "genres": set(), "origins": set(), "subjects": []},
                )
                b["taxonomy"] = {
                    "categories": sorted(list(tinfo["categories"])),
                    "subcategories": sorted(list(tinfo["subcategories"])),
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
                b.is_missing,
                b.created_at,
                s.name as source_name,
                s.is_active as source_active,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.progress_locator,
                p.updated_at as progress_updated_at
            FROM books b
            LEFT JOIN library_sources s ON b.source_id = s.id
            LEFT JOIN reading_progress p
                ON b.id = p.book_id AND p.user_id = ?
            WHERE b.id = ?
            """,
            (user_id, book_id),
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

        locks = data.get("manual_lock_fields")
        if isinstance(locks, str):
            try:
                data["manual_lock_fields"] = json.loads(locks)
            except Exception:
                data["manual_lock_fields"] = []
        elif not locks:
            data["manual_lock_fields"] = []

        data["taxonomy"] = get_book_taxonomy(book_id)
        return data


def get_discovery_shelves(user_id: int = 1) -> List[Dict[str, Any]]:
    """
    Constrói prateleiras dinâmicas horizontais inspiradas em plataformas de streaming.
    Garante categoricamente que nenhuma prateleira vazia seja retornada.
    """
    shelves: List[Dict[str, Any]] = []

    # 1. Continuar Lendo
    in_progress = get_books(status_filter="in_progress", sort_by="last_read", order="desc", user_id=user_id)
    if in_progress:
        shelves.append({
            "id": "continue_reading",
            "title": "Continuar Lendo",
            "subtitle": "Retome suas leituras recentes",
            "type": "continue_reading",
            "books": in_progress[:15],
        })

    # 2. Adicionados Recentemente
    recent = get_books(sort_by="recent", order="desc", user_id=user_id)
    if recent:
        shelves.append({
            "id": "recently_added",
            "title": "Adicionados Recentemente",
            "subtitle": "Novas aquisições da sua biblioteca",
            "type": "recently_added",
            "books": recent[:15],
        })

    # 3. Categorias Principais da Taxonomia
    for cat_name in TAXONOMY_TREE.keys():
        cat_books = get_books(category_filter=cat_name, sort_by="title", order="asc", user_id=user_id)
        if cat_books:
            slug = cat_name.lower().replace(" ", "_").replace("ç", "c").replace("ã", "a")
            shelves.append({
                "id": f"category_{slug}",
                "title": cat_name,
                "subtitle": f"Obras no acervo de {cat_name}",
                "type": "category",
                "category": cat_name,
                "books": cat_books[:15],
            })

    # 4. Autores com mais obras no acervo (mínimo 2 obras)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT author, COUNT(*) as cnt
            FROM books
            WHERE is_missing = 0 AND author IS NOT NULL AND TRIM(author) != ''
            GROUP BY author
            HAVING cnt >= 2
            ORDER BY cnt DESC, author ASC
            LIMIT 3
            """
        )
        top_authors = cursor.fetchall()
        for row in top_authors:
            author_name = row["author"]
            author_books = get_books(search=author_name, sort_by="title", order="asc", user_id=user_id)
            exact_author_books = [b for b in author_books if b.get("author") == author_name]
            if exact_author_books:
                slug = author_name.lower().replace(" ", "_")[:20]
                shelves.append({
                    "id": f"author_{slug}",
                    "title": f"Obras de {author_name}",
                    "subtitle": f"{len(exact_author_books)} livros disponíveis no acervo",
                    "type": "author",
                    "author": author_name,
                    "books": exact_author_books[:15],
                })

    # 5. Livros Não Iniciados
    unread = get_books(status_filter="unread", sort_by="recent", order="desc", user_id=user_id)
    if unread:
        shelves.append({
            "id": "unread",
            "title": "Para Começar a Ler",
            "subtitle": "Livros ainda não iniciados",
            "type": "unread",
            "books": unread[:15],
        })

    # 6. Livros Concluídos
    completed = get_books(status_filter="completed", sort_by="last_read", order="desc", user_id=user_id)
    if completed:
        shelves.append({
            "id": "completed",
            "title": "Leituras Concluídas",
            "subtitle": "Livros que você já finalizou",
            "type": "completed",
            "books": completed[:15],
        })

    # 7. Coleções do Usuário
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM collections WHERE user_id = ? ORDER BY name ASC", (user_id,))
        for col_row in cursor.fetchall():
            col_books = get_books(collection_id=col_row["id"], user_id=user_id)
            if col_books:
                shelves.append({
                    "id": f"collection_{col_row['id']}",
                    "title": f"Coleção: {col_row['name']}",
                    "subtitle": f"{len(col_books)} itens nesta coleção",
                    "type": "collection",
                    "collection_id": col_row["id"],
                    "books": col_books[:15],
                })

    # Filtro defensivo: retorna apenas prateleiras com livros
    return [s for s in shelves if s.get("books") and len(s["books"]) > 0]


def update_book_metadata(
    book_id: str,
    updates: Dict[str, Any],
    user_id: int = 1,
) -> Dict[str, Any]:
    """
    Atualiza os metadados de um livro com proteção de campos travados,
    atualização no FTS5 e persistência taxonômica.
    """
    allowed_fields = {
        "title", "author", "isbn10", "isbn13", "publisher", "publish_year",
        "language", "description", "original_title", "work_id", "edition",
        "identification_status",
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
                current_locks.add(k)

        if "identification_status" not in updates and fields_to_update:
            fields_to_update["identification_status"] = "manual_override"

        fields_to_update["manual_lock_fields"] = json.dumps(sorted(list(current_locks)))
        fields_to_update["metadata_source"] = updates.get("metadata_source", "manual")
        fields_to_update["metadata_updated_at"] = datetime.now().isoformat()

        set_clauses = [f"{k} = ?" for k in fields_to_update.keys()]
        values = list(fields_to_update.values())
        values.append(book_id)

        cursor.execute(
            f"UPDATE books SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
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
