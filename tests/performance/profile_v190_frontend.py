#!/usr/bin/env python3
"""
Perfilador Frontend Playwright (Chromium headless) para a Fase 8 (Lectoria v1.9.0).

Mede em dados 100% sintéticos (diretório temporário + servidor Uvicorn local):
1. Carga inicial de `/` (aba padrão Descoberta): tempo até utilizável, chamadas /api/*,
   requisições duplicadas, bytes transferidos, nós DOM, heap JS e erros de console.
2. Aba Biblioteca (`#tab-nav-library`): nós DOM (chunking 36), requisições HTTP, latência de filtro local.
3. Modal Detalhes do Livro + subaba Conhecimento (carregamento sob demanda).
4. Leitor PDF (`/reader/{pdf_id}`): abertura, HTTP 206 Range requests, Caderno de Estudos (`#annotations-drawer`).
5. Leitor EPUB (`/reader/{epub_id}`): abertura, renderização EPUB.js, heap JS.
6. Atlas (`#tab-nav-atlas`): Visão Geral, Conceitos, Editor de Reflexões, Pesquisa FTS5, Mapa SVG (1 e 2 saltos),
   Lista Textual Acessível e Modal de Exportação (`#atlas-export-modal`).
7. Modo opcional `--capture-screenshots <dir>` para gerar as 11 capturas de validação visual.

Uso:
    ./.venv/bin/python tests/performance/profile_v190_frontend.py --output-json /tmp/fe.json
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _create_valid_pdf(dest: Path, label: str = "Lectoria Frontend Profiling Page") -> None:
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
    for pg in range(3):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 15 Tf 72 740 Td ({label} p{pg + 1}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with open(dest, "wb") as fp:
        writer.write(fp)


def _create_valid_epub(dest: Path, title: str = "A Questão da Técnica") -> None:
    container_xml = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles>"
        '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>Martin Heidegger</dc:creator><dc:language>pt-BR</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>"""
    nav_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><title>Sumário</title></head><body><nav epub:type="toc" id="toc">'
        '<ol><li><a href="ch1.xhtml">Capítulo 1</a></li></ol></nav></body></html>'
    )
    ch1_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Capítulo 1</title></head>'
        '<body><h1>Capítulo 1</h1><p id="p1">A técnica moderna como modo de desvelamento e organização.</p></body></html>'
    )
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml)
        zf.writestr("OEBPS/ch1.xhtml", ch1_xhtml)


def _seed_frontend_env(tmp_dir: Path) -> Dict[str, Any]:
    data_dir = tmp_dir / "data"
    books_dir = tmp_dir / "books"
    backups_dir = tmp_dir / "backups"
    covers_dir = data_dir / "covers"
    for d in (data_dir, books_dir, backups_dir, covers_dir):
        d.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    os.environ["LECTORIA_DATA_DIR"] = str(data_dir)
    os.environ["LECTORIA_DB_PATH"] = str(db_path)
    os.environ["LECTORIA_LIBRARY_DIR"] = str(books_dir)
    os.environ["LECTORIA_BACKUPS_DIR"] = str(backups_dir)
    os.environ["LECTORIA_COVERS_DIR"] = str(covers_dir)

    import config
    config.DATA_DIR = data_dir
    config.DB_PATH = db_path
    config.LIBRARY_DIR = books_dir
    config.BACKUPS_DIR = backups_dir
    config.COVERS_DIR = covers_dir

    import annotations_service
    import auth_service
    import database
    import knowledge_service

    database.init_db(db_path)
    alice = auth_service.create_user("fe_alice", "FePass123!", display_name="Alice Profiler", role="admin")
    uid = alice["id"]
    token = auth_service.create_session(uid)

    pdf_main = books_dir / "ellul_tecnica.pdf"
    epub_main = books_dir / "heidegger_tecnica.epub"
    _create_valid_pdf(pdf_main, "A Tecnica e o Desafio do Seculo")
    _create_valid_epub(epub_main, "A Questão da Técnica")
    pdf_bytes = pdf_main.read_bytes()

    with database.get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Acervo Profiling', ?, 1);",
            (str(books_dir),),
        )
        now_iso = knowledge_service._now_iso()
        cur.execute(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, page_count, format, is_missing, description)
            VALUES
            ('fe_pdf_001', 1, 'A Técnica e o Desafio do Século', 'Jacques Ellul', 'ellul_tecnica.pdf', 'ellul_tecnica.pdf', ?, 3, 'pdf', 0, 'Obra clássica sobre filosofia da técnica.'),
            ('fe_epub_001', 1, 'A Questão da Técnica', 'Martin Heidegger', 'heidegger_tecnica.epub', 'heidegger_tecnica.epub', ?, 0, 'epub', 0, 'Ensaio filosófico sobre Gestell e essência da técnica.');
            """,
            (pdf_main.stat().st_size, epub_main.stat().st_size),
        )
        extra_books = []
        extra_fts = []
        for i in range(198):
            bid = f"fe_book_{i:04d}"
            fname = f"{bid}.pdf"
            (books_dir / fname).write_bytes(pdf_bytes)
            title = f"Obra Bibliográfica {i:04d}"
            author = f"Autor {i % 15:02d}"
            extra_books.append((
                bid, 1, title, author, fname, fname, len(pdf_bytes), 100, "pdf", 0,
                "Sinopse sintética para perfilamento de interface.", now_iso, now_iso,
            ))
            extra_fts.append((bid, title, author, "Sinopse sintética para perfilamento de interface.", "", "Filosofia"))
        cur.executemany(
            """
            INSERT INTO books (id, source_id, title, author, filename, rel_path, filesize, page_count, format, is_missing, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            extra_books,
        )
        cur.executemany(
            "INSERT INTO books_fts (book_id, title, author, description, publisher, subjects) VALUES (?, ?, ?, ?, ?, ?);",
            extra_fts,
        )
        conn.commit()

    # Conceitos, reflexões, anotações e conexões no Atlas
    c1 = knowledge_service.create_concept(uid, "Autonomia da Técnica", description="A técnica como sistema fechado voltado à eficiência máxima.", tags=["filosofia", "técnica"])
    c2 = knowledge_service.create_concept(uid, "Gestell", description="Com-posição que desvela o real como reserva disponível.", tags=["heidegger"])
    c3 = knowledge_service.create_concept(uid, "Racionalidade Instrumental", description="Meios sobrepostos aos fins humanos.", tags=["ética"])
    r1 = knowledge_service.create_reflection(
        uid,
        "Ellul e Heidegger em Diálogo Crítico",
        "# Tese Central\n\nA **autonomia técnica** dialoga com o conceito de *Gestell*.\n\n> A essência da técnica não é nada de técnico.\n\n- Eficiência instrumental\n- Desvelamento moderno",
        primary_book_id="fe_pdf_001",
        tags=["ensaio", "técnica"],
        is_pinned=True,
    )
    hl1 = annotations_service.create_highlight(
        book_id="fe_pdf_001",
        user_id=uid,
        page=1,
        selected_text="A técnica tornou-se um meio autônomo com leis próprias.",
        rects=[{"x": 0.1, "y": 0.12, "w": 0.5, "h": 0.04}],
        chapter_label="Cap. 1",
    )
    nt1 = annotations_service.create_note(
        book_id="fe_epub_001",
        user_id=uid,
        page=1,
        content="Nota comparativa sobre técnica e Gestell no EPUB.",
        epub_cfi="epubcfi(/6/2[ch1]!/4/2[p1]/1:0)",
        chapter_label="Capítulo 1",
    )
    knowledge_service.create_knowledge_link(uid, "reflection", r1["id"], "concept", c1["id"], "grounds", "A reflexão fundamenta o conceito central.")
    knowledge_service.create_knowledge_link(uid, "concept", c1["id"], "concept", c2["id"], "complements", "Paralelo direto entre autonomia e Gestell.")
    knowledge_service.create_knowledge_link(uid, "concept", c2["id"], "concept", c3["id"], "develops", "Desenvolve o conceito ético em 2º salto.")
    knowledge_service.create_knowledge_link(uid, "highlight", hl1["id"], "concept", c1["id"], "exemplifies", "Trecho exemplar do PDF.")
    knowledge_service.create_knowledge_link(uid, "note", nt1["id"], "concept", c2["id"], "references", "Nota vinculada no EPUB.")

    return {
        "env": {
            "LECTORIA_DATA_DIR": str(data_dir),
            "LECTORIA_DB_PATH": str(db_path),
            "LECTORIA_LIBRARY_DIR": str(books_dir),
            "LECTORIA_BACKUPS_DIR": str(backups_dir),
            "LECTORIA_COVERS_DIR": str(covers_dir),
        },
        "token": token,
        "csrf": "fe_prof_csrf_tok",
        "pdf_book_id": "fe_pdf_001",
        "epub_book_id": "fe_epub_001",
        "concept_id": c1["id"],
        "reflection_id": r1["id"],
    }


def _page_metrics(page) -> Dict[str, Any]:
    return page.evaluate(
        """() => {
            const mem = performance.memory ? {
                js_heap_used_mb: Math.round((performance.memory.usedJSHeapSize / 1048576) * 100) / 100,
                js_heap_total_mb: Math.round((performance.memory.totalJSHeapSize / 1048576) * 100) / 100,
                js_heap_limit_mb: Math.round((performance.memory.jsHeapSizeLimit / 1048576) * 100) / 100
            } : null;
            return {
                dom_nodes: document.querySelectorAll('*').length,
                scroll_width: document.documentElement.scrollWidth,
                client_width: document.documentElement.clientWidth,
                overflow_x: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                memory: mem
            };
        }"""
    )


def run_frontend_profile(capture_screenshots_dir: Optional[str] = None) -> Dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="lectoria_p8_fe_"))
    shot_dir = Path(capture_screenshots_dir) if capture_screenshots_dir else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)

    proc = None
    try:
        seeded = _seed_frontend_env(tmp)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        env = os.environ.copy()
        env.update(seeded["env"])
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "main:app",
                "--host", "127.0.0.1", "--port", str(port),
                "--workers", "1", "--log-level", "warning",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.1)

        report: Dict[str, Any] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "viewport_desktop": {"width": 1366, "height": 860},
            "phases": {},
            "console_errors": [],
            "page_errors": [],
            "screenshots": [],
        }

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--enable-precise-memory-info"],
            )
            context = browser.new_context(viewport={"width": 1366, "height": 860})
            context.add_cookies([
                {"name": "lectoria_session", "value": seeded["token"], "url": base_url},
                {"name": "lectoria_csrf", "value": seeded["csrf"], "url": base_url},
            ])
            page = context.new_page()

            api_events: List[Dict[str, Any]] = []
            range_events: List[int] = []
            recording = True

            def on_response(resp):
                if not recording:
                    return
                if "/api/" in resp.url:
                    path = resp.url.split(f":{port}")[-1]
                    is_cover = "/cover" in path
                    if "/file" in path:
                        range_events.append(resp.status)
                    sz = 0
                    if not is_cover and "/file" not in path:
                        try:
                            sz = len(resp.body())
                        except Exception:
                            sz = 0
                    api_events.append({"status": resp.status, "path": path, "bytes": sz, "is_cover": is_cover})

            page.on("response", on_response)
            page.on("console", lambda msg: report["console_errors"].append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: report["page_errors"].append(str(err)))

            def _summarize_api_window(events: List[Dict[str, Any]]) -> Dict[str, Any]:
                non_cover = [e for e in events if not e["is_cover"]]
                covers = [e for e in events if e["is_cover"]]
                counts: Dict[str, int] = {}
                for e in non_cover:
                    counts[e["path"]] = counts.get(e["path"], 0) + 1
                duplicates = {k: v for k, v in counts.items() if v > 1}
                return {
                    "api_calls_non_cover": len(non_cover),
                    "api_calls_cover": len(covers),
                    "non_cover_bytes": sum(e["bytes"] for e in non_cover),
                    "endpoints": [{"path": e["path"], "status": e["status"], "bytes": e["bytes"]} for e in non_cover],
                    "duplicate_endpoints": duplicates,
                }

            # 1. Carga inicial de `/` (aba padrão Descoberta)
            api_events.clear()
            t0 = time.perf_counter()
            page.goto(base_url + "/", wait_until="networkidle")
            page.wait_for_selector(".shelf-wrapper", timeout=10000)
            init_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            report["phases"]["1_initial_load_discovery"] = {
                "load_ms": init_ms,
                **_summarize_api_window(api_events),
                **_page_metrics(page),
            }

            # 2. Troca para aba Biblioteca (200 livros com chunking de 36)
            api_events.clear()
            t0 = time.perf_counter()
            page.click("#tab-nav-library")
            page.wait_for_selector("#books-container .book-card", timeout=10000)
            lib_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            cards_initial = page.locator("#books-container .book-card").count()

            # Mede latência de filtro local (digitar "Téc" no campo de busca)
            api_events.clear()
            t_flt0 = time.perf_counter()
            page.fill("#search-input", "Técnica")
            page.wait_for_function("() => document.querySelectorAll('#books-container .book-card').length <= 5", timeout=5000)
            flt_ms = round((time.perf_counter() - t_flt0) * 1000.0, 2)
            filter_api_calls = len([e for e in api_events if not e["is_cover"]])
            page.fill("#search-input", "")
            page.wait_for_function("() => document.querySelectorAll('#books-container .book-card').length >= 36", timeout=5000)
            if shot_dir:
                sp = shot_dir / "p8_01_library_desktop.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))

            report["phases"]["2_library_tab_and_local_filter"] = {
                "tab_open_ms": lib_ms,
                "rendered_cards_initial_chunk": cards_initial,
                "local_search_filter_ms_with_100ms_debounce": flt_ms,
                "api_calls_during_local_filter": filter_api_calls,
                **_page_metrics(page),
            }

            # 3. Modal Detalhes do Livro + subaba Conhecimento sob demanda
            api_events.clear()
            t0 = time.perf_counter()
            page.locator("#books-container .btn-book-details").first.click()
            page.wait_for_selector("#modal-book-details.active", timeout=8000)
            det_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            pre_knowledge_calls = [e["path"] for e in api_events if "/knowledge" in e["path"]]
            api_events.clear()
            t0k = time.perf_counter()
            page.click(".details-tab-btn[data-subtab='knowledge']")
            page.wait_for_selector("#details-subtab-knowledge", timeout=5000)
            page.wait_for_load_state("networkidle")
            det_know_ms = round((time.perf_counter() - t0k) * 1000.0, 2)
            report["phases"]["3_book_details_and_knowledge_subtab"] = {
                "open_modal_ms": det_ms,
                "knowledge_calls_before_clicking_subtab": len(pre_knowledge_calls),
                "knowledge_subtab_ms": det_know_ms,
                **_summarize_api_window(api_events),
            }

            # 4. Atlas: Visão Geral, Conceitos, Editor de Reflexões, Pesquisa, Mapa SVG/Lista Textual, Exportação
            api_events.clear()
            t0 = time.perf_counter()
            page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
            page.wait_for_selector("#atlas-pane-overview", timeout=10000)
            atlas_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_04_atlas_overview_dark.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            report["phases"]["4_atlas_overview"] = {
                "open_ms": atlas_ms,
                **_summarize_api_window(api_events),
                **_page_metrics(page),
            }

            # 4b. Editor de Reflexões
            api_events.clear()
            t0 = time.perf_counter()
            page.click("#atlas-subtab-reflections")
            page.wait_for_selector("#atlas-pane-reflections", timeout=8000)
            page.wait_for_load_state("networkidle")
            page.locator("#atlas-pane-reflections button, #atlas-pane-reflections .atlas-reflection-card").filter(has_text="Ellul e Heidegger").first.click()
            page.wait_for_selector("#atlas-reflection-title", timeout=8000)
            refl_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_07_atlas_reflections.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            report["phases"]["4b_atlas_reflection_editor"] = {
                "open_ms": refl_ms,
                **_summarize_api_window(api_events),
            }

            # 4c. Pesquisa Integrada FTS5
            api_events.clear()
            page.click("#atlas-subtab-search")
            page.wait_for_selector("#atlas-integrated-search-input", timeout=8000)
            page.fill("#atlas-integrated-search-input", "tecnica")
            t0 = time.perf_counter()
            page.click("#atlas-btn-execute-search")
            page.wait_for_selector("#atlas-search-results-list .atlas-search-result-card", timeout=8000)
            search_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_05_atlas_search.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            report["phases"]["4c_atlas_search"] = {
                "search_roundtrip_ms": search_ms,
                "results_rendered": page.locator("#atlas-search-results-list .atlas-search-result-card").count(),
                **_summarize_api_window(api_events),
            }

            # 4d. Mapa Local SVG (2 saltos) + Lista Textual + Modal de Exportação
            api_events.clear()
            t0 = time.perf_counter()
            page.goto(f"{base_url}/?tab=atlas&subtab=graph&type=concept&id={seeded['concept_id']}", wait_until="networkidle")
            page.wait_for_selector("#atlas-graph-svg .atlas-graph-node", timeout=10000)
            page.select_option("#atlas-graph-depth-select", "2")
            page.wait_for_function("() => document.querySelectorAll('#atlas-graph-svg .atlas-graph-node').length >= 4", timeout=8000)
            graph_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_06_atlas_graph.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))

            # Modal de exportação
            page.click("#atlas-btn-open-export")
            page.wait_for_selector("#atlas-export-modal:not(.hidden)", timeout=5000)
            if shot_dir:
                sp = shot_dir / "p8_08_atlas_export_modal.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            page.keyboard.press("Escape")

            report["phases"]["4d_atlas_graph"] = {
                "open_and_2hop_ms": graph_ms,
                "svg_nodes": page.locator("#atlas-graph-svg .atlas-graph-node").count(),
                **_summarize_api_window(api_events),
                **_page_metrics(page),
            }

            # 5. Leitor PDF + Caderno de Estudos
            api_events.clear()
            range_events.clear()
            t0 = time.perf_counter()
            page.goto(f"{base_url}/reader/{seeded['pdf_book_id']}", wait_until="networkidle")
            page.wait_for_selector("#btn-toggle-drawer", timeout=15000)
            pdf_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            api_events_before_nb = list(api_events)
            api_events.clear()
            t0nb = time.perf_counter()
            page.click("#btn-toggle-drawer")
            page.wait_for_selector("#annotations-drawer.active", timeout=8000)
            page.wait_for_load_state("networkidle")
            nb_ms = round((time.perf_counter() - t0nb) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_02_reader_pdf_notebook.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            report["phases"]["5_reader_pdf_and_notebook"] = {
                "reader_open_ms": pdf_ms,
                "http_206_range_count": range_events.count(206),
                "file_status_codes": list(range_events),
                "notebook_open_ms": nb_ms,
                "reader_init_api": _summarize_api_window(api_events_before_nb),
                "notebook_open_api": _summarize_api_window(api_events),
                **_page_metrics(page),
            }

            # 6. Leitor EPUB
            api_events.clear()
            t0 = time.perf_counter()
            page.goto(f"{base_url}/reader/{seeded['epub_book_id']}", wait_until="networkidle")
            page.wait_for_selector("#epub-viewer", timeout=15000)
            epub_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if shot_dir:
                sp = shot_dir / "p8_03_reader_epub.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))
            report["phases"]["6_reader_epub"] = {
                "reader_open_ms": epub_ms,
                **_summarize_api_window(api_events),
                **_page_metrics(page),
            }

            # 7. Responsividade Tablet (768x1024) e Celular (360x800) + Tema Escuro
            page.set_viewport_size({"width": 768, "height": 1024})
            page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
            page.wait_for_selector("#atlas-pane-overview", timeout=8000)
            tablet_metrics = _page_metrics(page)
            if shot_dir:
                sp = shot_dir / "p8_09_tablet_atlas.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))

            page.set_viewport_size({"width": 360, "height": 800})
            page.goto(f"{base_url}/?tab=atlas", wait_until="networkidle")
            page.wait_for_selector("#atlas-pane-overview", timeout=8000)
            mobile_atlas_metrics = _page_metrics(page)
            if shot_dir:
                sp = shot_dir / "p8_10_mobile_atlas.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))

            # Tema escuro explícito na Biblioteca em desktop
            page.set_viewport_size({"width": 1366, "height": 860})
            page.goto(base_url + "/", wait_until="networkidle")
            page.evaluate("() => { document.documentElement.setAttribute('data-theme', 'dark'); }")
            page.click("#tab-nav-library")
            page.wait_for_selector("#books-container .book-card", timeout=8000)
            if shot_dir:
                sp = shot_dir / "p8_11_dark_theme_library.png"
                page.screenshot(path=str(sp))
                report["screenshots"].append(str(sp))

            report["phases"]["7_responsive_checks"] = {
                "tablet_768": tablet_metrics,
                "mobile_360": mobile_atlas_metrics,
            }
            browser.close()

        return report
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Perfilador Frontend Playwright do Lectoria v1.9.0.")
    parser.add_argument("--output-json", type=str, default=None, help="Arquivo de saída JSON.")
    parser.add_argument("--capture-screenshots", type=str, default=None, help="Diretório opcional para capturas PNG.")
    args = parser.parse_args()

    res = run_frontend_profile(capture_screenshots_dir=args.capture_screenshots)
    out = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output_json:
        Path(args.output_json).write_text(out, encoding="utf-8")
        print(f"Frontend profile JSON salvo em: {args.output_json}")
    else:
        print(out)

    p1 = res["phases"]["1_initial_load_discovery"]
    p2 = res["phases"]["2_library_tab_and_local_filter"]
    p4 = res["phases"]["4_atlas_overview"]
    p5 = res["phases"]["5_reader_pdf_and_notebook"]
    print("\n=== RESUMO FRONTEND PROFILE ===")
    print("Initial `/` load_ms:", p1["load_ms"], "| non-cover APIs:", p1["api_calls_non_cover"], "| duplicates:", p1["duplicate_endpoints"], "| bytes:", p1["non_cover_bytes"])
    print("Library DOM nodes:", p2["dom_nodes"], "| cards initial:", p2["rendered_cards_initial_chunk"], "| filter API calls:", p2["api_calls_during_local_filter"])
    print("Atlas overview ms:", p4["open_ms"], "| DOM nodes:", p4["dom_nodes"], "| JS Heap MB:", (p4["memory"] or {}).get("js_heap_used_mb"))
    print("PDF reader ms:", p5["reader_open_ms"], "| 206 count:", p5["http_206_range_count"], "| console errors:", len(res["console_errors"]), "| page errors:", len(res["page_errors"]))


if __name__ == "__main__":
    main()
