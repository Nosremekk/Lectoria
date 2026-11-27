"""
Testes automatizados da Fase 12.5: validação integrada de segurança, fixação imutável de
representação, subprocessos sem preexec_fn, rejeição de comentários falsos /Linearized,
integridade ampliada, fila/recuperação, coordenação, isolamento RBAC/CSRF e inspeção real
de backups (exclusão comprovada de miniaturas e derivados).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

import auth_service
import backup_service
import config
import document_optimization_service as dos
import main


def _make_pdf(path: Path, num_pages: int = 2, pad_bytes: int = 2048, cropbox: tuple | None = None) -> Path:
    writer = PdfWriter()
    for _ in range(num_pages):
        page = writer.add_blank_page(width=595, height=842)
        if cropbox:
            page.cropbox.lower_left = (cropbox[0], cropbox[1])
            page.cropbox.upper_right = (cropbox[2], cropbox[3])
    if pad_bytes > 0:
        writer.add_metadata({"/Subject": "P" * pad_bytes})
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def _make_comment_spoofed_linearized_pdf(path: Path, num_pages: int = 2) -> Path:
    """PDF com comentário falso `% /Linearized 1 /L <size>` que DEVE ser rejeitado."""
    tmp = path.with_suffix(".raw.pdf")
    _make_pdf(tmp, num_pages=num_pages, pad_bytes=2048)
    body = tmp.read_bytes()
    tmp.unlink()
    header_end = body.find(b"\n") + 1
    prefix, rest = body[:header_end], body[header_end:]
    sample = f"% /Linearized 1 /L {0:010d} /E 0000010000\n".encode("ascii")
    total_len = len(prefix) + len(sample) + len(rest)
    comment = f"% /Linearized 1 /L {total_len:010d} /E 0000010000\n".encode("ascii")
    path.write_bytes(prefix + comment + rest)
    return path


def _make_dict_linearized_pdf(path: Path, num_pages: int = 2, pad_bytes: int = 2048) -> Path:
    tmp = path.with_suffix(".raw.pdf")
    _make_pdf(tmp, num_pages=num_pages, pad_bytes=pad_bytes)
    body = tmp.read_bytes()
    tmp.unlink()
    header_end = body.find(b"\n") + 1
    prefix, rest = body[:header_end], body[header_end:]
    tpl = "999 0 obj\n<< /Linearized 1 /L {length:010d} /E 0000010000 /N {pages} /T 0000010000 >>\nendobj\n"
    sample = tpl.format(length=0, pages=num_pages).encode("ascii")
    total_len = len(prefix) + len(sample) + len(rest)
    path.write_bytes(prefix + tpl.format(length=total_len, pages=num_pages).encode("ascii") + rest)
    return path


def _login_user(client: TestClient, username: str, password: str, role: str = "admin") -> dict:
    client.get("/")
    csrf = client.cookies.get("lectoria_csrf", "")
    r = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": username},
        headers={"X-CSRF-Token": csrf},
    )
    if r.status_code == 403:
        try:
            auth_service.create_user(username=username, password=password, display_name=username, role=role)
        except auth_service.UserAlreadyExistsError:
            pass
        lr = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        assert lr.status_code == 200, lr.text
    csrf = client.cookies.get("lectoria_csrf", csrf)
    return {"X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# 1. Rejeição de comentário falso /Linearized e ausência de preexec_fn
# ---------------------------------------------------------------------------
def test_phase125_rejects_fake_linearized_comment_and_no_preexec_fn(tmp_path):
    # Código do serviço não deve usar preexec_fn (risco de deadlock fork/exec em multithread)
    src = (Path(__file__).resolve().parents[1] / "document_optimization_service.py").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#") and "preexec_fn=" in ln]
    assert code_lines == [], f"Encontrado preexec_fn= em document_optimization_service.py: {code_lines}"

    # Launcher bounded_exec.py funciona em subprocesso real
    proc = dos._run_bounded_subprocess(
        [sys.executable, "-c", "print('bounded_ok')"],
        timeout_s=10,
        mem_limit_mb=512,
    )
    assert proc.returncode == 0
    assert "bounded_ok" in (proc.stdout or "")

    # PDF com comentário falso `% /Linearized 1 /L <size>` NÃO pode ser aceito como linearizado
    spoof = _make_comment_spoofed_linearized_pdf(tmp_path / "spoof.pdf", num_pages=2)
    cfg = dict(dos.DEFAULT_CONFIG, min_document_mb=0.0, max_document_mb=100.0)
    with patch.object(dos, "check_pdf_linearization_with_qpdf", return_value=None):
        elig = dos.analyze_pdf_eligibility(spoof, cfg=cfg)
        assert elig["eligible"] is True
        assert elig["state"] == "pending"

        orig = _make_pdf(tmp_path / "orig.pdf", num_pages=2, pad_bytes=2048)
        with patch.object(dos, "_run_bounded_subprocess", return_value=subprocess.CompletedProcess([], 0, "", "")):
            ok, reason = dos._validate_pdf_derivative(orig, spoof, "/usr/bin/qpdf")
            assert ok is False
            assert reason == "derivative_not_linearized"


# ---------------------------------------------------------------------------
# 2. Integridade ampliada (CropBox) e recuperação pós-reinício / corrupção state.json
# ---------------------------------------------------------------------------
def test_phase125_integrity_cropbox_recovery_and_corrupt_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

    orig = _make_pdf(tmp_path / "c_orig.pdf", num_pages=2, pad_bytes=2048, cropbox=(0, 0, 500, 700))
    deriv_bad_crop = _make_dict_linearized_pdf(tmp_path / "c_bad.pdf", num_pages=2, pad_bytes=2048)
    with (
        patch.object(dos, "_run_bounded_subprocess", return_value=subprocess.CompletedProcess([], 0, "", "")),
        patch.object(dos, "check_pdf_linearization_with_qpdf", return_value=True),
    ):
        ok, reason = dos._validate_pdf_derivative(orig, deriv_bad_crop, "/usr/bin/qpdf")
        assert ok is False
        assert "cropbox_mismatch" in reason

    # Recuperação após reinício: itens presos em processing voltam a pending e .tmp.* são limpos
    pdf_dir = dos.get_optimized_pdf_dir()
    pdf_dir.mkdir(parents=True, exist_ok=True)
    orphan = pdf_dir / ".book1_abc_lin_v1.pdf.tmp.123.456.dead"
    orphan.write_bytes(b"partial")
    dos._write_json_atomic(
        dos.get_state_file_path(),
        {
            "version": 1,
            "config": dict(dos.DEFAULT_CONFIG),
            "items": {"b1": {"book_id": "b1", "state": "processing", "reason": "qpdf_linearizing"}},
        },
    )
    rec = dos.recover_on_startup()
    assert rec["cleaned_tmp"] == 1
    assert rec["recovered_items"] == 1
    assert not orphan.exists()
    with dos._STATE_LOCK:
        st = dos._load_state_unlocked()
        assert st["items"]["b1"]["state"] == "pending"

    # Recuperação de state.json corrompido
    dos.get_state_file_path().write_text("{CORRUPT JSON", encoding="utf-8")
    cfg_after = dos.get_config()
    assert cfg_after["mode"] == "disabled"
    assert cfg_after["enabled"] is False


# ---------------------------------------------------------------------------
# 3. Todos os 9 cenários de fixação imutável da representação + RBAC/CSRF
# ---------------------------------------------------------------------------
def test_phase125_pinned_representation_all_9_scenarios_and_security(test_env, monkeypatch):
    monkeypatch.setattr(dos, "_PINNED_SESSIONS", {})
    monkeypatch.setattr(dos, "_ACTIVE_FILES", set())

    books_dir = test_env["library_dir"]
    books_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = _make_pdf(books_dir / "pinned_book.pdf", num_pages=2, pad_bytes=3072)
    orig_bytes = pdf_path.read_bytes()

    with TestClient(main.app) as client:
        admin_headers = _login_user(client, "admin125", "SenhaForte125!", role="admin")
        client.post("/api/scan", headers=admin_headers)
        books = client.get("/api/books").json()["books"]
        book_id = next(b["id"] for b in books if b.get("filename") == "pinned_book.pdf")

        # Salva progresso e anotação antes da otimização para garantir preservação
        p_res = client.post(
            f"/api/books/{book_id}/progress",
            json={"current_page": 2, "total_pages": 2},
            headers=admin_headers,
        )
        assert p_res.status_code == 200

        # Cenário 1: Abrir original -> publicar derivado durante leitura -> URL pinada continua no original
        rep1 = client.get(f"/api/books/{book_id}/reader-representation").json()
        assert rep1["representation"] == "original"
        assert rep1["content_length"] == len(orig_bytes)

        # Habilita e publica derivado enquanto rep1 está aberta
        client.put(
            "/api/admin/optimization/config",
            json={
                "mode": "adaptive",
                "adaptive_use_window": True,
                "window_start": "01:00",
                "window_end": "05:00",
                "timezone": "America/Sao_Paulo",
                "max_storage_mb": 9216,
                "min_document_mb": 0.0,
            },
            headers=admin_headers,
        )

        def _fake_qpdf(cmd, timeout_s=30, mem_limit_mb=384, track_for_cancel=False):
            if "--linearize" in cmd:
                _make_dict_linearized_pdf(Path(cmd[-1]), num_pages=2, pad_bytes=4096)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch.object(dos, "get_qpdf_info", return_value={"available": True, "path": "/usr/bin/qpdf", "version": "qpdf 12"}),
            patch.object(dos, "_run_bounded_subprocess", side_effect=_fake_qpdf),
            patch.object(dos, "check_pdf_linearization_with_qpdf", return_value=True),
        ):
            opt_res = client.post(f"/api/admin/optimization/books/{book_id}", headers=admin_headers)
            assert opt_res.status_code == 200
            assert opt_res.json()["result"]["state"] == "optimized"

        # rep1 DEVE continuar servindo original (nunca troca silenciosamente para derivado)
        r1 = client.get(rep1["file_url"], headers={"Range": "bytes=0-127"})
        assert r1.status_code == 206
        assert r1.headers.get("X-Document-Representation") == "original"
        assert r1.content == orig_bytes[:128]

        # Agora abre nova sessão -> recebe derivado otimizado pinado
        rep2 = client.get(f"/api/books/{book_id}/reader-representation").json()
        assert rep2["representation"] == "optimized"
        assert rep2["content_length"] != len(orig_bytes)

        # Cenário 2: Cleanup administrativo com derivado em uso -> arquivo em uso é PROTEGIDO
        cl = client.post("/api/admin/optimization/cleanup", headers=admin_headers).json()
        assert cl["skipped_in_use"] >= 1
        r2 = client.get(rep2["file_url"], headers={"Range": "bytes=0-127"})
        assert r2.status_code == 206
        assert r2.headers.get("X-Document-Representation") == "optimized"

        # Cenário 3: LRU com derivado em uso -> protegido contra evicção
        cfg_tiny = dict(dos.get_config(), max_storage_mb=1)
        dos._enforce_storage_budget(1024 * 1024, cfg_tiny)
        r3 = client.get(rep2["file_url"], headers={"Range": "bytes=0-63"})
        assert r3.status_code == 206

        # Cenário 5: Desativar otimização durante leitura -> rep2 continua estável no derivado
        client.put("/api/admin/optimization/config", json={"mode": "disabled"}, headers=admin_headers)
        r5 = client.get(rep2["file_url"], headers={"Range": "bytes=64-127"})
        assert r5.status_code == 206
        assert r5.headers.get("X-Document-Representation") == "optimized"

        # Cenários 6 e 7: Range sem If-Range e com If-Range (matching vs mismatching)
        r6 = client.get(rep2["file_url"], headers={"Range": "bytes=0-99"})
        assert r6.status_code == 206
        etag2 = r6.headers["ETag"]
        r7_match = client.get(rep2["file_url"], headers={"Range": "bytes=0-99", "If-Range": etag2})
        assert r7_match.status_code == 206
        r7_miss = client.get(rep2["file_url"], headers={"Range": "bytes=0-99", "If-Range": '"stale-etag"'})
        assert r7_miss.status_code == 200

        # Cenário 8: Requisições Range concorrentes na mesma sessão pinada
        def _fetch_chunk(start: int):
            return client.get(rep2["file_url"], headers={"Range": f"bytes={start}-{start + 63}"})

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_fetch_chunk, i * 64) for i in range(4)]
            results = [f.result() for f in futs]
        assert all(res.status_code == 206 and res.headers.get("X-Document-Representation") == "optimized" for res in results)

        # Download convencional continua 100% byte a byte idêntico ao original
        dl = client.get(f"/api/books/{book_id}/download")
        assert dl.status_code == 200
        assert dl.content == orig_bytes

        # Progresso salvo preservado intacto
        pg = client.get(f"/api/books/{book_id}/progress").json()
        assert pg["current_page"] == 2

        # Cenário 4: Substituir arquivo original durante leitura -> HTTP 409 Conflict (nunca mistura bytes!)
        _make_pdf(pdf_path, num_pages=2, pad_bytes=5120)
        os.utime(pdf_path, (time.time() + 10, time.time() + 10))
        r4 = client.get(rep2["file_url"], headers={"Range": "bytes=0-63"})
        assert r4.status_code == 409
        assert r4.headers.get("X-Document-Representation-Stale") == "1"

        # Cenário 9: Logout revoga sessões pinadas + usuário comum recebe 403 em admin endpoints e sem CSRF
        rep_before_logout = client.get(f"/api/books/{book_id}/reader-representation").json()
        client.post("/api/auth/logout", headers=admin_headers)
        assert rep_before_logout["rep_id"] not in dos._PINNED_SESSIONS

        # Login como usuário comum (role="user")
        user_headers = _login_user(client, "reader125", "SenhaReader125!", role="user")
        assert client.get("/api/admin/optimization/status").status_code == 403
        assert client.put("/api/admin/optimization/config", json={"mode": "immediate"}, headers=user_headers).status_code == 403
        # Sem CSRF -> 403
        assert client.post("/api/admin/optimization/pause").status_code == 403


# ---------------------------------------------------------------------------
# 4. Inspeção real de backup: exclui miniaturas e derivados otimizados
# ---------------------------------------------------------------------------
def test_phase125_backup_excludes_thumbnails_and_optimized_derivatives(test_env):
    thumbs_dir = config.COVERS_DIR / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / "book1_card.webp").write_bytes(b"RIFF....WEBP")
    (config.COVERS_DIR / "book1_custom.jpg").write_bytes(b"\xff\xd8\xff\xe0CUSTOM")

    opt_dir = dos.get_optimized_pdf_dir()
    opt_dir.mkdir(parents=True, exist_ok=True)
    (opt_dir / "book1_fp_lin_v1.pdf").write_bytes(b"%PDF-1.7 DERIVATIVE")

    bkp = backup_service.create_backup()
    bkp_dir = Path(bkp["path"]) if "path" in bkp else (config.BACKUPS_DIR / bkp["filename"])
    if bkp_dir.is_file():
        bkp_dir = bkp_dir.parent
    assert bkp_dir.is_dir()

    # Nenhum arquivo de optimized/ no diretório de backup
    all_bkp_files = [p.name for p in bkp_dir.rglob("*") if p.is_file()]
    assert not any("lin_v1" in name or "optimized" in name for name in all_bkp_files)

    # Inspeciona membros reais de covers.tar.gz: inclui *_custom.jpg, EXCLUI thumbs/*.webp e optimized/*
    covers_tar = bkp_dir / "covers.tar.gz"
    if covers_tar.is_file():
        with tarfile.open(covers_tar, "r:gz") as tf:
            names = tf.getnames()
        assert any("book1_custom.jpg" in n for n in names)
        assert not any("thumbs" in n or ".webp" in n or "optimized" in n for n in names)
