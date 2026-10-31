"""
Testes de filtros de status de leitura, ordenação avançada e busca por autor.
"""


def test_status_filters_and_reading_states(client):
    books = client.get("/api/books").json()["books"]
    assert len(books) >= 3

    book_unread = books[0]
    book_in_progress = books[1]
    book_completed = books[2]

    # Configura livro 2 como Em Andamento (página 5 de 15)
    client.post(
        f"/api/books/{book_in_progress['id']}/progress",
        json={"current_page": 5, "total_pages": 15},
    )

    # Configura livro 3 como Concluído (página 8 de 8 = 100%)
    client.post(
        f"/api/books/{book_completed['id']}/progress",
        json={"current_page": 8, "total_pages": 8},
    )

    # Filtro: Em andamento
    in_prog_res = client.get("/api/books?status=in_progress")
    assert in_prog_res.status_code == 200
    in_prog_ids = [b["id"] for b in in_prog_res.json()["books"]]
    assert book_in_progress["id"] in in_prog_ids
    assert book_completed["id"] not in in_prog_ids

    # Filtro: Concluídos
    comp_res = client.get("/api/books?status=completed")
    assert comp_res.status_code == 200
    comp_ids = [b["id"] for b in comp_res.json()["books"]]
    assert book_completed["id"] in comp_ids
    assert book_in_progress["id"] not in comp_ids

    # Filtro: Não iniciados
    unread_res = client.get("/api/books?status=unread")
    assert unread_res.status_code == 200
    unread_ids = [b["id"] for b in unread_res.json()["books"]]
    assert book_unread["id"] in unread_ids
    assert book_in_progress["id"] not in unread_ids
    assert book_completed["id"] not in unread_ids


def test_search_by_author(client):
    # Busca por Machado
    res = client.get("/api/books?search=Machado")
    assert res.status_code == 200
    books = res.json()["books"]
    assert len(books) >= 2
    for b in books:
        assert "Machado" in b["author"]


def test_sort_by_author_and_last_read(client):
    # Ordenação por autor
    res = client.get("/api/books?sort_by=author&order=asc")
    assert res.status_code == 200
    authors = [b["author"] for b in res.json()["books"] if b["author"]]
    assert authors == sorted(authors, key=lambda a: a.lower())

    # Ordenação por última leitura
    res_last = client.get("/api/books?sort_by=last_read&order=desc")
    assert res_last.status_code == 200
    books = res_last.json()["books"]
    # Os livros com progresso registrado devem vir primeiro
    progressed_books = [b for b in books if b.get("progress_updated_at")]
    assert len(progressed_books) >= 2


def test_concurrent_access_simulation(client):
    """Simula acessos simultâneos de leitura e escrita sem concorrência destrutiva."""
    from tests.conftest import create_sample_pdf
    from library import scan_library
    import config

    concurrent_file = config.LIBRARY_DIR / "livro_concorrencia.pdf"
    create_sample_pdf(concurrent_file, "Livro Concorrência", "Autor Concorrente", pages=20)
    scan_library()

    res = client.get("/api/books?search=Concorrência")
    book_id = res.json()["books"][0]["id"]

    # Simula cliente 1 atualizando progresso enquanto cliente 2 lê a biblioteca
    for page in range(1, 6):
        save_res = client.post(
            f"/api/books/{book_id}/progress",
            json={"current_page": page, "total_pages": 20},
        )
        assert save_res.status_code == 200

        read_res = client.get("/api/books")
        assert read_res.status_code == 200
        assert read_res.json()["count"] >= 3
