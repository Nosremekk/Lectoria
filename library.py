"""
Módulo de gerenciamento da biblioteca do Lectoria.
Responsável por escaneamento de arquivos PDF, extração leve de metadados,
proteção contra path traversal e consultas com filtros e ordenação.
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import pypdf

import config
from database import get_db


class SecurityError(Exception):
    """Lançada em caso de tentativa de acesso indevido fora da biblioteca."""
    pass


class BookNotFoundError(Exception):
    """Lançada quando o livro não existe no banco ou seu arquivo foi removido."""
    pass


def _extract_pdf_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extrai metadados básicos do PDF de maneira leve e tolerante a falhas.
    Lê apenas o cabeçalho e contagem de páginas sem renderizar nem carregar
    o arquivo inteiro em memória.
    """
    title = None
    author = None
    page_count = 0

    try:
        # pypdf.PdfReader lê em modo streaming sob demanda (apenas XREF e metadados)
        reader = pypdf.PdfReader(str(file_path), strict=False)
        page_count = len(reader.pages)
        if reader.metadata:
            raw_title = reader.metadata.get("/Title")
            if raw_title and isinstance(raw_title, str) and raw_title.strip():
                title = raw_title.strip()

            raw_author = reader.metadata.get("/Author")
            if raw_author and isinstance(raw_author, str) and raw_author.strip():
                author = raw_author.strip()
    except Exception:
        # Se o PDF tiver metadados corrompidos ou proteção de leitura,
        # não interrompe o scanner.
        pass

    # Se o PDF não tiver título nos metadados, usa o nome do arquivo sanitizado
    if not title:
        title = file_path.stem.replace("_", " ").replace("-", " ").strip()

    return {
        "title": title or file_path.name,
        "author": author,
        "page_count": page_count,
    }


def scan_library() -> Dict[str, int]:
    """
    Varre o diretório da biblioteca em busca de novos arquivos PDF ou modificações.
    Executado sob demanda (sem watchers ou threads de fundo consumindo CPU).
    """
    stats = {"added": 0, "updated": 0, "missing": 0, "total": 0}
    canonical_lib = config.LIBRARY_DIR.resolve()

    if not canonical_lib.exists():
        return stats

    found_rel_paths = set()

    with get_db() as conn:
        cursor = conn.cursor()

        # Mapeia livros existentes no banco por rel_path
        cursor.execute("SELECT id, rel_path, filesize, file_hash, is_missing FROM books")
        existing_books = {row["rel_path"]: dict(row) for row in cursor.fetchall()}

        # Varredura no sistema de arquivos
        for root, dirs, files in os.walk(canonical_lib):
            # Ignora pastas ocultas
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for filename in files:
                if filename.startswith(".") or not filename.lower().endswith(".pdf"):
                    continue

                full_path = (Path(root) / filename).resolve()

                # Verificação de segurança: garante que o arquivo está dentro da biblioteca
                try:
                    rel_path = full_path.relative_to(canonical_lib).as_posix()
                except ValueError:
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
                                (existing["id"],)
                            )
                            stats["updated"] += 1
                        continue

                    # Arquivo foi alterado
                    meta = _extract_pdf_metadata(full_path)
                    cursor.execute(
                        """
                        UPDATE books
                        SET title = ?, author = ?, filename = ?, filesize = ?,
                            page_count = ?, file_hash = ?, is_missing = 0, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            meta["title"],
                            meta["author"],
                            filename,
                            current_size,
                            meta["page_count"],
                            current_hash,
                            existing["id"],
                        ),
                    )
                    stats["updated"] += 1
                else:
                    # Novo livro encontrado
                    book_id = str(uuid.uuid4())
                    meta = _extract_pdf_metadata(full_path)
                    cursor.execute(
                        """
                        INSERT INTO books (id, title, author, filename, rel_path, filesize, page_count, file_hash, is_missing)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            book_id,
                            meta["title"],
                            meta["author"],
                            filename,
                            rel_path,
                            current_size,
                            meta["page_count"],
                            current_hash,
                        ),
                    )
                    stats["added"] += 1

        # Marca como ausente qualquer livro no banco que não esteja mais no disco
        for rel_path, data in existing_books.items():
            if rel_path not in found_rel_paths and data["is_missing"] == 0:
                cursor.execute(
                    "UPDATE books SET is_missing = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (data["id"],)
                )
                stats["missing"] += 1

        cursor.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0")
        stats["total"] = cursor.fetchone()[0]

    return stats


def get_books(
    search: Optional[str] = None,
    sort_by: str = "title",
    order: str = "asc",
    status_filter: Optional[str] = None,
    collection_id: Optional[int] = None,
    user_id: int = 1,
) -> List[Dict[str, Any]]:
    """
    Retorna a lista de livros disponíveis, enriquecida com o progresso de leitura
    e coleções associadas. Suporta busca, filtros de status e ordenação flexível.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        sql = """
            SELECT
                b.id,
                b.title,
                b.author,
                b.filename,
                b.filesize,
                b.page_count,
                b.created_at,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.updated_at as progress_updated_at
            FROM books b
            LEFT JOIN reading_progress p
                ON b.id = p.book_id AND p.user_id = ?
            WHERE b.is_missing = 0
        """
        params: List[Any] = [user_id]

        # Filtro de busca textual simples e rápida por título ou autor
        if search and search.strip():
            query_term = f"%{search.strip()}%"
            sql += " AND (b.title LIKE ? OR b.author LIKE ?)"
            params.extend([query_term, query_term])

        # Filtro por coleção
        if collection_id is not None:
            sql += " AND b.id IN (SELECT book_id FROM collection_books WHERE collection_id = ?)"
            params.append(collection_id)

        # Filtro por status de leitura
        if status_filter == "in_progress":
            sql += " AND (p.current_page > 1 AND COALESCE(p.percentage, 0) < 100)"
        elif status_filter == "unread":
            sql += " AND (p.current_page IS NULL OR (p.current_page = 1 AND COALESCE(p.percentage, 0) = 0))"
        elif status_filter == "completed":
            sql += " AND (COALESCE(p.percentage, 0) >= 100 OR (b.page_count > 0 AND p.current_page >= b.page_count))"

        # Validação estrita de ordenação para prevenir injeção SQL
        order_clause = "DESC" if order.lower() == "desc" else "ASC"
        if sort_by == "recent":
            sql += f" ORDER BY b.created_at {order_clause}"
        elif sort_by == "author":
            sql += f" ORDER BY COALESCE(b.author, '') COLLATE NOCASE {order_clause}, b.title COLLATE NOCASE ASC"
        elif sort_by == "last_read":
            # Livros lidos mais recentemente primeiro (nulos ao final)
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

        # Anexa as coleções de cada livro de maneira eficiente
        if books:
            book_ids = [b["id"] for b in books]
            placeholders = ",".join("?" for _ in book_ids)
            cursor.execute(
                f"""
                SELECT cb.book_id, c.id, c.name
                FROM collections c
                INNER JOIN collection_books cb ON c.id = cb.collection_id
                WHERE cb.book_id IN ({placeholders})
                ORDER BY c.name COLLATE NOCASE ASC
                """,
                book_ids,
            )
            coll_map: Dict[str, List[Dict[str, Any]]] = {}
            for row in cursor.fetchall():
                b_id = row["book_id"]
                if b_id not in coll_map:
                    coll_map[b_id] = []
                coll_map[b_id].append({"id": row["id"], "name": row["name"]})

            for b in books:
                b["collections"] = coll_map.get(b["id"], [])

        return books


def get_book(book_id: str, user_id: int = 1) -> Dict[str, Any]:
    """Retorna detalhes de um único livro pelo seu ID estável."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                b.id,
                b.title,
                b.author,
                b.filename,
                b.filesize,
                b.page_count,
                b.is_missing,
                b.created_at,
                p.current_page,
                p.total_pages as progress_total_pages,
                p.percentage as progress_percentage,
                p.updated_at as progress_updated_at
            FROM books b
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
        if data["is_missing"]:
            raise BookNotFoundError(f"O arquivo do livro '{book_id}' não está disponível no disco.")

        cursor.execute(
            """
            SELECT c.id, c.name, c.description
            FROM collections c
            INNER JOIN collection_books cb ON c.id = cb.collection_id
            WHERE cb.book_id = ?
            ORDER BY c.name COLLATE NOCASE ASC
            """,
            (book_id,),
        )
        data["collections"] = [dict(c) for c in cursor.fetchall()]
        return data


def resolve_book_path(book_id: str) -> Path:
    """
    Localiza o arquivo físico no disco e aplica validações rigorosas de segurança
    contra path traversal, garantindo que o arquivo está restrito à LIBRARY_DIR.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT rel_path, is_missing FROM books WHERE id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise BookNotFoundError(f"Livro '{book_id}' não cadastrado.")
        if row["is_missing"]:
            raise BookNotFoundError(f"Livro '{book_id}' marcado como ausente do disco.")

        rel_path = row["rel_path"]

    canonical_lib = config.LIBRARY_DIR.resolve()
    target_path = (canonical_lib / rel_path).resolve()

    # Proteção de segurança contra Path Traversal
    try:
        target_path.relative_to(canonical_lib)
    except ValueError:
        raise SecurityError("Tentativa de acesso ilegal a caminho fora da biblioteca.")

    if not target_path.is_file():
        # Atualiza status no banco se o arquivo desapareceu
        with get_db() as conn:
            conn.execute("UPDATE books SET is_missing = 1 WHERE id = ?", (book_id,))
        raise BookNotFoundError("O arquivo PDF do livro não pôde ser encontrado no disco.")

    return target_path
