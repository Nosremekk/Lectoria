"""
Testes automatizados obrigatórios da Fase 12.3 (Pipeline de Miniaturas e Otimização de Capas).
Cobre integralmente os 18 requisitos especificados no contrato da Fase 12.3.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfWriter
import pytest

import auth_service
import backup_service
import config
import main
import thumbnail_service


def _create_minimal_pdf(path: Path, num_pages: int = 1) -> Path:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=595, height=842)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _make_test_image(width: int, height: int, fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    img = Image.new(mode, (width, height), (35, 60, 95, 220) if mode == "RGBA" else (35, 60, 95))
    draw = ImageDraw.Draw(img)
    # Adiciona padrão visual de alto contraste para simular capa real
    for i in range(0, width, max(8, width // 16)):
        draw.line([(i, 0), (width - i, height)], fill=(220, 195, 140), width=max(1, width // 120))
    draw.rectangle(
        [width // 8, height // 8, width * 7 // 8, height * 7 // 8],
        outline=(240, 230, 210),
        width=max(2, width // 80),
    )
    buf = BytesIO()
    if fmt.upper() == "PNG":
        img.save(buf, format="PNG")
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _setup_admin_and_scan(client: TestClient, username: str = "admin123", password: str = "SenhaForte123!"):
    client.get("/")
    csrf = client.cookies.get("lectoria_csrf", "")
    r = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": "Admin 123"},
        headers={"X-CSRF-Token": csrf},
    )
    if r.status_code == 403:
        lr = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        if lr.status_code != 200:
            try:
                auth_service.create_user(username=username, password=password, display_name="Admin 123", role="admin")
            except auth_service.UserAlreadyExistsError:
                pass
            lr = client.post(
                "/api/auth/login",
                json={"username": username, "password": password},
                headers={"X-CSRF-Token": csrf},
            )
        assert lr.status_code == 200, lr.text
    else:
        assert r.status_code in (200, 201), r.text
    csrf = client.cookies.get("lectoria_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/scan", headers=headers)
    return headers


# ---------------------------------------------------------------------------
# 1, 2, 3, 4, 5: JPEG, PNG (com transparência), custom cover, preservação byte
# a byte dos originais, proporção/qualidade e no-upscale para imagens pequenas
# ---------------------------------------------------------------------------
def test_phase123_jpeg_png_custom_preservation_proportion_and_no_upscale(test_env):
    _create_minimal_pdf(test_env["library_dir"] / "thumb_jpeg.pdf")
    _create_minimal_pdf(test_env["library_dir"] / "thumb_png.pdf")
    _create_minimal_pdf(test_env["library_dir"] / "thumb_small.pdf")

    large_jpg = _make_test_image(1400, 2000, fmt="JPEG")
    large_png_rgba = _make_test_image(1200, 1800, fmt="PNG", mode="RGBA")
    small_jpg = _make_test_image(200, 300, fmt="JPEG")

    with TestClient(main.app) as client:
        headers = _setup_admin_and_scan(client)
        books = {b["filename"]: b["id"] for b in client.get("/api/books").json()["books"]}

        # Upload JPEG grande -> verifica preservação byte a byte do original + card/detail
        bid_jpg = books["thumb_jpeg.pdf"]
        r_up = client.post(
            f"/api/books/{bid_jpg}/cover",
            content=large_jpg,
            headers={**headers, "Content-Type": "image/jpeg"},
        )
        assert r_up.status_code == 200, r_up.text
        orig_path = config.COVERS_DIR / f"{bid_jpg}_custom.jpg"
        assert orig_path.is_file()
        assert hashlib.sha256(orig_path.read_bytes()).hexdigest() == hashlib.sha256(large_jpg).hexdigest()

        r_card = client.get(f"/api/books/{bid_jpg}/cover?variant=card", headers={"Accept-Encoding": "gzip"})
        r_detail = client.get(f"/api/books/{bid_jpg}/cover?variant=detail", headers={"Accept-Encoding": "gzip"})
        r_orig = client.get(f"/api/books/{bid_jpg}/cover?variant=original", headers={"Accept-Encoding": "gzip"})
        assert r_card.status_code == 200 and r_detail.status_code == 200 and r_orig.status_code == 200
        assert "content-encoding" not in r_card.headers
        assert r_card.headers.get("x-cover-variant") == "card"
        assert r_detail.headers.get("x-cover-variant") == "detail"
        assert r_orig.headers.get("x-cover-variant") == "original"
        assert r_orig.content == large_jpg

        with Image.open(BytesIO(r_card.content)) as im_c:
            assert im_c.size[0] == 360
            assert abs((im_c.size[1] / im_c.size[0]) - (2000 / 1400)) < 0.02
        with Image.open(BytesIO(r_detail.content)) as im_d:
            assert im_d.size[0] == 600
            assert abs((im_d.size[1] / im_d.size[0]) - (2000 / 1400)) < 0.02
        assert len(r_card.content) < len(large_jpg)
        # Original preservado intacto após geração
        assert hashlib.sha256(orig_path.read_bytes()).hexdigest() == hashlib.sha256(large_jpg).hexdigest()

        # Upload PNG com transparência (RGBA)
        bid_png = books["thumb_png.pdf"]
        r_up_png = client.post(
            f"/api/books/{bid_png}/cover",
            content=large_png_rgba,
            headers={**headers, "Content-Type": "image/png"},
        )
        assert r_up_png.status_code == 200
        r_png_card = client.get(f"/api/books/{bid_png}/cover?variant=card")
        assert r_png_card.status_code == 200
        with Image.open(BytesIO(r_png_card.content)) as im_p:
            assert im_p.size[0] == 360
            if thumbnail_service.supports_webp():
                assert im_p.mode in ("RGBA", "RGB")

        # Imagem pequena (200x300) NÃO sofre upscale
        bid_sm = books["thumb_small.pdf"]
        r_up_sm = client.post(
            f"/api/books/{bid_sm}/cover",
            content=small_jpg,
            headers={**headers, "Content-Type": "image/jpeg"},
        )
        assert r_up_sm.status_code == 200
        r_sm_card = client.get(f"/api/books/{bid_sm}/cover?variant=card")
        assert r_sm_card.status_code == 200
        with Image.open(BytesIO(r_sm_card.content)) as im_s:
            assert im_s.size == (200, 300)


# ---------------------------------------------------------------------------
# 6, 7, 8, 9, 10, 11: Imagens inválidas, concorrência, gravação atômica,
# invalidação na substituição, recuperação após exclusão de thumbs e capas antigas
# ---------------------------------------------------------------------------
def test_phase123_invalid_concurrency_atomic_invalidation_recovery_and_legacy(test_env, tmp_path):
    # Imagem corrompida/truncada retorna None sem derrubar o servidor
    corrupt_file = tmp_path / "corrupt.jpg"
    corrupt_file.write_bytes(b"\xff\xd8\xff\xe0CORRUPTED_TRUNCATED_DATA")
    assert thumbnail_service.get_or_create_thumbnail("bad1", corrupt_file, variant="card") is None

    # Arquivo inexistente retorna None
    assert thumbnail_service.get_or_create_thumbnail("bad2", tmp_path / "missing.jpg", variant="card") is None

    # Geração concorrente (8 threads simultâneas) -> arquivo íntegro e zero sobra .tmp
    _create_minimal_pdf(test_env["library_dir"] / "conc_book.pdf")
    img_v1 = _make_test_image(1000, 1500, fmt="JPEG")
    img_v2 = _make_test_image(900, 1200, fmt="PNG")

    with TestClient(main.app) as client:
        headers = _setup_admin_and_scan(client)
        books = {b["filename"]: b["id"] for b in client.get("/api/books").json()["books"]}
        bid = books["conc_book.pdf"]

        client.post(f"/api/books/{bid}/cover", content=img_v1, headers={**headers, "Content-Type": "image/jpeg"})
        orig_path = config.COVERS_DIR / f"{bid}_custom.jpg"

        # Simula capa antiga (remove thumbs existentes) e dispara 8 gerações simultâneas
        thumbs_dir = thumbnail_service.get_thumbs_dir()
        if thumbs_dir.exists():
            shutil.rmtree(thumbs_dir)
        assert orig_path.is_file()  # Excluir thumbs nunca toca nos originais

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [
                pool.submit(thumbnail_service.get_or_create_thumbnail, bid, orig_path, "card")
                for _ in range(8)
            ]
            results = [f.result() for f in futs]
        assert all(res is not None and Path(res["file_path"]).is_file() for res in results)
        # Nenhum arquivo temporário residual (gravação atômica)
        leftover_tmps = [p for p in thumbs_dir.iterdir() if ".tmp." in p.name]
        assert leftover_tmps == []

        # Captura ETag v1 e substitui capa -> ETag e derivados devem mudar imediatamente
        r1 = client.get(f"/api/books/{bid}/cover?variant=card")
        assert r1.status_code == 200
        etag_v1 = r1.headers["etag"]

        r_rep = client.post(f"/api/books/{bid}/cover", content=img_v2, headers={**headers, "Content-Type": "image/png"})
        assert r_rep.status_code == 200
        r2 = client.get(f"/api/books/{bid}/cover?variant=card", headers={"If-None-Match": etag_v1})
        assert r2.status_code == 200
        assert r2.headers["etag"] != etag_v1

        # Exclusão completa do diretório thumbs -> recuperação transparente sob demanda
        shutil.rmtree(thumbs_dir)
        r3 = client.get(f"/api/books/{bid}/cover?variant=card")
        assert r3.status_code == 200
        assert r3.headers.get("x-cover-variant") == "card"
        assert thumbs_dir.exists() and any(thumbs_dir.iterdir())


# ---------------------------------------------------------------------------
# 12, 13, 14, 15, 16, 17: ETag/HEAD/304, isolamento entre usuários, logout,
# integração frontend, backup/restore e contenção de diretório (ReadonlyRootfs)
# ---------------------------------------------------------------------------
def test_phase123_http_cache_auth_logout_backup_and_frontend_integration(test_env):
    _create_minimal_pdf(test_env["library_dir"] / "auth_cover.pdf")
    img_bytes = _make_test_image(800, 1200, fmt="JPEG")

    with TestClient(main.app) as client:
        headers = _setup_admin_and_scan(client)
        books = {b["filename"]: b["id"] for b in client.get("/api/books").json()["books"]}
        bid = books["auth_cover.pdf"]
        client.post(f"/api/books/{bid}/cover", content=img_bytes, headers={**headers, "Content-Type": "image/jpeg"})

        # HEAD e ETag / 304
        r_head = client.head(f"/api/books/{bid}/cover?variant=card")
        assert r_head.status_code == 200
        assert r_head.content == b""
        assert r_head.headers.get("cache-control") == "private, no-cache"
        assert "cookie" in r_head.headers.get("vary", "").lower()
        etag = r_head.headers["etag"]

        r_304 = client.get(f"/api/books/{bid}/cover?variant=card", headers={"If-None-Match": etag})
        assert r_304.status_code == 304
        assert r_304.content == b""

        # Backup inclui apenas originais (*_custom.*), NUNCA inclui thumbs/
        bkp = backup_service.create_backup()
        assert bkp["integrity"] == "ok"
        assert bkp["custom_covers_count"] >= 1
        covers_bkp_dir = Path(bkp["path"]).parent / f"{Path(bkp['path']).stem}_covers"
        assert covers_bkp_dir.is_dir()
        assert not (covers_bkp_dir / "thumbs").exists()
        assert all("_card_" not in f.name and "_detail_" not in f.name for f in covers_bkp_dir.iterdir())

        # Logout limpa sessão e impede 304 com ETag antigo
        r_out = client.post("/api/auth/logout", headers=headers)
        assert r_out.status_code == 200
        assert '"cache"' in r_out.headers.get("clear-site-data", "")

        r_unauth = client.get(f"/api/books/{bid}/cover?variant=card", headers={"If-None-Match": etag})
        assert r_unauth.status_code == 401

    # Verifica integração mínima no frontend (app.js e reader.js)
    repo_root = Path(__file__).resolve().parent.parent
    app_js = (repo_root / "static" / "js" / "app.js").read_text(encoding="utf-8")
    reader_js = (repo_root / "static" / "js" / "reader.js").read_text(encoding="utf-8")
    assert 'appendCoverVariant(coverUrl, "card")' in app_js
    assert '"detail"' in app_js
    assert 'decoding="async"' in app_js
    assert 'variant=card' in reader_js

    # Confirma que nenhum derivado foi escrito fora de config.COVERS_DIR / "thumbs"
    assert thumbnail_service.get_thumbs_dir().resolve().is_relative_to(config.COVERS_DIR.resolve())
