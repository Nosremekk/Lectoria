"""
Suíte de Testes E2E de Segurança, Privacidade e Robustez — Fase 3 da Lectoria v1.9.0.
Cobre:
- Ciclo multiusuário de privacidade e revogação (11 passos):
  Alice cria destaque privado -> Bob não vê -> Alice publica -> Bob vê somente leitura + associa conceito/link privado ->
  Alice não vê conceitos/links/backlinks de Bob -> Alice revoga para privado -> Bob vê 'Referência indisponível' sem vazamento
  de metadados, sem botão Abrir, preservando sua justificativa e podendo excluir a conexão.
- Proteção contra XSS (payloads HTML/JS em conceito e justificativa renderizados via textContent).
- Zero persistência de dados privados do Atlas em localStorage.
- Envio estrito de CSRF via cabeçalho X-CSRF-Token (nunca na URL).
- Tratamento de erro de rede sem quebrar o leitor.
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
        text = f"Seguranca Atlas - Pagina {idx}: Conteudo de validacao."
        stream.set_data(f"BT /F1 16 Tf 72 740 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def security_knowledge_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p3_sec_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "seguranca_atlas.pdf", num_pages=3)

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
        username="alice_sec_p3",
        password="Password123!",
        display_name="Alice Seguranca",
        role="admin",
    )
    bob = auth_service.create_user(
        username="bob_sec_p3",
        password="Password123!",
        display_name="Bob Seguranca",
        role="reader",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
            (?, 'Alice Seguranca', '#e11d48', 'Autora Alice'),
            (?, 'Bob Seguranca', '#2563eb', 'Leitor Bob');
            """,
            (alice["id"], bob["id"]),
        )
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Sec v190', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book_pdf_sec', 'Obra de Segurança Atlas', 'Autor Sec', 'seguranca_atlas.pdf', 'seguranca_atlas.pdf', ?, 'pdf', 3, 1, 0);
            """,
            ((lib_dir / "seguranca_atlas.pdf").stat().st_size,),
        )
        conn.commit()
    finally:
        conn.close()

    # Step 1: Alice creates a PRIVATE highlight; Bob creates his own PRIVATE note
    alice_hl = annotations_service.create_highlight(
        book_id="book_pdf_sec",
        user_id=alice["id"],
        page=1,
        selected_text="Trecho sigiloso exclusivo de Alice antes da publicação.",
        rects=[{"x": 0.12, "y": 0.12, "w": 0.55, "h": 0.04}],
        color="#fef08a",
        visibility="private",
    )
    bob_note = annotations_service.create_note(
        book_id="book_pdf_sec",
        user_id=bob["id"],
        page=1,
        content="Nota pessoal de Bob para conectar ao estudo.",
        visibility="private",
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

    assert started, "Servidor E2E v1.9.0 Segurança não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "bob": bob,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "alice_hl": alice_hl,
            "bob_note": bob_note,
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


def test_v190_multiuser_privacy_revocation_xss_csrf_and_errors(security_knowledge_server):
    base_url = security_knowledge_server["base_url"]
    alice = security_knowledge_server["alice"]
    token_alice = security_knowledge_server["token_alice"]
    token_bob = security_knowledge_server["token_bob"]
    alice_hl = security_knowledge_server["alice_hl"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ctx_alice = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_alice.add_cookies(
            [
                {"name": "lectoria_session", "value": token_alice, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_alice_sec", "url": base_url},
            ]
        )
        page_alice = ctx_alice.new_page()

        ctx_bob = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_bob.add_cookies(
            [
                {"name": "lectoria_session", "value": token_bob, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_bob_sec", "url": base_url},
            ]
        )
        page_bob = ctx_bob.new_page()

        csrf_mutating_checks = []

        def _inspect_bob_req(req):
            if "/api/knowledge/" in req.url and req.method in ("POST", "PATCH", "DELETE"):
                csrf_mutating_checks.append(
                    {
                        "url": req.url,
                        "method": req.method,
                        "has_header": bool(req.headers.get("x-csrf-token")),
                        "has_query_csrf": "csrf_token=" in req.url,
                    }
                )

        page_bob.on("request", _inspect_bob_req)

        # Step 2: Bob opens book_pdf_sec -> must NOT see Alice's private highlight
        page_bob.goto(f"{base_url}/reader/book_pdf_sec", wait_until="networkidle")
        page_bob.wait_for_selector(".page-note-pin", timeout=15000)
        assert page_bob.locator(".highlight-box").count() == 0

        # Step 3: Alice explicitly publishes her highlight
        annotations_service.set_highlight_visibility(
            highlight_id=alice_hl["id"],
            user_id=alice["id"],
            visibility="public",
        )

        # Step 4: Bob reloads, sees Alice's public highlight, opens popover (read-only + Conhecimento)
        page_bob.reload(wait_until="networkidle")
        page_bob.wait_for_selector(".highlight-box", timeout=15000)
        page_bob.locator(".highlight-box").first.click()
        page_bob.wait_for_selector("#highlight-popover", timeout=5000)
        pop_text = page_bob.locator("#hl-pop-actions").inner_text()
        assert "somente leitura" in pop_text.lower()
        assert "Excluir" not in pop_text
        assert "Conhecimento" in pop_text

        # Step 5: Bob opens Conhecimento on Alice's public highlight and associates an XSS-named private concept
        xss_concept_name = '<img src=x onerror="window.__xss=1"> Conceito Privado de Bob'
        page_bob.locator("#hl-pop-actions .btn-open-knowledge").click()
        page_bob.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page_bob.click("#rk-btn-toggle-create-concept")
        page_bob.fill("#rk-concept-name", xss_concept_name)
        page_bob.fill("#rk-concept-desc", '<script>window.__xss=2</script> Descrição segura.')
        page_bob.click("#rk-btn-submit-create-concept")
        page_bob.wait_for_selector(".rk-concept-chip", timeout=8000)
        assert page_bob.evaluate("() => window.__xss === undefined")
        page_bob.keyboard.press("Escape")

        # Bob also connects his own private note -> Alice's public highlight with XSS rationale
        page_bob.locator(".page-note-pin").first.click()
        page_bob.wait_for_selector("#page-note-popover", timeout=5000)
        page_bob.locator("#note-pop-actions .btn-open-knowledge").click()
        page_bob.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)

        page_bob.click("#rk-btn-toggle-new-link")
        page_bob.select_option("#rk-target-type", "highlight")
        page_bob.fill("#rk-target-search", "sigiloso")
        page_bob.wait_for_selector("#rk-target-search-results .rk-lookup-item", timeout=8000)
        page_bob.locator("#rk-target-search-results .rk-lookup-item").first.click()
        page_bob.select_option("#rk-relation-select", "questions")
        page_bob.fill(
            "#rk-rationale-input",
            'Justificativa privada do Bob <img src=x onerror="window.__xss=3">',
        )
        page_bob.click("#rk-btn-submit-new-link")
        page_bob.wait_for_function(
            "() => document.getElementById('rk-links-list').innerText.includes('Questiona')",
            timeout=8000,
        )
        assert page_bob.evaluate("() => window.__xss === undefined")

        # Verify CSRF strictly in header, never in URL
        assert len(csrf_mutating_checks) >= 2
        for chk in csrf_mutating_checks:
            assert chk["has_header"] is True
            assert chk["has_query_csrf"] is False

        # Verify ZERO private knowledge data leaked to localStorage
        ls_dump = page_bob.evaluate("() => JSON.stringify(window.localStorage)")
        assert "Conceito Privado de Bob" not in ls_dump
        assert "Justificativa privada do Bob" not in ls_dump

        # Step 6: Alice opens her own highlight -> sees ZERO concepts, links or backlinks of Bob
        page_alice.goto(f"{base_url}/reader/book_pdf_sec", wait_until="networkidle")
        page_alice.wait_for_selector(".highlight-box", timeout=15000)
        page_alice.locator(".highlight-box").first.click()
        page_alice.wait_for_selector("#highlight-popover", timeout=5000)
        page_alice.locator("#hl-pop-actions .btn-open-knowledge").click()
        page_alice.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page_alice.wait_for_function(
            "() => document.getElementById('rk-status-banner').classList.contains('hidden')",
            timeout=8000,
        )
        alice_modal_text = page_alice.locator("#modal-reader-knowledge").inner_text()
        assert "Conceito Privado de Bob" not in alice_modal_text
        assert "Justificativa privada do Bob" not in alice_modal_text
        assert "Conexões de saída (0)" in alice_modal_text
        assert "Backlinks recebidos (0)" in alice_modal_text

        # Step 7: Alice revokes highlight visibility (public -> private)
        annotations_service.set_highlight_visibility(
            highlight_id=alice_hl["id"],
            user_id=alice["id"],
            visibility="private",
        )

        # Step 8: Bob reloads his note's Knowledge modal -> sees "Referência indisponível",
        # ZERO protected metadata from Alice, NO "Abrir" button, rationale preserved, can delete!
        page_bob.reload(wait_until="networkidle")
        page_bob.wait_for_selector(".page-note-pin", timeout=15000)
        page_bob.locator(".page-note-pin").first.click()
        page_bob.wait_for_selector("#page-note-popover", timeout=5000)
        page_bob.locator("#note-pop-actions .btn-open-knowledge").click()
        page_bob.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page_bob.wait_for_selector(".rk-link-card-unavailable", timeout=8000)

        unavail_card = page_bob.locator(".rk-link-card-unavailable").first
        unavail_text = unavail_card.inner_text()
        assert "Referência indisponível" in unavail_text
        assert "Justificativa privada do Bob" in unavail_text
        assert "Trecho sigiloso exclusivo de Alice" not in unavail_text
        assert "Alice Seguranca" not in unavail_text
        assert "Obra de Segurança Atlas" not in unavail_text
        assert unavail_card.locator(".rk-btn-navigate").count() == 0

        # Capture visual screenshot of Unavailable Reference state
        page_bob.screenshot(path=str(ARTIFACT_DIR / "v190_phase3_unavailable_ref_desktop.png"))

        # Bob can still delete his own link to the unavailable reference
        unavail_card.locator(".rk-btn-delete-link").click()
        unavail_card.locator(".rk-btn-confirm-delete").click()
        page_bob.wait_for_function(
            "() => document.querySelectorAll('.rk-link-card-unavailable').length === 0",
            timeout=8000,
        )

        # Network error handling: abort /api/knowledge/concepts -> error banner shown, reader intact
        page_bob.route("**/api/knowledge/concepts", lambda route: route.abort())
        page_bob.click("#rk-btn-toggle-create-concept")
        page_bob.fill("#rk-concept-name", "Conceito Sem Rede")
        page_bob.click("#rk-btn-submit-create-concept")
        page_bob.wait_for_selector("#rk-status-banner.rk-banner-error", timeout=8000)
        assert "Falha de comunicação" in page_bob.locator("#rk-status-banner").inner_text()
        page_bob.unroute("**/api/knowledge/concepts")

        browser.close()
