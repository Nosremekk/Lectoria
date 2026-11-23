"""
Suíte de Testes E2E (Playwright) — Fase 6 da Lectoria v1.9.0 (`tests/e2e/test_v190_atlas_graph.py`).
Valida o Mapa Local de Conhecimento SVG e a Alternativa Textual Acessível (`#atlas-subtab-graph`, `#atlas-pane-graph`):
- Mapa de 1 salto (`depth=1`) e 2 saltos (`depth=2`).
- Mapa com múltiplas relações semânticas entre diferentes tipos de nós (`[C]`, `[R]`, `[L]`, `[D]`, `[N]`).
- Painel lateral sob demanda (`#atlas-graph-detail-panel`) ao clicar ou pressionar Enter em nós e arestas.
- Recentralização do mapa em um nó vizinho (`#atlas-graph-btn-recenter`).
- Alternância entre Mapa Visual SVG (`#atlas-graph-view-svg`) e Lista Textual Acessível (`#atlas-graph-view-list`, `#atlas-graph-text-view`).
- Aviso explícito de truncamento (`#atlas-graph-truncation-notice`) quando um nó possui mais de 40 vizinhos.
- Responsividade em Tablet (`768x1024`), Celular (`360x800`) e Tema Escuro sem overflow horizontal.
- Captura das evidências visuais 5, 6, 7, 8, 10, 11, 12, 13 e 14 da Seção 41.
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
    stream.set_data(b"BT /F1 16 Tf 72 740 Td (Mapa Local de Conhecimento Fase 6) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest_path, "wb") as f:
        writer.write(f)


@pytest.fixture(scope="module")
def atlas_graph_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v190_p6_graph_e2e")
    lib_dir = root / "books"
    data_dir = root / "data"
    covers_dir = data_dir / "covers"
    backups_dir = root / "backups"
    lib_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    _create_text_pdf(lib_dir / "obra_mapa_fase6.pdf")

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

    alice = auth_service.create_user("alice_p6_e2e_graph", "Password123!", "Alice Graph E2E", role="admin")
    uid = alice["id"]

    # Grafo principal com múltiplos tipos de entidades e relações em 1 e 2 saltos
    c_center = knowledge_service.create_concept(
        uid,
        "Filosofia da Técnica",
        description="Núcleo conceitual sobre técnica, ciência e cultura moderna.",
        tags=["técnica", "filosofia"],
        color="#d4a853",
    )
    c_hop1_a = knowledge_service.create_concept(
        uid,
        "Racionalidade Instrumental",
        description="Cálculo de meios eficientes sem deliberação sobre fins.",
        color="#4a90e2",
    )
    c_hop1_b = knowledge_service.create_concept(
        uid,
        "Desencantamento do Mundo",
        description="Racionalização progressiva na modernidade segundo Weber.",
        color="#50b86c",
    )
    ref_hop1 = knowledge_service.create_reflection(
        uid,
        "Síntese: Técnica e Modernidade",
        "# Síntese\n\nDiálogo entre Weber, Heidegger e Ellul.",
        primary_book_id=book["id"],
        tags=["ensaio"],
    )
    hl_hop1 = annotations_service.create_highlight(
        book_id=book["id"],
        user_id=uid,
        page=1,
        selected_text="A técnica moderna transforma a relação entre razão e natureza.",
        rects=[{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.04}],
        chapter_label="Introdução",
        visibility="private",
    )

    # Nós de 2º salto (ligados a c_hop1_a e ref_hop1)
    c_hop2 = knowledge_service.create_concept(
        uid,
        "Autonomia Tecnológica",
        description="Autoaceleração do sistema técnico contemporâneo.",
        color="#e06c75",
    )
    nt_hop2 = annotations_service.create_note(
        book_id=book["id"],
        user_id=uid,
        page=1,
        content="Nota de segundo salto sobre crítica da razão instrumental.",
        chapter_label="Introdução",
        visibility="private",
    )

    # Conexões de 1º salto
    knowledge_service.create_knowledge_link(
        uid, "concept", c_center["id"], "concept", c_hop1_a["id"], "develops", "Desenvolve a crítica da razão instrumental."
    )
    knowledge_service.create_knowledge_link(
        uid, "concept", c_center["id"], "concept", c_hop1_b["id"], "complements", "Complementa a tese weberiana."
    )
    knowledge_service.create_knowledge_link(
        uid, "reflection", ref_hop1["id"], "concept", c_center["id"], "grounds", "A reflexão fundamenta o conceito central."
    )
    knowledge_service.create_knowledge_link(
        uid, "highlight", hl_hop1["id"], "concept", c_center["id"], "exemplifies", "Trecho que exemplifica a tese central."
    )
    # Conexão múltipla paralela entre c_center e c_hop1_a
    knowledge_service.create_knowledge_link(
        uid, "concept", c_hop1_a["id"], "concept", c_center["id"], "questions", "Questiona os limites éticos da técnica."
    )

    # Conexões de 2º salto
    knowledge_service.create_knowledge_link(
        uid, "concept", c_hop1_a["id"], "concept", c_hop2["id"], "grounds", "Razão instrumental fundamenta autonomia tecnológica."
    )
    knowledge_service.create_knowledge_link(
        uid, "note", nt_hop2["id"], "concept", c_hop1_a["id"], "references", "Nota analisa racionalidade instrumental."
    )
    knowledge_service.create_knowledge_link(
        uid, "reflection", ref_hop1["id"], "book", book["id"], "references", "Obra base da reflexão."
    )

    # Nó hub denso (> 45 conexões) para testar truncamento visual no mapa (max_nodes padrão = 40)
    c_hub = knowledge_service.create_concept(
        uid,
        "Hub Enciclopédico Denso",
        description="Conceito conectado a 48 subconceitos para validar o aviso de truncamento do subgrafo.",
    )
    now_iso = "2026-11-21 15:00:00"
    with database.get_db() as conn:
        cur = conn.cursor()
        for idx in range(48):
            sc_id = f"hub_sub_{idx:02d}"
            cur.execute(
                """
                INSERT INTO concepts (id, user_id, name, normalized_name, description, tags_json, color, is_archived, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'Subconceito do hub', '[]', '#d4a853', 0, ?, ?);
                """,
                (sc_id, uid, f"Ramo Temático {idx:02d}", f"ramo tematico {idx:02d}", now_iso, now_iso),
            )
            cur.execute(
                """
                INSERT INTO knowledge_links (id, user_id, source_type, source_id, target_type, target_id, relation_type, rationale, created_at, updated_at)
                VALUES (?, ?, 'concept', ?, 'concept', ?, 'relates_to', '', ?, ?);
                """,
                (f"hub_lnk_{idx:02d}", uid, c_hub["id"], sc_id, now_iso, now_iso),
            )

    token_alice = auth_service.create_session(uid)
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

    assert started, "Servidor E2E Fase 6 Graph não iniciou a tempo."

    try:
        yield {
            "base_url": base_url,
            "token_alice": token_alice,
            "c_center": c_center,
            "c_hop1_a": c_hop1_a,
            "c_hub": c_hub,
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


def test_v190_atlas_local_knowledge_graph_svg_and_accessible_list(atlas_graph_server):
    base_url = atlas_graph_server["base_url"]
    token = atlas_graph_server["token_alice"]
    c_center = atlas_graph_server["c_center"]
    c_hop1_a = atlas_graph_server["c_hop1_a"]
    c_hub = atlas_graph_server["c_hub"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 860})
        context.add_cookies(
            [
                {"name": "lectoria_session", "value": token, "url": base_url},
                {"name": "lectoria_csrf", "value": "csrf_p6_graph_e2e", "url": base_url},
            ]
        )
        page = context.new_page()

        # 1. Abre o mapa centrado em c_center com profundidade de 1 nível (Screenshot #5 e #7)
        page.goto(
            f"{base_url}/?tab=atlas&subtab=graph&type=concept&id={c_center['id']}",
            wait_until="networkidle",
        )
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-graph-svg", timeout=8000)

        # Em 1 salto: c_center + 4 vizinhos diretos = 5 nós e 5 conexões (incluindo relação múltipla entre c_center e c_hop1_a)
        assert page.locator("#atlas-graph-svg .atlas-graph-node").count() == 5
        assert page.locator("#atlas-graph-svg .atlas-graph-edge-group").count() == 5
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_05_graph_1hop_desktop.png"))

        # Seleciona uma aresta para evidenciar múltiplas relações entre os nós (Screenshot #7)
        page.locator("#atlas-graph-svg .atlas-graph-edge-group").first.click()
        page.wait_for_function(
            "() => document.getElementById('atlas-graph-detail-panel').innerText.includes('Detalhes da conexão')",
            timeout=5000,
        )
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_07_graph_multiple_relations_desktop.png"))

        # 2. Muda profundidade para 2 níveis (Screenshot #6)
        page.select_option("#atlas-graph-depth-select", "2")
        page.wait_for_function(
            "() => document.querySelectorAll('#atlas-graph-svg .atlas-graph-node').length === 8",
            timeout=8000,
        )
        # Verifica presença de arestas tracejadas de 2º salto
        assert page.locator("#atlas-graph-svg .atlas-edge-dashed").count() >= 2
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_06_graph_2hop_desktop.png"))

        # 3. Clica em um nó vizinho (c_hop1_a) para exibir detalhes no painel lateral (Screenshot #11)
        page.locator(f"#atlas-graph-svg .atlas-graph-node[data-node-id='{c_hop1_a['id']}']").click()
        page.wait_for_function(
            "() => document.getElementById('atlas-graph-detail-panel').innerText.includes('Racionalidade Instrumental')",
            timeout=5000,
        )
        page.locator("#atlas-graph-detail-panel").screenshot(
            path=str(ARTIFACT_DIR / "v190_phase6_11_graph_detail_panel_desktop.png")
        )

        # 4. Alterna para a Lista Textual Acessível obrigatória (Screenshot #10)
        page.click("#atlas-graph-view-list")
        page.wait_for_selector("#atlas-graph-text-view", timeout=5000)
        assert page.locator("#atlas-graph-text-list .atlas-graph-text-item").count() == 8
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_10_graph_accessible_list_desktop.png"))

        # Volta para o modo SVG e testa recentralização no nó selecionado
        page.click("#atlas-graph-view-svg")
        page.wait_for_selector("#atlas-graph-svg", timeout=5000)
        page.locator(f"#atlas-graph-svg .atlas-graph-node[data-node-id='{c_hop1_a['id']}']").click()
        page.click("#atlas-graph-btn-recenter")
        page.wait_for_function(
            "() => { const el = document.querySelector('.atlas-graph-node.is-center'); return Boolean(el && el.getAttribute('data-node-id') === '"
            + c_hop1_a["id"]
            + "'); }",
            timeout=8000,
        )

        # 5. Tema escuro no Desktop (Screenshot #14)
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'dark'); }")
        page.wait_for_timeout(280)
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_14_graph_dark_theme_desktop.png"))
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_timeout(280)

        # 6. Mapa truncado em nó hub denso (> 40 nós — Screenshot #8)
        page.goto(
            f"{base_url}/?tab=atlas&subtab=graph&type=concept&id={c_hub['id']}",
            wait_until="networkidle",
        )
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-graph-truncation-notice", timeout=8000)
        assert page.locator("#atlas-graph-svg .atlas-graph-node").count() == 40
        assert "40/40 nós" in page.locator("#atlas-graph-truncation-notice").inner_text()
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_08_graph_truncated_desktop.png"))

        # 7. Responsividade em Tablet (768x1024 — Screenshot #12) e Mobile (360x800 — Screenshot #13)
        page.goto(
            f"{base_url}/?tab=atlas&subtab=graph&type=concept&id={c_center['id']}",
            wait_until="networkidle",
        )
        page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'light'); }")
        page.wait_for_selector("#atlas-graph-svg", timeout=8000)

        page.set_viewport_size({"width": 768, "height": 1024})
        overflow_tablet = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
        assert not overflow_tablet
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_12_graph_tablet.png"))

        page.set_viewport_size({"width": 360, "height": 800})
        overflow_mobile = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 1")
        assert not overflow_mobile
        page.locator("#atlas-graph-svg").scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACT_DIR / "v190_phase6_13_graph_mobile.png"))

        browser.close()
