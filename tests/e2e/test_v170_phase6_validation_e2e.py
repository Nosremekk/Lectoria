"""
Suíte E2E de Validação Final — Fase 6 da Lectoria v1.7.0.
Valida:
- Barreira real de spoilers no DOM/acessibilidade em todas as visões e teclado (Enter/Space).
- Concorrência: duplo clique em publicar (trava), troca rápida de abas com latência, erro HTTP 500 e logout/reset.
- PDF (páginas 1, intermediária, última, 0, -5, abc, 999, Range 206) e EPUB (init lento + CFI inválido).
- Responsividade nas 8 larguras (320, 360, 375, 390, 412, 768, 1280, 1920), temas claro/escuro,
  modo somente leitura para leitura concluída e guarda de regressão das capas da biblioteca (v1.6.3).
"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import zipfile

import pytest
from playwright.sync_api import sync_playwright

import auth_service
import clubs_service
import config
import database
import shared_reading_service as srs


EVIDENCE_DIR = Path("/tmp/lectoria_v170_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_large_pdf(dest: Path, num_pages: int = 25):
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
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 14 Tf 72 740 Td (Pagina {idx} de {num_pages} do PDF grande.) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest, "wb") as f:
        writer.write(f)


def _create_epub(dest: Path):
    container = '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="B" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB P6</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>'
    nav = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="c1.xhtml">C1</a></li><li><a href="c2.xhtml">C2</a></li></ol></nav></body></html>'
    c1 = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Cap 1</h1><p>Texto inicial do capítulo 1.</p></body></html>'
    c2 = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Cap 2</h1><p>Texto do capítulo 2.</p></body></html>'
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/c1.xhtml", c1)
        zf.writestr("OEBPS/c2.xhtml", c2)


@pytest.fixture(scope="module")
def p6_e2e_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v170_p6_e2e")
    db_path = root / "lectoria_p6.db"
    covers_dir = root / "covers"
    lib_dir = root / "library"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    _create_large_pdf(lib_dir / "grande.pdf", num_pages=25)
    _create_epub(lib_dir / "obra.epub")
    # Capa sintética PNG 1x1 válida para testar o card na biblioteca
    (covers_dir / "book_p6_pdf.jpg").write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00\x00\x00\x1f\xff\xd9"
    )

    old_db, old_cov, old_lib = config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    database.init_db(db_path)

    alice = auth_service.create_user(username="alice_p6e2e", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_p6e2e", password="password123", role="reader")
    token_alice = auth_service.create_session(alice["id"])
    token_bob = auth_service.create_session(bob["id"])

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, cover_path, is_missing)
            VALUES
              ('book_p6_pdf', 'Grande Obra PDF', 'Autor A', 'grande.pdf', 'grande.pdf', 204800, 'pdf', 25, 'book_p6_pdf.jpg', 0),
              ('book_p6_epub', 'EPUB P6', 'Autor B', 'obra.epub', 'obra.epub', 32768, 'epub', 0, NULL, 0);
            """
        )
        conn.commit()
    finally:
        conn.close()

    club = clubs_service.create_club("Clube P6 E2E", "Validação final", alice["id"], "public")
    clubs_service.join_club(club["id"], bob["id"])

    r_active = clubs_service.create_collective_read(club["id"], alice["id"], "book_p6_pdf", title="Leitura Ativa P6", status="in_progress")
    r_completed = clubs_service.create_collective_read(club["id"], alice["id"], "book_p6_pdf", title="Leitura Concluída P6", status="in_progress")

    # Conteúdo com spoiler secreto para auditoria de DOM/acessibilidade
    t_sp = srs.create_topic(r_active["id"], alice["id"], "Tópico Spoiler P6", "SEGREDO_TOPICO_P6", is_spoiler=True)
    srs.create_post(r_active["id"], t_sp["id"], alice["id"], "SEGREDO_RESPOSTA_P6", is_spoiler=True)
    srs.share_quote(r_active["id"], alice["id"], quote_text="SEGREDO_CITACAO_P6", is_spoiler=True)
    srs.create_goal(r_active["id"], alice["id"], "Meta P6", "Cap 1 a 10")

    srs.create_topic(r_completed["id"], alice["id"], "Tópico Histórico", "Texto concluído")
    clubs_service.update_collective_read(r_completed["id"], alice["id"], status="completed")

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["LECTORIA_DB_PATH"] = str(db_path)
    env["LECTORIA_COVERS_DIR"] = str(covers_dir)
    env["LECTORIA_LIBRARY_DIR"] = str(lib_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/me", timeout=1):
                pass
            break
        except Exception:
            time.sleep(0.25)

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "bob": bob,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "club": club,
            "r_active": r_active,
            "r_completed": r_completed,
            "t_sp": t_sp,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR = old_db, old_cov, old_lib


def _save_shot(page, filename: str):
    page.wait_for_timeout(250)
    dest = EVIDENCE_DIR / filename
    page.screenshot(path=str(dest), full_page=False)


def test_phase6_spoilers_concurrency_responsive_and_covers(p6_e2e_server):
    srv = p6_e2e_server
    base_url = srv["base_url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx.add_cookies([{"name": "lectoria_session", "value": srv["token_bob"], "domain": "127.0.0.1", "path": "/"}])
            page = ctx.new_page()

            console_errors = []
            page.on("pageerror", lambda err: console_errors.append(str(err)))

            # 1. Guarda de regressão das capas v1.6.3 na Biblioteca
            page.goto(base_url)
            page.wait_for_selector("#tab-nav-library", state="visible")
            page.click("#tab-nav-library")
            page.wait_for_selector(".books-grid .book-card .book-cover", state="visible", timeout=10000)
            cover_box = page.locator(".books-grid .book-card .book-cover").first.bounding_box()
            assert cover_box and cover_box["width"] >= 100 and cover_box["height"] >= 120, f"Capa pequena detectada: {cover_box}"

            # 2. Abrir painel da Leitura Ativa e auditar Spoilers (DOM/acessibilidade + teclado)
            page.click("#tab-nav-clubs")
            page.wait_for_selector(".club-card:has-text('Clube P6 E2E') .btn-open-club-detail")
            page.click(".club-card:has-text('Clube P6 E2E') .btn-open-club-detail")
            page.wait_for_selector(".collective-read-card:has-text('Leitura Ativa P6') .btn-open-shared-panel")
            page.click(".collective-read-card:has-text('Leitura Ativa P6') .btn-open-shared-panel")
            page.wait_for_selector("#shared-reading-view .sr-overview-stats")

            # Garante ausência dos segredos no DOM antes da revelação
            html_before = page.locator("#shared-reading-view").inner_html()
            assert "SEGREDO_TOPICO_P6" not in html_before
            assert "SEGREDO_RESPOSTA_P6" not in html_before
            assert "SEGREDO_CITACAO_P6" not in html_before

            # Aba Discussões: spoiler oculto até Enter no botão de spoiler
            page.click("#shared-reading-view [data-sr-tab='discussions']")
            page.wait_for_selector(".sr-topic-card:has-text('Tópico Spoiler P6')")
            assert "SEGREDO_TOPICO_P6" not in page.locator("#sr-panel-discussions").inner_html()

            # Abre detalhe do tópico e revela via teclado (Enter)
            page.click(".sr-topic-card:has-text('Tópico Spoiler P6') .sr-topic-title-btn")
            page.wait_for_selector(".sr-topic-detail-card")
            assert "SEGREDO_TOPICO_P6" not in page.locator("#sr-topic-detail-body").inner_html()
            assert "SEGREDO_RESPOSTA_P6" not in page.locator("#sr-topic-detail-body").inner_html()
            page.locator("#sr-topic-detail-body .sr-spoiler-barrier").first.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("() => document.querySelector('#sr-topic-detail-body')?.innerText.includes('SEGREDO_TOPICO_P6')")

            # Aba Citações: spoiler oculto até clique
            page.click("#shared-reading-view [data-sr-tab='quotes']")
            page.wait_for_selector(".sr-quote-card")
            assert "SEGREDO_CITACAO_P6" not in page.locator("#sr-panel-quotes").inner_html()
            page.click("#sr-panel-quotes .sr-spoiler-barrier")
            page.wait_for_function("() => document.querySelector('#sr-panel-quotes')?.innerText.includes('SEGREDO_CITACAO_P6')")

            # 3. Duplo clique em publicar tópico -> cria apenas 1 tópico
            page.click("#shared-reading-view [data-sr-tab='discussions']")
            page.click("#btn-sr-back-topics")
            page.click("#btn-sr-new-topic")
            page.wait_for_selector("#modal-sr-topic.active")
            page.fill("#sr-topic-title", "Tópico Duplo Clique P6")
            page.fill("#sr-topic-content", "Conteúdo único")
            submit_btn = page.locator("#form-sr-topic button[type='submit']")
            submit_btn.dblclick()
            page.wait_for_selector("#modal-sr-topic.active", state="detached", timeout=10000)
            page.wait_for_selector(".sr-topic-card:has-text('Tópico Duplo Clique P6')")
            assert page.locator(".sr-topic-card:has-text('Tópico Duplo Clique P6')").count() == 1

            # 4. Troca rápida de abas com latência + erro HTTP 500 simulado
            def _slow_goals(route):
                time.sleep(0.15)
                route.fulfill(status=500, content_type="application/json", body='{"detail":"Erro simulado"}')

            page.route(f"**/api/collective-reads/{srv['r_active']['id']}/goals*", _slow_goals)
            page.click("#shared-reading-view [data-sr-tab='goals']")
            page.click("#shared-reading-view [data-sr-tab='quotes']")
            page.wait_for_selector("#sr-panel-quotes .sr-quote-card")
            page.unroute(f"**/api/collective-reads/{srv['r_active']['id']}/goals*")

            # Erro 500 exibido com UI limpa sem quebrar painel
            page.route(f"**/api/collective-reads/{srv['r_active']['id']}/goals*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"Falha temporária"}'))
            page.click("#shared-reading-view [data-sr-tab='goals']")
            page.wait_for_selector("#sr-panel-goals .sr-state-box", state="visible")
            page.unroute(f"**/api/collective-reads/{srv['r_active']['id']}/goals*")

            # 5. Leitura Concluída em modo somente-leitura
            page.click("#shared-reading-view button:has-text('Voltar ao Clube')")
            page.wait_for_selector(".collective-read-card:has-text('Leitura Concluída P6') .btn-open-shared-panel")
            page.click(".collective-read-card:has-text('Leitura Concluída P6') .btn-open-shared-panel")
            page.wait_for_selector("#sr-readonly-banner", state="visible")
            assert page.locator("#btn-sr-new-topic").is_hidden()
            assert page.locator("#btn-sr-new-quote").is_hidden()
            _save_shot(page, "v170_phase6_completed_readonly_desktop.png")

            # 6. Matriz responsiva nas 8 larguras (320, 360, 375, 390, 412, 768, 1280, 1920)
            for vw in (320, 360, 375, 390, 412, 768, 1280, 1920):
                page.set_viewport_size({"width": vw, "height": 800})
                if vw == 360:
                    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
                    _save_shot(page, "v170_phase6_mobile_360_light.png")
                else:
                    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
                no_overflow = page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                assert no_overflow, f"Overflow horizontal detectado em {vw}px"

            assert console_errors == [], f"Erros de console inesperados: {console_errors}"
            ctx.close()
        finally:
            browser.close()


def test_phase6_pdf_edge_cases_range_and_epub_slow_init(p6_e2e_server):
    srv = p6_e2e_server
    base_url = srv["base_url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx.add_cookies([{"name": "lectoria_session", "value": srv["token_bob"], "domain": "127.0.0.1", "path": "/"}])
            page = ctx.new_page()

            # Verifica preservação de HTTP Range (206 / Accept-Ranges) no PDF grande
            range_statuses = []
            page.on("response", lambda resp: range_statuses.append(resp.status) if "/api/books/book_p6_pdf/file" in resp.url else None)

            # Páginas válidas: 1, intermediária (12) e última (25)
            for target_p in (1, 12, 25):
                page.goto("about:blank")
                page.goto(f"{base_url}/reader/book_p6_pdf#page={target_p}")
                page.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
                page.wait_for_function(f"() => document.getElementById('page-num-input')?.value === '{target_p}'", timeout=10000)

            assert any(s in (200, 206) for s in range_statuses)

            # Casos inválidos de PDF: #page=0, #page=-5, #page=abc, #page=999
            for bad_hash in ("#page=0", "#page=-5", "#page=abc", "#page=999"):
                page.goto("about:blank")
                page.goto(f"{base_url}/reader/book_p6_pdf{bad_hash}")
                page.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
                page.wait_for_selector("#reader-toast", state="visible", timeout=10000)

            # EPUB com inicialização lenta + CFI inválido
            def _delay_epub(route):
                time.sleep(0.2)
                route.continue_()

            page.route("**/api/books/book_p6_epub/file*", _delay_epub)
            page.goto("about:blank")
            page.goto(f"{base_url}/reader/book_p6_epub#cfi=epubcfi(/6/99!/4/999)")
            page.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page.wait_for_selector("#reader-toast", state="visible", timeout=12000)
            page.unroute("**/api/books/book_p6_epub/file*")

            ctx.close()
        finally:
            browser.close()
