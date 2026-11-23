"""
Suíte de Testes E2E de Segurança, Sanitização Markdown e Privacidade — Fase 5 da Lectoria v1.9.0.
Valida:
- Renderizador seguro de Markdown (construído 100% via nós DOM, zero innerHTML):
  * Bloqueio total de <script>, <img onerror>, <svg onload>, <iframe>, <form> (DOM clobbering), <object>.
  * Bloqueio de links javascript:, data:, vbscript: e variantes ofuscadas com caracteres de controle.
  * Bloqueio estrito de imagens externas ![alt](https://...) impedindo qualquer requisição remota (Seção 12.3).
- Isolamento multiusuário completo no Atlas (conceitos, reflexões, conexões e estatísticas).
- Renderização segura de "Referência indisponível" em reflexões após revogação por terceiro, preservando a justificativa.
- Zero persistência de reflexões/conceitos privados em localStorage.
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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Seguranca Atlas Fase 5) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def atlas_security_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p5_sec_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "sec_p5.pdf")

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

    alice = auth_service.create_user("alice_p5_sec", "Password123!", "Alice Sec P5", role="admin")
    bob = auth_service.create_user("bob_p5_sec", "Password123!", "Bob Sec P5", role="reader")

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Sec P5', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book_p5_sec', 'Obra Seguranca Fase 5', 'Autor Sec', 'sec_p5.pdf', 'sec_p5.pdf', ?, 'pdf', 1, 1, 0);
            """,
            ((lib_dir / "sec_p5.pdf").stat().st_size,),
        )
        conn.commit()
    finally:
        conn.close()

    alice_hl = annotations_service.create_highlight(
        book_id="book_p5_sec",
        user_id=alice["id"],
        page=1,
        selected_text="Trecho confidencial de Alice temporariamente público.",
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

    assert started, "Servidor E2E Fase 5 Segurança não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "alice": alice,
            "bob": bob,
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


def test_v190_atlas_markdown_adversarial_security_and_revocation(atlas_security_server):
    base_url = atlas_security_server["base_url"]
    alice = atlas_security_server["alice"]
    bob = atlas_security_server["bob"]
    token_alice = atlas_security_server["token_alice"]
    token_bob = atlas_security_server["token_bob"]
    alice_hl = atlas_security_server["alice_hl"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ctx_bob = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_bob.add_cookies(
            [
                {"name": "lectoria_session", "value": token_bob, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_bob_p5_sec", "url": base_url},
            ]
        )
        page_bob = ctx_bob.new_page()

        remote_requests = []
        page_bob.on(
            "request",
            lambda req: remote_requests.append(req.url) if "evil.example.com" in req.url else None,
        )

        adversarial_md = "\n".join(
            [
                "# Reflexão Adversarial",
                '<script>window.__xss_atlas = 1;</script>',
                '<img src="x" onerror="window.__xss_atlas = 2">',
                '<svg onload="window.__xss_atlas = 3"></svg>',
                '<iframe src="javascript:window.__xss_atlas = 4"></iframe>',
                '<form id="createElement"><input name="nodeName"></form>',
                "[Link JS](javascript:window.__xss_atlas=5)",
                "[Link Ofuscado](java\tscript:window.__xss_atlas=6)",
                "[Link Data](data:text/html,<script>window.__xss_atlas=7</script>)",
                "![Tracker Externo](https://evil.example.com/tracker.png)",
                "[Link Seguro](https://example.org/estudo)",
            ]
        )

        # Bob creates reflection with adversarial Markdown and links Alice's public highlight
        ref_bob = knowledge_service.create_reflection(
            bob["id"],
            'Reflexão Privada de Bob <img src=x onerror="window.__xss_atlas=8">',
            adversarial_md,
        )
        knowledge_service.create_knowledge_link(
            bob["id"],
            "reflection",
            ref_bob["id"],
            "highlight",
            alice_hl["id"],
            "questions",
            "Justificativa autoral privada de Bob sobre o destaque.",
        )

        page_bob.goto(f"{base_url}/?tab=atlas&subtab=reflections&id={ref_bob['id']}", wait_until="networkidle")
        page_bob.wait_for_selector("#atlas-reflection-preview", timeout=10000)

        # 1. Verify ZERO script execution, ZERO forbidden HTML tags, ZERO remote image requests!
        assert page_bob.evaluate("() => window.__xss_atlas === undefined")
        assert len(remote_requests) == 0
        forbidden_count = page_bob.evaluate(
            "() => document.querySelectorAll('#atlas-reflection-preview script, #atlas-reflection-preview img, #atlas-reflection-preview svg, #atlas-reflection-preview iframe, #atlas-reflection-preview form, #atlas-reflection-preview object').length"
        )
        assert forbidden_count == 0
        assert page_bob.locator("#atlas-reflection-preview .atlas-md-image-blocked").count() == 1
        assert page_bob.locator("#atlas-reflection-preview .atlas-md-link-blocked").count() == 3
        safe_link = page_bob.locator("#atlas-reflection-preview a.atlas-md-link")
        assert safe_link.count() == 1
        assert safe_link.get_attribute("rel") == "noopener noreferrer"

        # Verify zero private reflection content in localStorage
        ls_dump = page_bob.evaluate("() => JSON.stringify(window.localStorage)")
        assert "Reflexão Privada de Bob" not in ls_dump
        assert "Justificativa autoral privada de Bob" not in ls_dump

        # 2. Alice opens her Atlas -> sees ZERO reflections, concepts or links of Bob
        ctx_alice = browser.new_context(viewport={"width": 1366, "height": 860})
        ctx_alice.add_cookies(
            [
                {"name": "lectoria_session", "value": token_alice, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_alice_p5_sec", "url": base_url},
            ]
        )
        page_alice = ctx_alice.new_page()
        page_alice.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
        page_alice.wait_for_selector(".atlas-welcome-empty", timeout=8000)
        assert "Reflexão Privada de Bob" not in page_alice.locator("#atlas-section").inner_text()

        # 3. Alice revokes highlight visibility (public -> private)
        annotations_service.set_highlight_visibility(
            highlight_id=alice_hl["id"],
            user_id=alice["id"],
            visibility="private",
        )

        # 4. Bob reloads his reflection in Atlas -> sees "Referência indisponível", zero protected metadata, rationale preserved!
        page_bob.reload(wait_until="networkidle")
        page_bob.wait_for_selector(".rk-link-card-unavailable", timeout=8000)
        unavail_txt = page_bob.locator(".rk-link-card-unavailable").first.inner_text()
        assert "Referência indisponível" in unavail_txt
        assert "Justificativa autoral privada de Bob" in unavail_txt
        assert "Trecho confidencial de Alice" not in unavail_txt
        assert "Obra Seguranca Fase 5" not in unavail_txt
        assert page_bob.locator(".rk-link-card-unavailable .rk-btn-navigate").count() == 0

        page_bob.locator(".rk-link-card-unavailable").first.scroll_into_view_if_needed()
        page_bob.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_unavailable_ref_desktop.png"))

        browser.close()
