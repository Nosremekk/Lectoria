"""
Módulo de gerenciamento de capas de livros do Lectoria (Iteração 8).
Implementa estratégia em três camadas:
1. Prioridade 1: Extração de capa real a partir da primeira página do PDF ou cache renderizado pelo leitor (PDF.js),
   com invalidação automática caso o hash do arquivo PDF mude.
2. Prioridade 2: Capa personalizada enviada pelo usuário/administrador (compartilhada no catálogo),
   com validação rigorosa de assinatura binária (magic bytes), dimensões e limite de tamanho.
3. Prioridade 3: Capa editorial substituta determinística em SVG (sem IA e sem serviços externos).
"""

from datetime import datetime
from email.parser import BytesParser
from email.policy import default as default_email_policy
import hashlib
import os
from pathlib import Path
import struct
from typing import Any, Dict, Optional, Tuple
import zlib
import pypdf

import config
from database import get_db
from library import BookNotFoundError, SecurityError, resolve_book_path

# Limite máximo de 5 MB para upload de capas personalizadas
MAX_COVER_SIZE_BYTES = 5 * 1024 * 1024
MIN_COVER_SIZE_BYTES = 24


class CoverError(Exception):
    """Exceção base para erros relacionados a capas."""
    pass


class CoverValidationError(CoverError):
    """Exceção levantada quando o arquivo de capa enviado é inválido ou inseguro."""
    pass


class CoverTooLargeError(CoverError):
    """Exceção levantada quando o arquivo de capa excede o limite de tamanho."""
    pass


# Paletas editoriais clássicas inspiradas em encadernações de tecido e imprensa tradicional
EDITORIAL_PALETTES = [
    {"bg": "#1F3042", "bg_dark": "#152230", "ink": "#F4EFE6", "accent": "#C8A97E", "muted": "#A3B1BF"},  # Azul Prússia
    {"bg": "#4F2124", "bg_dark": "#381518", "ink": "#F6F0E6", "accent": "#D4B483", "muted": "#C49FA2"},  # Borgonha Clássico
    {"bg": "#233B2E", "bg_dark": "#17281F", "ink": "#F2EFE9", "accent": "#C2A878", "muted": "#9BB0A3"},  # Verde Musgo
    {"bg": "#5C3D24", "bg_dark": "#402916", "ink": "#F7F2E8", "accent": "#DFC18D", "muted": "#C4AE99"},  # Ocre Encadernação
    {"bg": "#2C2B29", "bg_dark": "#1D1C1A", "ink": "#F0ECE1", "accent": "#C5A06D", "muted": "#9E9B95"},  # Grafite Editorial
    {"bg": "#3A263B", "bg_dark": "#261827", "ink": "#F5EFE8", "accent": "#CEB08A", "muted": "#B29FB3"},  # Aubergine Sóbrio
]


def _escape_xml(text: str) -> str:
    """Escapa caracteres especiais para inserção segura em SVG/XML."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def validate_cover_image(data: bytes) -> Tuple[str, str, int, int]:
    """
    Valida os bytes reais (magic bytes) e cabeçalho de uma imagem de capa.
    Suporta exclusivamente PNG, JPEG e WebP.
    Rejeita HTML, SVG, scripts, arquivos truncados ou maiores que MAX_COVER_SIZE_BYTES.
    Retorna (extensao, mime_type, largura, altura).
    """
    if not data or not isinstance(data, (bytes, bytearray)):
        raise CoverValidationError("Nenhum dado de imagem recebido.")

    size = len(data)
    if size < MIN_COVER_SIZE_BYTES:
        raise CoverValidationError("Arquivo de imagem muito pequeno ou truncado.")

    if size > MAX_COVER_SIZE_BYTES:
        raise CoverTooLargeError(
            f"A imagem excede o tamanho máximo permitido de {MAX_COVER_SIZE_BYTES // (1024 * 1024)} MB."
        )

    # Bloqueio defensivo contra payloads de script disfarçados no início do arquivo
    head_lower = bytes(data[:512]).lower()
    for forbidden_token in (b"<script", b"<!doctype html", b"<html", b"<svg", b"javascript:"):
        if forbidden_token in head_lower:
            raise CoverValidationError("Conteúdo executável ou marcação não permitida em imagem de capa.")

    # 1. Validação de PNG: \x89PNG\r\n\x1a\n seguido por chunk IHDR
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise CoverValidationError("Cabeçalho PNG malformado (IHDR ausente).")
        width, height = struct.unpack(">II", data[16:24])
        if width < 16 or height < 16 or width > 12000 or height > 12000:
            raise CoverValidationError("Dimensões de imagem PNG inválidas (mínimo 16x16 pixels).")
        return ("png", "image/png", width, height)

    # 2. Validação de JPEG: \xff\xd8\xff
    if data[:3] == b"\xff\xd8\xff":
        width, height = _parse_jpeg_dimensions(data)
        if width < 16 or height < 16 or width > 12000 or height > 12000:
            raise CoverValidationError("Arquivo JPEG corrompido ou com dimensões inválidas (mínimo 16x16 pixels).")
        return ("jpg", "image/jpeg", width, height)

    # 3. Validação de WebP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:15] != b"VP8":
            raise CoverValidationError("Cabeçalho WebP inválido.")
        return ("webp", "image/webp", 400, 600)

    raise CoverValidationError("Formato de arquivo inválido. Envie apenas imagens PNG, JPEG ou WebP.")


def _parse_jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    """Extrai largura e altura de um stream JPEG inspecionando os marcadores SOF."""
    idx = 2
    length = len(data)
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3,
        0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB,
        0xCD, 0xCE, 0xCF,
    }
    while idx < length - 8:
        if data[idx] != 0xFF:
            idx += 1
            continue
        while idx < length and data[idx] == 0xFF:
            idx += 1
        if idx >= length:
            break
        marker = data[idx]
        idx += 1

        if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
            continue

        if idx + 2 > length:
            break
        seg_len = struct.unpack(">H", data[idx:idx + 2])[0]
        if seg_len < 2 or idx + seg_len > length:
            break

        if marker in sof_markers:
            if idx + 7 <= length:
                height, width = struct.unpack(">HH", data[idx + 3:idx + 7])
                return (width, height)
            break

        idx += seg_len

    return (0, 0)


def _encode_raw_pixels_to_png(raw_pixels: bytes, width: int, height: int, channels: int) -> Optional[bytes]:
    """
    Converte um buffer bruto de pixels 8-bit (RGB ou Grayscale) em um arquivo PNG válido
    usando exclusivamente a biblioteca padrão do Python (struct + zlib).
    """
    if width <= 0 or height <= 0 or channels not in (1, 3):
        return None
    row_bytes = width * channels
    expected_len = row_bytes * height
    if len(raw_pixels) < expected_len:
        return None

    # Constrói scanlines precedidas pelo byte de filtro 0 (None)
    scanlines = bytearray()
    for y in range(height):
        start = y * row_bytes
        scanlines.append(0)
        scanlines.extend(raw_pixels[start:start + row_bytes])

    def _make_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        return struct.pack(">I", len(chunk_data)) + chunk_type + chunk_data + struct.pack(">I", crc)

    color_type = 2 if channels == 3 else 0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    compressed_idat = zlib.compress(bytes(scanlines), level=6)

    return (
        b"\x89PNG\r\n\x1a\n"
        + _make_chunk(b"IHDR", ihdr_data)
        + _make_chunk(b"IDAT", compressed_idat)
        + _make_chunk(b"IEND", b"")
    )


def _extract_cover_from_pdf_file(pdf_path: Path) -> Optional[Tuple[bytes, str, str]]:
    """
    Inspeciona as primeiras páginas do arquivo PDF em busca de uma imagem de capa real.
    Suporta streams JPEG (/DCTDecode) e buffers de imagem (/FlateDecode RGB/Gray).
    Trata de maneira segura PDFs protegidos por senha, truncados ou corrompidos.
    """
    try:
        with open(pdf_path, "rb") as fh:
            reader = pypdf.PdfReader(fh, strict=False)
            if reader.is_encrypted:
                try:
                    dec_result = reader.decrypt("")
                    if dec_result == 0:
                        return None
                except Exception:
                    return None

            num_pages = len(reader.pages)
            if num_pages == 0:
                return None

            # Inspeciona até as 2 primeiras páginas em busca da imagem principal da capa
            pages_to_check = min(2, num_pages)
            for page_idx in range(pages_to_check):
                page = reader.pages[page_idx]
                resources = page.get("/Resources")
                if not resources:
                    continue
                resources_obj = resources.get_object()
                xobjects = resources_obj.get("/XObject")
                if not xobjects:
                    continue
                xobjects_obj = xobjects.get_object()

                best_candidate: Optional[Tuple[int, bytes, str, str]] = None

                for _, ref in xobjects_obj.items():
                    try:
                        obj = ref.get_object()
                        if obj.get("/Subtype") != "/Image":
                            continue

                        width = int(obj.get("/Width") or 0)
                        height = int(obj.get("/Height") or 0)
                        if width < 48 or height < 48 or width > 8000 or height > 8000:
                            continue

                        area = width * height
                        filters = obj.get("/Filter")
                        if isinstance(filters, list):
                            filter_list = [str(f) for f in filters]
                        elif filters:
                            filter_list = [str(filters)]
                        else:
                            filter_list = []

                        # Caso 1: Imagem JPEG incorporada (/DCTDecode)
                        if "/DCTDecode" in filter_list:
                            raw_stream = getattr(obj, "_data", None)
                            if not raw_stream:
                                continue
                            if filter_list[0] == "/FlateDecode":
                                raw_stream = zlib.decompress(raw_stream)

                            if (
                                len(raw_stream) <= MAX_COVER_SIZE_BYTES
                                and raw_stream[:3] == b"\xff\xd8\xff"
                            ):
                                if best_candidate is None or area > best_candidate[0]:
                                    best_candidate = (area, raw_stream, "jpg", "image/jpeg")

                        # Caso 2: Imagem raster comprimida com FlateDecode (RGB ou Grayscale 8-bit)
                        elif filter_list == ["/FlateDecode"] or not filter_list:
                            bpc = int(obj.get("/BitsPerComponent") or 8)
                            cs = str(obj.get("/ColorSpace") or "/DeviceRGB")
                            if bpc == 8 and cs in ("/DeviceRGB", "/DeviceGray") and area <= (2500 * 2500):
                                decoded = obj.get_data()
                                channels = 3 if cs == "/DeviceRGB" else 1
                                png_bytes = _encode_raw_pixels_to_png(decoded, width, height, channels)
                                if png_bytes and len(png_bytes) <= MAX_COVER_SIZE_BYTES:
                                    if best_candidate is None or area > best_candidate[0]:
                                        best_candidate = (area, png_bytes, "png", "image/png")
                    except Exception:
                        continue

                if best_candidate is not None:
                    return (best_candidate[1], best_candidate[2], best_candidate[3])

        return None
    except Exception:
        return None


def _wrap_title_lines(title: str, max_chars: int = 18, max_lines: int = 4) -> list[str]:
    """Divide o título do livro em linhas equilibradas para diagramação na capa SVG."""
    words = (title or "Documento Sem Título").strip().split()
    if not words:
        return ["Documento"]

    lines: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars and not current:
            lines.append(word[:max_chars - 1] + "…")
        elif not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break

    if current and len(lines) < max_lines:
        lines.append(current)

    # Se sobraram palavras, adiciona reticências na última linha
    total_joined = " ".join(lines)
    full_joined = " ".join(words)
    if len(full_joined) > len(total_joined) and lines:
        if not lines[-1].endswith("…"):
            lines[-1] = lines[-1][: max(1, max_chars - 1)].rstrip() + "…"

    return lines


def generate_editorial_svg_cover(book_id: str, title: str, author: Optional[str] = None) -> str:
    """
    Gera uma capa editorial determinística em SVG para livros sem capa de imagem.
    A aparência é derivada do hash determinístico do livro, mantendo consistência entre reinicializações.
    """
    seed_str = f"{book_id}:{title or ''}:{author or ''}"
    digest = hashlib.sha256(seed_str.encode("utf-8")).digest()
    palette = EDITORIAL_PALETTES[digest[0] % len(EDITORIAL_PALETTES)]
    motif_type = digest[1] % 4

    clean_title = (title or "Obra Sem Título").strip()
    clean_author = (author or "Acervo Pessoal").strip()
    if len(clean_author) > 28:
        clean_author = clean_author[:27].rstrip() + "…"

    title_lines = _wrap_title_lines(clean_title, max_chars=18, max_lines=4)
    title_tspans = ""
    start_y = 185 - (len(title_lines) - 1) * 16
    for idx, line in enumerate(title_lines):
        dy = 0 if idx == 0 else 34
        title_tspans += f'<tspan x="190" dy="{dy}">{_escape_xml(line)}</tspan>'

    # Ornamento central discreto e determinístico
    if motif_type == 0:
        motif_svg = f"""
        <circle cx="190" cy="340" r="24" fill="none" stroke="{palette['accent']}" stroke-width="1" opacity="0.65"/>
        <circle cx="190" cy="340" r="16" fill="none" stroke="{palette['accent']}" stroke-width="0.75" opacity="0.45"/>
        <path d="M178 340 H202 M190 328 V352" stroke="{palette['accent']}" stroke-width="1" opacity="0.65"/>
        """
    elif motif_type == 1:
        motif_svg = f"""
        <rect x="170" y="320" width="40" height="40" transform="rotate(45 190 340)" fill="none" stroke="{palette['accent']}" stroke-width="1" opacity="0.65"/>
        <rect x="176" y="326" width="28" height="28" transform="rotate(45 190 340)" fill="none" stroke="{palette['accent']}" stroke-width="0.75" opacity="0.45"/>
        """
    elif motif_type == 2:
        motif_svg = f"""
        <line x1="145" y1="336" x2="235" y2="336" stroke="{palette['accent']}" stroke-width="1" opacity="0.7"/>
        <line x1="165" y1="344" x2="215" y2="344" stroke="{palette['accent']}" stroke-width="1" opacity="0.5"/>
        <circle cx="190" cy="340" r="3.5" fill="{palette['accent']}" opacity="0.8"/>
        """
    else:
        initial = _escape_xml(clean_title[0].upper()) if clean_title else "L"
        motif_svg = f"""
        <rect x="168" y="318" width="44" height="44" rx="2" fill="none" stroke="{palette['accent']}" stroke-width="1" opacity="0.6"/>
        <text x="190" y="348" text-anchor="middle" fill="{palette['accent']}" font-family="Georgia, serif" font-size="22" font-style="italic" opacity="0.85">{initial}</text>
        """

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 510" width="100%" height="100%">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{palette['bg']}"/>
      <stop offset="100%" stop-color="{palette['bg_dark']}"/>
    </linearGradient>
    <linearGradient id="spineGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#000000" stop-opacity="0.38"/>
      <stop offset="35%" stop-color="#ffffff" stop-opacity="0.06"/>
      <stop offset="75%" stop-color="#000000" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0.0"/>
    </linearGradient>
  </defs>
  <rect width="360" height="510" fill="url(#bgGrad)"/>
  <rect width="22" height="510" fill="url(#spineGrad)"/>
  <line x1="22" y1="0" x2="22" y2="510" stroke="{palette['accent']}" stroke-width="0.6" opacity="0.25"/>

  <!-- Moldura Editorial Dupla -->
  <rect x="40" y="28" width="296" height="454" fill="none" stroke="{palette['accent']}" stroke-width="1.2" opacity="0.55"/>
  <rect x="46" y="34" width="284" height="442" fill="none" stroke="{palette['accent']}" stroke-width="0.6" opacity="0.35"/>

  <!-- Cabeçalho da Coleção -->
  <text x="190" y="66" text-anchor="middle" fill="{palette['accent']}" font-family=" sans-serif" font-size="10" letter-spacing="3" opacity="0.8">EDIÇÃO DIGITAL</text>
  <line x1="150" y1="78" x2="230" y2="78" stroke="{palette['accent']}" stroke-width="0.75" opacity="0.45"/>

  <!-- Título da Obra -->
  <text x="190" y="{start_y}" text-anchor="middle" fill="{palette['ink']}" font-family="Georgia, 'Palatino Linotype', 'Book Antiqua', serif" font-size="25" font-weight="600">
    {title_tspans}
  </text>

  <!-- Ornamento Editorial -->
  {motif_svg}

  <!-- Rodapé / Autor -->
  <line x1="130" y1="422" x2="250" y2="422" stroke="{palette['accent']}" stroke-width="0.6" opacity="0.35"/>
  <text x="190" y="448" text-anchor="middle" fill="{palette['muted']}" font-family="Georgia, serif" font-size="14" font-style="italic">
    {_escape_xml(clean_author)}
  </text>
</svg>"""
    return svg


def _remove_cached_files_for_book(book_id: str, prefix_filter: Optional[str] = None) -> None:
    """Remove arquivos de capa associados a um livro no diretório COVERS_DIR."""
    if not config.COVERS_DIR.exists():
        return
    pattern = f"{book_id}_{prefix_filter}*" if prefix_filter else f"{book_id}_*"
    for p in config.COVERS_DIR.glob(pattern):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def cleanup_covers_for_book_ids(book_ids: list[str]) -> None:
    """Remove os caches de capas geradas de uma lista de livros (ex: ao remover uma fonte)."""
    for bid in book_ids:
        _remove_cached_files_for_book(bid)


def invalidate_extracted_cover_if_needed(book_id: str) -> None:
    """Remove o cache de capa extraída quando o arquivo PDF original sofreu alteração."""
    _remove_cached_files_for_book(book_id, prefix_filter="extracted")


def get_or_create_book_cover(book_id: str) -> Dict[str, Any]:
    """
    Resolve a capa de um livro seguindo a hierarquia:
    1. Capa personalizada ('custom') válida no disco.
    2. Capa extraída/renderizada ('extracted') em cache cujo cover_source_hash coincide com file_hash atual.
    3. Extração sob demanda a partir da primeira página do PDF (se o PDF possuir imagem incorporada).
    4. Capa substituta editorial determinística em SVG ('fallback').
    """
    # Primeiro valida se o livro existe, se a fonte está ativa e se o PDF está acessível no disco
    pdf_path = resolve_book_path(book_id)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, author, file_hash, cover_type, cover_path, cover_source_hash
            FROM books WHERE id = ?
            """,
            (book_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise BookNotFoundError(f"Livro '{book_id}' não encontrado.")
        book = dict(row)

    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    cover_type = book.get("cover_type") or "none"
    cover_filename = book.get("cover_path")
    current_file_hash = book.get("file_hash") or ""

    # 1. Prioridade máxima: Capa personalizada definida pelo usuário/admin
    if cover_type == "custom" and cover_filename:
        custom_path = (config.COVERS_DIR / cover_filename).resolve()
        if custom_path.is_relative_to(config.COVERS_DIR.resolve()) and custom_path.is_file():
            ext = custom_path.suffix.lower().lstrip(".")
            mime = "image/png" if ext == "png" else "image/webp" if ext == "webp" else "image/jpeg"
            return {
                "type": "custom",
                "file_path": custom_path,
                "media_type": mime,
                "content": None,
            }

    # 2. Capa extraída em cache (desde que o PDF não tenha sido modificado desde a extração)
    if (
        cover_type == "extracted"
        and cover_filename
        and book.get("cover_source_hash") == current_file_hash
    ):
        cached_path = (config.COVERS_DIR / cover_filename).resolve()
        if cached_path.is_relative_to(config.COVERS_DIR.resolve()) and cached_path.is_file():
            ext = cached_path.suffix.lower().lstrip(".")
            mime = "image/png" if ext == "png" else "image/webp" if ext == "webp" else "image/jpeg"
            return {
                "type": "extracted",
                "file_path": cached_path,
                "media_type": mime,
                "content": None,
            }

    # Se já foi avaliado para este mesmo file_hash e resultou em fallback, evita reabrir o PDF a cada requisição
    if cover_type == "fallback" and book.get("cover_source_hash") == current_file_hash:
        svg_str = generate_editorial_svg_cover(book_id, book["title"], book.get("author"))
        return {
            "type": "fallback",
            "file_path": None,
            "media_type": "image/svg+xml",
            "content": svg_str.encode("utf-8"),
        }

    # 3. Tenta extrair imagem real de capa (PDF ou EPUB) sob demanda
    if pdf_path.suffix.lower() == ".epub":
        from epub_service import extract_epub_cover_bytes
        extracted = extract_epub_cover_bytes(pdf_path)
    else:
        extracted = _extract_cover_from_pdf_file(pdf_path)
    now_iso = datetime.now().isoformat()

    if extracted is not None:
        img_bytes, ext, mime = extracted
        target_filename = f"{book_id}_extracted.{ext}"
        target_path = config.COVERS_DIR / target_filename
        _remove_cached_files_for_book(book_id, prefix_filter="extracted")
        target_path.write_bytes(img_bytes)

        with get_db() as conn:
            conn.execute(
                """
                UPDATE books
                SET cover_type = 'extracted', cover_path = ?, cover_source_hash = ?, cover_updated_at = ?
                WHERE id = ?
                """,
                (target_filename, current_file_hash, now_iso, book_id),
            )

        return {
            "type": "extracted",
            "file_path": target_path,
            "media_type": mime,
            "content": None,
        }

    # 4. Nenhuma imagem de capa identificável no PDF -> registra estado 'fallback' para o hash atual
    with get_db() as conn:
        conn.execute(
            """
            UPDATE books
            SET cover_type = 'fallback', cover_path = NULL, cover_source_hash = ?, cover_updated_at = ?
            WHERE id = ? AND cover_type != 'custom'
            """,
            (current_file_hash, now_iso, book_id),
        )

    svg_str = generate_editorial_svg_cover(book_id, book["title"], book.get("author"))
    return {
        "type": "fallback",
        "file_path": None,
        "media_type": "image/svg+xml",
        "content": svg_str.encode("utf-8"),
    }


def save_custom_cover(book_id: str, raw_image_bytes: bytes) -> Dict[str, Any]:
    """
    Valida e armazena uma capa personalizada para o livro indicado.
    Substitui capas personalizadas anteriores sem tocar no arquivo PDF original.
    """
    # Garante que o livro existe e está acessível
    resolve_book_path(book_id)

    ext, mime, width, height = validate_cover_image(raw_image_bytes)
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)

    # Remove capa personalizada anterior se existir
    _remove_cached_files_for_book(book_id, prefix_filter="custom")

    target_filename = f"{book_id}_custom.{ext}"
    target_path = config.COVERS_DIR / target_filename
    target_path.write_bytes(raw_image_bytes)

    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE books
            SET cover_type = 'custom', cover_path = ?, cover_updated_at = ?
            WHERE id = ?
            """,
            (target_filename, now_iso, book_id),
        )

    return {
        "book_id": book_id,
        "cover_type": "custom",
        "mime_type": mime,
        "width": width,
        "height": height,
        "cover_url": f"/api/books/{book_id}/cover?v={int(datetime.now().timestamp())}",
    }


def save_rendered_cover_cache(book_id: str, raw_image_bytes: bytes) -> Dict[str, Any]:
    """
    Armazena em cache a miniatura da 1ª página renderizada pelo cliente (PDF.js),
    desde que o livro ainda não possua uma capa personalizada ('custom').
    """
    resolve_book_path(book_id)
    ext, mime, width, height = validate_cover_image(raw_image_bytes)
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cover_type, file_hash FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()
        if not row:
            raise BookNotFoundError(f"Livro '{book_id}' não encontrado.")
        if row["cover_type"] == "custom":
            return {"book_id": book_id, "cover_type": "custom", "cached": False}
        file_hash = row["file_hash"] or ""

    _remove_cached_files_for_book(book_id, prefix_filter="extracted")
    target_filename = f"{book_id}_extracted.{ext}"
    target_path = config.COVERS_DIR / target_filename
    target_path.write_bytes(raw_image_bytes)

    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE books
            SET cover_type = 'extracted', cover_path = ?, cover_source_hash = ?, cover_updated_at = ?
            WHERE id = ? AND cover_type != 'custom'
            """,
            (target_filename, file_hash, now_iso, book_id),
        )

    return {
        "book_id": book_id,
        "cover_type": "extracted",
        "mime_type": mime,
        "width": width,
        "height": height,
        "cached": True,
    }


def delete_custom_cover(book_id: str) -> Dict[str, Any]:
    """
    Remove a capa personalizada de um livro e restaura a detecção automática
    (capa extraída do PDF ou capa editorial substituta).
    """
    resolve_book_path(book_id)
    _remove_cached_files_for_book(book_id, prefix_filter="custom")

    now_iso = datetime.now().isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE books
            SET cover_type = 'none', cover_path = NULL, cover_source_hash = NULL, cover_updated_at = ?
            WHERE id = ?
            """,
            (now_iso, book_id),
        )

    # Resolve imediatamente o novo estado (extraído ou fallback)
    resolved = get_or_create_book_cover(book_id)
    return {
        "book_id": book_id,
        "cover_type": resolved["type"],
        "cover_url": f"/api/books/{book_id}/cover?v={int(datetime.now().timestamp())}",
    }


def extract_uploaded_image_from_request(body: bytes, content_type: str) -> bytes:
    """
    Extrai os bytes da imagem tanto de uma requisição multipart/form-data
    quanto de um envio binário direto (image/png, image/jpeg, image/webp),
    sem depender de pacotes externos como python-multipart.
    """
    if len(body) > MAX_COVER_SIZE_BYTES + 65536:
        raise CoverTooLargeError(
            f"O corpo da requisição excede o limite máximo de {MAX_COVER_SIZE_BYTES // (1024 * 1024)} MB."
        )

    ct_lower = (content_type or "").lower()
    if "multipart/form-data" in ct_lower:
        raw_headers = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        msg = BytesParser(policy=default_email_policy).parsebytes(raw_headers + body)
        if msg.is_multipart():
            for part in msg.iter_parts():
                payload = part.get_payload(decode=True)
                if payload:
                    return payload
        raise CoverValidationError("Nenhum arquivo encontrado no envio multipart/form-data.")

    return body
