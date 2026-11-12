"""
Testes de regressão da v1.6.3:
Garante a renderização proporcional das capas no modo Grade e Lista,
prevenindo o encolhimento de capas para dimensões de miniatura (48x68) no grid.
"""

from pathlib import Path
import re
import pytest
import database
import config

def test_sqlite_schema_version_remains_v10():
    """Garante que a v1.6.3 não altera a versão do schema SQLite (permanece estritamente 10)."""
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;")
        row = cur.fetchone()
        assert row is not None, "Tabela schema_migrations não encontrada."
        assert row["version"] == 10, f"Schema SQLite alterado indevidamente: versão {row['version']} (esperado: 10)."
    finally:
        conn.close()


def test_book_card_html_template_has_no_list_classes():
    """Valida que o template HTML do card de livro em app.js não inclui classes legadas list-*."""
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
    assert app_js_path.exists(), "app.js deve existir"
    content = app_js_path.read_text(encoding="utf-8")

    # Extrai o corpo da função generateBookCardHtml
    match = re.search(r"function generateBookCardHtml\(book\)\s*\{(.*?)\n  \}", content, re.DOTALL)
    assert match, "Função generateBookCardHtml deve estar definida em app.js"
    func_body = match.group(1)

    forbidden_classes = [
        "list-item",
        "list-cover-link",
        "list-cover-thumb",
        "list-info",
        "list-title",
        "list-author",
        "list-meta",
        "list-progress",
        "list-actions"
    ]

    for cls in forbidden_classes:
        assert cls not in func_body, f"Classe legada '{cls}' encontrada em generateBookCardHtml; vaza estilos para o modo Grade"


def test_css_unification_and_aspect_ratio_safety():
    """Valida que o CSS trata corretamente o container .books-list e .book-cover."""
    css_path = Path(__file__).resolve().parent.parent / "static" / "css" / "style.css"
    assert css_path.exists(), "style.css deve existir"
    css = css_path.read_text(encoding="utf-8")

    # .book-cover-wrapper no grid tem aspect-ratio: 1 / 1.42
    assert ".book-cover-wrapper" in css
    assert "aspect-ratio: 1 / 1.42" in css

    # .books-list .book-card .book-cover-wrapper tem aspect-ratio: auto
    assert ".books-list .book-card .book-cover-wrapper" in css
    assert "aspect-ratio: auto;" in css

    # .books-list .book-card .book-cover::after oculta a lombada no modo lista
    assert ".books-list .book-card .book-cover::after" in css
    assert "display: none;" in css
