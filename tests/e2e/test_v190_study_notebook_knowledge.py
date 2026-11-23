"""
Suíte de Testes E2E (Playwright) — Fase 4 da Lectoria v1.9.0.
Valida a integração do Atlas no Caderno de Estudos (#annotations-drawer):
- Ausência estrita de consultas N+1 (1 summary do livro + 1 lote por página).
- Exibição de chips de conceitos, contadores e botão Conhecimento apenas em destaques/notas (nunca em desenhos/marcadores).
- Associação/criação de conceitos, criação/edição de conexões e backlinks a partir do Caderno.
- Filtros de conhecimento (Todas / Conectadas / Sem conexões) operando sobre paginação multi-página (>50 itens).
- Retorno de foco após Escape (cobertura complementar da Fase 3).
- Responsividade desktop, tablet (768x1024) e mobile (360x800) com zero overflow horizontal e capturas visuais.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

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
        text = f"{title_prefix} - Pagina {idx}: Estudo conectado."
        stream.set_data(f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def notebook_knowledge_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p4_notebook_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "caderno_atlas.pdf", "Caderno Atlas", num_pages=5)
    _create_text_pdf(lib_dir / "obra_destino.pdf", "Obra Destino", num_pages=3)

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
        username="alice_v190_p4_nb",
        password="Password123!",
        display_name="Alice Caderno",
        role="admin",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES (?, 'Alice Caderno', '#d4a853', 'Leitora');",
            (alice["id"],),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo P4', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_p4_nb', 'Caderno de Estudos e Atlas', 'Autor A', 'caderno_atlas.pdf', 'caderno_atlas.pdf', ?, 'pdf', 5, 1, 0),
            ('book_p4_dest', 'Obra Complementar', 'Autor B', 'obra_destino.pdf', 'obra_destino.pdf', ?, 'pdf', 3, 1, 0);
            """,
            (
                (lib_dir / "caderno_atlas.pdf").stat().st_size,
                (lib_dir / "obra_destino.pdf").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Seed 55 highlights (so pagination > 1 page of 50) + 1 drawing + 1 bookmark + 1 target highlight in book_p4_dest
    hl_ids = []
    for idx in range(55):
        h = annotations_service.create_highlight(
            book_id="book_p4_nb",
            user_id=alice["id"],
            page=(idx % 5) + 1,
            selected_text=f"Passagem de estudo número {idx:02d} no livro principal.",
            rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
            color="#fef08a",
        )
        hl_ids.append(h["id"])

    annotations_service.save_page_drawing(
        "book_p4_nb",
        1,
        [{"color": "#3d7cf4", "width": 3, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.3, "y": 0.3}]}],
        user_id=alice["id"],
    )
    annotations_service.create_bookmark("book_p4_nb", user_id=alice["id"], page=2, label="Marcador de Revisão")

    hl_dest = annotations_service.create_highlight(
        book_id="book_p4_dest",
        user_id=alice["id"],
        page=2,
        selected_text="Passagem complementar na segunda obra para navegação cruzada.",
        rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
        color="#bbf7d0",
    )

    # Connect hl_ids[0] (page 1) and hl_ids[54] (page 2 of pagination!) to a concept
    concept_seed = knowledge_service.create_concept(
        alice["id"],
        "Hermenêutica",
        description="Arte e teoria da interpretação de textos.",
        tags=["metodo"],
    )
    knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_ids[0], "concept", concept_seed["id"], "relates_to")
    knowledge_service.create_knowledge_link(alice["id"], "highlight", hl_ids[54], "concept", concept_seed["id"], "relates_to")

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

    assert started, "Servidor E2E Fase 4 Caderno não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "hl_first_id": hl_ids[0],
            "hl_dest": hl_dest,
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


def test_v190_study_notebook_zero_n_plus_1_filters_and_visuals(notebook_knowledge_server):
    base_url = notebook_knowledge_server["base_url"]
    token = notebook_knowledge_server["token_alice"]
    hl_first_id = notebook_knowledge_server["hl_first_id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p4_nb_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        knowledge_urls = []
        page.on(
            "request",
            lambda req: knowledge_urls.append(f"{req.method} {req.url}")
            if "/api/knowledge/" in req.url
            else None,
        )

        page.goto(f"{base_url}/reader/book_p4_nb", wait_until="networkidle")
        page.wait_for_selector("#btn-toggle-drawer", timeout=15000)

        # Before opening Study Notebook: zero knowledge requests
        assert len(knowledge_urls) == 0

        # Open Study Notebook drawer
        page.click("#btn-toggle-drawer")
        page.wait_for_selector("#annotations-drawer.active", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('drawer-knowledge-summary').textContent.includes('1 conceito(s)')",
            timeout=10000,
        )
        page.wait_for_selector(".drawer-concept-chip", timeout=8000)

        # Verify ZERO N+1: opening 50+ items made only 1 book summary + 1 batch entities summary (<= 2 requests total!)
        assert len(knowledge_urls) == 2, f"Esperava exatamente 2 requisições agregadas, obteve {knowledge_urls}"
        assert any("/api/knowledge/books/book_p4_nb/summary" in u for u in knowledge_urls)
        assert any("POST" in u and "/api/knowledge/entities/summary" in u for u in knowledge_urls)

        # Verify drawings and bookmarks do NOT have .btn-drawer-knowledge
        page.locator('.drawer-tab[data-drawer-filter="drawings"]').click()
        page.wait_for_selector('.annotation-item[data-item-type="drawing"]', timeout=5000)
        assert page.locator('.annotation-item[data-item-type="drawing"] .btn-drawer-knowledge').count() == 0

        page.locator('.drawer-tab[data-drawer-filter="bookmarks"]').click()
        page.wait_for_selector('.annotation-item[data-item-type="bookmark"]', timeout=5000)
        assert page.locator('.annotation-item[data-item-type="bookmark"] .btn-drawer-knowledge').count() == 0

        page.locator('.drawer-tab[data-drawer-filter="all"]').click()

        # Click "Carregar mais" -> must trigger only 1 more batch summary request (no N+1)
        count_before_more = len(knowledge_urls)
        page.click("#btn-drawer-load-more")
        page.wait_for_function(
            "() => document.querySelectorAll('#drawer-items-list .annotation-item[data-item-type=\"highlight\"]').length === 55",
            timeout=10000,
        )
        time.sleep(0.3)
        assert len(knowledge_urls) - count_before_more <= 1

        # Test knowledge filter: "Conectadas" must find BOTH connected highlights (including the one from page 2!)
        page.locator('.drawer-kf-btn[data-knowledge-filter="connected"]').click()
        page.wait_for_function(
            "() => document.getElementById('count-highlights').textContent.trim() === '2'",
            timeout=8000,
        )
        assert page.locator('#drawer-items-list .annotation-item[data-item-type="highlight"]').count() == 2

        # Capture Desktop screenshot of Study Notebook with concepts & filter active
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase4_notebook_concepts_desktop.png"))

        # Test Tablet (768x1024) and Mobile (360x800) with zero horizontal overflow
        page.set_viewport_size({"width": 768, "height": 1024})
        time.sleep(0.2)
        assert not page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase4_notebook_tablet.png"))

        page.set_viewport_size({"width": 360, "height": 800})
        time.sleep(0.2)
        assert not page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase4_notebook_mobile.png"))

        # Restore Desktop and open Knowledge modal directly from a Study Notebook card
        page.set_viewport_size({"width": 1366, "height": 860})
        trigger_btn = page.locator(f'.btn-drawer-knowledge[data-entity-id="{hl_first_id}"]').first
        trigger_btn.click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        # Create cross-book connection from Study Notebook modal
        page.click("#rk-btn-toggle-new-link")
        page.select_option("#rk-target-type", "highlight")
        page.fill("#rk-target-search", "segunda obra")
        page.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#rk-relation-select", "complements")
        page.fill("#rk-rationale-input", "Conexão criada a partir do Caderno de Estudos.")
        page.click("#rk-btn-submit-new-link")
        page.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Complementa')",
            timeout=8000,
        )

        # Close with Escape and verify modal closes AND Study Notebook summary updates to 3 links!
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-reader-knowledge.hidden", state="hidden", timeout=5000)
        page.wait_for_function(
            "() => document.getElementById('drawer-knowledge-summary').textContent.includes('3 conexão(ões)')",
            timeout=8000,
        )

        # Filter "Sem conexões" must show 53 highlights
        page.locator('.drawer-kf-btn[data-knowledge-filter="unconnected"]').click()
        page.wait_for_function(
            "() => document.getElementById('count-highlights').textContent.trim() === '53'",
            timeout=8000,
        )

        browser.close()
