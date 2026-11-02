"""
Testes automatizados para a Iteração 8 do Lectoria:
- Sistema de capas de livros (extração sob demanda de PDFs, cache em disco, invalidação por hash,
  capas personalizadas com validação binária de magic bytes, limites de tamanho, remoção,
  fallback editorial determinístico em SVG, PDFs corrompidos/protegidos por senha e fontes inativas).
- Seleção visual de diretórios (navegador visual seguro pelo backend e seletor nativo local,
  controle de acesso admin/reader/anônimo, bloqueio de acesso remoto ao seletor nativo,
  tratamento de cancelamento/timeout/erro, bloqueio de diretórios proibidos, symlinks e path traversal,
  e integração ponta a ponta com POST /api/sources).
"""

import os
import struct
import subprocess
import time
import zlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pypdf
import pytest
from starlette.testclient import TestClient

import auth_service
import config
import covers_service
import directory_picker_service
import library
import sources_service
from main import app
from tests.conftest import create_sample_pdf


# =========================================================================
# UTILITÁRIOS DE TESTE PARA IMAGENS E PDFS COM CAPA EMBUTIDA
# =========================================================================

def make_valid_png_bytes(width: int = 120, height: int = 170, rgb=(158, 59, 36)) -> bytes:
    """Gera uma imagem PNG RGB válida em memória."""
    raw_rows = bytearray()
    row_pixels = bytes(rgb) * width
    for _ in range(height):
        raw_rows.append(0)
        raw_rows.extend(row_pixels)

    def chunk(c_type: bytes, c_data: bytes) -> bytes:
        crc = zlib.crc32(c_type + c_data) & 0xFFFFFFFF
        return struct.pack(">I", len(c_data)) + c_type + c_data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_valid_jpeg_bytes(width: int = 120, height: int = 170) -> bytes:
    """Gera um cabeçalho/fluxo JPEG válido com marcador SOF0 e EOI."""
    sof0 = struct.pack(">BHHB", 8, height, width, 3)
    sof0_seg = b"\xff\xc0" + struct.pack(">H", len(sof0) + 2) + sof0
    body = b"\x00" * 128
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof0_seg + body + b"\xff\xd9"


def create_pdf_with_embedded_cover(
    file_path: Path,
    title: str = "Livro com Capa Real",
    author: str = "Editora Clássica",
    img_width: int = 200,
    img_height: int = 280,
) -> Path:
    """
    Cria um arquivo PDF contendo um XObject de imagem (/DCTDecode JPEG) na primeira página,
    simulando um livro digital com capa real incorporada.
    """
    writer = pypdf.PdfWriter()
    writer.add_metadata({"/Title": title, "/Author": author})
    page = writer.add_blank_page(width=400, height=560)

    jpeg_data = make_valid_jpeg_bytes(width=img_width, height=img_height)

    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    img_stream = DecodedStreamObject()
    img_stream._data = jpeg_data
    img_stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(img_width),
            NameObject("/Height"): NumberObject(img_height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/DCTDecode"),
        }
    )
    img_ref = writer._add_object(img_stream)

    resources = DictionaryObject()
    xobj_dict = DictionaryObject({NameObject("/ImCover"): img_ref})
    resources[NameObject("/XObject")] = xobj_dict
    page[NameObject("/Resources")] = resources

    with open(file_path, "wb") as f:
        writer.write(f)
    return file_path


# =========================================================================
# 6.1. TESTES DO SISTEMA DE CAPAS
# =========================================================================

def test_cover_extraction_from_pdf_and_cache_reuse(client: TestClient, test_env):
    """
    Testa:
    - PDF com primeira página renderizável (imagem de capa incorporada)
    - Uso de cache existente em disco nas chamadas subsequentes
    """
    pdf_path = test_env["library_dir"] / "livro_com_capa.pdf"
    create_pdf_with_embedded_cover(pdf_path, "Obras Completas", "Machado de Assis")
    try:
        library.scan_library()

        books = library.get_books(search="Obras Completas")
        assert len(books) == 1
        book_id = books[0]["id"]

        # 1ª chamada: extrai a capa do PDF e grava no cache data/covers/
        res1 = client.get(f"/api/books/{book_id}/cover")
        assert res1.status_code == 200
        assert res1.headers["x-cover-type"] == "extracted"
        assert res1.headers["content-type"] == "image/jpeg"
        assert res1.content.startswith(b"\xff\xd8\xff")

        cached_file = config.COVERS_DIR / f"{book_id}_extracted.jpg"
        assert cached_file.is_file()
        mtime_first = cached_file.stat().st_mtime_ns

        # 2ª chamada: deve servir diretamente do cache existente sem recriar o arquivo
        res2 = client.get(f"/api/books/{book_id}/cover")
        assert res2.status_code == 200
        assert res2.headers["x-cover-type"] == "extracted"
        assert cached_file.stat().st_mtime_ns == mtime_first
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        with database.get_db() as conn:
            conn.execute("DELETE FROM books WHERE filename = 'livro_com_capa.pdf'")


def test_cover_fallback_for_pdf_without_embedded_image(client: TestClient):
    """
    Testa:
    - PDF sem capa identificável (páginas em branco sem XObject de imagem)
    - Geração determinística da capa substituta editorial em SVG
    """
    books = library.get_books(search="Dom Casmurro")
    assert len(books) == 1
    book = books[0]
    book_id = book["id"]

    res1 = client.get(f"/api/books/{book_id}/cover")
    assert res1.status_code == 200
    assert res1.headers["x-cover-type"] == "fallback"
    assert "image/svg+xml" in res1.headers["content-type"]
    svg_text = res1.text
    assert "<svg" in svg_text
    assert "Dom Casmurro" in svg_text
    assert "Machado de Assis" in svg_text

    # Determinismo: uma segunda geração deve produzir exatamente o mesmo SVG
    res2 = client.get(f"/api/books/{book_id}/cover")
    assert res2.text == svg_text


def test_cover_corrupted_and_password_protected_pdfs(client: TestClient, test_env):
    """
    Testa:
    - PDF corrompido (não derruba o servidor; retorna capa editorial substituta)
    - PDF protegido por senha (não trava nem falha; retorna capa editorial substituta)
    """
    # 1. PDF corrompido
    corrupted_path = test_env["library_dir"] / "corrompido.pdf"
    corrupted_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\n%%EOF-CORRUPTED-STREAM")

    # 2. PDF criptografado/protegido por senha
    encrypted_path = test_env["library_dir"] / "protegido.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=400, height=560)
    writer.encrypt("senha_secreta_123")
    with open(encrypted_path, "wb") as f:
        writer.write(f)

    try:
        library.scan_library()

        for search_term in ("corrompido", "protegido"):
            found = library.get_books(search=search_term)
            assert len(found) == 1
            b_id = found[0]["id"]

            res = client.get(f"/api/books/{b_id}/cover")
            assert res.status_code == 200
            assert res.headers["x-cover-type"] == "fallback"
            assert "image/svg+xml" in res.headers["content-type"]
            assert "<svg" in res.text
    finally:
        for p in (corrupted_path, encrypted_path):
            if p.exists():
                p.unlink()
        with database.get_db() as conn:
            conn.execute("DELETE FROM books WHERE filename IN ('corrompido.pdf', 'protegido.pdf')")


def test_custom_cover_upload_replace_and_delete(client: TestClient):
    """
    Testa:
    - Upload de capa personalizada válida (PNG e JPEG)
    - Substituição de capa personalizada existente
    - Remoção de capa personalizada e restauração da capa automática
    """
    books = library.get_books()
    book_id = books[0]["id"]

    # 1. Upload de capa PNG válida
    png_bytes = make_valid_png_bytes(160, 220, rgb=(40, 80, 120))
    res_up1 = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("capa.png", png_bytes, "image/png")},
    )
    assert res_up1.status_code == 200
    data1 = res_up1.json()
    assert data1["cover_type"] == "custom"
    assert data1["width"] == 160
    assert data1["height"] == 220

    custom_png_path = config.COVERS_DIR / f"{book_id}_custom.png"
    assert custom_png_path.is_file()

    # Verifica entrega da capa personalizada
    res_get1 = client.get(f"/api/books/{book_id}/cover")
    assert res_get1.status_code == 200
    assert res_get1.headers["x-cover-type"] == "custom"
    assert res_get1.headers["content-type"] == "image/png"
    assert res_get1.content == png_bytes

    # 2. Substituição da capa personalizada por um JPEG válido
    jpg_bytes = make_valid_jpeg_bytes(200, 280)
    res_up2 = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("nova_capa.jpg", jpg_bytes, "image/jpeg")},
    )
    assert res_up2.status_code == 200
    assert res_up2.json()["cover_type"] == "custom"

    # O arquivo PNG anterior deve ter sido removido e o novo JPG criado
    assert not custom_png_path.exists()
    custom_jpg_path = config.COVERS_DIR / f"{book_id}_custom.jpg"
    assert custom_jpg_path.is_file()

    res_get2 = client.get(f"/api/books/{book_id}/cover")
    assert res_get2.status_code == 200
    assert res_get2.headers["x-cover-type"] == "custom"
    assert res_get2.headers["content-type"] == "image/jpeg"

    # 3. Remoção da capa personalizada
    res_del = client.delete(f"/api/books/{book_id}/cover")
    assert res_del.status_code == 200
    assert not custom_jpg_path.exists()

    # Deve voltar para capa automática (fallback ou extracted)
    res_get3 = client.get(f"/api/books/{book_id}/cover")
    assert res_get3.status_code == 200
    assert res_get3.headers["x-cover-type"] in ("fallback", "extracted")


def test_custom_cover_invalid_and_oversized_uploads(client: TestClient):
    """
    Testa:
    - Upload de arquivo inválido (HTML/script, SVG, texto puro, imagem minúscula) -> 400
    - Upload de imagem acima do limite de 5 MB -> 413
    """
    books = library.get_books()
    book_id = books[0]["id"]

    # 1. Tentativa de enviar script HTML disfarçado de imagem
    malicious_html = b"<!DOCTYPE html><html><script>alert('xss')</script></html>"
    res_html = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("exploit.png", malicious_html, "image/png")},
    )
    assert res_html.status_code == 400

    # 2. Tentativa de enviar SVG com script
    malicious_svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    res_svg = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("capa.svg", malicious_svg, "image/svg+xml")},
    )
    assert res_svg.status_code == 400

    # 3. Tentativa de enviar PNG com conteúdo <script> embutido nos bytes iniciais
    fake_png_script = b"\x89PNG\r\n\x1a\n" + b"<script>evil()</script>" + b"\x00" * 100
    res_fake = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("fake.png", fake_png_script, "image/png")},
    )
    assert res_fake.status_code == 400

    # 4. Imagem com dimensões abaixo do mínimo (ex: 4x4 pixels)
    tiny_png = make_valid_png_bytes(4, 4)
    res_tiny = client.post(
        f"/api/books/{book_id}/cover",
        files={"file": ("tiny.png", tiny_png, "image/png")},
    )
    assert res_tiny.status_code == 400

    # 5. Arquivo acima do limite máximo de 5 MB -> deve retornar 413
    oversized_payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (covers_service.MAX_COVER_SIZE_BYTES + 2048))
    res_large = client.post(
        f"/api/books/{book_id}/cover",
        content=oversized_payload,
        headers={"Content-Type": "image/png"},
    )
    assert res_large.status_code == 413


def test_cover_invalidation_when_pdf_modified(client: TestClient, test_env):
    """
    Testa:
    - PDF modificado após geração da miniatura: invalida o cache extraído ao reescanear
      e regenera a capa a partir do novo conteúdo do PDF.
    """
    pdf_path = test_env["library_dir"] / "mutavel.pdf"
    create_sample_pdf(pdf_path, "Livro Mutável", "Autor Inicial", pages=2)
    try:
        library.scan_library()

        book = library.get_books(search="Livro Mutável")[0]
        book_id = book["id"]

        # Inicialmente não tem imagem embutida -> fallback
        res1 = client.get(f"/api/books/{book_id}/cover")
        assert res1.status_code == 200
        assert res1.headers["x-cover-type"] == "fallback"

        # Substitui o PDF no disco por uma versão que possui capa JPEG embutida e mtime diferente
        create_pdf_with_embedded_cover(pdf_path, "Livro Mutável", "Autor Inicial", img_width=180, img_height=260)
        future_mtime = time.time() + 120
        os.utime(pdf_path, (future_mtime, future_mtime))

        # Reescaneia a biblioteca: deve invalidar o estado anterior de capa
        library.scan_library()

        # Agora a requisição de capa deve extrair a nova capa real embutida
        res2 = client.get(f"/api/books/{book_id}/cover")
        assert res2.status_code == 200
        assert res2.headers["x-cover-type"] == "extracted"
        assert res2.headers["content-type"] == "image/jpeg"
    finally:
        if pdf_path.exists():
            pdf_path.unlink()
        with database.get_db() as conn:
            conn.execute("DELETE FROM books WHERE filename = 'mutavel.pdf'")


def test_cover_deactivated_or_removed_source_and_inaccessible_book(client: TestClient, tmp_path: Path):
    """
    Testa:
    - Livro inexistente -> 404
    - Fonte desativada -> bloqueia acesso à capa (403)
    - Fonte removida -> remove registros e limpa arquivos de capa em cache
    - Livro cujo PDF foi apagado do disco -> 404
    """
    # 1. Livro inexistente
    assert client.get("/api/books/id_inexistente_999/cover").status_code == 404

    # 2. Cria uma nova fonte com um livro com capa real
    src_dir = tmp_path / "fonte_capas"
    src_dir.mkdir(parents=True, exist_ok=True)
    pdf_file = src_dir / "obra_rara.pdf"
    create_pdf_with_embedded_cover(pdf_file, "Obra Rara", "Autor Clássico")

    src = sources_service.add_library_source("Fonte Capas", str(src_dir))
    library.scan_library(source_id=src["id"])

    books = library.get_books(source_id=src["id"])
    assert len(books) == 1
    book_id = books[0]["id"]

    # Gera o cache da capa
    res_cov = client.get(f"/api/books/{book_id}/cover")
    assert res_cov.status_code == 200
    cached_cover = config.COVERS_DIR / f"{book_id}_extracted.jpg"
    assert cached_cover.is_file()

    # 3. Desativa a fonte -> acesso à capa deve ser negado (403)
    sources_service.set_source_active(src["id"], False)
    res_deact = client.get(f"/api/books/{book_id}/cover")
    assert res_deact.status_code == 403

    # Reativa a fonte
    sources_service.set_source_active(src["id"], True)
    assert client.get(f"/api/books/{book_id}/cover").status_code == 200

    # 4. Remove o PDF fisicamente do disco -> livro torna-se inacessível (404)
    pdf_file.unlink()
    res_missing = client.get(f"/api/books/{book_id}/cover")
    assert res_missing.status_code == 404

    # 5. Remove a fonte -> o arquivo de cache da capa deve ser limpo do diretório COVERS_DIR
    sources_service.delete_library_source(src["id"])
    assert not cached_cover.exists()


# =========================================================================
# 6.2. TESTES DE SELEÇÃO DE DIRETÓRIOS (NAVEGADOR WEB E SELETOR NATIVO)
# =========================================================================

def test_directory_browse_navigation_and_source_integration(client: TestClient, tmp_path: Path):
    """
    Testa:
    - Navegação entre diretório pai e subdiretórios via GET /api/sources/browse
    - Contagem de arquivos PDF por diretório
    - Integração completa do caminho selecionado com POST /api/sources
    """
    parent_dir = tmp_path / "acervo_familia"
    child_dir = parent_dir / "literatura_brasileira"
    child_dir.mkdir(parents=True, exist_ok=True)
    create_sample_pdf(child_dir / "livro1.pdf", "Livro 1", "Autor 1", pages=2)
    create_sample_pdf(child_dir / "livro2.pdf", "Livro 2", "Autor 2", pages=3)

    # 1. Navega no diretório pai
    res_parent = client.get("/api/sources/browse", params={"path": str(parent_dir)})
    assert res_parent.status_code == 200
    data_parent = res_parent.json()
    assert data_parent["current_path"] == str(parent_dir.resolve())
    assert data_parent["can_select_current"] is True

    subdirs = {d["name"]: d for d in data_parent["directories"]}
    assert "literatura_brasileira" in subdirs
    sub_info = subdirs["literatura_brasileira"]
    assert sub_info["readable"] is True
    assert sub_info["selectable"] is True
    assert sub_info["pdf_count"] == 2

    # 2. Entra no subdiretório filho
    res_child = client.get("/api/sources/browse", params={"path": sub_info["path"]})
    assert res_child.status_code == 200
    data_child = res_child.json()
    assert data_child["current_path"] == str(child_dir.resolve())
    assert data_child["parent_path"] == str(parent_dir.resolve())
    assert data_child["current_pdf_count"] == 2

    # 3. Cadastra o diretório selecionado em POST /api/sources
    res_create = client.post(
        "/api/sources",
        json={"label": "Literatura Brasileira", "path": data_child["current_path"]},
    )
    assert res_create.status_code == 201
    created_source = res_create.json()["source"]
    try:
        assert created_source["path"] == str(child_dir.resolve())
        assert created_source["book_count"] == 2
    finally:
        sources_service.delete_library_source(created_source["id"])


def test_directory_browse_security_blocks(client: TestClient, tmp_path: Path):
    """
    Testa bloqueios de segurança no navegador de diretórios:
    - Diretório inexistente
    - Diretório sem permissão de leitura
    - Diretório proibido (/etc, /root, /proc, data/, backups/, covers/, .venv)
    - Link simbólico externo
    - Tentativa de path traversal ('../../etc')
    """
    # 1. Diretório inexistente
    res_nonexistent = client.get("/api/sources/browse", params={"path": str(tmp_path / "nao_existe_123")})
    assert res_nonexistent.status_code == 400

    # 2. Tentativa de Path Traversal
    for traversal in ("../../etc", f"{tmp_path}/../../etc/passwd", "/home/../etc"):
        res_trav = client.get("/api/sources/browse", params={"path": traversal})
        assert res_trav.status_code == 400

    # 3. Diretórios proibidos do sistema e internos da aplicação
    forbidden_targets = [
        "/etc",
        "/root",
        "/proc",
        str(config.DATA_DIR),
        str(config.BACKUPS_DIR),
        str(config.COVERS_DIR),
    ]
    for f_path in forbidden_targets:
        res_forb = client.get("/api/sources/browse", params={"path": f_path})
        assert res_forb.status_code == 400

    # 4. Link simbólico externo
    outside_target = tmp_path / "outside_secret"
    outside_target.mkdir(parents=True, exist_ok=True)
    browse_root = tmp_path / "browse_root"
    browse_root.mkdir(parents=True, exist_ok=True)
    symlink_dir = browse_root / "atalho_externo"
    symlink_dir.symlink_to(outside_target)

    # Ao listar browse_root, o symlink deve aparecer marcado como bloqueado (selectable=False, is_symlink=True)
    res_list = client.get("/api/sources/browse", params={"path": str(browse_root)})
    assert res_list.status_code == 200
    entries = {d["name"]: d for d in res_list.json()["directories"]}
    assert "atalho_externo" in entries
    assert entries["atalho_externo"]["is_symlink"] is True
    assert entries["atalho_externo"]["selectable"] is False
    assert entries["atalho_externo"]["readable"] is False

    # Tentar navegar diretamente para o link simbólico ou cadastrá-lo como fonte deve falhar com 400
    assert client.get("/api/sources/browse", params={"path": str(symlink_dir)}).status_code == 400
    assert client.post("/api/sources", json={"label": "Symlink", "path": str(symlink_dir)}).status_code == 400

    # 5. Diretório sem permissão de leitura
    unreadable_dir = browse_root / "sem_permissao"
    unreadable_dir.mkdir(parents=True, exist_ok=True)
    unreadable_dir.chmod(0o000)
    try:
        if not os.access(unreadable_dir, os.R_OK):
            res_unreadable = client.get("/api/sources/browse", params={"path": str(unreadable_dir)})
            assert res_unreadable.status_code == 400
    finally:
        unreadable_dir.chmod(0o755)


def test_directory_endpoints_authentication_and_rbac():
    """
    Testa controle de acesso nos endpoints de seleção de diretórios:
    - Usuário não autenticado -> 401
    - Usuário com perfil 'reader' -> 403
    - Usuário com perfil 'admin' -> 200
    """
    admin_user = auth_service.create_user("admin_dir_test", "AdminPass123!", role="admin")
    reader_user = auth_service.create_user("reader_dir_test", "ReaderPass123!", role="reader")

    try:
        admin_token = auth_service.create_session(admin_user["id"])
        reader_token = auth_service.create_session(reader_user["id"])

        # 1. Não autenticado -> 401
        with TestClient(app) as anon_client:
            assert anon_client.get("/api/sources/browse").status_code == 401
            assert anon_client.post("/api/sources/pick-native").status_code == 401

        # 2. Leitor comum (reader) -> 403
        with TestClient(app, cookies={auth_service.SESSION_COOKIE_NAME: reader_token}) as reader_client:
            assert reader_client.get("/api/sources/browse").status_code == 403
            assert reader_client.post("/api/sources/pick-native").status_code == 403

        # 3. Administrador (admin) -> 200 no browse
        with TestClient(app, cookies={auth_service.SESSION_COOKIE_NAME: admin_token}) as admin_client:
            res_admin = admin_client.get("/api/sources/browse")
            assert res_admin.status_code == 200
    finally:
        with database_cleanup():
            pass


from contextlib import contextmanager
import database


@contextmanager
def database_cleanup():
    try:
        yield
    finally:
        with database.get_db() as conn:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM users WHERE username IN ('admin_dir_test', 'reader_dir_test')")


def test_native_picker_local_vs_remote_and_lifecycle(client: TestClient, tmp_path: Path):
    """
    Testa o seletor nativo do sistema (POST /api/sources/pick-native):
    - Acesso remoto tentando abrir janela no servidor -> bloqueado com 403
    - Seletor nativo indisponível (sem DISPLAY ou sem utilitário) -> 503
    - Seleção de diretório válido via seletor nativo -> 200 com caminho canônico
    - Cancelamento da seleção pelo usuário -> 200 com status='cancelled'
    - Erro ou timeout ao abrir diálogo do sistema -> 503
    """
    valid_folder = tmp_path / "pasta_escolhida_zenity"
    valid_folder.mkdir(parents=True, exist_ok=True)

    # 1. Acesso remoto tentando abrir janela gráfica no servidor deve ser bloqueado com 403
    with patch.object(subprocess, "run") as mock_run:
        res_remote = client.post(
            "/api/sources/pick-native",
            headers={"X-Forwarded-For": "192.168.1.55"},
        )
        assert res_remote.status_code == 403
        mock_run.assert_not_called()

    # 2. Seletor nativo indisponível (sem DISPLAY/WAYLAND_DISPLAY) -> 503
    with patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False):
        res_no_gui = client.post("/api/sources/pick-native")
        assert res_no_gui.status_code == 503

    # 3. Seleção de diretório válido (simulando Zenity retornando código 0 e caminho válido)
    mock_Completed_ok = MagicMock(returncode=0, stdout=f"{valid_folder}\n", stderr="")
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
         patch("shutil.which", return_value="/usr/bin/zenity"), \
         patch("subprocess.run", return_value=mock_Completed_ok) as mock_sub:
        res_ok = client.post("/api/sources/pick-native", json={"initial_dir": str(tmp_path)})
        assert res_ok.status_code == 200
        body_ok = res_ok.json()
        assert body_ok["status"] == "selected"
        assert body_ok["path"] == str(valid_folder.resolve())
        assert body_ok["name_suggestion"] == "pasta_escolhida_zenity"
        mock_sub.assert_called_once()

    # 4. Cancelamento da janela pelo usuário (código de retorno 1 no Zenity)
    mock_cancelled = MagicMock(returncode=1, stdout="", stderr="")
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
         patch("shutil.which", return_value="/usr/bin/zenity"), \
         patch("subprocess.run", return_value=mock_cancelled):
        res_cancel = client.post("/api/sources/pick-native")
        assert res_cancel.status_code == 200
        body_cancel = res_cancel.json()
        assert body_cancel["status"] == "cancelled"
        assert body_cancel["path"] is None

    # 5. Timeout ou falha do processo gráfico -> 503 Service Unavailable
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
         patch("shutil.which", return_value="/usr/bin/zenity"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="zenity", timeout=60)):
        res_timeout = client.post("/api/sources/pick-native")
        assert res_timeout.status_code == 503

    mock_error = MagicMock(returncode=255, stdout="", stderr="Gtk-WARNING: cannot open display")
    with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
         patch("shutil.which", return_value="/usr/bin/zenity"), \
         patch("subprocess.run", return_value=mock_error):
        res_err = client.post("/api/sources/pick-native")
        assert res_err.status_code == 503
