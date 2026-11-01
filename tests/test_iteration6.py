"""
Testes automatizados para a Iteração 6 do Lectoria:
- Download direto de PDFs originais (streaming, headers, integridade, path traversal).
- Gerenciamento de múltiplas fontes de biblioteca (CRUD, validações de segurança, symlinks, preservação no disco).
- Autenticação, perfis de usuário, papéis (admin/reader) e proteção contra força bruta.
- Isolamento estrito de dados por usuário (progresso, destaques, notas, desenhos, coleções).
"""

import time
import pytest
from pathlib import Path
from starlette.testclient import TestClient

import auth_service
import sources_service
import library
import annotations_service
import collections_service
import config
import database
from main import app
from tests.conftest import create_sample_pdf


def test_pdf_download_valid_and_streaming(client: TestClient):
    """Verifica download direto do PDF com headers corretos e integridade de conteúdo."""
    books = library.get_books()
    assert len(books) > 0
    book = books[0]
    book_id = book["id"]

    res = client.get(f"/api/books/{book_id}/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "attachment" in res.headers["content-disposition"]
    assert ".pdf" in res.headers["content-disposition"]

    # Verifica integridade dos bytes baixados comparando com o arquivo original
    real_path = library.resolve_book_path(book_id)
    with open(real_path, "rb") as f:
        expected_bytes = f.read()

    assert res.content == expected_bytes


def test_pdf_download_missing_or_deactivated_source(client: TestClient):
    """Testa download para livros inexistentes ou pertencentes a fontes inativas."""
    # Livro inexistente
    res = client.get("/api/books/nonexistent_id_123/download")
    assert res.status_code == 404

    # Desativa a fonte primária e testa o download
    sources = sources_service.list_sources()
    assert len(sources) > 0
    primary_source_id = sources[0]["id"]

    books = library.get_books(source_id=primary_source_id)
    book_id = books[0]["id"]

    # Desativa fonte
    sources_service.toggle_source_active(primary_source_id)

    res_deactivated = client.get(f"/api/books/{book_id}/download")
    assert res_deactivated.status_code in (403, 404)

    # Reativa a fonte
    sources_service.toggle_source_active(primary_source_id)
    res_reactivated = client.get(f"/api/books/{book_id}/download")
    assert res_reactivated.status_code == 200


def test_pdf_download_path_traversal_prevention(client: TestClient):
    """Garante que tentativas de path traversal no endpoint de download sejam bloqueadas."""
    malicious_ids = [
        "../../../../etc/passwd",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "....//....//etc/passwd",
    ]
    for bad_id in malicious_ids:
        res = client.get(f"/api/books/{bad_id}/download")
        assert res.status_code in (400, 403, 404)


def test_sources_crud_and_security(client: TestClient, tmp_path: Path):
    """Testa cadastro de fontes, bloqueio de diretórios perigosos e varredura."""
    # Listar fontes
    initial_sources = sources_service.list_sources()
    assert len(initial_sources) >= 1

    # Rejeição de diretórios de sistema
    system_paths = ["/", "/etc", "/proc", "/sys", "/dev", "/boot"]
    for sys_p in system_paths:
        with pytest.raises(sources_service.InvalidSourceError):
            sources_service.create_source(path=sys_p, label="Sistema")

    # Rejeição de diretórios internos da aplicação
    internal_paths = [
        str(config.DATA_DIR),
        str(config.BASE_DIR),
        str(config.BASE_DIR / "backups"),
    ]
    for int_p in internal_paths:
        with pytest.raises(sources_service.InvalidSourceError):
            sources_service.create_source(path=int_p, label="Interno")

    # Criar uma nova fonte válida
    extra_dir = tmp_path / "extra_library"
    extra_dir.mkdir(parents=True, exist_ok=True)
    create_sample_pdf(extra_dir / "livro_extra.pdf", "Livro Extra", "Autor Extra", pages=3)

    new_source = sources_service.create_source(path=str(extra_dir), label="Biblioteca Secundária")
    assert new_source["id"] > 0
    assert new_source["is_active"] == 1

    # Varre a nova fonte
    stats = library.scan_library(source_id=new_source["id"])
    assert stats["added"] == 1
    assert stats["total"] >= 1

    # Rejeição de links simbólicos que apontam para fora do diretório da fonte
    outside_file = tmp_path / "secret.pdf"
    create_sample_pdf(outside_file, "Segredo Externo", "Autor Fora", pages=2)
    symlink_target = extra_dir / "leak_symlink.pdf"
    symlink_target.symlink_to(outside_file)

    # Varre novamente: o symlink apontando para fora não deve ser indexado
    stats2 = library.scan_library(source_id=new_source["id"])
    assert stats2["added"] == 0  # Ignorado por violar o escopo da fonte


def test_source_delete_preserves_disk_files(client: TestClient, tmp_path: Path):
    """Garante que a remoção de uma fonte no banco NUNCA apague arquivos PDF físicos do disco."""
    source_dir = tmp_path / "source_to_delete"
    source_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = source_dir / "permanente.pdf"
    create_sample_pdf(pdf_file, "Permanente", "Autor Fiel", pages=4)

    src = sources_service.create_source(path=str(source_dir), label="Temporária")
    library.scan_library(source_id=src["id"])

    # Verifica que o livro foi registrado
    books = library.get_books(source_id=src["id"])
    assert len(books) == 1

    # Remove a fonte
    sources_service.delete_source(src["id"])

    # Verifica que o livro foi removido do catálogo
    books_after = library.get_books(source_id=src["id"])
    assert len(books_after) == 0

    # Confirma que o arquivo físico no disco está 100% intacto
    assert pdf_file.exists()
    assert pdf_file.is_file()
    assert pdf_file.stat().st_size > 0


def test_auth_flow_and_brute_force():
    """Testa criação de conta, login com scrypt, rate-limit contra força bruta e logout."""
    auth_service.clear_failed_logins()

    with TestClient(app) as client:
        # Cria usuário administrador para o teste
        admin_user = auth_service.create_user(
            username="admin_test",
            password="SenhaSegura123!",
            display_name="Admin Teste",
            role="admin",
        )
        assert admin_user["id"] > 0
        assert admin_user["role"] == "admin"

        try:
            # Tentativa com senha incorreta
            res_bad = client.post("/api/auth/login", json={
                "username": "admin_test",
                "password": "SenhaErrada!",
            })
            assert res_bad.status_code == 401

            # Proteção contra força bruta: excede limite de tentativas consecutivas
            for _ in range(5):
                client.post("/api/auth/login", json={
                    "username": "admin_test",
                    "password": "SenhaErrada!",
                })

            # Próxima tentativa deve ser bloqueada por rate-limiting
            res_lockout = client.post("/api/auth/login", json={
                "username": "admin_test",
                "password": "SenhaSegura123!",
            })
            assert res_lockout.status_code == 429
            assert "bloqueado" in res_lockout.json()["detail"].lower()

            # Limpa contadores de falha e realiza login correto
            auth_service.clear_failed_logins()
            res_ok = client.post("/api/auth/login", json={
                "username": "admin_test",
                "password": "SenhaSegura123!",
            })
            assert res_ok.status_code == 200
            assert "user" in res_ok.json()
            assert res_ok.json()["user"]["username"] == "admin_test"

            # Sessão ativa permite consultar /api/auth/me
            res_me = client.get("/api/auth/me")
            assert res_me.status_code == 200
            assert res_me.json()["user"]["username"] == "admin_test"

            # Logout invalida a sessão
            res_logout = client.post("/api/auth/logout")
            assert res_logout.status_code == 200

            # Próxima consulta já não é autenticada como admin_test
            res_me_after = client.get("/api/auth/me")
            # Caso caia no default ou exija login
            if res_me_after.status_code == 200:
                assert res_me_after.json()["user"]["id"] != admin_user["id"]
        finally:
            with database.get_db() as conn:
                conn.cursor().execute("DELETE FROM users WHERE id = ?", (admin_user["id"],))
            auth_service.clear_failed_logins()


def test_admin_endpoint_authorization():
    """Garante que usuários com papel 'reader' não acessem endpoints administrativos."""
    # Cria usuário com papel reader
    reader_user = auth_service.create_user(
        username="leitor_comum",
        password="SenhaLeitor123!",
        display_name="Leitor Comum",
        role="reader",
    )

    try:
        with TestClient(app) as client_reader:
            login_res = client_reader.post("/api/auth/login", json={
                "username": "leitor_comum",
                "password": "SenhaLeitor123!",
            })
            assert login_res.status_code == 200

            # Tentativa de listar fontes como leitor -> 403
            res_sources = client_reader.get("/api/sources")
            assert res_sources.status_code == 403

            # Tentativa de criar fonte como leitor -> 403
            res_create_source = client_reader.post("/api/sources", json={"path": "/tmp", "label": "Teste"})
            assert res_create_source.status_code == 403

            # Tentativa de listar usuários como leitor -> 403
            res_users = client_reader.get("/api/users")
            assert res_users.status_code == 403

            # Tentativa de disparar backup como leitor -> 403
            res_backup = client_reader.post("/api/backup")
            assert res_backup.status_code == 403
    finally:
        auth_service.delete_user(reader_user["id"])


def test_user_data_isolation():
    """
    Verifica que dados pessoais de usuários distintos são completamente isolados:
    - Progresso de leitura
    - Destaques (marca-texto)
    - Notas de estudo
    - Desenhos livres vetoriais em páginas
    - Coleções personalizadas
    """
    user_alice = auth_service.create_user(
        username="alice_books",
        password="PasswordAlice123!",
        display_name="Alice",
        role="reader",
    )
    user_bob = auth_service.create_user(
        username="bob_books",
        password="PasswordBob123!",
        display_name="Bob",
        role="reader",
    )

    try:
        books = library.get_books()
        assert len(books) > 0
        book_id = books[0]["id"]

        client_alice = TestClient(app)
        client_bob = TestClient(app)

        # Login Alice
        res_a = client_alice.post("/api/auth/login", json={"username": "alice_books", "password": "PasswordAlice123!"})
        assert res_a.status_code == 200

        # Login Bob
        res_b = client_bob.post("/api/auth/login", json={"username": "bob_books", "password": "PasswordBob123!"})
        assert res_b.status_code == 200

        # --- 1. Catálogo Compartilhado ---
        books_alice = client_alice.get("/api/books").json()["books"]
        books_bob = client_bob.get("/api/books").json()["books"]
        assert len(books_alice) == len(books_bob)

        # --- 2. Isolamento de Progresso ---
        client_alice.post(f"/api/books/{book_id}/progress", json={"current_page": 8, "total_pages": 10})
        client_bob.post(f"/api/books/{book_id}/progress", json={"current_page": 2, "total_pages": 10})

        prog_alice = client_alice.get(f"/api/books/{book_id}/progress").json()
        prog_bob = client_bob.get(f"/api/books/{book_id}/progress").json()
        assert prog_alice["current_page"] == 8
        assert prog_bob["current_page"] == 2

        # --- 3. Isolamento de Destaques ---
        hl_res = client_alice.post(f"/api/books/{book_id}/highlights", json={
            "page": 3,
            "color": "#fef08a",
            "rects": [{"x": 10, "y": 10, "width": 50, "height": 10}],
            "selected_text": "Trecho destacado por Alice",
        })
        assert hl_res.status_code == 201
        hl_id_alice = hl_res.json()["highlight"]["id"]

        # Bob consulta anotações do livro: não deve ver o destaque de Alice
        bob_notes_summary = client_bob.get(f"/api/books/{book_id}/annotations").json()
        assert len(bob_notes_summary["highlights"]) == 0

        # Bob cria seu próprio destaque
        client_bob.post(f"/api/books/{book_id}/highlights", json={
            "page": 3,
            "color": "#bbf7d0",
            "rects": [{"x": 10, "y": 25, "width": 60, "height": 10}],
            "selected_text": "Trecho destacado por Bob",
        })

        bob_summary_after = client_bob.get(f"/api/books/{book_id}/annotations").json()
        assert len(bob_summary_after["highlights"]) == 1
        assert bob_summary_after["highlights"][0]["selected_text"] == "Trecho destacado por Bob"

        # --- 4. Isolamento de Notas ---
        client_alice.post(f"/api/books/{book_id}/notes", json={
            "page": 4,
            "content": "Anotação secreta de Alice",
        })
        alice_notes = client_alice.get(f"/api/books/{book_id}/annotations").json()["notes"]
        bob_notes = client_bob.get(f"/api/books/{book_id}/annotations").json()["notes"]
        assert len(alice_notes) == 1
        assert alice_notes[0]["content"] == "Anotação secreta de Alice"
        assert len(bob_notes) == 0

        # --- 5. Isolamento de Desenhos Livres ---
        drawing_alice = [
            {"id": "s1", "color": "#ef4444", "width": 3, "points": [{"x": 10, "y": 10}, {"x": 20, "y": 20}]}
        ]
        drawing_bob = [
            {"id": "s2", "color": "#3b82f6", "width": 4, "points": [{"x": 50, "y": 50}, {"x": 60, "y": 60}]}
        ]

        client_alice.put(f"/api/books/{book_id}/pages/1/drawing", json={"strokes": drawing_alice})
        client_bob.put(f"/api/books/{book_id}/pages/1/drawing", json={"strokes": drawing_bob})

        strokes_alice = client_alice.get(f"/api/books/{book_id}/pages/1/drawing").json()["strokes"]
        strokes_bob = client_bob.get(f"/api/books/{book_id}/pages/1/drawing").json()["strokes"]

        assert len(strokes_alice) == 1
        assert strokes_alice[0]["id"] == "s1"
        assert len(strokes_bob) == 1
        assert strokes_bob[0]["id"] == "s2"

        # Alice limpa seu desenho: o de Bob permanece intacto
        client_alice.delete(f"/api/books/{book_id}/pages/1/drawing")
        assert len(client_alice.get(f"/api/books/{book_id}/pages/1/drawing").json()["strokes"]) == 0
        assert len(client_bob.get(f"/api/books/{book_id}/pages/1/drawing").json()["strokes"]) == 1

        # --- 6. Isolamento de Coleções ---
        col_a_res = client_alice.post("/api/collections", json={"name": "Coleção Privada Alice"})
        assert col_a_res.status_code == 201

        col_b_res = client_bob.post("/api/collections", json={"name": "Coleção Privada Bob"})
        assert col_b_res.status_code == 201

        cols_alice = client_alice.get("/api/collections").json()["collections"]
        cols_bob = client_bob.get("/api/collections").json()["collections"]

        assert len(cols_alice) == 1
        assert cols_alice[0]["name"] == "Coleção Privada Alice"
        assert len(cols_bob) == 1
        assert cols_bob[0]["name"] == "Coleção Privada Bob"
    finally:
        auth_service.delete_user(user_alice["id"])
        auth_service.delete_user(user_bob["id"])
