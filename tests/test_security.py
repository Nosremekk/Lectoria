"""
Testes de segurança, HTTP Range Requests e tratamento de arquivos ausentes.
"""

from pathlib import Path
from library import scan_library
from tests.conftest import create_sample_pdf
import config


def test_pdf_streaming_and_range_requests(client):
    books = client.get("/api/books").json()["books"]
    book = books[0]

    # Requisição completa do arquivo
    full_res = client.get(f"/api/books/{book['id']}/file")
    assert full_res.status_code == 200
    assert full_res.headers.get("content-type") == "application/pdf"
    assert "accept-ranges" in full_res.headers
    assert full_res.headers["accept-ranges"] == "bytes"
    total_bytes = len(full_res.content)
    assert total_bytes > 0

    # Range Request parcial (primeiros 64 bytes)
    range_res = client.get(
        f"/api/books/{book['id']}/file",
        headers={"Range": "bytes=0-63"},
    )
    assert range_res.status_code == 206
    assert len(range_res.content) == 64
    assert range_res.content == full_res.content[:64]
    assert range_res.headers["content-range"].startswith("bytes 0-63/")


def test_missing_or_removed_book(client):
    # Garante que a fonte principal esteja apontando para o diretório de teste atual
    import database
    with database.get_db() as conn:
        conn.execute("UPDATE library_sources SET path = ?, is_active = 1 WHERE id = 1", (str(config.LIBRARY_DIR.resolve()),))

    # Cria um livro temporário específico para testar remoção
    temp_book_file = config.LIBRARY_DIR / "livro_temporario.pdf"
    create_sample_pdf(temp_book_file, "Livro Temporário", "Autor Teste", pages=3)
    scan_library()

    # Busca o ID cadastrado
    res = client.get("/api/books?search=Temporário")
    assert res.status_code == 200
    books = res.json()["books"]
    assert len(books) > 0
    book = books[0]
    book_id = book["id"]

    # Remove o arquivo físico diretamente do disco
    temp_book_file.unlink()

    # 1. O escaneamento da biblioteca deve detectar o arquivo faltante e marcar is_missing = 1
    scan_stats = scan_library()
    assert scan_stats["missing"] >= 1

    # 2. O livro não deve mais aparecer na listagem ativa
    lib_res = client.get("/api/books")
    ids = [b["id"] for b in lib_res.json()["books"]]
    assert book_id not in ids

    # 3. Tentativa de obter o arquivo deve retornar HTTP 404 limpo
    res_file = client.get(f"/api/books/{book_id}/file")
    assert res_file.status_code == 404


def test_path_traversal_prevention(client):
    """Garante que IDs forjados ou rotas manipuladas não acessem arquivos do sistema."""
    # Tentativa de ID com path traversal
    malicious_ids = [
        "../../../../etc/passwd",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "/etc/shadow",
        "nested/../../../config.py",
    ]

    for bad_id in malicious_ids:
        res = client.get(f"/api/books/{bad_id}/file")
        # Deve ser rejeitado com 404 (não cadastrado) ou 403 (violação de segurança)
        assert res.status_code in [404, 403, 422]


def test_reader_page_validation(client):
    books = client.get("/api/books").json()["books"]
    valid_id = books[0]["id"]

    res_valid = client.get(f"/reader/{valid_id}")
    assert res_valid.status_code == 200

    res_invalid = client.get("/reader/non-existent-uuid")
    assert res_invalid.status_code == 404
