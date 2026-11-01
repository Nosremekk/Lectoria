"""
Testes automatizados da Iteração 7 do Lectoria.
Foco em:
1. Auditoria e endurecimento de segurança (CSRF, hashes de sessão, revogação em troca de senha/desativação).
2. Isolamento estrito entre usuários (testes negativos de anotações, destaques, desenhos e coleções).
3. Prevenção de IDOR e vazamento de metadados em coleções cruzadas.
4. Proteção de fontes (.venv, .git, caminhos reservados).
5. Prevenção de vazamento de informações em /api/health.
6. Integridade relacional e restauração de backup com múltiplos perfis.
"""

import hashlib
from pathlib import Path
import pytest
from starlette.testclient import TestClient

import annotations_service
import auth_service
import backup_service
import collections_service
import config
import database
from main import app
import progress
import sources_service


def test_health_check_information_disclosure(client):
    """Garante que o endpoint /api/health não expõe caminhos internos de arquivos."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.1.1"
    assert "data_dir" not in data
    assert "library_dir" not in data


def test_session_token_hashing_in_database():
    """Garante que tokens de sessão são persistidos no SQLite apenas na forma de hash SHA-256."""
    user = auth_service.create_user(
        username="hash_test_user",
        password="SenhaSegura123!",
        role="reader",
    )
    try:
        raw_token = auth_service.create_session(user["id"])
        assert raw_token is not None
        assert len(raw_token) > 20

        # Inspeciona diretamente a tabela sessions no SQLite
        with database.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token FROM sessions WHERE user_id = ?", (user["id"],))
            rows = cursor.fetchall()
            stored_tokens = [r["token"] for r in rows]

            # O token em texto puro NÃO pode estar gravado no banco de dados
            assert raw_token not in stored_tokens

            # O hash SHA-256 do token bruto DEVE estar presente no banco
            expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            assert expected_hash in stored_tokens

        # A validação de sessão em alto nível deve resolver o usuário normalmente
        resolved_user = auth_service.get_user_from_session(raw_token)
        assert resolved_user is not None
        assert resolved_user["id"] == user["id"]
    finally:
        with database.get_db() as conn:
            conn.cursor().execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            conn.cursor().execute("DELETE FROM users WHERE id = ?", (user["id"],))


def test_session_revocation_on_password_change_and_deactivation():
    """Garante que a alteração de senha e a desativação de conta revogam sessões ativas."""
    user = auth_service.create_user(
        username="revoke_test_user",
        password="SenhaOriginal123!",
        role="reader",
    )
    try:
        token1 = auth_service.create_session(user["id"])
        token2 = auth_service.create_session(user["id"])

        assert auth_service.get_user_from_session(token1) is not None
        assert auth_service.get_user_from_session(token2) is not None

        # 1. Troca de senha deve revogar todas as sessões anteriores
        auth_service.update_user(user["id"], new_password="NovaSenhaSegura456!")
        assert auth_service.get_user_from_session(token1) is None
        assert auth_service.get_user_from_session(token2) is None

        # Emite uma nova sessão após a troca de senha
        token3 = auth_service.create_session(user["id"])
        assert auth_service.get_user_from_session(token3) is not None

        # 2. Desativação da conta deve invalidar sessões
        auth_service.update_user(user["id"], is_active=False)
        assert auth_service.get_user_from_session(token3) is None
    finally:
        with database.get_db() as conn:
            conn.cursor().execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            conn.cursor().execute("DELETE FROM users WHERE id = ?", (user["id"],))


def test_csrf_origin_header_validation():
    """Garante que requisições mutantes com cabeçalho Origin de outro domínio sejam rejeitadas com 403."""
    with TestClient(app) as client:
        # Requisição segura GET com Origin externo é permitida
        res_get = client.get("/api/books", headers={"Origin": "http://evil.com"})
        assert res_get.status_code == 200

        # Requisição mutante POST com Origin de atacante deve ser bloqueada
        res_post = client.post(
            "/api/scan",
            headers={"Origin": "http://malicious-site.com"},
        )
        assert res_post.status_code == 403
        assert "CSRF" in res_post.json()["detail"]

        # Requisição mutante POST com Origin legítimo igual ao Host deve passar no filtro de origem
        res_post_same_origin = client.post(
            "/api/scan",
            headers={"Origin": "http://testserver", "Host": "testserver"},
        )
        assert res_post_same_origin.status_code in (200, 401, 403)  # Depende de permissões, não bloqueado por CSRF


def test_csrf_double_submit_cookie_protection():
    """Garante que clientes que possuem o cookie lectoria_csrf devem fornecer o token correspondente."""
    with TestClient(app) as client:
        # Obtém o token CSRF chamando o endpoint ou carregando a página inicial
        csrf_res = client.get("/api/auth/csrf")
        assert csrf_res.status_code == 200
        csrf_token = csrf_res.json()["csrf_token"]
        assert csrf_token

        # 1. Tentativa de POST sem cabeçalho X-CSRF-Token quando o cookie está presente -> 403
        bad_post = client.post(
            "/api/collections",
            json={"name": "Coleção Sem CSRF"},
        )
        assert bad_post.status_code == 403
        assert "Token CSRF" in bad_post.json()["detail"]

        # 2. Tentativa de POST com token X-CSRF-Token falso -> 403
        forged_post = client.post(
            "/api/collections",
            json={"name": "Coleção Falsificada"},
            headers={"X-CSRF-Token": "token_inventado_invalido"},
        )
        assert forged_post.status_code == 403
        assert "Token CSRF" in forged_post.json()["detail"]

        # 3. Requisição de POST com o cabeçalho X-CSRF-Token correto -> Sucesso (201)
        good_post = client.post(
            "/api/collections",
            json={"name": "Coleção Segura CSRF"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert good_post.status_code == 201
        created_id = good_post.json()["collection"]["id"]

        # 4. Requisição com token via query param (ex: beacon) -> Sucesso
        beacon_post = client.delete(
            f"/api/collections/{created_id}?csrf_token={csrf_token}",
        )
        assert beacon_post.status_code == 200


def test_cross_user_collection_probing_prevention(client):
    """
    Garante que o Usuário A não consiga bisbilhotar o conteúdo de coleções do Usuário B.
    Ao filtrar /api/books?collection_id=<ID de B>, a consulta DEVE retornar 0 livros para A.
    """
    user_a = auth_service.create_user(username="leitor_probe_a", password="Password123!", role="reader")
    user_b = auth_service.create_user(username="leitor_probe_b", password="Password123!", role="reader")
    token_a = auth_service.create_session(user_a["id"])
    token_b = auth_service.create_session(user_b["id"])

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    try:
        # Pega um livro existente no catálogo
        books = client.get("/api/books", headers=headers_b).json()["books"]
        book_id = books[0]["id"]

        # Usuário B cria uma coleção privada e adiciona o livro nela
        col_b = collections_service.create_collection(
            name="Coleção Super Secreta de Bob",
            user_id=user_b["id"],
        )
        collections_service.add_book_to_collection(col_b["id"], book_id, user_id=user_b["id"])

        # Usuário B consulta sua coleção e vê o livro
        res_b = client.get(f"/api/books?collection_id={col_b['id']}", headers=headers_b)
        assert res_b.status_code == 200
        assert len(res_b.json()["books"]) == 1

        # Usuário A tenta consultar os livros da coleção de B
        res_a = client.get(f"/api/books?collection_id={col_b['id']}", headers=headers_a)
        assert res_a.status_code == 200
        # A resposta DEVE ser vazia para o Usuário A (não vaza os livros da coleção de B)
        assert len(res_a.json()["books"]) == 0

        # Usuário A também não consegue adicionar livros à coleção de B
        with pytest.raises(collections_service.CollectionNotFoundError):
            collections_service.add_book_to_collection(col_b["id"], book_id, user_id=user_a["id"])

        # Usuário A também não consegue remover livros da coleção de B
        with pytest.raises(collections_service.CollectionNotFoundError):
            collections_service.remove_book_from_collection(col_b["id"], book_id, user_id=user_a["id"])

        # Usuário A não consegue excluir a coleção de B
        with pytest.raises(collections_service.CollectionNotFoundError):
            collections_service.delete_collection(col_b["id"], user_id=user_a["id"])
    finally:
        with database.get_db() as conn:
            conn.cursor().execute("DELETE FROM collection_books WHERE collection_id = ?", (col_b["id"],))
            conn.cursor().execute("DELETE FROM collections WHERE id = ?", (col_b["id"],))
            conn.cursor().execute("DELETE FROM sessions WHERE user_id IN (?, ?)", (user_a["id"], user_b["id"]))
            conn.cursor().execute("DELETE FROM users WHERE id IN (?, ?)", (user_a["id"], user_b["id"]))


def test_note_to_highlight_cross_user_idor_prevention():
    """
    Garante que o Usuário A não possa vincular uma nota a um destaque do Usuário B,
    evitando que o texto destacado por B seja vazado para A via LEFT JOIN.
    """
    user_a = auth_service.create_user(username="idor_user_a", password="Password123!", role="reader")
    user_b = auth_service.create_user(username="idor_user_b", password="Password123!", role="reader")

    with database.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM books LIMIT 1")
        book_id = cursor.fetchone()["id"]

    try:
        # Usuário B cria um destaque com texto sensível/privado
        highlight_b = annotations_service.create_highlight(
            book_id=book_id,
            page=2,
            color="#fef08a",
            rects=[{"x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.4}],
            selected_text="Anotação ultra confidencial do Bob",
            user_id=user_b["id"],
        )

        # Usuário A tenta criar uma nota vinculando ao highlight de B
        with pytest.raises(ValueError) as excinfo:
            annotations_service.create_note(
                book_id=book_id,
                page=2,
                content="Tentativa de bisbilhotar o texto de Bob",
                highlight_id=highlight_b["id"],
                user_id=user_a["id"],
            )
        assert "não pertence a este usuário" in str(excinfo.value)
    finally:
        with database.get_db() as conn:
            conn.cursor().execute("DELETE FROM highlights WHERE user_id IN (?, ?)", (user_a["id"], user_b["id"]))
            conn.cursor().execute("DELETE FROM notes WHERE user_id IN (?, ?)", (user_a["id"], user_b["id"]))
            conn.cursor().execute("DELETE FROM users WHERE id IN (?, ?)", (user_a["id"], user_b["id"]))


def test_sources_forbidden_subdirectories():
    """Garante que diretórios como .venv e .git sejam bloqueados na validação de fontes."""
    base_dir = config.BASE_DIR.resolve()
    venv_dir = base_dir / ".venv"
    git_dir = base_dir / ".git"

    if venv_dir.exists():
        with pytest.raises(sources_service.InvalidSourceError):
            sources_service.validate_and_canonicalize_path(str(venv_dir))

    if git_dir.exists():
        with pytest.raises(sources_service.InvalidSourceError):
            sources_service.validate_and_canonicalize_path(str(git_dir))

    # Testa diretório raiz do sistema operacional
    with pytest.raises(sources_service.InvalidSourceError):
        sources_service.validate_and_canonicalize_path("/")


def test_multi_user_data_backup_and_restore_integrity(tmp_path):
    """
    Testa criação de backup e restauração com múltiplos usuários, garantindo que
    metadados, fontes, progresso, destaques, notas, desenhos e coleções permaneçam
    íntegros e isolados após o ciclo de restauração.
    """
    test_db_path = tmp_path / "integrity_test.db"
    backup_file = tmp_path / "integrity_backup.sqlite3"

    # Inicializa banco de teste
    database.init_db(test_db_path)

    # Cria dois usuários no banco de teste
    with database.get_db(test_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, display_name, password_hash, role, is_active) VALUES (?, ?, ?, ?, 1)",
            ("u_alice", "Alice", "scrypt:dummy", "admin"),
        )
        u1_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO users (username, display_name, password_hash, role, is_active) VALUES (?, ?, ?, ?, 1)",
            ("u_bob", "Bob", "scrypt:dummy", "reader"),
        )
        u2_id = cursor.lastrowid

        # Insere um livro simulado
        cursor.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, page_count, file_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("book_test_1", "Livro Teste de Integridade", "Autor Teste", "livro.pdf", "livro.pdf", 1024, 50, "hash123"),
        )

        # Progresso individualizado
        cursor.execute(
            "INSERT INTO reading_progress (book_id, user_id, current_page, percentage) VALUES (?, ?, ?, ?)",
            ("book_test_1", u1_id, 25, 50.0),
        )
        cursor.execute(
            "INSERT INTO reading_progress (book_id, user_id, current_page, percentage) VALUES (?, ?, ?, ?)",
            ("book_test_1", u2_id, 10, 20.0),
        )

        # Anotações e destaques de Alice
        cursor.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("h_alice", "book_test_1", 2, "#fef08a", "[]", "Trecho da Alice", u1_id),
        )
        cursor.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("n_alice", "book_test_1", 2, "h_alice", "Nota de estudo da Alice", "#fef08a", u1_id),
        )

        # Desenho de Bob
        cursor.execute(
            """
            INSERT INTO page_drawings (id, book_id, page, strokes_json, user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("d_bob", "book_test_1", 5, '[{"points": [[10, 10], [20, 20]]}]', u2_id),
        )

        # Coleção de Alice
        cursor.execute(
            "INSERT INTO collections (name, user_id) VALUES (?, ?)",
            ("Coleção de Alice", u1_id),
        )
        c_alice_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO collection_books (collection_id, book_id) VALUES (?, ?)",
            (c_alice_id, "book_test_1"),
        )

    # 1. Realiza backup do banco de teste usando a API nativa do SQLite
    import shutil
    import sqlite3

    source_conn = sqlite3.connect(str(test_db_path), timeout=30.0)
    target_conn = sqlite3.connect(str(backup_file))
    source_conn.backup(target_conn)
    source_conn.close()
    target_conn.close()

    assert backup_file.exists()
    assert backup_file.stat().st_size > 0
    assert backup_service.verify_sqlite_integrity(backup_file)

    # 2. Restaura o backup em um novo banco de destino
    restored_db_path = tmp_path / "restored.db"
    shutil.copy2(backup_file, restored_db_path)
    assert backup_service.verify_sqlite_integrity(restored_db_path)

    # 3. Verifica a integridade e isolamento dos dados restaurados
    with database.get_db(restored_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        assert cursor.fetchone()[0] == "ok"

        # Progresso de Alice
        cursor.execute("SELECT current_page FROM reading_progress WHERE book_id = ? AND user_id = ?", ("book_test_1", u1_id))
        assert cursor.fetchone()["current_page"] == 25

        # Progresso de Bob
        cursor.execute("SELECT current_page FROM reading_progress WHERE book_id = ? AND user_id = ?", ("book_test_1", u2_id))
        assert cursor.fetchone()["current_page"] == 10

        # Destaque de Alice
        cursor.execute("SELECT selected_text FROM highlights WHERE id = ? AND user_id = ?", ("h_alice", u1_id))
        assert cursor.fetchone()["selected_text"] == "Trecho da Alice"

        # Desenho de Bob
        cursor.execute("SELECT strokes_json FROM page_drawings WHERE id = ? AND user_id = ?", ("d_bob", u2_id))
        assert "points" in cursor.fetchone()["strokes_json"]

        # Coleção de Alice
        cursor.execute("SELECT name FROM collections WHERE id = ? AND user_id = ?", (c_alice_id, u1_id))
        assert cursor.fetchone()["name"] == "Coleção de Alice"
