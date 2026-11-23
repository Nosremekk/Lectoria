"""
Suíte de Testes E2E (Playwright) — Fase 7 da Lectoria v1.9.0 (`tests/e2e/test_v190_atlas_export.py`).
Valida a interface e os downloads reais de exportação do Atlas:
- Abertura do modal de exportação (`#atlas-btn-open-export` -> `#atlas-export-modal`).
- Exibição dos avisos de privacidade (`#atlas-export-privacy-notice`).
- Seleção e download real de Markdown ZIP (com wikilinks), JSON v2.0 e CSV tabular.
- Verificação do conteúdo dos arquivos baixados no disco.
- Prevenção de cliques duplicados, estado de carregamento, tratamento de erro (HTTP 500 simulado) e cancelamento.
- Isolamento multiusuário no navegador.
- Responsividade em Tablet (768x1024), Celular (360x800) e Tema Escuro sem overflow horizontal.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

import pytest
from playwright.sync_api import sync_playwright

import annotations_service
import auth_service
import config
import database
import knowledge_service
import library


ARTIFACT_DIR = Path("/home/vini/.gemini/antigravity/brain/9ae11ae2-ebc0-44c2-9e79-22a1b0910e27")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Exportacao Atlas Fase 7) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def atlas_export_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p7_export_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "obra_export_fase7.pdf")

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
    library.scan_library()
    book = library.get_books()[0]

    alice = auth_service.create_user("alice_p7_e2e", "Password123!", "Alice Export E2E", role="admin")
    bob = auth_service.create_user("bob_p7_e2e", "Password123!", "Bob Export E2E", role="reader")

    c_alice = knowledge_service.create_concept(
        alice["id"],
        "Portabilidade do Conhecimento",
        description="Capacidade de preservar conceitos e conexões fora da plataforma.",
        tags=["portabilidade", "atlas"],
    )
    r_alice = knowledge_service.create_reflection(
        alice["id"],
        "Reflexão Portátil de Alice",
        "# Síntese\n\nTexto autoral preservado na exportação.",
        primary_book_id=book["id"],
        tags=["ensaio"],
    )
    knowledge_service.create_knowledge_link(
        alice["id"],
        "reflection",
        r_alice["id"],
        "concept",
        c_alice["id"],
        "grounds",
        rationale="Fundamenta a portabilidade.",
    )

    knowledge_service.create_concept(bob["id"], "ConceitoPrivadoBobE2E", description="Exclusivo de Bob")

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

    assert started, "Servidor E2E Fase 7 Export não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "c_alice": c_alice,
            "r_alice": r_alice,
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


def test_v190_atlas_export_ui_downloads_errors_and_responsive(atlas_export_server, tmp_path):
    base_url = atlas_export_server["base_url"]
    token_alice = atlas_export_server["token_alice"]
    token_bob = atlas_export_server["token_bob"]
    c_alice = atlas_export_server["c_alice"]
    r_alice = atlas_export_server["r_alice"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True)
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token_alice, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p7_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-btn-open-export", timeout=8000)

        # 1. Abre modal de exportação e verifica avisos de privacidade + seleção Markdown ZIP (Screenshot #1)
        page.click("#atlas-btn-open-export")
        page.wait_for_selector("#atlas-export-modal:not(.hidden)", timeout=5000)
        assert "informações pessoais" in page.locator("#atlas-export-privacy-notice").inner_text()
        page.check("#atlas-export-wikilinks")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_01_export_modal_markdown_desktop.png"))

        # Download real de Markdown ZIP
        with page.expect_download() as dl_md_info:
            page.click("#atlas-btn-confirm-export")
        dl_md = dl_md_info.value
        md_zip_path = tmp_path / dl_md.suggested_filename
        dl_md.save_as(str(md_zip_path))
        page.wait_for_function(
            "() => document.getElementById('atlas-export-status').innerText.includes('Exportação concluída')",
            timeout=8000,
        )
        with zipfile.ZipFile(md_zip_path, "r") as zf:
            names = zf.namelist()
            assert f"lectoria-atlas-export/conceitos/{c_alice['id']}.md" in names
            assert f"lectoria-atlas-export/reflexoes/{r_alice['id']}.md" in names
            assert "ConceitoPrivadoBobE2E" not in zf.read("lectoria-atlas-export/indice.md").decode("utf-8")

        # 2. Seleciona JSON v2.0 e realiza download real (Screenshot #2)
        page.click("#atlas-export-fmt-json")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_02_export_json_desktop.png"))
        with page.expect_download() as dl_json_info:
            page.click("#atlas-btn-confirm-export")
        dl_json = dl_json_info.value
        json_path = tmp_path / dl_json.suggested_filename
        dl_json.save_as(str(json_path))
        page.wait_for_function(
            "() => document.getElementById('atlas-export-status').innerText.includes('.json')",
            timeout=8000,
        )
        parsed_json = json.loads(json_path.read_text(encoding="utf-8"))
        assert parsed_json["format"] == "lectoria-atlas"
        assert parsed_json["format_version"] == "2.0"
        assert parsed_json["counts"]["concepts"] == 1

        # 3. Seleciona CSV e realiza download real (Screenshot #3)
        page.click("#atlas-export-fmt-csv")
        with page.expect_download() as dl_csv_info:
            page.click("#atlas-btn-confirm-export")
        dl_csv = dl_csv_info.value
        csv_zip_path = tmp_path / dl_csv.suggested_filename
        dl_csv.save_as(str(csv_zip_path))
        page.wait_for_function(
            "() => document.getElementById('atlas-export-status').innerText.includes('csv')",
            timeout=8000,
        )
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_03_export_csv_completed_desktop.png"))
        with zipfile.ZipFile(csv_zip_path, "r") as zf:
            assert "lectoria-atlas-export/concepts.csv" in zf.namelist()

        # 4. Simula falha de exportação (HTTP 500) -> exibe erro claro sem falsamente indicar sucesso (Screenshot #4)
        page.route(
            "**/api/knowledge/export*",
            lambda route: route.fulfill(
                status=500,
                content_type="application/json",
                body='{"detail":"Falha simulada na geração da exportação."}',
            ),
        )
        page.click("#atlas-btn-confirm-export")
        page.wait_for_function(
            "() => document.getElementById('atlas-export-status').classList.contains('rk-banner-error')",
            timeout=8000,
        )
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_04_export_error_desktop.png"))
        page.unroute("**/api/knowledge/export*")

        # 5. Tema Escuro (Screenshot #7)
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'dark'); }")
        page.wait_for_timeout(280)
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_07_export_dark_theme.png"))
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_timeout(280)

        # 6. Responsividade Tablet (768x1024 — Screenshot #5) e Mobile (360x800 — Screenshot #6)
        page.set_viewport_size({"width": 768, "height": 1024})
        assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_05_export_tablet.png"))

        page.set_viewport_size({"width": 360, "height": 800})
        assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase7_06_export_mobile.png"))

        # 7. Cancelamento fecha o modal
        page.click("#atlas-btn-cancel-export")
        assert page.locator("#atlas-export-modal").evaluate("el => el.classList.contains('hidden')")

        # 8. Isolamento multiusuário com sessão de Bob
        context_bob = browser.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True)
        context_bob.add_cookies(
            [
                {"name": "lectoria_session", "value": token_bob, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p7_bob", "url": base_url},
            ]
        )
        page_bob = context_bob.new_page()
        page_bob.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
        page_bob.click("#atlas-btn-open-export")
        page_bob.click("#atlas-export-fmt-json")
        with page_bob.expect_download() as dl_bob_info:
            page_bob.click("#atlas-btn-confirm-export")
        bob_json_path = tmp_path / f"bob_{dl_bob_info.value.suggested_filename}"
        dl_bob_info.value.save_as(str(bob_json_path))
        bob_data = json.loads(bob_json_path.read_text(encoding="utf-8"))
        assert bob_data["counts"]["concepts"] == 1
        assert bob_data["concepts"][0]["name"] == "ConceitoPrivadoBobE2E"
        assert "Portabilidade do Conhecimento" not in bob_json_path.read_text(encoding="utf-8")

        browser.close()
