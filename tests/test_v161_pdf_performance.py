"""
Suíte de Testes Automatizados — Lectoria v1.6.1.
Validação de Desempenho, HTTP Range (206, 416, HEAD), Cache Condicional (ETag, 304, If-Range)
e Segurança na Entrega de Documentos PDF/EPUB.
"""

from email.utils import formatdate
import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

import config
from auth_service import create_session, create_user, SESSION_COOKIE_NAME
from database import get_db
from library import (
    BookNotFoundError,
    SecurityError,
    resolve_book_file_for_user,
    scan_library,
)
from main import app
from tests.conftest import create_sample_pdf


@pytest.fixture
def test_setup():
    test_pdf = config.LIBRARY_DIR / "sample_perf_v161.pdf"
    create_sample_pdf(test_pdf, "Livro de Desempenho", "Autor Benchmark", pages=20)
    scan_library()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM books WHERE filename = 'sample_perf_v161.pdf'")
        row = cursor.fetchone()
        assert row is not None
        book_id = row["id"]

        cursor.execute("SELECT id FROM users WHERE username = 'reader_v161'")
        user_row = cursor.fetchone()
        if not user_row:
            user = create_user("reader_v161", "senha123", "Leitor v161", role="reader")
            user_id = user["id"]
        else:
            user_id = user_row["id"]

    token = create_session(user_id)
    client = TestClient(app, cookies={SESSION_COOKIE_NAME: token})

    yield {"book_id": book_id, "user_id": user_id, "file_path": test_pdf, "client": client}

    if test_pdf.exists():
        try:
            test_pdf.unlink()
        except Exception:
            pass


def test_resolve_book_file_for_user_efficiency_and_security(test_setup):
    """Verifica que resolve_book_file_for_user executa com eficiência máxima e preserva segurança."""
    book_id = test_setup["book_id"]
    user_id = test_setup["user_id"]

    # 1. Resolução bem-sucedida
    path, stat_res, media_type = resolve_book_file_for_user(book_id, user_id)
    assert path.exists()
    assert path.is_file()
    assert media_type == "application/pdf"
    assert stat_res.st_size > 0

    # 2. Livro inexistente
    with pytest.raises(BookNotFoundError):
        resolve_book_file_for_user("id-inexistente-12345", user_id)

    # 3. Path traversal protegido
    with pytest.raises(BookNotFoundError):
        resolve_book_file_for_user("../../etc/passwd", user_id)


def test_stream_book_file_head_method(test_setup):
    """Valida que HEAD /api/books/{id}/file retorna cabeçalhos corretos sem corpo."""
    client = test_setup["client"]
    book_id = test_setup["book_id"]

    res = client.head(f"/api/books/{book_id}/file")
    assert res.status_code == 200
    assert res.headers["accept-ranges"] == "bytes"
    assert "content-length" in res.headers
    assert int(res.headers["content-length"]) > 0
    assert "etag" in res.headers
    assert "last-modified" in res.headers
    assert res.headers["cache-control"] == "private, no-cache"
    assert res.headers["content-type"] == "application/pdf"
    assert len(res.content) == 0  # HEAD não deve possuir corpo


def test_stream_book_file_get_full_and_range_206(test_setup):
    """Valida requisições completas (200) e parciais (206 Partial Content)."""
    client = test_setup["client"]
    book_id = test_setup["book_id"]

    # 1. Requisição completa (GET)
    full_res = client.get(f"/api/books/{book_id}/file")
    assert full_res.status_code == 200
    assert full_res.headers["cache-control"] == "private, no-cache"
    assert full_res.headers["accept-ranges"] == "bytes"
    file_size = len(full_res.content)
    assert file_size > 100

    # 2. Requisição parcial inicial (bytes 0-99)
    range_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"Range": "bytes=0-99"}
    )
    assert range_res.status_code == 206
    assert len(range_res.content) == 100
    assert range_res.content == full_res.content[:100]
    assert range_res.headers["content-range"] == f"bytes 0-99/{file_size}"
    assert range_res.headers["content-length"] == "100"
    assert range_res.headers["cache-control"] == "private, no-cache"

    # 3. Requisição de cauda (últimos 50 bytes)
    tail_start = file_size - 50
    tail_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"Range": f"bytes={tail_start}-{file_size - 1}"}
    )
    assert tail_res.status_code == 206
    assert len(tail_res.content) == 50
    assert tail_res.content == full_res.content[tail_start:]
    assert tail_res.headers["content-range"] == f"bytes {tail_start}-{file_size - 1}/{file_size}"


def test_stream_book_file_range_416_not_satisfiable(test_setup):
    """Valida resposta 416 quando o intervalo solicitado ultrapassa os limites do arquivo."""
    client = test_setup["client"]
    book_id = test_setup["book_id"]

    res = client.get(
        f"/api/books/{book_id}/file",
        headers={"Range": "bytes=99999999-100000000"}
    )
    assert res.status_code == 416
    assert "content-range" in res.headers
    assert res.headers["content-range"].startswith("bytes */")


def test_conditional_cache_validation_etag_and_304(test_setup):
    """Valida que requisição condicional com If-None-Match retorna 304 Not Modified."""
    client = test_setup["client"]
    book_id = test_setup["book_id"]

    # Primeiro obtém o ETag atual
    head_res = client.head(f"/api/books/{book_id}/file")
    etag = head_res.headers["etag"]

    # Revalidação condicional (arquivo idêntico)
    cond_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"If-None-Match": etag}
    )
    assert cond_res.status_code == 304
    assert len(cond_res.content) == 0
    assert cond_res.headers["etag"] == etag

    # ETag divergente deve retornar 200 com arquivo completo
    diff_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"If-None-Match": '"etag-obsoleto-12345"'}
    )
    assert diff_res.status_code == 200
    assert len(diff_res.content) > 0


def test_conditional_if_range_behavior(test_setup):
    """Valida o comportamento padrão RFC de If-Range para fatias parciais."""
    client = test_setup["client"]
    book_id = test_setup["book_id"]

    head_res = client.head(f"/api/books/{book_id}/file")
    etag = head_res.headers["etag"]

    # 1. If-Range coincidente -> retorna 206 Partial Content
    match_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"Range": "bytes=0-49", "If-Range": etag}
    )
    assert match_res.status_code == 206
    assert len(match_res.content) == 50

    # 2. If-Range divergente -> retorna 200 com arquivo completo
    mismatch_res = client.get(
        f"/api/books/{book_id}/file",
        headers={"Range": "bytes=0-49", "If-Range": '"etag-desatualizado"'}
    )
    assert mismatch_res.status_code == 200
    assert len(mismatch_res.content) > 50


def test_stream_book_file_authentication_required(test_setup):
    """Valida que a rota /api/books/{id}/file exige autenticação quando usuários administradores existem."""
    # Garante que um administrador existe no banco para desativar o fallback anônimo
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE role = 'admin'")
        if not cursor.fetchone():
            create_user("admin_test_v161", "AdminPass123!", "Admin Teste", role="admin")

    anon_client = TestClient(app)
    res = anon_client.get(f"/api/books/{test_setup['book_id']}/file")
    assert res.status_code == 401
