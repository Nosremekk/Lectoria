"""
Testes adicionais da Iteração 2: validação de coleções em lote, entradas inválidas e PDFs com muitas páginas.
"""

from tests.conftest import create_sample_pdf
from library import scan_library
import config


def test_batch_book_collections_assignment(client):
    # Cria duas coleções
    c1 = client.post("/api/collections", json={"name": "Ciência e Filosofia"}).json()["collection"]
    c2 = client.post("/api/collections", json={"name": "História Geral"}).json()["collection"]

    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]

    # Atribui ambas ao livro
    assign_res = client.put(
        f"/api/books/{book_id}/collections",
        json={"collection_ids": [c1["id"], c2["id"]]},
    )
    assert assign_res.status_code == 200
    assigned_cols = assign_res.json()["collections"]
    assert len(assigned_cols) == 2
    assigned_names = [c["name"] for c in assigned_cols]
    assert "Ciência e Filosofia" in assigned_names
    assert "História Geral" in assigned_names

    # Recupera o livro individualmente e verifica a presença das coleções
    book_res = client.get(f"/api/books/{book_id}")
    assert len(book_res.json()["collections"]) == 2

    # Remove uma das coleções via lote
    update_res = client.put(
        f"/api/books/{book_id}/collections",
        json={"collection_ids": [c1["id"]]},
    )
    assert update_res.status_code == 200
    assert len(update_res.json()["collections"]) == 1


def test_validation_errors(client):
    # Nome de coleção vazio
    empty_res = client.post("/api/collections", json={"name": "   "})
    assert empty_res.status_code == 422 or empty_res.status_code == 400

    # ID de livro inexistente ao atribuir coleções
    invalid_book_res = client.put(
        "/api/books/non-existent-uuid/collections",
        json={"collection_ids": [1]},
    )
    assert invalid_book_res.status_code == 404

    # ID de coleção inexistente
    invalid_col_res = client.get("/api/collections/999999")
    assert invalid_col_res.status_code == 404


def test_large_pdf_simulation(client):
    """Simula livro com muitas páginas para garantir estabilidade e ausência de estouro de memória."""
    large_pdf_file = config.LIBRARY_DIR / "livro_grande_volume.pdf"
    create_sample_pdf(large_pdf_file, "Grande Enciclopédia", "Vários Autores", pages=120)
    scan_library()

    res = client.get("/api/books?search=Enciclopédia")
    assert res.status_code == 200
    book = res.json()["books"][0]
    assert book["page_count"] == 120

    # Range request na última parte do PDF
    file_res = client.get(
        f"/api/books/{book['id']}/file",
        headers={"Range": "bytes=100-200"},
    )
    assert file_res.status_code == 206
    assert len(file_res.content) == 101

    # Salva progresso na página 95
    prog_res = client.post(
        f"/api/books/{book['id']}/progress",
        json={"current_page": 95, "total_pages": 120},
    )
    assert prog_res.status_code == 200
    assert prog_res.json()["progress"]["current_page"] == 95
    assert prog_res.json()["progress"]["percentage"] > 79.0

