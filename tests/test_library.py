"""
Testes de escaneamento, pesquisa e listagem da biblioteca.
"""

from library import scan_library, get_books, get_book
from tests.conftest import create_sample_pdf
import config


def test_scan_and_list_books(client):
    # Cria um novo livro dinâmico no disco para testar a adição via scan
    novo_livro = config.LIBRARY_DIR / "livro_novo.pdf"
    create_sample_pdf(novo_livro, "Livro Novo Adicionado", "Clarice Lispector", pages=6)

    # Executa o escaneamento
    stats = scan_library()
    assert stats["total"] >= 4
    assert stats["added"] >= 1

    # Testa listagem geral
    res = client.get("/api/books")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 4
    titles = [b["title"] for b in data["books"]]
    assert "Dom Casmurro" in titles
    assert "Memórias Póstumas" in titles
    assert "Ficções" in titles
    assert "Livro Novo Adicionado" in titles


def test_search_books_by_title(client):
    res = client.get("/api/books?search=Casmurro")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["books"][0]["title"] == "Dom Casmurro"
    assert data["books"][0]["author"] == "Machado de Assis"


def test_sort_books(client):
    res = client.get("/api/books?sort_by=title&order=asc")
    assert res.status_code == 200
    titles = [b["title"] for b in res.json()["books"]]
    assert titles == sorted(titles)

    res_desc = client.get("/api/books?sort_by=title&order=desc")
    assert res_desc.status_code == 200
    titles_desc = [b["title"] for b in res_desc.json()["books"]]
    assert titles_desc == sorted(titles, reverse=True)


def test_get_individual_book(client):
    books = client.get("/api/books").json()["books"]
    book = books[0]

    res = client.get(f"/api/books/{book['id']}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["id"] == book["id"]
    assert detail["title"] == book["title"]
    assert detail["page_count"] > 0
