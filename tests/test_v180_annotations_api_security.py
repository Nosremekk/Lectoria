"""
Suíte de Testes da Fase 2 da Lectoria v1.8.0 — API REST, Validação Pydantic e Segurança
das Anotações Colaborativas, Desenhos Compartilhados e Marcadores Pessoais.

Cobre os 15 cenários obrigatórios da Fase 2:
1. Autenticação obrigatória (`401`) em todas as rotas de leitura e escrita.
2. Proteção CSRF (`403`) em todas as rotas mutáveis (`POST`, `PUT`, `PATCH`, `DELETE`) com sessão em cookie.
3. Rejeição de Mass Assignment (`ConfigDict(extra="forbid")` -> `422`) em todos os payloads.
4. Criação privada por padrão (`visibility="private"`, invisível para terceiros).
5. Publicação explícita via `PATCH` (`private` -> `public` para destaques, notas e desenhos).
6. Revogação explícita via `PATCH` (`public` -> `private`, remoção imediata para terceiros e `Cache-Control: private, no-store`).
7. Escopos `mine`, `public` e `all_visible` (sem duplicatas das próprias anotações públicas).
8. Anti-IDOR entre múltiplos usuários (bloqueio de edição, exclusão, alteração de visibilidade e vínculo cruzado de `highlight_id`).
9. Acesso negado a livros não autorizados (inexistente `404`, ausente `404`, fonte inativa `403`, usuário inativo `401`).
10. Metadados públicos de autoria sem vazamento de credenciais ou caminhos internos.
11. Desenhos próprios e públicos (`own_drawing` + `public_drawings`, preservação de visibilidade, validação geométrica).
12. Destaques e notas EPUB CFI via API REST (criação, herança de CFI, filtro por `epub_cfi`, validação de formato).
13. Marcadores pessoais (`GET`, `POST`, `DELETE /api/books/{book_id}/bookmarks`, idempotência e isolamento).
14. Busca textual (`search`), ordenação (`sort=position|recent`) e paginação (`limit`, `offset`).
15. Compatibilidade retroativa integral com os contratos da v1.7.0.
"""

from pathlib import Path
from typing import Optional, Tuple
import pytest
from fastapi.testclient import TestClient

import auth_service
import config
import database
import main as app_main


@pytest.fixture
def phase2_api_env(tmp_path):
    """
    Ambiente isolado com banco SQLite v12 temporário, 3 usuários ativos, 1 usuário inativo,
    2 fontes (1 ativa e 1 inativa) e 4 livros (PDF ativo, EPUB ativo, outro PDF ativo, PDF de fonte inativa e PDF ausente).
    """
    db_path = tmp_path / "lectoria_v180_phase2.db"
    covers_dir = tmp_path / "covers"
    lib_dir = tmp_path / "library"
    lib_dir_inactive = tmp_path / "library_inactive"
    backups_dir = tmp_path / "backups"
    covers_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    lib_dir_inactive.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    (lib_dir / "livro_pdf_1.pdf").write_bytes(b"%PDF-1.4 sample 1")
    (lib_dir / "livro_pdf_2.pdf").write_bytes(b"%PDF-1.4 sample 2")
    (lib_dir / "livro_epub_1.epub").write_bytes(b"PK\x03\x04 sample epub")
    (lib_dir_inactive / "livro_inativo.pdf").write_bytes(b"%PDF-1.4 sample inactive")

    old_db, old_cov, old_lib, old_bak = (
        config.DB_PATH,
        config.COVERS_DIR,
        config.LIBRARY_DIR,
        config.BACKUPS_DIR,
    )
    config.DB_PATH = db_path
    config.COVERS_DIR = covers_dir
    config.LIBRARY_DIR = lib_dir
    config.BACKUPS_DIR = backups_dir

    database.init_db(db_path)

    alice = auth_service.create_user(
        username="alice_v180",
        password="Password123!",
        display_name="Alice Lemos",
        role="admin",
    )
    bob = auth_service.create_user(
        username="bob_v180",
        password="Password123!",
        display_name="Bob Ferreira",
        role="reader",
    )
    carlos = auth_service.create_user(
        username="carlos_v180",
        password="Password123!",
        display_name="Carlos Dias",
        role="reader",
    )
    inativo = auth_service.create_user(
        username="inativo_v180",
        password="Password123!",
        display_name="Leitor Inativo",
        role="reader",
    )

    conn = database.get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO user_profiles (user_id, display_name, avatar_color, bio) VALUES
            (?, 'Alice Lemos', '#e11d48', 'Bio Alice'),
            (?, 'Bob Ferreira', '#2563eb', 'Bio Bob'),
            (?, 'Carlos Dias', '#16a34a', 'Bio Carlos');
            """,
            (alice["id"], bob["id"], carlos["id"]),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO library_sources (id, name, path, is_active) VALUES
            (1, 'Fonte Principal', ?, 1),
            (2, 'Fonte Desativada', ?, 0);
            """,
            (str(lib_dir), str(lib_dir_inactive)),
        )
        cur.execute(
            """
            INSERT INTO books (id, title, author, filename, rel_path, filesize, format, page_count, source_id, is_missing) VALUES
            ('book_pdf_1', 'Dom Casmurro', 'Machado de Assis', 'livro_pdf_1.pdf', 'livro_pdf_1.pdf', 10240, 'pdf', 200, 1, 0),
            ('book_pdf_2', 'Quincas Borba', 'Machado de Assis', 'livro_pdf_2.pdf', 'livro_pdf_2.pdf', 10240, 'pdf', 180, 1, 0),
            ('book_epub_1', 'Meditações', 'Marco Aurélio', 'livro_epub_1.epub', 'livro_epub_1.epub', 8192, 'epub', 120, 1, 0),
            ('book_inactive_src', 'Livro Fonte Inativa', 'Autor X', 'livro_inativo.pdf', 'livro_inativo.pdf', 4096, 'pdf', 50, 2, 0),
            ('book_missing_file', 'Livro Ausente', 'Autor Y', 'ausente.pdf', 'ausente.pdf', 4096, 'pdf', 50, 1, 1);
            """
        )
        conn.commit()
    finally:
        conn.close()

    user_map = {
        "alice": alice["id"],
        "bob": bob["id"],
        "carlos": carlos["id"],
        "inativo": inativo["id"],
    }

    def make_client(user_key: Optional[str], include_csrf: bool = True) -> Tuple[TestClient, str]:
        client = TestClient(app_main.app)
        if not user_key:
            return client, ""
        uid = user_map[user_key]
        token = auth_service.create_session(uid)
        client.cookies.set(auth_service.SESSION_COOKIE_NAME, token)
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        if include_csrf:
            client.headers["x-csrf-token"] = csrf
        return client, csrf

    try:
        yield {
            "make_client": make_client,
            "alice": alice,
            "bob": bob,
            "carlos": carlos,
            "inativo": inativo,
            "db_path": db_path,
        }
    finally:
        config.DB_PATH, config.COVERS_DIR, config.LIBRARY_DIR, config.BACKUPS_DIR = (
            old_db,
            old_cov,
            old_lib,
            old_bak,
        )


# =========================================================================
# 1 & 2. AUTENTICAÇÃO OBRIGATÓRIA (401) E PROTEÇÃO CSRF (403)
# =========================================================================

def test_authentication_required_on_all_annotation_endpoints(phase2_api_env):
    """Garante que requisições sem sessão autenticada recebem HTTP 401 em todas as rotas de anotações."""
    anon_client, _ = phase2_api_env["make_client"](None)
    # Sem cookie de sessão, mesmo em ambiente de teste, ao forçar cookie inválido deve retornar 401
    anon_client.cookies.set(auth_service.SESSION_COOKIE_NAME, "invalid_or_expired_session_token")
    csrf = anon_client.get("/api/auth/csrf").json()["csrf_token"]
    anon_client.headers["x-csrf-token"] = csrf

    endpoints = [
        ("GET", "/api/books/book_pdf_1/annotations", None),
        ("GET", "/api/books/book_pdf_1/pages/1/annotations", None),
        ("POST", "/api/books/book_pdf_1/highlights", {"page": 1, "selected_text": "Trecho", "rects": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}]}),
        ("PATCH", "/api/books/book_pdf_1/highlights/hl-1/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/highlights/hl-1", None),
        ("POST", "/api/books/book_pdf_1/notes", {"page": 1, "content": "Nota"}),
        ("PUT", "/api/books/book_pdf_1/notes/nt-1", {"content": "Nota editada"}),
        ("PATCH", "/api/books/book_pdf_1/notes/nt-1/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/notes/nt-1", None),
        ("GET", "/api/books/book_pdf_1/pages/1/drawing", None),
        ("PUT", "/api/books/book_pdf_1/pages/1/drawing", {"strokes": []}),
        ("PATCH", "/api/books/book_pdf_1/pages/1/drawing/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/pages/1/drawing", None),
        ("GET", "/api/books/book_pdf_1/bookmarks", None),
        ("POST", "/api/books/book_pdf_1/bookmarks", {"page": 1, "label": "Marcador"}),
        ("DELETE", "/api/books/book_pdf_1/bookmarks/bm-1", None),
    ]

    for method, url, payload in endpoints:
        res = anon_client.request(method, url, json=payload) if payload is not None else anon_client.request(method, url)
        assert res.status_code == 401, f"Esperado 401 em {method} {url}, obteve {res.status_code}"


def test_csrf_required_on_all_mutable_annotation_endpoints(phase2_api_env):
    """Garante que todas as operações de escrita exigem cabeçalho x-csrf-token válido (HTTP 403 se ausente)."""
    alice_no_csrf, _ = phase2_api_env["make_client"]("alice", include_csrf=False)

    mutable_calls = [
        ("POST", "/api/books/book_pdf_1/highlights", {"page": 1, "selected_text": "Texto", "rects": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}]}),
        ("PATCH", "/api/books/book_pdf_1/highlights/hl-1/visibility", {"visibility": "public"}),
        ("PATCH", "/api/highlights/hl-1/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/highlights/hl-1", None),
        ("POST", "/api/books/book_pdf_1/notes", {"page": 1, "content": "Nota"}),
        ("PUT", "/api/books/book_pdf_1/notes/nt-1", {"content": "Nota 2"}),
        ("PATCH", "/api/books/book_pdf_1/notes/nt-1/visibility", {"visibility": "public"}),
        ("PATCH", "/api/notes/nt-1/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/notes/nt-1", None),
        ("PUT", "/api/books/book_pdf_1/pages/1/drawing", {"strokes": []}),
        ("PATCH", "/api/books/book_pdf_1/pages/1/drawing/visibility", {"visibility": "public"}),
        ("DELETE", "/api/books/book_pdf_1/pages/1/drawing", None),
        ("POST", "/api/books/book_pdf_1/bookmarks", {"page": 1, "label": "Marcador"}),
        ("DELETE", "/api/books/book_pdf_1/bookmarks/bm-1", None),
    ]

    for method, url, payload in mutable_calls:
        res = alice_no_csrf.request(method, url, json=payload) if payload is not None else alice_no_csrf.request(method, url)
        assert res.status_code == 403, f"Esperado 403 CSRF em {method} {url}, obteve {res.status_code}"


# =========================================================================
# 3. EXTRA="FORBID" E VALIDAÇÃO DE ENTRADA (MASS ASSIGNMENT PROTECTION)
# =========================================================================

def test_pydantic_extra_forbid_and_validation_limits(phase2_api_env):
    """Rejeita campos arbitrários (Mass Assignment), visibilidades inválidas (`club`) e limites excedidos."""
    alice, _ = phase2_api_env["make_client"]("alice")

    # Tentativa de injetar user_id ou campos desconhecidos
    res_hl_extra = alice.post(
        "/api/books/book_pdf_1/highlights",
        json={
            "page": 1,
            "selected_text": "Trecho válido",
            "rects": [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}],
            "user_id": 999,
        },
    )
    assert res_hl_extra.status_code == 422

    res_note_extra = alice.post(
        "/api/books/book_pdf_1/notes",
        json={"page": 1, "content": "Nota", "author_display_name": "Admin Fake"},
    )
    assert res_note_extra.status_code == 422

    res_upd_extra = alice.put(
        "/api/books/book_pdf_1/notes/nt-1",
        json={"content": "Nota", "is_own": True},
    )
    assert res_upd_extra.status_code == 422

    res_draw_extra = alice.put(
        "/api/books/book_pdf_1/pages/1/drawing",
        json={"strokes": [], "user_id": 999},
    )
    assert res_draw_extra.status_code == 422

    res_vis_extra = alice.patch(
        "/api/books/book_pdf_1/highlights/hl-1/visibility",
        json={"visibility": "public", "club_id": 1},
    )
    assert res_vis_extra.status_code == 422

    res_bm_extra = alice.post(
        "/api/books/book_pdf_1/bookmarks",
        json={"page": 1, "label": "Marcador", "visibility": "public"},
    )
    assert res_bm_extra.status_code == 422

    # Visibilidade inválida ('club', 'friends', etc.)
    for bad_vis in ("club", "friends", "global", ""):
        res_bad_vis = alice.patch(
            "/api/books/book_pdf_1/highlights/hl-1/visibility",
            json={"visibility": bad_vis},
        )
        assert res_bad_vis.status_code == 422

    # Escopo inválido na query string
    res_bad_scope = alice.get("/api/books/book_pdf_1/annotations?scope=club")
    assert res_bad_scope.status_code == 422

    # Página inválida (< 1)
    assert alice.get("/api/books/book_pdf_1/pages/0/annotations").status_code == 400
    assert alice.get("/api/books/book_pdf_1/pages/-2/drawing").status_code == 400


# =========================================================================
# 4, 5, 6, 7 & 10. CICLO DE VIDA DE VISIBILIDADE, ESCOPOS E METADADOS DE AUTORIA
# =========================================================================

def test_visibility_lifecycle_scopes_and_author_metadata(phase2_api_env):
    """
    Valida:
    - Criação privada por padrão ('private').
    - Publicação explícita via PATCH ('public').
    - Consulta nos escopos 'mine', 'public' e 'all_visible' sem duplicatas.
    - Metadados públicos de autoria seguros e flags de permissão ('is_own', 'can_edit', etc.).
    - Revogação explícita via PATCH ('public' -> 'private') com remoção imediata para terceiros.
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    # 1. Alice cria destaque, nota e desenho (sem informar visibility -> 'private' por padrão)
    hl_res = alice.post(
        "/api/books/book_pdf_1/highlights",
        json={
            "page": 5,
            "color": "#fef08a",
            "rects": [{"x": 0.12, "y": 0.25, "w": 0.45, "h": 0.03}],
            "selected_text": "Passagem privada de Alice na página 5.",
            "chapter_label": "Capítulo V",
        },
    )
    assert hl_res.status_code == 201
    hl = hl_res.json()["highlight"]
    assert hl["visibility"] == "private"
    assert hl["is_own"] is True
    assert hl["can_edit"] is True
    assert hl["can_delete"] is True
    assert hl["can_toggle_visibility"] is True
    assert hl["type"] == "highlight"

    nt_res = alice.post(
        "/api/books/book_pdf_1/notes",
        json={
            "page": 5,
            "content": "Reflexão privada de Alice sobre o Capítulo V.",
            "highlight_id": hl["id"],
        },
    )
    assert nt_res.status_code == 201
    nt = nt_res.json()["note"]
    assert nt["visibility"] == "private"
    assert nt["chapter_label"] == "Capítulo V"  # herdado do destaque vinculado
    assert nt["type"] == "note"

    dr_res = alice.put(
        "/api/books/book_pdf_1/pages/5/drawing",
        json={
            "strokes": [
                {"color": "#e11d48", "width": 2.5, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.3, "y": 0.3}]}
            ]
        },
    )
    assert dr_res.status_code == 200
    assert dr_res.json()["visibility"] == "private"

    # 2. Bob consulta 'mine', 'public' e 'all_visible' -> não deve ver nada de Alice enquanto privado
    for scope in ("mine", "public", "all_visible"):
        bob_sum = bob.get(f"/api/books/book_pdf_1/annotations?scope={scope}").json()
        assert bob_sum["highlights"] == []
        assert bob_sum["notes"] == []
        assert bob_sum["drawing_pages"] == []

        bob_page = bob.get(f"/api/books/book_pdf_1/pages/5/annotations?scope={scope}").json()
        assert bob_page["highlights"] == []
        assert bob_page["notes"] == []
        assert bob_page["public_drawings"] == []

    # 3. Alice publica explicitamente o destaque, a nota e o desenho via PATCH
    pub_hl = alice.patch(
        f"/api/books/book_pdf_1/highlights/{hl['id']}/visibility",
        json={"visibility": "public"},
    )
    assert pub_hl.status_code == 200
    assert pub_hl.json()["highlight"]["visibility"] == "public"

    pub_nt = alice.patch(
        f"/api/books/book_pdf_1/notes/{nt['id']}/visibility",
        json={"visibility": "public"},
    )
    assert pub_nt.status_code == 200
    assert pub_nt.json()["note"]["visibility"] == "public"

    pub_dr = alice.patch(
        "/api/books/book_pdf_1/pages/5/drawing/visibility",
        json={"visibility": "public"},
    )
    assert pub_dr.status_code == 200
    assert pub_dr.json()["drawing"]["visibility"] == "public"

    # 4. Bob consulta escopo 'mine' -> continua vazio para Bob
    bob_mine = bob.get("/api/books/book_pdf_1/annotations?scope=mine").json()
    assert bob_mine["highlights"] == []
    assert bob_mine["notes"] == []
    assert bob_mine["drawing_pages"] == []

    # 5. Bob consulta escopo 'public' e 'all_visible' -> vê os itens públicos de Alice com metadados seguros
    for scope in ("public", "all_visible"):
        res_bob = bob.get(f"/api/books/book_pdf_1/annotations?scope={scope}")
        assert res_bob.status_code == 200
        assert "no-store" in res_bob.headers.get("Cache-Control", "")
        data_bob = res_bob.json()
        assert len(data_bob["highlights"]) == 1
        assert len(data_bob["notes"]) == 1
        assert len(data_bob["drawing_pages"]) == 1

        pub_h_item = data_bob["highlights"][0]
        assert pub_h_item["id"] == hl["id"]
        assert pub_h_item["author_user_id"] == phase2_api_env["alice"]["id"]
        assert pub_h_item["author_display_name"] == "Alice Lemos"
        assert pub_h_item["author_avatar_color"] == "#e11d48"
        assert pub_h_item["is_own"] is False
        assert pub_h_item["can_edit"] is False
        assert pub_h_item["can_delete"] is False
        assert pub_h_item["can_toggle_visibility"] is False

        # Verifica ausência de vazamento de campos sensíveis
        for forbidden_key in ("password_hash", "token_hash", "session_token", "rel_path", "filepath"):
            assert forbidden_key not in pub_h_item

    # 6. Alice consulta 'all_visible' -> suas anotações públicas não são duplicadas
    alice_all = alice.get("/api/books/book_pdf_1/annotations?scope=all_visible").json()
    assert len(alice_all["highlights"]) == 1
    assert len(alice_all["notes"]) == 1
    assert len(alice_all["drawing_pages"]) == 1

    # 7. Alice revoga a visibilidade de volta para 'private'
    assert alice.patch(f"/api/highlights/{hl['id']}/visibility", json={"visibility": "private"}).status_code == 200
    assert alice.patch(f"/api/notes/{nt['id']}/visibility", json={"visibility": "private"}).status_code == 200
    assert alice.patch("/api/books/book_pdf_1/pages/5/drawing/visibility", json={"visibility": "private"}).status_code == 200

    # Bob deixa imediatamente de vê-las em 'public' e 'all_visible'
    for scope in ("public", "all_visible"):
        bob_after = bob.get(f"/api/books/book_pdf_1/annotations?scope={scope}").json()
        assert bob_after["highlights"] == []
        assert bob_after["notes"] == []
        assert bob_after["drawing_pages"] == []


# =========================================================================
# 8. ANTI-IDOR ENTRE MÚLTIPLOS USUÁRIOS
# =========================================================================

def test_anti_idor_cross_user_and_cross_book_protection(phase2_api_env):
    """
    Garante que o Usuário B nunca pode:
    - Editar, excluir ou publicar anotação privada ou pública do Usuário A.
    - Vincular nota própria a um highlight_id privado de outro usuário ou de outro livro.
    - Consultar/modificar anotação informando book_id divergente na rota.
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    # Alice cria destaque, nota e desenho privados no book_pdf_1
    hl_priv = alice.post(
        "/api/books/book_pdf_1/highlights",
        json={
            "page": 2,
            "selected_text": "Destaque privado de Alice",
            "rects": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.04}],
        },
    ).json()["highlight"]

    nt_priv = alice.post(
        "/api/books/book_pdf_1/notes",
        json={"page": 2, "content": "Nota privada de Alice", "highlight_id": hl_priv["id"]},
    ).json()["note"]

    alice.put(
        "/api/books/book_pdf_1/pages/2/drawing",
        json={"strokes": [{"color": "#000000", "width": 2, "points": [{"x": 0.2, "y": 0.2}, {"x": 0.4, "y": 0.4}]}]},
    )

    # Bob tenta alterar visibilidade de itens privados de Alice -> 404 (anti-enumeração)
    assert bob.patch(f"/api/books/book_pdf_1/highlights/{hl_priv['id']}/visibility", json={"visibility": "public"}).status_code == 404
    assert bob.patch(f"/api/books/book_pdf_1/notes/{nt_priv['id']}/visibility", json={"visibility": "public"}).status_code == 404
    assert bob.patch("/api/books/book_pdf_1/pages/2/drawing/visibility", json={"visibility": "public"}).status_code == 404

    # Bob tenta editar ou excluir itens privados de Alice -> 404
    assert bob.put(f"/api/books/book_pdf_1/notes/{nt_priv['id']}", json={"content": "Hack"}).status_code == 404
    assert bob.delete(f"/api/books/book_pdf_1/highlights/{hl_priv['id']}").status_code == 404
    assert bob.delete(f"/api/books/book_pdf_1/notes/{nt_priv['id']}").status_code == 404

    # Bob tenta vincular uma nota sua ao highlight_id de Alice -> 400
    cross_link = bob.post(
        "/api/books/book_pdf_1/notes",
        json={"page": 2, "content": "Tentando usar highlight de Alice", "highlight_id": hl_priv["id"]},
    )
    assert cross_link.status_code == 400

    # Alice tenta vincular nota no book_pdf_2 usando highlight_id do book_pdf_1 -> 400
    cross_book_link = alice.post(
        "/api/books/book_pdf_2/notes",
        json={"page": 2, "content": "Highlight de outro livro", "highlight_id": hl_priv["id"]},
    )
    assert cross_book_link.status_code == 400

    # Alice tenta operar sobre anotação de book_pdf_1 usando rota de book_pdf_2 -> 404
    assert alice.patch(f"/api/books/book_pdf_2/highlights/{hl_priv['id']}/visibility", json={"visibility": "public"}).status_code == 404
    assert alice.put(f"/api/books/book_pdf_2/notes/{nt_priv['id']}", json={"content": "Outro livro"}).status_code == 404
    assert alice.delete(f"/api/books/book_pdf_2/notes/{nt_priv['id']}").status_code == 404

    # Agora Alice torna o destaque e a nota públicos
    alice.patch(f"/api/books/book_pdf_1/highlights/{hl_priv['id']}/visibility", json={"visibility": "public"})
    alice.patch(f"/api/books/book_pdf_1/notes/{nt_priv['id']}/visibility", json={"visibility": "public"})

    # Mesmo sendo públicos, Bob NÃO pode alterar visibilidade (403), editar (404) ou excluir (404)
    assert bob.patch(f"/api/books/book_pdf_1/highlights/{hl_priv['id']}/visibility", json={"visibility": "private"}).status_code == 403
    assert bob.patch(f"/api/books/book_pdf_1/notes/{nt_priv['id']}/visibility", json={"visibility": "private"}).status_code == 403
    assert bob.put(f"/api/books/book_pdf_1/notes/{nt_priv['id']}", json={"content": "Tentativa de edição por Bob"}).status_code == 404
    assert bob.delete(f"/api/books/book_pdf_1/highlights/{hl_priv['id']}").status_code == 404
    assert bob.delete(f"/api/books/book_pdf_1/notes/{nt_priv['id']}").status_code == 404


# =========================================================================
# 9. ACESSO NEGADO A LIVROS NÃO AUTORIZADOS
# =========================================================================

def test_unauthorized_or_unavailable_books_rejected(phase2_api_env):
    """
    Garante que livros inexistentes (404), arquivos ausentes (404), livros de fontes inativas (403)
    e usuários desativados (401) não expõem nem permitem criar anotações.
    """
    alice, _ = phase2_api_env["make_client"]("alice")

    # Livro inexistente -> 404
    assert alice.get("/api/books/non_existent_book/annotations").status_code == 404
    assert alice.post(
        "/api/books/non_existent_book/notes",
        json={"page": 1, "content": "Nota"},
    ).status_code == 404

    # Livro marcado como is_missing = 1 -> 404
    assert alice.get("/api/books/book_missing_file/annotations").status_code == 404
    assert alice.post(
        "/api/books/book_missing_file/notes",
        json={"page": 1, "content": "Nota"},
    ).status_code == 404

    # Livro pertencente a uma fonte desativada (source.is_active = 0) -> 403
    assert alice.get("/api/books/book_inactive_src/annotations").status_code == 403
    assert alice.get("/api/books/book_inactive_src/pages/1/annotations").status_code == 403
    assert alice.post(
        "/api/books/book_inactive_src/notes",
        json={"page": 1, "content": "Nota em fonte inativa"},
    ).status_code == 403
    assert alice.get("/api/books/book_inactive_src/pages/1/drawing").status_code == 403
    assert alice.get("/api/books/book_inactive_src/bookmarks").status_code == 403

    # Usuário desativado após criar sessão -> 401
    inativo_client, _ = phase2_api_env["make_client"]("inativo")
    conn = database.get_connection(phase2_api_env["db_path"])
    try:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?;", (phase2_api_env["inativo"]["id"],))
        conn.commit()
    finally:
        conn.close()

    assert inativo_client.get("/api/books/book_pdf_1/annotations").status_code == 401


# =========================================================================
# 11. DESENHOS PRÓPRIOS E PÚBLICOS E VALIDAÇÃO GEOMÉTRICA
# =========================================================================

def test_shared_drawings_bundle_and_geometry_validation(phase2_api_env):
    """
    Verifica:
    - Retorno aditivo de `own_drawing` e `public_drawings` em GET /pages/{page}/drawing e GET /pages/{page}/annotations.
    - Preservação da visibilidade ao atualizar traços do desenho.
    - Isolamento ao excluir o próprio desenho sem afetar desenhos públicos de terceiros.
    - Rejeição de traços com espessura ou coordenadas inválidas.
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    # Alice salva um desenho público na página 7
    res_save_a = alice.put(
        "/api/books/book_pdf_1/pages/7/drawing",
        json={
            "strokes": [
                {"color": "#e11d48", "width": 3.0, "points": [{"x": 0.15, "y": 0.2}, {"x": 0.45, "y": 0.6}]}
            ],
            "visibility": "public",
        },
    )
    assert res_save_a.status_code == 200
    assert res_save_a.json()["visibility"] == "public"

    # Alice atualiza os traços sem enviar visibility -> deve preservar 'public'
    res_upd_a = alice.put(
        "/api/books/book_pdf_1/pages/7/drawing",
        json={
            "strokes": [
                {"color": "#e11d48", "width": 4.0, "points": [{"x": 0.15, "y": 0.2}, {"x": 0.5, "y": 0.7}]}
            ]
        },
    )
    assert res_upd_a.status_code == 200
    assert res_upd_a.json()["visibility"] == "public"

    # Bob salva seu próprio desenho privado na página 7
    bob.put(
        "/api/books/book_pdf_1/pages/7/drawing",
        json={
            "strokes": [
                {"color": "#2563eb", "width": 2.0, "points": [{"x": 0.8, "y": 0.1}, {"x": 0.9, "y": 0.2}]}
            ]
        },
    )

    # Bob consulta GET /api/books/book_pdf_1/pages/7/drawing
    draw_bob = bob.get("/api/books/book_pdf_1/pages/7/drawing").json()
    assert len(draw_bob["strokes"]) == 1  # Compatibilidade retroativa no topo
    assert draw_bob["own_drawing"]["is_own"] is True
    assert len(draw_bob["own_drawing"]["strokes"]) == 1
    assert len(draw_bob["public_drawings"]) == 1
    pub_alice = draw_bob["public_drawings"][0]
    assert pub_alice["author_display_name"] == "Alice Lemos"
    assert pub_alice["is_own"] is False
    assert pub_alice["can_edit"] is False
    assert pub_alice["strokes"][0]["width"] == 4.0

    # Bob exclui seu próprio desenho -> o desenho público de Alice continua intacto
    del_bob = bob.delete("/api/books/book_pdf_1/pages/7/drawing")
    assert del_bob.status_code == 200
    draw_bob_after = bob.get("/api/books/book_pdf_1/pages/7/drawing").json()
    assert draw_bob_after["own_drawing"]["strokes"] == []
    assert len(draw_bob_after["public_drawings"]) == 1

    # Validação geométrica: espessura fora de [0.5, 64] ou coordenadas fora do intervalo -> 400
    bad_width = alice.put(
        "/api/books/book_pdf_1/pages/7/drawing",
        json={"strokes": [{"color": "#000", "width": 120, "points": [{"x": 0.1, "y": 0.1}]}]},
    )
    assert bad_width.status_code == 400

    bad_coord = alice.put(
        "/api/books/book_pdf_1/pages/7/drawing",
        json={"strokes": [{"color": "#000", "width": 2, "points": [{"x": -5.0, "y": 0.1}]}]},
    )
    assert bad_coord.status_code == 400


# =========================================================================
# 12. DESTAQUES E NOTAS EPUB CFI VIA API REST
# =========================================================================

def test_epub_cfi_highlights_and_notes_via_rest_api(phase2_api_env):
    """
    Valida a criação, consulta, filtragem e compartilhamento de destaques e notas
    ancorados por EPUB CFI (`epubcfi(...)`), sem exigir retângulos de página PDF.
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    cfi = "epubcfi(/6/4[chap01ref]!/4/2/10,/1:0,/1:42)"

    # Criação de destaque EPUB sem rects, diretamente público
    hl_res = alice.post(
        "/api/books/book_epub_1/highlights",
        json={
            "page": 1,
            "color": "#a7f3d0",
            "selected_text": "A felicidade da tua vida depende da qualidade dos teus pensamentos.",
            "epub_cfi": cfi,
            "chapter_label": "Livro III",
            "visibility": "public",
        },
    )
    assert hl_res.status_code == 201
    hl = hl_res.json()["highlight"]
    assert hl["epub_cfi"] == cfi
    assert hl["chapter_label"] == "Livro III"
    assert hl["rects"] == []
    assert hl["visibility"] == "public"

    # Criação de nota vinculada ao destaque EPUB (herda epub_cfi e chapter_label)
    nt_res = alice.post(
        "/api/books/book_epub_1/notes",
        json={
            "page": 1,
            "content": "Máxima estoica central de Marco Aurélio.",
            "highlight_id": hl["id"],
            "visibility": "public",
        },
    )
    assert nt_res.status_code == 201
    nt = nt_res.json()["note"]
    assert nt["epub_cfi"] == cfi
    assert nt["chapter_label"] == "Livro III"

    # Bob filtra anotações do EPUB especificamente por epub_cfi e scope=public
    bob_filtered = bob.get(
        "/api/books/book_epub_1/annotations",
        params={"scope": "public", "epub_cfi": cfi},
    ).json()
    assert len(bob_filtered["highlights"]) == 1
    assert len(bob_filtered["notes"]) == 1
    assert bob_filtered["highlights"][0]["id"] == hl["id"]

    # EPUB CFI malformado (não inicia com epubcfi(...)) -> 400
    bad_cfi = alice.post(
        "/api/books/book_epub_1/highlights",
        json={
            "page": 1,
            "selected_text": "Texto",
            "epub_cfi": "invalid_cfi_format(/6/2)",
        },
    )
    assert bad_cfi.status_code == 400


# =========================================================================
# 13. MARCADORES PESSOAIS (BOOKMARKS)
# =========================================================================

def test_personal_bookmarks_crud_and_isolation(phase2_api_env):
    """
    Valida criação, atualização idempotente na mesma posição, listagem e exclusão
    de marcadores pessoais (sempre privados e isolados por usuário).
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    # Alice cria marcador PDF na página 12
    bm1_res = alice.post(
        "/api/books/book_pdf_1/bookmarks",
        json={"page": 12, "chapter_label": "Capítulo XII", "label": "Página importante"},
    )
    assert bm1_res.status_code == 201
    bm1 = bm1_res.json()["bookmark"]
    assert bm1["page"] == 12
    assert bm1["label"] == "Página importante"
    assert bm1["type"] == "bookmark"

    # Alice cria marcador novamente na mesma página 12 -> atualiza sem duplicar
    bm1_dup = alice.post(
        "/api/books/book_pdf_1/bookmarks",
        json={"page": 12, "label": "Página importante (atualizado)"},
    )
    assert bm1_dup.status_code == 201
    assert bm1_dup.json()["bookmark"]["id"] == bm1["id"]

    # Alice cria marcador EPUB com CFI
    bm_epub = alice.post(
        "/api/books/book_epub_1/bookmarks",
        json={
            "page": 1,
            "epub_cfi": "epubcfi(/6/6!/4/2/4)",
            "chapter_label": "Livro IV",
            "label": "Retomar leitura aqui",
        },
    )
    assert bm_epub.status_code == 201

    # Listagem de marcadores de Alice no book_pdf_1
    list_a = alice.get("/api/books/book_pdf_1/bookmarks").json()
    assert list_a["count"] == 1
    assert list_a["bookmarks"][0]["label"] == "Página importante (atualizado)"

    # Bob não vê os marcadores de Alice nem pode excluí-los
    list_b = bob.get("/api/books/book_pdf_1/bookmarks").json()
    assert list_b["count"] == 0
    assert bob.delete(f"/api/books/book_pdf_1/bookmarks/{bm1['id']}").status_code == 404

    # Alice exclui seu marcador
    del_a = alice.delete(f"/api/books/book_pdf_1/bookmarks/{bm1['id']}")
    assert del_a.status_code == 200
    assert alice.get("/api/books/book_pdf_1/bookmarks").json()["count"] == 0


# =========================================================================
# 14. BUSCA TEXTUAL, ORDENAÇÃO E PAGINAÇÃO NA API REST
# =========================================================================

def test_annotations_search_sort_and_pagination(phase2_api_env):
    """Valida parâmetros `search`, `sort=position|recent`, `limit` e `offset` em GET /api/books/{book_id}/annotations."""
    alice, _ = phase2_api_env["make_client"]("alice")

    alice.post(
        "/api/books/book_pdf_2/notes",
        json={"page": 30, "content": "Conceito de Humanitas segundo Quincas Borba", "chapter_label": "Cap. 30"},
    )
    alice.post(
        "/api/books/book_pdf_2/notes",
        json={"page": 10, "content": "Introdução ao filósofo e ao cão", "chapter_label": "Cap. 10"},
    )
    alice.post(
        "/api/books/book_pdf_2/notes",
        json={"page": 20, "content": "Encontro de Rubião no trem", "chapter_label": "Cap. 20"},
    )

    # Ordenação por posição (padrão): páginas 10, 20, 30
    by_pos = alice.get("/api/books/book_pdf_2/annotations?sort=position").json()["notes"]
    assert [n["page"] for n in by_pos] == [10, 20, 30]

    # Paginação: limit=2, offset=1 -> páginas 20, 30
    paged = alice.get("/api/books/book_pdf_2/annotations?sort=position&limit=2&offset=1").json()["notes"]
    assert [n["page"] for n in paged] == [20, 30]

    # Busca textual case-insensitive ('humanitas')
    searched = alice.get("/api/books/book_pdf_2/annotations?search=humanitas").json()["notes"]
    assert len(searched) == 1
    assert searched[0]["page"] == 30


# =========================================================================
# 15. COMPATIBILIDADE RETROATIVA INTEGRAL COM CONTRATOS DA v1.7.0
# =========================================================================

def test_v170_backward_compatibility_contracts(phase2_api_env):
    """
    Garante que chamadas exatamente no formato da v1.7.0 (sem informar `scope`, `visibility`,
    `epub_cfi` ou `chapter_label`) continuam retornando as mesmas chaves e semântica de isolamento.
    """
    alice, _ = phase2_api_env["make_client"]("alice")
    bob, _ = phase2_api_env["make_client"]("bob")

    # Criação clássica v1.7.0
    h_res = alice.post(
        "/api/books/book_pdf_2/highlights",
        json={
            "page": 4,
            "color": "#fef08a",
            "rects": [{"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.03}],
            "selected_text": "Texto clássico v1.7.0",
        },
    )
    assert h_res.status_code == 201
    h_id = h_res.json()["highlight"]["id"]

    n_res = alice.post(
        "/api/books/book_pdf_2/notes",
        json={"page": 4, "content": "Nota clássica v1.7.0", "highlight_id": h_id},
    )
    assert n_res.status_code == 201
    n_id = n_res.json()["note"]["id"]

    d_res = alice.put(
        "/api/books/book_pdf_2/pages/4/drawing",
        json={"strokes": [{"color": "#000000", "width": 2, "points": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]}]},
    )
    assert d_res.status_code == 200
    assert d_res.json()["status"] == "success"

    # Leitura clássica sem query params -> escopo padrão 'mine'
    summary = alice.get("/api/books/book_pdf_2/annotations").json()
    for required_key in ("highlights", "notes", "drawing_pages", "counts"):
        assert required_key in summary
    assert summary["scope"] == "mine"

    page_data = alice.get("/api/books/book_pdf_2/pages/4/annotations").json()
    for required_key in ("page", "highlights", "notes", "drawing"):
        assert required_key in page_data
    assert len(page_data["drawing"]["strokes"]) == 1

    # Bob sem query params não vê nada privado de Alice
    bob_page = bob.get("/api/books/book_pdf_2/pages/4/annotations").json()
    assert bob_page["highlights"] == []
    assert bob_page["notes"] == []
    assert bob_page["drawing"]["strokes"] == []

    # Rotas curtas legadas de edição e exclusão (/api/notes/{id}, /api/highlights/{id})
    assert alice.put(f"/api/notes/{n_id}", json={"content": "Nota clássica editada"}).status_code == 200
    assert alice.delete(f"/api/notes/{n_id}").status_code == 200
    assert alice.delete(f"/api/highlights/{h_id}").status_code == 200

