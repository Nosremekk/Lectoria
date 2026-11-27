"""
Serviço centralizado de geração e gerenciamento de miniaturas (thumbnails) de capas (Fase 12.3).

Princípios arquiteturais e de segurança:
- Preservação absoluta das capas originais: nenhum arquivo em COVERS_DIR raiz é alterado ou removido.
- Derivados armazenados exclusivamente em `<COVERS_DIR>/thumbs/`, regeneráveis a qualquer momento
  e excluídos do backup obrigatório.
- Identidade determinística do derivado vinculada ao fingerprint de conteúdo/stat do original,
  variante de tamanho (`card` = 360px, `detail` = 600px), versão do algoritmo (`v1`) e formato (`webp`/`jpg`).
- Nunca amplia (upscale) imagens menores que a largura alvo.
- Gravação atômica via arquivo temporário no mesmo filesystem + `os.replace()`.
- Controle de concorrência em duas camadas: lock por chave (evita geração duplicada) e
  `BoundedSemaphore(2)` global (protege CPU/RAM em VPS de 1 vCPU).
- Proteção contra decompression bombs (`Image.MAX_IMAGE_PIXELS`), dimensões excessivas,
  arquivos corrompidos/truncados, orientação EXIF e transparência.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Dict, Optional, Set, Tuple

from PIL import Image, ImageOps, features as pil_features

import config

logger = logging.getLogger("lectoria.thumbnails")

# Proteção rigorosa contra decompression bombs (40 megapixels máx.)
MAX_THUMB_SOURCE_PIXELS = 40_000_000
MAX_THUMB_SOURCE_DIM = 12_000
Image.MAX_IMAGE_PIXELS = MAX_THUMB_SOURCE_PIXELS

THUMB_ALGO_VERSION = "v1"

# Variantes oficiais de largura máxima (preservando proporção, sem upscale)
THUMB_VARIANTS: Dict[str, int] = {
    "card": 360,
    "detail": 600,
}

# Qualidade equilibrada entre nitidez tipográfica e tamanho alvo (12-25 KB em cards)
WEBP_QUALITY = 80
WEBP_METHOD = 4
JPEG_QUALITY = 82

# Limite global de gerações simultâneas para preservar CPU/RAM em VPS de 1 vCPU
_GENERATION_SEMAPHORE = threading.BoundedSemaphore(2)
_KEY_LOCKS_GUARD = threading.Lock()
_KEY_LOCKS: Dict[str, threading.Lock] = {}

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def get_thumbs_dir() -> Path:
    """Retorna dinamicamente o diretório de thumbnails derivado de config.COVERS_DIR."""
    return config.COVERS_DIR / "thumbs"


def _sanitize_book_id(book_id: str) -> str:
    cleaned = _SAFE_ID_RE.sub("", str(book_id or ""))
    if not cleaned:
        raise ValueError("Identificador de livro inválido para thumbnail.")
    return cleaned[:64]


def _get_key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            if len(_KEY_LOCKS) > 512:
                # Limpa locks ociosos para evitar crescimento ilimitado em memória
                idle = [k for k, lk in _KEY_LOCKS.items() if not lk.locked()]
                for k in idle[:256]:
                    _KEY_LOCKS.pop(k, None)
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def supports_webp() -> bool:
    """Verifica se a build atual do Pillow possui suporte nativo a WebP."""
    try:
        return bool(pil_features.check("webp"))
    except Exception:
        return False


def compute_source_fingerprint(source_path: Path) -> Tuple[str, os.stat_result]:
    """
    Calcula fingerprint leve de 12 chars hex a partir de (st_mtime_ns, st_size, st_ino).
    Evita ler o arquivo inteiro em disco no caminho quente de cache hit.
    """
    st = source_path.stat()
    raw = f"{st.st_mtime_ns}:{st.st_size}:{st.st_ino}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12], st


def _preferred_format() -> Tuple[str, str]:
    if supports_webp():
        return ("webp", "image/webp")
    return ("jpg", "image/jpeg")


def get_thumbnail_target_path(
    book_id: str,
    fingerprint: str,
    variant: str,
    ext: Optional[str] = None,
) -> Tuple[Path, str]:
    """Resolve com segurança o caminho alvo do thumbnail dentro de `<COVERS_DIR>/thumbs/`."""
    if variant not in THUMB_VARIANTS:
        raise ValueError(f"Variante de thumbnail inválida: '{variant}'.")
    safe_id = _sanitize_book_id(book_id)
    if ext is None:
        ext, mime = _preferred_format()
    else:
        mime = "image/webp" if ext == "webp" else "image/jpeg"
    thumbs_dir = get_thumbs_dir().resolve()
    filename = f"{safe_id}_{fingerprint}_{variant}_{THUMB_ALGO_VERSION}.{ext}"
    candidate = (thumbs_dir / filename).resolve()
    if not candidate.is_relative_to(thumbs_dir):
        raise ValueError("Caminho de thumbnail fora do diretório autorizado.")
    return candidate, mime


def _render_thumbnail_bytes(source_path: Path, max_width: int, out_ext: str) -> Tuple[bytes, int, int]:
    """
    Decodifica a imagem original com segurança, corrige orientação EXIF, redimensiona
    sem ampliar (no-upscale), preserva transparência em WebP (ou compõe fundo branco em JPEG)
    e retorna (bytes_finais, largura_final, altura_final).
    """
    from io import BytesIO

    with Image.open(source_path) as img:
        w, h = img.size
        if w < 16 or h < 16 or w > MAX_THUMB_SOURCE_DIM or h > MAX_THUMB_SOURCE_DIM:
            raise ValueError(f"Dimensões fora dos limites seguros: {w}x{h}")
        if (w * h) > MAX_THUMB_SOURCE_PIXELS:
            raise ValueError(f"Imagem excede limite de pixels seguros ({w}x{h})")

        # Aplica orientação EXIF antes de calcular proporção
        img = ImageOps.exif_transpose(img)
        w, h = img.size

        # Reduz apenas se a largura original exceder max_width (nunca faz upscale)
        if w > max_width:
            new_h = max(1, int(round(h * (max_width / float(w)))))
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

        out_w, out_h = img.size
        buf = BytesIO()

        if out_ext == "webp":
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            converted = img.convert("RGBA" if has_alpha else "RGB")
            converted.save(
                buf,
                format="WEBP",
                quality=WEBP_QUALITY,
                method=WEBP_METHOD,
            )
        else:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                converted = bg
            else:
                converted = img.convert("RGB")
            converted.save(
                buf,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )

        return buf.getvalue(), out_w, out_h


def _write_atomic(target_path: Path, data: bytes) -> None:
    """Grava arquivo de forma atômica usando arquivo temporário no mesmo filesystem + os.replace."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = f".{target_path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
    tmp_path = target_path.parent / tmp_name
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def get_or_create_thumbnail(
    book_id: str,
    source_path: Path,
    variant: str = "card",
    force_ext: Optional[str] = None,
) -> Optional[Dict[str, object]]:
    """
    Retorna metadados e caminho do thumbnail pronto (fast-path sem leitura do original quando já existe).
    Caso ausente, gera sob demanda com lock por chave, semáforo global e gravação atômica.
    Em caso de falha ou imagem inválida/corrompida, retorna None para fallback seguro ao original.
    """
    if variant not in THUMB_VARIANTS:
        return None
    if not source_path or not source_path.is_file():
        return None

    try:
        fingerprint, _ = compute_source_fingerprint(source_path)
        out_ext = force_ext if force_ext in ("webp", "jpg") else _preferred_format()[0]
        target_path, mime = get_thumbnail_target_path(book_id, fingerprint, variant, ext=out_ext)
    except Exception as exc:
        logger.debug("Falha ao resolver caminho de thumbnail para %s: %s", book_id, exc)
        return None

    # Fast-path: thumbnail já existente e não vazio
    try:
        if target_path.is_file() and target_path.stat().st_size > 24:
            return {
                "file_path": target_path,
                "media_type": mime,
                "variant": variant,
                "fingerprint": fingerprint,
                "generated": False,
            }
    except OSError:
        pass

    lock_key = f"{book_id}:{fingerprint}:{variant}:{out_ext}"
    key_lock = _get_key_lock(lock_key)

    with key_lock:
        # Double-check após adquirir o lock por chave
        try:
            if target_path.is_file() and target_path.stat().st_size > 24:
                return {
                    "file_path": target_path,
                    "media_type": mime,
                    "variant": variant,
                    "fingerprint": fingerprint,
                    "generated": False,
                }
        except OSError:
            pass

        with _GENERATION_SEMAPHORE:
            try:
                thumb_bytes, out_w, out_h = _render_thumbnail_bytes(
                    source_path,
                    max_width=THUMB_VARIANTS[variant],
                    out_ext=out_ext,
                )
                if len(thumb_bytes) <= 24:
                    return None
                _write_atomic(target_path, thumb_bytes)
                return {
                    "file_path": target_path,
                    "media_type": mime,
                    "variant": variant,
                    "fingerprint": fingerprint,
                    "width": out_w,
                    "height": out_h,
                    "size_bytes": len(thumb_bytes),
                    "generated": True,
                }
            except Exception as exc:
                logger.warning("Falha ao gerar thumbnail %s para livro %s: %s", variant, book_id, exc)
                return None


def pregenerate_book_thumbnails(book_id: str, source_path: Path) -> Dict[str, bool]:
    """
    Pré-gera as variantes `card` e `detail` quando uma capa é extraída ou enviada.
    Nunca propaga exceção para não comprometer o upload/extração da capa original.
    """
    results: Dict[str, bool] = {}
    for variant in THUMB_VARIANTS:
        res = get_or_create_thumbnail(book_id, source_path, variant=variant)
        results[variant] = res is not None
    return results


def invalidate_book_thumbnails(book_id: str) -> int:
    """
    Remove exclusivamente os thumbnails derivados do livro em `<COVERS_DIR>/thumbs/`.
    Nunca toca nas capas originais em `COVERS_DIR`.
    """
    thumbs_dir = get_thumbs_dir()
    if not thumbs_dir.exists() or not thumbs_dir.is_dir():
        return 0
    try:
        safe_id = _sanitize_book_id(book_id)
    except ValueError:
        return 0

    removed = 0
    resolved_dir = thumbs_dir.resolve()
    for candidate in thumbs_dir.glob(f"{safe_id}_*"):
        try:
            res_cand = candidate.resolve()
            if res_cand.is_relative_to(resolved_dir) and res_cand.is_file():
                res_cand.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def cleanup_orphan_thumbnails(valid_book_ids: Set[str]) -> int:
    """
    Remove derivados cujos livros não existem mais no catálogo.
    Opera estritamente dentro de `<COVERS_DIR>/thumbs/`.
    """
    thumbs_dir = get_thumbs_dir()
    if not thumbs_dir.exists() or not thumbs_dir.is_dir():
        return 0
    safe_valid = {_sanitize_book_id(bid) for bid in valid_book_ids if bid}
    removed = 0
    resolved_dir = thumbs_dir.resolve()
    for candidate in thumbs_dir.iterdir():
        if not candidate.is_file():
            continue
        # Ignora arquivos temporários em gravação
        if candidate.name.startswith("."):
            continue
        prefix = candidate.name.split("_", 1)[0]
        if prefix not in safe_valid:
            try:
                res_cand = candidate.resolve()
                if res_cand.is_relative_to(resolved_dir):
                    res_cand.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
    return removed
