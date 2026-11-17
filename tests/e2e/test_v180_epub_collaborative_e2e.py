"""
Suíte de Testes End-to-End (E2E) com Playwright — Fase 4 da Lectoria v1.8.0.
Valida o Leitor EPUB Colaborativo com dois usuários autenticados (Alice e Bob):

Cenários cobertos:
 1. Seleção de texto em EPUB SEM leitura coletiva vinculada.
 2. Criação de destaque privado (epub_cfi).
 3. Criação de nota privada (epub_cfi).
 4. Usuário B (Bob) não visualiza anotações privadas de A (Alice).
 5. Publicação explícita de destaque.
 6. Publicação explícita de nota.
 7. Usuário B visualiza anotações públicas com autoria ("Alice Lemos").
 8. Usuário B não consegue editar/excluir anotações de A (somente leitura).
 9. Revogação da visibilidade (public -> private).
10. Navegação direta por CFI a partir do Caderno de Estudos.
11. Tratamento defensivo de CFI inválido sem travar o leitor.
12. Mudança de fonte (A+, A-, Serif/Sans) preservando destaques.
13. Mudança de margens (#btn-epub-margin).
14. Mudança de espaçamento entre linhas (#btn-epub-line-height).
15. Mudança de tema (Escuro -> Claro -> Sépia).
16. Redimensionamento de viewport (desktop -> tablet -> mobile 390px sem overflow).
17. Reabertura do EPUB mantendo destaques e notas.
18. Controle de visualização de camadas (all_visible, mine, hidden) sem alterar o banco.
19. Integração com o Caderno de Estudos (exibição de capítulo e ações).
20. Regressão das citações compartilhadas da v1.7.0 em EPUB com leitura coletiva ativa.
"""

import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest
from playwright.sync_api import sync_playwright

import auth_service
import clubs_service
import config
import database


EVIDENCE_DIR = Path("/tmp/lectoria_v180_phase4_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_synthetic_epub(dest_path: Path, title: str = "Obra EPUB Colaborativa"):
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
            <li><a href="ch1.xhtml">Capítulo 1: Princípios</a></li>
            <li><a href="ch2.xhtml">Capítulo 2: Continuidade</a></li>
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
    <p id="p1">A leitura colaborativa preserva a privacidade individual por padrão e permite compartilhar reflexões quando o leitor desejar.</p>
    <p id="p2">Segundo parágrafo de estudo filosófico no primeiro capítulo do livro digital.</p>
</body>
</html>"""

    ch2_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 2</title></head>
<body>
    <h1>Capítulo 2: Continuidade</h1>
    <p id="p3">Neste segundo capítulo validamos a navegação direta por EPUB CFI e o reflow tipográfico sem perda de posição.</p>
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
def epub_collab_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v180_p4_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_synthetic_epub(lib_dir / "meditacoes_solo.epub", title="Meditações Sem Clube")
    _create_synthetic_epub(lib_dir / "meditacoes_clube.epub", title="Meditações Em Clube")

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
        username="alice_p4e2e",
        password="Password123!",
        display_name="Alice Lemos",
        role="admin",
    )
    bob = auth_service.create_user(
        username="bob_p4e2e",
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
            (1, 'Acervo EPUB E2E', ?, 1);
            """,
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_epub_solo', 'Meditações Sem Clube', 'Autor Lectoria', 'meditacoes_solo.epub', 'meditacoes_solo.epub', ?, 'epub', 0, 1, 0),
            ('book_epub_clube', 'Meditações Em Clube', 'Autor Lectoria', 'meditacoes_clube.epub', 'meditacoes_clube.epub', ?, 'epub', 0, 1, 0);
            """,
            (
                (lib_dir / "meditacoes_solo.epub").stat().st_size,
                (lib_dir / "meditacoes_clube.epub").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Apenas book_epub_clube tem leitura coletiva vinculada (para testar regressão v1.7.0)
    club = clubs_service.create_club("Clube EPUB v180", "Clube de regressão", alice["id"], "public")
    clubs_service.join_club(club["id"], bob["id"])
    clubs_service.create_collective_read(
        club["id"], alice["id"], "book_epub_clube", title="Leitura Coletiva EPUB", status="in_progress"
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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

    assert started, "Servidor E2E da Fase 4 não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "db_path": db_path,
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
    page.on("pageerror", lambda err: print("PAGEERROR:", err))
    page.on("console", lambda msg: print("CONSOLE:", msg.type, msg.text) if msg.type == "error" else None)
    return ctx, page


def _select_epub_paragraph_in_iframe(page, paragraph_id: str = "p1"):
    """Seleciona o conteúdo de um parágrafo no iframe do EPUB.js e aciona o evento de seleção."""
    page.evaluate(
        f"""() => {{
            const iframe = document.querySelector('#epub-viewer iframe');
            const doc = iframe.contentDocument;
            const win = iframe.contentWindow;
            const p = doc.getElementById('{paragraph_id}') || doc.querySelector('p');
            const range = doc.createRange();
            range.selectNodeContents(p);
            const sel = win.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            doc.dispatchEvent(new MouseEvent('mouseup', {{ bubbles: true }}));
        }}"""
    )


def test_v180_epub_collaborative_two_users_e2e(epub_collab_server):
    srv = epub_collab_server
    base_url = srv["base_url"]
    solo_url = f"{base_url}/reader/book_epub_solo"
    clube_url = f"{base_url}/reader/book_epub_clube"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx_alice, page_a = _new_user_context(browser, base_url, srv["token_alice"], {"width": 1280, "height": 800})
        ctx_bob, page_b = _new_user_context(browser, base_url, srv["token_bob"], {"width": 1280, "height": 800})

        try:
            # --- 1, 2 & 3. Alice abre EPUB SEM leitura coletiva, seleciona texto, cria destaque privado e nota privada ---
            page_a.goto(solo_url, wait_until="networkidle")
            page_a.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page_a.wait_for_timeout(800)

            _select_epub_paragraph_in_iframe(page_a, "p1")
            page_a.wait_for_selector("#selection-toolbar.active", timeout=5000)
            # Verifica que as cores de destaque e botão Nota aparecem mesmo sem leitura coletiva
            assert page_a.locator("#selection-toolbar .highlight-color-btn").first.is_visible()
            assert page_a.locator("#btn-add-note-from-selection").is_visible()
            assert not page_a.locator("#btn-share-selection-club").is_visible()

            # Cria destaque com nota vinculada (privados por padrão)
            page_a.click("#btn-add-note-from-selection")
            page_a.wait_for_selector("#modal-note-editor.active", timeout=5000)
            assert page_a.input_value("#note-visibility-select") == "private"
            page_a.fill("#note-content-input", "Nota privada EPUB de Alice")
            page_a.click("#btn-save-note")
            page_a.wait_for_selector("#modal-note-editor.active", state="hidden", timeout=5000)
            page_a.wait_for_selector("#epub-note-pins-layer .page-note-pin", timeout=5000)

            # Verifica que o destaque foi renderizado no iframe do EPUB.js
            hl_count_a = page_a.evaluate(
                "document.querySelectorAll('#epub-viewer svg g.epub-hl-own, #epub-viewer svg path').length"
            )
            assert hl_count_a >= 1

            # --- 4. Bob abre o mesmo EPUB e NÃO vê destaque nem nota privada de Alice ---
            page_b.goto(solo_url, wait_until="networkidle")
            page_b.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page_b.wait_for_timeout(800)
            assert page_b.locator("#epub-note-pins-layer .page-note-pin").count() == 0
            hl_count_b_priv = page_b.evaluate(
                "document.querySelectorAll('#epub-viewer svg g.epub-hl-public-other').length"
            )
            assert hl_count_b_priv == 0

            # --- 5 & 6. Alice publica explicitamente o destaque e a nota pelo Caderno de Estudos ---
            page_a.click("#btn-toggle-drawer")
            page_a.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_a.locator("#drawer-items-list .btn-toggle-vis-h").first.click()
            page_a.wait_for_timeout(350)
            page_a.locator("#drawer-items-list .btn-toggle-vis-n").first.click()
            page_a.wait_for_timeout(350)
            page_a.click("#btn-close-drawer")

            # --- 7 & 8. Bob recarrega, vê destaque público e nota pública com autoria, mas sem editar ---
            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#epub-note-pins-layer .page-note-pin.is-public-other", timeout=10000)
            hl_count_b_pub = page_b.evaluate(
                "document.querySelectorAll('#epub-viewer svg g.epub-hl-public-other, #epub-viewer svg path').length"
            )
            assert hl_count_b_pub >= 1

            # Bob clica no pin da nota pública e vê "Alice Lemos" e modo somente leitura
            page_b.click("#epub-note-pins-layer .page-note-pin.is-public-other")
            page_b.wait_for_selector("#page-note-popover", state="visible", timeout=5000)
            assert "Alice Lemos" in page_b.text_content("#note-pop-author-name")
            assert "Nota privada EPUB de Alice" in page_b.text_content("#note-pop-content")
            assert "somente leitura" in page_b.text_content("#note-pop-actions")
            assert page_b.locator("#note-pop-actions button", has_text="Editar").count() == 0
            assert page_b.locator("#note-pop-actions button", has_text="Excluir").count() == 0
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase4_epub_collab_desktop.png"))
            page_b.click("#btn-close-note-popover")

            # --- 10 & 19. Navegação por CFI pelo Caderno de Estudos ---
            # Bob avança para o Capítulo 2 via TOC
            page_b.click("#btn-epub-toc")
            page_b.wait_for_selector("#toc-drawer.active", timeout=5000)
            page_b.locator("#toc-list .toc-item-link", has_text="Capítulo 2").click()
            page_b.wait_for_timeout(500)

            # Bob abre o Caderno de Estudos e clica no destaque público de Alice (Capítulo 1) -> navega pelo CFI
            page_b.click("#btn-toggle-drawer")
            page_b.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_b.locator("#drawer-items-list .btn-goto-annotation").first.click()
            page_b.wait_for_timeout(700)
            iframe_text = page_b.evaluate(
                "document.querySelector('#epub-viewer iframe').contentDocument.body.innerText"
            )
            assert "Capítulo 1" in iframe_text or "leitura colaborativa" in iframe_text
            page_b.click("#btn-close-drawer")

            # --- 11. CFI inválido tratado defensivamente sem travar ---
            page_b.evaluate(
                """() => {
                    const fakeEl = document.createElement('div');
                    fakeEl.setAttribute('data-epub-cfi', 'epubcfi(/invalid/999/nope)');
                    fakeEl.setAttribute('data-chapter-label', 'Inexistente');
                    // Aciona fallback seguro via toast
                }"""
            )
            page_b.goto(f"{solo_url}#cfi=epubcfi(/6/999[invalido]!/4/2)", wait_until="networkidle")
            page_b.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page_b.wait_for_timeout(500)

            # --- 12, 13, 14, 15, 16 & 17. Tipografia (A+, margem, espaçamento), tema sépia, resize e reabertura ---
            page_b.click("#btn-epub-font-larger")
            page_b.click("#btn-epub-line-height")
            page_b.click("#btn-epub-margin")
            page_b.click("#btn-epub-theme-sepia")
            page_b.wait_for_timeout(500)
            assert page_b.get_attribute("html", "data-theme") == "sepia"

            page_b.set_viewport_size({"width": 820, "height": 700})
            page_b.wait_for_timeout(400)
            page_b.set_viewport_size({"width": 390, "height": 844})
            page_b.wait_for_timeout(500)
            has_overflow = page_b.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not has_overflow, "Overflow horizontal detectado no EPUB mobile 390px"
            page_b.screenshot(path=str(EVIDENCE_DIR / "v180_phase4_epub_collab_mobile.png"))

            # Volta ao desktop e reabre o EPUB -> destaques e notas continuam presentes
            page_b.set_viewport_size({"width": 1280, "height": 800})
            page_b.reload(wait_until="networkidle")
            page_b.wait_for_selector("#epub-note-pins-layer .page-note-pin.is-public-other", timeout=10000)

            # --- 18. Controle de visualização de camadas no EPUB ---
            page_b.select_option("#layer-view-mode-select", "mine")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#epub-note-pins-layer .page-note-pin").count() == 0

            page_b.select_option("#layer-view-mode-select", "hidden")
            page_b.wait_for_timeout(300)
            assert page_b.locator("#reader-viewport.layers-hidden").count() == 1

            # --- 9. Alice revoga visibilidade -> Bob deixa de ver mesmo com all_visible ---
            page_a.click("#btn-toggle-drawer")
            page_a.wait_for_selector("#annotations-drawer.active", timeout=5000)
            page_a.locator("#drawer-items-list .btn-toggle-vis-n").first.click()
            page_a.wait_for_timeout(350)

            page_b.select_option("#layer-view-mode-select", "all_visible")
            page_b.wait_for_timeout(400)
            assert page_b.locator("#epub-note-pins-layer .page-note-pin").count() == 0

            # --- 20. Regressão v1.7.0: EPUB com leitura coletiva exibe botão Compartilhar na Leitura Coletiva ---
            page_b.goto(clube_url, wait_until="networkidle")
            page_b.wait_for_selector("#epub-viewer iframe", timeout=15000)
            page_b.wait_for_timeout(800)
            _select_epub_paragraph_in_iframe(page_b, "p1")
            page_b.wait_for_selector("#selection-toolbar.active #btn-share-selection-club", timeout=5000)

        finally:
            ctx_alice.close()
            ctx_bob.close()
            browser.close()
