"""
Suíte de Testes E2E (Playwright) — Fase 5 da Lectoria v1.9.0.
Valida:
- Visão Geral povoada do Atlas (contagens reais, conceitos recentes, reflexões recentes).
- Explorador textual de Conexões (#atlas-pane-connections): listagem, criação de conexão independente
  entre quaisquer duas entidades autorizadas, filtro pelos 8 tipos semânticos, filtro por tipo de entidade,
  edição de justificativa e exclusão com confirmação em duas etapas.
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
import knowledge_service


from tests.e2e.artifact_utils import get_artifact_dir

ARTIFACT_DIR = get_artifact_dir()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def atlas_connections_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p5_connections_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

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

    alice = auth_service.create_user("alice_p5_conn", "Password123!", "Alice Conexoes", role="admin")
    c1 = knowledge_service.create_concept(alice["id"], "Racionalidade Instrumental", description="Meios sem crítica dos fins.")
    c2 = knowledge_service.create_concept(alice["id"], "Emancipação", description="Autonomia reflexiva do sujeito.")
    r1 = knowledge_service.create_reflection(
        alice["id"],
        "Notas sobre a Escola de Frankfurt",
        "# Crítica da Razão\n\nArticulação entre técnica e emancipação.",
        is_pinned=True,
    )
    knowledge_service.create_knowledge_link(
        alice["id"],
        "reflection",
        r1["id"],
        "concept",
        c1["id"],
        "questions",
        "A reflexão questiona o predomínio da razão instrumental.",
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

    assert started, "Servidor E2E Fase 5 Conexões não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "c1": c1,
            "c2": c2,
            "r1": r1,
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


def test_v190_atlas_overview_and_connections_explorer(atlas_connections_server):
    base_url = atlas_connections_server["base_url"]
    token = atlas_connections_server["token_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p5_conn_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        # 1. Open Atlas Overview with populated data
        page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
        page.wait_for_selector("#atlas-stat-concepts", timeout=8000)
        assert "2" in page.locator("#atlas-stat-concepts").inner_text()
        assert "1" in page.locator("#atlas-stat-reflections").inner_text()
        assert "1" in page.locator("#atlas-stat-links").inner_text()
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_overview_desktop.png"))

        # 2. Switch to Conexões -> create independent connection c1 -> c2 (contradicts)
        page.click("#atlas-subtab-connections")
        page.wait_for_selector("#atlas-btn-new-connection", timeout=5000)
        page.click("#atlas-btn-new-connection")

        page.fill("#atlas-conn-source-search", "Racionalidade")
        page.wait_for_selector("#atlas-conn-source-results .rk-lookup-item", timeout=8000)
        page.locator("#atlas-conn-source-results .rk-lookup-item").first.click()

        page.fill("#atlas-conn-target-search", "Emancipação")
        page.wait_for_selector("#atlas-conn-target-results .rk-lookup-item", timeout=8000)
        page.locator("#atlas-conn-target-results .rk-lookup-item").first.click()

        page.select_option("#atlas-indep-relation-select", "contradicts")
        page.fill("#atlas-indep-rationale-input", "Tensão dialética entre razão instrumental e emancipação.")
        page.click("#atlas-indep-submit-btn")

        page.wait_for_function(
            "() => document.getElementById('atlas-connections-list').innerText.includes('Contradiz')",
            timeout=8000,
        )

        # Capture Connections Explorer screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase5_atlas_connections_desktop.png"))

        # 3. Filter by entity_type = reflection, concept, book (Section 8)
        page.select_option("#atlas-connections-entity-filter", "reflection")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 1",
            timeout=8000,
        )
        page.select_option("#atlas-connections-entity-filter", "concept")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 2",
            timeout=8000,
        )
        page.select_option("#atlas-connections-entity-filter", "book")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 0",
            timeout=8000,
        )
        page.select_option("#atlas-connections-entity-filter", "")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 2",
            timeout=8000,
        )

        # 4. Filter by relation_type = contradicts
        page.select_option("#atlas-connections-relation-filter", "contradicts")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 1",
            timeout=8000,
        )

        # Edit rationale inline
        card = page.locator("#atlas-connections-list .rk-link-card").first
        card.locator(".rk-btn-edit-link").click()
        card.locator(".rk-edit-rationale-input").fill("Justificativa atualizada no explorador de conexões.")
        card.locator(".rk-btn-save-edit").click()
        page.wait_for_function(
            "() => document.getElementById('atlas-connections-list').innerText.includes('Justificativa atualizada')",
            timeout=8000,
        )

        # Delete the connection with 2-step confirmation
        card = page.locator("#atlas-connections-list .rk-link-card").first
        card.locator(".rk-btn-delete-link").click()
        card.locator(".rk-btn-confirm-delete").click()
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-connections-list .rk-link-card').length === 0",
            timeout=8000,
        )

        browser.close()
