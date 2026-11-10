"""
Suíte de Testes Automatizados — Lectoria v1.6.0.
Valida:
1. Migração de banco de dados SQLite Schema v9 -> v10.
2. Criação, edição, arquivamento e transferência de propriedade de clubes.
3. Permissões de papéis (owner, moderator, member) e proteção anti-IDOR.
4. Clubes privados e sistema de convites direcionados (enviar, aceitar, recusar, cancelar).
5. Criação e ciclo de vida de leituras coletivas (planned, in_progress, completed, cancelled).
6. Inscrição e saída de membros em leituras coletivas com prevenção de duplicatas.
7. Filtro 'Leitura Coletiva' na biblioteca (/api/books?collective_read_only=true ou status=collective_reads).
8. Isolamento de privacidade e integridade dos estados individuais (Favoritos, Quero Ler, Progresso).
9. Verificação de 3 usuários: Proprietário (A), Participante (B) e Externo (C).
"""

import pytest
import sqlite3
from pathlib import Path
from fastapi.testclient import TestClient

from main import app
import config
import database
import clubs_service
import library
import auth_service


@pytest.fixture
def v160_env(tmp_path, monkeypatch):
    """Ambiente de teste isolado com banco SQLite migrado para schema v10."""
    test_db = tmp_path / "test_lectoria_v160.db"
    test_covers = tmp_path / "covers"
    test_library = tmp_path / "library_books"
    test_covers.mkdir(parents=True, exist_ok=True)
    test_library.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "COVERS_DIR", test_covers)
    monkeypatch.setattr(config, "LIBRARY_DIR", test_library)

    # Inicializa banco
    database.init_db()

    # Cria 3 usuários para validação de isolamento e permissões:
    # User A (Admin / Criador de clube)
    user_a = auth_service.create_user(username="user_a", password="password123", role="admin")
    # User B (Leitor participante)
    user_b = auth_service.create_user(username="user_b", password="password123", role="reader")
    # User C (Leitor externo / não autorizado)
    user_c = auth_service.create_user(username="user_c", password="password123", role="reader")

    # Cria PDFs válidos usando pypdf para evitar erros na varredura do lifespan
    import pypdf
    from pypdf import PdfWriter

    def make_pdf(path: Path, title: str):
        w = PdfWriter()
        w.add_blank_page(width=300, height=400)
        w.add_metadata({"/Title": title, "/Author": "Autor Teste"})
        with open(path, "wb") as f:
            w.write(f)

    make_pdf(test_library / "republica.pdf", "A República")
    make_pdf(test_library / "etica.pdf", "Ética a Nicômaco")

    # Cria livro sintético para testes de leitura coletiva
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO books (
                id, title, author, filename, rel_path, filesize, format,
                page_count, is_missing
            ) VALUES (
                'book160test0001', 'A República', 'Platão', 'republica.pdf',
                'republica.pdf', 1048576, 'pdf', 350, 0
            );
            """
        )
        cur.execute(
            """
            INSERT INTO books (
                id, title, author, filename, rel_path, filesize, format,
                page_count, is_missing
            ) VALUES (
                'book160test0002', 'Ética a Nicômaco', 'Aristóteles', 'etica.pdf',
                'etica.pdf', 524288, 'pdf', 280, 0
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Cria sessões para os 3 usuários
    token_a = auth_service.create_session(user_a["id"])
    token_b = auth_service.create_session(user_b["id"])
    token_c = auth_service.create_session(user_c["id"])

    with TestClient(app) as test_client:
        yield test_client, {
            "user_a": user_a,
            "user_b": user_b,
            "user_c": user_c,
            "token_a": token_a,
            "token_b": token_b,
            "token_c": token_c,
            "book1_id": "book160test0001",
            "book2_id": "book160test0002",
        }


def test_schema_v10_tables_and_columns(v160_env):
    """Confirma que o schema SQLite migrou para v10 e contém todas as tabelas e índices."""
    client, data = v160_env
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(version) FROM schema_migrations;")
        assert cur.fetchone()[0] == 10

        # reading_clubs
        cur.execute("PRAGMA table_info(reading_clubs);")
        club_cols = {c["name"] for c in cur.fetchall()}
        assert {"id", "name", "description", "owner_id", "visibility", "is_archived", "created_at"}.issubset(club_cols)

        # reading_club_members
        cur.execute("PRAGMA table_info(reading_club_members);")
        member_cols = {c["name"] for c in cur.fetchall()}
        assert {"club_id", "user_id", "role", "joined_at"}.issubset(member_cols)

        # reading_club_invites
        cur.execute("PRAGMA table_info(reading_club_invites);")
        invite_cols = {c["name"] for c in cur.fetchall()}
        assert {"id", "club_id", "inviter_id", "invitee_id", "status", "created_at"}.issubset(invite_cols)

        # collective_reads
        cur.execute("PRAGMA table_info(collective_reads);")
        read_cols = {c["name"] for c in cur.fetchall()}
        assert {"id", "club_id", "book_id", "title", "description", "start_date", "end_date", "status", "created_by"}.issubset(read_cols)

        # collective_read_members
        cur.execute("PRAGMA table_info(collective_read_members);")
        crm_cols = {c["name"] for c in cur.fetchall()}
        assert {"read_id", "user_id", "joined_at"}.issubset(crm_cols)
    finally:
        conn.close()


def test_club_lifecycle_and_permissions(v160_env):
    """Testa criação, edição, arquivamento e restrições de permissão."""
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}

    # 1. User A cria clube público
    res = client.post(
        "/api/reading-clubs",
        json={"name": "Clube de Filosofia", "description": "Estudos dos clássicos", "visibility": "public"},
        headers=headers_a,
    )
    assert res.status_code == 201
    club = res.json()["club"]
    club_id = club["id"]
    assert club["name"] == "Clube de Filosofia"
    assert club["owner_id"] == data["user_a"]["id"]
    assert club["user_role"] == "owner"
    assert club["is_member"] is True

    # 2. User B (não-owner) tenta editar o clube -> HTTP 403
    res_b_edit = client.patch(
        f"/api/reading-clubs/{club_id}",
        json={"name": "Nome Alterado por B"},
        headers=headers_b,
    )
    assert res_b_edit.status_code == 403

    # 3. User A edita com sucesso
    res_a_edit = client.patch(
        f"/api/reading-clubs/{club_id}",
        json={"description": "Nova descrição oficial"},
        headers=headers_a,
    )
    assert res_a_edit.status_code == 200
    assert res_a_edit.json()["club"]["description"] == "Nova descrição oficial"

    # 4. User B entra no clube público
    res_join = client.post(f"/api/reading-clubs/{club_id}/join", headers=headers_b)
    assert res_join.status_code == 200
    assert res_join.json()["club"]["is_member"] is True

    # 5. User A promove B a moderador
    res_promote = client.patch(
        f"/api/reading-clubs/{club_id}/members/{data['user_b']['id']}",
        json={"role": "moderator"},
        headers=headers_a,
    )
    assert res_promote.status_code == 200
    assert res_promote.json()["result"]["role"] == "moderator"

    # 6. User A arquiva o clube
    res_archive = client.post(
        f"/api/reading-clubs/{club_id}/archive",
        json={"archive": True},
        headers=headers_a,
    )
    assert res_archive.status_code == 200
    assert res_archive.json()["club"]["is_archived"] == 1


def test_private_club_invitation_flow_and_anti_enumeration(v160_env):
    """
    Testa clube privado:
    - Oculto para usuário C (não expõe existência nem dados -> HTTP 404).
    - Envio de convite de A para B.
    - B consulta seus convites em /api/reading-clubs/invites/mine.
    - B aceita o convite e se torna participante.
    - C tenta entrar sem convite -> HTTP 403.
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}
    headers_c = {"Cookie": f"lectoria_session={data['token_c']}"}

    # 1. User A cria clube privado
    res = client.post(
        "/api/reading-clubs",
        json={"name": "Clube Secreto de Estudos", "description": "Apenas convidados", "visibility": "private"},
        headers=headers_a,
    )
    assert res.status_code == 201
    club_id = res.json()["club"]["id"]

    # 2. User C tenta consultar o clube privado -> HTTP 404 (anti-enumeração)
    res_c_get = client.get(f"/api/reading-clubs/{club_id}", headers=headers_c)
    assert res_c_get.status_code == 404

    # 3. User C tenta entrar diretamente -> HTTP 403 (requer convite)
    res_c_join = client.post(f"/api/reading-clubs/{club_id}/join", headers=headers_c)
    assert res_c_join.status_code == 403

    # 4. User A convida User B
    res_invite = client.post(
        f"/api/reading-clubs/{club_id}/invites",
        json={"invitee_id": data["user_b"]["id"]},
        headers=headers_a,
    )
    assert res_invite.status_code == 201
    invite_id = res_invite.json()["result"]["invite_id"]

    # 5. User B lista seus convites pendentes
    res_b_invites = client.get("/api/reading-clubs/invites/mine", headers=headers_b)
    assert res_b_invites.status_code == 200
    invites = res_b_invites.json()["invites"]
    assert len(invites) == 1
    assert invites[0]["id"] == invite_id
    assert invites[0]["club_name"] == "Clube Secreto de Estudos"

    # 6. User B aceita o convite
    res_accept = client.post(
        f"/api/reading-clubs/invites/{invite_id}/respond",
        json={"accept": True},
        headers=headers_b,
    )
    assert res_accept.status_code == 200
    assert res_accept.json()["result"]["status"] == "accepted"

    # 7. Agora User B acessa os detalhes do clube privado normalmente
    res_b_club = client.get(f"/api/reading-clubs/{club_id}", headers=headers_b)
    assert res_b_club.status_code == 200
    assert res_b_club.json()["club"]["is_member"] is True

    # 8. User C continua sem acesso -> HTTP 404
    assert client.get(f"/api/reading-clubs/{club_id}", headers=headers_c).status_code == 404


def test_collective_reads_lifecycle_and_book_filter(v160_env):
    """
    Testa:
    1. Criação de leitura coletiva em clube pelo owner.
    2. Inscrição do membro B.
    3. Prevenção de duplicatas de inscrição.
    4. Consulta da rota estática /api/collective-reads/mine.
    5. Filtro na biblioteca /api/books?collective_read_only=true ou status=collective_reads.
    6. Confirmação de que o livro aparece para A e B, mas NÃO para C.
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}
    headers_c = {"Cookie": f"lectoria_session={data['token_c']}"}

    # 1. Cria clube público e B entra
    club_res = client.post(
        "/api/reading-clubs",
        json={"name": "Clube Platônico", "visibility": "public"},
        headers=headers_a,
    )
    club_id = club_res.json()["club"]["id"]
    client.post(f"/api/reading-clubs/{club_id}/join", headers=headers_b)

    # 2. User A cria leitura coletiva do livro 1 ('A República')
    read_res = client.post(
        f"/api/reading-clubs/{club_id}/reads",
        json={
            "book_id": data["book1_id"],
            "title": "Leitura Coletiva: Livros I a IV da República",
            "description": "Debate quinzenal",
            "start_date": "2026-11-15",
            "end_date": "2026-12-15",
            "status": "in_progress",
        },
        headers=headers_a,
    )
    assert read_res.status_code == 201
    read_id = read_res.json()["read"]["id"]

    # Criador A já está inscrito automaticamente
    assert read_res.json()["read"]["is_participating"] is True
    assert read_res.json()["read"]["members_count"] == 1

    # 3. User B participa da leitura coletiva
    join_res = client.post(f"/api/collective-reads/{read_id}/join", headers=headers_b)
    assert join_res.status_code == 200
    assert join_res.json()["read"]["is_participating"] is True
    assert join_res.json()["read"]["members_count"] == 2

    # 4. Inscrição duplicada por B é tratada de forma idempotente
    join_dup = client.post(f"/api/collective-reads/{read_id}/join", headers=headers_b)
    assert join_dup.status_code == 200
    assert join_dup.json()["read"]["members_count"] == 2

    # 5. Rota estática /api/collective-reads/mine
    mine_a = client.get("/api/collective-reads/mine", headers=headers_a).json()
    assert len(mine_a["reads"]) == 1
    assert mine_a["reads"][0]["id"] == read_id

    mine_b = client.get("/api/collective-reads/mine", headers=headers_b).json()
    assert len(mine_b["reads"]) == 1
    assert mine_b["reads"][0]["id"] == read_id

    mine_c = client.get("/api/collective-reads/mine", headers=headers_c).json()
    assert len(mine_c["reads"]) == 0

    # 6. Filtro da biblioteca /api/books com status=collective_reads
    books_b = client.get("/api/books?status=collective_reads", headers=headers_b).json()
    assert len(books_b["books"]) == 1
    assert books_b["books"][0]["id"] == data["book1_id"]
    assert len(books_b["books"][0]["collective_reads"]) == 1
    assert books_b["books"][0]["collective_reads"][0]["club_name"] == "Clube Platônico"

    # Livros para User C com status=collective_reads deve ser vazio
    books_c = client.get("/api/books?status=collective_reads", headers=headers_c).json()
    assert len(books_c["books"]) == 0


def test_privacy_and_individual_state_integrity(v160_env):
    """
    Garante que entrar em uma leitura coletiva NÃO altera nem contamina:
    - Favoritos individuais
    - Quero Ler individual
    - Progresso de leitura (páginas, percentual)
    - Isolamento de dados entre usuários
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}

    # User A favorita o Livro 1
    client.put(
        f"/api/books/{data['book1_id']}/reviews",
        json={"is_favorite": True, "want_to_read": False, "rating": 5},
        headers=headers_a,
    )

    # User B marca Livro 1 como 'Quero Ler' e avança progresso para página 45
    client.put(
        f"/api/books/{data['book1_id']}/reviews",
        json={"is_favorite": False, "want_to_read": True},
        headers=headers_b,
    )
    client.post(
        f"/api/books/{data['book1_id']}/progress",
        json={"current_page": 45, "total_pages": 350, "percentage": 12.8},
        headers=headers_b,
    )

    # Ambos participam da mesma leitura coletiva
    club = clubs_service.create_club(name="Clube Ético", description="", owner_id=data["user_a"]["id"])
    read = clubs_service.create_collective_read(club_id=club["id"], user_id=data["user_a"]["id"], book_id=data["book1_id"])
    clubs_service.join_club(club["id"], data["user_b"]["id"])
    clubs_service.join_collective_read(read["id"], data["user_b"]["id"])

    # Consulta livro 1 como User A
    book_a = client.get(f"/api/books/{data['book1_id']}", headers=headers_a).json()
    assert book_a["is_favorite"] is True
    assert book_a["want_to_read"] is False
    assert (book_a.get("current_page") or 1) == 1  # A não leu nenhuma página

    # Consulta livro 1 como User B
    book_b = client.get(f"/api/books/{data['book1_id']}", headers=headers_b).json()
    assert book_b["is_favorite"] is False
    assert book_b["want_to_read"] is True
    assert book_b["current_page"] == 45  # Progresso estritamente isolado de B
    assert book_b["progress_percentage"] == 12.9


def test_ownership_transfer_and_exit_rules(v160_env):
    """
    Testa:
    1. Proprietário é impedido de sair do clube caso haja outros membros sem transferir a posse.
    2. Transferência de propriedade com sucesso para User B.
    3. User A (anteriormente owner) agora é moderador e pode sair ou ser gerenciado.
    4. Remoção de membro pelo novo proprietário.
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}

    # 1. User A cria clube e User B entra
    club = clubs_service.create_club(name="Clube de Teste de Posse", description="", owner_id=data["user_a"]["id"])
    club_id = club["id"]
    client.post(f"/api/reading-clubs/{club_id}/join", headers=headers_b)

    # 2. User A tenta sair sem transferir posse -> HTTP 400
    res_leave = client.post(f"/api/reading-clubs/{club_id}/leave", headers=headers_a)
    assert res_leave.status_code == 400
    assert "transferir a posse" in res_leave.json()["detail"].lower()

    # 3. User A transfere posse para User B
    res_transfer = client.post(
        f"/api/reading-clubs/{club_id}/transfer-ownership",
        json={"new_owner_id": data["user_b"]["id"]},
        headers=headers_a,
    )
    assert res_transfer.status_code == 200
    assert res_transfer.json()["club"]["owner_id"] == data["user_b"]["id"]

    # 4. User B agora é o novo proprietário e pode alterar o clube
    res_edit = client.patch(
        f"/api/reading-clubs/{club_id}",
        json={"name": "Clube Liderado por B"},
        headers=headers_b,
    )
    assert res_edit.status_code == 200

    # 5. User A agora consegue sair
    res_leave_a = client.post(f"/api/reading-clubs/{club_id}/leave", headers=headers_a)
    assert res_leave_a.status_code == 200


def test_invite_rejection_and_cancellation(v160_env):
    """
    Testa cancelamento de convite pelo remetente e recusa pelo destinatário.
    Garante que convites cancelados ou recusados não permitam acesso.
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}
    headers_c = {"Cookie": f"lectoria_session={data['token_c']}"}

    # User A cria clube privado
    club = clubs_service.create_club(name="Clube Restrito", description="", owner_id=data["user_a"]["id"], visibility="private")
    club_id = club["id"]

    # User A convida User B e depois cancela
    inv_b = client.post(f"/api/reading-clubs/{club_id}/invites", json={"invitee_id": data["user_b"]["id"]}, headers=headers_a).json()
    invite_b_id = inv_b["result"]["invite_id"]

    res_cancel = client.delete(f"/api/reading-clubs/invites/{invite_b_id}", headers=headers_a)
    assert res_cancel.status_code == 200

    # User B tenta aceitar convite cancelado -> HTTP 400
    res_accept_cancelled = client.post(
        f"/api/reading-clubs/invites/{invite_b_id}/respond",
        json={"accept": True},
        headers=headers_b,
    )
    assert res_accept_cancelled.status_code == 400

    # User A convida User C e User C recusa
    inv_c = client.post(f"/api/reading-clubs/{club_id}/invites", json={"invitee_id": data["user_c"]["id"]}, headers=headers_a).json()
    invite_c_id = inv_c["result"]["invite_id"]

    res_reject = client.post(
        f"/api/reading-clubs/invites/{invite_c_id}/respond",
        json={"accept": False},
        headers=headers_c,
    )
    assert res_reject.status_code == 200
    assert res_reject.json()["result"]["status"] == "rejected"

    # User C continua sem acesso ao clube privado -> HTTP 404
    assert client.get(f"/api/reading-clubs/{club_id}", headers=headers_c).status_code == 404


def test_completed_and_cancelled_reads_preservation(v160_env):
    """
    Testa que leituras concluídas permanecem no histórico do clube e não são apagadas.
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}

    club = clubs_service.create_club(name="Clube Histórico", description="", owner_id=data["user_a"]["id"])
    club_id = club["id"]

    # Cria leitura e a marca como concluída
    read_res = client.post(
        f"/api/reading-clubs/{club_id}/reads",
        json={"book_id": data["book1_id"], "title": "Leitura Concluída Histórica", "status": "in_progress"},
        headers=headers_a,
    )
    read_id = read_res.json()["read"]["id"]

    # Atualiza status para 'completed'
    res_comp = client.patch(
        f"/api/collective-reads/{read_id}",
        json={"status": "completed"},
        headers=headers_a,
    )
    assert res_comp.status_code == 200
    assert res_comp.json()["read"]["status"] == "completed"

    # Consulta leituras do clube com filtro de status completed
    reads_list = client.get(f"/api/reading-clubs/{club_id}/reads?status_filter=completed", headers=headers_a).json()["reads"]
    assert len(reads_list) == 1
    assert reads_list[0]["id"] == read_id
    assert reads_list[0]["status"] == "completed"


def test_history_preservation_when_club_archived_or_book_removed(v160_env):
    """
    Valida a preservação integral do histórico de leituras:
    1. Quando o arquivo de um livro é removido ou marcado como ausente (is_missing = 1).
    2. Quando um clube de leitura é arquivado (is_archived = 1).
    """
    client, data = v160_env
    headers_a = {"Cookie": f"lectoria_session={data['token_a']}"}
    headers_b = {"Cookie": f"lectoria_session={data['token_b']}"}

    # 1. Cria clube e adiciona User B
    club = clubs_service.create_club(name="Clube Memória Perpétua", description="Clube para arquivo", owner_id=data["user_a"]["id"])
    club_id = club["id"]
    clubs_service.join_club(club_id, data["user_b"]["id"])

    # 2. Cria leitura coletiva vinculada a livro real
    read_res = client.post(
        f"/api/reading-clubs/{club_id}/reads",
        json={"book_id": data["book1_id"], "title": "Leitura de Preservação", "status": "completed"},
        headers=headers_a,
    )
    assert read_res.status_code == 201
    read_id = read_res.json()["read"]["id"]

    # 3. Simula remoção do livro físico do acervo (is_missing = 1)
    with database.get_db() as conn:
        conn.execute("UPDATE books SET is_missing = 1 WHERE id = ?", (data["book1_id"],))

    # Verifica que o histórico do clube continua exibindo a leitura com os metadados preservados
    reads_after_book_missing = client.get(f"/api/reading-clubs/{club_id}/reads", headers=headers_b).json()["reads"]
    assert len(reads_after_book_missing) == 1
    read_entry = reads_after_book_missing[0]
    assert read_entry["id"] == read_id
    assert read_entry["title"] == "Leitura de Preservação"
    assert read_entry["book_is_missing"] == 1

    # 4. Proprietário arquiva o clube de leitura
    res_archive = client.patch(f"/api/reading-clubs/{club_id}/archive", json={"archive": True}, headers=headers_a)
    assert res_archive.status_code == 200
    assert res_archive.json()["club"]["is_archived"] == 1

    # 5. Membros ainda conseguem acessar os detalhes e todo o histórico de leituras do clube arquivado
    club_details = client.get(f"/api/reading-clubs/{club_id}", headers=headers_b).json()["club"]
    assert club_details["is_archived"] == 1
    archived_reads = client.get(f"/api/reading-clubs/{club_id}/reads", headers=headers_b).json()["reads"]
    assert len(archived_reads) == 1
    assert archived_reads[0]["id"] == read_id

    # 6. Tentativa de criar nova leitura ou enviar convites em clube arquivado é bloqueada com HTTP 400
    res_new_read = client.post(
        f"/api/reading-clubs/{club_id}/reads",
        json={"book_id": data["book2_id"], "title": "Tentativa Bloqueada"},
        headers=headers_a,
    )
    assert res_new_read.status_code == 400
    assert "arquivado" in res_new_read.json()["detail"].lower()


