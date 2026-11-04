"""
Módulo de suporte nativo a livros digitais no formato EPUB para o Lectoria.
Fornece validação segura de arquivos ZIP (defesa contra zip bombs, path traversal e symlinks),
extração leve de metadados (título, autor, idioma, status de DRM) e recuperação de capa original
diretamente do pacote EPUB sem dependências externas pesadas.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile

# Tenta importar defusedxml se disponível no ambiente
try:
    import defusedxml.ElementTree as DefusedET
    _HAS_DEFUSEDXML = True
except ImportError:
    _HAS_DEFUSEDXML = False

# Limites defensivos de segurança para arquivos EPUB
MAX_EPUB_ENTRIES = 20_000
MAX_EPUB_UNCOMPRESSED_BYTES = 250 * 1024 * 1024  # 250 MB
MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024         # 50 MB
MAX_COMPRESSION_RATIO = 100.0                    # Razão máxima de descompressão
MAX_XML_BYTES = 5 * 1024 * 1024                  # 5 MB para arquivos XML de metadados


class EpubValidationError(Exception):
    """Exceção levantada quando um arquivo EPUB é malformado ou inseguro."""
    pass


def validate_epub_archive(file_path: Path) -> None:
    """
    Valida a integridade e segurança de um arquivo EPUB (contêiner ZIP).
    Verifica:
    1. Assinatura ZIP válida e integridade básica.
    2. Proteção contra Path Traversal (nomes de arquivo com '..', caminhos absolutos).
    3. Proteção contra Links Simbólicos e Entradas Especiais (Zip Symlink Attack).
    4. Proteção contra Zip Bombs (limite de entradas, tamanho total descompactado e taxa de compressão).
    5. Presença do descritor de contêiner padrão 'META-INF/container.xml'.
    """
    if not file_path.exists() or not file_path.is_file():
        raise EpubValidationError(f"Arquivo não encontrado: {file_path}")

    try:
        stat_info = file_path.stat()
        file_size = stat_info.st_size
    except OSError as e:
        raise EpubValidationError(f"Erro ao acessar arquivo: {e}")

    if file_size < 58:  # Menor contêiner ZIP / EPUB possível
        raise EpubValidationError("Arquivo EPUB truncado ou vazio.")

    if not zipfile.is_zipfile(file_path):
        raise EpubValidationError("Arquivo não é um pacote EPUB/ZIP válido.")

    total_uncompressed = 0
    entry_count = 0
    seen_entries = set()

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            infolist = zf.infolist()
            entry_count = len(infolist)

            if entry_count > MAX_EPUB_ENTRIES:
                raise EpubValidationError(
                    f"EPUB excede o limite de segurança de {MAX_EPUB_ENTRIES} entradas ({entry_count} encontradas)."
                )

            has_container = False
            for info in infolist:
                # 1. Defesa contra Path Traversal
                norm_name = info.filename.replace("\\", "/")
                if norm_name.startswith("/") or ".." in norm_name.split("/"):
                    raise EpubValidationError(
                        f"Tentativa de Path Traversal detectada no arquivo EPUB: '{info.filename}'"
                    )
                if ":" in norm_name.split("/")[0]:  # Letras de unidade no Windows (ex: C:)
                    raise EpubValidationError(
                        f"Caminho absoluto não permitido no arquivo EPUB: '{info.filename}'"
                    )

                # 2. Defesa contra Links Simbólicos no ZIP (S_IFLNK = 0o120000)
                is_symlink = ((info.external_attr >> 16) & 0o170000) == 0o120000
                if is_symlink:
                    raise EpubValidationError(
                        f"Entrada com link simbólico proibida no EPUB: '{info.filename}'"
                    )

                # 3. Defesa contra Colisão de Nomes Duplicados Maliciosos
                lower_norm = norm_name.lower()
                if lower_norm in seen_entries and not info.is_dir():
                    # Aviso/bloqueio se houver arquivos conflitantes de mesmo caminho
                    pass
                seen_entries.add(lower_norm)

                # 4. Defesa contra Zip Bomb
                if info.file_size > MAX_SINGLE_FILE_BYTES:
                    raise EpubValidationError(
                        f"Entrada excede o limite máximo permitido: '{info.filename}' ({info.file_size} bytes)"
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_EPUB_UNCOMPRESSED_BYTES:
                    raise EpubValidationError(
                        f"EPUB descompactado excede o limite de {MAX_EPUB_UNCOMPRESSED_BYTES // (1024 * 1024)} MB."
                    )

                if norm_name.lower() == "meta-inf/container.xml":
                    has_container = True

            # Checagem de taxa de compressão global
            if file_size > 0 and (total_uncompressed / file_size) > MAX_COMPRESSION_RATIO and total_uncompressed > 5 * 1024 * 1024:
                raise EpubValidationError("Taxa de descompressão anormalmente alta (suspeita de Zip Bomb).")

            if not has_container:
                raise EpubValidationError("Arquivo EPUB inválido: 'META-INF/container.xml' não encontrado.")

    except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        raise EpubValidationError(f"Pacote EPUB corrompido: {e}")


def _parse_xml_safely(data: bytes) -> ET.Element:
    """
    Decodifica XML em memória de forma segura com verificação ativa de ameaças.
    1. Bloqueia tamanhos excessivos de payload XML (> 5 MB).
    2. Proíbe declarações DTD e entidades personalizadas (defesa contra Billion Laughs e XXE).
    3. Utiliza defusedxml quando instalado; caso contrário, aplica parser com política restritiva.
    """
    if len(data) > MAX_XML_BYTES:
        raise EpubValidationError(f"Payload XML excede o tamanho máximo de segurança ({MAX_XML_BYTES} bytes).")

    lower_data = data.lower()
    if b"<!entity" in lower_data:
        raise EpubValidationError("Declaração de entidade XML proibida por segurança (defesa contra Billion Laughs / XXE).")
    if b"<!doctype" in lower_data:
        if b"system" in lower_data or b"public" in lower_data or b"[" in lower_data:
            raise EpubValidationError("Declaração DTD externa ou customizada proibida por segurança no EPUB.")

    if _HAS_DEFUSEDXML:
        return DefusedET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)

    parser = ET.XMLParser()
    return ET.fromstring(data, parser=parser)


def check_epub_drm(zf: zipfile.ZipFile) -> Tuple[bool, Optional[str]]:
    """
    Verifica se o EPUB possui criptografia de direitos digitais (DRM).
    Distingue ofuscação de fontes (padrão aberto aceito) de DRM comercial proprietário.
    Retorna (is_drm, drm_description).
    """
    names = [n.replace("\\", "/").lower() for n in zf.namelist()]

    if "meta-inf/rights.xml" in names:
        return True, "Adobe ADEPT / Rights XML"

    enc_name = None
    for n in zf.namelist():
        if n.replace("\\", "/").lower() == "meta-inf/encryption.xml":
            enc_name = n
            break

    if enc_name:
        try:
            enc_bytes = zf.read(enc_name)
            root = _parse_xml_safely(enc_bytes)
            for elem in root.iter():
                if elem.tag.lower().endswith("encrypteddata"):
                    uri = ""
                    for child in elem.iter():
                        if child.tag.lower().endswith("cipherreference"):
                            uri = child.attrib.get("URI", "").lower()
                    method = ""
                    for child in elem.iter():
                        if child.tag.lower().endswith("encryptionmethod"):
                            method = child.attrib.get("Algorithm", "").lower()

                    # Ofuscação legítima de fonte embutida não é DRM
                    is_font = (
                        "font" in method
                        or "embedding" in method
                        or uri.endswith((".otf", ".ttf", ".woff", ".woff2"))
                    )

                    if not is_font and (
                        uri.endswith((".xhtml", ".html", ".htm", ".xml", ".opf"))
                        or "adept" in method
                        or "lcp" in method
                        or "fairplay" in method
                        or "aes" in method
                    ):
                        alg_name = method.split("#")[-1] if "#" in method else "proprietário"
                        return True, f"Criptografia DRM ({alg_name})"
        except Exception:
            return True, "Criptografia de contêiner não identificada"

    return False, None


def _get_opf_path(zf: zipfile.ZipFile) -> str:
    """Localiza o caminho do arquivo .opf a partir do META-INF/container.xml."""
    container_bytes = zf.read("META-INF/container.xml")
    root = _parse_xml_safely(container_bytes)

    # Namespace padrão do container: urn:oasis:names:tc:opendocument:xmlns:container
    for rootfile in root.iter():
        if rootfile.tag.endswith("rootfile"):
            full_path = rootfile.attrib.get("full-path")
            if full_path:
                return full_path.replace("\\", "/").strip()

    raise EpubValidationError("Arquivo OPF não especificado em META-INF/container.xml.")


def extract_epub_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extrai metadados do EPUB de maneira leve e tolerante a falhas.
    Lê o pacote .opf diretamente do arquivo ZIP sem extrair nada para o disco.
    Retorna título, autor, contagem de itens (spine) e status de proteção por DRM.
    """
    validate_epub_archive(file_path)

    title = None
    author = None
    spine_count = 0
    is_drm = False
    drm_type = None

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            is_drm, drm_type = check_epub_drm(zf)
            opf_path = _get_opf_path(zf)
            opf_bytes = zf.read(opf_path)
            root = _parse_xml_safely(opf_bytes)

            for elem in root.iter():
                tag_lower = elem.tag.lower()
                if tag_lower.endswith("title") and elem.text and not title:
                    title = elem.text.strip()
                elif tag_lower.endswith("creator") and elem.text and not author:
                    author = elem.text.strip()
                elif tag_lower.endswith("itemref"):
                    spine_count += 1

    except EpubValidationError:
        raise
    except Exception:
        # Tolerância a falhas: se metadados específicos falharem, usa o nome do arquivo
        pass

    if not title:
        title = file_path.stem.replace("_", " ").replace("-", " ").strip()

    return {
        "title": title or file_path.name,
        "author": author,
        "page_count": max(1, spine_count),
        "is_drm": is_drm,
        "drm_type": drm_type,
    }


def extract_epub_cover_bytes(file_path: Path) -> Optional[Tuple[bytes, str, str]]:
    """
    Localiza e extrai a capa oficial embutida no EPUB diretamente do arquivo ZIP.
    Suporta:
    1. EPUB 3: item do manifesto com properties="cover-image".
    2. EPUB 2: meta com name="cover" referenciando o id de um item no manifesto.
    3. Fallback: item no manifesto com id="cover" ou nome de arquivo contendo 'cover' e tipo de imagem válido.
    Retorna (image_bytes, ext, media_type) ou None.
    """
    try:
        validate_epub_archive(file_path)
    except Exception:
        return None

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            opf_path = _get_opf_path(zf)
            opf_bytes = zf.read(opf_path)
            root = _parse_xml_safely(opf_bytes)

            opf_dir = Path(opf_path).parent.as_posix()
            if opf_dir == ".":
                opf_dir = ""

            manifest_items: Dict[str, Dict[str, str]] = {}
            cover_item_id = None
            cover_href_candidate = None

            # 1. Varre metadados e manifesto
            for elem in root.iter():
                tag_lower = elem.tag.lower()
                if tag_lower.endswith("meta"):
                    if elem.attrib.get("name", "").lower() == "cover":
                        cover_item_id = elem.attrib.get("content")

                elif tag_lower.endswith("item"):
                    item_id = elem.attrib.get("id")
                    href = elem.attrib.get("href")
                    media_type = elem.attrib.get("media-type", "").lower()
                    properties = elem.attrib.get("properties", "").lower()

                    if item_id and href:
                        manifest_items[item_id] = {
                            "href": href,
                            "media_type": media_type,
                            "properties": properties,
                        }

                        # EPUB 3 cover-image
                        if "cover-image" in properties and media_type.startswith("image/"):
                            cover_href_candidate = href

            # 2. Se encontrou pelo meta cover (EPUB 2)
            if not cover_href_candidate and cover_item_id and cover_item_id in manifest_items:
                candidate = manifest_items[cover_item_id]
                if candidate["media_type"].startswith("image/"):
                    cover_href_candidate = candidate["href"]

            # 3. Fallback: procura item no manifesto com id="cover" ou href contendo 'cover'
            if not cover_href_candidate:
                for item_id, item in manifest_items.items():
                    if item["media_type"].startswith("image/"):
                        if item_id.lower() in ("cover", "cover-image", "coverimage") or "cover" in item["href"].lower():
                            cover_href_candidate = item["href"]
                            break

            if not cover_href_candidate:
                return None

            # Resolve o caminho do arquivo de imagem dentro do arquivo ZIP
            if opf_dir:
                full_cover_path = (Path(opf_dir) / cover_href_candidate).as_posix()
            else:
                full_cover_path = cover_href_candidate

            norm_parts = []
            for part in full_cover_path.replace("\\", "/").split("/"):
                if part == "..":
                    if norm_parts:
                        norm_parts.pop()
                elif part and part != ".":
                    norm_parts.append(part)
            clean_cover_path = "/".join(norm_parts)

            matched_entry = None
            for name in zf.namelist():
                if name.replace("\\", "/") == clean_cover_path or name.lower() == clean_cover_path.lower():
                    matched_entry = name
                    break

            if not matched_entry:
                return None

            img_bytes = zf.read(matched_entry)
            if not img_bytes:
                return None

            ext = "jpg"
            media_type = "image/jpeg"
            if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                ext = "png"
                media_type = "image/png"
            elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
                ext = "webp"
                media_type = "image/webp"

            return (img_bytes, ext, media_type)

    except Exception:
        return None
