"""
Módulo de gerenciamento de coleções e categorias do Lectoria.
Permite criar, editar, listar e remover coleções, bem como associar livros a coleções.
"""

from typing import Any, Dict, List, Optional
from database import get_db


class CollectionNotFoundError(Exception):
    """Lançada quando uma coleção não for encontrada."""
    pass


class CollectionAlreadyExistsError(Exception):
    """Lançada quando já existe uma coleção com o mesmo nome."""
    pass


def list_collections() -> List[Dict[str, Any]]:
    """Retorna todas as coleções com a quantidade de livros associados."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.id,
                c.name,
                c.description,
                c.created_at,
                COUNT(cb.book_id) as book_count
            FROM collections c
            LEFT JOIN collection_books cb ON c.id = cb.collection_id
            LEFT JOIN books b ON cb.book_id = b.id AND b.is_missing = 0
            GROUP BY c.id
            ORDER BY c.name COLLATE NOCASE ASC
            """
        )
        return [dict(r) for r in cursor.fetchall()]


def get_collection(collection_id: int) -> Dict[str, Any]:
    """Retorna os dados de uma coleção específica."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.id,
                c.name,
                c.description,
                c.created_at,
                COUNT(cb.book_id) as book_count
            FROM collections c
            LEFT JOIN collection_books cb ON c.id = cb.collection_id
            LEFT JOIN books b ON cb.book_id = b.id AND b.is_missing = 0
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (collection_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise CollectionNotFoundError(f"Coleção #{collection_id} não encontrada.")
        return dict(row)


def create_collection(name: str, description: Optional[str] = None) -> Dict[str, Any]:
    """Cria uma nova coleção no banco de dados."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("O nome da coleção não pode ser vazio.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM collections WHERE name = ? COLLATE NOCASE", (clean_name,))
        if cursor.fetchone():
            raise CollectionAlreadyExistsError(f"Já existe uma coleção com o nome '{clean_name}'.")

        cursor.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            (clean_name, description.strip() if description else None),
        )
        collection_id = cursor.lastrowid
        return {
            "id": collection_id,
            "name": clean_name,
            "description": description.strip() if description else None,
            "book_count": 0,
        }


def update_collection(collection_id: int, name: str, description: Optional[str] = None) -> Dict[str, Any]:
    """Atualiza o nome e/ou descrição de uma coleção existente."""
    clean_name = name.strip()
    clean_desc = description.strip() if description else None
    if not clean_name:
        raise ValueError("O nome da coleção não pode ser vazio.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM collections WHERE id = ?", (collection_id,))
        if not cursor.fetchone():
            raise CollectionNotFoundError(f"Coleção #{collection_id} não encontrada.")

        cursor.execute(
            "SELECT id FROM collections WHERE name = ? COLLATE NOCASE AND id != ?",
            (clean_name, collection_id),
        )
        if cursor.fetchone():
            raise CollectionAlreadyExistsError(f"Já existe outra coleção com o nome '{clean_name}'.")

        cursor.execute(
            "UPDATE collections SET name = ?, description = ? WHERE id = ?",
            (clean_name, clean_desc, collection_id),
        )

        cursor.execute(
            """
            SELECT
                c.id,
                c.name,
                c.description,
                c.created_at,
                COUNT(cb.book_id) as book_count
            FROM collections c
            LEFT JOIN collection_books cb ON c.id = cb.collection_id
            LEFT JOIN books b ON cb.book_id = b.id AND b.is_missing = 0
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (collection_id,),
        )
        return dict(cursor.fetchone())


def delete_collection(collection_id: int) -> bool:
    """Exclui uma coleção sem apagar nenhum documento PDF do sistema de arquivos."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM collections WHERE id = ?", (collection_id,))
        if not cursor.fetchone():
            raise CollectionNotFoundError(f"Coleção #{collection_id} não encontrada.")

        cursor.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return True


def add_book_to_collection(collection_id: int, book_id: str) -> bool:
    """Associa um livro a uma coleção."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Valida existência de ambos
        cursor.execute("SELECT id FROM collections WHERE id = ?", (collection_id,))
        if not cursor.fetchone():
            raise CollectionNotFoundError(f"Coleção #{collection_id} não encontrada.")

        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro '{book_id}' não encontrado.")

        cursor.execute(
            """
            INSERT OR IGNORE INTO collection_books (collection_id, book_id)
            VALUES (?, ?)
            """,
            (collection_id, book_id),
        )
        return True


def remove_book_from_collection(collection_id: int, book_id: str) -> bool:
    """Remove a associação de um livro com uma coleção sem apagar o arquivo do livro."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM collection_books WHERE collection_id = ? AND book_id = ?",
            (collection_id, book_id),
        )
        return cursor.rowcount > 0


def get_book_collections(book_id: str) -> List[Dict[str, Any]]:
    """Retorna todas as coleções às quais um livro pertence."""
    with get_db() as conn:
        cursor = conn.cursor()
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
        return [dict(r) for r in cursor.fetchall()]


def set_book_collections(book_id: str, collection_ids: List[int]) -> List[Dict[str, Any]]:
    """Atualiza em lote todas as coleções de um livro."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM books WHERE id = ?", (book_id,))
        if not cursor.fetchone():
            raise ValueError(f"Livro '{book_id}' não encontrado.")

        # Remove todas as associações atuais
        cursor.execute("DELETE FROM collection_books WHERE book_id = ?", (book_id,))

        # Insere as novas associações válidas
        for cid in collection_ids:
            cursor.execute("SELECT id FROM collections WHERE id = ?", (cid,))
            if cursor.fetchone():
                cursor.execute(
                    "INSERT OR IGNORE INTO collection_books (collection_id, book_id) VALUES (?, ?)",
                    (cid, book_id),
                )

    return get_book_collections(book_id)
