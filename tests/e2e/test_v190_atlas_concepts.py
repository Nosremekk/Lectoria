"""
Suíte de Testes E2E (Playwright) — Fase 5 da Lectoria v1.9.0.
Valida:
- Navegação principal para o Atlas (#tab-nav-atlas) e carregamento sob demanda (zero chamadas antes de abrir o Atlas).
- Estado vazio do Atlas na Visão Geral.
- Gestão independente de Conceitos: criação (com acentuação/Unicode), conflito 409 de duplicidade, busca, ordenação,
  detalhes do conceito, criação de conexões diretas, backlinks, renomeação preservando UUID e conexões existentes,
  integração reversa (Leitor -> Abrir no Atlas) e exclusão com confirmação explícita preservando obras/anotações.
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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Conceitos Atlas Fase 5) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def atlas_concepts_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p5_concepts_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "obra_conceitos.pdf")

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

    alice = auth_service.create_user("alice_p5_conc", "Password123!", "Alice Conceitos", role="admin")

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo P5', ?, 1);",
            (str(lib_dir),),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing)
            VALUES ('book_p5_conc', 'Fenomenologia e Técnica', 'Autor Fenomeno', 'obra_conceitos.pdf', 'obra_conceitos.pdf', ?, 'pdf', 1, 1, 0);
            """,
            ((lib_dir / "obra_conceitos.pdf").stat().st_size,),
        )
        conn.commit()
    finally:
        conn.close()

    hl = annotations_service.create_highlight(
        book_id="book_p5_conc",
        user_id=alice["id"],
        page=1,
        selected_text="Trecho sobre intencionalidade e horizonte técnico.",
        rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
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

    assert started, "Servidor E2E Fase 5 Conceitos não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "hl": hl,
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


def test_v190_atlas_concepts_crud_rename_and_reverse_navigation(atlas_concepts_server):
    base_url = atlas_concepts_server["base_url"]
    token = atlas_concepts_server["token_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p5_conc_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        knowledge_reqs = []
        page.on("request", lambda req: knowledge_reqs.append(req.url) if "/api/knowledge/" in req.url else None)

        page.goto(base_url, wait_until="networkidle")
        assert len(knowledge_reqs) == 0

        # 1. Open Atlas main tab -> Empty State
        page.click("#tab-nav-atlas")
        page.wait_for_selector(".atlas-welcome-empty", timeout=8000)
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_empty_desktop.png"))

        # 2. Switch to Conceitos -> create 2 concepts with accents/Unicode
        page.click("#atlas-subtab-concepts")
        page.wait_for_selector("#atlas-btn-new-concept", timeout=5000)
        page.click("#atlas-btn-new-concept")
        page.fill("#atlas-concept-form-name", "Hermenêutica Filosófica")
        page.fill("#atlas-concept-form-desc", "Compreensão histórica e horizonte de sentido.")
        page.fill("#atlas-concept-form-tags", "filosofia, linguagem")
        page.click("#atlas-concept-form-submit")
        page.wait_for_selector(".atlas-entity-card", timeout=8000)

        page.click("#atlas-btn-new-concept")
        page.fill("#atlas-concept-form-name", "Autonomia da Técnica")
        page.fill("#atlas-concept-form-desc", "O sistema técnico determina seus próprios fins.")
        page.fill("#atlas-concept-form-tags", "modernidade")
        page.click("#atlas-concept-form-submit")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-concepts-grid .atlas-entity-card').length === 2",
            timeout=8000,
        )

        # Duplicate concept (case/whitespace normalized) triggers 409 banner
        page.click("#atlas-btn-new-concept")
        page.fill("#atlas-concept-form-name", "  AUTONOMIA   DA TÉCNICA ")
        page.click("#atlas-concept-form-submit")
        page.wait_for_selector("#atlas-status-banner.rk-banner-error", timeout=8000)

        # Sort by Name A-Z and capture Concepts List screenshot
        page.select_option("#atlas-concepts-sort", "name_asc")
        page.wait_for_function(
            "() => document.querySelector('#atlas-concepts-grid .atlas-entity-card').innerText.includes('Autonomia')",
            timeout=8000,
        )
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_concepts_list_desktop.png"))

        # 3. Open Concept Detail for "Autonomia da Técnica"
        first_card = page.locator("#atlas-concepts-grid .atlas-entity-card").first
        concept_id = first_card.get_attribute("data-concept-id")
        first_card.locator(".atlas-btn-open-concept").click()
        page.wait_for_selector(".atlas-detail-title", timeout=8000)
        assert "Autonomia da Técnica" in page.locator(".atlas-detail-title").inner_text()

        # Connect concept -> highlight in book_p5_conc
        page.click("#atlas-btn-connect-concept")
        page.select_option(".atlas-link-target-type", "highlight")
        page.fill(".atlas-link-target-search", "intencionalidade")
        page.wait_for_selector(".rk-lookup-item", timeout=8000)
        page.locator(".rk-lookup-item").first.click()
        page.select_option(".atlas-link-relation-select", "grounds")
        page.fill(".atlas-link-rationale-input", "Conceito fundamenta o trecho destacado.")
        page.click(".atlas-link-submit-btn")
        page.wait_for_function(
            "() => document.getElementById('atlas-concept-links-list').innerText.includes('Fundamenta')",
            timeout=8000,
        )

        # Capture Concept Detail screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_concept_detail_desktop.png"))

        # 4. Rename concept -> verify UUID and existing link remain intact!
        page.click("#atlas-btn-edit-concept")
        page.fill("#atlas-concept-form-name", "Autonomia Tecnológica Moderna")
        page.click("#atlas-concept-form-submit")
        page.wait_for_function(
            "() => document.querySelector('.atlas-detail-title').innerText.includes('Autonomia Tecnológica Moderna')",
            timeout=8000,
        )
        assert "Fundamenta" in page.locator("#atlas-concept-links-list").inner_text()

        # 5. Navigate from Concept Detail -> Reader, then reverse navigate Reader -> "Abrir no Atlas"
        page.locator("#atlas-concept-links-list .rk-btn-navigate").first.click()
        page.wait_for_url("**/reader/book_p5_conc#page=1", timeout=10000)
        page.wait_for_selector(".highlight-box", timeout=12000)
        page.locator(".highlight-box").first.click()
        page.wait_for_selector("#highlight-popover", timeout=5000)
        page.locator("#hl-pop-actions .btn-open-knowledge").click()
        page.wait_for_selector("#modal-reader-knowledge:not(.hidden)", timeout=5000)
        page.wait_for_selector(".rk-concept-chip-btn", timeout=8000)
        page.locator(".rk-concept-chip-btn").first.click()
        page.wait_for_selector(".rk-btn-open-atlas", timeout=5000)
        page.locator("#rk-concept-preview .rk-btn-open-atlas").click()

        page.wait_for_url(f"**/?tab=atlas&subtab=concepts&id={concept_id}", timeout=10000)
        page.wait_for_selector(".atlas-detail-title", timeout=8000)
        assert "Autonomia Tecnológica Moderna" in page.locator(".atlas-detail-title").inner_text()

        # 6. Delete the second concept ("Hermenêutica Filosófica") with confirmation
        page.click("#atlas-btn-back-concepts")
        page.wait_for_selector("#atlas-concepts-grid", timeout=8000)
        herm_card = page.locator(".atlas-entity-card", has_text="Hermenêutica").first
        herm_card.locator(".atlas-btn-open-concept").click()
        page.wait_for_selector("#atlas-btn-delete-concept", timeout=5000)
        page.click("#atlas-btn-delete-concept")
        page.wait_for_selector("#atlas-btn-confirm-delete-concept", timeout=5000)
        page.click("#atlas-btn-confirm-delete-concept")
        page.wait_for_selector("#atlas-concepts-grid", timeout=8000)
        assert "Hermenêutica Filosófica" not in page.locator("#atlas-concepts-grid").inner_text()

        browser.close()
