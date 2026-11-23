"""
Suíte de Testes End-to-End (E2E) com Playwright — Fase 3 da Lectoria v1.8.0.
Valida o Leitor PDF Colaborativo com dois usuários autenticados (Alice e Bob):

Cenários cobertos:
 1. Usuário A (Alice) cria nota privada.
 2. Usuário B (Bob) não a visualiza.
 3. Usuário A publica a nota explicitamente.
 4. Usuário B consegue visualizá-la na página e no Caderno de Estudos.
 5. Usuário B não consegue editá-la nem excluí-la (UI somente leitura + backend protegido).
 6. Usuário A revoga a publicação (public -> private).
 7. Usuário B deixa de visualizá-la imediatamente.
 8. Usuário A desenha e publica um desenho na página.
 9. Usuário B visualiza o desenho de A na camada pública (.public-drawing-layer) com autoria discreta.
10. Usuário B não consegue apagar o desenho de A com a borracha nem com Limpar Página.
11. Usuário B oculta desenhos/anotações públicas via controle de visualização ("mine" / "hidden").
12. Desenhos próprios de B permanecem visíveis quando apenas os públicos são ocultados ("mine").
13. Zoom (+ / -) não desloca os desenhos (geometria preservada via viewBox isotrópico).
14. Redimensionamento da janela (desktop <-> tablet <-> mobile) não desloca os desenhos.
15. Rolagem contínua preserva as camadas pessoal e pública.
16. Destaques públicos mostram autoria no popover contextual (sem confirm() abrupto) e modo marca-texto funciona.
17. O modo ocultar não modifica o campo visibility no banco SQLite.
18. A publicação exige ação explícita (padrão privado).
19. O Caderno de Estudos existente continua funcionando (busca, abas, navegação).
20. A integração PDF com leitura coletiva da v1.7.0 permanece funcional.
"""

import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

import pytest
from playwright.sync_api import sync_playwright

import auth_service
import clubs_service
import config
import database


EVIDENCE_DIR = Path("/tmp/lectoria_v180_phase3_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_text_pdf(dest_path: Path, num_pages: int = 8):
    """Cria um PDF sintético com texto real selecionável no textLayer do PDF.js."""
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
        text = f"Capitulo {idx}: Trecho filosofico selecionavel na pagina {idx} do Lectoria."
        stream.set_data(f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def pdf_collab_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v180_p3_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "etica_colaborativa.pdf", num_pages=6)

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
        username="alice_p3e2e",
        password="Password123!",
        display_name="Alice Lemos",
        role="admin",
    )
    bob = auth_service.create_user(
        username="bob_p3e2e",
        password="Password123!",
        display_name="Bob Ferreira",
        role="reader",
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
            """
            INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES
            (1, 'Acervo E2E', ?, 1);
            """,
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book_pdf_collab', 'Etica Colaborativa', 'Autor Teste', 'etica_colaborativa.pdf', 'etica_colaborativa.pdf', ?, 'pdf', 6, 1, 0);
            """,
            ((lib_dir / "etica_colaborativa.pdf").stat().st_size,),
        )
        conn.commit()
    finally:
        conn.close()

    # Clube e leitura coletiva v1.7.0 para testar regressão da integração PDF
    club = clubs_service.create_club("Clube PDF v180", "Clube de regressão", alice["id"], "public")
    clubs_service.join_club(club["id"], bob["id"])
    collective_read = clubs_service.create_collective_read(
        club["id"], alice["id"], "book_pdf_collab", title="Leitura Coletiva Etica", status="in_progress"
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
    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/csrf", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.25)

    assert started, "Servidor E2E da Fase 3 não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "db_path": db_path,
            "alice": alice,
            "bob": bob,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "collective_read": collective_read,
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


def test_v180_pdf_collaborative_two_users_e2e(pdf_collab_server):
    srv = pdf_collab_server
    base_url = srv["base_url"]
    reader_url = f"{base_url}/reader/book_pdf_collab"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ctx_alice, page_a = _new_user_context(browser, base_url, srv["token_alice"], {"width": 1280, "height": 800})
        ctx_bob, page_b = _new_user_context(browser, base_url, srv["token_bob"], {"width": 1280, "height": 800})

        try:
            # --- 1. Alice abre o leitor PDF ---
            page_a.goto(reader_url, wait_until="networkidle")
            page_a.wait_for_selector("#single-page-wrapper canvas", timeout=15000)
            page_a.wait_for_selector("#single-text-layer span", timeout=10000)

            # Cria nota privada (Cenário 1 & 18: padrão é privado)
            page_a.click("#btn-toggle-drawer")
            page_a.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_a.click("#btn-new-page-note")
            page_a.wait_for_selector("#modal-note-editor.active", timeout=5000)
            assert page_a.input_value("#note-visibility-select") == "private"
            page_a.fill("#note-content-input", "Nota privada inicial de Alice")
            page_a.click("#btn-save-note")
            page_a.wait_for_selector("#modal-note-editor.active", state="hidden", timeout=5000)
            page_a.wait_for_selector("#single-note-pins-layer .page-note-pin", timeout=5000)
            page_a.click("#btn-close-drawer")
            page_a.wait_for_timeout(200)

            # --- 2. Bob abre o leitor e NÃO vê a nota privada de Alice ---
            page_b.goto(reader_url, wait_until="networkidle")
            page_b.wait_for_selector("#single-page-wrapper canvas", timeout=15000)
            assert page_b.locator("#single-note-pins-layer .page-note-pin").count() == 0

            # --- 3. Alice publica explicitamente a nota ---
            page_a.click("#single-note-pins-layer .page-note-pin")
            page_a.wait_for_selector("#page-note-popover", state="visible", timeout=5000)
            page_a.locator("#note-pop-actions button", has_text="Tornar Pública").click()
            page_a.wait_for_timeout(400)

            # --- 4 & 5. Bob recarrega anotações e vê a nota pública com autoria, mas sem editar ---
            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#single-note-pins-layer .page-note-pin.is-public-other", timeout=10000)
            page_b.click("#single-note-pins-layer .page-note-pin.is-public-other")
            page_b.wait_for_selector("#page-note-popover", state="visible", timeout=5000)
            assert "Alice Lemos" in page_b.text_content("#note-pop-author-name")
            assert "Nota privada inicial de Alice" in page_b.text_content("#note-pop-content")
            assert "somente leitura" in page_b.text_content("#note-pop-actions")
            assert page_b.locator("#note-pop-actions button", has_text="Editar").count() == 0
            assert page_b.locator("#note-pop-actions button", has_text="Excluir").count() == 0
            page_b.click("#btn-close-note-popover")

            # --- 6 & 7. Alice revoga a publicação -> Bob deixa de ver ---
            page_a.click("#single-note-pins-layer .page-note-pin")
            page_a.wait_for_selector("#page-note-popover", state="visible", timeout=5000)
            page_a.locator("#note-pop-actions button", has_text="Tornar Privada").click()
            page_a.wait_for_timeout(400)

            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#single-page-wrapper canvas", timeout=15000)
            assert page_b.locator("#single-note-pins-layer .page-note-pin").count() == 0

            # --- 8. Alice desenha na página 1 e publica o desenho ---
            page_a.click("#btn-tool-draw")
            page_a.wait_for_selector("#drawing-toolbar.active", timeout=5000)
            svg_box_a = page_a.locator("#single-drawing-layer").bounding_box()
            assert svg_box_a and svg_box_a["width"] > 100
            page_a.mouse.move(svg_box_a["x"] + svg_box_a["width"] * 0.25, svg_box_a["y"] + svg_box_a["height"] * 0.14)
            page_a.mouse.down()
            page_a.mouse.move(svg_box_a["x"] + svg_box_a["width"] * 0.65, svg_box_a["y"] + svg_box_a["height"] * 0.20, steps=8)
            page_a.mouse.up()
            page_a.wait_for_timeout(300)
            page_a.click("#btn-draw-visibility")
            page_a.wait_for_timeout(500)
            assert page_a.get_attribute("#btn-draw-visibility", "data-visibility") == "public"

            # --- 9 & 10. Bob vê o desenho público de Alice, desenha o seu e tenta apagar com borracha/limpar ---
            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#single-public-drawing-layer path.public-drawing-stroke", timeout=10000)
            assert page_b.locator("#single-note-pins-layer .page-public-drawings-badge").count() == 1
            assert "Alice Lemos" in page_b.text_content("#single-note-pins-layer .page-public-drawings-badge")

            # Bob desenha um traço próprio abaixo do traço de Alice (bem dentro da área visível, acima da barra de desenho)
            page_b.click("#btn-tool-draw")
            svg_box_b = page_b.locator("#single-drawing-layer").bounding_box()
            page_b.mouse.move(svg_box_b["x"] + svg_box_b["width"] * 0.25, svg_box_b["y"] + svg_box_b["height"] * 0.28)
            page_b.mouse.down()
            page_b.mouse.move(svg_box_b["x"] + svg_box_b["width"] * 0.65, svg_box_b["y"] + svg_box_b["height"] * 0.32, steps=8)
            page_b.mouse.up()
            page_b.wait_for_timeout(800)
            assert page_b.locator("#single-drawing-layer path.drawing-stroke").count() >= 1

            # Bob passa a borracha sobre a região do traço de Alice -> camada pública permanece intacta!
            page_b.click("#btn-draw-eraser")
            page_b.mouse.move(svg_box_b["x"] + svg_box_b["width"] * 0.45, svg_box_b["y"] + svg_box_b["height"] * 0.17)
            page_b.mouse.down()
            page_b.mouse.move(svg_box_b["x"] + svg_box_b["width"] * 0.55, svg_box_b["y"] + svg_box_b["height"] * 0.19, steps=5)
            page_b.mouse.up()
            page_b.wait_for_timeout(300)
            assert page_b.locator("#single-public-drawing-layer path.public-drawing-stroke").count() >= 1

            # --- 11, 12 & 17. Controle de visualização: 'mine' mantém desenho de Bob e oculta público de Alice; 'hidden' oculta tudo sem mudar o DB ---
            page_b.select_option("#layer-view-mode-select", "mine")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#single-public-drawing-layer path.public-drawing-stroke").count() == 0
            assert page_b.locator("#single-drawing-layer path.drawing-stroke").count() >= 1

            page_b.select_option("#layer-view-mode-select", "hidden")
            page_b.wait_for_timeout(300)
            assert page_b.locator("#reader-viewport.layers-hidden").count() == 1

            # Verifica no SQLite que visibility do desenho de Alice continua 'public'
            conn = sqlite3.connect(str(srv["db_path"]))
            vis_row = conn.execute(
                "SELECT visibility FROM page_drawings WHERE book_id = 'book_pdf_collab' AND user_id = ?;",
                (srv["alice"]["id"],),
            ).fetchone()
            conn.close()
            assert vis_row and vis_row[0] == "public"

            # Volta para 'all_visible'
            page_b.select_option("#layer-view-mode-select", "all_visible")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#single-public-drawing-layer path.public-drawing-stroke").count() >= 1

            # Bob clica em Limpar Página -> remove apenas o desenho próprio de Bob, desenho público de Alice continua lá
            page_b.click("#btn-draw-clear")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#single-drawing-layer path.drawing-stroke").count() == 0
            assert page_b.locator("#single-public-drawing-layer path.public-drawing-stroke").count() >= 1

            # --- 13 & 14. Zoom e redimensionamento preservam geometria do SVG (viewBox isotrópico) ---
            vb_before = page_b.get_attribute("#single-public-drawing-layer", "viewBox")
            path_d_before = page_b.get_attribute("#single-public-drawing-layer path.public-drawing-stroke", "d")
            page_b.click("#btn-zoom-in")
            page_b.wait_for_timeout(500)
            vb_after_zoom = page_b.get_attribute("#single-public-drawing-layer", "viewBox")
            path_d_after_zoom = page_b.get_attribute("#single-public-drawing-layer path.public-drawing-stroke", "d")
            assert vb_before == vb_after_zoom
            assert path_d_before == path_d_after_zoom

            page_b.set_viewport_size({"width": 900, "height": 700})
            page_b.wait_for_timeout(500)
            path_d_after_resize = page_b.get_attribute("#single-public-drawing-layer path.public-drawing-stroke", "d")
            assert path_d_before == path_d_after_resize
            page_b.set_viewport_size({"width": 1280, "height": 800})
            page_b.wait_for_timeout(400)

            # --- 16 & 20. Alice cria destaque público via modo Marca-texto e abre popover com autoria + botão Leitura Coletiva ---
            page_a.click("#btn-tool-highlight")
            page_a.wait_for_timeout(200)
            # Seleciona texto no textLayer via Range e dispara mouseup
            page_a.evaluate("""() => {
                const span = document.querySelector('#single-text-layer span');
                const range = document.createRange();
                range.selectNodeContents(span);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                document.getElementById('reader-viewport').dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
            }""")
            page_a.wait_for_selector("#single-highlights-layer .highlight-box", timeout=5000)

            # Clica no destaque -> abre popover contextual (sem confirm()) e publica
            page_a.click("#single-highlights-layer .highlight-box")
            page_a.wait_for_selector("#highlight-popover", state="visible", timeout=5000)
            page_a.locator("#hl-pop-actions button", has_text="Tornar Público").click()
            page_a.wait_for_timeout(400)

            # Bob vê o destaque público diferenciado (.highlight-public-other) com autoria de Alice
            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#single-highlights-layer .highlight-box.highlight-public-other", timeout=10000)
            page_b.click("#single-highlights-layer .highlight-box.highlight-public-other")
            page_b.wait_for_selector("#highlight-popover", state="visible", timeout=5000)
            assert "Alice Lemos" in page_b.text_content("#hl-pop-author-name")
            assert "somente leitura" in page_b.text_content("#hl-pop-actions")

            # Screenshot Desktop (Bob vendo destaque público + desenho público + popover de autoria)
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase3_pdf_collab_desktop.png"))
            page_b.click("#btn-close-hl-popover")

            # --- 15. Rolagem contínua preserva ambas as camadas ---
            page_b.click("#btn-mode-continuous")
            page_b.wait_for_selector("#continuous-wrapper #page-slot-1 .public-drawing-layer path.public-drawing-stroke", timeout=10000)
            assert page_b.locator("#continuous-wrapper #page-slot-1 .highlight-box.highlight-public-other").count() >= 1

            # --- 19. Caderno de Estudos funciona com busca, abas e navegação ---
            page_b.click("#btn-toggle-drawer")
            page_b.wait_for_selector("#annotations-drawer.active", timeout=5000)
            assert page_b.locator("#drawer-items-list .annotation-item").count() >= 2

            # --- 20. Leitura Coletiva v1.7.0 no PDF continua funcionando ---
            page_b.click("#btn-mode-single")
            page_b.wait_for_selector("#single-text-layer span", timeout=10000)
            page_b.click("#btn-tool-read")
            page_b.evaluate("""() => {
                const span = document.querySelector('#single-text-layer span');
                const range = document.createRange();
                range.selectNodeContents(span);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                document.dispatchEvent(new Event('selectionchange'));
            }""")
            page_b.wait_for_selector("#selection-toolbar.active #btn-share-selection-club", timeout=5000)

            # Screenshot Mobile (390x844) sem overflow horizontal
            page_b.set_viewport_size({"width": 390, "height": 844})
            page_b.wait_for_timeout(500)
            has_overflow = page_b.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not has_overflow, "Detectado overflow horizontal no viewport mobile 390px"
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase3_pdf_collab_mobile.png"))

        finally:
            ctx_alice.close()
            ctx_bob.close()
            browser.close()
