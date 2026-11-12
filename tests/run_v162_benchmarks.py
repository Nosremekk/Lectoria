"""
Script de benchmark e validação visual da v1.6.2
Executa bateria de testes entre 7 viewports, emulação Chrome Android,
medições de latência de filtros, contagem de requisições HTTP e capturas de tela.
"""
import sys
import os
import time
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from multiprocessing import Process
import uvicorn
from playwright.sync_api import sync_playwright

def setup_test_db(db_path, books_dir):
    import database
    database.init_db()

    import auth_service
    admin = auth_service.create_user("admin", "Admin123!", role="admin", display_name="Administrador")
    raissa = auth_service.create_user("raissa", "Raissa123!", role="reader", display_name="Raíssa")

    # Cria 60 livros para testar chunking (chunk size = 36) e filtros
    categories = ['Ficção', 'História', 'Tecnologia', 'Filosofia', 'Ciência']
    statuses = ['reading', 'read', 'unread']
    with database.get_db() as conn:
        cur = conn.cursor()
        for i in range(1, 61):
            fname = f"book_{i}.pdf"
            pdf_path = books_dir / fname
            pdf_path.write_bytes(b"%PDF-1.4 dummy content for test")

            cur.execute("""
                INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count)
                VALUES (?, ?, ?, ?, ?, 10240, 'pdf', 100)
            """, (f"book_{i:03d}", f"Livro de Teste {i:02d}", f"Autor {i%5 + 1}", fname, fname))
        conn.commit()

    import progress
    for i in range(1, 61):
        b_id = f"book_{i:03d}"
        if i % 3 == 0:
            progress.save_progress(b_id, 20, 100, user_id=raissa["id"]) # reading
        elif i % 3 == 1:
            progress.save_progress(b_id, 100, 100, user_id=raissa["id"]) # read
            # unread se i % 3 == 2

def run_benchmarks():
    temp_dir = Path(tempfile.mkdtemp())
    data_dir = temp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"
    books_dir = temp_dir / "books"
    books_dir.mkdir(parents=True, exist_ok=True)

    os.environ["LECTORIA_DATA_DIR"] = str(data_dir)
    os.environ["LECTORIA_DB_PATH"] = str(db_path)
    os.environ["LECTORIA_LIBRARY_DIR"] = str(books_dir)
    os.environ["TESTING"] = "1"

    import config
    config.DATA_DIR = data_dir
    config.DB_PATH = db_path
    config.LIBRARY_DIR = books_dir
    config.COVERS_DIR = data_dir / "covers"
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)

    setup_test_db(db_path, books_dir)

    from main import app
    port = 58220
    proc = Process(target=uvicorn.run, args=(app,), kwargs={"host": "127.0.0.1", "port": port, "log_level": "warning"})
    proc.daemon = True
    proc.start()
    time.sleep(2.5)

    base_url = f"http://127.0.0.1:{port}"
    artifacts_dir = Path("/home/vini/.gemini/antigravity/brain/9ae11ae2-ebc0-44c2-9e79-22a1b0910e27")

    results = {
        "viewports": {},
        "network_benchmarks": {},
        "chunking": {},
        "visual_tests": {}
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()

            # Autenticação como 'raissa' (leitor, exatamente o perfil do bug relatado)
            login_context = browser.new_context()
            login_page = login_context.new_page()
            login_page.goto(base_url)
            login_page.wait_for_timeout(500)
            
            # Login via API
            import urllib.request
            req = urllib.request.Request(
                f"{base_url}/api/auth/login",
                data=json.dumps({"username": "raissa", "password": "Raissa123!"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req) as resp:
                cookie_header = resp.headers.get("Set-Cookie")
                session_token = cookie_header.split("lectoria_session=")[1].split(";")[0]

            cookies = [{
                "name": "lectoria_session",
                "value": session_token,
                "domain": "127.0.0.1",
                "path": "/"
            }]

            # 1. TESTE NAS 7 RESOLUÇÕES
            viewports = [
                ("mobile_320", 320, 568),
                ("mobile_360", 360, 640),
                ("mobile_375", 375, 667),
                ("mobile_390", 390, 844),
                ("mobile_412", 412, 915),
                ("tablet_768", 768, 1024),
                ("desktop_1366", 1366, 768),
            ]

            for name, w, h in viewports:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                ctx.add_cookies(cookies)
                page = ctx.new_page()
                page.goto(base_url)
                page.wait_for_timeout(600)

                # Avalia overflow horizontal no Discovery
                disc_metrics = page.evaluate("""() => {
                    return {
                        sw: document.documentElement.scrollWidth,
                        cw: document.documentElement.clientWidth,
                        hasOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
                    };
                }""")

                # Alterna para a Biblioteca
                page.click("#tab-nav-library")
                page.wait_for_timeout(500)

                lib_metrics = page.evaluate("""() => {
                    return {
                        sw: document.documentElement.scrollWidth,
                        cw: document.documentElement.clientWidth,
                        hasOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
                    };
                }""")

                # Captura de tela para evidência
                screenshot_path = artifacts_dir / f"v162_responsive_{name}.png"
                page.screenshot(path=str(screenshot_path), full_page=False)

                results["viewports"][name] = {
                    "width": w,
                    "height": h,
                    "discovery": disc_metrics,
                    "library": lib_metrics,
                    "screenshot": str(screenshot_path.name)
                }
                ctx.close()

            # 2. EMULAÇÃO CHROME ANDROID (Samsung Galaxy / Pixel moderno)
            chrome_android_ctx = browser.new_context(
                viewport={"width": 412, "height": 915},
                user_agent="Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36",
                device_scale_factor=2.875,
                is_mobile=True,
                has_touch=True
            )
            chrome_android_ctx.add_cookies(cookies)
            android_page = chrome_android_ctx.new_page()
            android_page.goto(base_url)
            android_page.wait_for_timeout(800)

            # Verifica integridade da barra e tabs
            tabs_info = android_page.evaluate("""() => {
                const nav = document.querySelector('.main-view-nav');
                const tabs = document.querySelector('.main-view-tabs');
                const style = window.getComputedStyle(nav);
                const rect = nav.getBoundingClientRect();
                return {
                    navWidth: rect.width,
                    navScrollWidth: nav.scrollWidth,
                    overflowX: style.overflowX,
                    maskImage: style.webkitMaskImage || style.maskImage,
                    docSw: document.documentElement.scrollWidth,
                    docCw: document.documentElement.clientWidth
                };
            }""")
            android_screenshot = artifacts_dir / "v162_chrome_android_emulated.png"
            android_page.screenshot(path=str(android_screenshot))
            results["visual_tests"]["chrome_android"] = {
                "tabs_info": tabs_info,
                "screenshot": str(android_screenshot.name)
            }
            chrome_android_ctx.close()

            # 3. BENCHMARK DE LATÊNCIA E REQUISIÇÕES HTTP NA BIBLIOTECA
            bench_ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            bench_ctx.add_cookies(cookies)
            bench_page = bench_ctx.new_page()

            # Contador de requisições do endpoint de livros /api/books (excluindo /cover)
            api_books_requests = []
            bench_page.on("request", lambda r: api_books_requests.append(r.url) if ("/api/books?" in r.url or r.url.rstrip("/").endswith("/api/books")) else None)

            bench_page.goto(base_url)
            bench_page.wait_for_timeout(600)
            bench_page.click("#tab-nav-library")
            bench_page.wait_for_timeout(600)

            reqs_initial = len(api_books_requests)

            # Teste de alternância rápida de filtros de status
            status_filters = ["all", "in_progress", "completed", "unread"]
            latencies = []

            for st in status_filters:
                t0 = time.perf_counter()
                bench_page.click(f'button[data-status="{st}"]')
                bench_page.wait_for_timeout(100) # breve espera para paint
                dt = (time.perf_counter() - t0) * 1000
                latencies.append({"status": st, "ms": round(dt, 2)})

            reqs_after_filter = len(api_books_requests)

            # Teste de alternância Grade <-> Lista
            t0_view = time.perf_counter()
            bench_page.click("#btn-view-list")
            bench_page.wait_for_timeout(50)
            dt_to_list = (time.perf_counter() - t0_view) * 1000

            t0_grid = time.perf_counter()
            bench_page.click("#btn-view-grid")
            bench_page.wait_for_timeout(50)
            dt_to_grid = (time.perf_counter() - t0_grid) * 1000

            reqs_after_view_toggles = len(api_books_requests)

            results["network_benchmarks"] = {
                "initial_books_requests": reqs_initial,
                "requests_during_filter_switching": reqs_after_filter - reqs_initial,
                "requests_during_grid_list_toggles": reqs_after_view_toggles - reqs_after_filter,
                "filter_latencies": latencies,
                "avg_filter_latency_ms": round(sum(l["ms"] for l in latencies) / len(latencies), 2),
                "view_toggle_list_ms": round(dt_to_list, 2),
                "view_toggle_grid_ms": round(dt_to_grid, 2),
            }

            # 4. CHUNKING E PROGRESSIVE RENDERING
            # Restaura filtro para Todos os Livros (60 livros no total)
            bench_page.click('button[data-status="all"]')
            bench_page.wait_for_timeout(300)

            # Com 60 livros no banco, inicialmente deve haver exatamente 36 cards
            initial_rendered_cards = bench_page.evaluate("""() => {
                return document.querySelectorAll('#books-container .book-card').length;
            }""")
            
            # Rola a página para baixo para acionar o sentinel
            bench_page.evaluate("""() => {
                window.scrollTo(0, document.body.scrollHeight);
                const sentinel = document.getElementById('books-scroll-sentinel');
                if (sentinel) sentinel.scrollIntoView();
            }""")
            bench_page.wait_for_timeout(600)

            cards_after_scroll = bench_page.evaluate("""() => {
                return document.querySelectorAll('#books-container .book-card').length;
            }""")

            results["chunking"] = {
                "total_in_db": 60,
                "initial_rendered": initial_rendered_cards,
                "expected_first_chunk": 36,
                "rendered_after_scroll": cards_after_scroll,
                "progressive_render_working": (initial_rendered_cards == 36 and cards_after_scroll == 60)
            }

            # Screenshot da biblioteca em Grid e em Lista
            lib_grid_screenshot = artifacts_dir / "v162_library_grid_mode.png"
            bench_page.screenshot(path=str(lib_grid_screenshot))
            bench_page.click("#btn-view-list")
            bench_page.wait_for_timeout(200)
            lib_list_screenshot = artifacts_dir / "v162_library_list_mode.png"
            bench_page.screenshot(path=str(lib_list_screenshot))

            results["visual_tests"]["library_grid"] = str(lib_grid_screenshot.name)
            results["visual_tests"]["library_list"] = str(lib_list_screenshot.name)

            bench_ctx.close()

    finally:
        proc.terminate()
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("=== RESULTADOS BENCHMARK V1.6.2 ===")
    print(json.dumps(results, indent=2))

    with open(artifacts_dir / "v162_benchmark_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_benchmarks()
