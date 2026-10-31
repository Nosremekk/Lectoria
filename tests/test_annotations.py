"""
Testes de destaques (highlights), anotações textuais e desenhos livres vetoriais.
"""


def test_highlights_crud(client):
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]

    # 1. Criação de destaque com coordenadas normalizadas
    highlight_payload = {
        "page": 2,
        "color": "#fef08a",
        "rects": [
            {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.03},
            {"x": 0.1, "y": 0.23, "w": 0.4, "h": 0.03},
        ],
        "selected_text": "Este é um trecho de texto destacado para estudos.",
        "text_context": "Capítulo 1: Este é um trecho de texto destacado para estudos. E continua...",
    }

    create_res = client.post(f"/api/books/{book_id}/highlights", json=highlight_payload)
    assert create_res.status_code == 201
    h_data = create_res.json()["highlight"]
    assert h_data["page"] == 2
    assert h_data["color"] == "#fef08a"
    assert len(h_data["rects"]) == 2
    assert h_data["selected_text"] == highlight_payload["selected_text"]
    highlight_id = h_data["id"]

    # 2. Listagem de destaques da página
    page_res = client.get(f"/api/books/{book_id}/pages/2/annotations")
    assert page_res.status_code == 200
    p_highlights = page_res.json()["highlights"]
    assert len(p_highlights) == 1
    assert p_highlights[0]["id"] == highlight_id

    # 3. Listagem geral do livro
    all_res = client.get(f"/api/books/{book_id}/annotations")
    assert all_res.status_code == 200
    assert all_res.json()["counts"]["highlights"] >= 1

    # 4. Remoção do destaque
    del_res = client.delete(f"/api/books/{book_id}/highlights/{highlight_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

    # Confirma remoção
    page_res_after = client.get(f"/api/books/{book_id}/pages/2/annotations")
    assert len(page_res_after.json()["highlights"]) == 0


def test_notes_crud_and_link_to_highlight(client):
    books = client.get("/api/books").json()["books"]
    book_id = books[1]["id"]

    # Cria um destaque primeiro
    h_res = client.post(
        f"/api/books/{book_id}/highlights",
        json={
            "page": 1,
            "color": "#a7f3d0",
            "rects": [{"x": 0.15, "y": 0.3, "w": 0.6, "h": 0.04}],
            "selected_text": "Trecho para vincular comentário.",
        },
    )
    assert h_res.status_code == 201
    highlight_id = h_res.json()["highlight"]["id"]

    # Cria uma nota vinculada ao destaque
    note_payload = {
        "page": 1,
        "content": "Comentário importante sobre o conceito filosófico.",
        "color": "#a7f3d0",
        "highlight_id": highlight_id,
    }
    create_note_res = client.post(f"/api/books/{book_id}/notes", json=note_payload)
    assert create_note_res.status_code == 201
    note_data = create_note_res.json()["note"]
    assert note_data["content"] == note_payload["content"]
    assert note_data["highlight_id"] == highlight_id
    note_id = note_data["id"]

    # Cria uma nota avulsa na página sem destaque vinculado
    page_note_payload = {
        "page": 3,
        "content": "Ideia geral sobre o capítulo 2.",
        "color": "#bae6fd",
    }
    page_note_res = client.post(f"/api/books/{book_id}/notes", json=page_note_payload)
    assert page_note_res.status_code == 201
    assert page_note_res.json()["note"]["highlight_id"] is None

    # Edição da nota
    edit_res = client.put(
        f"/api/books/{book_id}/notes/{note_id}",
        json={"content": "Comentário revisado e expandido."},
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["note"]["content"] == "Comentário revisado e expandido."

    # Remoção da nota
    del_note_res = client.delete(f"/api/books/{book_id}/notes/{note_id}")
    assert del_note_res.status_code == 200

    # Confirma que o destaque original continua existindo intacto
    page_annot = client.get(f"/api/books/{book_id}/pages/1/annotations").json()
    assert len(page_annot["highlights"]) == 1


def test_page_drawings_vector_storage(client):
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]
    page_num = 4

    # Traços simulados de desenho vetorial (coordenadas normalizadas 0.0 a 1.0)
    strokes = [
        {
            "color": "#3d7cf4",
            "width": 3,
            "points": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.15, "y": 0.12},
                {"x": 0.2, "y": 0.14},
            ],
        },
        {
            "color": "#f87171",
            "width": 5,
            "points": [
                {"x": 0.5, "y": 0.6},
                {"x": 0.55, "y": 0.65},
            ],
        },
    ]

    # Salva traços
    save_res = client.put(
        f"/api/books/{book_id}/pages/{page_num}/drawing",
        json={"strokes": strokes},
    )
    assert save_res.status_code == 200
    assert save_res.json()["stroke_count"] == 2

    # Recupera traços salvos
    get_res = client.get(f"/api/books/{book_id}/pages/{page_num}/drawing")
    assert get_res.status_code == 200
    recovered = get_res.json()
    assert len(recovered["strokes"]) == 2
    assert recovered["strokes"][0]["color"] == "#3d7cf4"
    assert len(recovered["strokes"][0]["points"]) == 3

    # Limpeza dos desenhos da página
    clear_res = client.delete(f"/api/books/{book_id}/pages/{page_num}/drawing")
    assert clear_res.status_code == 200

    # Confirma limpeza
    get_empty_res = client.get(f"/api/books/{book_id}/pages/{page_num}/drawing")
    assert len(get_empty_res.json()["strokes"]) == 0


def test_eraser_single_stroke_removal(client):
    """Valida a remoção de um traço individual preservando os demais e persistindo no SQLite."""
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]
    page_num = 5

    # 3 traços independentes na página
    stroke1 = {"color": "#3d7cf4", "width": 4, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]}
    stroke2 = {"color": "#f87171", "width": 4, "points": [{"x": 0.5, "y": 0.5}, {"x": 0.6, "y": 0.6}]}
    stroke3 = {"color": "#34d399", "width": 4, "points": [{"x": 0.8, "y": 0.8}, {"x": 0.9, "y": 0.9}]}

    # Salva os 3 traços
    client.put(f"/api/books/{book_id}/pages/{page_num}/drawing", json={"strokes": [stroke1, stroke2, stroke3]})
    initial = client.get(f"/api/books/{book_id}/pages/{page_num}/drawing").json()["strokes"]
    assert len(initial) == 3

    # Simula ação da borracha removendo o traço 2 (permanecem 1 e 3)
    remaining_after_erase = [s for s in initial if s["color"] != "#f87171"]
    save_res = client.put(f"/api/books/{book_id}/pages/{page_num}/drawing", json={"strokes": remaining_after_erase})
    assert save_res.status_code == 200

    # Recarrega do banco e confirma que apenas o traço 2 foi apagado
    reloaded = client.get(f"/api/books/{book_id}/pages/{page_num}/drawing").json()["strokes"]
    assert len(reloaded) == 2
    colors = [s["color"] for s in reloaded]
    assert "#3d7cf4" in colors
    assert "#34d399" in colors
    assert "#f87171" not in colors


def test_eraser_multiple_strokes_removal_in_single_stroke(client):
    """Valida remoção de múltiplos traços interceptados em um movimento e persistência após recarregar."""
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]
    page_num = 6

    # 4 traços paralelos verticais (x = 0.2, x = 0.3, x = 0.4, x = 0.8)
    stroke_a = {"color": "#3d7cf4", "width": 4, "points": [{"x": 0.2, "y": 0.1}, {"x": 0.2, "y": 0.9}]}
    stroke_b = {"color": "#3d7cf4", "width": 4, "points": [{"x": 0.3, "y": 0.1}, {"x": 0.3, "y": 0.9}]}
    stroke_c = {"color": "#3d7cf4", "width": 4, "points": [{"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.9}]}
    stroke_isolated = {"color": "#3d7cf4", "width": 4, "points": [{"x": 0.8, "y": 0.1}, {"x": 0.8, "y": 0.9}]}

    client.put(
        f"/api/books/{book_id}/pages/{page_num}/drawing",
        json={"strokes": [stroke_a, stroke_b, stroke_c, stroke_isolated]},
    )

    # Simula borracha passando horizontalmente por x=0.2 e x=0.3 em um único movimento de arrasto
    # (apagando stroke_a e stroke_b, mantendo stroke_c e stroke_isolated)
    after_drag_erase = [stroke_c, stroke_isolated]
    client.put(f"/api/books/{book_id}/pages/{page_num}/drawing", json={"strokes": after_drag_erase})

    # Recarrega e valida estado final
    persisted = client.get(f"/api/books/{book_id}/pages/{page_num}/drawing").json()["strokes"]
    assert len(persisted) == 2
    x_coords = [s["points"][0]["x"] for s in persisted]
    assert 0.4 in x_coords
    assert 0.8 in x_coords
    assert 0.2 not in x_coords
    assert 0.3 not in x_coords


def test_eraser_isolation_from_highlights_and_other_pages(client):
    """Garante que a borracha não interfere em marcações de texto nem em desenhos de outras páginas."""
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]

    # Cria destaque na página 7
    h_res = client.post(
        f"/api/books/{book_id}/highlights",
        json={
            "page": 7,
            "color": "#fef08a",
            "rects": [{"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.05}],
            "selected_text": "Texto imutável sob a borracha",
        },
    )
    assert h_res.status_code == 201

    # Cria traço na página 7 e traço na página 8
    client.put(
        f"/api/books/{book_id}/pages/7/drawing",
        json={"strokes": [{"color": "#f87171", "width": 4, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]}]},
    )
    client.put(
        f"/api/books/{book_id}/pages/8/drawing",
        json={"strokes": [{"color": "#34d399", "width": 4, "points": [{"x": 0.3, "y": 0.3}, {"x": 0.4, "y": 0.4}]}]},
    )

    # Apaga traços da página 7 via borracha (strokes = [])
    client.put(f"/api/books/{book_id}/pages/7/drawing", json={"strokes": []})

    # 1. Desenhos da página 7 estão zerados
    assert len(client.get(f"/api/books/{book_id}/pages/7/drawing").json()["strokes"]) == 0

    # 2. Desenhos da página 8 continuam intactos
    p8_drawings = client.get(f"/api/books/{book_id}/pages/8/drawing").json()["strokes"]
    assert len(p8_drawings) == 1
    assert p8_drawings[0]["color"] == "#34d399"

    # 3. Destaque de texto na página 7 continua intacto
    p7_highlights = client.get(f"/api/books/{book_id}/pages/7/annotations").json()["highlights"]
    assert len(p7_highlights) == 1
    assert p7_highlights[0]["selected_text"] == "Texto imutável sob a borracha"


def test_annotations_validation_errors(client):
    books = client.get("/api/books").json()["books"]
    book_id = books[0]["id"]

    # Texto selecionado vazio no destaque
    bad_h = client.post(
        f"/api/books/{book_id}/highlights",
        json={"page": 1, "color": "#fef08a", "rects": [], "selected_text": " "},
    )
    assert bad_h.status_code in [400, 422]

    # Conteúdo vazio na anotação
    bad_note = client.post(
        f"/api/books/{book_id}/notes",
        json={"page": 1, "content": "  "},
    )
    assert bad_note.status_code in [400, 422]

    # Livro inexistente
    bad_book = client.get("/api/books/non-existent-book/annotations")
    assert bad_book.status_code == 404


def test_geometric_distance_to_segment_algorithm():
    """Valida matematicamente a fórmula de proximidade da borracha ponto-segmento."""
    import math

    def distance_to_segment(px, py, x1, y1, x2, y2):
        dx = x2 - x1
        dy = y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / l2))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y)

    # Segmento de (100, 100) a (200, 100) em uma tela de largura 1000
    # Ponto no meio (150, 105) -> distância é 5px (deve colidir com raio de 18px)
    assert distance_to_segment(150, 105, 100, 100, 200, 100) == 5.0
    assert distance_to_segment(150, 105, 100, 100, 200, 100) <= 18.0

    # Ponto antes do início do segmento (90, 100) -> distância é 10px (colide)
    assert distance_to_segment(90, 100, 100, 100, 200, 100) == 10.0

    # Ponto muito distante (150, 300) -> distância é 200px (não colide)
    assert distance_to_segment(150, 300, 100, 100, 200, 100) > 18.0

