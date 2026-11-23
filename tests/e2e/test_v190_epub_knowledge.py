"""
Suíte de Testes E2E (Playwright) — Fase 3 da Lectoria v1.9.0.
Valida a integração do Atlas (Conhecimento Conectado) no Leitor EPUB:
- Abertura contextual do painel Conhecimento a partir de anotação EPUB (nota/destaque com CFI).
- Associação e criação de Conceitos em EPUB.
- Conexões semânticas EPUB -> EPUB (mesmo livro e livros distintos) e EPUB -> PDF (cross-format).
- Consulta de Backlinks recebidos e exclusão com confirmação inline.
- Navegação segura EPUB -> PDF preservando progresso.
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


def _create_text_pdf(dest_path: Path, num_pages: int = 3):
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
        text = f"PDF Cruzado - Pagina {idx}: Argumento complementar."
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
    <dc:creator>Autor EPUB</dc:creator>
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
  <nav epub:type="toc" id="toc">
    <ol>
      <li><a href="ch1.xhtml">Capítulo 1: Princípios</a></li>
      <li><a href="ch2.xhtml">Capítulo 2: Desdobramentos</a></li>
    </ol>
  </nav>
</body>
</html>"""
    ch1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 1</title></head>
<body>
  <h1>Capítulo 1: Princípios</h1>
  <p id="p1">A leitura conectada permite articular passagens de obras distintas em EPUB e PDF.</p>
</body>
</html>"""
    ch2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 2</title></head>
<body>
  <h1>Capítulo 2: Desdobramentos</h1>
  <p id="p2">No segundo capítulo examinamos as consequências práticas da síntese conceitual.</p>
</body>
</html>"""
    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)
        zf.writestr("OEBPS/ch2.xhtml", ch2_xhtml)


@pytest.fixture(scope="module")
def epub_knowledge_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p3_epub_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_synthetic_epub(lib_dir / "obra_epub_a.epub", "Filosofia do Método")
    _create_synthetic_epub(lib_dir / "obra_epub_b.epub", "Cadernos de Epistemologia")
    _create_text_pdf(lib_dir / "obra_pdf_cruzada.pdf", num_pages=3)

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
        username="alice_v190_epub",
        password="Password123!",
        display_name="Alice Lemos",
        role="admin",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES (?, 'Alice Lemos', '#d4a853', 'Leitora');",
            (alice["id"],),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo v190 EPUB', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_epub_a', 'Filosofia do Método', 'Autor A', 'obra_epub_a.epub', 'obra_epub_a.epub', ?, 'epub', 0, 1, 0),
            ('book_epub_b', 'Cadernos de Epistemologia', 'Autor B', 'obra_epub_b.epub', 'obra_epub_b.epub', ?, 'epub', 0, 1, 0),
            ('book_pdf_cross', 'Tratado de Síntese', 'Autor C', 'obra_pdf_cruzada.pdf', 'obra_pdf_cruzada.pdf', ?, 'pdf', 3, 1, 0);
            """,
            (
                (lib_dir / "obra_epub_a.epub").stat().st_size,
                (lib_dir / "obra_epub_b.epub").stat().st_size,
                (lib_dir / "obra_pdf_cruzada.pdf").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Seed persisted EPUB note on ch1 (appears as .page-note-pin in #epub-note-pins)
    note_epub_a = annotations_service.create_note(
        book_id="book_epub_a",
        user_id=alice["id"],
        page=1,
        content="Nota EPUB sobre método e síntese conceitual.",
        epub_cfi="epubcfi(/6/2[ch1]!/4/4,/1:0,/1:35)",
        chapter_label="Capítulo 1: Princípios",
    )
    note_epub_b = annotations_service.create_note(
        book_id="book_epub_b",
        user_id=alice["id"],
        page=1,
        content="Nota em Cadernos de Epistemologia sobre critérios de validação.",
        epub_cfi="epubcfi(/6/2[ch1]!/4/4,/1:0,/1:30)",
        chapter_label="Capítulo 1: Princípios",
    )
    hl_pdf_cross = annotations_service.create_highlight(
        book_id="book_pdf_cross",
        user_id=alice["id"],
        page=2,
        selected_text="A síntese documental exige rastreabilidade entre fontes.",
        rects=[{"x": 0.12, "y": 0.14, "w": 0.5, "h": 0.04}],
        color="#fef08a",
    )

    # Seed an existing concept and an incoming backlink to note_epub_a
    concept = knowledge_service.create_concept(
        user_id=alice["id"],
        name="Epistemologia",
        description="Teoria do conhecimento e critérios de justificação.",
        tags=["metodo"],
    )
    knowledge_service.create_knowledge_link(
        user_id=alice["id"],
        source_type="note",
        source_id=note_epub_b["id"],
        target_type="note",
        target_id=note_epub_a["id"],
        relation_type="references",
        rationale="Cadernos de Epistemologia referencia a nota inicial de Filosofia do Método.",
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

    assert started, "Servidor E2E v1.9.0 EPUB não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "concept": concept,
            "note_epub_a": note_epub_a,
            "note_epub_b": note_epub_b,
            "hl_pdf_cross": hl_pdf_cross,
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


def test_v190_epub_knowledge_and_cross_format_navigation(epub_knowledge_server):
    base_url = epub_knowledge_server["base_url"]
    token = epub_knowledge_server["token_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_e2e_epub_token", "url": base_url},
            ]
        )
        page = context.new_page()

        page.goto(f"{base_url}/reader/book_epub_a", wait_until="networkidle")
        page.wait_for_selector(".page-note-pin", timeout=15000)

        # Open EPUB note popover -> click "Conhecimento"
        page.locator(".page-note-pin").first.click()
        page.wait_for_selector("#page-note-popover", timeout=5000)
        page.locator("#note-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        # Associate existing concept "Epistemologia"
        page.fill("#rk-concept-search", "Epistemologia")
        page.wait_for_selector("#rk-concept-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-concept-search-results .rk-lookup-item").first.click()
        page.wait_for_selector(".rk-concept-chip", timeout=8000)
        assert "Epistemologia" in page.locator("#rk-associated-concepts").inner_text()

        # Create cross-book EPUB -> EPUB connection (book_epub_a -> book_epub_b)
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "note")
        page.fill("#rk-target-search", "critérios de validação")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "complements")
        page.fill("#rk-rationale-input", "Articula dois livros EPUB sobre método.")
        page.click("#rk-btn-submit-new-link")
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Complementa')",
            timeout=8000,
        )

        # Create cross-format EPUB -> PDF connection (book_epub_a -> book_pdf_cross p2)
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "highlight")
        page.fill("#rk-target-search", "rastreabilidade")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "exemplifies")
        page.fill("#rk-rationale-input", "O destaque no PDF exemplifica a síntese documental.")
        page.click("#rk-btn-submit-new-link")
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Exemplifica')",
            timeout=8000,
        )

        # Capture Desktop screenshot for EPUB Knowledge modal
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase3_epub_knowledge_desktop.png"))

        # Check Backlinks recebidos tab (from note_epub_b)
        page.click("#rk-tab-incoming")
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Referenciado por')",
            timeout=8000,
        )

        # Delete the EPUB -> EPUB outgoing link with inline confirmation
        page.click("#rk-tab-outgoing")
        epub_b_card = page.locator(".rk-link-card", has_text="critérios de validação").first
        epub_b_card.locator(".rk-btn-delete-link").click()
        epub_b_card.locator(".rk-btn-confirm-delete").click()
        page.wait_for_function(
            "() => !document.getElementById('rk-links-list').innerText.includes('critérios de validação')",
            timeout=8000,
        )

        # Navigate cross-format EPUB -> PDF (book_pdf_cross#page=2)
        pdf_card = page.locator(".rk-link-card", has_text="rastreabilidade").first
        pdf_card.locator(".rk-btn-navigate").click()
        page.wait_for_url("**/reader/book_pdf_cross#page=2", timeout=10000)
        page.wait_for_function(
            "() => document.getElementById('page-num-input') && document.getElementById('page-num-input').value === '2'",
            timeout=10000,
        )

        browser.close()
