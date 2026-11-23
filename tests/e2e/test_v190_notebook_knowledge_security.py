"""
Suíte de Testes E2E de Segurança e Privacidade — Fase 4 da Lectoria v1.9.0.
Valida no Caderno de Estudos:
- Isolamento multiusuário completo (conceitos, conexões, contagens e filtro 'Conectadas' de Bob nunca vazam para Alice).
- Proteção contra XSS nos chips do Caderno de Estudos (renderização estrita via textContent).
- Degradação graciosa: se /api/knowledge/entities/summary falhar na rede, as anotações convencionais continuam visíveis.
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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Seguranca Caderno Atlas) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def notebook_security_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p4_sec_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "sec_caderno.pdf")

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

    alice = auth_service.create_user("alice_p4_sec", "Password123!", "Alice Sec", role="admin")
    bob = auth_service.create_user("bob_p4_sec", "Password123!", "Bob Sec", role="reader")

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
            (?, 'Alice Sec', '#e11d48', ''),
            (?, 'Bob Sec', '#2563eb', '');
            """,
            (alice["id"], bob["id"]),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Sec P4', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book_p4_sec', 'Obra Seguranca Caderno', 'Autor Sec', 'sec_caderno.pdf', 'sec_caderno.pdf', ?, 'pdf', 1, 1, 0);
            """,
            ((lib_dir / "sec_caderno.pdf").stat().st_size,),
        )
        conn.commit()
    finally:
        conn.close()

    alice_hl = annotations_service.create_highlight(
        book_id="book_p4_sec",
        user_id=alice["id"],
        page=1,
        selected_text="Destaque público de Alice no Caderno de Estudos.",
        rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
        visibility="public",
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
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/csrf", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.25)

    assert started, "Servidor E2E Fase 4 Segurança não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "alice_hl": alice_hl,
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


def test_v190_notebook_multiuser_privacy_xss_and_graceful_degradation(notebook_security_server):
    base_url = notebook_security_server["base_url"]
    token_alice = notebook_security_server["token_alice"]
    token_bob = notebook_security_server["token_bob"]
    alice_hl = notebook_security_server["alice_hl"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ctx_bob = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_bob.add_cookies(
            [
                {"name": "lectoria_session", "value": token_bob, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_bob_p4_sec", "url": base_url},
            ]
        )
        page_bob = ctx_bob.new_page()

        # Bob opens Study Notebook, sees Alice's public highlight, attaches XSS concept privately
        page_bob.goto(f"{base_url}/reader/book_p4_sec?drawer=1", wait_until="networkidle")
        page_bob.wait_for_selector(f'.btn-drawer-knowledge[data-entity-id="{alice_hl["id"]}"]', timeout=12000)
        page_bob.locator(f'.btn-drawer-knowledge[data-entity-id="{alice_hl["id"]}"]').click()
        page_bob.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        page_bob.click("#rk-btn-toggle-create-concept")
        page_bob.fill("#rk-concept-name", '<img src=x onerror="window.__xss_nb=1"> Conceito Secreto Bob')
        page_bob.click("#rk-btn-submit-create-concept")
        page_bob.wait_for_selector(".rk-concept-chip", timeout=8000)
        page_bob.keyboard.press("Escape")
        page_bob.wait_for_selector("#modal-reader-knowledge.hidden", state="hidden", timeout=5000)

        # Verify XSS concept renders safely in Bob's Study Notebook chip without executing JS
        page_bob.wait_for_selector(".drawer-concept-chip", timeout=8000)
        assert page_bob.evaluate("() => window.__xss_nb === undefined")
        assert "Conceito Secreto Bob" in page_bob.locator("#drawer-items-list").inner_text()

        # Alice opens her Study Notebook -> ZERO concepts, ZERO connections, ZERO counts of Bob!
        ctx_alice = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_alice.add_cookies(
            [
                {"name": "lectoria_session", "value": token_alice, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_alice_p4_sec", "url": base_url},
            ]
        )
        page_alice = ctx_alice.new_page()
        page_alice.goto(f"{base_url}/reader/book_p4_sec?drawer=1", wait_until="networkidle")
        page_alice.wait_for_function(
            "() => document.getElementById('drawer-knowledge-summary').textContent.includes('0 conceito(s)')",
            timeout=10000,
        )
        drawer_txt = page_alice.locator("#annotations-drawer").inner_text()
        assert "Conceito Secreto Bob" not in drawer_txt
        assert page_alice.locator(".drawer-concept-chip").count() == 0

        # Filter "Conectadas" for Alice returns 0
        page_alice.locator('.drawer-kf-btn[data-knowledge-filter="connected"]').click()
        page_alice.wait_for_function(
            "() => document.getElementById('count-highlights').textContent.trim() === '0'",
            timeout=8000,
        )

        # Graceful degradation: abort /api/knowledge/* -> Study Notebook STILL renders conventional annotations!
        page_alice.route("**/api/knowledge/**", lambda route: route.abort())
        page_alice.locator('.drawer-kf-btn[data-knowledge-filter="all"]').click()
        page_alice.wait_for_selector(f'.annotation-item[data-item-id="{alice_hl["id"]}"]', timeout=8000)
        assert "Destaque público de Alice" in page_alice.locator("#drawer-items-list").inner_text()
        page_alice.unroute("**/api/knowledge/**")

        browser.close()
