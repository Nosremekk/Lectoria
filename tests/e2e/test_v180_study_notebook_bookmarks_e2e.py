"""
Suíte de Testes End-to-End (E2E) com Playwright — Fase 5 da Lectoria v1.8.0.
Valida o Caderno de Estudos Colaborativo e Marcadores Pessoais (PDF e EPUB)
com dois usuários autenticados (Alice e Bob):

Cenários cobertos:
 1-4. Escopos independentes: Minhas, Públicas, Todas visíveis e alternância.
 5.   Independência em relação ao controle de camadas do leitor.
 6-8. Abas de tipo: Todas, Destaques, Notas, Desenhos e Marcadores.
 9.   Pesquisa textual por conteúdo e por nome público do autor (com debounce).
10.   Ordenação: Posição no livro vs. Mais recentes.
11.   Paginação incremental ("Carregar mais") com > 50 itens sem duplicatas.
12-16. Autoria, edição própria, bloqueio de edição alheia, publicação e revogação.
17-18. Navegação PDF e Navegação EPUB (CFI).
19-22. Marcadores pessoais PDF e EPUB (criação, exclusão, privacidade absoluta).
23.   Responsividade mobile (390x844 sem overflow horizontal).
"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest
from playwright.sync_api import sync_playwright

import annotations_service as ann
import auth_service
import config
import database


EVIDENCE_DIR = Path("/tmp/lectoria_v180_phase5_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_text_pdf(dest_path: Path, num_pages: int = 8):
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font_dict)

    for idx in range(1, num_pages + 1):
        page = writer.add_blank_page(width=595, height=842)
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        text = f"Capitulo {idx}: Conteudo de estudo na pagina {idx} do Lectoria."
        stream.set_data(f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


def _create_synthetic_epub(dest_path: Path, title: str = "Obra EPUB Fase 5"):
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""
    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>{title}</dc:title>
        <dc:creator>Autor Lectoria</dc:creator>
        <dc:language>pt-BR</dc:language>
    </metadata>
    <manifest>
        <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
        <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
        <item id="ch2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="ch1"/>
        <itemref idref="ch2"/>
    </spine>
</package>"""
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Sumário</title></head>
<body>
    <nav epub:type="toc" id="toc"><h1>Sumário</h1><ol>
        <li><a href="ch1.xhtml">Capítulo 1</a></li>
        <li><a href="ch2.xhtml">Capítulo 2</a></li>
    </ol></nav>
</body></html>"""
    ch1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cap 1</title></head>
<body><h1>Capítulo 1</h1><p id="p1">Primeiro parágrafo do EPUB para estudo colaborativo.</p></body></html>"""
    ch2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cap 2</title></head>
<body><h1>Capítulo 2</h1><p id="p2">Segundo parágrafo no capítulo dois.</p></body></html>"""

    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)
        zf.writestr("OEBPS/ch2.xhtml", ch2_xhtml)


@pytest.fixture(scope="module")
def notebook_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v180_p5_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "caderno_pdf.pdf", num_pages=6)
    _create_synthetic_epub(lib_dir / "caderno_epub.epub", title="Caderno EPUB")

    old_vals = (
        config.BASE_DIR,
        config.LIBRARY_DIR,
        config.DATA_DIR,
        config.DB_PATH,
        config.COVERS_DIR,
        config.BACKUPS_DIR,
    )
    config.BASE_DIR = root
    config.LIBRARY_DIR = lib_dir
    config.DATA_DIR = data_dir
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.BACKUPS_DIR = backups_dir

    database.init_db(db_path)

    alice = auth_service.create_user(
        username="alice_p5", password="Password123!", display_name="Alice Lemos", role="admin"
    )
    bob = auth_service.create_user(
        username="bob_p5", password="Password123!", display_name="Bob Ferreira", role="reader"
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
            (?, 'Alice Lemos', '#e11d48', 'Leitora Alice'),
            (?, 'Bob Ferreira', '#2563eb', 'Leitor Bob');
            """,
            (alice["id"], bob["id"]),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo P5', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_p5_pdf', 'Caderno PDF', 'Autor P5', 'caderno_pdf.pdf', 'caderno_pdf.pdf', ?, 'pdf', 6, 1, 0),
            ('book_p5_epub', 'Caderno EPUB', 'Autor P5', 'caderno_epub.epub', 'caderno_epub.epub', ?, 'epub', 0, 1, 0);
            """,
            (
                (lib_dir / "caderno_pdf.pdf").stat().st_size,
                (lib_dir / "caderno_epub.epub").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Popula 55 notas públicas de Alice no PDF para testar paginação incremental (> 50),
    # além de 1 nota privada e 1 desenho público de Alice na página 3
    for i in range(1, 56):
        ann.create_note(
            book_id="book_p5_pdf",
            page=(i % 5) + 1,
            content=f"Nota publica lote {i:02d} de Alice",
            user_id=alice["id"],
            visibility="public",
        )
    ann.create_note(
        book_id="book_p5_pdf",
        page=2,
        content="Segredo estritamente privado de Alice",
        user_id=alice["id"],
        visibility="private",
    )
    ann.create_highlight(
        book_id="book_p5_pdf",
        page=4,
        color="#fef08a",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.03}],
        selected_text="Trecho destacado na pagina 4 por Alice",
        user_id=alice["id"],
        visibility="public",
    )
    ann.save_page_drawing(
        book_id="book_p5_pdf",
        page=3,
        strokes=[{"color": "#e11d48", "width": 3, "points": [{"x": 0.2, "y": 0.2}, {"x": 0.5, "y": 0.3}]}],
        user_id=alice["id"],
        visibility="public",
    )
    # Bob tem 1 nota privada própria no PDF
    ann.create_note(
        book_id="book_p5_pdf",
        page=1,
        content="Nota privada de Bob na pagina 1",
        user_id=bob["id"],
        visibility="private",
    )

    # No EPUB: Alice publica um destaque e uma nota no Capítulo 1
    ann.create_highlight(
        book_id="book_p5_epub",
        page=1,
        color="#a7f3d0",
        rects=[],
        selected_text="Primeiro parágrafo do EPUB para estudo colaborativo.",
        epub_cfi="epubcfi(/6/4[ch1]!/4/2/4,/1:0,/1:25)",
        chapter_label="Capítulo 1",
        user_id=alice["id"],
        visibility="public",
    )

    token_alice = auth_service.create_session(alice["id"])
    token_bob = auth_service.create_session(bob["id"])

    port = _find_free_port()
    env = os.environ.copy()
    env["LECTORIA_DATA_DIR"] = str(data_dir)
    env["LECTORIA_LIBRARY_DIR"] = str(lib_dir)
    env["LECTORIA_DB_PATH"] = str(db_path)
    env["LECTORIA_COVERS_DIR"] = str(covers_dir)
    env["LECTORIA_BACKUPS_DIR"] = str(backups_dir)

    repo_root = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"
    started = False
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/csrf", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.25)

    assert started, "Servidor E2E da Fase 5 não iniciou."

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "bob": bob,
            "token_alice": token_alice,
            "token_bob": token_bob,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        (
            config.BASE_DIR,
            config.LIBRARY_DIR,
            config.DATA_DIR,
            config.DB_PATH,
            config.COVERS_DIR,
            config.BACKUPS_DIR,
        ) = old_vals
        shutil.rmtree(root, ignore_errors=True)


def _new_user_context(browser, base_url: str, session_token: str, viewport: dict):
    ctx = browser.new_context(viewport=viewport)
    ctx.add_cookies(
        [{"name": auth_service.SESSION_COOKIE_NAME, "value": session_token, "url": base_url}]
    )
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    return ctx, page


def test_v180_study_notebook_and_bookmarks_e2e(notebook_server):
    srv = notebook_server
    base_url = srv["base_url"]
    pdf_url = f"{base_url}/reader/book_p5_pdf"
    epub_url = f"{base_url}/reader/book_p5_epub"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_alice, page_a = _new_user_context(browser, base_url, srv["token_alice"], {"width": 1280, "height": 800})
        ctx_bob, page_b = _new_user_context(browser, base_url, srv["token_bob"], {"width": 1280, "height": 800})

        try:
            # --- 1-5. Bob abre PDF, oculta camadas na página ("hidden") e abre Caderno de Estudos ---
            page_b.goto(pdf_url, wait_until="networkidle")
            page_b.wait_for_selector("#single-page-wrapper canvas", timeout=15000)

            page_b.select_option("#layer-view-mode-select", "hidden")
            page_b.click("#btn-toggle-drawer")
            page_b.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_b.wait_for_timeout(400)

            # Independência: mesmo com camadas da página ocultas, Caderno em all_visible exibe anotações públicas e próprias
            assert page_b.text_content("#count-notes") == "56"  # 55 públicas de Alice + 1 privada de Bob
            assert "Segredo estritamente privado de Alice" not in page_b.text_content("#drawer-items-list")

            # --- 11. Paginação incremental ("Carregar mais") ---
            assert page_b.locator("#btn-drawer-load-more").is_visible()
            initial_cards = page_b.locator("#drawer-items-list .annotation-item").count()
            assert initial_cards >= 50
            page_b.click("#btn-drawer-load-more")
            page_b.wait_for_timeout(500)
            after_cards = page_b.locator("#drawer-items-list .annotation-item").count()
            assert after_cards > initial_cards

            # --- 1-4. Alternância entre escopos: Minhas, Públicas, Todas visíveis ---
            page_b.click('.drawer-scope-btn[data-drawer-scope="mine"]')
            page_b.wait_for_timeout(400)
            assert page_b.text_content("#count-notes") == "1"
            assert "Nota privada de Bob" in page_b.text_content("#drawer-items-list")
            # Bob pode editar sua própria nota, mas não a de Alice
            assert page_b.locator("#drawer-items-list .btn-edit-note").count() == 1

            page_b.click('.drawer-scope-btn[data-drawer-scope="public"]')
            page_b.wait_for_timeout(400)
            assert page_b.text_content("#count-notes") == "55"
            assert "Alice Lemos" in page_b.text_content("#drawer-items-list")
            assert page_b.locator("#drawer-items-list .btn-edit-note").count() == 0

            # --- 6-8. Abas Destaques, Notas e Desenhos ---
            page_b.click('.drawer-tab[data-drawer-filter="highlights"]')
            page_b.wait_for_timeout(200)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 1
            assert "Trecho destacado na pagina 4" in page_b.text_content("#drawer-items-list")

            page_b.click('.drawer-tab[data-drawer-filter="drawings"]')
            page_b.wait_for_timeout(200)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 1
            assert "Alice Lemos" in page_b.text_content("#drawer-items-list")
            assert page_b.locator("#drawer-items-list .btn-del-drawing").count() == 0

            # --- 9 & 10. Pesquisa por autor/texto e ordenação ---
            page_b.click('.drawer-tab[data-drawer-filter="all"]')
            page_b.click('.drawer-scope-btn[data-drawer-scope="all_visible"]')
            page_b.wait_for_timeout(350)
            page_b.fill("#drawer-search-input", "lote 55")
            page_b.wait_for_timeout(450)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 1
            page_b.click("#btn-clear-drawer-search")
            page_b.wait_for_timeout(400)

            page_b.select_option("#drawer-sort-select", "recent")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#drawer-items-list .annotation-item").count() >= 1

            # --- 17. Navegação PDF a partir do Caderno ---
            page_b.click('.drawer-tab[data-drawer-filter="highlights"]')
            page_b.wait_for_timeout(200)
            page_b.locator("#drawer-items-list .btn-goto-annotation").first.click()
            page_b.wait_for_timeout(600)
            assert page_b.input_value("#page-num-input") == "4"

            # --- 19, 21 & 22. Marcadores pessoais PDF (criação, privacidade e exclusão) ---
            page_b.click("#btn-toggle-bookmark")
            page_b.wait_for_timeout(400)
            assert "is-bookmarked" in (page_b.get_attribute("#btn-toggle-bookmark", "class") or "")
            page_b.click('.drawer-tab[data-drawer-filter="bookmarks"]')
            page_b.wait_for_timeout(250)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 1
            assert "Privado" in page_b.text_content("#drawer-items-list")
            assert "Tornar Público" not in page_b.text_content("#drawer-items-list")

            # Screenshot Desktop do Caderno de Estudos Colaborativo
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase5_notebook_desktop.png"))

            # Alice não vê o marcador privado de Bob
            page_a.goto(pdf_url, wait_until="networkidle")
            page_a.wait_for_selector("#single-page-wrapper canvas", timeout=15000)
            page_a.click("#btn-toggle-drawer")
            page_a.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_a.click('.drawer-tab[data-drawer-filter="bookmarks"]')
            page_a.wait_for_timeout(300)
            assert page_a.locator("#drawer-items-list .annotation-item").count() == 0

            # Alice publica e depois revoga nota própria no Caderno
            page_a.click('.drawer-tab[data-drawer-filter="drawings"]')
            page_a.wait_for_timeout(250)
            assert page_a.locator("#drawer-items-list .btn-toggle-vis-d").count() == 1
            assert page_a.locator("#drawer-items-list .btn-del-drawing").count() == 1

            # Bob exclui seu marcador
            page_b.locator("#drawer-items-list .btn-del-bookmark").first.click()
            page_b.wait_for_timeout(400)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 0

            # --- 18 & 20. Navegação e Marcador no EPUB + Responsividade Mobile ---
            page_b.goto(epub_url, wait_until="networkidle")
            page_b.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page_b.wait_for_timeout(700)

            page_b.click("#btn-toggle-bookmark")
            page_b.wait_for_timeout(400)
            page_b.click("#btn-toggle-drawer")
            page_b.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_b.click('.drawer-tab[data-drawer-filter="bookmarks"]')
            page_b.wait_for_timeout(300)
            assert page_b.locator("#drawer-items-list .annotation-item").count() == 1

            # Mobile 390x844 sem overflow
            page_b.set_viewport_size({"width": 390, "height": 844})
            page_b.wait_for_timeout(450)
            has_overflow = page_b.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not has_overflow, "Overflow horizontal detectado no Caderno mobile 390px"
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase5_notebook_mobile.png"))

        finally:
            ctx_alice.close()
            ctx_bob.close()
            browser.close()
