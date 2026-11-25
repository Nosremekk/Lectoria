"""
Testes da Fase 11.4 — Restauração segura de backups, cenários de falha/segurança
e invariantes de hardening residual Docker/Compose.
"""

from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tarfile
import zipfile

from fastapi.testclient import TestClient
import pytest

import backup_service
import config
import database
import main as main_module


REPO_ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_minimal_pdf(path: Path) -> None:
    """Cria um PDF mínimo válido (1 página) para testes sintéticos isolados."""
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer << /Size 4 /Root 1 0 R >>\n"
        b"startxref\n190\n%%EOF\n"
    )
    path.write_bytes(content)


def _create_minimal_epub(path: Path) -> None:
    """Cria um EPUB mínimo válido (ZIP + container.xml + OPF + XHTML) para testes sintéticos."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:identifier id=\"uid\">urn:uuid:synthetic-114</dc:identifier>"
            "<dc:title>Livro EPUB Sintetico 11.4</dc:title>"
            "<dc:creator>Autora Teste</dc:creator>"
            "<dc:language>pt-BR</dc:language>"
            "</metadata>"
            "<manifest><item id=\"ch1\" href=\"ch1.xhtml\" media-type=\"application/xhtml+xml\"/></manifest>"
            "<spine><itemref idref=\"ch1\"/></spine>"
            "</package>",
        )
        zf.writestr(
            "OEBPS/ch1.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cap 1</title></head>'
            "<body><h1>Capitulo 1</h1><p>Conteudo sintetico de teste.</p></body></html>",
        )


def _create_minimal_png(path: Path) -> None:
    """PNG 16x16 válido mínimo para simular capa personalizada."""
    import struct, zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w, h = 16, 16
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + b"\x20\x40\x60" * w for _ in range(h))
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw_scanlines))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)


@contextmanager
def _isolated_lectoria_env(monkeypatch, data_dir: Path, books_dir: Path, backups_dir: Path):
    """Configura diretórios isolados em config.* para executar a aplicação sem tocar em nada externo."""
    data_dir.mkdir(parents=True, exist_ok=True)
    books_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    covers_dir = data_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "lectoria.db"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "COVERS_DIR", covers_dir)
    monkeypatch.setattr(config, "LIBRARY_DIR", books_dir)
    monkeypatch.setattr(config, "BACKUPS_DIR", backups_dir)
    yield db_path, covers_dir


def _auth_headers(client: TestClient) -> dict:
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    token = r.json()["csrf_token"]
    return {"x-csrf-token": token}


def _create_snapshot_backup(db_path: Path, covers_dir: Path, snapshot_dir: Path) -> None:
    """Simula exatamente o formato de snapshot do servidor (lectoria.db via Online Backup + covers.tar.gz + commit.txt)."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(db_path), timeout=15.0)
    dst = sqlite3.connect(str(snapshot_dir / "lectoria.db"))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    with tarfile.open(snapshot_dir / "covers.tar.gz", "w:gz") as tf:
        if covers_dir.exists():
            for item in sorted(covers_dir.iterdir()):
                if item.is_file():
                    tf.add(item, arcname=f"covers/{item.name}")
    (snapshot_dir / "commit.txt").write_text("4d682e3d2c611b3e67062a51f07c922a20aadecf\n", encoding="utf-8")


# ==============================================================================
# 1. RESTAURAÇÃO FUNCIONAL INTEGRAL COM DADOS SINTÉTICOS REPRESENTATIVOS
# ==============================================================================

def test_v192_full_synthetic_backup_restore_and_user_isolation(tmp_path, monkeypatch):
    """
    Valida restauração ponta a ponta em ambiente 100% isolado:
    - SQLite + PRAGMA integrity_check + Schema 13;
    - Inicialização da aplicação e login após invalidação de sessões antigas;
    - Preservação de usuários, metadados manuais, progresso, notas, destaques, coleções,
      conceitos/reflexões/conexões do Atlas e capas personalizadas;
    - Abertura de PDF (com Range 206) e EPUB;
    - Isolamento rigoroso entre usuários (alice vs bob);
    - Imutabilidade total dos arquivos de origem (books e snapshot).
    """
    origin_data = tmp_path / "origin_data"
    origin_books = tmp_path / "origin_books"
    origin_backups = tmp_path / "origin_backups"
    snapshot_dir = tmp_path / "snapshot_2026_11_25"

    origin_books.mkdir(parents=True)
    pdf_file = origin_books / "manual_sintetico.pdf"
    epub_file = origin_books / "romance_sintetico.epub"
    _create_minimal_pdf(pdf_file)
    _create_minimal_epub(epub_file)
    pdf_hash_before = _sha256(pdf_file)
    epub_hash_before = _sha256(epub_file)

    with _isolated_lectoria_env(monkeypatch, origin_data, origin_books, origin_backups) as (origin_db, origin_covers):
        database.init_db(origin_db)
        with TestClient(main_module.app) as client:
            # 1. Cria admin Alice e usuário comum Bob
            r_setup = client.post(
                "/api/auth/setup",
                json={"username": "alice_admin", "password": "AlicePassword!2026", "display_name": "Alice"},
                headers=_auth_headers(client),
            )
            assert r_setup.status_code in (200, 201), r_setup.text
            old_alice_session_cookie = client.cookies.get("lectoria_session")
            assert old_alice_session_cookie

            r_bob = client.post(
                "/api/users",
                json={
                    "username": "bob_reader",
                    "password": "BobPassword!2026",
                    "display_name": "Bob",
                    "role": "reader",
                },
                headers=_auth_headers(client),
            )
            assert r_bob.status_code in (200, 201), r_bob.text

            # 2. Livros sintéticos (PDF + EPUB) já indexados no startup da aplicação
            books = client.get("/api/books").json()["books"]
            assert len(books) == 2
            pdf_book = next(b for b in books if b["filename"].endswith(".pdf"))
            epub_book = next(b for b in books if b["filename"].endswith(".epub"))

            # 3. Metadados manuais, progresso, nota, destaque, coleção e Atlas (Alice)
            r_meta = client.put(
                f"/api/books/{pdf_book['id']}/metadata",
                json={"title": "Titulo Manual Preservado", "author": "Autora Preservada"},
                headers=_auth_headers(client),
            )
            assert r_meta.status_code == 200, r_meta.text

            r_prog = client.post(
                f"/api/books/{pdf_book['id']}/progress",
                json={"current_page": 1, "total_pages": 1},
                headers=_auth_headers(client),
            )
            assert r_prog.status_code == 200, r_prog.text

            r_hl = client.post(
                f"/api/books/{pdf_book['id']}/highlights",
                json={
                    "page": 1,
                    "selected_text": "Trecho destacado para restauracao",
                    "color": "yellow",
                    "rects": [{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.05}],
                },
                headers=_auth_headers(client),
            )
            assert r_hl.status_code in (200, 201), r_hl.text

            r_note = client.post(
                f"/api/books/{pdf_book['id']}/notes",
                json={"page": 1, "content": "Nota privada exclusiva da Alice"},
                headers=_auth_headers(client),
            )
            assert r_note.status_code in (200, 201), r_note.text

            r_col = client.post(
                "/api/collections",
                json={"name": "Colecao Restaurada", "description": "Teste 11.4"},
                headers=_auth_headers(client),
            )
            assert r_col.status_code in (200, 201), r_col.text
            col_id = r_col.json()["collection"]["id"] if "collection" in r_col.json() else r_col.json()["id"]
            r_col_add = client.post(
                f"/api/collections/{col_id}/books",
                json={"book_id": pdf_book["id"]},
                headers=_auth_headers(client),
            )
            assert r_col_add.status_code in (200, 201), r_col_add.text

            # Atlas: conceito + reflexão + conexão
            r_c = client.post(
                "/api/knowledge/concepts",
                json={"name": "Resiliencia Operacional", "description": "Conceito privado Alice"},
                headers=_auth_headers(client),
            )
            assert r_c.status_code in (200, 201), r_c.text
            concept_id = r_c.json()["concept"]["id"]

            r_ref = client.post(
                "/api/knowledge/reflections",
                json={"title": "Reflexao de Backup", "body_markdown": "Conteudo Markdown restaurado."},
                headers=_auth_headers(client),
            )
            assert r_ref.status_code in (200, 201), r_ref.text
            reflection_id = r_ref.json()["reflection"]["id"]

            r_link = client.post(
                "/api/knowledge/links",
                json={
                    "source_type": "concept",
                    "source_id": str(concept_id),
                    "target_type": "reflection",
                    "target_id": str(reflection_id),
                    "relation_type": "relates_to",
                },
                headers=_auth_headers(client),
            )
            assert r_link.status_code in (200, 201), r_link.text

            # Capa personalizada sintética via API real de upload de capa
            tmp_png = tmp_path / "custom_cover_upload.png"
            _create_minimal_png(tmp_png)
            custom_cover_bytes = tmp_png.read_bytes()
            custom_cover_hash = hashlib.sha256(custom_cover_bytes).hexdigest()
            r_cov_up = client.post(
                f"/api/books/{pdf_book['id']}/cover",
                content=custom_cover_bytes,
                headers={**_auth_headers(client), "content-type": "image/png"},
            )
            assert r_cov_up.status_code == 200, r_cov_up.text
            custom_cover_files = list(origin_covers.glob(f"{pdf_book['id']}_custom.*"))
            assert len(custom_cover_files) == 1
            custom_cover = custom_cover_files[0]

        # 4. Gera snapshot completo e registra hashes da origem
        _create_snapshot_backup(origin_db, origin_covers, snapshot_dir)
        snapshot_db_hash = _sha256(snapshot_dir / "lectoria.db")
        snapshot_tar_hash = _sha256(snapshot_dir / "covers.tar.gz")

    # 5. Restaura o snapshot em diretório 100% isolado
    restored_data = tmp_path / "restored_data"
    restored_backups = tmp_path / "restored_backups"
    res = backup_service.restore_snapshot_to_directory(
        snapshot_dir,
        restored_data,
        allow_overwrite=False,
        invalidate_sessions=True,
    )
    assert res["status"] == "success"
    assert res["verified_integrity"] is True
    assert res["schema_version"] == database.SCHEMA_VERSION
    assert res["restored_covers_count"] == 1
    assert res["invalidated_sessions_count"] >= 1

    # Origem intacta (zero alterações)
    assert _sha256(snapshot_dir / "lectoria.db") == snapshot_db_hash
    assert _sha256(snapshot_dir / "covers.tar.gz") == snapshot_tar_hash
    assert _sha256(pdf_file) == pdf_hash_before
    assert _sha256(epub_file) == epub_hash_before

    # 6. Sobe a aplicação contra o diretório restaurado
    with _isolated_lectoria_env(monkeypatch, restored_data, origin_books, restored_backups) as (restored_db, restored_covers):
        assert backup_service.verify_sqlite_integrity(restored_db)
        assert _sha256(restored_covers / custom_cover.name) == custom_cover_hash

        with TestClient(main_module.app) as client:
            # Sessão anterior do backup FOI invalidada
            client.cookies.set("lectoria_session", old_alice_session_cookie)
            r_old = client.get("/api/auth/me")
            assert r_old.status_code == 401
            client.cookies.clear()

            # Novo login da Alice funciona com a senha preservada
            r_login = client.post(
                "/api/auth/login",
                json={"username": "alice_admin", "password": "AlicePassword!2026"},
                headers=_auth_headers(client),
            )
            assert r_login.status_code == 200, r_login.text

            # Verifica metadados manuais, progresso, notas, destaques, coleções e Atlas
            restored_books = client.get("/api/books").json()["books"]
            assert len(restored_books) == 2
            restored_pdf = next(b for b in restored_books if b["id"] == pdf_book["id"])
            assert restored_pdf["title"] == "Titulo Manual Preservado"
            assert restored_pdf["author"] == "Autora Preservada"

            assert client.get(f"/api/books/{pdf_book['id']}/progress").json()["current_page"] == 1
            ann = client.get(f"/api/books/{pdf_book['id']}/annotations").json()
            hls = ann.get("highlights", [])
            assert any(
                (h.get("selected_text") or h.get("text")) == "Trecho destacado para restauracao"
                for h in hls
            )
            notes = ann.get("notes", [])
            assert any(n["content"] == "Nota privada exclusiva da Alice" for n in notes)

            cols = client.get("/api/collections").json()["collections"]
            assert any(c["name"] == "Colecao Restaurada" for c in cols)

            concepts = client.get("/api/knowledge/concepts").json().get("items") or client.get("/api/knowledge/concepts").json().get("concepts", [])
            assert any(c["name"] == "Resiliencia Operacional" for c in concepts)
            refs = client.get("/api/knowledge/reflections").json().get("items") or client.get("/api/knowledge/reflections").json().get("reflections", [])
            assert any(rf["title"] == "Reflexao de Backup" for rf in refs)

            # Capa restaurada servida corretamente
            r_cov = client.get(f"/api/books/{pdf_book['id']}/cover")
            assert r_cov.status_code == 200
            assert hashlib.sha256(r_cov.content).hexdigest() == custom_cover_hash

            # PDF (com Range 206) e EPUB abrem normalmente
            r_pdf = client.get(f"/api/books/{pdf_book['id']}/file", headers={"Range": "bytes=0-31"})
            assert r_pdf.status_code == 206
            assert r_pdf.content.startswith(b"%PDF-1.4")

            r_epub = client.get(f"/api/books/{epub_book['id']}/file")
            assert r_epub.status_code == 200
            assert r_epub.content.startswith(b"PK")

        # 7. Isolamento entre usuários: Bob faz login e NÃO vê notas nem Atlas privados de Alice
        with TestClient(main_module.app) as bob_client:
            r_bob_login = bob_client.post(
                "/api/auth/login",
                json={"username": "bob_reader", "password": "BobPassword!2026"},
                headers=_auth_headers(bob_client),
            )
            assert r_bob_login.status_code == 200, r_bob_login.text
            assert bob_client.get(f"/api/books/{pdf_book['id']}/annotations").json().get("notes", []) == []
            bob_concepts = bob_client.get("/api/knowledge/concepts").json()
            assert (bob_concepts.get("items") or bob_concepts.get("concepts") or []) == []
            bob_refs = bob_client.get("/api/knowledge/reflections").json()
            assert (bob_refs.get("items") or bob_refs.get("reflections") or []) == []


# ==============================================================================
# 2. CENÁRIOS DE FALHA E SEGURANÇA DA RESTAURAÇÃO
# ==============================================================================

def _make_valid_db(path: Path) -> None:
    database.init_db(path)


def test_v192_restore_rejects_corrupted_incomplete_and_future_schema(tmp_path):
    target_dir = tmp_path / "target"

    # (a) Backup SQLite corrompido
    corrupt_snap = tmp_path / "snap_corrupt"
    corrupt_snap.mkdir()
    (corrupt_snap / "lectoria.db").write_bytes(b"NOT_A_SQLITE_DATABASE_AT_ALL")
    with pytest.raises(backup_service.BackupError):
        backup_service.restore_snapshot_to_directory(corrupt_snap, target_dir)
    assert not (target_dir / "lectoria.db").exists()

    # (b) Snapshot incompleto (sem lectoria.db)
    incomplete_snap = tmp_path / "snap_incomplete"
    incomplete_snap.mkdir()
    with pytest.raises(backup_service.BackupError, match="incompleto"):
        backup_service.restore_snapshot_to_directory(incomplete_snap, target_dir)

    # (c) Banco SQLite vazio sem tabelas obrigatórias
    empty_tables_snap = tmp_path / "snap_empty_tables"
    empty_tables_snap.mkdir()
    c = sqlite3.connect(str(empty_tables_snap / "lectoria.db"))
    c.execute("CREATE TABLE dummy (id INTEGER);")
    c.commit()
    c.close()
    with pytest.raises(backup_service.BackupError, match="tabelas obrigatórias"):
        backup_service.restore_snapshot_to_directory(empty_tables_snap, target_dir)

    # (d) Schema incompatível (versão futura > SCHEMA_VERSION — sem downgrade automático)
    future_snap = tmp_path / "snap_future"
    future_snap.mkdir()
    _make_valid_db(future_snap / "lectoria.db")
    c = sqlite3.connect(str(future_snap / "lectoria.db"))
    c.execute("INSERT INTO schema_migrations (version) VALUES (?);", (database.SCHEMA_VERSION + 5,))
    c.commit()
    c.close()
    with pytest.raises(backup_service.BackupError, match="Downgrade automático"):
        backup_service.restore_snapshot_to_directory(future_snap, target_dir)
    assert not (target_dir / "lectoria.db").exists()


def test_v192_restore_rejects_invalid_and_unsafe_covers_tar(tmp_path):
    target_dir = tmp_path / "target"
    base_snap = tmp_path / "snap_base"
    base_snap.mkdir()
    _make_valid_db(base_snap / "lectoria.db")

    # (a) covers.tar.gz corrompido
    (base_snap / "covers.tar.gz").write_bytes(b"garbage-not-a-tarball")
    with pytest.raises(backup_service.BackupError, match="corrompido"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)
    assert not (target_dir / "lectoria.db").exists()

    # (b) Path traversal (../escape.png)
    with tarfile.open(base_snap / "covers.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo(name="../escape.png")
        data = b"evil"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(backup_service.BackupError, match="traversal"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)
    assert not (tmp_path / "escape.png").exists()
    assert not (target_dir / "lectoria.db").exists()

    # (c) Caminho absoluto (/tmp/evil.png)
    with tarfile.open(base_snap / "covers.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo(name="/tmp/evil.png")
        data = b"evil"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(backup_service.BackupError, match="absoluto"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)

    # (d) Symlink malicioso
    with tarfile.open(base_snap / "covers.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo(name="covers/link.png")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(backup_service.BackupError, match="inseguro"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)

    # (e) Hardlink malicioso
    with tarfile.open(base_snap / "covers.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo(name="covers/hardlink.png")
        info.type = tarfile.LNKTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(backup_service.BackupError, match="inseguro"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)

    # (f) Entradas duplicadas / colisão de caminhos
    with tarfile.open(base_snap / "covers.tar.gz", "w:gz") as tf:
        for raw_name in ("covers/dup.png", "./dup.png"):
            info = tarfile.TarInfo(name=raw_name)
            data = b"same"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    with pytest.raises(backup_service.BackupError, match="duplicada"):
        backup_service.restore_snapshot_to_directory(base_snap, target_dir)


def test_v192_extract_covers_blocks_preexisting_symlinks_and_size_limits(tmp_path, monkeypatch):
    # (a) Destino preexistente contendo symlink apontando para fora
    outside_target = tmp_path / "outside_secret.txt"
    outside_target.write_text("KEEP_ME", encoding="utf-8")
    dest_covers = tmp_path / "dest_covers"
    dest_covers.mkdir()
    (dest_covers / "cover1.png").symlink_to(outside_target)

    archive = tmp_path / "valid_covers.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo(name="covers/cover1.png")
        payload = b"OVERWRITE_ATTEMPT"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    with pytest.raises(backup_service.BackupError, match="symlink inseguro"):
        backup_service.extract_covers_archive_safely(archive, dest_covers)
    assert outside_target.read_text(encoding="utf-8") == "KEEP_ME"

    # (b) Limite proporcional de consumo de espaço (rejeita arquivo acima do limite)
    clean_dest = tmp_path / "clean_dest_covers"
    monkeypatch.setattr(backup_service, "MAX_TOTAL_COVERS_BYTES", 8)
    with pytest.raises(backup_service.BackupError, match="limite total"):
        backup_service.extract_covers_archive_safely(archive, clean_dest)
    assert not clean_dest.exists()


def test_v192_restore_rolls_back_db_and_covers_on_covers_swap_failure(tmp_path, monkeypatch):
    """
    Confirma que falha intermediária após substituição do banco (durante a troca de capas)
    reverte tanto o SQLite quanto o diretório de capas anterior.
    """
    target_dir = tmp_path / "existing_full"
    target_dir.mkdir()
    existing_db = target_dir / "lectoria.db"
    _make_valid_db(existing_db)
    c = sqlite3.connect(str(existing_db))
    c.execute("UPDATE users SET display_name = 'BEFORE_SWAP' WHERE id = 1;")
    c.commit()
    c.close()
    existing_covers = target_dir / "covers"
    existing_covers.mkdir()
    (existing_covers / "old_cover.png").write_bytes(b"OLD_COVER_BYTES")

    snap_dir = tmp_path / "snap_with_covers"
    snap_dir.mkdir()
    _make_valid_db(snap_dir / "lectoria.db")
    c = sqlite3.connect(str(snap_dir / "lectoria.db"))
    c.execute("UPDATE users SET display_name = 'NEW_SNAPSHOT' WHERE id = 1;")
    c.commit()
    c.close()
    with tarfile.open(snap_dir / "covers.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo(name="covers/new_cover.png")
        data = b"NEW_COVER_BYTES"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    orig_rename = Path.rename

    def _fail_on_stage_rename(self, target):
        if self.name == "covers" and ".lectoria_restore_stage_" in str(self.parent):
            raise OSError("falha simulada na troca de capas")
        return orig_rename(self, target)

    monkeypatch.setattr(Path, "rename", _fail_on_stage_rename)
    with pytest.raises(backup_service.BackupError, match="Falha durante a restauração"):
        backup_service.restore_snapshot_to_directory(snap_dir, target_dir, allow_overwrite=True)

    # Tanto o banco quanto a capa anterior foram preservados integralmente
    c = sqlite3.connect(str(existing_db))
    val = c.execute("SELECT display_name FROM users WHERE id = 1;").fetchone()[0]
    c.close()
    assert val == "BEFORE_SWAP"
    assert (existing_covers / "old_cover.png").read_bytes() == b"OLD_COVER_BYTES"
    assert not (existing_covers / "new_cover.png").exists()


def test_v192_restore_protects_existing_destination_and_recovers_on_mid_failure(tmp_path, monkeypatch):
    # Prepara destino já existente com registro distinto
    target_dir = tmp_path / "existing_target"
    target_dir.mkdir()
    existing_db = target_dir / "lectoria.db"
    _make_valid_db(existing_db)
    c = sqlite3.connect(str(existing_db))
    c.execute("UPDATE users SET display_name = 'ORIGINAL_DATA' WHERE id = 1;")
    c.commit()
    c.close()
    original_hash = _sha256(existing_db)

    # Prepara snapshot válido diferente
    snap_dir = tmp_path / "valid_snap"
    snap_dir.mkdir()
    _make_valid_db(snap_dir / "lectoria.db")
    c = sqlite3.connect(str(snap_dir / "lectoria.db"))
    c.execute("UPDATE users SET display_name = 'SNAPSHOT_DATA' WHERE id = 1;")
    c.commit()
    c.close()

    # (a) Sem confirmação explícita (allow_overwrite=False), recusa sobrescrever
    with pytest.raises(backup_service.BackupError, match="já existe"):
        backup_service.restore_snapshot_to_directory(snap_dir, target_dir, allow_overwrite=False)
    assert _sha256(existing_db) == original_hash

    # (b) Falha no meio da restauração (init_db levanta erro) → reverte para cópia de segurança original
    def _boom(_db_path):
        raise RuntimeError("falha simulada no meio da restauracao")

    monkeypatch.setattr(database, "init_db", _boom)
    with pytest.raises(backup_service.BackupError, match="Falha durante a restauração"):
        backup_service.restore_snapshot_to_directory(snap_dir, target_dir, allow_overwrite=True)

    c = sqlite3.connect(str(existing_db))
    val = c.execute("SELECT display_name FROM users WHERE id = 1;").fetchone()[0]
    c.close()
    assert val == "ORIGINAL_DATA"
    assert (target_dir / "lectoria_pre_restore.db.bak").exists()


def test_v192_restored_app_handles_missing_book_files_gracefully(tmp_path, monkeypatch):
    """
    Se o backup do banco foi restaurado mas os arquivos originais dos livros (PDF/EPUB)
    estão ausentes no destino, a aplicação deve iniciar normalmente, exibir o catálogo/notas
    e retornar 404 limpo ao tentar abrir o arquivo ausente.
    """
    origin_data = tmp_path / "origin_data"
    origin_books = tmp_path / "origin_books"
    origin_backups = tmp_path / "origin_backups"
    origin_books.mkdir(parents=True)
    pdf_file = origin_books / "livro_ausente_depois.pdf"
    _create_minimal_pdf(pdf_file)

    with _isolated_lectoria_env(monkeypatch, origin_data, origin_books, origin_backups) as (origin_db, origin_covers):
        database.init_db(origin_db)
        with TestClient(main_module.app) as client:
            client.post(
                "/api/auth/setup",
                json={"username": "admin_miss", "password": "AdminPassword!2026", "display_name": "Admin"},
                headers=_auth_headers(client),
            )
            client.post("/api/library/scan", headers=_auth_headers(client))
            book_id = client.get("/api/books").json()["books"][0]["id"]

        snap_dir = tmp_path / "snap_missing_books"
        _create_snapshot_backup(origin_db, origin_covers, snap_dir)

    # Restaura apontando para um diretório de livros VAZIO (e remove o arquivo original)
    pdf_file.unlink()
    restored_data = tmp_path / "restored_data"
    empty_books = tmp_path / "empty_books"
    backup_service.restore_snapshot_to_directory(snap_dir, restored_data)

    with _isolated_lectoria_env(monkeypatch, restored_data, empty_books, tmp_path / "restored_bkp"):
        with TestClient(main_module.app) as client:
            assert client.get("/api/health").status_code == 200
            r_login = client.post(
                "/api/auth/login",
                json={"username": "admin_miss", "password": "AdminPassword!2026"},
                headers=_auth_headers(client),
            )
            assert r_login.status_code == 200
            # O registro do livro permanece preservado no SQLite (marcado is_missing=1 após varredura)
            # e a tentativa de abrir o arquivo ausente retorna 404 limpo sem erro 500
            with database.get_db(restored_data / "lectoria.db") as conn:
                row = conn.execute("SELECT id, is_missing FROM books WHERE id = ?", (book_id,)).fetchone()
                assert row is not None
                assert row["is_missing"] == 1
            r_file = client.get(f"/api/books/{book_id}/file")
            assert r_file.status_code == 404


# ==============================================================================
# 3. INVARIANTES DE HARDENING RESIDUAL DOCKER / COMPOSE E PORTABILIDADE
# ==============================================================================

def test_v192_dockerfile_and_compose_hardening_invariants():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'ARG LECTORIA_UID="1000"' in dockerfile
    assert 'ARG LECTORIA_GID="1000"' in dockerfile
    assert "useradd" in dockerfile and "lectoria" in dockerfile
    assert "USER lectoria:lectoria" in dockerfile
    assert "chown lectoria:lectoria /app/data /app/data/covers /app/backups" in dockerfile
    assert "chmod 777" not in dockerfile
    assert "chown -R" not in dockerfile

    for compose_name in ("docker-compose.yml", "docker-compose.example.yml"):
        txt = (REPO_ROOT / compose_name).read_text(encoding="utf-8")
        assert "ghcr.io/nosremekk/lectoria:stable" in txt
        assert "no-new-privileges:true" in txt
        assert "cap_drop:" in txt and "- ALL" in txt
        assert "read_only: true" in txt
        assert "/tmp:rw,noexec,nosuid" in txt
        assert "/app/books:ro" in txt
        assert "/api/health" in txt
        assert "LECTORIA_SECURE_COOKIES=auto" in txt
        assert "LECTORIA_FORWARDED_ALLOW_IPS=127.0.0.1,::1" in txt
        assert "LECTORIA_CSP_MODE=report-only" in txt
        assert "LECTORIA_HSTS_ENABLED=false" in txt

    # Portabilidade: nenhuma dependência da infraestrutura particular do proprietário
    for rel in ("Dockerfile", "docker-compose.yml", "docker-compose.example.yml", ".env.example", "backup_service.py"):
        txt = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for forbidden in ("srv2137600", "tailf5c3ce", "/srv/nosreme"):
            assert forbidden not in txt, f"Referência específica de host encontrada em {rel}: {forbidden}"
