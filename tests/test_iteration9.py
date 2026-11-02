"""
Testes automatizados da Iteração 9:
Portabilidade de configuração (.env), reconciliação de caminhos entre máquinas,
backup preventivo automático em migrações de schema, preservação de capas
personalizadas no backup/restauração e validação dos scripts de implantação
e diagnóstico para o Positivo C1260 (Linux Mint XFCE).
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import backup_service
import config
import database


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_load_env_file_and_precedence(tmp_path, monkeypatch):
    """
    Garante que o carregador nativo de .env lê variáveis corretamente,
    remove aspas e respeita variáveis de ambiente já definidas no processo.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comentário de teste\n"
        "LECTORIA_PORT=9123\n"
        'LECTORIA_HOST="192.168.1.50"\n'
        "LECTORIA_APP_NAME='Lectoria Casa'\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("LECTORIA_PORT", raising=False)
    monkeypatch.setenv("LECTORIA_HOST", "127.0.0.1")

    loaded = config.load_env_file(env_file, override=False)
    assert loaded["LECTORIA_PORT"] == "9123"
    assert os.environ["LECTORIA_PORT"] == "9123"
    # Não deve sobrescrever variável já presente no ambiente quando override=False
    assert os.environ["LECTORIA_HOST"] == "127.0.0.1"
    assert os.environ["LECTORIA_APP_NAME"] == "Lectoria Casa"


def test_reconcile_default_library_source_when_moved_across_machines(tmp_path, monkeypatch):
    """
    Simula um banco de dados copiado de outra máquina onde a Biblioteca Principal (id=1)
    apontava para um caminho inexistente (/home/antigo/servidor/books).
    Ao rodar init_db() na máquina nova, o caminho da fonte id=1 deve ser reconciliado
    automaticamente para o novo config.LIBRARY_DIR.
    """
    new_books_dir = tmp_path / "novo_servidor" / "books"
    new_books_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "lectoria.db"

    monkeypatch.setattr(config, "LIBRARY_DIR", new_books_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "COVERS_DIR", tmp_path / "data" / "covers")
    monkeypatch.setattr(config, "DB_PATH", db_path)

    database.init_db(db_path)

    # Força um caminho antigo inexistente na Biblioteca Principal (id=1)
    conn = database.get_connection(db_path)
    conn.execute(
        "UPDATE library_sources SET path = ? WHERE id = 1",
        ("/caminho/inexistente/outra_maquina/books",),
    )
    conn.commit()
    conn.close()

    # Executa init_db() novamente simulando boot no Positivo C1260
    database.init_db(db_path)

    conn = database.get_connection(db_path)
    row = conn.execute("SELECT path FROM library_sources WHERE id = 1").fetchone()
    conn.close()

    assert row is not None
    assert Path(row["path"]).resolve() == new_books_dir.resolve()


def test_schema_migration_creates_safety_backup_and_preserves_all_data(tmp_path, monkeypatch):
    """
    Cria um banco SQLite na versão de schema V3 populado com livro, progresso,
    coleção, destaque e nota, e verifica se init_db() cria o backup preventivo
    automático (lectoria_pre_migration_v3_to_v5.db.bak) e migra para V5 sem perda.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    books_dir = tmp_path / "books"
    books_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "COVERS_DIR", data_dir / "covers")
    monkeypatch.setattr(config, "LIBRARY_DIR", books_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)

    # Constrói banco legado autêntico em Schema V3 usando as migrações V1..V3
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    database._apply_migration_v1(cur)
    cur.execute("INSERT INTO schema_migrations (version) VALUES (1);")
    database._apply_migration_v2(cur)
    cur.execute("INSERT INTO schema_migrations (version) VALUES (2);")
    database._apply_migration_v3(cur)
    cur.execute("INSERT INTO schema_migrations (version) VALUES (3);")

    cur.execute(
        "INSERT INTO books (id, title, author, filename, rel_path, filesize, page_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("book_legacy_1", "Livro Legado", "Machado", "legado.pdf", "legado.pdf", 12345, 120),
    )
    cur.execute(
        "INSERT INTO reading_progress (user_id, book_id, current_page, total_pages, percentage) VALUES (?, ?, ?, ?, ?)",
        (1, "book_legacy_1", 45, 120, 37.5),
    )
    cur.execute("INSERT INTO collections (id, name) VALUES (1, 'Clássicos')")
    cur.execute("INSERT INTO collection_books (collection_id, book_id) VALUES (1, 'book_legacy_1')")
    cur.execute(
        "INSERT INTO highlights (id, book_id, page, color, selected_text, rects_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("hl_1", "book_legacy_1", 12, "#fef08a", "Trecho importante", '[{"x":0.1,"y":0.2,"w":0.3,"h":0.05}]'),
    )
    cur.execute(
        "INSERT INTO notes (id, book_id, page, highlight_id, content) VALUES (?, ?, ?, ?, ?)",
        ("note_1", "book_legacy_1", 12, "hl_1", "Minha anotação de estudo"),
    )
    cur.execute(
        "INSERT INTO page_drawings (id, book_id, page, strokes_json) VALUES (?, ?, ?, ?)",
        ("draw_1", "book_legacy_1", 12, '[{"id":"s1","points":[[0.1,0.1],[0.2,0.2]]}]'),
    )
    conn.commit()
    conn.close()

    # Executa init_db() para migrar V3 -> V5
    database.init_db(db_path)

    # Verifica se o backup preventivo pré-migração foi gerado
    safety_bak = data_dir / f"lectoria_pre_migration_v3_to_v{database.SCHEMA_VERSION}.db.bak"
    assert safety_bak.exists()
    assert backup_service.verify_sqlite_integrity(safety_bak)

    # Verifica integridade e preservação de todos os dados no banco migrado
    assert backup_service.verify_sqlite_integrity(db_path)
    conn = database.get_connection(db_path)
    cur = conn.cursor()
    version = cur.execute("SELECT MAX(version) FROM schema_migrations;").fetchone()[0]
    assert version == database.SCHEMA_VERSION

    book = cur.execute("SELECT title, page_count, cover_type FROM books WHERE id = 'book_legacy_1'").fetchone()
    assert book["title"] == "Livro Legado"
    assert book["page_count"] == 120
    assert book["cover_type"] == "none"

    prog = cur.execute(
        "SELECT current_page FROM reading_progress WHERE user_id = 1 AND book_id = 'book_legacy_1'"
    ).fetchone()
    assert prog is not None and prog["current_page"] == 45

    hl = cur.execute("SELECT selected_text, user_id FROM highlights WHERE id = 'hl_1'").fetchone()
    assert hl["selected_text"] == "Trecho importante"
    assert hl["user_id"] == 1

    note = cur.execute("SELECT content, user_id FROM notes WHERE id = 'note_1'").fetchone()
    assert note["content"] == "Minha anotação de estudo"
    assert note["user_id"] == 1

    draw = cur.execute("SELECT strokes_json, user_id FROM page_drawings WHERE id = 'draw_1'").fetchone()
    assert "s1" in draw["strokes_json"]
    assert draw["user_id"] == 1
    conn.close()


def test_backup_and_restore_preserves_custom_covers(tmp_path, monkeypatch):
    """
    Verifica se create_backup() inclui capas personalizadas (*_custom.*)
    e se restore_backup() restaura tanto o banco SQLite quanto as capas personalizadas,
    além de limpar diretórios de capas ao executar prune_backups().
    """
    data_dir = tmp_path / "data"
    covers_dir = data_dir / "covers"
    backups_dir = tmp_path / "backups"
    books_dir = tmp_path / "books"
    db_path = data_dir / "lectoria.db"

    for d in (data_dir, covers_dir, backups_dir, books_dir):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "COVERS_DIR", covers_dir)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", books_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)

    database.init_db(db_path)

    # Cria uma capa personalizada simulada
    custom_cover = covers_dir / "bookabc_custom.png"
    custom_cover.write_bytes(b"\x89PNG\r\n\x1a\nCUSTOM_COVER_DATA")

    res = backup_service.create_backup("homolog")
    assert res["integrity"] == "ok"
    assert res["custom_covers_count"] == 1

    # Remove a capa personalizada do diretório ativo e verifica se o restore a recupera
    custom_cover.unlink()
    assert not custom_cover.exists()

    restore_res = backup_service.restore_backup(res["filename"])
    assert restore_res["verified_integrity"] is True
    assert restore_res["restored_covers_count"] == 1
    assert custom_cover.exists()
    assert custom_cover.read_bytes() == b"\x89PNG\r\n\x1a\nCUSTOM_COVER_DATA"


def test_deployment_scripts_and_diagnostics(tmp_path):
    """
    Valida a existência, permissão de execução e sintaxe de todos os scripts
    de deploy (install.sh, configure.sh, update.sh, backup.sh, restore.sh,
    uninstall.sh, diagnose.sh), bem como a execução de configure.sh e diagnose.py.
    """
    expected_scripts = [
        PROJECT_ROOT / "deployment" / "install.sh",
        PROJECT_ROOT / "deployment" / "configure.sh",
        PROJECT_ROOT / "deployment" / "update.sh",
        PROJECT_ROOT / "deployment" / "backup.sh",
        PROJECT_ROOT / "deployment" / "restore.sh",
        PROJECT_ROOT / "deployment" / "uninstall.sh",
        PROJECT_ROOT / "deployment" / "diagnose.sh",
        PROJECT_ROOT / "scripts" / "diagnose.py",
    ]
    for script in expected_scripts:
        assert script.exists(), f"Script ausente: {script}"
        assert os.access(script, os.X_OK), f"Script sem permissão de execução: {script}"

    # Testa configure.sh gerando .env e lectoria.service em diretórios temporários com espaço no nome
    spaced_dir = tmp_path / "Servidor Positivo C1260"
    spaced_dir.mkdir(parents=True, exist_ok=True)
    env_out = spaced_dir / ".env"
    service_out = spaced_dir / "lectoria.service"
    lib_dir = spaced_dir / "Meus Livros"
    data_dir = spaced_dir / "data"
    backups_dir = spaced_dir / "backups"

    cmd = [
        "bash",
        str(PROJECT_ROOT / "deployment" / "configure.sh"),
        "--non-interactive",
        "--port",
        "8765",
        "--host",
        "0.0.0.0",
        "--library-dir",
        str(lib_dir),
        "--data-dir",
        str(data_dir),
        "--backups-dir",
        str(backups_dir),
        "--env-file",
        str(env_out),
        "--service-file",
        str(service_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert env_out.exists()
    assert service_out.exists()

    env_text = env_out.read_text(encoding="utf-8")
    assert "LECTORIA_PORT=8765" in env_text
    assert str(lib_dir) in env_text

    service_text = service_out.read_text(encoding="utf-8")
    assert "MemoryMax=512M" in service_text
    assert "CPUWeight=100" in service_text
    assert f'ReadWritePaths="{data_dir}" "{backups_dir}"' in service_text

    # Executa scripts/diagnose.py --json usando o ambiente recém-configurado
    diag_env = os.environ.copy()
    diag_env["LECTORIA_ENV_FILE"] = str(env_out)
    diag_env["LECTORIA_LIBRARY_DIR"] = str(lib_dir)
    diag_env["LECTORIA_DATA_DIR"] = str(data_dir)
    diag_env["LECTORIA_COVERS_DIR"] = str(data_dir / "covers")
    diag_env["LECTORIA_BACKUPS_DIR"] = str(backups_dir)

    diag_proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "diagnose.py"), "--json"],
        capture_output=True,
        text=True,
        env=diag_env,
        check=True,
    )
    diag_data = json.loads(diag_proc.stdout)
    assert diag_data["status"] == "ok"
    assert diag_data["dependencies"]["ok"] is True
    assert diag_data["directories"]["ok"] is True
    assert diag_data["database"]["ok"] is True
    assert diag_data["database"]["schema_version"] == database.SCHEMA_VERSION
