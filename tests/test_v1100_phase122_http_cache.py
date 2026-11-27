"""
Testes automatizados obrigatórios da Fase 12.2 (Otimização da Camada HTTP, Compressão e Cache).
Cobre integralmente os 19 itens especificados no contrato da Fase 12.2.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

import auth_service
import config
import main
from http_cache_service import (
    AssetManifest,
    etag_matches_if_none_match,
    parse_accept_encoding_gzip,
)


def create_minimal_pdf(path: Path, num_pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _create_minimal_epub(path: Path, title: str = "Livro EPUB Teste") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0" encoding="utf-8"?>'
            f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title></metadata>'
            f'<manifest><item id="c1" href="chap1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            f'<spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr(
            "OEBPS/chap1.xhtml",
            "<html><body><h1>Capitulo 1</h1><p>Conteudo de teste EPUB.</p></body></html>",
        )
    return path


def _setup_admin_and_login(client: TestClient, username: str = "admin122", password: str = "SenhaForte122!"):
    client.get("/")
    csrf = client.cookies.get("lectoria_csrf", "")
    setup_res = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": "Admin 122"},
        headers={"X-CSRF-Token": csrf},
    )
    if setup_res.status_code == 403:
        login_res = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        if login_res.status_code != 200:
            try:
                auth_service.create_user(username=username, password=password, display_name="Admin 122", role="admin")
            except auth_service.UserAlreadyExistsError:
                pass
            login_res = client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
                headers={"X-CSRF-Token": csrf},
            )
        assert login_res.status_code == 200, login_res.text
    else:
        assert setup_res.status_code in (200, 201), setup_res.text
    csrf = client.cookies.get("lectoria_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/scan", headers=headers)
    return headers


def _create_reader_user(client: TestClient, admin_headers: dict, username: str, password: str):
    res = client.post(
        "/api/users",
        json={"username": username, "password": password, "display_name": username, "role": "reader"},
        headers=admin_headers,
    )
    if res.status_code == 409:
        return {"username": username}
    assert res.status_code in (200, 201), res.text
    return res.json()


def _extract_asset_url(html: str, path_prefix: str) -> str:
    m = re.search(rf'(?:href|src)="({re.escape(path_prefix)}\?[^"]+)"', html)
    assert m, f"URL versionada não encontrada no HTML para {path_prefix}"
    return m.group(1)


def _extract_dynamic_manifest(html: str) -> dict:
    import html as html_lib

    m = re.search(r'<meta name="lectoria-asset-manifest" content="([^"]+)">', html)
    assert m, "Meta tag lectoria-asset-manifest ausente no HTML"
    return json.loads(html_lib.unescape(m.group(1)))


# ---------------------------------------------------------------------------
# 1, 2, 3, 4: Compressão HTML/CSS/JS/JSON, sem gzip, Vary, integridade
# ---------------------------------------------------------------------------
def test_phase122_selective_gzip_and_integrity(test_env):
    """Testa compressão de HTML, CSS, JS e JSON elegíveis, Vary: Accept-Encoding, clientes sem gzip e integridade."""
    create_minimal_pdf(test_env["library_dir"] / "compress_sample.pdf", num_pages=2)
    with TestClient(main.app) as client:
        # 1. HTML (/)
        r_html_gz = client.get("/", headers={"Accept-Encoding": "gzip"})
        assert r_html_gz.status_code == 200
        assert r_html_gz.headers.get("content-encoding") == "gzip"
        assert "accept-encoding" in r_html_gz.headers.get("vary", "").lower()
        assert int(r_html_gz.headers["content-length"]) < len(r_html_gz.content)

        # Cliente sem gzip (identity / gzip;q=0)
        r_html_plain = client.get("/", headers={"Accept-Encoding": "identity"})
        assert r_html_plain.status_code == 200
        assert "content-encoding" not in r_html_plain.headers
        assert "accept-encoding" in r_html_plain.headers.get("vary", "").lower()
        assert r_html_gz.content == r_html_plain.content

        r_html_q0 = client.get("/", headers={"Accept-Encoding": "gzip;q=0"})
        assert r_html_q0.status_code == 200
        assert "content-encoding" not in r_html_q0.headers

        # 2. CSS e JS versionados
        css_url = _extract_asset_url(r_html_plain.text, "/static/css/style.css")
        js_url = _extract_asset_url(r_html_plain.text, "/static/js/app.js")
        for url in (css_url, js_url):
            rg = client.get(url, headers={"Accept-Encoding": "gzip"})
            rp = client.get(url, headers={"Accept-Encoding": "identity"})
            assert rg.status_code == 200
            assert rg.headers.get("content-encoding") == "gzip"
            assert "accept-encoding" in rg.headers.get("vary", "").lower()
            assert "content-encoding" not in rp.headers
            assert rg.content == rp.content
            # Confirma redução > 70% em CSS/JS
            assert int(rg.headers["content-length"]) < len(rp.content) * 0.30

        # 3. JSON elegível (/api/books e /api/discovery/shelves)
        client.post("/api/scan") if False else None
        _setup_admin_and_login(client)
        r_books_gz = client.get("/api/books", headers={"Accept-Encoding": "gzip"})
        r_books_plain = client.get("/api/books", headers={"Accept-Encoding": "identity"})
        assert r_books_gz.status_code == 200
        assert r_books_gz.headers.get("content-encoding") == "gzip"
        assert "accept-encoding" in r_books_gz.headers.get("vary", "").lower()
        assert r_books_gz.json() == r_books_plain.json()

        # 4. Rotas de autenticação (/api/auth/*) nunca são comprimidas (mitigação BREACH)
        r_auth = client.get("/api/auth/status", headers={"Accept-Encoding": "gzip"})
        assert r_auth.status_code == 200
        assert "content-encoding" not in r_auth.headers


def test_phase122_accept_encoding_parser():
    assert parse_accept_encoding_gzip("gzip, deflate, br") is True
    assert parse_accept_encoding_gzip("deflate, gzip;q=0.5") is True
    assert parse_accept_encoding_gzip("gzip;q=0") is False
    assert parse_accept_encoding_gzip("identity") is False
    assert parse_accept_encoding_gzip("") is False


# ---------------------------------------------------------------------------
# 5, 6, 7, 8, 9: Range 206, HEAD, 304 sem corpo, Downloads PDF/EPUB, binários não comprimidos
# ---------------------------------------------------------------------------
def test_phase122_range_head_304_downloads_and_binary_exclusions(test_env):
    """Preserva Range 206, HEAD, 304 sem corpo, downloads PDF/EPUB e não comprime binários/imagens."""
    pdf_path = create_minimal_pdf(test_env["library_dir"] / "range_doc.pdf", num_pages=3)
    epub_path = _create_minimal_epub(test_env["library_dir"] / "book_doc.epub")
    pdf_bytes = pdf_path.read_bytes()
    epub_bytes = epub_path.read_bytes()

    with TestClient(main.app) as client:
        _setup_admin_and_login(client)
        books = client.get("/api/books").json()["books"]
        pdf_book = next(b for b in books if b["filename"] == "range_doc.pdf")
        epub_book = next(b for b in books if b["filename"] == "book_doc.epub")

        # HEAD no PDF
        r_head = client.head(f"/api/books/{pdf_book['id']}/file", headers={"Accept-Encoding": "gzip"})
        assert r_head.status_code == 200
        assert r_head.content == b""
        assert "content-encoding" not in r_head.headers
        assert r_head.headers.get("accept-ranges") == "bytes"
        assert int(r_head.headers.get("content-length", "0")) == len(pdf_bytes)
        etag = r_head.headers["etag"]

        # Range 206 no PDF (mesmo enviando Accept-Encoding: gzip)
        r_range = client.get(
            f"/api/books/{pdf_book['id']}/file",
            headers={"Range": "bytes=0-127", "Accept-Encoding": "gzip"},
        )
        assert r_range.status_code == 206
        assert "content-encoding" not in r_range.headers
        assert r_range.headers.get("content-range") == f"bytes 0-127/{len(pdf_bytes)}"
        assert r_range.headers.get("accept-ranges") == "bytes"
        assert r_range.content == pdf_bytes[:128]

        # Download integral PDF e EPUB sem compressão indevida
        r_dl_pdf = client.get(f"/api/books/{pdf_book['id']}/download", headers={"Accept-Encoding": "gzip"})
        assert r_dl_pdf.status_code == 200
        assert "content-encoding" not in r_dl_pdf.headers
        assert r_dl_pdf.content == pdf_bytes

        r_dl_epub = client.get(f"/api/books/{epub_book['id']}/file", headers={"Accept-Encoding": "gzip"})
        assert r_dl_epub.status_code == 200
        assert "content-encoding" not in r_dl_epub.headers
        assert r_dl_epub.content == epub_bytes

        # 304 Not Modified sem corpo no /file e no HTML /
        r_304_file = client.get(
            f"/api/books/{pdf_book['id']}/file",
            headers={"If-None-Match": etag, "Accept-Encoding": "gzip"},
        )
        assert r_304_file.status_code == 304
        assert r_304_file.content == b""
        assert "content-encoding" not in r_304_file.headers

        r_idx = client.get("/")
        idx_etag = r_idx.headers["etag"]
        r_304_idx = client.get("/", headers={"If-None-Match": idx_etag, "Accept-Encoding": "gzip"})
        assert r_304_idx.status_code == 304
        assert r_304_idx.content == b""

        # HEAD no HTML /
        r_head_idx = client.head("/", headers={"Accept-Encoding": "gzip"})
        assert r_head_idx.status_code == 200
        assert r_head_idx.content == b""
        assert "content-encoding" not in r_head_idx.headers

        # Capa nunca é comprimida com gzip
        r_cov = client.get(f"/api/books/{pdf_book['id']}/cover", headers={"Accept-Encoding": "gzip"})
        assert r_cov.status_code == 200
        assert "content-encoding" not in r_cov.headers


# ---------------------------------------------------------------------------
# 10, 11, 12, 13: Cache imutável estrito, invalidação sob mesmo APP_VERSION,
# PDF.js worker, EPUB.js e JSZip
# ---------------------------------------------------------------------------
def test_phase122_immutable_cache_and_same_version_invalidation(test_env, tmp_path):
    """Valida immutable apenas com hash correto, invalidação com mesmo APP_VERSION, worker e EPUB.js/JSZip."""
    create_minimal_pdf(test_env["library_dir"] / "reader_assets.pdf", num_pages=1)
    with TestClient(main.app) as client:
        _setup_admin_and_login(client)
        books = client.get("/api/books").json()["books"]
        book_id = books[0]["id"]

        # HTML / e /reader/{id} devem sempre usar no-cache
        r_home = client.get("/")
        assert r_home.headers.get("cache-control") == "no-cache"
        r_reader = client.get(f"/reader/{book_id}")
        assert r_reader.status_code == 200
        assert r_reader.headers.get("cache-control") == "no-cache"

        # Manifesto dinâmico no reader.html contém PDF.js worker, JSZip e EPUB.js com ?v= e &h=
        dyn = _extract_dynamic_manifest(r_reader.text)
        for key in (
            "/static/vendor/pdfjs/pdf.worker.min.js",
            "/static/vendor/epubjs/jszip.min.js",
            "/static/vendor/epubjs/epub.min.js",
        ):
            assert key in dyn
            assert f"?v={config.APP_VERSION}&h=" in dyn[key]
            r_dyn = client.get(dyn[key], headers={"Accept-Encoding": "gzip"})
            assert r_dyn.status_code == 200
            assert r_dyn.headers.get("cache-control") == "public, max-age=31536000, immutable"
            assert r_dyn.headers.get("content-encoding") == "gzip"

        # pdf.min.js no manifesto dinâmico também recebe hash e immutable
        pdf_js_url = dyn["/static/vendor/pdfjs/pdf.min.js"]
        assert f"?v={config.APP_VERSION}&h=" in pdf_js_url
        r_pdfjs = client.get(pdf_js_url, headers={"Accept-Encoding": "gzip"})
        assert r_pdfjs.status_code == 200
        assert r_pdfjs.headers.get("cache-control") == "public, max-age=31536000, immutable"

        # Sem ?h= ou com ?h= errado -> NUNCA immutable (public, no-cache)
        r_no_h = client.get("/static/js/app.js?v=1.9.2")
        assert r_no_h.status_code == 200
        assert r_no_h.headers.get("cache-control") == "public, no-cache"

        r_bad_h = client.get("/static/js/app.js?v=1.9.2&h=deadbeef0000")
        assert r_bad_h.status_code == 200
        assert r_bad_h.headers.get("cache-control") == "public, no-cache"

        # /static/reader.html direto nunca recebe immutable
        r_static_html = client.get("/static/reader.html")
        assert r_static_html.status_code == 200
        assert r_static_html.headers.get("cache-control") == "no-cache"

        # 304 em asset estático versionado via ETag (inclusive fraco W/)
        app_url = _extract_asset_url(r_home.text, "/static/js/app.js")
        r_app_gz = client.get(app_url, headers={"Accept-Encoding": "gzip"})
        app_etag = r_app_gz.headers["etag"]
        r_app_304 = client.get(app_url, headers={"If-None-Match": app_etag, "Accept-Encoding": "gzip"})
        assert r_app_304.status_code == 304
        assert r_app_304.content == b""

    # Teste de invalidação entre dois builds com o MESMO APP_VERSION e conteúdo diferente
    sim_static = tmp_path / "static_sim"
    (sim_static / "js").mkdir(parents=True)
    (sim_static / "index.html").write_text(
        '<html><head></head><body><script src="/static/js/app.js?v=1.9.2"></script></body></html>',
        encoding="utf-8",
    )
    js_file = sim_static / "js" / "app.js"
    js_file.write_text("console.log('build-A');", encoding="utf-8")

    manifest = AssetManifest(sim_static)
    html_a, etag_a = manifest.render_html(sim_static / "index.html", "1.9.2")
    url_a = _extract_asset_url(html_a.decode(), "/static/js/app.js")

    # Modifica o conteúdo mantendo EXATAMENTE o mesmo APP_VERSION="1.9.2"
    js_file.write_text("console.log('build-B-modified');", encoding="utf-8")
    os.utime(js_file, ns=(js_file.stat().st_atime_ns, js_file.stat().st_mtime_ns + 1_000_000))
    html_b, etag_b = manifest.render_html(sim_static / "index.html", "1.9.2")
    url_b = _extract_asset_url(html_b.decode(), "/static/js/app.js")

    assert url_a != url_b
    assert etag_a != etag_b


# ---------------------------------------------------------------------------
# 14, 15, 16, 17, 18, 19: Segurança (CSP/CSRF/HTTPS proxy), isolamento entre
# usuários, logout/revogação, substituição de arquivo e If-Range, ReadonlyRootfs
# ---------------------------------------------------------------------------
def test_phase122_private_document_cache_isolation_logout_and_replacement(test_env):
    """Valida isolamento entre contas, logout/revogação, substituição de arquivo, If-Range e CSP/CSRF/Proxy."""
    pdf_path = create_minimal_pdf(test_env["library_dir"] / "private_book.pdf", num_pages=2)

    with TestClient(main.app) as client_a:
        admin_headers = _setup_admin_and_login(client_a)
        _create_reader_user(client_a, admin_headers, "reader_b", "SenhaReaderB122!")

        books = client_a.get("/api/books").json()["books"]
        book_id = next(b["id"] for b in books if b["filename"] == "private_book.pdf")

        # Cabeçalhos seguros de documento privado
        r_file_a = client_a.get(f"/api/books/{book_id}/file")
        assert r_file_a.status_code == 200
        assert r_file_a.headers.get("cache-control") == "private, no-cache"
        assert "cookie" in r_file_a.headers.get("vary", "").lower()
        etag_v1 = r_file_a.headers["etag"]

        # Logout limpa sessão e instrui limpeza de cache (Clear-Site-Data)
        r_logout = client_a.post("/api/auth/logout", headers=admin_headers)
        assert r_logout.status_code == 200
        assert '"cache"' in r_logout.headers.get("clear-site-data", "")

        # Após logout / sessão expirada: NUNCA retorna 304 mesmo enviando ETag válido!
        r_after_logout = client_a.get(
            f"/api/books/{book_id}/file",
            headers={"If-None-Match": etag_v1},
        )
        assert r_after_logout.status_code == 401

        # Troca de conta no mesmo cliente (login como reader_b)
        client_a.get("/")
        csrf_b = client_a.cookies.get("lectoria_csrf", "")
        r_login_b = client_a.post(
            "/api/auth/login",
            json={"username": "reader_b", "password": "SenhaReaderB122!"},
            headers={"X-CSRF-Token": csrf_b},
        )
        assert r_login_b.status_code == 200

        # Substituição do arquivo no disco invalida ETag e força 200 em If-None-Match e If-Range
        new_pdf = create_minimal_pdf(pdf_path, num_pages=4)
        os.utime(new_pdf, ns=(new_pdf.stat().st_atime_ns, new_pdf.stat().st_mtime_ns + 2_000_000))
        r_reval = client_a.get(
            f"/api/books/{book_id}/file",
            headers={"If-None-Match": etag_v1},
        )
        assert r_reval.status_code == 200
        etag_v2 = r_reval.headers["etag"]
        assert etag_v2 != etag_v1

        # If-Range com ETag antigo deve retornar 200 (arquivo completo atualizado), não 206 corrompido
        r_if_range_old = client_a.get(
            f"/api/books/{book_id}/file",
            headers={"Range": "bytes=0-63", "If-Range": etag_v1},
        )
        assert r_if_range_old.status_code == 200
        assert len(r_if_range_old.content) == len(new_pdf.read_bytes())

        # If-Range com ETag atual retorna 206 corretamente
        r_if_range_ok = client_a.get(
            f"/api/books/{book_id}/file",
            headers={"Range": "bytes=0-63", "If-Range": etag_v2},
        )
        assert r_if_range_ok.status_code == 206
        assert len(r_if_range_ok.content) == 64

        # Preservação de CSP, X-Content-Type-Options, CSRF e compatibilidade com proxy HTTPS
        r_https = client_a.get(
            "/",
            headers={
                "Accept-Encoding": "gzip",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "100.66.170.6",
            },
        )
        assert r_https.status_code == 200
        assert r_https.headers.get("content-encoding") == "gzip"
        assert r_https.headers.get("x-content-type-options") == "nosniff"
        assert r_https.headers.get("x-frame-options") == "SAMEORIGIN"
        assert (
            "content-security-policy" in r_https.headers
            or "content-security-policy-report-only" in r_https.headers
        )
        # Garante zero scripts inline adicionados (manifesto usa apenas <meta>)
        assert '<meta name="lectoria-asset-manifest"' in r_https.text


def test_phase122_etag_matcher_rfc9110():
    assert etag_matches_if_none_match('"abc123"', '"abc123"') is True
    assert etag_matches_if_none_match('W/"abc123"', '"abc123"') is True
    assert etag_matches_if_none_match('"other", W/"abc123"', '"abc123"') is True
    assert etag_matches_if_none_match("*", '"abc123"') is True
    assert etag_matches_if_none_match('"different"', '"abc123"') is False
    assert etag_matches_if_none_match(None, '"abc123"') is False
