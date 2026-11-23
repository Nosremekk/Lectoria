"""
Suíte de Testes E2E (Playwright) — Fase 6 da Lectoria v1.9.0 (`tests/e2e/test_v190_atlas_search.py`).
Valida a Pesquisa Integrada do Atlas (`#atlas-subtab-search`, `#atlas-pane-search`):
- Pesquisa retornando resultados variados (conceito, reflexão, destaque PDF, nota EPUB, conexão e livro).
- Insensibilidade a acentos/cedilha/caixa na interface (`"tecnica organizacao"` -> encontra `"Técnica"` e `"Organização"`).
- Destaque seguro de trechos via `<mark class="atlas-search-mark">` (zero `innerHTML`).
- Filtragem por tipo de entidade (`concept`, `reflection`, `highlight`, `note`, `link`, `book`).
- Estado de pesquisa vazia / sem resultados.
- Navegação para conceito, reflexão, leitor PDF (`#page=N`), leitor EPUB (`#cfi=...`) e transição direta `"Explorar no Mapa"`.
- Captura das 4 evidências visuais de pesquisa da Seção 41 (itens 1 a 4).
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
import library


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
    for idx in range(1, 4):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 16 Tf 72 740 Td (Pagina {idx} - Tecnica e Organizacao no Atlas) Tj ET".encode("latin1"))
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
    <dc:title>{title}</dc:title><dc:creator>Martin Heidegger</dc:creator><dc:language>pt-BR</dc:language>
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
<body><h1>Cap 1</h1><p id="p1">A técnica moderna desvela a organização como disponibilidade.</p></body></html>"""
    with zipfile.ZipFile(dest_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)


@pytest.fixture(scope="module")
def atlas_search_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p6_search_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "ellul_tecnica_organizacao.pdf")
    _create_synthetic_epub(lib_dir / "heidegger_tecnica.epub", "A Questão da Técnica e Organização")

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
    library.scan_library()

    books = library.get_books()
    pdf_book = next(b for b in books if b["format"] == "pdf")
    epub_book = next(b for b in books if b["format"] == "epub")

    alice = auth_service.create_user("alice_p6_e2e_search", "Password123!", "Alice Search E2E", role="admin")
    c1 = knowledge_service.create_concept(
        alice["id"],
        "Autonomia da Técnica",
        description="Conceito sobre como a técnica moderna reorganiza as instituições e a organização social.",
        tags=["filosofia", "civilização"],
    )
    r1 = knowledge_service.create_reflection(
        alice["id"],
        "Ensaio sobre Técnica e Organização Racional",
        "# Técnica e Organização\n\nAnálise comparada sobre a evolução da técnica e da organização.",
        primary_book_id=pdf_book["id"],
        tags=["síntese"],
        is_pinned=True,
    )
    hl1 = annotations_service.create_highlight(
        book_id=pdf_book["id"],
        user_id=alice["id"],
        page=2,
        selected_text="A técnica impõe sua própria lógica de organização eficiente.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        chapter_label="Capítulo II",
        visibility="private",
    )
    nt1 = annotations_service.create_note(
        book_id=epub_book["id"],
        user_id=alice["id"],
        page=1,
        content="Nota EPUB: a técnica transforma a organização do mundo em estoque.",
        epub_cfi="epubcfi(/6/2[ch1]!/4/2[p1]/1:0)",
        chapter_label="Cap 1",
        visibility="private",
    )
    lnk1 = knowledge_service.create_knowledge_link(
        alice["id"],
        "reflection",
        r1["id"],
        "concept",
        c1["id"],
        "grounds",
        rationale="Esta reflexão fundamenta a tese sobre técnica e organização.",
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

    assert started, "Servidor E2E Fase 6 Search não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "pdf_book": pdf_book,
            "epub_book": epub_book,
            "c1": c1,
            "r1": r1,
            "hl1": hl1,
            "nt1": nt1,
            "lnk1": lnk1,
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


def test_v190_atlas_integrated_search_and_navigation(atlas_search_server):
    base_url = atlas_search_server["base_url"]
    token = atlas_search_server["token_alice"]
    pdf_book = atlas_search_server["pdf_book"]
    epub_book = atlas_search_server["epub_book"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p6_search_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        # Usa tema claro para as capturas desktop principais e verifica estado inicial da aba Pesquisa
        page.goto(f"{base_url}/?tab=atlas&subtab=search", wait_until="networkidle")
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-integrated-search-input", timeout=8000)

        # 1. Pesquisa sem resultados ("Busca vazia / sem correspondências" — Screenshot #2)
        page.fill("#atlas-integrated-search-input", "termoinexistente_xyz")
        page.click("#atlas-btn-execute-search")
        page.wait_for_selector("#atlas-search-empty-results", timeout=8000)
        assert "Nenhum resultado autorizado" in page.locator("#atlas-search-empty-results").inner_text()
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_02_search_empty_desktop.png"))

        # 2. Pesquisa com resultados variados e sem acentos ("tecnica organizacao" — Screenshot #1 e #4)
        page.fill("#atlas-integrated-search-input", "tecnica organizacao")
        page.click("#atlas-btn-execute-search")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card').length >= 5",
            timeout=8000,
        )

        # Verifica presença de tags <mark class="atlas-search-mark"> nos snippets seguros
        marks_count = page.locator("#atlas-search-results-list mark.atlas-search-mark").count()
        assert marks_count >= 5

        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_01_search_varied_results_desktop.png"))
        page.locator("#atlas-search-results-list .atlas-search-result-card").first.screenshot(
            path=str(ARTIFACT_DIR / "v190_phase6_04_search_snippet_highlight_desktop.png")
        )

        # 3. Pesquisa com filtro por tipo ("concept" — Screenshot #3)
        page.select_option("#atlas-integrated-search-type", "concept")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card').length === 1",
            timeout=8000,
        )
        first_card_type = page.locator("#atlas-search-results-list .atlas-search-result-card").first.get_attribute("data-entity-type")
        assert first_card_type == "concept"
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_03_search_filtered_desktop.png"))

        # 4. Verifica links de navegação para leitor PDF (#page=2) e leitor EPUB (#cfi=...)
        page.select_option("#atlas-integrated-search-type", "highlight")
        page.wait_for_function(
            "() => { const cards = document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card'); return cards.length === 1 && cards[0].getAttribute('data-entity-type') === 'highlight'; }",
            timeout=8000,
        )
        hl_href = page.locator("#atlas-search-results-list .rk-btn-navigate").first.get_attribute("href")
        assert f"/reader/{pdf_book['id']}#page=2" in hl_href

        page.select_option("#atlas-integrated-search-type", "note")
        page.wait_for_function(
            "() => { const cards = document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card'); return cards.length === 1 && cards[0].getAttribute('data-entity-type') === 'note'; }",
            timeout=8000,
        )
        nt_href = page.locator("#atlas-search-results-list .rk-btn-navigate").first.get_attribute("href")
        assert f"/reader/{epub_book['id']}#cfi=" in nt_href

        # 5. Transição direta "Explorar no Mapa" a partir de um resultado da pesquisa
        page.select_option("#atlas-integrated-search-type", "concept")
        page.wait_for_function(
            "() => { const cards = document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card'); return cards.length === 1 && cards[0].getAttribute('data-entity-type') === 'concept'; }",
            timeout=8000,
        )
        page.locator("#atlas-search-results-list .atlas-btn-open-in-graph").first.click()
        page.wait_for_selector("#atlas-graph-svg", timeout=8000)
        assert "Autonomia da Técnica" in page.locator("#atlas-graph-detail-panel").inner_text()

        browser.close()
