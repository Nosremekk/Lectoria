"""
Testes de integração para a interface de Descoberta, Prateleiras Horizontais,
Filtros Multidimensionais, FTS5 e Fila de Enriquecimento (v1.4.0).
"""

from unittest.mock import patch

import pytest

import config
from database import get_db
from library import scan_library, get_books, get_discovery_shelves, update_book_metadata
from tests.conftest import create_sample_pdf


def test_discovery_shelves_non_empty(client):
    # Garante que as prateleiras de descoberta não retornam nenhuma prateleira vazia
    res = client.get("/api/discovery/shelves")
    assert res.status_code == 200
    data = res.json()
    assert "shelves" in data
    shelves = data["shelves"]
    assert len(shelves) > 0

    shelf_types = set()
    for s in shelves:
        assert len(s["books"]) > 0
        assert "id" in s
        assert "title" in s
        assert "type" in s
        shelf_types.add(s["type"])

    # Pelo menos recém-adicionados deve estar presente
    assert "recently_added" in shelf_types


def test_multidimensional_filters_and_fts(client):
    # Filtro por formato
    res_pdf = client.get("/api/books?format=pdf")
    assert res_pdf.status_code == 200
    books_pdf = res_pdf.json()["books"]
    for b in books_pdf:
        assert b["format"] == "pdf"

    # Busca textual FTS5
    res_fts = client.get("/api/books?fts_query=Machado")
    assert res_fts.status_code == 200
    books_fts = res_fts.json()["books"]
    assert len(books_fts) >= 1
    for b in books_fts:
        assert "machado" in (b.get("author") or "").lower() or "machado" in b["title"].lower()


def test_manual_metadata_update_and_locks(client):
    sample_file = config.LIBRARY_DIR / "obra_teste.pdf"
    create_sample_pdf(sample_file, "Obra Original de Teste", "Autor Teste", pages=2)
    scan_library()

    try:
        books = client.get("/api/books?search=Original").json()["books"]
        assert len(books) > 0
        b_id = books[0]["id"]

        # Atualiza metadados com bloqueio manual
        payload = {
            "title": "Título Customizado do Usuário",
            "author": "Autor Especial",
            "publisher": "Editora Antigravity",
            "publish_year": 2026,
            "isbn13": "9788535902778",
            "lock_fields": ["title", "author", "publisher"],
        }
        res = client.put(f"/api/books/{b_id}/metadata", json=payload)
        assert res.status_code == 200
        updated = res.json()["book"]
        assert updated["title"] == "Título Customizado do Usuário"
        assert updated["author"] == "Autor Especial"
        assert updated["publisher"] == "Editora Antigravity"
        assert "title" in updated["manual_lock_fields"]
        assert "author" in updated["manual_lock_fields"]
        assert updated["identification_status"] == "manual_override"

        # Executa scan_library() e verifica que os campos travados NÃO foram revertidos
        scan_library()
        res_after = client.get(f"/api/books/{b_id}")
        assert res_after.status_code == 200
        after = res_after.json()
        assert after["title"] == "Título Customizado do Usuário"
        assert after["author"] == "Autor Especial"
    finally:
        if sample_file.exists():
            sample_file.unlink()
        with get_db() as conn:
            conn.execute("DELETE FROM books WHERE id = ?", (b_id,))
        scan_library()


@patch("metadata_service._safe_http_get_json")
def test_metadata_search_and_apply(mock_http, client):
    sample_file = config.LIBRARY_DIR / "obra_reconciliar.pdf"
    create_sample_pdf(sample_file, "Obra Para Reconciliar", "Autor Inicial", pages=2)
    scan_library()

    try:
        books = client.get("/api/books?search=Reconciliar").json()["books"]
        assert len(books) > 0
        b_id = books[0]["id"]

        mock_http.return_value = {
            "title": "Edição Reconciliada Oficial",
            "authors": [{"name": "Autor Canônico"}],
            "publishers": ["Editora Nacional"],
            "publish_date": "2010",
            "isbn_13": ["9788535902778"],
            "isbn_10": ["8535902775"],
            "covers": [99999],
        }

        # 1. Busca externa de candidatos
        res_search = client.post(
            f"/api/books/{b_id}/metadata/search",
            json={"isbn": "9788535902778", "provider": "open_library"},
        )
        assert res_search.status_code == 200
        candidates = res_search.json()["candidates"]
        assert len(candidates) >= 1
        cand = candidates[0]
        cand_id = cand["id"]

        # 2. Aplica candidato
        res_apply = client.post(
            f"/api/books/{b_id}/metadata/apply",
            json={"candidate_id": cand_id, "apply_cover": False},
        )
        assert res_apply.status_code == 200
        applied_book = res_apply.json()["book"]
        assert applied_book["identification_status"] == "identified"
        assert applied_book["isbn13"] == "9788535902778"
    finally:
        if sample_file.exists():
            sample_file.unlink()
        with get_db() as conn:
            conn.execute("DELETE FROM books WHERE id = ?", (b_id,))
        scan_library()


def test_metadata_queue_admin_endpoints(client):
    # Consulta status da fila
    res_status = client.get("/api/metadata/queue/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "status" in data
    assert "total" in data

    # Enfileira livros pendentes
    res_enqueue = client.post("/api/metadata/queue/enqueue", json={"only_pending": True})
    assert res_enqueue.status_code == 200
    assert "enqueued_count" in res_enqueue.json()

    # Pausa a fila
    res_pause = client.post("/api/metadata/queue/pause")
    assert res_pause.status_code == 200
    assert res_pause.json()["queue"]["status"] in ("paused", "idle")

    # Cancela a fila
    res_cancel = client.post("/api/metadata/queue/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["queue"]["status"] in ("cancelled", "idle")
