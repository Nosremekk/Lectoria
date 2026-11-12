"""
Testes de regressão e garantia de qualidade para a Lectoria v1.6.2:
- Otimização da Biblioteca (Cache em memória, invalidação, chunking, alternância de visualização).
- Isolamento e segurança de sessão (limpeza de cache no logout/troca de usuário).
- Integridade do Schema SQLite (permanência estrita no schema v10).
- Integridade das regras de responsividade CSS (ordem de especificidade e máscara de rolagem).
"""
import re
import pytest
import sqlite3
from pathlib import Path
import config
import database
import auth_service

def test_sqlite_schema_version_is_strictly_v10():
    """Garante que a v1.6.2 não altera o schema v10 nem adiciona migrações de banco."""
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;")
        row = cur.fetchone()
        assert row is not None, "Tabela schema_migrations não encontrada."
        assert row["version"] == 10, f"Schema SQLite alterado indevidamente: versão {row['version']} (esperado: 10)."
    finally:
        conn.close()

def test_css_rules_and_cascade_order():
    """Valida a arquitetura e especificidade do CSS da v1.6.2."""
    css_path = Path("static/css/style.css")
    assert css_path.exists(), "style.css não encontrado."
    content = css_path.read_text(encoding="utf-8")

    # 1. Garante ausência de html, body { overflow-x: hidden; } global que causa bugs de rasterização
    assert not re.search(r"html\s*,\s*body\s*\{[^}]*overflow-x\s*:\s*hidden", content), (
        "Encontrado overflow-x: hidden em html/body! Isso degrada sticky headers e causa retângulos não-rasterizados no Chrome Android."
    )

    # 2. Valida máscara de gradiente na navegação horizontal
    assert "-webkit-mask-image: linear-gradient" in content or "mask-image: linear-gradient" in content, (
        "Falta máscara de gradiente na barra de navegação (.main-view-nav)."
    )

    # 3. Valida scroll snap e scroll behavior
    assert "scroll-snap-type: x proximity" in content, "Falta scroll-snap-type em .main-view-nav."
    assert "scroll-snap-align: start" in content, "Falta scroll-snap-align em .main-nav-tab-btn."

    # 4. Valida estilização unificada de Grid/List sem reconstrução de DOM
    assert ".books-list .book-card" in content, "Falta regra CSS unificada .books-list .book-card."

    # 5. Valida contenção de admin-nav-group para evitar overflow do header no mobile
    assert ".admin-nav-group" in content and "flex-wrap: wrap" in content, (
        "Falta flex-wrap: wrap em .admin-nav-group para prevenir overflow em telas estreitas."
    )

def test_js_cache_and_chunking_contract():
    """Valida que o código JavaScript do app.js implementa cache em memória, chunking e limpeza de sessão."""
    js_path = Path("static/js/app.js")
    assert js_path.exists(), "app.js não encontrado."
    content = js_path.read_text(encoding="utf-8")

    # 1. Objeto de cache em memória
    assert "const libraryCache = {" in content, "libraryCache não definido no app.js."
    assert "abortController: null" in content, "AbortController ausente no libraryCache."

    # 2. Funções de invalidação e limpeza de sessão
    assert "function invalidateLibraryCache()" in content, "invalidateLibraryCache ausente."
    assert "function clearUserSessionCache()" in content, "clearUserSessionCache ausente."

    # 3. Limpeza de cache no logout
    assert "clearUserSessionCache();" in content, "clearUserSessionCache não é chamado nas trocas de sessão."

    # 4. Chunking progressivo
    assert "BOOKS_CHUNK_SIZE = 36" in content, "Constante BOOKS_CHUNK_SIZE = 36 não encontrada."
    assert "books-scroll-sentinel" in content, "Elemento sentinela de scroll ausente."
    assert "IntersectionObserver" in content, "IntersectionObserver ausente para scroll progressivo."

    # 5. Alternância pura de Grid/Lista via classe CSS
    assert "booksContainer.className =" in content, "Alternância de classe em booksContainer ausente."

def test_user_session_isolation_cache_helpers():
    """Testa a lógica de isolamento de usuários do auth_service no backend."""
    # Cria dois leitores distintos
    u1 = auth_service.create_user("user_isol_a", "SenhaA123!", role="reader", display_name="Usuário A")
    u2 = auth_service.create_user("user_isol_b", "SenhaB123!", role="reader", display_name="Usuário B")
    assert u1["id"] != u2["id"]

    # Cria sessão para usuário A
    token_a = auth_service.create_session(u1["id"])
    user_a = auth_service.get_user_from_session(token_a)
    assert user_a["id"] == u1["id"]

    # Cria sessão para usuário B
    token_b = auth_service.create_session(u2["id"])
    user_b = auth_service.get_user_from_session(token_b)
    assert user_b["id"] == u2["id"]

    # Destrói sessão do usuário A (logout)
    auth_service.destroy_session(token_a)
    assert auth_service.get_user_from_session(token_a) is None
    # Sessão B continua ativa e intacta
    assert auth_service.get_user_from_session(token_b)["id"] == u2["id"]
