"""
Testes automatizados para o suporte nativo a EPUB no Lectoria (Iteração 18).
Cobre validação defensiva (zip bomb, path traversal, corrupção),
extração de metadados, extração de capas, escaneamento de biblioteca mista (PDF + EPUB),
filtros por formato e progresso com CFI/localizador.
"""

import io
from pathlib import Path
import tempfile
import zipfile
import pytest
from starlette.testclient import TestClient

from database import init_db, get_db
from epub_service import (
    EpubValidationError,
    validate_epub_archive,
    extract_epub_metadata,
    extract_epub_cover_bytes,
)
from library import scan_library, get_books, get_book
from progress import save_progress, get_progress
import main


def _create_minimal_epub(
    dest_path: Path,
    title: str = "Livro de Teste EPUB",
    author: str = "Autor Teste",
    include_cover: bool = True,
    cover_bytes: bytes = None,
) -> None:
    """Gera um arquivo EPUB mínimo válido para testes unitários e de integração."""
    if cover_bytes is None and include_cover:
        # PNG 1x1 pixel mínimo válido
        cover_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff"
            b"?'\x05\xfe\x02\xfe\x1c\xef\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""

    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        <dc:language>pt-BR</dc:language>
        <meta name="cover" content="cover-image"/>
    </metadata>
    <manifest>
        <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
        <item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
        {"<item id='cover-image' href='images/cover.png' media-type='image/png' properties='cover-image'/>" if include_cover else ""}
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
        <itemref idref="chapter2"/>
    </spine>
</package>"""

    ch1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 1</title></head>
<body><h1>Capítulo 1</h1><p>Conteúdo de leitura inicial.</p></body>
</html>"""

    ch2 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Capítulo 2</title></head>
<body><h1>Capítulo 2</h1><p>Conclusão do livro.</p></body>
</html>"""

    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/chapter1.xhtml", ch1)
        zf.writestr("OEBPS/chapter2.xhtml", ch2)
        if include_cover and cover_bytes:
            zf.writestr("OEBPS/images/cover.png", cover_bytes)


class TestEpubValidationAndMetadata:
    def test_valid_epub_metadata_extraction(self, tmp_path):
        epub_file = tmp_path / "teste_valido.epub"
        _create_minimal_epub(epub_file, title="Dom Casmurro", author="Machado de Assis")

        meta = extract_epub_metadata(epub_file)
        assert meta["title"] == "Dom Casmurro"
        assert meta["author"] == "Machado de Assis"
        assert meta["page_count"] == 2

    def test_epub_cover_extraction(self, tmp_path):
        epub_file = tmp_path / "com_capa.epub"
        _create_minimal_epub(epub_file, include_cover=True)

        extracted = extract_epub_cover_bytes(epub_file)
        assert extracted is not None
        img_bytes, ext, mime = extracted
        assert ext == "png"
        assert mime == "image/png"
        assert len(img_bytes) > 20

    def test_epub_without_cover(self, tmp_path):
        epub_file = tmp_path / "sem_capa.epub"
        _create_minimal_epub(epub_file, include_cover=False)

        extracted = extract_epub_cover_bytes(epub_file)
        assert extracted is None

    def test_reject_zip_bomb_entry_limit(self, tmp_path):
        bomb_file = tmp_path / "bomb_entries.epub"
        with zipfile.ZipFile(bomb_file, "w") as zf:
            zf.writestr("META-INF/container.xml", "<container/>")
            # Cria 20.005 arquivos pequenos
            for i in range(20005):
                zf.writestr(f"file_{i}.txt", "x")

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(bomb_file)
        assert "excede o limite de segurança" in str(exc_info.value)

    def test_reject_path_traversal_in_zip(self, tmp_path):
        malicious_file = tmp_path / "traversal.epub"
        with zipfile.ZipFile(malicious_file, "w") as zf:
            zf.writestr("META-INF/container.xml", "<container/>")
            zf.writestr("../etc/passwd", "root:x:0:0...")

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(malicious_file)
        assert "Path Traversal" in str(exc_info.value)

    def test_reject_absolute_path_in_zip(self, tmp_path):
        malicious_file = tmp_path / "absolute_path.epub"
        with zipfile.ZipFile(malicious_file, "w") as zf:
            zf.writestr("META-INF/container.xml", "<container/>")
            zf.writestr("/root/secret.txt", "secret")

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(malicious_file)
        assert "Path Traversal" in str(exc_info.value)

    def test_reject_non_zip_file(self, tmp_path):
        fake_epub = tmp_path / "fake.epub"
        fake_epub.write_bytes(b"This is not a zip file at all" * 5)

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(fake_epub)
        assert "não é um pacote EPUB/ZIP válido" in str(exc_info.value)

    def test_reject_missing_container(self, tmp_path):
        no_container = tmp_path / "no_container.epub"
        with zipfile.ZipFile(no_container, "w") as zf:
            zf.writestr("some_file.txt", "hello")

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(no_container)
        assert "container.xml" in str(exc_info.value)

    def test_reject_xml_billion_laughs_and_entity_expansion(self, tmp_path):
        """Valida que tentativas de ataque Billion Laughs / XXE em container.xml ou OPF são barradas."""
        malicious_xml = b"""<?xml version="1.0"?>
        <!DOCTYPE lolz [
         <!ENTITY lol "lol">
         <!ELEMENT lolz (#PCDATA)>
         <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
        ]>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
            <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
            </rootfiles>
        </container>"""

        malicious_epub = tmp_path / "bomb_xml.epub"
        with zipfile.ZipFile(malicious_epub, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", malicious_xml)

        with pytest.raises(EpubValidationError) as exc_info:
            extract_epub_metadata(malicious_epub)
        assert "proibida por segurança" in str(exc_info.value).lower()

    def test_reject_zip_symlink_entry(self, tmp_path):
        """Valida que entradas contendo links simbólicos no contêiner ZIP são rejeitadas."""
        symlink_epub = tmp_path / "symlink.epub"
        with zipfile.ZipFile(symlink_epub, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", "<container/>")
            # Cria entrada com atributo de symlink Unix (0o120000)
            zinfo = zipfile.ZipInfo("link_to_passwd")
            zinfo.external_attr = 0o120777 << 16  # S_IFLNK | 0777
            zf.writestr(zinfo, "/etc/passwd")

        with pytest.raises(EpubValidationError) as exc_info:
            validate_epub_archive(symlink_epub)
        assert "link simbólico" in str(exc_info.value).lower()

    def test_detect_epub_drm(self, tmp_path):
        """Valida detecção de DRM comercial (Adobe ADEPT / rights.xml) versus livros abertos."""
        # 1. EPUB aberto normal
        open_epub = tmp_path / "open.epub"
        _create_minimal_epub(open_epub, title="Livro Livre", author="Autor Livre")
        meta_open = extract_epub_metadata(open_epub)
        assert meta_open["is_drm"] is False

        # 2. EPUB com DRM proprietário via META-INF/rights.xml
        drm_epub = tmp_path / "drm_rights.epub"
        _create_minimal_epub(drm_epub, title="Livro Bloqueado", author="Editora")
        with zipfile.ZipFile(drm_epub, "a") as zf:
            zf.writestr("META-INF/rights.xml", "<rights>Commercial License</rights>")
        meta_drm = extract_epub_metadata(drm_epub)
        assert meta_drm["is_drm"] is True
        assert "ADEPT" in meta_drm["drm_type"] or "Rights" in meta_drm["drm_type"]

        # 3. EPUB com encryption.xml cifrando conteúdo XHTML (DRM)
        drm_enc_epub = tmp_path / "drm_enc.epub"
        _create_minimal_epub(drm_enc_epub, title="Livro Encriptado", author="Editora")
        enc_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
            <EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">
                <EncryptionMethod Algorithm="http://ns.adobe.com/adept"/>
                <CipherData>
                    <CipherReference URI="OEBPS/chapter1.xhtml"/>
                </CipherData>
            </EncryptedData>
        </encryption>"""
        with zipfile.ZipFile(drm_enc_epub, "a") as zf:
            zf.writestr("META-INF/encryption.xml", enc_xml)
        meta_enc = extract_epub_metadata(drm_enc_epub)
        assert meta_enc["is_drm"] is True


class TestMixedLibraryAndProgress:
    @pytest.fixture
    def setup_env(self, tmp_path, monkeypatch):
        test_db = tmp_path / "test_lectoria.db"
        test_lib = tmp_path / "books"
        test_lib.mkdir()
        covers_dir = tmp_path / "covers"
        covers_dir.mkdir()

        monkeypatch.setattr("config.DB_PATH", test_db)
        monkeypatch.setattr("config.LIBRARY_DIR", test_lib)
        monkeypatch.setattr("config.COVERS_DIR", covers_dir)

        init_db(test_db)
        return {"lib": test_lib, "db": test_db}

    def test_scan_mixed_library_and_format_filter(self, setup_env):
        lib = setup_env["lib"]

        # 1. Cria um PDF simples
        pdf_file = lib / "manual.pdf"
        import pypdf
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=300)
        with open(pdf_file, "wb") as f:
            writer.write(f)

        # 2. Cria um EPUB
        epub_file = lib / "romance.epub"
        _create_minimal_epub(epub_file, title="O Cortiço", author="Aluísio Azevedo")

        stats = scan_library()
        assert stats["added"] == 2

        # 3. Lista todos os livros
        all_books = get_books()
        assert len(all_books) == 2

        formats = {b["format"] for b in all_books}
        assert "pdf" in formats
        assert "epub" in formats

        # 4. Filtra apenas PDF
        pdf_only = get_books(format_filter="pdf")
        assert len(pdf_only) == 1
        assert pdf_only[0]["format"] == "pdf"
        assert pdf_only[0]["filename"] == "manual.pdf"

        # 5. Filtra apenas EPUB
        epub_only = get_books(format_filter="epub")
        assert len(epub_only) == 1
        assert epub_only[0]["format"] == "epub"
        assert epub_only[0]["title"] == "O Cortiço"
        assert epub_only[0]["author"] == "Aluísio Azevedo"

    def test_epub_reading_progress_with_cfi(self, setup_env):
        lib = setup_env["lib"]
        epub_file = lib / "filosofia.epub"
        _create_minimal_epub(epub_file, title="Ética", author="Spinoza")
        scan_library()

        books = get_books(format_filter="epub")
        assert len(books) == 1
        book_id = books[0]["id"]

        # Salva progresso com CFI
        cfi_str = "epubcfi(/6/4[chapter1]!/4/2/10/1:0)"
        saved = save_progress(
            book_id=book_id,
            current_page=35,
            total_pages=100,
            user_id=1,
            progress_locator=cfi_str,
        )

        assert saved["percentage"] == 35.0
        assert saved["progress_locator"] == cfi_str

        # Recupera progresso
        retrieved = get_progress(book_id=book_id, user_id=1)
        assert retrieved is not None
        assert retrieved["current_page"] == 35
        assert retrieved["progress_locator"] == cfi_str

    def test_stream_and_download_epub(self, setup_env, monkeypatch):
        lib = setup_env["lib"]
        epub_file = lib / "obra.epub"
        _create_minimal_epub(epub_file, title="Memórias Póstumas", author="Machado de Assis")
        scan_library()

        books = get_books(format_filter="epub")
        book_id = books[0]["id"]

        client = TestClient(main.app)
        # Bypassa autenticação nos testes
        monkeypatch.setattr(main, "require_user", lambda r: {"id": 1, "username": "admin", "role": "admin"})

        # 1. Download do EPUB
        res_dl = client.get(f"/api/books/{book_id}/download")
        assert res_dl.status_code == 200
        assert "application/epub+zip" in res_dl.headers.get("content-type", "")
        content_disp = res_dl.headers.get("content-disposition", "")
        assert ".epub" in content_disp

        # 2. Streaming do arquivo EPUB
        res_file = client.get(f"/api/books/{book_id}/file")
        assert res_file.status_code == 200
        assert "application/epub+zip" in res_file.headers.get("content-type", "")
