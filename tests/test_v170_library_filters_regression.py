"""
Testes automatizados de regressão para o Sistema de Filtros e Contadores Taxonômicos da Biblioteca (v1.7.0).

Cobre:
1. Filtro isolado por Categoria (incluindo regressão específica de 'Filosofia (N)').
2. Filtro isolado por Subcategoria, Gênero, Origem e Status de leitura.
3. Filtros combinados (Categoria + Subcategoria, Categoria + Gênero, Categoria + Origem, Categoria + Status e múltiplos filtros simultâneos).
4. Filtro de livros sem categoria ('__uncategorized__' / uncategorized=true) e metadados parciais.
5. Categorias, subcategorias, gêneros e origens com acentuação e capitalização variada.
6. Consistência exata entre os contadores de `/api/taxonomy/tree` e os livros retornados por `/api/books` (incluindo múltiplas subcategorias no mesmo livro e fontes inativas).
"""

import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import taxonomy_service
from main import app


@pytest.fixture
def filter_test_env(tmp_path, monkeypatch):
    """Ambiente isolado com acervo rico em categorias, subcategorias, gêneros, origens e status."""
    test_db = tmp_path / "test_filters_v170.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()
    user = auth_service.create_user(username="leitor_filtros", password="password123", role="admin")

    books_seed = [
        ("phil-1", "A República", "Platão", "pdf", "rep.pdf"),
        ("phil-2", "Ética a Nicômaco", "Aristóteles", "pdf", "etica.pdf"),
        ("phil-3", "Meditações Metafísicas", "René Descartes", "epub", "med.epub"),
        ("lit-1", "Dom Casmurro", "Machado de Assis", "pdf", "dom.pdf"),
        ("lit-2", "Fundação", "Isaac Asimov", "epub", "fund.epub"),
        ("sci-1", "Sistemas Operacionais Modernos", "Andrew S. Tanenbaum", "pdf", "so.pdf"),
        ("uncat-1", "Caderno de Notas Pessoais", "Anônimo", "pdf", "notas.pdf"),
    ]

    with TestClient(app) as client:
        conn = database.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1)",
                (str(test_library),),
            )
            cur.execute(
                "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (2, 'Fonte Inativa', ?, 0)",
                (str(test_library / "inativa"),),
            )

            for bid, title, author, fmt, fname in books_seed:
                cur.execute(
                    """
                    INSERT INTO books (id, source_id, title, author, format, filesize, filename, rel_path, page_count, is_missing)
                    VALUES (?, 1, ?, ?, ?, 2048, ?, ?, 200, 0)
                    """,
                    (bid, title, author, fmt, fname, fname),
                )

            # Livro em fonte inativa (não deve inflar contadores nem aparecer na listagem)
            cur.execute(
                """
                INSERT INTO books (id, source_id, title, author, format, filesize, filename, rel_path, page_count, is_missing)
                VALUES ('phil-inactive', 2, 'Obra Oculta', 'Autor Inativo', 'pdf', 1024, 'oculta.pdf', 'oculta.pdf', 100, 0)
                """
            )
            conn.commit()
        finally:
            conn.close()

        # Taxonomia das obras ativas
        # phil-1 possui DUAS subcategorias na mesma categoria Filosofia (testa não-duplicação de total_books)
        taxonomy_service.sync_book_taxonomy(
            "phil-1",
            {
                "categories": ["Filosofia"],
                "subcategories": [("Filosofia", "Filosofia antiga"), ("Filosofia", "Ética e política")],
                "genre": "Diálogo filosófico",
                "origin": "Grécia Antiga",
                "tags": ["Justiça", "Polis"],
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
                "tags": ["Império Galáctico"],
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
                "tags": ["Kernel"],
            },
            is_manual=True,
        )
        # Obra da fonte inativa também categorizada em Filosofia para validar que não infla o contador
        taxonomy_service.sync_book_taxonomy(
            "phil-inactive",
            {
                "categories": ["Filosofia"],
                "subcategories": [("Filosofia", "Filosofia antiga")],
                "genre": "Diálogo filosófico",
                "origin": "Grécia Antiga",
                "tags": [],
            },
            is_manual=True,
        )

        res = client.post("/api/auth/login", json={"username": "leitor_filtros", "password": "password123"})
        assert res.status_code == 200
        # Configura status de leitura: phil-1 em andamento, phil-2 concluído, lit-1 favorito
        client.post("/api/books/phil-1/progress", json={"current_page": 50, "total_pages": 200})
        client.put("/api/books/phil-2/reading-status", json={"status": "completed"})
        client.post("/api/books/lit-1/reviews", json={"is_favorite": True, "is_public": False})
        yield client, user


def test_taxonomy_tree_counters_match_available_books_exactly(filter_test_env):
    """
    Garante que os contadores de `/api/taxonomy/tree` (categorias, subcategorias, gêneros,
    origens e sem categoria) correspondem exatamente às obras disponíveis em `/api/books`.
    """
    client, _ = filter_test_env

    tree_res = client.get("/api/taxonomy/tree")
    assert tree_res.status_code == 200
    tree_data = tree_res.json()

    tree_by_cat = {c["category"]: c for c in tree_data["tree"]}
    assert "Filosofia" in tree_by_cat
    # Devem ser exatamente 3 livros ativos de Filosofia (phil-1 não duplica por ter 2 subcategorias e phil-inactive é ignorado)
    assert tree_by_cat["Filosofia"]["total_books"] == 3
    assert tree_by_cat["Literatura"]["total_books"] == 2
    assert tree_by_cat["Ciência e tecnologia"]["total_books"] == 1
    assert tree_data["uncategorized_count"] == 1

    # Valida que cada contador da árvore bate com a listagem filtrada de /api/books
    for cat_name, cat_obj in tree_by_cat.items():
        books_res = client.get("/api/books", params={"category": cat_name})
        assert books_res.status_code == 200
        assert books_res.json()["count"] == cat_obj["total_books"]

    # Valida facetas de gêneros e origens retornadas no endpoint da árvore
    genres_map = {g["name"]: g["book_count"] for g in tree_data["genres"]}
    assert genres_map["Diálogo filosófico"] == 1
    assert genres_map["Ficção Científica"] == 1

    origins_map = {o["name"]: o["book_count"] for o in tree_data["origins"]}
    assert origins_map["Grécia Antiga"] == 2
    assert origins_map["Literatura brasileira"] == 1


def test_isolated_filters_category_subcategory_genre_origin_status(filter_test_env):
    """Valida cada filtro funcionando isoladamente e a estrutura `book.taxonomy` consumida pelo frontend."""
    client, _ = filter_test_env

    # 1. Categoria isolada: Filosofia (regressão reportada pelo usuário)
    res_phil = client.get("/api/books", params={"category": "Filosofia"})
    assert res_phil.status_code == 200
    phil_books = res_phil.json()["books"]
    assert {b["id"] for b in phil_books} == {"phil-1", "phil-2", "phil-3"}
    for b in phil_books:
        assert "Filosofia" in b["taxonomy"]["categories"]

    # 2. Subcategoria isolada (com acentuação)
    res_sub = client.get("/api/books", params={"subcategory": "Ética e política"})
    assert {b["id"] for b in res_sub.json()["books"]} == {"phil-1", "phil-2"}

    # 3. Gênero isolado (com acentuação)
    res_genre = client.get("/api/books", params={"genre": "Ficção Científica"})
    assert {b["id"] for b in res_genre.json()["books"]} == {"lit-2"}

    # 4. Origem isolada
    res_origin = client.get("/api/books", params={"origin": "Grécia Antiga"})
    assert {b["id"] for b in res_origin.json()["books"]} == {"phil-1", "phil-2"}

    # 5. Status isolado
    res_in_prog = client.get("/api/books", params={"status": "in_progress"})
    assert {b["id"] for b in res_in_prog.json()["books"]} == {"phil-1"}

    res_comp = client.get("/api/books", params={"status": "completed"})
    assert {b["id"] for b in res_comp.json()["books"]} == {"phil-2"}

    # 6. Sem categoria (__uncategorized__)
    res_uncat = client.get("/api/books", params={"category": "__uncategorized__"})
    assert {b["id"] for b in res_uncat.json()["books"]} == {"uncat-1"}


def test_combined_filters_and_empty_results(filter_test_env):
    """Valida combinações multidimensionais de filtros e filtro com resultado legitimamente vazio."""
    client, _ = filter_test_env

    # Categoria + Subcategoria
    res_cat_sub = client.get("/api/books", params={"category": "Filosofia", "subcategory": "Filosofia moderna"})
    assert {b["id"] for b in res_cat_sub.json()["books"]} == {"phil-3"}

    # Categoria + Gênero
    res_cat_genre = client.get("/api/books", params={"category": "Literatura", "genre": "Romance"})
    assert {b["id"] for b in res_cat_genre.json()["books"]} == {"lit-1"}

    # Categoria + Origem
    res_cat_origin = client.get("/api/books", params={"category": "Filosofia", "origin": "Grécia Antiga"})
    assert {b["id"] for b in res_cat_origin.json()["books"]} == {"phil-1", "phil-2"}

    # Categoria + Status
    res_cat_status = client.get("/api/books", params={"category": "Filosofia", "status": "in_progress"})
    assert {b["id"] for b in res_cat_status.json()["books"]} == {"phil-1"}

    # Múltiplos filtros simultâneos (Categoria + Subcategoria + Gênero + Origem + Status)
    res_multi = client.get(
        "/api/books",
        params={
            "category": "Filosofia",
            "subcategory": "Ética e política",
            "genre": "Tratado filosófico",
            "origin": "Grécia Antiga",
            "status": "completed",
        },
    )
    assert {b["id"] for b in res_multi.json()["books"]} == {"phil-2"}

    # Combinação sem resultados correspondentes
    res_empty = client.get("/api/books", params={"category": "Filosofia", "origin": "Literatura brasileira"})
    assert res_empty.json()["books"] == []
    assert res_empty.json()["count"] == 0


def test_diagnose_taxonomy_detects_legacy_shared_facets_and_preserves_manual_classification(tmp_path, monkeypatch):
    """
    Valida que:
    1. O diagnóstico read-only (`scripts/diagnose_taxonomy.py`) detecta assuntos legados compartilhados
       com facetas acopladas (genre/origin/period) sem modificar o banco.
    2. Re-sincronizar um livro automático preserva `is_manual` de outros livros e isola as facetas
       ortogonais em registros próprios.
    """
    from scripts.diagnose_taxonomy import inspect_taxonomy_health

    test_db = tmp_path / "test_diag_taxonomy.db"
    test_library = tmp_path / "lib"
    test_library.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'Principal', ?, 1)",
            (str(test_library),),
        )
        cur.execute(
            "INSERT INTO books (id, source_id, title, author, format, filesize, filename, rel_path, page_count, is_missing) VALUES ('b1', 1, 'Livro 1', 'Platão', 'pdf', 100, 'b1.pdf', 'b1.pdf', 10, 0)"
        )
        cur.execute(
            "INSERT INTO books (id, source_id, title, author, format, filesize, filename, rel_path, page_count, is_missing) VALUES ('b2', 1, 'Livro 2', 'René Descartes', 'pdf', 100, 'b2.pdf', 'b2.pdf', 10, 0)"
        )
        # Simula um registro legado da v1.6.x onde genre/origin ficavam acoplados à subcategoria compartilhada
        cur.execute(
            """
            INSERT INTO subjects (id, name, category, subcategory, genre, origin, period)
            VALUES (900, 'Filosofia: Ética', 'Filosofia', 'Ética', 'Tratado', 'Grécia Antiga', 'Antiguidade')
            """
        )
        cur.execute("INSERT INTO book_subjects (book_id, subject_id, is_manual) VALUES ('b1', 900, 1)")
        cur.execute("INSERT INTO book_subjects (book_id, subject_id, is_manual) VALUES ('b2', 900, 0)")
        conn.commit()
    finally:
        conn.close()

    # Diagnóstico deve detectar o subject 900 como legado compartilhado com faceta acoplada
    report_before = inspect_taxonomy_health(test_db)
    assert report_before["shared_legacy_facet_subjects_count"] == 1
    assert report_before["shared_legacy_facet_subjects"][0]["subject_id"] == 900
    affected_by_id = {b["book_id"]: b for b in report_before["potentially_affected_books"]}
    assert affected_by_id["b1"]["has_manual_classification"] is True
    assert affected_by_id["b2"]["has_manual_classification"] is False

    # Ao sincronizar b1 manualmente e tentar sobrescrever automaticamente (`is_manual=False`), a trava manual é preservada
    taxonomy_service.sync_book_taxonomy(
        "b1",
        {"categories": ["Filosofia"], "subcategories": [("Filosofia", "Ética")], "origin": "Grécia Antiga"},
        is_manual=True,
    )
    taxonomy_service.sync_book_taxonomy(
        "b1",
        {"categories": ["Literatura"], "subcategories": [("Literatura", "Romance")], "origin": "França"},
        is_manual=False,
    )
    b1_tax = taxonomy_service.get_book_taxonomy("b1")
    assert b1_tax["is_manual"] is True
    assert b1_tax["categories"] == ["Filosofia"]


def test_automation_service_dir_snapshots_baseline_and_incremental_scan(tmp_path, monkeypatch):
    """
    Audita o ajuste em `automation_service.py`:
    - `start()` e `stop()` limpam `_dir_snapshots`.
    - A primeira chamada a `check_directory_changes_and_auto_scan()` registra o snapshot basal
      sem disparar scan duplicado no boot (`returns None`).
    - Uma alteração subsequente de mtime no diretório dispara o auto-scan normalmente.
    """
    import os
    import automation_service

    test_db = tmp_path / "test_auto_snapshots.db"
    test_library = tmp_path / "auto_lib"
    test_library.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES (1, 'AutoSource', ?, 1)",
            (str(test_library),),
        )
        conn.commit()
    finally:
        conn.close()

    automation_service._dir_snapshots.clear()

    # 1ª verificação: apenas registra snapshot inicial (source_id=1), não dispara scan
    res_baseline = automation_service.check_directory_changes_and_auto_scan()
    assert res_baseline is None
    assert 1 in automation_service._dir_snapshots

    # 2ª verificação sem mudanças no diretório: não dispara scan
    res_unchanged = automation_service.check_directory_changes_and_auto_scan()
    assert res_unchanged is None

    # Simula adição de subdiretório com mtime mais recente
    sub = test_library / "nova_pasta"
    sub.mkdir()
    future_mtime = automation_service._dir_snapshots[1] + 10.0
    os.utime(sub, (future_mtime, future_mtime))

    res_changed = automation_service.check_directory_changes_and_auto_scan()
    assert res_changed is not None
    assert res_changed.get("job_type") == "scan_library"
    assert res_changed.get("status") == "completed"

