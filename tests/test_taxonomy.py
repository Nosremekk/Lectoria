"""
Testes unitários e de integração para a Taxonomia e Classificação Multidimensional (v1.4.0).
Valida árvore hierárquica, separação multidimensional, persistência no SQLite e isolamento de edições manuais.
"""

import pytest

from database import get_db
from taxonomy_service import (
    map_raw_subjects_to_taxonomy,
    sync_book_taxonomy,
    get_book_taxonomy,
    get_taxonomy_tree,
    TAXONOMY_TREE,
)


def test_taxonomy_hierarchical_mapping():
    # 1. Filosofia Antiga
    tax_filosofia = map_raw_subjects_to_taxonomy(
        title="A República",
        author="Platão",
        raw_subjects=["Filosofia grega", "Ética", "Política"],
    )
    assert "Filosofia" in tax_filosofia["categories"]
    subcats = [s[1] for s in tax_filosofia["subcategories"]]
    assert any("antiga" in s.lower() or "ética" in s.lower() or "política" in s.lower() for s in subcats)

    # 2. Literatura Brasileira (Machado de Assis)
    tax_literatura = map_raw_subjects_to_taxonomy(
        title="Dom Casmurro",
        author="Machado de Assis",
        raw_subjects=["Romance", "Ficção brasileira"],
    )
    assert "Literatura" in tax_literatura["categories"]
    assert tax_literatura["origin"] == "Literatura brasileira"
    assert tax_literatura["genre"] == "Romance"

    # 3. Ciência e Tecnologia / Computação
    tax_computacao = map_raw_subjects_to_taxonomy(
        title="Estruturas de Dados e Algoritmos em Python",
        author="Alan Turing",
        raw_subjects=["Programação", "Algoritmos", "Ciência da Computação"],
    )
    assert "Ciência e tecnologia" in tax_computacao["categories"]
    subcats_comp = [s[1] for s in tax_computacao["subcategories"]]
    assert any("computação" in s.lower() or "inteligência" in s.lower() for s in subcats_comp)


def test_taxonomy_db_sync_and_retrieval(client):
    book_id = "tax_book_test_1"
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO books (id, source_id, title, author, filename, rel_path, filesize, is_missing)
            VALUES (?, 1, 'Meditações Metafísicas', 'René Descartes', 'meditacoes.pdf', 'meditacoes.pdf', 1000, 0)
            """,
            (book_id,),
        )

    tax = map_raw_subjects_to_taxonomy(
        title="Meditações Metafísicas",
        author="René Descartes",
        raw_subjects=["Filosofia moderna", "Epistemologia"],
    )
    sync_book_taxonomy(book_id, tax, is_manual=False)

    saved = get_book_taxonomy(book_id)
    assert "Filosofia" in saved["categories"]
    assert len(saved["subjects"]) >= 1


def test_manual_taxonomy_override_preserved(client):
    book_id = "tax_book_test_2"
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO books (id, source_id, title, author, filename, rel_path, filesize, is_missing)
            VALUES (?, 1, 'O Banquete', 'Platão', 'banquete.pdf', 'banquete.pdf', 1000, 0)
            """,
            (book_id,),
        )

    # 1. Atribui taxonomia manual pelo usuário
    manual_tax = {
        "categories": ["Filosofia"],
        "subcategories": [("Filosofia", "Filosofia antiga")],
        "genre": "Diálogo filosófico",
        "origin": "Grécia Antiga",
        "tags": ["Amor", "Banquete", "Sócrates"],
    }
    sync_book_taxonomy(book_id, manual_tax, is_manual=True)

    # 2. Executa nova sincronização automática (não deve apagar os vínculos manuais)
    auto_tax = {
        "categories": ["Literatura"],
        "subcategories": [],
        "tags": ["Outra tag"],
    }
    sync_book_taxonomy(book_id, auto_tax, is_manual=False)

    saved = get_book_taxonomy(book_id)
    # A categoria manual 'Filosofia' e tags continuam preservadas
    assert "Filosofia" in saved["categories"]
    assert saved["origin"] == "Grécia Antiga"
    assert saved["genre"] == "Diálogo filosófico"


def test_get_taxonomy_tree_endpoint(client):
    res = client.get("/api/taxonomy/tree")
    assert res.status_code == 200
    data = res.json()
    assert "tree" in data
    # Árvore não deve conter categorias vazias (total_books > 0)
    for cat in data["tree"]:
        assert cat["total_books"] > 0
