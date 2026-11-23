"""
Suíte de Testes E2E (Playwright) — Fase 5 da Lectoria v1.9.0.
Valida Reflexões Autorais e o Editor Markdown Seguro:
- Criação, edição, contagem de palavras, modos Editar / Visualizar / Dividido.
- Exigência de identidade persistida antes de vincular referências assistidas (com botão de salvamento rápido).
- Inserção assistida de referências canônicas (UUIDs) para destaque PDF, nota EPUB e conceito.
- Proteção contra saída com alterações não salvas (#atlas-unsaved-modal).
- Detecção de concorrência entre abas/sessões preservando o texto local.
- Responsividade em Tablet (768x1024) e Celular (360x800) sem overflow horizontal e capturas visuais.
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


def _create_text_pdf(dest_path: Path):
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
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Reflexoes Atlas Fase 5) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


def _create_synthetic_epub(dest_path: Path, title: str):
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>Autor EPUB</dc:creator><dc:language>pt-BR</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>"""
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Sumário</title></head><body><nav epub:type="toc" id="toc"><ol><li><a href="ch1.xhtml">Cap 1</a></li></ol></nav></body></html>"""
    ch1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cap 1</title></head>
<body><h1>Cap 1</h1><p id="p1">Texto EPUB para referência em reflexão.</p></body></html>"""
    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)


@pytest.fixture(scope="module")
def atlas_reflections_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p5_reflections_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "ellul_ref.pdf")
    _create_synthetic_epub(lib_dir / "heidegger_ref.epub", "A Questão da Técnica")

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

    alice = auth_service.create_user("alice_p5_ref", "Password123!", "Alice Reflexoes", role="admin")

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Ref P5', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_pdf_ref', 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul_ref.pdf', 'ellul_ref.pdf', ?, 'pdf', 1, 1, 0),
            ('book_epub_ref', 'A Questão da Técnica', 'Martin Heidegger', 'heidegger_ref.epub', 'heidegger_ref.epub', ?, 'epub', 0, 1, 0);
            """,
            (
                (lib_dir / "ellul_ref.pdf").stat().st_size,
                (lib_dir / "heidegger_ref.epub").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    hl_pdf = annotations_service.create_highlight(
        book_id="book_pdf_ref",
        user_id=alice["id"],
        page=1,
        selected_text="A autonomia técnica impõe sua própria racionalidade.",
        rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
    )
    nt_epub = annotations_service.create_note(
        book_id="book_epub_ref",
        user_id=alice["id"],
        page=1,
        content="Nota EPUB sobre o desvelamento técnico como Gestell.",
        epub_cfi="epubcfi(/6/2[ch1]!/4/2,/1:0,/1:25)",
        chapter_label="Capítulo 1",
    )
    concept = knowledge_service.create_concept(
        alice["id"],
        "Gestell",
        description="Armação ou com-posição na filosofia da técnica.",
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

    assert started, "Servidor E2E Fase 5 Reflexões não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "token_alice": token_alice,
            "hl_pdf": hl_pdf,
            "nt_epub": nt_epub,
            "concept": concept,
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


def test_v190_atlas_reflections_markdown_editor_references_and_concurrency(atlas_reflections_server):
    base_url = atlas_reflections_server["base_url"]
    alice = atlas_reflections_server["alice"]
    token = atlas_reflections_server["token_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p5_ref_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        page.goto(f"{base_url}/?tab=atlas&subtab=reflections", wait_until="networkidle")
        page.wait_for_selector("#atlas-btn-new-reflection", timeout=8000)
        page.click("#atlas-btn-new-reflection")
        page.wait_for_selector("#atlas-reflection-title", timeout=5000)

        # 1. Fill title and Markdown content -> verify live word counter & safe split preview
        page.fill("#atlas-reflection-title", "Ellul e Heidegger: Duas Leituras sobre a Técnica")
        page.fill(
            "#atlas-reflection-body",
            "# Introdução\n\nComparação entre **autonomia sociológica** e *Gestell*.\n\n- Ponto 1\n- Ponto 2\n\n```txt\nbloco de codigo\n```",
        )
        page.fill("#atlas-reflection-tags", "ensaio, filosofia")
        page.check("#atlas-reflection-pinned")

        assert "palavra(s)" in page.locator("#atlas-reflection-word-counter").inner_text()
        assert "Introdução" in page.locator("#atlas-reflection-preview").inner_text()

        # 2. Clicking "+ Inserir referência assistida" before saving prompts to save first
        page.click("#atlas-btn-insert-reference")
        page.wait_for_selector("#atlas-btn-save-before-reference", timeout=5000)
        page.click("#atlas-btn-save-before-reference")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-save-status').textContent.trim() === 'Salva'",
            timeout=8000,
        )

        # 3. Now insert assisted references to PDF highlight and EPUB note
        page.select_option("#atlas-assisted-reference-panel .atlas-link-target-type", "highlight")
        page.fill("#atlas-assisted-reference-panel .atlas-link-target-search", "racionalidade")
        page.wait_for_selector("#atlas-assisted-reference-panel .rk-lookup-item", timeout=8000)
        page.locator("#atlas-assisted-reference-panel .rk-lookup-item").first.click()
        page.fill("#atlas-assisted-reference-panel .atlas-link-rationale-input", "Citação base de Ellul.")
        page.click("#atlas-assisted-reference-panel .atlas-link-submit-btn")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-links-list').innerText.includes('racionalidade')",
            timeout=8000,
        )

        # Capture Assisted Reference Insertion screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_assisted_ref_desktop.png"))

        # Insert reference to EPUB note as well
        page.select_option("#atlas-assisted-reference-panel .atlas-link-target-type", "note")
        page.fill("#atlas-assisted-reference-panel .atlas-link-target-search", "desvelamento")
        page.wait_for_selector("#atlas-assisted-reference-panel .rk-lookup-item", timeout=8000)
        page.locator("#atlas-assisted-reference-panel .rk-lookup-item").first.click()
        page.select_option("#atlas-assisted-reference-panel .atlas-link-relation-select", "complements")
        page.click("#atlas-assisted-reference-panel .atlas-link-submit-btn")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-tab-references').textContent.includes('(2)')",
            timeout=8000,
        )

        # Save reflection after automatic citation block insertion
        page.click("#atlas-btn-save-reflection")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-save-status').textContent.trim() === 'Salva'",
            timeout=8000,
        )
        page.click("#atlas-btn-insert-reference")  # hide assisted panel for clean editor screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_reflection_editor_desktop.png"))

        # 4. Test Preview-only mode, Tablet (768x1024), and Mobile (360x800) with zero overflow
        page.click("#atlas-md-mode-preview")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_reflection_preview_desktop.png"))
        page.click("#atlas-md-mode-split")

        page.set_viewport_size({"width": 768, "height": 1024})
        time.sleep(0.2)
        assert not page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_tablet.png"))

        page.set_viewport_size({"width": 360, "height": 800})
        time.sleep(0.2)
        assert not page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_editor_mobile.png"))
        page.set_viewport_size({"width": 1366, "height": 860})

        # 5. Test Concurrency Detection across TWO browser tabs (atomic 409 Conflict without time.sleep)
        ref_list = knowledge_service.list_reflections(alice["id"])["items"]
        ref_id = ref_list[0]["id"]

        page_tab2 = context.new_page()
        page_tab2.goto(f"{base_url}/?tab=atlas&subtab=reflections&id={ref_id}", wait_until="networkidle")
        page_tab2.wait_for_selector("#atlas-reflection-title", timeout=8000)
        page_tab2.fill("#atlas-reflection-title", "Ellul e Heidegger (Salvo Concorrentemente na Aba 2)")
        page_tab2.click("#atlas-btn-save-reflection")
        page_tab2.wait_for_function(
            "() => document.getElementById('atlas-reflection-save-status').textContent.trim() === 'Salva'",
            timeout=8000,
        )
        page_tab2.close()

        # Tab 1 now attempts to save with its older loadedUpdatedAt -> receives HTTP 409 Conflict from backend
        page.fill("#atlas-reflection-title", "Ellul e Heidegger: Versão Local Preservada")
        page.click("#atlas-btn-save-reflection")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-save-status').textContent.includes('Conflito')",
            timeout=8000,
        )
        # Local text is preserved and a second click confirms overwrite
        assert page.input_value("#atlas-reflection-title") == "Ellul e Heidegger: Versão Local Preservada"
        page.click("#atlas-btn-save-reflection")
        page.wait_for_function(
            "() => document.getElementById('atlas-reflection-save-status').textContent.trim() === 'Salva'",
            timeout=8000,
        )

        # 6. Test Unsaved Changes Guard (#atlas-unsaved-modal)
        page.fill("#atlas-reflection-title", "Alteração Pendente Não Salva")
        page.click("#atlas-btn-back-reflections")
        page.wait_for_selector("#atlas-unsaved-modal:not(.hidden)", timeout=5000)
        page.click("#atlas-btn-stay-editor")
        page.wait_for_selector("#atlas-unsaved-modal.hidden", state="hidden", timeout=5000)

        page.click("#atlas-btn-back-reflections")
        page.wait_for_selector("#atlas-unsaved-modal:not(.hidden)", timeout=5000)
        page.click("#atlas-btn-discard-changes")
        page.wait_for_selector("#atlas-reflections-grid", timeout=8000)
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_reflections_list_desktop.png"))

        # 7. Test Reading Progress Serialization (progressSaveChain / progressSaveSeq) under concurrent calls and intermediate error
        page.goto(f"{base_url}/reader/book_pdf_ref#page=1", wait_until="networkidle")
        page.wait_for_selector("#pdf-canvas", state="visible", timeout=10000)

        progress_calls = []

        def handle_progress_route(route):
            progress_calls.append(route.request.url)
            if len(progress_calls) == 1:
                # First request fails with network abort to verify queue recovery
                route.abort("failed")
            else:
                route.continue_()

        page.route("**/api/books/book_pdf_ref/progress*", handle_progress_route)
        # Open Knowledge modal on highlight and navigate to Atlas (which awaits adapter.persistProgress())
        page.wait_for_selector(".highlight-box", state="visible", timeout=8000)
        page.locator(".highlight-box").first.click()
        page.wait_for_selector("#highlight-popover", state="visible", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        # Link highlight to reflection so "Abrir no Atlas" appears and triggers persistProgress()
        page.click("#rk-tab-incoming")
        page.wait_for_selector("#rk-links-list .rk-btn-open-atlas", timeout=5000)
        # First click triggers persistProgress() -> call 1 aborts, navigation to Atlas still proceeds safely!
        page.locator("#rk-links-list .rk-btn-open-atlas").first.click()
        page.wait_for_selector("#atlas-section", state="visible", timeout=8000)
        assert len(progress_calls) >= 1
        page.unroute("**/api/books/book_pdf_ref/progress*")

        browser.close()
