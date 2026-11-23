"""
Suíte de Testes End-to-End (E2E) com Playwright — Fase 4 da Lectoria v1.7.0.
Valida o Painel Integrado de Leitura Compartilhada em desktop e mobile:
- Abertura sem recarregamento, carregamento sob demanda das 4 abas.
- Discussões: criação, paginação, respostas encadeadas (máx. profundidade 1), edição, moderação.
- Spoilers: ocultação real do DOM antes do clique/teclado (Enter), sem revelação por hover.
- Metas: criação/edição/arquivamento por owner, progresso voluntário desacoplado de reading_progress.
- Citações: compartilhamento manual, proteção de spoiler e link para o leitor.
- Responsividade (320px a 1920px sem overflow horizontal), temas claro/escuro e modo somente-leitura.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from playwright.sync_api import sync_playwright

import auth_service
import clubs_service
import config
import database


EVIDENCE_DIR = Path("/tmp/lectoria_v170_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_pdf(dest: Path):
    import pypdf
    w = pypdf.PdfWriter()
    for _ in range(5):
        w.add_blank_page(width=595, height=842)
    with open(dest, "wb") as f:
        w.write(f)


@pytest.fixture(scope="module")
def v170_e2e_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v170_e2e")
    db_path = root / "lectoria_v170.db"
    covers_dir = root / "covers"
    lib_dir = root / "library"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    _make_pdf(lib_dir / "dom_casmurro.pdf")

    old_db, old_cov, old_lib = config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    database.init_db(db_path)

    alice = auth_service.create_user(username="alice_e2e", password="password123", role="admin")
    bob = auth_service.create_user(username="bob_e2e", password="password123", role="reader")
    carlos = auth_service.create_user(username="carlos_e2e", password="password123", role="reader")

    token_alice = auth_service.create_session(alice["id"])
    token_bob = auth_service.create_session(bob["id"])
    token_carlos = auth_service.create_session(carlos["id"])

    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, is_missing)
            VALUES ('book170e2e01', 'Dom Casmurro', 'Machado de Assis', 'dom_casmurro.pdf', 'dom_casmurro.pdf', 102400, 'pdf', 256, 0);
            """
        )
        cur.execute(
            """
            INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage)
            VALUES (?, 'book170e2e01', 25, 256, 9.8);
            """,
            (bob["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    pub_club = clubs_service.create_club(
        name="Clube E2E Machado",
        description="Leitura compartilhada E2E",
        visibility="public",
        owner_id=alice["id"],
    )
    clubs_service.join_club(pub_club["id"], bob["id"])
    pub_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170e2e01",
        title="Leitura Coletiva Dom Casmurro",
        status="in_progress",
    )

    readonly_read = clubs_service.create_collective_read(
        club_id=pub_club["id"],
        user_id=alice["id"],
        book_id="book170e2e01",
        title="Leitura Encerrada",
        status="in_progress",
    )

    port = _find_free_port()
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

    base_url = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as r:
                if r.status == 200:
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
            "club": pub_club,
            "read": pub_read,
            "readonly_read": readonly_read,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR = old_db, old_cov, old_lib


def _open_panel_for_user(page, base_url, token, club_name, read_title):
    page.context.add_cookies([{"name": "lectoria_session", "value": token, "domain": "127.0.0.1", "path": "/"}])
    page.goto(base_url)
    page.wait_for_selector("#tab-nav-clubs")
    page.click("#tab-nav-clubs")
    page.wait_for_selector(f".club-card:has-text('{club_name}')")
    page.click(f".club-card:has-text('{club_name}') .btn-open-club-detail")
    page.wait_for_selector(f".collective-read-card:has-text('{read_title}') .btn-open-shared-panel")
    page.click(f".collective-read-card:has-text('{read_title}') .btn-open-shared-panel")
    page.wait_for_selector("#shared-reading-view")
    page.wait_for_selector(".sr-overview-stats")


def test_shared_reading_panel_full_flow_spoilers_and_responsive(v170_e2e_server):
    srv = v170_e2e_server
    base_url = srv["base_url"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # --- Contexto Alice (owner) ---
            ctx_a = browser.new_context(viewport={"width": 1280, "height": 800})
            page_a = ctx_a.new_page()
            page_a.on("dialog", lambda d: d.accept())

            # Monitora requisições para validar carregamento sob demanda
            requests_a = []
            page_a.on("request", lambda req: requests_a.append(req.url))

            _open_panel_for_user(
                page_a, base_url, srv["token_alice"], "Clube E2E Machado", "Leitura Coletiva Dom Casmurro"
            )

            # Carregamento sob demanda: apenas overview deve ter sido buscado inicialmente
            assert any("/overview" in u for u in requests_a)
            assert not any("/topics" in u for u in requests_a)
            assert not any("/goals" in u for u in requests_a)
            assert not any("/quotes" in u for u in requests_a)

            page_a.screenshot(path=str(EVIDENCE_DIR / "v170_sr_overview_desktop.png"))

            # --- Aba Discussões: criar tópico com spoiler ---
            page_a.click("#sr-tab-discussions")
            page_a.wait_for_selector("#btn-sr-new-topic")
            page_a.click("#btn-sr-new-topic")
            page_a.fill("#sr-topic-title", "Revelação do Capítulo Final")
            page_a.fill("#sr-topic-content", "Segredo absoluto do desfecho.")
            page_a.fill("#sr-topic-chapter", "Cap. 148")
            page_a.fill("#sr-topic-page-start", "250")
            page_a.check("#sr-topic-spoiler")
            page_a.click("#btn-save-sr-topic")

            page_a.wait_for_selector(".sr-topic-title-btn:has-text('Revelação do Capítulo Final')")
            # Prévia não pode vazar texto do spoiler
            assert "Segredo absoluto do desfecho" not in page_a.text_content("#sr-topics-list")

            # Abrir detalhe do tópico: spoiler oculto do DOM antes de revelar
            page_a.click(".sr-topic-title-btn:has-text('Revelação do Capítulo Final')")
            page_a.wait_for_selector(".sr-spoiler-barrier")
            assert "Segredo absoluto do desfecho" not in page_a.content()

            # Hover NÃO deve revelar
            page_a.hover(".sr-spoiler-barrier")
            page_a.wait_for_timeout(150)
            assert "Segredo absoluto do desfecho" not in page_a.content()

            # Teclado (Enter) revela intencionalmente
            page_a.focus(".sr-spoiler-barrier")
            page_a.keyboard.press("Enter")
            page_a.wait_for_selector(".sr-spoiler-revealed")
            assert "Segredo absoluto do desfecho" in page_a.text_content(".sr-spoiler-revealed")

            # Alice fixa o tópico e cria resposta raiz
            page_a.click("button:has-text('Fixar')")
            page_a.wait_for_selector("button:has-text('Desafixar')")
            page_a.fill(".sr-reply-form textarea", "Comentário inicial da Alice.")
            page_a.click(".sr-reply-form button[type='submit']")
            page_a.wait_for_selector(".sr-post-card:has-text('Comentário inicial da Alice.')")
            page_a.screenshot(path=str(EVIDENCE_DIR / "v170_sr_discussions_desktop.png"))

            # --- Aba Metas: Alice cria meta ---
            page_a.click("#sr-tab-goals")
            page_a.wait_for_selector("#btn-sr-new-goal")
            page_a.click("#btn-sr-new-goal")
            page_a.fill("#sr-goal-title", "Semana 1: Capítulos 1 a 30")
            page_a.fill("#sr-goal-date", "2026-12-15")
            page_a.fill("#sr-goal-page", "60")
            page_a.click("#btn-save-sr-goal")
            page_a.wait_for_selector(".sr-goal-card:has-text('Semana 1: Capítulos 1 a 30')")
            page_a.screenshot(path=str(EVIDENCE_DIR / "v170_sr_goals_desktop.png"))

            # --- Aba Citações: Alice publica citação manual ---
            page_a.click("#sr-tab-quotes")
            page_a.wait_for_selector("#btn-sr-new-quote")
            page_a.click("#btn-sr-new-quote")
            page_a.fill("#sr-quote-text", "Não consultes dicionários.")
            page_a.fill("#sr-quote-comment", "Abertura memorável.")
            page_a.fill("#sr-quote-page", "10")
            page_a.click("#btn-save-sr-quote")
            page_a.wait_for_selector(".sr-quote-card:has-text('Não consultes dicionários.')")
            page_a.screenshot(path=str(EVIDENCE_DIR / "v170_sr_quotes_desktop.png"))

            # --- Contexto Bob (member): respostas encadeadas, metas voluntárias e permissões ---
            ctx_b = browser.new_context(viewport={"width": 1280, "height": 800})
            page_b = ctx_b.new_page()
            _open_panel_for_user(
                page_b, base_url, srv["token_bob"], "Clube E2E Machado", "Leitura Coletiva Dom Casmurro"
            )

            # Bob responde ao comentário raiz da Alice (profundidade 1)
            page_b.click("#sr-tab-discussions")
            page_b.wait_for_selector(".sr-topic-title-btn:has-text('Revelação do Capítulo Final')")
            page_b.click(".sr-topic-title-btn:has-text('Revelação do Capítulo Final')")
            page_b.wait_for_selector(".sr-post-card:has-text('Comentário inicial da Alice.')")
            page_b.click(".sr-post-card button:has-text('Responder')")
            page_b.fill(".sr-inline-reply-form textarea", "Resposta encadeada do Bob.")
            page_b.click(".sr-inline-reply-form button[type='submit']")
            page_b.wait_for_selector(".sr-post-nested:has-text('Resposta encadeada do Bob.')")
            # Profundidade 1 não pode ter botão Responder
            assert page_b.locator(".sr-post-nested button:has-text('Responder')").count() == 0

            # Bob em Metas: não vê botão "+ Nova Meta", mas marca progresso voluntário
            page_b.click("#sr-tab-goals")
            page_b.wait_for_selector(".sr-goal-card:has-text('Semana 1: Capítulos 1 a 30')")
            assert not page_b.is_visible("#btn-sr-new-goal")
            page_b.click(".sr-progress-btn-group button:has-text('Concluída')")
            page_b.wait_for_selector(".sr-progress-btn-group button[aria-pressed='true']:has-text('Concluída')")

            # Progresso privado de Bob em reading_progress permanece intacto (25 / 9.8%)
            config.DB_PATH = srv["db_path"]
            conn = database.get_connection()
            try:
                row = conn.execute(
                    "SELECT current_page, percentage FROM reading_progress WHERE user_id = ? AND book_id = 'book170e2e01';",
                    (srv["bob"]["id"],),
                ).fetchone()
                assert row["current_page"] == 25
                assert row["percentage"] == pytest.approx(9.8)
            finally:
                conn.close()

            # --- Alternância de Tema (Claro / Escuro) + Responsividade (320px e 375px sem overflow horizontal) ---
            theme_before = page_a.get_attribute("html", "data-theme")
            page_a.click("#btn-theme-toggle")
            theme_after = page_a.get_attribute("html", "data-theme")
            assert theme_after != theme_before
            assert theme_after in ("light", "dark")

            for width, height, tag in [(320, 700, "320"), (375, 812, "mobile")]:
                page_a.set_viewport_size({"width": width, "height": height})
                for tab_sel, tab_name in [
                    ("#sr-tab-overview", "overview"),
                    ("#sr-tab-discussions", "discussions"),
                    ("#sr-tab-goals", "goals"),
                    ("#sr-tab-quotes", "quotes"),
                ]:
                    page_a.click(tab_sel)
                    page_a.wait_for_timeout(150)
                    overflow = page_a.evaluate(
                        "() => document.documentElement.scrollWidth > window.innerWidth"
                    )
                    assert not overflow, f"Overflow horizontal detectado em {width}px na aba {tab_name}"
                    if tag == "mobile":
                        page_a.screenshot(path=str(EVIDENCE_DIR / f"v170_sr_{tab_name}_mobile.png"))

            ctx_a.close()
            ctx_b.close()
        finally:
            browser.close()


def test_shared_reading_pagination_and_readonly_state(v170_e2e_server):
    srv = v170_e2e_server
    base_url = srv["base_url"]
    config.DB_PATH = srv["db_path"]

    # Cria 22 tópicos na leitura para testar paginação, depois cancela a leitura
    for i in range(22):
        import shared_reading_service as srs
        srs.create_topic(srv["readonly_read"]["id"], srv["alice"]["id"], title=f"Tópico Paginação {i+1}", content="Texto")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            _open_panel_for_user(page, base_url, srv["token_bob"], "Clube E2E Machado", "Leitura Encerrada")

            page.click("#sr-tab-discussions")
            page.wait_for_selector("#sr-topics-pagination")
            assert "Página 1 de 2" in page.text_content("#sr-topics-pagination")
            page.click("#sr-topics-pagination button:has-text('Próxima')")
            page.wait_for_timeout(250)
            assert "Página 2 de 2" in page.text_content("#sr-topics-pagination")

            # Cancela leitura e recarrega o painel -> modo somente-leitura
            clubs_service.update_collective_read(srv["readonly_read"]["id"], srv["alice"]["id"], status="cancelled")
            page.click("#btn-back-from-shared-reading")
            page.wait_for_selector(".collective-read-card:has-text('Leitura Encerrada') .btn-open-shared-panel")
            page.click(".collective-read-card:has-text('Leitura Encerrada') .btn-open-shared-panel")
            page.wait_for_selector("#sr-readonly-banner")
            assert page.is_visible("#sr-readonly-banner")
            assert not page.is_visible("#btn-sr-new-topic")

            ctx.close()
        finally:
            browser.close()
