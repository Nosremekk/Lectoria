"""
Testes automatizados para a Iteração 5 do Lectoria (Estabilização, Segurança e Produção 1.0):
- Validações de segurança e limites de DoS na importação de JSON (profundidade, contagem, coordenadas)
- Limite rígido de tamanho do corpo HTTP (15 MB / HTTP 413)
- Concorrência de backups protegida por Lock
- Fallback seguro com preservação da base pré-restauração (.bak)
- Limpeza manual de retenção de backups (prune_backups)
- Validações de path traversal e integridade
"""

import json
from pathlib import Path
import threading
import pytest
from starlette.testclient import TestClient

import config
from main import app
from database import get_db
from backup_service import (
    create_backup,
    list_backups,
    restore_backup,
    prune_backups,
    verify_sqlite_integrity,
    BackupError,
)
from export_service import (
    validate_import_payload,
    preview_import_annotations,
    apply_import_annotations,
    ImportError,
    MAX_IMPORT_HIGHLIGHTS,
    MAX_IMPORT_NOTES,
    MAX_IMPORT_DRAWINGS,
    MAX_STROKES_PER_DRAWING,
    MAX_POINTS_PER_STROKE,
    MAX_TEXT_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_JSON_DEPTH,
)


@pytest.fixture
def client():
    """Retorna cliente de testes HTTP para a aplicação."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def target_book(client):
    """Obtém um livro existente no banco para os testes de importação e integridade."""
    res = client.get("/api/books")
    assert res.status_code == 200
    books = res.json()["books"]
    assert len(books) > 0
    return books[0]


# =========================================================================
# 1. TESTES DE SEGURANÇA E LIMITES NA IMPORTAÇÃO DE ANOTAÇÕES
# =========================================================================

def test_import_json_depth_limit():
    """Garante que payloads com profundidade excessiva de aninhamento são rejeitados."""
    # Cria uma estrutura aninhada com 15 níveis (acima do limite de 10)
    nested_obj = {"leaf": True}
    for _ in range(15):
        nested_obj = {"nested": nested_obj}

    payload = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [],
        "extra": nested_obj,
    }

    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(payload)
    assert "profundidade de aninhamento" in str(exc_info.value).lower()


def test_import_items_count_limits():
    """Garante que a quantidade de itens por payload respeita os limites configurados."""
    # Testa excesso de destaques
    too_many_highlights = {
        "format": "lectoria_study_export",
        "highlights": [
            {"page": 1, "selected_text": f"Texto {i}", "rects": []}
            for i in range(MAX_IMPORT_HIGHLIGHTS + 1)
        ],
        "notes": [],
        "drawings": [],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(too_many_highlights)
    assert "quantidade de destaques excede o limite" in str(exc_info.value).lower()

    # Testa excesso de notas
    too_many_notes = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [
            {"page": 1, "content": f"Nota {i}"}
            for i in range(MAX_IMPORT_NOTES + 1)
        ],
        "drawings": [],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(too_many_notes)
    assert "quantidade de notas excede o limite" in str(exc_info.value).lower()

    # Testa excesso de desenhos
    too_many_drawings = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [
            {"page": i + 1, "strokes": []}
            for i in range(MAX_IMPORT_DRAWINGS + 1)
        ],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(too_many_drawings)
    assert "quantidade de desenhos excede o limite" in str(exc_info.value).lower()


def test_import_drawing_strokes_and_points_limits():
    """Garante que quantidade de traços e pontos por traço são limitadas."""
    # Excesso de traços em uma única página
    too_many_strokes = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [
            {
                "page": 1,
                "strokes": [{"points": [{"x": 0.1, "y": 0.1}], "width": 2}] * (MAX_STROKES_PER_DRAWING + 1),
            }
        ],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(too_many_strokes)
    assert "quantidade de traços excede o limite" in str(exc_info.value).lower()

    # Excesso de pontos em um único traço
    too_many_points = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [
            {
                "page": 1,
                "strokes": [
                    {
                        "points": [{"x": 0.1, "y": 0.1} for _ in range(MAX_POINTS_PER_STROKE + 1)],
                        "width": 2,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(too_many_points)
    assert "pontos excessivos" in str(exc_info.value).lower()


def test_import_coordinate_and_range_validation():
    """Valida rejeição de coordenadas normalizadas fora da margem (-0.5 a 1.5) e tamanhos de texto."""
    # Coordenada absurda em retângulos
    bad_rect_payload = {
        "format": "lectoria_study_export",
        "highlights": [
            {
                "page": 1,
                "selected_text": "Texto válido",
                "rects": [{"x1": 999.0, "y1": 0.1, "x2": 0.5, "y2": 0.2}],
            }
        ],
        "notes": [],
        "drawings": [],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(bad_rect_payload)
    assert "coordenada normalizada fora dos limites" in str(exc_info.value).lower()

    # Ponto de desenho fora dos limites
    bad_point_payload = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [
            {
                "page": 1,
                "strokes": [
                    {
                        "points": [{"x": -10.0, "y": 0.5}],
                        "width": 2,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(bad_point_payload)
    assert "fora dos limites permitidos" in str(exc_info.value).lower()

    # Espessura de traço inválida
    bad_width_payload = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [],
        "drawings": [
            {
                "page": 1,
                "strokes": [
                    {
                        "points": [{"x": 0.1, "y": 0.1}],
                        "width": 128,  # Máximo é 64
                    }
                ],
            }
        ],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(bad_width_payload)
    assert "espessura do traço inválida" in str(exc_info.value).lower()


def test_import_text_length_limit():
    """Garante que textos gigantescos não provoquem DoS de banco de dados."""
    huge_text_payload = {
        "format": "lectoria_study_export",
        "highlights": [
            {
                "page": 1,
                "selected_text": "A" * (MAX_TEXT_LENGTH + 1),
                "rects": [],
            }
        ],
        "notes": [],
        "drawings": [],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(huge_text_payload)
    assert "excede o tamanho máximo" in str(exc_info.value).lower()

    huge_note_payload = {
        "format": "lectoria_study_export",
        "highlights": [],
        "notes": [
            {
                "page": 1,
                "content": "B" * (MAX_NOTE_LENGTH + 1),
            }
        ],
        "drawings": [],
    }
    with pytest.raises(ImportError) as exc_info:
        validate_import_payload(huge_note_payload)
    assert "excede o limite" in str(exc_info.value).lower()


# =========================================================================
# 2. TESTE DE LIMITE HTTP BODY SIZE (15 MB)
# =========================================================================

def test_import_body_size_limit_via_api(client, target_book):
    """Testa se o endpoint da API rejeita com HTTP 413 corpos maiores que 15 MB."""
    book_id = target_book["id"]

    # Simula requisição com cabeçalho Content-Length excessivo
    headers = {"Content-Length": str(20 * 1024 * 1024), "Content-Type": "application/json"}
    res = client.post(f"/api/books/{book_id}/import/preview", content=b"{}", headers=headers)
    assert res.status_code == 413
    assert "15 MB" in res.json()["detail"]


# =========================================================================
# 3. TESTES DE SEGURANÇA E CONCORRÊNCIA DE BACKUP / RESTORE
# =========================================================================

def test_backup_thread_safety():
    """Verifica se múltiplas chamadas concorrentes a create_backup são executadas sem erros de SQLite."""
    results = []
    errors = []

    def run_backup():
        try:
            res = create_backup()
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run_backup) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Ocorreram erros na execução concorrente: {errors}"
    assert len(results) == 4
    for r in results:
        assert r["integrity"] == "ok"


def test_restore_creates_safety_backup_and_verifies_integrity(target_book):
    """Testa se restore_backup cria a cópia lectoria_pre_restore.db.bak antes de alterar o banco."""
    # 1. Cria um backup
    b_res = create_backup("safety_test")
    filename = b_res["filename"]

    # 2. Executa a restauração
    res = restore_backup(filename)
    assert res["status"] == "success"
    assert res["verified_integrity"] is True

    # 3. Verifica se a cópia de proteção prévia foi criada no diretório data/
    safety_bak = config.DATA_DIR / "lectoria_pre_restore.db.bak"
    assert safety_bak.exists()
    assert verify_sqlite_integrity(safety_bak) is True


def test_prune_backups_preserves_keep_count():
    """Testa a limpeza manual de retenção de backups."""
    # Cria pelo menos 4 backups
    for i in range(4):
        create_backup(f"prune_test_{i}")

    all_backups = list_backups()
    assert len(all_backups) >= 4

    # Preserva apenas os 2 mais recentes
    removed = prune_backups(keep_count=2)
    assert removed >= 2

    remaining = list_backups()
    assert len(remaining) == 2


def test_prune_backups_invalid_keep_count():
    """Garante que keep_count menor que 1 lança exceção BackupError."""
    with pytest.raises(BackupError):
        prune_backups(keep_count=0)


def test_restore_path_traversal_rejection():
    """Garante que tentativas de path traversal na restauração de backup são bloqueadas."""
    with pytest.raises(BackupError) as exc_info:
        restore_backup("../../etc/shadow")
    assert "inválido" in str(exc_info.value).lower() or "não encontrado" in str(exc_info.value).lower()
