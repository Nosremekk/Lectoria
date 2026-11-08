"""
Suíte de Testes da Versão v1.5.0 — Lectoria "Biblioteca Viva":
- Pipeline bibliográfico progressivo em 5 estágios, normalização reversível, Crossref, diagnóstico por provedor e proveniência por campo.
- Validação visual de capas extraídas (rejeição de JPEG CMYK de 4 canais e máscaras pretas sólidas) e capa editorial SVG com categoria.
- Reconciliação de arquivos movidos/renomeados por SHA-256 + tamanho preservando book_id e progresso.
- Automação da biblioteca e fila persistente de tarefas (scheduled_jobs e background_jobs).
- Multiusuário e camada social "Nossa Biblioteca" com privacidade por padrão (opt-in granular) e proteção anti-IDOR.
- Painel Administrativo dedicado (6 seções) e revogação de sessões.
- Migração SQLite v7 -> v8 preservando dados existentes.
"""

import sqlite3
import struct
from pathlib import Path

import pytest

import annotations_service
import auth_service
import automation_service
import config
import covers_service
import database
import library
import metadata_service
import progress
import social_service


# ---------------------------------------------------------------------------
# 1. Pipeline Bibliográfico Avançado, Normalização e Coerência
# ---------------------------------------------------------------------------

def test_parse_structured_title_and_author_and_cleaning():
    raw = "Platão - As Leis [Coleção Textos Clássicos] (Z-Library) (2ª Edição) Vol. 1.pdf"
    parsed = metadata_service.parse_structured_title_and_author(raw)
    assert parsed["author"] == "Platão"
    assert "As Leis" in parsed["title"]
    assert "Z-Library" not in parsed["title"]
    assert parsed["edition"] is not None
    assert parsed["volume"] is not None


def test_score_candidate_detailed_title_only_multi_source_coherence():
    query = {"title": "Sociedade do Cansaço", "author": "", "isbn13": ""}
    candidate_single = {
        "source": "openlibrary",
        "title": "Sociedade do Cansaço",
        "author": "Byung-Chul Han",
        "publisher": "Vozes",
        "publish_year": 2015,
        "isbn13": "9788532649966",
        "description": "Um ensaio filosófico sobre a sociedade contemporânea do desempenho.",
        "cover_url": "https://covers.openlibrary.org/b/id/123-L.jpg",
    }
    candidate_multi = {**candidate_single, "multi_source_confirmed": True}

    score_single, conf_single, bd_single = metadata_service.score_candidate_detailed(query, candidate_single)
    score_multi, conf_multi, bd_multi = metadata_service.score_candidate_detailed(query, candidate_multi)

    assert score_multi > score_single
    assert score_single >= 55.0
    assert "title_only_fallback" in bd_single
    assert "multi_source_coherence" in bd_multi


def test_provider_diagnostics_and_provenance(test_env):
    metadata_service.record_provider_stat("openlibrary", "success")
    metadata_service.record_provider_stat("googlebooks", "rate_limited", "HTTP 429 Too Many Requests")
    metadata_service.record_provider_stat("crossref", "success")

    diag_list = metadata_service.get_providers_diagnostics()
    provs = {p["provider"]: p for p in diag_list}
    assert provs["openlibrary"]["success_count"] >= 1
    assert provs["googlebooks"]["rate_limit_count"] >= 1
    assert provs["crossref"]["success_count"] >= 1

    books = library.get_books()
    assert len(books) > 0
    book_id = books[0]["id"]

    with database.get_db() as conn:
        cursor = conn.cursor()
        metadata_service.record_metadata_provenance(
            cursor=cursor,
            book_id=book_id,
            fields={"publisher": "Companhia das Letras", "publish_year": 2020},
            source="openlibrary",
            confidence=91.5,
        )
        row = cursor.execute(
            "SELECT * FROM metadata_provenance WHERE book_id = ? AND field_name = 'publisher'",
            (book_id,),
        ).fetchone()
    assert row is not None
    assert row["source"] == "openlibrary"
    assert row["field_value"] == "Companhia das Letras"


# ---------------------------------------------------------------------------
# 2. Validação Visual de Capas (Rejeição de CMYK / Preta Sólida)
# ---------------------------------------------------------------------------

def _make_minimal_jpeg_header(width: int, height: int, components: int) -> bytes:
    """Constrói um buffer JPEG sintético com marcador SOF0 para testar _parse_jpeg_info."""
    sof0_payload = struct.pack(">BHHB", 8, height, width, components) + (b"\x01\x11\x00" * components)
    sof0_segment = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    return b"\xff\xd8" + sof0_segment + (b"\x55" * 1200) + b"\xff\xd9"


def test_cover_visual_validation_rejects_cmyk_and_solid_black():
    # JPEG CMYK (4 componentes) -> deve ser rejeitado
    cmyk_jpeg = _make_minimal_jpeg_header(400, 600, 4)
    assert covers_service.is_extracted_cover_visually_valid(cmyk_jpeg) is False

    # JPEG RGB normal (3 componentes) -> aceito
    rgb_jpeg = _make_minimal_jpeg_header(400, 600, 3)
    assert covers_service.is_extracted_cover_visually_valid(rgb_jpeg) is True

    # Buffer de pixels totalmente preto (máscara /SMask sem composição) -> rejeitado
    solid_black_pixels = b"\x00" * (200 * 300 * 3)
    assert covers_service._is_raw_pixel_buffer_valid(solid_black_pixels) is False

    # Buffer de pixels com contraste editorial real -> aceito
    rich_pixels = (b"\xf4\xf1\xea" * (200 * 150)) + (b"\x22\x33\x44" * (200 * 150))
    assert covers_service._is_raw_pixel_buffer_valid(rich_pixels) is True


def test_editorial_svg_cover_includes_category():
    svg_text = covers_service.generate_editorial_svg_cover(
        title="Crítica da Razão Pura",
        author="Immanuel Kant",
        book_id="42",
        category="Filosofia",
    )
    assert "<svg" in svg_text
    assert "Crítica da Razão" in svg_text
    assert "Immanuel Kant" in svg_text
    assert "Filosofia" in svg_text or "FILOSOFIA" in svg_text


# ---------------------------------------------------------------------------
# 3. Reconciliação de Arquivos Movidos / Renomeados no Scan
# ---------------------------------------------------------------------------

def test_scan_reconciles_moved_or_renamed_file_preserving_book_id_and_progress(test_env):
    books = library.get_books()
    target = books[0]
    original_id = target["id"]
    old_rel_path = target["filename"]

    # Registra progresso no livro antes de renomear
    progress.save_progress(original_id, current_page=4, total_pages=10)

    old_abs_path = test_env["library_dir"] / old_rel_path
    new_abs_path = test_env["library_dir"] / "subpasta_reorganizada" / f"renomeado_{Path(old_rel_path).name}"
    new_abs_path.parent.mkdir(parents=True, exist_ok=True)
    old_abs_path.rename(new_abs_path)

    stats = library.scan_library()
    assert stats["updated"] >= 1

    reconciled = library.get_book(original_id)
    assert reconciled is not None
    assert "renomeado_" in reconciled["filename"]
    assert reconciled["current_page"] == 4


# ---------------------------------------------------------------------------
# 4. Automação da Biblioteca e Fila Persistente de Tarefas
# ---------------------------------------------------------------------------

def test_automation_schedules_and_persistent_jobs(test_env):
    schedules = automation_service.list_schedules()
    job_types = {s["job_type"] for s in schedules}
    assert {
        "scan_library",
        "enrich_metadata",
        "update_missing_covers",
        "rebuild_search_index",
        "db_maintenance",
        "auto_backup",
    }.issubset(job_types)

    updated = automation_service.update_schedule(
        "scan_library",
        enabled=True,
        schedule_mode="interval",
        interval_minutes=30,
        run_time="04:00",
    )
    assert updated["enabled"] is True
    assert updated["schedule_mode"] == "interval"
    assert updated["interval_minutes"] == 30
    assert updated["next_run_at"] is not None

    # Simula tarefa interrompida antes de reinicialização
    with database.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO background_jobs (job_type, status, trigger_source) VALUES ('scan_library', 'running', 'manual')"
        )
        stale_id = cursor.lastrowid

    recovered = automation_service.recover_interrupted_jobs_on_startup()
    assert recovered >= 1
    stale_job = automation_service.get_job(stale_id)
    assert stale_job["status"] == "interrupted"

    # Executa manutenção do banco de forma síncrona
    job_id = automation_service.create_job_record("db_maintenance", trigger_source="test")
    result = automation_service.execute_job_sync(job_id)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 5. Multiusuário, Privacidade por Padrão e Proteção Anti-IDOR
# ---------------------------------------------------------------------------

def test_multiuser_privacy_by_default_and_anti_idor_protection(client, test_env):
    # Cria usuários para testar isolamento e privacidade
    admin_user = auth_service.create_user("admin_v15", "AdminSenha123", "Administrador", role="admin")
    alice = auth_service.create_user("alice", "AliceSenha123", "Alice Leitora", role="reader")
    bob = auth_service.create_user("bob", "BobSenha123", "Bob Leitor", role="reader")

    # 1. Verifica privacidade 100% privada por padrão
    alice_priv = social_service.get_privacy_settings(alice["id"])
    assert alice_priv["share_current_reading"] is False
    assert alice_priv["share_reading_history"] is False
    assert alice_priv["share_reviews"] is False
    assert alice_priv["share_lists"] is False
    assert alice_priv["share_annotations_default"] is False

    books = library.get_books()
    book_id = books[0]["id"]

    # 2. Alice cria uma nota privada no livro
    private_note = annotations_service.create_note(
        book_id=book_id,
        page=2,
        content="Minha reflexão 100% pessoal e privada da Alice",
        user_id=alice["id"],
    )

    # 3. Nem Bob nem o Admin podem ver a nota privada da Alice ao listar notas
    bob_notes = annotations_service.get_notes(book_id, user_id=bob["id"])
    admin_notes = annotations_service.get_notes(book_id, user_id=admin_user["id"])
    assert all(n["id"] != private_note["id"] for n in bob_notes)
    assert all(n["id"] != private_note["id"] for n in admin_notes)

    # 4. Proteção Anti-IDOR: Bob tenta compartilhar a nota privada da Alice -> PermissionError
    with pytest.raises(PermissionError):
        social_service.share_annotation(
            user_id=bob["id"],
            book_id=book_id,
            source_note_id=private_note["id"],
            visibility="all_users",
        )

    # 5. Alice registra progresso de leitura com compartilhamento DESATIVADO -> não aparece no feed
    progress.save_progress(book_id, current_page=3, total_pages=10, user_id=alice["id"])
    feed_before = social_service.get_nossa_biblioteca_feed(viewer_user_id=bob["id"])
    assert all(item["user_id"] != alice["id"] for item in feed_before["currently_reading"])

    # 6. Alice opta explicitamente por compartilhar leitura atual -> passa a aparecer no feed
    social_service.update_privacy_settings(alice["id"], {"share_current_reading": True})
    feed_after = social_service.get_nossa_biblioteca_feed(viewer_user_id=bob["id"])
    assert any(item["user_id"] == alice["id"] and item["book_id"] == book_id for item in feed_after["currently_reading"])

    # 7. Alice compartilha uma citação pública e Bob comenta nela
    shared = social_service.share_annotation(
        user_id=alice["id"],
        book_id=book_id,
        page_number=3,
        quote_text="O ser humano é aquilo que a educação faz dele.",
        content="Excelente passagem!",
        visibility="all_users",
    )
    comment = social_service.add_annotation_comment(
        shared_annotation_id=shared["id"],
        user_id=bob["id"],
        content="Concordo plenamente, Alice!",
    )
    assert comment["content"] == "Concordo plenamente, Alice!"

    shared_list = social_service.list_shared_annotations(book_id=book_id, viewer_user_id=bob["id"])
    assert len(shared_list) == 1
    assert len(shared_list[0]["comments"]) == 1

    # 8. Alice não pode deletar o comentário do Bob (apenas Bob ou Admin podem)
    with pytest.raises(PermissionError):
        social_service.delete_annotation_comment(comment["id"], user_id=alice["id"])

    # Bob deleta o próprio comentário com sucesso
    assert social_service.delete_annotation_comment(comment["id"], user_id=bob["id"]) is True


def test_social_reviews_favorites_want_to_read_and_recommendations(test_env):
    alice = auth_service.create_user("alice_rev", "AliceSenha123", "Alice", role="reader")
    bob = auth_service.create_user("bob_rev", "BobSenha123", "Bob", role="reader")
    books = library.get_books()
    b1, b2 = books[0]["id"], books[1]["id"]

    # Alice favorita b1 e avalia com 5 estrelas (público)
    social_service.upsert_book_review(
        user_id=alice["id"],
        book_id=b1,
        rating=5,
        review_text="Obra-prima indispensável!",
        is_favorite=True,
        want_to_read=False,
        is_public=True,
    )
    # Alice marca b2 como "Quero Ler"
    social_service.upsert_book_review(
        user_id=alice["id"],
        book_id=b2,
        want_to_read=True,
    )

    fav_books = library.get_books(user_id=alice["id"], favorite_only=True)
    assert [b["id"] for b in fav_books] == [b1]

    wtr_books = library.get_books(user_id=alice["id"], want_to_read_only=True)
    assert [b["id"] for b in wtr_books] == [b2]

    summary = social_service.get_book_reviews_summary(b1, viewer_user_id=bob["id"])
    assert summary["average_rating"] == 5.0
    assert summary["ratings_count"] == 1

    # Alice recomenda b1 para Bob
    rec = social_service.create_recommendation(
        sender_user_id=alice["id"],
        book_id=b1,
        recipient_user_id=bob["id"],
        message="Bob, você vai adorar este livro!",
    )
    bob_recs = social_service.list_user_recommendations(bob["id"])
    assert any(r["id"] == rec["id"] for r in bob_recs["received"])


# ---------------------------------------------------------------------------
# 6. Endpoints do Painel Administrativo e Controle de Permissões
# ---------------------------------------------------------------------------

def test_admin_panel_endpoints_and_role_enforcement(client, test_env):
    # Faz login como o admin criado no teste anterior
    res_login_admin = client.post(
        "/api/auth/login",
        json={"username": "admin_v15", "password": "AdminSenha123"},
    )
    assert res_login_admin.status_code == 200
    csrf = client.cookies.get("lectoria_csrf", "")
    headers = {"X-CSRF-Token": csrf}

    # Dashboard Admin
    res_dash = client.get("/api/admin/dashboard")
    assert res_dash.status_code == 200
    dash = res_dash.json()
    assert "library" in dash
    assert "covers" in dash
    assert "users" in dash
    assert "storage" in dash

    # Diagnósticos da Biblioteca
    res_diag = client.get("/api/admin/library/diagnostics")
    assert res_diag.status_code == 200
    assert "summary" in res_diag.json()

    # Status de Manutenção
    res_maint = client.get("/api/admin/maintenance/status")
    assert res_maint.status_code == 200
    assert res_maint.json()["schema_version"] == 8
    assert res_maint.json()["db_integrity"] == "ok"

    # Cria usuário leitor comum e verifica bloqueio 403 nos endpoints administrativos
    res_u = client.post(
        "/api/users",
        json={"username": "leitor_comum", "password": "LeitorPassword123", "display_name": "Leitor", "role": "reader"},
        headers=headers,
    )
    assert res_u.status_code == 201
    reader_id = res_u.json()["user"]["id"]

    # Revoga sessões do leitor pelo admin
    res_rev = client.post(f"/api/users/{reader_id}/revoke-sessions", headers=headers)
    assert res_rev.status_code == 200

    # Faz login como leitor comum
    client.post("/api/auth/logout", headers=headers)
    res_login = client.post(
        "/api/auth/login",
        json={"username": "leitor_comum", "password": "LeitorPassword123"},
    )
    assert res_login.status_code == 200
    reader_csrf = client.cookies.get("lectoria_csrf", "")
    reader_headers = {"X-CSRF-Token": reader_csrf}

    assert client.get("/api/admin/dashboard").status_code == 403
    assert client.get("/api/admin/library/diagnostics").status_code == 403
    assert client.get("/api/automation/schedules").status_code == 403
    assert client.post("/api/admin/maintenance/clear-cache", json={}, headers=reader_headers).status_code == 403


# ---------------------------------------------------------------------------
# 7. Migração SQLite v7 -> v8 Preservando Dados Existentes
# ---------------------------------------------------------------------------

def test_migration_v7_to_v8_preserves_data(tmp_path, monkeypatch):
    v7_db = tmp_path / "legacy_v7.db"
    conn = sqlite3.connect(str(v7_db))
    cur = conn.cursor()
    cur.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP);")
    cur.execute("INSERT INTO schema_migrations (version) VALUES (7);")
    cur.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'reader',
            is_active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    cur.execute(
        "INSERT INTO users (username, display_name, password_hash, role) VALUES ('vini', 'Vini', 'hash', 'admin');"
    )
    cur.execute(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            filename TEXT NOT NULL UNIQUE,
            file_hash TEXT DEFAULT '',
            filesize INTEGER DEFAULT 100,
            page_count INTEGER DEFAULT 10,
            format TEXT DEFAULT 'pdf',
            identification_status TEXT DEFAULT 'identified',
            identification_score REAL DEFAULT 92.0
        );
        """
    )
    cur.execute(
        "INSERT INTO books (title, author, filename) VALUES ('As Leis', 'Platão', 'as_leis.pdf');"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", v7_db)
    database.init_db()

    conn2 = sqlite3.connect(str(v7_db))
    conn2.row_factory = sqlite3.Row
    v_row = conn2.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    assert v_row["v"] == 8

    book_row = conn2.execute("SELECT * FROM books WHERE filename = 'as_leis.pdf'").fetchone()
    assert book_row["title"] == "As Leis"
    assert "subtitle" in book_row.keys()
    assert "last_error_reason" in book_row.keys()

    priv_row = conn2.execute("SELECT * FROM user_privacy_settings WHERE user_id = 1").fetchone()
    assert priv_row is not None
    assert priv_row["share_current_reading"] == 0
    assert priv_row["share_annotations_default"] == 0

    integrity = conn2.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    conn2.close()
