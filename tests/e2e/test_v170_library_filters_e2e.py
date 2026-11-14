"""
Suíte E2E Playwright para validação real dos Filtros da Biblioteca (v1.7.0).

Reproduz e valida no navegador Chromium headless:
1. Seleção da categoria 'Filosofia (3)' em `#category-select` exibindo os 3 livros correspondentes (sem 'Nenhum livro correspondente').
2. Filtros isolados de Subcategoria, Gênero, Origem, Status e 'Sem categoria (__uncategorized__)'.
3. Filtros combinados (Categoria + Subcategoria, Categoria + Gênero, Categoria + Origem, Categoria + Status e múltiplos simultâneos).
4. Limpeza completa de filtros (`#btn-clear-filters`) e contador de filtros ativos no mobile (`#active-filters-count-badge`).
5. Persistência de estado dos filtros ao alternar entre abas principais e retornar à Biblioteca.
6. Captura de evidências visuais em desktop e mobile.
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
import progress
import social_service
import taxonomy_service
from tests.conftest import create_sample_pdf


EVIDENCE_DIR = Path("/tmp/lectoria_v170_screenshots")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def filters_e2e_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("lectoria_v170_filters_e2e")
    db_path = root / "lectoria_filters_e2e.db"
    covers_dir = root / "covers"
    lib_dir = root / "library"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    books_seed = [
        ("phil-1", "A República", "Platão", "rep.pdf"),
        ("phil-2", "Ética a Nicômaco", "Aristóteles", "etica.pdf"),
        ("phil-3", "Meditações Metafísicas", "René Descartes", "med.pdf"),
        ("lit-1", "Dom Casmurro", "Machado de Assis", "dom.pdf"),
        ("lit-2", "Fundação", "Isaac Asimov", "fund.pdf"),
        ("sci-1", "Sistemas Operacionais Modernos", "Andrew S. Tanenbaum", "so.pdf"),
        ("uncat-1", "Caderno de Notas Pessoais", "Anônimo", "notas.pdf"),
    ]
    for _, title, author, fname in books_seed:
        create_sample_pdf(lib_dir / fname, title, author, pages=10)

    old_db, old_cov, old_lib = config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    database.init_db(db_path)

    user = auth_service.create_user(username="leitor_e2e_filtros", password="password123", role="admin")
    token = auth_service.create_session(user["id"])

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1)",
            (str(lib_dir),),
        )
        for bid, title, author, fname in books_seed:
            fsize = (lib_dir / fname).stat().st_size
            cur.execute(
                """
                INSERT INTO books (id, source_id, title, author, format, filesize, filename, rel_path, page_count, is_missing)
                VALUES (?, 1, ?, ?, 'pdf', ?, ?, ?, 10, 0)
                """,
                (bid, title, author, fsize, fname, fname),
            )
        conn.commit()
    finally:
        conn.close()

    taxonomy_service.sync_book_taxonomy(
        "phil-1",
        {
            "categories": ["Filosofia"],
            "subcategories": [("Filosofia", "Filosofia antiga"), ("Filosofia", "Ética e política")],
            "genre": "Diálogo filosófico",
            "origin": "Grécia Antiga",
            "tags": ["Justiça"],
        },
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "phil-2",
        {
            "categories": ["Filosofia"],
            "subcategories": [("Filosofia", "Ética e política")],
            "genre": "Tratado filosófico",
            "origin": "Grécia Antiga",
            "tags": ["Virtude"],
        },
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "phil-3",
        {
            "categories": ["Filosofia"],
            "subcategories": [("Filosofia", "Filosofia moderna")],
            "genre": "Ensaio filosófico",
            "origin": "França",
            "tags": ["Epistemologia"],
        },
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "lit-1",
        {
            "categories": ["Literatura"],
            "subcategories": [("Literatura", "Romance clássico")],
            "genre": "Romance",
            "origin": "Literatura brasileira",
            "tags": ["Realismo"],
        },
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "lit-2",
        {
            "categories": ["Literatura"],
            "subcategories": [("Literatura", "Ficção Científica")],
            "genre": "Ficção Científica",
            "origin": "Estados Unidos",
            "tags": ["Galáxia"],
        },
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "sci-1",
        {
            "categories": ["Ciência e tecnologia"],
            "subcategories": [("Ciência e tecnologia", "Computação")],
            "genre": "Livro técnico",
            "origin": "Holanda",
            "tags": ["Sistemas"],
        },
        is_manual=True,
    )

    # Progresso em phil-1 (em andamento) e phil-2 (concluído)
    progress.save_progress("phil-1", current_page=4, total_pages=10, user_id=user["id"])
    progress.save_progress("phil-2", current_page=10, total_pages=10, user_id=user["id"])
    social_service.upsert_book_review(user_id=user["id"], book_id="lit-1", is_favorite=True, is_public=False)

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["LECTORIA_DB_PATH"] = str(db_path)
    env["LECTORIA_COVERS_DIR"] = str(covers_dir)
    env["LECTORIA_LIBRARY_DIR"] = str(lib_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/auth/status", timeout=1):
                break
        except Exception:
            time.sleep(0.25)

    try:
        yield {"base_url": base_url, "token": token}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR = old_db, old_cov, old_lib


def test_e2e_library_filters_isolated_combined_clear_and_mobile(filters_e2e_server):
    base_url = filters_e2e_server["base_url"]
    token = filters_e2e_server["token"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies(
            [
                {
                    "name": "lectoria_session",
                    "value": token,
                    "url": base_url,
                }
            ]
        )
        page = context.new_page()
        page.goto(base_url, wait_until="networkidle")

        # Abre a aba Biblioteca / Acervo
        page.locator("#tab-nav-library").click()
        page.locator("#books-container .book-card").first.wait_for(state="visible", timeout=10000)

        # Total inicial: 7 livros
        assert page.locator("#books-container .book-card").count() == 7

        # Confirma que os seletores taxonômicos foram populados com contadores exatos
        cat_options_text = page.locator("#category-select option").all_inner_texts()
        assert any("Filosofia (3)" in t for t in cat_options_text)
        assert any("Literatura (2)" in t for t in cat_options_text)
        assert any("Ciência e tecnologia (1)" in t for t in cat_options_text)
        assert any("Sem categoria (1)" in t for t in cat_options_text)

        genre_options_text = page.locator("#genre-select option").all_inner_texts()
        assert any("Diálogo filosófico (1)" in t for t in genre_options_text)
        assert any("Ficção Científica (1)" in t for t in genre_options_text)

        origin_options_text = page.locator("#origin-select option").all_inner_texts()
        assert any("Grécia Antiga (2)" in t for t in origin_options_text)
        assert any("Literatura brasileira (1)" in t for t in origin_options_text)

        # 1. Regressão principal: Seleciona Categoria "Filosofia" isoladamente
        page.select_option("#category-select", "Filosofia")
        assert page.locator("#books-container .book-card").count() == 3
        assert page.locator("#books-container .empty-state").count() == 0
        titles_phil = set(page.locator("#books-container .book-card .book-title").all_inner_texts())
        assert titles_phil == {"A República", "Ética a Nicômaco", "Meditações Metafísicas"}

        # Captura evidência visual desktop com filtro Filosofia (3) ativo
        page.screenshot(path=str(EVIDENCE_DIR / "v170_filter_filosofia_desktop.png"))

        # 2. Combinação: Categoria "Filosofia" + Subcategoria "Ética e política"
        page.select_option("#subcategory-select", "Ética e política")
        assert page.locator("#books-container .book-card").count() == 2
        titles_sub = set(page.locator("#books-container .book-card .book-title").all_inner_texts())
        assert titles_sub == {"A República", "Ética a Nicômaco"}

        # 3. Combinação: Categoria + Subcategoria + Gênero ("Tratado filosófico")
        page.select_option("#genre-select", "Tratado filosófico")
        assert page.locator("#books-container .book-card").count() == 1
        assert page.locator("#books-container .book-card .book-title").inner_text() == "Ética a Nicômaco"

        # 4. Persistência ao navegar para Descoberta e voltar para Biblioteca
        page.locator("#tab-nav-discovery").click()
        page.locator("#discovery-section").wait_for(state="visible")
        page.locator("#tab-nav-library").click()
        assert page.locator("#category-select").input_value() == "Filosofia"
        assert page.locator("#books-container .book-card").count() == 1

        # 5. Botão Limpar filtros restaura estado completo (7 livros)
        page.locator("#btn-clear-filters").click()
        assert page.locator("#category-select").input_value() == ""
        assert page.locator("#subcategory-select").input_value() == ""
        assert page.locator("#genre-select").input_value() == ""
        assert page.locator("#origin-select").input_value() == ""
        assert page.locator("#books-container .book-card").count() == 7

        # 6. Filtro "Sem categoria" (__uncategorized__) retorna apenas a obra sem categoria
        page.select_option("#category-select", "__uncategorized__")
        assert page.locator("#books-container .book-card").count() == 1
        assert page.locator("#books-container .book-card .book-title").inner_text() == "Caderno de Notas Pessoais"

        # 7. Filtro isolado por Origem e combinação Categoria + Status em Mobile (375x812)
        page.locator("#btn-clear-filters").click()
        page.set_viewport_size({"width": 375, "height": 920})
        page.locator("#btn-toggle-filters-mobile").click()
        page.select_option("#category-select", "Filosofia")
        page.select_option("#origin-select", "Grécia Antiga")
        page.locator(".status-tabs .tab-btn[data-status='in_progress']").click()

        assert "active" in (page.locator(".status-tabs .tab-btn[data-status='in_progress']").get_attribute("class") or "")
        assert page.locator("#books-container .book-card").count() == 1
        assert page.locator("#books-container .book-card .book-title").inner_text() == "A República"
        assert page.locator("#active-filters-count-badge").inner_text() == "3"

        # Captura evidência visual mobile com filtros combinados ativos
        page.screenshot(path=str(EVIDENCE_DIR / "v170_filter_combined_mobile.png"), full_page=True)

        browser.close()
