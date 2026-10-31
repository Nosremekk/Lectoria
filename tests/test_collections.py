"""
Testes de coleções e categorias da biblioteca.
"""


def test_collections_crud(client):
    # Lista inicial vazia ou com coleções existentes
    res = client.get("/api/collections")
    assert res.status_code == 200
    initial_count = len(res.json()["collections"])

    # Cria nova coleção
    create_res = client.post(
        "/api/collections",
        json={"name": "Literatura Brasileira", "description": "Clássicos nacionais"},
    )
    assert create_res.status_code == 201
    coll = create_res.json()["collection"]
    assert coll["name"] == "Literatura Brasileira"
    coll_id = coll["id"]

    # Tentativa de criar coleção duplicada (deve falhar com 409 Conflict)
    dup_res = client.post(
        "/api/collections",
        json={"name": "literatura brasileira"},
    )
    assert dup_res.status_code == 409

    # Edita coleção
    update_res = client.put(
        f"/api/collections/{coll_id}",
        json={"name": "Ficção Brasileira", "description": "Obras renomadas"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["collection"]["name"] == "Ficção Brasileira"

    # Associa um livro à coleção
    books = client.get("/api/books").json()["books"]
    book = books[0]
    book_id = book["id"]

    add_res = client.post(
        f"/api/collections/{coll_id}/books",
        json={"book_id": book_id},
    )
    assert add_res.status_code == 200

    # Verifica se a contagem na coleção aumentou para 1
    detail_res = client.get(f"/api/collections/{coll_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["book_count"] == 1

    # Filtra livros por esta coleção
    filtered_res = client.get(f"/api/books?collection_id={coll_id}")
    assert filtered_res.status_code == 200
    filtered_books = filtered_res.json()["books"]
    assert len(filtered_books) == 1
    assert filtered_books[0]["id"] == book_id
    assert any(c["id"] == coll_id for c in filtered_books[0]["collections"])

    # Remove o livro da coleção
    remove_res = client.delete(f"/api/collections/{coll_id}/books/{book_id}")
    assert remove_res.status_code == 200
    assert remove_res.json()["removed"] is True

    # Verifica contagem zerada
    detail_res_after = client.get(f"/api/collections/{coll_id}")
    assert detail_res_after.json()["book_count"] == 0

    # Exclui a coleção
    del_res = client.delete(f"/api/collections/{coll_id}")
    assert del_res.status_code == 200

    # Confirma exclusão
    assert client.get(f"/api/collections/{coll_id}").status_code == 404

