"""
Suíte de Testes E2E (Playwright) — Fase 3 da Lectoria v1.9.0.
Valida a integração do Atlas (Conhecimento Conectado) no Leitor PDF:
- Carregamento sob demanda (zero chamadas /api/knowledge/* antes de abrir o painel).
- Criação e associação contextual de Conceitos, preview de detalhes e desassociação segura.
- Conexões semânticas PDF -> PDF (mesmo livro e livros distintos), PDF -> EPUB (cross-format).
- Edição de relação/justificativa e consulta de Backlinks recebidos.
- Navegação segura mesmo livro (#page=N in-place), cross-book e cross-format preservando progresso.
- Acessibilidade (Escape fecha modal) e responsividade (desktop, tablet 768px, mobile 360px sem overflow).
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

import annotations_service
import auth_service
import config
import database
import knowledge_service


from tests.e2e.artifact_utils import get_artifact_dir

ARTIFACT_DIR = get_artifact_dir()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_text_pdf(dest_path: Path, title_prefix: str, num_pages: int = 5):
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
        text = f"{title_prefix} - Pagina {idx}: Reflexao tecnica no Lectoria."
        stream.set_data(f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


def _create_synthetic_epub(dest_path: Path, title: str):
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
    <dc:creator>Autor E2E</dc:creator>
    <dc:language>pt-BR</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>"""
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Sumário</title></head>
<body><nav epub:type="toc" id="toc"><ol><li><a href="ch1.xhtml">Capítulo 1</a></li></ol></nav></body>
</html>"""
    ch1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 1</title></head>
<body><h1>Capítulo 1</h1><p id="p1">A essência da técnica moderna como Gestell.</p></body>
</html>"""
    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)


@pytest.fixture(scope="module")
def pdf_knowledge_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p3_pdf_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "ellul_tecnica.pdf", "Ellul Tecnica", num_pages=5)
    _create_text_pdf(lib_dir / "simondon_tecnica.pdf", "Simondon Tecnica", num_pages=4)
    _create_synthetic_epub(lib_dir / "heidegger_tecnica.epub", "A Questão da Técnica")

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
        username="alice_v190_pdf",
        password="Password123!",
        display_name="Alice Lemos",
        role="admin",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES (?, 'Alice Lemos', '#d4a853', 'Pesquisadora');",
            (alice["id"],),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo v190 PDF', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_pdf_a', 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul_tecnica.pdf', 'ellul_tecnica.pdf', ?, 'pdf', 5, 1, 0),
            ('book_pdf_b', 'Do Modo de Existência dos Objetos Técnicos', 'Gilbert Simondon', 'simondon_tecnica.pdf', 'simondon_tecnica.pdf', ?, 'pdf', 4, 1, 0),
            ('book_epub_cross', 'A Questão da Técnica', 'Martin Heidegger', 'heidegger_tecnica.epub', 'heidegger_tecnica.epub', ?, 'epub', 0, 1, 0);
            """,
            (
                (lib_dir / "ellul_tecnica.pdf").stat().st_size,
                (lib_dir / "simondon_tecnica.pdf").stat().st_size,
                (lib_dir / "heidegger_tecnica.epub").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Seed persisted annotations for Alice
    hl_pdf_a_p1 = annotations_service.create_highlight(
        book_id="book_pdf_a",
        user_id=alice["id"],
        page=1,
        selected_text="A técnica tornou-se autônoma frente à economia e à política.",
        rects=[{"x": 0.12, "y": 0.12, "w": 0.55, "h": 0.04}],
        color="#fef08a",
    )
    hl_pdf_a_p4 = annotations_service.create_highlight(
        book_id="book_pdf_a",
        user_id=alice["id"],
        page=4,
        selected_text="A auto-ampliação técnica ocorre sem decisão deliberada.",
        rects=[{"x": 0.12, "y": 0.14, "w": 0.52, "h": 0.04}],
        color="#bbf7d0",
    )
    hl_pdf_b_p3 = annotations_service.create_highlight(
        book_id="book_pdf_b",
        user_id=alice["id"],
        page=3,
        selected_text="A concretização do objeto técnico supera a alienação.",
        rects=[{"x": 0.12, "y": 0.14, "w": 0.52, "h": 0.04}],
        color="#bfdbfe",
    )
    hl_epub_cross = annotations_service.create_highlight(
        book_id="book_epub_cross",
        user_id=alice["id"],
        page=1,
        selected_text="A essência da técnica moderna como Gestell.",
        rects=[],
        epub_cfi="epubcfi(/6/2[ch1]!/4/4,/1:0,/1:40)",
        chapter_label="Capítulo 1",
        color="#fef08a",
    )
    note_pdf_a_p1 = annotations_service.create_note(
        book_id="book_pdf_a",
        user_id=alice["id"],
        page=1,
        content="Nota central sobre autonomia técnica em Ellul.",
    )
    # Seed a backlink pointing TO hl_pdf_a_p1 from note_pdf_a_p1 so Backlinks tab has data
    knowledge_service.create_knowledge_link(
        user_id=alice["id"],
        source_type="note",
        source_id=note_pdf_a_p1["id"],
        target_type="highlight",
        target_id=hl_pdf_a_p1["id"],
        relation_type="develops",
        rationale="Esta nota desenvolve o argumento do destaque na página 1.",
    )

    token_alice = auth_service.create_session(alice["id"])
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

    assert started, "Servidor E2E v1.9.0 PDF não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "token_alice": token_alice,
            "hl_pdf_a_p1": hl_pdf_a_p1,
            "hl_pdf_a_p4": hl_pdf_a_p4,
            "hl_pdf_b_p3": hl_pdf_b_p3,
            "hl_epub_cross": hl_epub_cross,
            "note_pdf_a_p1": note_pdf_a_p1,
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


def _setup_auth_context(playwright, base_url: str, token: str, viewport: dict):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport=viewport)
    context.add_cookies(
        [
            {"name": "lectoria_session", "value": token, "url": base_url},
            {"name": "lectoria_csrf", "value": "csrf_e2e_pdf_token", "url": base_url},
        ]
    )
    return browser, context


def test_v190_pdf_knowledge_full_flow_and_responsiveness(pdf_knowledge_server):
    base_url = pdf_knowledge_server["base_url"]
    token = pdf_knowledge_server["token_alice"]

    with sync_playwright() as p:
        browser, context = _setup_auth_context(p, base_url, token, {"width": 1366, "height": 860})
        page = context.new_page()

        knowledge_requests_before_open = []
        page.on(
            "request",
            lambda req: knowledge_requests_before_open.append(req.url)
            if "/api/knowledge/" in req.url
            else None,
        )

        page.goto(f"{base_url}/reader/book_pdf_a", wait_until="networkidle")
        page.wait_for_selector(".highlight-box", timeout=15000)

        # 1. Lazy loading: zero /api/knowledge/* requests before opening panel
        assert len(knowledge_requests_before_open) == 0

        # 2. Open highlight popover -> click "Conhecimento"
        page.locator(".highlight-box").first.click()
        page.wait_for_selector("#highlight-popover", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        # Verify incoming backlink from note_pdf_a_p1 is loaded
        page.wait_for_function(
            "() => document.getElementById('rk-tab-incoming').textContent.includes('(1)')",
            timeout=8000,
        )

        # 3. Create new concept contextually and associate it
        page.click("#rk-btn-toggle-create-concept")
        page.fill("#rk-concept-name", "Autonomia da Técnica")
        page.fill("#rk-concept-desc", "Fenômeno em que o sistema técnico define seus próprios fins.")
        page.fill("#rk-concept-tags", "filosofia, modernidade")
        page.click("#rk-btn-submit-create-concept")

        page.wait_for_selector(".rk-concept-chip", timeout=8000)
        assert "Autonomia da Técnica" in page.locator("#rk-associated-concepts").inner_text()

        # Click chip to preview concept description and tags
        page.locator(".rk-concept-chip-btn").first.click()
        page.wait_for_selector("#rk-concept-preview:not(.hidden)", timeout=5000)
        assert "próprios fins" in page.locator("#rk-concept-preview").inner_text()
        assert "#filosofia" in page.locator("#rk-concept-preview").inner_text()

        # Unlink concept (must NOT delete concept globally)
        page.locator(".rk-concept-unlink-btn").first.click()
        page.wait_for_function(
            "() => document.querySelectorAll('#rk-associated-concepts .rk-concept-chip').length === 0",
            timeout=8000,
        )

        # Re-associate same concept via assisted search (proving concept still exists in Atlas)
        page.fill("#rk-concept-search", "Autonomia")
        page.wait_for_selector("#rk-concept-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-concept-search-results .rk-lookup-item").first.click()
        page.wait_for_selector(".rk-concept-chip", timeout=8000)

        # 4. Create semantic connection: same-book PDF p1 -> p4 (complements)
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "highlight")
        page.fill("#rk-target-search", "auto-ampliação")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "complements")
        page.fill("#rk-rationale-input", "A página 4 expande a tese da autonomia da página 1.")
        page.click("#rk-btn-submit-new-link")

        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Complementa')",
            timeout=8000,
        )

        # 5. Create cross-book PDF -> PDF connection (book_pdf_a -> book_pdf_b p3)
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "highlight")
        page.fill("#rk-target-search", "concretização")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "contradicts")
        page.fill("#rk-rationale-input", "Simondon contrapõe a visão determinista de Ellul.")
        page.click("#rk-btn-submit-new-link")

        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Contradiz')",
            timeout=8000,
        )

        # 6. Create cross-format PDF -> EPUB connection (book_pdf_a -> book_epub_cross)
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "highlight")
        page.fill("#rk-target-search", "Gestell")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "grounds")
        page.fill("#rk-rationale-input", "Heidegger oferece base ontológica para a discussão.")
        page.click("#rk-btn-submit-new-link")

        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Fundamenta')",
            timeout=8000,
        )

        # 7. Edit rationale inline via PATCH
        first_card = page.locator(".rk-link-card").first
        first_card.locator(".rk-btn-edit-link").click()
        first_card.locator(".rk-edit-rationale-input").fill("Justificativa revisada na Fase 3.")
        first_card.locator(".rk-btn-save-edit").click()
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Justificativa revisada na Fase 3.')",
            timeout=8000,
        )

        # Capture Desktop screenshot (PDF concepts + connections)
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase3_pdf_knowledge_desktop.png"))

        # Check Incoming Backlinks tab
        page.click("#rk-tab-incoming")
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Desenvolvido por')",
            timeout=5000,
        )
        page.click("#rk-tab-outgoing")

        # Capture Tablet screenshot (768x1024) and verify zero horizontal overflow
        page.set_viewport_size({"width": 768, "height": 1024})
        time.sleep(0.2)
        overflow_tablet = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not overflow_tablet, "Overflow horizontal detectado no layout tablet (768px)."
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase3_knowledge_tablet.png"))

        # Capture Mobile screenshot (360x800) and verify zero horizontal overflow
        page.set_viewport_size({"width": 360, "height": 800})
        time.sleep(0.2)
        overflow_mobile = page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not overflow_mobile, "Overflow horizontal detectado no layout mobile (360px)."
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase3_knowledge_mobile.png"))

        # Restore desktop viewport and test Escape keyboard closure
        page.set_viewport_size({"width": 1366, "height": 860})
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-reader-knowledge.hidden", state="hidden", timeout=5000)

        # 8. Test same-book PDF navigation (p1 -> p4 in-place)
        page.locator(".highlight-box").first.click()
        page.wait_for_selector("#highlight-popover", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Complementa')",
            timeout=8000,
        )

        same_book_card = page.locator(".rk-link-card", has_text="auto-ampliação").first
        same_book_card.locator(".rk-btn-navigate").click()
        page.wait_for_selector("#modal-reader-knowledge.hidden", state="hidden", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('page-num-input') && document.getElementById('page-num-input').value === '4'",
            timeout=8000,
        )

        # 9. Test cross-book PDF -> PDF navigation (book_pdf_a -> book_pdf_b#page=3)
        hl_p1_id = pdf_knowledge_server["hl_pdf_a_p1"]["id"]
        page.fill("#page-num-input", "1")
        page.locator("#page-num-input").press("Enter")
        page.wait_for_selector(f'.highlight-box[data-highlight-id="{hl_p1_id}"]', timeout=10000)
        page.locator(f'.highlight-box[data-highlight-id="{hl_p1_id}"]').first.click()
        page.wait_for_selector("#highlight-popover", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('concretização')",
            timeout=8000,
        )

        cross_pdf_card = page.locator(".rk-link-card", has_text="concretização").first
        cross_pdf_card.locator(".rk-btn-navigate").click()
        page.wait_for_url("**/reader/book_pdf_b#page=3", timeout=10000)
        page.wait_for_function(
            "() => document.getElementById('page-num-input') && document.getElementById('page-num-input').value === '3'",
            timeout=10000,
        )

        # 10. Test cross-format PDF -> EPUB navigation (book_pdf_a -> book_epub_cross#cfi=...)
        page.goto(f"{base_url}/reader/book_pdf_a#page=1", wait_until="networkidle")
        page.wait_for_selector(".highlight-box", timeout=15000)
        page.locator(".highlight-box").first.click()
        page.wait_for_selector("#highlight-popover", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Gestell')",
            timeout=8000,
        )

        cross_epub_card = page.locator(".rk-link-card", has_text="Gestell").first
        cross_epub_card.locator(".rk-btn-navigate").click()
        page.wait_for_url("**/reader/book_epub_cross#cfi=*", timeout=10000)
        assert "#cfi=" in page.url

        browser.close()
