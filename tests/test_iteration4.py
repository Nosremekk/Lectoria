"""
Testes automatizados para a Iteração 4 do Lectoria:
- Exportação em Markdown (.md) e JSON (.json)
- Importação de anotações com preview e prevenção de duplicatas
- Transação atômica e rollback em caso de falha
- Backup manual SQLite com verificação de integridade e restauração
- Comportamento em documentos e navegação
"""

import json
from pathlib import Path
import sqlite3
import pytest
from starlette.testclient import TestClient

import config
from main import app
from database import get_db
from backup_service import (
    create_backup,
    list_backups,
    restore_backup,
    verify_sqlite_integrity,
    BackupError,
)
from export_service import (
    export_book_annotations_markdown,
    export_book_annotations_json,
    preview_import_annotations,
    apply_import_annotations,
    ImportError,
)


@pytest.fixture
def client():
    """Retorna cliente de testes HTTP para a aplicação."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def setup_study_data(client):
    """Configura anotações de teste em um livro existente da biblioteca."""
    books = client.get("/api/books").json()["books"]
    assert len(books) > 0, "Deve haver pelo menos um livro cadastrado"
    book_id = books[0]["id"]

    # Limpa anotações anteriores do livro
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM highlights WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM notes WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM page_drawings WHERE book_id = ?", (book_id,))

        # Insere destaque na página 2
        cursor.execute(
            """
            INSERT INTO highlights (id, book_id, page, color, rects_json, selected_text, text_context)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("hl-1", book_id, 2, "#fef08a", json.dumps([{"x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.15}]), "O homem é por natureza um animal político.", "Contexto...")
        )

        # Insere nota associada ao destaque
        cursor.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("nt-1", book_id, 2, "hl-1", "Comentário central sobre a Polis.", "#fef08a")
        )

        # Insere nota independente na página 5
        cursor.execute(
            """
            INSERT INTO notes (id, book_id, page, highlight_id, content, color)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("nt-2", book_id, 5, None, "Lembrar de pesquisar sobre a Ética a Nicômaco.", "#a7f3d0")
        )

        # Insere desenho na página 5
        cursor.execute(
            """
            INSERT INTO page_drawings (id, book_id, page, strokes_json)
            VALUES (?, ?, ?, ?)
            """,
            ("dr-1", book_id, 5, json.dumps([{"color": "#3d7cf4", "width": 4, "points": [{"x": 0.2, "y": 0.2}, {"x": 0.3, "y": 0.3}]}]))
        )

    yield book_id

    # Limpeza final das anotações do teste
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM highlights WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM notes WHERE book_id = ?", (book_id,))
        cursor.execute("DELETE FROM page_drawings WHERE book_id = ?", (book_id,))


def test_markdown_export(setup_study_data, client):
    """Testa geração de relatório em Markdown tanto via serviço quanto via HTTP."""
    book_id = setup_study_data
    book_info = client.get(f"/api/books/{book_id}").json()

    # Via serviço direto
    md_content = export_book_annotations_markdown(book_id)
    assert "# Caderno de Leituras e Estudos" in md_content
    assert book_info["title"] in md_content
    assert "O homem é por natureza um animal político." in md_content
    assert "Comentário central sobre a Polis." in md_content
    assert "Lembrar de pesquisar sobre a Ética a Nicômaco." in md_content
    assert "## Página 2" in md_content
    assert "## Página 5" in md_content

    # Via endpoint HTTP
    response = client.get(f"/api/books/{book_id}/export/markdown")
    assert response.status_code == 200
    assert "text/markdown" in response.headers.get("content-type", "")
    assert "attachment;" in response.headers.get("content-disposition", "")
    assert response.text == md_content


def test_json_export(setup_study_data, client):
    """Testa exportação estruturada em JSON com integridade de dados."""
    book_id = setup_study_data
    book_info = client.get(f"/api/books/{book_id}").json()

    # Via serviço direto
    data = export_book_annotations_json(book_id)
    assert data["version"] == "1.0"
    assert data["format"] == "lectoria_study_export"
    assert data["book"]["id"] == book_id
    assert len(data["highlights"]) == 1
    assert len(data["notes"]) == 2
    assert len(data["drawings"]) == 1
    assert data["highlights"][0]["selected_text"] == "O homem é por natureza um animal político."
    assert len(data["drawings"][0]["strokes"]) == 1

    # Via endpoint HTTP
    response = client.get(f"/api/books/{book_id}/export/json")
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    body = response.json()
    assert body["book"]["title"] == book_info["title"]
    assert len(body["highlights"]) == 1


def test_import_preview_and_duplicates(setup_study_data, client):
    """Testa a análise de preview, detectando duplicatas sem alterar o banco."""
    book_id = setup_study_data

    export_data = export_book_annotations_json(book_id)

    # 1. Faz preview dos mesmos dados já existentes no banco (tudo deve ser duplicata)
    preview = preview_import_annotations(book_id, export_data)
    assert preview["valid"] is True
    assert preview["counts"]["highlights_new"] == 0
    assert preview["counts"]["highlights_duplicate"] == 1
    assert preview["counts"]["notes_new"] == 0
    assert preview["counts"]["notes_duplicate"] == 2
    assert preview["counts"]["drawings_new"] == 0
    assert preview["counts"]["drawings_duplicate"] == 1

    # 2. Adiciona um novo destaque e uma nova nota ao payload
    export_data["highlights"].append({
        "id": "hl-new-99",
        "page": 10,
        "color": "#bae6fd",
        "rects": [{"x1": 0.2, "y1": 0.2, "x2": 0.6, "y2": 0.25}],
        "selected_text": "A coragem é a primeira das qualidades humanas.",
    })
    export_data["notes"].append({
        "id": "nt-new-99",
        "page": 10,
        "highlight_id": "hl-new-99",
        "content": "Excelente citação sobre coragem.",
    })

    preview2 = preview_import_annotations(book_id, export_data)
    assert preview2["counts"]["highlights_new"] == 1
    assert preview2["counts"]["highlights_duplicate"] == 1
    assert preview2["counts"]["notes_new"] == 1
    assert preview2["counts"]["notes_duplicate"] == 2
    assert preview2["counts"]["total_new_items"] == 2

    # Via endpoint HTTP
    resp = client.post(f"/api/books/{book_id}/import/preview", json=export_data)
    assert resp.status_code == 200
    assert resp.json()["preview"]["counts"]["total_new_items"] == 2


def test_import_confirmation_transaction(setup_study_data, client):
    """Testa a aplicação definitiva da importação com consistência transacional."""
    book_id = setup_study_data

    new_data = {
        "version": "1.0",
        "format": "lectoria_study_export",
        "highlights": [
            {
                "id": "hl-imported-1",
                "page": 20,
                "color": "#fed7aa",
                "rects": [{"x1": 0.1, "y1": 0.2, "x2": 0.8, "y2": 0.25}],
                "selected_text": "Texto importado com sucesso para a página 20.",
            }
        ],
        "notes": [
            {
                "id": "nt-imported-1",
                "page": 20,
                "highlight_id": "hl-imported-1",
                "content": "Nota importada vinculada.",
            }
        ],
        "drawings": [
            {
                "page": 20,
                "strokes": [{"color": "#f87171", "width": 2, "points": [{"x": 0.1, "y": 0.1}]}]
            }
        ]
    }

    resp = client.post(f"/api/books/{book_id}/import/confirm", json=new_data)
    assert resp.status_code == 200
    result = resp.json()["result"]["imported_counts"]
    assert result["highlights"] == 1
    assert result["notes"] == 1
    assert result["drawings"] == 1
    assert result["total"] == 3

    # Verifica no banco se realmente foram persistidos
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT selected_text FROM highlights WHERE book_id = ? AND page = 20", (book_id,))
        hl_row = cursor.fetchone()
        assert hl_row is not None
        assert hl_row[0] == "Texto importado com sucesso para a página 20."

        cursor.execute("SELECT content FROM notes WHERE book_id = ? AND page = 20", (book_id,))
        note_row = cursor.fetchone()
        assert note_row is not None
        assert note_row[0] == "Nota importada vinculada."


def test_import_rollback_on_error(setup_study_data, monkeypatch):
    """Garante que qualquer erro durante a importação reverta completamente a transação SQLite."""
    book_id = setup_study_data

    # Conta registros atuais antes da falha simulada
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM highlights WHERE book_id = ?", (book_id,))
        orig_hl_count = cursor.fetchone()[0]

    bad_payload = {
        "version": "1.0",
        "format": "lectoria_study_export",
        "highlights": [
            {
                "page": 30,
                "selected_text": "Este destaque seria inserido se não houvesse erro posterior.",
                "rects": []
            }
        ],
        "notes": [
            {
                "page": "nao_inteiro_invalido",
                "content": "Força erro de conversão e dispara rollback total da transação"
            }
        ]
    }

    with pytest.raises(Exception):
        apply_import_annotations(book_id, bad_payload)

    # Verifica se o rollback foi honrado: a contagem de highlights deve ser exatamente a original
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM highlights WHERE book_id = ?", (book_id,))
        post_count = cursor.fetchone()[0]
        assert post_count == orig_hl_count


def test_malformed_json_handling(client, setup_study_data):
    """Testa tratamento de formatos de importação corrompidos ou inválidos."""
    book_id = setup_study_data

    # Não é objeto
    resp = client.post(f"/api/books/{book_id}/import/preview", json=["lista_invalida"])
    assert resp.status_code == 400

    # Falta de formato compatível
    resp = client.post(f"/api/books/{book_id}/import/preview", json={"campo_aleatorio": 123})
    assert resp.status_code == 400


def test_backup_and_restore_operations(tmp_path):
    """Testa criação, listagem, verificação e integridade do mecanismo de backup."""
    # 1. Criação de backup
    res = create_backup(custom_name="test")
    assert res["integrity"] == "ok"
    assert Path(res["path"]).exists()
    assert res["size_bytes"] > 0

    # 2. Listagem de backups
    backups = list_backups()
    assert len(backups) >= 1
    assert any(b["filename"] == res["filename"] for b in backups)

    # 3. Verificação de integridade
    assert verify_sqlite_integrity(Path(res["path"])) is True

    # 4. Restauração
    restore_res = restore_backup(res["filename"])
    assert restore_res["status"] == "success"
    assert restore_res["verified_integrity"] is True

    # 5. Tentativa de restaurar backup inexistente deve lançar BackupError
    with pytest.raises(BackupError):
        restore_backup("arquivo_fantasma_nao_existente.db")
