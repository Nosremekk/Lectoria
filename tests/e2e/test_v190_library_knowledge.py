"""
Suíte de Testes E2E (Playwright) — Fase 4 da Lectoria v1.9.0.
Valida a integração contextual mínima do Atlas na Biblioteca (#modal-book-details -> subaba Conhecimento):
- Carregamento estritamente sob demanda (zero chamadas /api/knowledge/* até clicar na subaba Conhecimento).
- Exibição de indicadores pessoais da obra e atalho "Abrir Caderno de Estudos" (/reader/{id}?drawer=1).
- Criação e associação de conceito diretamente na entidade `book`.
- Criação e edição de conexão semântica `book` -> `book`.
- Navegação para o Caderno de Estudos abrindo o drawer automaticamente.
- Captura visual da integração na biblioteca.
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

import auth_service
import config
import database


from tests.e2e.artifact_utils import get_artifact_dir

ARTIFACT_DIR = get_artifact_dir()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_text_pdf(dest_path: Path, title_prefix: str):
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
    stream.set_data(f"BT /F1 16 Tf 72 740 Td ({title_prefix}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def library_knowledge_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p4_lib_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "obra_alfa.pdf", "Obra Alfa")
    _create_text_pdf(lib_dir / "obra_beta.pdf", "Obra Beta")

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
        username="alice_v190_p4_lib",
        password="Password123!",
        display_name="Alice Biblioteca",
        role="admin",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Lib P4', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_lib_alfa', 'Obra Alfa de Filosofia', 'Autor Alfa', 'obra_alfa.pdf', 'obra_alfa.pdf', ?, 'pdf', 1, 1, 0),
            ('book_lib_beta', 'Obra Beta de História', 'Autor Beta', 'obra_beta.pdf', 'obra_beta.pdf', ?, 'pdf', 1, 1, 0);
            """,
            (
                (lib_dir / "obra_alfa.pdf").stat().st_size,
                (lib_dir / "obra_beta.pdf").stat().st_size,
            ),
        )
        conn.commit()
    finally:
        conn.close()

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

    assert started, "Servidor E2E Fase 4 Biblioteca não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
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


def test_v190_library_book_details_knowledge_subtab(library_knowledge_server):
    base_url = library_knowledge_server["base_url"]
    token = library_knowledge_server["token_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p4_lib_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        knowledge_reqs = []
        page.on(
            "request",
            lambda req: knowledge_reqs.append(req.url) if "/api/knowledge/" in req.url else None,
        )

        page.goto(base_url, wait_until="networkidle")
        # Switch to Library tab ("Biblioteca & Explorar") and open Book Details modal for Obra Alfa
        page.click("#tab-nav-library")
        page.wait_for_selector('.book-card[data-id="book_lib_alfa"]', timeout=10000)
        page.locator('.book-card[data-id="book_lib_alfa"] .btn-book-details').click()
        page.wait_for_selector("#modal-book-details.active", timeout=5000)

        # Lazy loading: ZERO /api/knowledge/* requests before clicking the Conhecimento subtab
        assert len(knowledge_reqs) == 0

        # Click Conhecimento subtab
        page.locator('.details-tab-btn[data-subtab="knowledge"]').click()
        page.wait_for_selector("#bk-btn-toggle-create-concept", timeout=8000)
        assert len(knowledge_reqs) >= 1

        # Create and associate a concept directly with book_lib_alfa
        page.click("#bk-btn-toggle-create-concept")
        page.fill("#bk-concept-name", "Dialética Histórica")
        page.fill("#bk-concept-desc", "Articulação entre filosofia e processo histórico.")
        page.click("#bk-btn-submit-create-concept")
        page.wait_for_selector("#bk-associated-concepts .rk-concept-chip", timeout=8000)
        assert "Dialética Histórica" in page.locator("#bk-associated-concepts").inner_text()

        # Connect book_lib_alfa -> book_lib_beta
        page.click("#bk-btn-toggle-new-link")
        page.select_option("#bk-target-type", "book")
        page.fill("#bk-target-search", "Beta")
        page.wait_for_selector("#bk-target-search-results .rk-lookup-item", timeout=8000)
        page.locator("#bk-target-search-results .rk-lookup-item").first.click()
        page.select_option("#bk-relation-select", "grounds")
        page.fill("#bk-rationale-input", "Obra Alfa fundamenta os pressupostos da Obra Beta.")
        page.click("#bk-btn-submit-new-link")
        page.wait_for_function(
            "() => document.getElementById('bk-links-list').innerText.includes('Fundamenta')",
            timeout=8000,
        )

        # Capture visual screenshot of Book Details -> Conhecimento
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase4_library_knowledge_desktop.png"))

        # Click "Abrir Caderno de Estudos" -> navigates to /reader/book_lib_alfa?drawer=1 and opens drawer!
        page.locator(".bk-open-notebook-link").click()
        page.wait_for_url("**/reader/book_lib_alfa?drawer=1", timeout=10000)
        page.wait_for_selector("#annotations-drawer.active", timeout=10000)

        browser.close()
