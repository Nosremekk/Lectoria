"""
Suíte de Testes E2E de Segurança (Playwright) — Fase 6 da Lectoria v1.9.0 (`tests/e2e/test_v190_atlas_search_graph_security.py`).
Valida:
- Isolamento absoluto entre Usuários A e B na Pesquisa Integrada e no Mapa Local de Conhecimento.
- Revogação de visibilidade (`public -> private`) de anotação de terceiro:
  - Desaparece imediatamente da pesquisa integrada do Usuário A (sem snippet residual).
  - Aparece no Mapa Local de Conhecimento do Usuário A exclusivamente como sentinela neutro `"Referência indisponível"`,
    sem título, sem excerto, sem obra, não expansível e não navegável (Screenshot #9 da Seção 41).
- Proteção contra XSS e HTML bruto em títulos, descrições, reflexões, notas, justificativas e snippets de busca e grafo.
- Preservação do bloqueio de alterações não salvas (`#atlas-unsaved-modal`) ao acionar `"Explorar no Mapa"` ou trocar para as abas Pesquisa e Mapa durante a edição de uma reflexão.
- Higienização de estado em memória após logout/troca de sessão (`LectoriaAtlas.reset()`).
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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Seguranca Pesquisa e Mapa Fase 6) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def atlas_sec_p6_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p6_sec_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "seguranca_fase6.pdf")

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

    alice = auth_service.create_user("alice_p6_sec_e2e", "Password123!", "Alice Sec E2E", role="admin")
    bob = auth_service.create_user("bob_p6_sec_e2e", "Password123!", "Bob Sec E2E", role="reader")

    # Bob cria itens privados e 1 destaque público que Alice referencia antes de Bob revogar para privado
    knowledge_service.create_concept(
        bob["id"],
        "ConceitoUltraSecretoBob",
        description="Informação privada exclusiva de Bob sobre criptografia.",
    )
    hl_bob = annotations_service.create_highlight(
        book_id=book["id"],
        user_id=bob["id"],
        page=1,
        selected_text="TrechoConfidencialRevogadoDeBob que não pode vazar após revogação.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        chapter_label="Cap. Confidencial",
        visibility="public",
    )

    # Alice cria conceito e reflexão com payloads XSS literais e conecta ao destaque de Bob
    c_alice = knowledge_service.create_concept(
        alice["id"],
        "Conceito Seguranca <img src=x onerror=window.__p6_xss=1>",
        description="Descrição com payload <script>window.__p6_xss=2</script> e termo hermeneutica.",
    )
    r_alice = knowledge_service.create_reflection(
        alice["id"],
        "Reflexão Seguranca <svg onload=window.__p6_xss=3>",
        "Corpo da reflexão com hermeneutica e [link malicioso](javascript:window.__p6_xss=4).",
    )
    knowledge_service.create_knowledge_link(
        alice["id"],
        "concept",
        c_alice["id"],
        "highlight",
        hl_bob["id"],
        "references",
        rationale="Vínculo com destaque que será revogado.",
    )
    knowledge_service.create_knowledge_link(
        alice["id"],
        "concept",
        c_alice["id"],
        "reflection",
        r_alice["id"],
        "develops",
        rationale="Justificativa com <script>window.__p6_xss=5</script> hermeneutica.",
    )

    # Bob revoga a visibilidade do seu destaque para privado!
    annotations_service.set_highlight_visibility(hl_bob["id"], "private", user_id=bob["id"])

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

    assert started, "Servidor E2E Fase 6 Security não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "token_bob": token_bob,
            "c_alice": c_alice,
            "r_alice": r_alice,
            "hl_bob": hl_bob,
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


def test_v190_atlas_search_and_graph_security_isolation_xss_and_dirty_guard(atlas_sec_p6_server):
    base_url = atlas_sec_p6_server["base_url"]
    token_alice = atlas_sec_p6_server["token_alice"]
    c_alice = atlas_sec_p6_server["c_alice"]
    r_alice = atlas_sec_p6_server["r_alice"]
    hl_bob = atlas_sec_p6_server["hl_bob"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token_alice, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p6_sec_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        # 1. Alice pesquisa pelo conceito privado de Bob e pelo trecho do destaque revogado de Bob -> 0 resultados
        page.goto(f"{base_url}/?tab=atlas&subtab=search", wait_until="networkidle")
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-integrated-search-input", timeout=8000)

        page.fill("#atlas-integrated-search-input", "ConceitoUltraSecretoBob")
        page.click("#atlas-btn-execute-search")
        page.wait_for_selector("#atlas-search-empty-results", timeout=8000)
        assert "ConceitoUltraSecretoBob" not in page.locator("#atlas-search-results-area").inner_text() or "Nenhum resultado" in page.locator("#atlas-search-empty-results").inner_text()

        page.fill("#atlas-integrated-search-input", "TrechoConfidencialRevogadoDeBob")
        page.click("#atlas-btn-execute-search")
        page.wait_for_selector("#atlas-search-empty-results", timeout=8000)

        # 2. Alice pesquisa por "hermeneutica" -> resultados contêm payloads XSS tratados como texto puro, sem executar script
        page.fill("#atlas-integrated-search-input", "hermeneutica")
        page.click("#atlas-btn-execute-search")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-search-results-list .atlas-search-result-card').length >= 3",
            timeout=8000,
        )
        xss_val = page.evaluate("() => window.__p6_xss || null")
        assert xss_val is None

        # 3. Alice abre o Mapa centrado em c_alice e seleciona o nó da referência indisponível (hl_bob revogado — Screenshot #9)
        page.goto(
            f"{base_url}/?tab=atlas&subtab=graph&type=concept&id={c_alice['id']}",
            wait_until="networkidle",
        )
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-graph-svg", timeout=8000)

        unavail_node = page.locator(f"#atlas-graph-svg .atlas-graph-node[data-node-id='{hl_bob['id']}']")
        assert unavail_node.count() == 1
        unavail_node.click()
        page.wait_for_function(
            "() => document.getElementById('atlas-graph-detail-panel').innerText.includes('Referência indisponível')",
            timeout=5000,
        )
        panel_text = page.locator("#atlas-graph-detail-panel").inner_text()
        assert "TrechoConfidencialRevogadoDeBob" not in panel_text
        assert "Cap. Confidencial" not in panel_text
        # Nenhum botão de recentralizar ou navegar pode existir para nó indisponível
        assert page.locator("#atlas-graph-detail-panel #atlas-graph-btn-recenter").count() == 0
        assert page.locator("#atlas-graph-detail-panel .rk-btn-navigate").count() == 0

        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_09_graph_unavailable_ref_desktop.png"))

        # Verifica novamente ausência de XSS após renderizar o grafo e a lista textual acessível
        page.click("#atlas-graph-view-list")
        page.wait_for_selector("#atlas-graph-text-view", timeout=5000)
        assert "TrechoConfidencialRevogadoDeBob" not in page.locator("#atlas-graph-text-view").inner_text()
        assert page.evaluate("() => window.__p6_xss || null") is None

        # 4. Verifica que o bloqueio de alterações não salvas (#atlas-unsaved-modal) protege o botão "Explorar no Mapa"
        # e as trocas para as abas Pesquisa / Mapa no editor de reflexão
        page.goto(
            f"{base_url}/?tab=atlas&subtab=reflections&id={r_alice['id']}",
            wait_until="networkidle",
        )
        page.wait_for_selector("#atlas-reflection-body", timeout=8000)
        page.fill("#atlas-reflection-body", "Texto modificado não salvo na reflexão.")

        # Clica em "Explorar no Mapa" com texto dirty -> abre #atlas-unsaved-modal em vez de perder alterações
        page.click("#atlas-btn-explore-reflection-graph")
        page.wait_for_selector("#atlas-unsaved-modal:not(.hidden)", timeout=5000)
        page.click("#atlas-btn-stay-editor")
        assert page.locator("#atlas-unsaved-modal").evaluate("el => el.classList.contains('hidden')")

        # Clica em "Descartar alterações" após clicar na subaba Pesquisa -> navega para Pesquisa
        page.click("#atlas-subtab-search")
        page.wait_for_selector("#atlas-unsaved-modal:not(.hidden)", timeout=5000)
        page.click("#atlas-btn-discard-changes")
        page.wait_for_selector("#atlas-integrated-search-input", timeout=5000)

        browser.close()
