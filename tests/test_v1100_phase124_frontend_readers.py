"""
Testes automatizados obrigatórios da Fase 12.4 (Otimização do Frontend e dos Leitores PDF/EPUB).
Cobre carregamento seletivo de módulos, separação PDF/EPUB, URLs versionadas via AssetManifest,
compatibilidade com CSP enforce, projeção compacta de /api/books e /api/discovery/shelves,
priorização de capas visíveis, modal de detalhes instantâneo, orçamento de memória de thumbnails
e compatibilidade integral com as Fases 12.2 e 12.3.
"""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

import auth_service
import config
import main
import thumbnail_service


def _create_minimal_pdf(path: Path, num_pages: int = 3) -> Path:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _create_minimal_epub(path: Path, title: str = "EPUB Fase 12.4") -> Path:
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
            f'<package xmlns="http://www.idpf.org/opf" version="3.0">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title></metadata>'
            f'<manifest><item id="c1" href="chap1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            f'<spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr(
            "OEBPS/chap1.xhtml",
            "<html><body><h1>Capitulo 1</h1><p>Leitura EPUB sem PDF.js.</p></body></html>",
        )
    return path


def _setup_admin(client: TestClient, username: str = "admin124", password: str = "SenhaForte124!"):
    client.get("/")
    csrf = client.cookies.get("lectoria_csrf", "")
    r = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": "Admin 124"},
        headers={"X-CSRF-Token": csrf},
    )
    if r.status_code == 403:
        lr = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        if lr.status_code != 200:
            try:
                auth_service.create_user(username=username, password=password, display_name="Admin 124", role="admin")
            except auth_service.UserAlreadyExistsError:
                pass
            lr = client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
                headers={"X-CSRF-Token": csrf},
            )
        assert lr.status_code == 200, lr.text
    else:
        assert r.status_code in (200, 201), r.text
    csrf = client.cookies.get("lectoria_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/scan", headers=headers)
    return headers


def test_phase124_selective_html_scripts_and_asset_manifest(test_env):
    """Confirma que / e /reader/{id} não carregam scripts secundários/PDF.js antecipadamente e expõem manifesto com hash."""
    _create_minimal_pdf(test_env["library_dir"] / "p124_sample.pdf", num_pages=2)
    with TestClient(main.app) as client:
        _setup_admin(client)
        r_home = client.get("/")
        assert r_home.status_code == 200
        home_html = r_home.text

        # Apenas app.js é carregado via <script src=...> no boot de /
        script_srcs = re.findall(r'<script\s+src="([^"]+)"', home_html)
        assert len(script_srcs) == 1
        assert script_srcs[0].startswith(f"/static/js/app.js?v={config.APP_VERSION}&h=")

        # Módulos secundários estão no <meta name="lectoria-asset-manifest"> com hash de conteúdo
        m = re.search(r'<meta name="lectoria-asset-manifest" content="([^"]+)">', home_html)
        assert m
        manifest = json.loads(html_lib.unescape(m.group(1)))
        for mod in (
            "/static/js/knowledge-api.js",
            "/static/js/book-knowledge.js",
            "/static/js/knowledge.js",
            "/static/js/shared_reading.js",
            "/static/vendor/pdfjs/pdf.min.js",
            "/static/vendor/pdfjs/pdf.worker.min.js",
            "/static/vendor/epubjs/jszip.min.js",
            "/static/vendor/epubjs/epub.min.js",
        ):
            assert mod in manifest
            assert f"?v={config.APP_VERSION}&h=" in manifest[mod]

        # reader.html não inclui <script src="/static/vendor/pdfjs/pdf.min.js"> incondicionalmente
        books = client.get("/api/books").json()["books"]
        bid = next(b["id"] for b in books if b["filename"] == "p124_sample.pdf")
        r_reader = client.get(f"/reader/{bid}")
        assert r_reader.status_code == 200
        reader_scripts = re.findall(r'<script\s+src="([^"]+)"', r_reader.text)
        assert all("pdf.min.js" not in s for s in reader_scripts)
        assert all("epub.min.js" not in s for s in reader_scripts)
        assert all("jszip.min.js" not in s for s in reader_scripts)


def test_phase124_compact_payload_and_details_preservation(test_env):
    """Valida que compact=1 omite campos pesados em /api/books preservando contrato completo em /api/books/{id}."""
    _create_minimal_pdf(test_env["library_dir"] / "p124_compact.pdf", num_pages=4)
    with TestClient(main.app) as client:
        headers = _setup_admin(client)
        books_full = client.get("/api/books").json()["books"]
        target = next(b for b in books_full if b["filename"] == "p124_compact.pdf")
        bid = target["id"]

        # Define uma descrição longa para testar a projeção compacta
        long_desc = "Sinopse editorial detalhada para teste de projeção compacta. " * 30
        r_upd = client.put(
            f"/api/books/{bid}/metadata",
            json={"description": long_desc},
            headers=headers,
        )
        assert r_upd.status_code == 200, r_upd.text

        r_full = client.get("/api/books")
        r_compact = client.get("/api/books?compact=1")
        assert r_full.status_code == 200 and r_compact.status_code == 200
        assert len(r_compact.content) < len(r_full.content)

        item_compact = next(b for b in r_compact.json()["books"] if b["id"] == bid)
        assert "description" not in item_compact
        assert item_compact["title"]
        assert item_compact["cover_url"]
        assert "reading_status" in item_compact

        # Detalhes completos preservam integralmente a descrição
        r_detail = client.get(f"/api/books/{bid}")
        assert r_detail.status_code == 200
        assert r_detail.json()["description"] == long_desc

        # Prateleiras da Descoberta também usam projeção compacta
        r_shelves = client.get("/api/discovery/shelves")
        assert r_shelves.status_code == 200
        for sh in r_shelves.json().get("shelves", []):
            for sb in sh.get("books", []):
                assert "description" not in sb


def test_phase124_thumbnail_memory_budget_hardening():
    """Confirma que o orçamento de memória de thumbnails (Fase 12.3 -> 12.4) respeita VPS 1 vCPU / 768 MiB."""
    assert thumbnail_service.MAX_THUMB_SOURCE_PIXELS <= 25_000_000
    # Semáforo global de 1 geração simultânea
    acquired_first = thumbnail_service._GENERATION_SEMAPHORE.acquire(blocking=False)
    assert acquired_first is True
    try:
        acquired_second = thumbnail_service._GENERATION_SEMAPHORE.acquire(blocking=False)
        assert acquired_second is False
    finally:
        thumbnail_service._GENERATION_SEMAPHORE.release()


def test_phase124_browser_selective_loading_csp_enforce_and_pdf_epub_isolation():
    """
    Teste real no Chromium (sem context.route) com LECTORIA_CSP_MODE=enforce:
    - / carrega apenas app.js inicialmente (sem knowledge.js ou shared_reading.js).
    - Abrir aba Atlas carrega knowledge-api.js e knowledge.js dinamicamente sem violação CSP.
    - Abrir leitor EPUB NÃO baixa pdf.min.js nem pdf.worker.min.js.
    - Abrir leitor PDF carrega pdf.min.js + pdf.worker.min.js e NÃO baixa epub.min.js nem jszip.min.js.
    """
    from playwright.sync_api import sync_playwright
    import os
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td) / "books"
        bdir.mkdir()
        _create_minimal_pdf(bdir / "e2e_pdf.pdf", num_pages=2)
        _create_minimal_epub(bdir / "e2e_epub.epub")

        env = os.environ.copy()
        env["LECTORIA_DATA_DIR"] = td
        env["LECTORIA_LIBRARY_DIR"] = str(bdir)
        env["LECTORIA_CSP_MODE"] = "enforce"
        srv = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    if urllib.request.urlopen(f"{base}/api/health", timeout=1).status == 200:
                        break
                except Exception:
                    time.sleep(0.1)

            books = json.loads(urllib.request.urlopen(f"{base}/api/books").read().decode())["books"]
            pdf_id = next(b["id"] for b in books if b["format"] == "pdf")
            epub_id = next(b["id"] for b in books if b["format"] == "epub")

            fallback_bin = "/home/vini/.var/app/com.visualstudio.code/cache/ms-playwright/chromium-1255/chrome-linux64/chrome"
            launch_kwargs = {"headless": True, **({"executable_path": fallback_bin} if os.path.exists(fallback_bin) else {})}

            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
                ctx = browser.new_context(viewport={"width": 1366, "height": 850})
                page = ctx.new_page()
                console_errs = []
                page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)

                # 1. Home (/) em CSP enforce: carrega apenas app.js, sem knowledge.js nem shared_reading.js
                page.goto(f"{base}/", wait_until="networkidle")
                loaded_home = page.evaluate(
                    "() => performance.getEntriesByType('resource').map(r => new URL(r.name).pathname)"
                )
                assert "/static/js/app.js" in loaded_home
                assert "/static/js/knowledge.js" not in loaded_home
                assert "/static/js/shared_reading.js" not in loaded_home
                assert "/static/js/book-knowledge.js" not in loaded_home

                # Completa setup inicial do admin no navegador para liberar navegação autenticada
                if page.locator("#modal-auth.active").count() > 0:
                    page.fill("#auth-username", "admin_e2e")
                    page.fill("#auth-password", "SenhaE2E124!")
                    if page.locator("#auth-display-name").is_visible():
                        page.fill("#auth-display-name", "Admin E2E")
                    page.click("#btn-auth-submit")
                    page.wait_for_selector("#modal-auth.active", state="detached", timeout=10000)

                # 2. Troca para aba Atlas -> carrega knowledge-api.js e knowledge.js dinamicamente
                page.click("#tab-nav-atlas")
                page.wait_for_function("() => Boolean(window.LectoriaAtlas)", timeout=10000)
                loaded_after_atlas = page.evaluate(
                    "() => performance.getEntriesByType('resource').map(r => new URL(r.name).pathname)"
                )
                assert "/static/js/knowledge-api.js" in loaded_after_atlas
                assert "/static/js/knowledge.js" in loaded_after_atlas

                # 3. Leitor EPUB: NÃO carrega pdf.min.js nem pdf.worker.min.js
                p_epub = ctx.new_page()
                p_epub.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
                p_epub.goto(f"{base}/reader/{epub_id}", wait_until="networkidle")
                p_epub.wait_for_function("() => Boolean(window.ePub && window.JSZip)", timeout=15000)
                epub_res = p_epub.evaluate(
                    "() => performance.getEntriesByType('resource').map(r => new URL(r.name).pathname)"
                )
                assert "/static/vendor/epubjs/jszip.min.js" in epub_res
                assert "/static/vendor/epubjs/epub.min.js" in epub_res
                assert "/static/vendor/pdfjs/pdf.min.js" not in epub_res
                assert "/static/vendor/pdfjs/pdf.worker.min.js" not in epub_res

                # 4. Leitor PDF: carrega pdf.min.js + worker, NÃO carrega epub.min.js nem jszip.min.js
                p_pdf = ctx.new_page()
                p_pdf.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
                p_pdf.goto(f"{base}/reader/{pdf_id}", wait_until="networkidle")
                p_pdf.wait_for_function(
                    "() => Boolean(window.pdfjsLib && document.querySelector('canvas'))",
                    timeout=15000,
                )
                pdf_res = p_pdf.evaluate(
                    "() => performance.getEntriesByType('resource').map(r => new URL(r.name).pathname)"
                )
                assert "/static/vendor/pdfjs/pdf.min.js" in pdf_res
                assert "/static/vendor/epubjs/epub.min.js" not in pdf_res
                assert "/static/vendor/epubjs/jszip.min.js" not in pdf_res

                csp_or_js_errs = [
                    e for e in console_errs
                    if any(tok in e for tok in ("Content Security Policy", "Refused to", "TypeError", "ReferenceError", "SyntaxError"))
                ]
                assert csp_or_js_errs == [], f"Erros de CSP/JS encontrados: {csp_or_js_errs}"
                ctx.close()
                browser.close()
        finally:
            srv.terminate()
            srv.wait(timeout=5)
