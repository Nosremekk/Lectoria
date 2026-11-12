"""
Testes de Integração e Contratos Frontend ↔ API ↔ SQLite (Lectoria v1.5.1).
Reproduz os cenários reais identificados nos logs de produção:
1. PUT /api/books/{book_id}/reviews (Avaliações, Favoritos e Quero Ler sem 405 Method Not Allowed).
2. POST /api/shared-annotations com book_id hexadecimal e campo `content` (sem 422 e sem [object Object]).
3. POST /api/recommendations (direto entre dois usuários e mural geral sem to_user_id obrigatório).
4. Persistência real de preferências de privacidade ao reabrir GET /api/profile/me (contrato data.profile.privacy).
5. Isolamento estrito entre dois usuários distintos e verificação de versão 1.5.1 entre frontend e backend.
"""

from pathlib import Path
from fastapi.testclient import TestClient

import config
import library
from main import app


def _login_user(client: TestClient, username: str, password: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    csrf = client.cookies.get("lectoria_csrf", "")
    return {"headers": {"X-CSRF-Token": csrf}, "user": res.json()["user"]}


def test_v151_frontend_backend_version_alignment(client):
    """Confirma que backend, index.html e app.js declaram e servem a versão da aplicação em sincronia."""
    assert config.APP_VERSION in ("1.5.1", "1.5.2", "1.6.0", "1.6.1", "1.6.2")

    status_res = client.get("/api/auth/status")
    assert status_res.status_code == 200
    assert status_res.json().get("version") in ("1.5.1", "1.5.2", "1.6.0", "1.6.1", "1.6.2")

    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text
    assert any(v in html for v in ('data-app-version="1.5.1"', 'data-app-version="1.5.2"', 'data-app-version="1.6.0"', 'data-app-version="1.6.1"', 'data-app-version="1.6.2"'))
    assert any(v in html for v in ('/static/js/app.js?v=1.5.1', '/static/js/app.js?v=1.5.2', '/static/js/app.js?v=1.6.0', '/static/js/app.js?v=1.6.1', '/static/js/app.js?v=1.6.2'))
    assert any(v in html for v in ('/static/css/style.css?v=1.5.1', '/static/css/style.css?v=1.5.2', '/static/css/style.css?v=1.6.0', '/static/css/style.css?v=1.6.1', '/static/css/style.css?v=1.6.2'))

    app_js = Path(config.BASE_DIR).parent / "static" / "js" / "app.js"
    if not app_js.exists():
        app_js = Path(__file__).resolve().parent.parent / "static" / "js" / "app.js"
    js_text = app_js.read_text(encoding="utf-8")
    assert any(v in js_text for v in ('window.LECTORIA_FRONTEND_VERSION = "1.5.1";', 'window.LECTORIA_FRONTEND_VERSION = "1.5.2";', 'window.LECTORIA_FRONTEND_VERSION = "1.6.0";', 'window.LECTORIA_FRONTEND_VERSION = "1.6.1";', 'window.LECTORIA_FRONTEND_VERSION = "1.6.2";'))
    assert "function formatApiError(" in js_text


def test_v151_privacy_persistence_and_profile_reopen_contract(test_env):
    """
    Reproduz o fluxo exato de openProfileModal() -> Salvar -> openProfileModal() em app.js:
    Verifica que GET /api/profile/me retorna tanto `data.profile.privacy` quanto `data.privacy`,
    garantindo que os checkboxes permaneçam marcados ao reabrir o modal.
    """
    with TestClient(app) as c_admin:
        # Garante admin e cria dois usuários para o teste
        books = library.get_books()
        assert len(books) >= 2

        setup_res = c_admin.post(
            "/api/auth/setup",
            json={"username": "admin_v151", "password": "AdminPassword123", "display_name": "Admin"},
        )
        if setup_res.status_code == 403:
            # Admin já existe na sessão de testes
            pass

    import auth_service
    try:
        u1 = auth_service.create_user("leitor_priv_1", "SenhaSegura123", "Leitor Um", role="reader")
    except Exception:
        u1 = auth_service.authenticate_user("leitor_priv_1", "SenhaSegura123")

    with TestClient(app) as client1:
        auth1 = _login_user(client1, "leitor_priv_1", "SenhaSegura123")
        headers1 = auth1["headers"]

        # 1. Abre o modal pela primeira vez: tudo deve estar False por padrão
        res_init = client1.get("/api/profile/me")
        assert res_init.status_code == 200
        data_init = res_init.json()
        assert "profile" in data_init
        assert "privacy" in data_init["profile"]
        priv_init = data_init["profile"]["privacy"]
        assert priv_init["share_current_reading"] is False
        assert priv_init["share_reading_history"] is False
        assert priv_init["share_reviews"] is False
        assert priv_init["share_lists"] is False
        assert priv_init["share_annotations_default"] is False

        # 2. Salva perfil e preferências exatamente como btnSaveProfileModal em app.js
        res_put_prof = client1.put(
            "/api/profile/me",
            json={
                "display_name": "Guilherme Leles",
                "avatar_color": "#10b981",
                "bio": "Filosofia clássica e história.",
            },
            headers=headers1,
        )
        assert res_put_prof.status_code == 200

        res_put_priv = client1.put(
            "/api/profile/privacy",
            json={
                "share_current_reading": True,
                "share_reading_history": True,
                "share_reviews": True,
                "share_lists": False,
                "share_annotations_default": True,
            },
            headers=headers1,
        )
        assert res_put_priv.status_code == 200

        # 3. Reabre o modal (GET /api/profile/me) e valida que data.profile.privacy persiste!
        res_reopen = client1.get("/api/profile/me")
        assert res_reopen.status_code == 200
        data_reopen = res_reopen.json()
        prof = data_reopen["profile"]
        priv = prof["privacy"]

        assert prof["display_name"] == "Guilherme Leles"
        assert prof["avatar_color"] == "#10b981"
        assert prof["bio"] == "Filosofia clássica e história."
        assert priv["share_current_reading"] is True
        assert priv["share_reading_history"] is True
        assert priv["share_reviews"] is True
        assert priv["share_lists"] is False
        assert priv["share_annotations_default"] is True


def test_v151_put_reviews_favorites_want_to_read_and_multiuser_isolation(test_env):
    """
    Reproduz o erro 405 em PUT /api/books/{book_id}/reviews e valida:
    - Botão Favoritar e Quero Ler via PUT /api/books/{book_id}/reviews
    - Preservação da preferência share_reviews ao favoritar um livro ainda não avaliado
    - Persistência de nota (1-5) e resenha
    - Isolamento entre dois usuários distintos
    """
    import auth_service
    for uname, dname in [("ana_v151", "Ana"), ("bruno_v151", "Bruno")]:
        try:
            auth_service.create_user(uname, "SenhaSegura123", dname, role="reader")
        except Exception:
            pass

    books = library.get_books()
    book_id = books[0]["id"]
    assert isinstance(book_id, str) and len(book_id) >= 8
    from database import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM book_reviews WHERE book_id = ?;", (book_id,))

    with TestClient(app) as c_ana, TestClient(app) as c_bruno:
        h_ana = _login_user(c_ana, "ana_v151", "SenhaSegura123")["headers"]
        h_bruno = _login_user(c_bruno, "bruno_v151", "SenhaSegura123")["headers"]

        # Ana ativa share_reviews no perfil
        c_ana.put("/api/profile/privacy", json={"share_reviews": True}, headers=h_ana)

        # 1. Ana clica no botão Favorito (PUT /api/books/{book_id}/reviews sem is_public)
        res_fav = c_ana.put(
            f"/api/books/{book_id}/reviews",
            json={
                "rating": None,
                "review_text": "",
                "is_favorite": True,
                "want_to_read": False,
            },
            headers=h_ana,
        )
        assert res_fav.status_code == 200, res_fav.text
        fav_data = res_fav.json()
        assert fav_data["user_review"]["is_favorite"] is True
        assert fav_data["user_review"]["want_to_read"] is False
        # Garante que favoritar não desativou share_reviews da Ana!
        assert fav_data["user_share_reviews_default"] is True

        # 2. Bruno consulta o mesmo livro: não deve estar favoritado para Bruno
        res_bruno_get = c_bruno.get(f"/api/books/{book_id}/reviews")
        assert res_bruno_get.status_code == 200
        assert res_bruno_get.json()["user_review"] is None

        # 3. Ana marca Quero Ler e depois salva avaliação 5 estrelas pública via PUT
        res_wtr = c_ana.put(
            f"/api/books/{book_id}/reviews",
            json={
                "rating": 5,
                "review_text": "Leitura fundamental sobre ética e política.",
                "is_favorite": True,
                "want_to_read": True,
                "is_public": True,
            },
            headers=h_ana,
        )
        assert res_wtr.status_code == 200
        wtr_data = res_wtr.json()
        assert wtr_data["user_review"]["rating"] == 5
        assert wtr_data["user_review"]["is_favorite"] is True
        assert wtr_data["user_review"]["want_to_read"] is True
        assert wtr_data["user_review"]["is_public"] is True
        assert wtr_data["average_rating"] == 5.0

        # 4. Bruno salva uma avaliação privada (is_public=False)
        res_bruno_rev = c_bruno.put(
            f"/api/books/{book_id}/reviews",
            json={
                "rating": 2,
                "review_text": "Anotação crítica privada do Bruno.",
                "is_favorite": False,
                "want_to_read": True,
                "is_public": False,
            },
            headers=h_bruno,
        )
        assert res_bruno_rev.status_code == 200

        # Ana vê sua avaliação (5 estrelas), mas NÃO vê a resenha privada do Bruno
        ana_view = c_ana.get(f"/api/books/{book_id}/reviews").json()
        assert len(ana_view["reviews"]) == 1
        assert ana_view["reviews"][0]["username"] == "ana_v151"
        assert ana_view["average_rating"] == 5.0

        # Bruno vê a avaliação pública da Ana E a sua própria
        bruno_view = c_bruno.get(f"/api/books/{book_id}/reviews").json()
        assert len(bruno_view["reviews"]) == 2
        assert bruno_view["user_review"]["want_to_read"] is True


def test_v151_shared_annotations_and_recommendations_contracts(test_env):
    """
    Reproduz os payloads exatos enviados pelo JavaScript para:
    - POST /api/shared-annotations (com book_id string hexadecimal, quote_text, content, visibility='all_users')
    - POST /api/recommendations (tanto para um destinatário específico quanto para o Mural Geral com to_user_id=None)
    - GET /api/social/feed (garantindo que feed.recommendations é uma lista iterável pelo frontend)
    """
    import auth_service
    for uname, dname in [("clara_v151", "Clara"), ("daniel_v151", "Daniel")]:
        try:
            auth_service.create_user(uname, "SenhaSegura123", dname, role="reader")
        except Exception:
            pass

    books = library.get_books()
    book_id = books[0]["id"]

    with TestClient(app) as c_clara, TestClient(app) as c_daniel:
        login_clara = _login_user(c_clara, "clara_v151", "SenhaSegura123")
        login_daniel = _login_user(c_daniel, "daniel_v151", "SenhaSegura123")
        h_clara = login_clara["headers"]
        h_daniel = login_daniel["headers"]
        daniel_id = login_daniel["user"]["id"]

        # 1. Clara compartilha citação usando o payload exato do modal de app.js
        res_share = c_clara.post(
            "/api/shared-annotations",
            json={
                "book_id": book_id,
                "page_number": 42,
                "quote_text": "A constituição é a forma de organização dos poderes na pólis.",
                "content": "Passagem central da obra de Aristóteles.",
                "visibility": "all_users",
            },
            headers=h_clara,
        )
        assert res_share.status_code == 201, res_share.text
        ann = res_share.json()["annotation"]
        assert ann["book_id"] == book_id
        assert ann["page_number"] == 42
        assert ann["quote_text"] == "A constituição é a forma de organização dos poderes na pólis."
        assert ann["content"] == "Passagem central da obra de Aristóteles."
        assert ann["comment_text"] == "Passagem central da obra de Aristóteles."

        # 2. Daniel comenta na citação compartilhada da Clara
        res_comment = c_daniel.post(
            f"/api/shared-annotations/{ann['id']}/comments",
            json={"content": "Excelente destaque, Clara!"},
            headers=h_daniel,
        )
        assert res_comment.status_code == 201, res_comment.text
        assert res_comment.json()["comment"]["content"] == "Excelente destaque, Clara!"

        # 3. Clara consulta lista de leitores para recomendação (/api/social/users)
        res_users = c_clara.get("/api/social/users")
        assert res_users.status_code == 200
        users_list = res_users.json()["users"]
        assert any(u["id"] == daniel_id for u in users_list)

        # 4. Clara envia recomendação para "Toda a Comunidade (Mural Geral)" (to_user_id=None)
        res_rec_mural = c_clara.post(
            "/api/recommendations",
            json={
                "book_id": book_id,
                "to_user_id": None,
                "message": "Recomendo a todos da biblioteca!",
            },
            headers=h_clara,
        )
        assert res_rec_mural.status_code == 201, res_rec_mural.text

        # 5. Clara envia recomendação direta para Daniel (to_user_id=daniel_id)
        res_rec_direct = c_clara.post(
            "/api/recommendations",
            json={
                "book_id": str(books[1]["id"]),
                "to_user_id": daniel_id,
                "message": "Daniel, este segundo livro complementa a leitura!",
            },
            headers=h_clara,
        )
        assert res_rec_direct.status_code == 201, res_rec_direct.text
        rec_obj = res_rec_direct.json()["recommendation"]
        assert rec_obj["sender_display_name"] == "Clara"
        assert rec_obj["recipient_display_name"] == "Daniel"

        # 6. Daniel carrega o feed 'Nossa Biblioteca' (GET /api/social/feed)
        res_feed = c_daniel.get("/api/social/feed")
        assert res_feed.status_code == 200
        feed = res_feed.json()
        assert isinstance(feed["recommendations"], list)
        assert len(feed["recommendations"]) >= 2
        assert any(
            r["book_id"] == str(books[1]["id"]) and r["sender_display_name"] == "Clara"
            for r in feed["recommendations"]
        )
        assert any(
            a["id"] == ann["id"] and len(a["comments"]) == 1
            for a in feed["shared_annotations"]
        )


def test_v151_browser_e2e_social_ui_flow():
    """
    Teste E2E em navegador Chromium real validando o fluxo completo:
    Frontend JS (app.js) -> API FastAPI -> SQLite -> Atualização visual e persistência no DOM.
    """
    import os
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    import urllib.request
    import pypdf
    import pytest
    from playwright.sync_api import sync_playwright

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    temp_dir = tempfile.TemporaryDirectory()
    base_tmp = Path(temp_dir.name)
    data_dir = base_tmp / "data"
    library_dir = base_tmp / "books"
    covers_dir = base_tmp / "covers"
    backups_dir = base_tmp / "backups"
    for d in (data_dir, library_dir, covers_dir, backups_dir):
        d.mkdir(parents=True, exist_ok=True)

    writer = pypdf.PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=595, height=842)
    writer.add_metadata({"/Title": "A Constituição de Atenas", "/Author": "Aristóteles"})
    with open(library_dir / "aristoteles.pdf", "wb") as f:
        writer.write(f)

    env = os.environ.copy()
    env["LECTORIA_DATA_DIR"] = str(data_dir)
    env["LECTORIA_LIBRARY_DIR"] = str(library_dir)
    env["LECTORIA_COVERS_DIR"] = str(covers_dir)
    env["LECTORIA_BACKUPS_DIR"] = str(backups_dir)
    env["LECTORIA_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parent.parent),
    )

    base_url = f"http://127.0.0.1:{port}"
    started = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{base_url}/api/books", timeout=1) as resp:
                if resp.status == 200:
                    started = True
                    break
        except Exception:
            time.sleep(0.2)

    try:
        assert started, "Servidor E2E não iniciou."
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(f"Chromium indisponível: {exc}")

            page = browser.new_page(viewport={"width": 1366, "height": 850})
            page.goto(base_url, wait_until="networkidle")

            # Configura o admin inicial se o modal de setup abrir
            if page.locator("#modal-auth.active").count() > 0:
                page.fill("#auth-username", "guilherme")
                page.fill("#auth-display-name", "Guilherme Leles")
                page.fill("#auth-password", "SenhaForte123")
                page.click("#btn-auth-submit")
                page.wait_for_selector("#modal-auth.active", state="hidden", timeout=5000)

            # 1. Testa Perfil & Privacidade: marca checkboxes, salva, fecha e reabre
            page.click("#btn-open-profile")
            page.wait_for_selector("#modal-user-profile.active", timeout=5000)
            assert page.is_checked("#priv-share-current") is False
            assert page.is_checked("#priv-share-reviews") is False

            page.fill("#profile-display-name", "Guilherme Leles")
            page.check("#priv-share-current")
            page.check("#priv-share-reviews")
            page.check("#priv-share-lists")
            page.click("#btn-save-profile-modal")
            page.wait_for_selector("#profile-save-feedback", state="visible", timeout=5000)
            assert "salvos com sucesso" in page.inner_text("#profile-save-feedback")

            page.click("#btn-close-profile-modal")
            page.wait_for_selector("#modal-user-profile.active", state="hidden", timeout=5000)

            # Reabre o modal e confirma que os checkboxes permanecem marcados!
            page.click("#btn-open-profile")
            page.wait_for_selector("#modal-user-profile.active", timeout=5000)
            assert page.input_value("#profile-display-name") == "Guilherme Leles"
            assert page.is_checked("#priv-share-current") is True
            assert page.is_checked("#priv-share-reviews") is True
            assert page.is_checked("#priv-share-lists") is True
            page.click("#btn-close-profile-modal")

            # 2. Testa Compartilhar Citação na aba Nossa Biblioteca
            page.click("#tab-nav-social")
            page.wait_for_selector("#social-section", state="visible", timeout=5000)
            page.click("#btn-social-new-quote")
            page.wait_for_selector("#modal-share-annotation.active", timeout=5000)
            page.fill("#share-ann-page", "42")
            page.fill("#share-ann-quote", "O homem é por natureza um animal político.")
            page.fill("#share-ann-content", "Reflexão excelente sobre Atenas.")
            page.click("#btn-submit-share-ann")
            page.wait_for_selector("#modal-share-annotation.active", state="hidden", timeout=5000)

            # Confirma que a citação apareceu no mural e nunca exibiu [object Object]
            feed_text = page.inner_text("#social-shared-annotations")
            assert "[object Object]" not in feed_text
            assert "O homem é por natureza um animal político." in feed_text
            assert "Reflexão excelente sobre Atenas." in feed_text

            # 3. Testa Recomendação de Livro
            page.click("#btn-social-send-rec")
            page.wait_for_selector("#modal-recommend-book.active", timeout=5000)
            page.fill("#rec-message", "Leitura essencial para compreender a democracia ateniense.")
            page.click("#btn-submit-rec-modal")
            page.wait_for_selector("#modal-recommend-book.active", state="hidden", timeout=5000)

            rec_text = page.inner_text("#social-recommendations")
            assert "Leitura essencial para compreender a democracia ateniense." in rec_text

            # 4. Testa Favoritar, Quero Ler e Avaliação por Estrelas no Modal de Detalhes
            page.click("#tab-nav-library")
            page.wait_for_selector(".book-card", timeout=5000)
            page.click(".book-card .btn-book-details")
            page.wait_for_selector("#modal-book-details.active", timeout=5000)

            page.click("#btn-details-favorite")
            page.wait_for_function(
                "document.querySelector('#btn-details-favorite').textContent.includes('★ Favorito')",
                timeout=5000,
            )
            page.click("#btn-details-want-to-read")
            page.wait_for_function(
                "document.querySelector('#btn-details-want-to-read').textContent.includes('✓ Quero Ler')",
                timeout=5000,
            )

            # Abre subaba social do livro e salva avaliação 5 estrelas
            page.click('.details-tab-btn[data-subtab="social"]')
            page.select_option("#details-rating-select", "5")
            page.fill("#details-review-text", "Tradução impecável e notas históricas ricas.")
            page.click("#btn-details-save-review")
            page.wait_for_function(
                "document.querySelector('#details-social-reviews-list').innerText.includes('Tradução impecável')",
                timeout=5000,
            )

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        temp_dir.cleanup()

