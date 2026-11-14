"""
Suíte de Testes End-to-End (E2E) com Playwright — Fase 5 da Lectoria v1.7.0.
Valida a integração da Leitura Compartilhada com os leitores PDF.js e EPUB.js:
- Seleção de texto no PDF.js e EPUB.js -> "Compartilhar na Leitura Coletiva" -> modal de confirmação.
- Seleção de leitura coletiva elegível (ocultando canceladas, concluídas, arquivadas e sem vínculo).
- Validação de limite de 1500 caracteres sem truncamento silencioso e cancelamento sem publicar.
- Compartilhamento de nota privada própria a partir da gaveta de estudo (snapshot independente).
- Isolamento entre usuários (não-membro não vê botão; membro vê apenas seus clubes elegíveis).
- Navegação por citação: #page=N (PDF) e #cfi=... (EPUB), além de fallback toast para página/CFI inválido.
- Responsividade (320px, 375px, 412px, 1280px) e temas claro/escuro sem overflow horizontal.
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


EVIDENCE_DIR = Path("/tmp/lectoria_v170_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR = Path("/home/vini/.gemini/antigravity/brain/9ae11ae2-ebc0-44c2-9e79-22a1b0910e27")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_text_pdf(dest_path: Path, num_pages: int = 10):
    """Cria um PDF sintético de N páginas com texto real selecionável no textLayer do PDF.js."""
    import pypdf
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

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
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_ref}
                )
            }
        )
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        text = f"Capitulo {idx}: Trecho literario selecionavel na pagina {idx} do Lectoria."
        stream.set_data(
            f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


def _create_synthetic_epub(dest_path: Path, title: str = "Obra EPUB Teste"):
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
    <nav epub:type="toc" id="toc">
        <h1>Sumário</h1>
        <ol>
            <li><a href="ch1.xhtml">Capítulo 1</a></li>
            <li><a href="ch2.xhtml">Capítulo 2</a></li>
        </ol>
    </nav>
</body>
</html>"""

    ch1_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 1</title></head>
<body>
    <h1>Capítulo 1</h1>
    <p id="epub-p1">Esta e uma citacao memoravel do primeiro capitulo em formato EPUB para o clube.</p>
</body>
</html>"""

    ch2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 2</title></head>
<body>
    <h1>Capítulo 2</h1>
    <p id="epub-p2">Segundo capitulo com mais conteudo para navegacao via CFI no Lectoria.</p>
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
def v170_reader_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v170_reader_e2e")
    db_path = root / "lectoria_v170_reader.db"
    covers_dir = root / "covers"
    lib_dir = root / "library"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    _create_text_pdf(lib_dir / "livro_teste.pdf", num_pages=10)
    _create_synthetic_epub(lib_dir / "livro_teste.epub", title="Obra EPUB Teste")

    old_db, old_cov, old_lib = config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    database.init_db(db_path)

    alice = auth_service.create_user(username="alice_r5_e2e", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_r5_e2e", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos_r5_e2e", password="password123", role="reader")

    token_alice = auth_service.create_session(alice["id"])
    token_bob = auth_service.create_session(bob["id"])
    token_carlos = auth_service.create_session(carlos["id"])

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('pdf170r5e2e', 'Dom Casmurro PDF', 'Machado de Assis', 'livro_teste.pdf', 'livro_teste.pdf', 102400, 'pdf', 10, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('epub170r5e2e', 'Obra EPUB Teste', 'Autor Lectoria', 'livro_teste.epub', 'livro_teste.epub', 65536, 'epub', 0, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO notes (id, user_id, book_id, page, content)
            VALUES ('note_e2e_alice_pdf', ?, 'pdf170r5e2e', 3, 'Minha anotacao privada sobre o capitulo 3.');
            """,
            (alice["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    club_a = clubs_service.create_club(
        name="Clube Alfa",
        description="Clube principal",
        visibility="public",
        owner_id=alice["id"],
    )
    clubs_service.join_club(club_a["id"], bob["id"])

    club_b = clubs_service.create_club(
        name="Clube Beta Privado",
        description="Clube exclusivo da Alice",
        visibility="private",
        owner_id=alice["id"],
    )

    club_arch = clubs_service.create_club(
        name="Clube Arquivado",
        description="Arquivado",
        visibility="public",
        owner_id=alice["id"],
    )
    clubs_service.join_club(club_arch["id"], bob["id"])

    r_pdf_a = clubs_service.create_collective_read(
        club_a["id"],
        alice["id"],
        "pdf170r5e2e",
        title="Leitura Alfa PDF",
        status="in_progress",
    )
    r_pdf_b = clubs_service.create_collective_read(
        club_b["id"],
        alice["id"],
        "pdf170r5e2e",
        title="Leitura Beta PDF",
        status="planned",
    )
    clubs_service.create_collective_read(
        club_a["id"],
        alice["id"],
        "pdf170r5e2e",
        title="Leitura Cancelada PDF",
        status="cancelled",
    )
    clubs_service.create_collective_read(
        club_arch["id"],
        alice["id"],
        "pdf170r5e2e",
        title="Leitura Clube Arquivado",
        status="in_progress",
    )
    clubs_service.archive_club(club_arch["id"], alice["id"])

    r_epub_a = clubs_service.create_collective_read(
        club_a["id"],
        alice["id"],
        "epub170r5e2e",
        title="Leitura Alfa EPUB",
        status="in_progress",
    )

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
            "db_path": db_path,
            "alice": alice,
            "bob": bob,
            "carlos": carlos,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "token_carlos": token_carlos,
            "r_pdf_a": r_pdf_a,
            "r_pdf_b": r_pdf_b,
            "r_epub_a": r_epub_a,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR = old_db, old_cov, old_lib


def _save_screenshot(page, filename: str):
    page.wait_for_timeout(300)
    dest = EVIDENCE_DIR / filename
    page.screenshot(path=str(dest), full_page=False)
    if ARTIFACT_DIR.exists():
        shutil.copy2(dest, ARTIFACT_DIR / filename)


def _select_pdf_text_and_trigger_toolbar(page):
    page.wait_for_selector(".textLayer span", timeout=15000)
    page.evaluate(
        """() => {
            const span = document.querySelector('.textLayer span');
            span.scrollIntoView({ block: 'center', inline: 'center' });
            const range = document.createRange();
            range.selectNodeContents(span);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
        }"""
    )
    page.wait_for_selector("#selection-toolbar.active", state="visible", timeout=5000)


def test_v170_reader_shared_quotes_pdf_epub_and_navigation(v170_reader_server):
    srv = v170_reader_server
    base_url = srv["base_url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # =========================================================
            # 1. ALICE (owner de 2 clubes): PDF seleção, validação 1500, nota privada, EPUB CFI
            # =========================================================
            ctx_a = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx_a.add_cookies([{"name": "lectoria_session", "value": srv["token_alice"], "domain": "127.0.0.1", "path": "/"}])
            page_a = ctx_a.new_page()

            page_a.goto(f"{base_url}/reader/pdf170r5e2e")
            page_a.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
            _select_pdf_text_and_trigger_toolbar(page_a)

            # Botão visível para Alice e abre modal com exatamente 2 leituras elegíveis
            page_a.wait_for_selector("#btn-share-selection-club", state="visible")
            page_a.click("#btn-share-selection-club")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="visible")

            opt_count = page_a.locator("#share-quote-read-select option").count()
            assert opt_count == 2, f"Alice deveria ver 2 leituras elegíveis, viu {opt_count}"

            # Evidência desktop PDF modal (tema escuro padrão)
            _save_screenshot(page_a, "v170_reader_pdf_share_desktop.png")

            # Validação >1500 chars sem truncamento silencioso
            page_a.fill("#share-quote-text", "X" * 1501)
            assert len(page_a.input_value("#share-quote-text")) == 1501
            page_a.click("#btn-submit-share-quote")
            page_a.wait_for_selector("#share-quote-error", state="visible")
            assert "1500" in page_a.inner_text("#share-quote-error")

            # Cancelar sem publicar
            page_a.click("#btn-cancel-share-quote")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="detached")

            # Reabrir e publicar trecho válido com comentário e spoiler
            _select_pdf_text_and_trigger_toolbar(page_a)
            page_a.click("#btn-share-selection-club")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="visible")
            page_a.select_option("#share-quote-read-select", srv["r_pdf_a"]["id"])
            page_a.fill("#share-quote-text", "Trecho selecionado do PDF na pagina 1.")
            page_a.fill("#share-quote-comment", "Abertura marcante!")
            page_a.check("#share-quote-spoiler")
            page_a.click("#btn-submit-share-quote")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="detached")
            page_a.wait_for_selector("#reader-toast", state="visible")

            # Compartilhar nota privada própria via gaveta de estudo
            page_a.click("#btn-toggle-drawer")
            page_a.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_a.wait_for_selector(".btn-share-note-club[data-note-id='note_e2e_alice_pdf']", state="visible")
            page_a.click(".btn-share-note-club[data-note-id='note_e2e_alice_pdf']")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="visible")
            assert "Minha anotacao privada" in page_a.input_value("#share-quote-text")
            page_a.select_option("#share-quote-read-select", srv["r_pdf_a"]["id"])
            page_a.click("#btn-submit-share-quote")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="detached")

            # Verifica que a nota privada continua intacta na gaveta e fecha a gaveta
            assert page_a.locator(".btn-share-note-club[data-note-id='note_e2e_alice_pdf']").count() == 1
            page_a.click("#btn-close-drawer")

            # --- Teste Mobile (320px / 375px / 412px) + tema claro sem overflow ---
            for vw, vh, save_shot in [(320, 700, True), (375, 812, False), (412, 915, False)]:
                page_a.set_viewport_size({"width": vw, "height": vh})
                page_a.wait_for_timeout(450)
                page_a.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
                _select_pdf_text_and_trigger_toolbar(page_a)
                page_a.click("#btn-share-selection-club")
                page_a.wait_for_selector("#modal-share-collective-quote.active", state="visible")
                no_overflow = page_a.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                assert no_overflow, f"Overflow horizontal detectado em {vw}px"
                if save_shot:
                    _save_screenshot(page_a, "v170_reader_share_mobile.png")
                page_a.click("#btn-cancel-share-quote")
                page_a.wait_for_selector("#modal-share-collective-quote.active", state="detached")

            page_a.set_viewport_size({"width": 1280, "height": 800})
            page_a.evaluate("() => { document.documentElement.removeAttribute('data-theme'); }")

            # --- EPUB seleção + publicação com epubcfi(...) ---
            page_a.goto(f"{base_url}/reader/epub170r5e2e")
            page_a.wait_for_selector("#epub-viewer iframe", timeout=15000)
            # Aguarda renderização do parágrafo dentro do iframe EPUB.js
            epub_frame = None
            for _ in range(30):
                for fr in page_a.frames:
                    if fr != page_a.main_frame and fr.locator("p").count() > 0:
                        epub_frame = fr
                        break
                if epub_frame:
                    break
                time.sleep(0.25)
            assert epub_frame is not None, "Iframe do EPUB não carregou conteúdo"

            # Simula seleção no EPUB.js disparando evento 'selected' via epubRendition ou seleção no iframe
            epub_frame.evaluate(
                """() => {
                    const p = document.querySelector('p');
                    const range = document.createRange();
                    range.selectNodeContents(p);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                }"""
            )
            page_a.wait_for_selector("#btn-share-selection-club", state="visible", timeout=8000)
            page_a.click("#btn-share-selection-club")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="visible")
            _save_screenshot(page_a, "v170_reader_epub_share_desktop.png")
            page_a.click("#btn-submit-share-quote")
            page_a.wait_for_selector("#modal-share-collective-quote.active", state="detached")
            page_a.wait_for_selector("#reader-toast", state="visible")

            # Obtém o epub_cfi salvo via API
            quotes_epub = page_a.evaluate(
                f"""async () => {{
                    const r = await fetch('/api/collective-reads/{srv["r_epub_a"]["id"]}/quotes');
                    return await r.json();
                }}"""
            )
            assert len(quotes_epub["quotes"]) >= 1
            valid_cfi = quotes_epub["quotes"][0]["epub_cfi"]
            assert valid_cfi and valid_cfi.startswith("epubcfi(")

            # --- Navegação por hash #page=6 e fallback #page=999 ---
            page_a.goto(f"{base_url}/reader/pdf170r5e2e#page=6")
            page_a.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
            page_a.wait_for_function("() => document.getElementById('page-num-input')?.value === '6'", timeout=10000)

            page_a.goto("about:blank")
            page_a.goto(f"{base_url}/reader/pdf170r5e2e#page=999")
            page_a.wait_for_selector("#reader-toast", state="visible", timeout=10000)

            # --- Navegação por hash #cfi=válido e fallback #cfi=inválido ---
            from urllib.parse import quote
            page_a.goto(f"{base_url}/reader/epub170r5e2e#cfi={quote(valid_cfi)}")
            page_a.wait_for_selector("#epub-viewer iframe", timeout=15000)

            page_a.goto("about:blank")
            page_a.goto(f"{base_url}/reader/epub170r5e2e#cfi={quote('epubcfi(/6/99!/4/999)')}")
            page_a.wait_for_selector("#reader-toast", state="visible", timeout=12000)

            ctx_a.close()

            # =========================================================
            # 2. BOB (membro apenas do Clube Alfa): vê apenas 1 leitura
            # =========================================================
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx_b.add_cookies([{"name": "lectoria_session", "value": srv["token_bob"], "domain": "127.0.0.1", "path": "/"}])
            page_b = ctx_b.new_page()
            page_b.goto(f"{base_url}/reader/pdf170r5e2e")
            page_b.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
            _select_pdf_text_and_trigger_toolbar(page_b)
            page_b.wait_for_selector("#btn-share-selection-club", state="visible")
            page_b.click("#btn-share-selection-club")
            page_b.wait_for_selector("#modal-share-collective-quote.active", state="visible")
            assert page_b.locator("#share-quote-read-select option").count() == 1
            ctx_b.close()

            # =========================================================
            # 3. CARLOS (não-membro): NÃO vê botão de compartilhar
            # =========================================================
            ctx_c = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx_c.add_cookies([{"name": "lectoria_session", "value": srv["token_carlos"], "domain": "127.0.0.1", "path": "/"}])
            page_c = ctx_c.new_page()
            page_c.goto(f"{base_url}/reader/pdf170r5e2e")
            page_c.wait_for_selector("#pdf-canvas", state="visible", timeout=15000)
            _select_pdf_text_and_trigger_toolbar(page_c)
            assert page_c.locator("#btn-share-selection-club").is_hidden()
            ctx_c.close()
        finally:
            browser.close()
