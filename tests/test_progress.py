"""
Testes de salvamento automático e recuperação de progresso de leitura.
"""

def test_save_and_restore_progress(client):
    books = client.get("/api/books").json()["books"]
    book = books[0]
    book_id = book["id"]

    # Verifica progresso inicial padrão
    init_res = client.get(f"/api/books/{book_id}/progress")
    assert init_res.status_code == 200
    assert init_res.json()["current_page"] == 1

    # Atualiza progresso para página 5
    save_res = client.post(
        f"/api/books/{book_id}/progress",
        json={"current_page": 5, "total_pages": 10},
    )
    assert save_res.status_code == 200
    assert save_res.json()["status"] == "success"

    # Recupera progresso e verifica se foi persistido corretamente no SQLite
    get_res = client.get(f"/api/books/{book_id}/progress")
    assert get_res.status_code == 200
    prog = get_res.json()
    assert prog["current_page"] == 5
    assert prog["total_pages"] == 10
    assert prog["percentage"] == 50.0

    # Verifica se a biblioteca agora reflete o progresso do livro
    lib_res = client.get("/api/books")
    updated_book = [b for b in lib_res.json()["books"] if b["id"] == book_id][0]
    assert updated_book["current_page"] == 5
    assert updated_book["progress_percentage"] == 50.0


def test_save_progress_beacon_plain_json(client):
    """Testa o envio de progresso via sendBeacon (Content-Type text/plain ou application/json)."""
    books = client.get("/api/books").json()["books"]
    book = books[1]
    book_id = book["id"]

    # Simula envio de beacon com string JSON
    beacon_payload = '{"current_page": 7, "total_pages": 15}'
    res = client.post(
        f"/api/books/{book_id}/progress",
        content=beacon_payload,
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )
    assert res.status_code == 200

    get_res = client.get(f"/api/books/{book_id}/progress")
    assert get_res.json()["current_page"] == 7

