"""
Suíte de Regressão E2E (Playwright) — Fase 10.1 (v1.9.1).
Valida a correção do bug crítico de loop infinito / travamento ao digitar `# `
no editor Markdown das reflexões do Atlas, bem como:
- Digitação real caractere por caractere de `# ` até `###### ` (H1–H6).
- Títulos com conteúdo, `#` sem espaço, `# ` seguido de Enter, acentuação/Unicode.
- Alternância entre modos Editar, Visualizar e Dividido sem travar.
- Digitação rápida, exclusão, colagem e conteúdo longo (250 títulos) com
  crescimento estritamente linear de nós DOM e tempo controlado.
- Salvamento e reabertura de reflexão com títulos H1–H6.
- Proteção XSS mantida (nenhum <script>, <img> ou href javascript:).
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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def markdown_regression_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("lectoria_v191_md_reg")
    data_dir = tmp_path / "data"
    lib_dir = tmp_path / "books"
    covers_dir = data_dir / "covers"
    backups_dir = data_dir / "backups"
    for d in (data_dir, lib_dir, covers_dir, backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    old_vals = (
        config.BASE_DIR,
        config.LIBRARY_DIR,
        config.DATA_DIR,
        config.DB_PATH,
        config.COVERS_DIR,
        config.BACKUPS_DIR,
    )
    config.BASE_DIR = tmp_path
    config.LIBRARY_DIR = lib_dir
    config.DATA_DIR = data_dir
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.BACKUPS_DIR = backups_dir

    database.init_db()
    user = auth_service.create_user(
        "md_tester",
        "SenhaForteMd123!",
        role="admin",
        display_name="Markdown Tester",
    )
    token = auth_service.create_session(user["id"])

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
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.25)

    assert started, "Servidor E2E de regressão Markdown não iniciou a tempo."

    try:
        yield {"base_url": base_url, "token": token}
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


def test_v191_atlas_markdown_heading_no_hang_h1_to_h6_xss_and_scale(markdown_regression_server):
    base_url = markdown_regression_server["base_url"]
    token = markdown_regression_server["token"]

    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_v191_md_reg", "url": base_url},
            ]
        )
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        page.goto(f"{base_url}/?tab=atlas&subtab=reflections", wait_until="networkidle")
        page.wait_for_selector("#atlas-btn-new-reflection", timeout=8000)
        page.click("#atlas-btn-new-reflection")
        page.wait_for_selector("#atlas-reflection-body", timeout=5000)

        body = page.locator("#atlas-reflection-body")
        preview = page.locator("#atlas-reflection-preview")

        # 1. Digitação real de `# ` até `###### ` (gatilho exato do bug crítico)
        for level in range(1, 7):
            hashes = "#" * level
            body.fill("")
            body.click()
            t0 = time.perf_counter()
            body.type(f"{hashes} ", delay=5, timeout=3000)
            elapsed = time.perf_counter() - t0
            assert elapsed < 2.0, f"Digitação de '{hashes} ' demorou {elapsed:.2f}s"

            dom_info = page.evaluate(
                """() => {
                    const box = document.getElementById('atlas-reflection-preview');
                    return {
                        childCount: box ? box.children.length : -1,
                        firstTag: box && box.firstElementChild ? box.firstElementChild.tagName : null,
                    };
                }"""
            )
            assert dom_info["childCount"] == 1, f"Esperado 1 nó para '{hashes} ', obtido {dom_info}"
            assert dom_info["firstTag"] == f"H{level}"

        # 2. `# ` seguido de Enter + parágrafo e `#` sem espaço
        body.fill("")
        body.click()
        body.type("# ", delay=5, timeout=3000)
        body.press("Enter")
        body.type("Linha após título vazio", delay=5, timeout=3000)
        body.press("Enter")
        body.press("Enter")
        body.type("#semespaco e # isolado", delay=5, timeout=3000)

        assert preview.locator("h1.atlas-md-h1").count() == 1
        assert "Linha após título vazio" in preview.inner_text()
        assert "#semespaco e # isolado" in preview.inner_text()

        # 3. Todos os níveis H1–H6, Unicode/acentuação e proteção XSS
        rich_md = "\n".join(
            [
                "# Título Principal — Época & Técnica",
                "## Subtítulo com **negrito** e *itálico*",
                "### Terceiro nível (ação, coração, Ω)",
                "#### Quarto nível",
                "##### Quinto nível",
                "###### Sexto nível",
                "",
                "####### Sete cerquilhas vira parágrafo normal",
                "",
                "<script>window.__lectoria_xss = 'pwned';</script>",
                "<img src=x onerror=\"window.__lectoria_xss='img'\">",
                "[Link malicioso](javascript:alert(1))",
                "![Tracking](https://example.com/pixel.png)",
            ]
        )
        body.fill(rich_md)
        for level in range(1, 7):
            assert preview.locator(f"h{level}.atlas-md-h{level}").count() == 1
        assert "Título Principal — Época & Técnica" in preview.locator("h1").inner_text()
        assert "Terceiro nível (ação, coração, Ω)" in preview.locator("h3").inner_text()
        assert "Sexto nível" in preview.locator("h6").inner_text()
        assert "Sete cerquilhas vira parágrafo normal" in preview.inner_text()

        # Garantias de segurança XSS
        assert preview.locator("script").count() == 0
        assert preview.locator("img").count() == 0
        assert preview.locator("a[href^='javascript:']").count() == 0
        assert page.evaluate("() => window.__lectoria_xss === undefined") is True
        assert "Imagem externa bloqueada" in preview.inner_text()

        # 4. Alternância entre modos Editar, Visualizar e Dividido + edição/deleção rápida
        page.click("#atlas-md-mode-edit")
        page.click("#atlas-md-mode-preview")
        assert preview.locator("h1.atlas-md-h1").count() == 1
        page.click("#atlas-md-mode-split")
        body.click()
        body.press("End")
        body.type("\n# ", delay=5, timeout=3000)
        body.press("Backspace")
        body.press("Backspace")
        assert preview.locator("h1.atlas-md-h1").count() == 1

        # 5. Escala controlada: 250 títulos + parágrafos em renderização linear (sem explosão DOM)
        long_md = "\n".join(
            f"{'#' * ((idx % 6) + 1)} Seção {idx}\nParágrafo de estudo {idx} com **ênfase**.\n"
            for idx in range(250)
        )
        t0 = time.perf_counter()
        body.fill(long_md)
        scale_elapsed = time.perf_counter() - t0
        assert scale_elapsed < 2.5, f"Renderização longa demorou {scale_elapsed:.2f}s"
        total_children = page.evaluate(
            "() => document.getElementById('atlas-reflection-preview').children.length"
        )
        assert total_children == 500, f"Esperado exatamente 500 blocos (250h + 250p), obtido {total_children}"

        # 6. Salvamento e reabertura da reflexão com H1–H6 e `# ` intermediário
        page.fill("#atlas-reflection-title", "Regressão Markdown H1-H6")
        body.fill("# Título Salvo\n## Subtítulo\n### Terceiro nível\n###### Sexto nível\n\nTexto final.")
        page.click("#atlas-btn-save-reflection")
        page.wait_for_selector("#atlas-reflection-save-status", timeout=5000)
        page.wait_for_function(
            "() => (document.getElementById('atlas-reflection-save-status')?.textContent || '').trim() === 'Salva'",
            timeout=8000,
        )

        # Volta à listagem e reabre a reflexão salva
        page.click("#atlas-btn-back-reflections")
        page.wait_for_selector("text=Regressão Markdown H1-H6", timeout=5000)
        page.click("text=Regressão Markdown H1-H6")
        page.wait_for_selector("#atlas-reflection-preview h6.atlas-md-h6", timeout=5000)
        assert preview.locator("h1.atlas-md-h1").inner_text() == "Título Salvo"
        assert preview.locator("h6.atlas-md-h6").inner_text() == "Sexto nível"

        assert console_errors == [], f"Erros de console inesperados: {console_errors}"
        assert page_errors == [], f"Erros de página inesperados: {page_errors}"

        browser.close()
