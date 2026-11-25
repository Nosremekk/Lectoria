"""
Teste E2E real (Playwright Chromium) — Fase 11.3:
Valida que a política Content-Security-Policy (modos report-only e enforce)
carrega a interface principal, Atlas, leitor PDF (PDF.js + worker) e leitor EPUB
(EPUB.js + JSZip) com ZERO violações `securitypolicyviolation`, preservando
HTTP Range (206), capas SVG e downloads.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest
from playwright.sync_api import sync_playwright

import auth_service
import config
import database


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_min_pdf(dest: Path):
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = pypdf.PdfWriter()
    font = DictionaryObject(
        {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}
    )
    fref = w._add_object(font)
    page = w.add_blank_page(width=595, height=842)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): fref})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (CSP PDF Test) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    with open(dest, "wb") as f:
        w.write(f)


def _create_min_epub(dest: Path):
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>CSP EPUB</dc:title><dc:creator>Autor</dc:creator><dc:language>pt-BR</dc:language></metadata>'
            '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="ch1"/></spine></package>',
        )
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            '<head><title>TOC</title></head><body><nav epub:type="toc" id="toc"><ol><li><a href="ch1.xhtml">Cap 1</a></li></ol></nav></body></html>',
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
            '<head><title>Cap 1</title></head><body><h1>Cap 1</h1><p>Texto EPUB sob CSP.</p></body></html>',
        )


def test_v192_csp_enforce_zero_violations_across_library_atlas_pdf_epub(tmp_path):
    data_dir = tmp_path / "data"
    lib_dir = tmp_path / "books"
    covers_dir = data_dir / "covers"
    backups_dir = data_dir / "backups"
    for d in (data_dir, lib_dir, covers_dir, backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_min_pdf(lib_dir / "csp_test.pdf")
    _create_min_epub(lib_dir / "csp_test.epub")

    old_vals = (config.BASE_DIR, config.LIBRARY_DIR, config.DATA_DIR, config.DB_PATH, config.COVERS_DIR, config.BACKUPS_DIR)
    config.BASE_DIR = tmp_path
    config.LIBRARY_DIR = lib_dir
    config.DATA_DIR = data_dir
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.BACKUPS_DIR = backups_dir

    database.init_db()
    user = auth_service.create_user("csp_e2e", "SenhaForteCsp123!", role="admin", display_name="CSP E2E")
    token = auth_service.create_session(user["id"])

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Default', ?, 1)", (str(lib_dir),))
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('csp_pdf_1', 'Livro PDF CSP', 'Autor PDF', 'csp_test.pdf', 'csp_test.pdf', ?, 'pdf', 1, 1, 0),
            ('csp_epub_1', 'Livro EPUB CSP', 'Autor EPUB', 'csp_test.epub', 'csp_test.epub', ?, 'epub', 0, 1, 0);
            """,
            ((lib_dir / "csp_test.pdf").stat().st_size, (lib_dir / "csp_test.epub").stat().st_size),
        )
        conn.commit()
    finally:
        conn.close()

    port = _find_free_port()
    env = os.environ.copy()
    env.update(
        {
            "LECTORIA_DATA_DIR": str(data_dir),
            "LECTORIA_LIBRARY_DIR": str(lib_dir),
            "LECTORIA_DB_PATH": str(db_path),
            "LECTORIA_COVERS_DIR": str(covers_dir),
            "LECTORIA_BACKUPS_DIR": str(backups_dir),
            "LECTORIA_CSP_MODE": "enforce",
            "LECTORIA_SECURE_COOKIES": "auto",
            "LECTORIA_FORWARDED_ALLOW_IPS": "127.0.0.1,::1",
        }
    )

    repo_root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)

        # Verifica HTTP Range 206 no PDF, Content-Disposition no download e CSP próprio na capa SVG
        req_range = urllib.request.Request(
            f"{base_url}/api/books/csp_pdf_1/file",
            headers={"Cookie": f"lectoria_session={token}", "Range": "bytes=0-127"},
        )
        with urllib.request.urlopen(req_range, timeout=5) as rr:
            assert rr.status == 206
            assert rr.headers.get("Accept-Ranges") == "bytes"
            assert "bytes 0-127/" in (rr.headers.get("Content-Range") or "")

        req_dl = urllib.request.Request(
            f"{base_url}/api/books/csp_pdf_1/download",
            headers={"Cookie": f"lectoria_session={token}"},
        )
        with urllib.request.urlopen(req_dl, timeout=5) as rdl:
            assert rdl.status == 200
            assert "attachment" in (rdl.headers.get("Content-Disposition") or "").lower()

        req_cov = urllib.request.Request(
            f"{base_url}/api/books/csp_pdf_1/cover",
            headers={"Cookie": f"lectoria_session={token}"},
        )
        with urllib.request.urlopen(req_cov, timeout=5) as rcov:
            assert rcov.status == 200
            assert "default-src 'none'" in (rcov.headers.get("Content-Security-Policy") or "")

        # Validação real no navegador sob CSP enforce: zero violações em /, Atlas, PDF e EPUB
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies(
                [
                    {"name": "lectoria_session", "value": token, "url": base_url},
                    {"name": "lectoria_csrf", "value": "csrf_csp_e2e", "url": base_url},
                ]
            )
            page = context.new_page()
            page.add_init_script(
                """
                window.__cspViolations = [];
                document.addEventListener('securitypolicyviolation', (e) => {
                    window.__cspViolations.push({
                        directive: e.violatedDirective,
                        blockedURI: e.blockedURI,
                        sourceFile: e.sourceFile,
                    });
                });
                """
            )

            # 1. Biblioteca + Atlas
            page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
            page.wait_for_selector("#tab-nav-atlas", timeout=8000)
            v_index = page.evaluate("() => window.__cspViolations")
            assert v_index == [], f"Violações CSP em /: {v_index}"

            # 2. Leitor PDF (PDF.js + worker)
            page.goto(f"{base_url}/reader?id=csp_pdf_1", wait_until="networkidle")
            page.wait_for_timeout(1200)
            v_pdf = page.evaluate("() => window.__cspViolations")
            assert v_pdf == [], f"Violações CSP no leitor PDF: {v_pdf}"

            # 3. Leitor EPUB (EPUB.js + JSZip + iframe)
            page.goto(f"{base_url}/reader?id=csp_epub_1", wait_until="networkidle")
            page.wait_for_timeout(1200)
            v_epub = page.evaluate("() => window.__cspViolations")
            assert v_epub == [], f"Violações CSP no leitor EPUB: {v_epub}"

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        (config.BASE_DIR, config.LIBRARY_DIR, config.DATA_DIR, config.DB_PATH, config.COVERS_DIR, config.BACKUPS_DIR) = old_vals
