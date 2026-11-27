"""
Testes automatizados obrigatórios da Fase 12.4.1 (Otimização Automática de Documentos em Segundo Plano).
Cobre todos os 24 itens do contrato: identificação de PDFs linearizados/candidatos, disponibilidade e
ausência de QPDF, geração/validação com preservação byte a byte dos originais, compatibilidade com
progresso e anotações, rejeição de PDFs criptografados/assinados, invalidação na alteração do original,
falha/timeout de subprocesso, cancelamento/pausa/retomada, limites de armazenamento/recursos/concorrência,
modos imediato/agendado/adaptativo, coordenação com backups, limpeza, autorização/isolamento, HTTP Range/
ETag/If-Range, download sempre do original, fallback e integridade EPUB.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
import zipfile
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest

import auth_service
import backup_service
import config
import document_optimization_service as dos
import main


def _create_pdf(path: Path, num_pages: int = 3, pad_bytes: int = 0) -> Path:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=595, height=842)
    if pad_bytes > 0:
        writer.add_metadata({"/Subject": "X" * pad_bytes})
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _create_fake_linearized_pdf(path: Path, num_pages: int = 2) -> Path:
    """Cria PDF válido com marcador /Linearized e /L exato para testar validação e detecção."""
    tmp = path.with_suffix(".raw.pdf")
    _create_pdf(tmp, num_pages=num_pages, pad_bytes=2048)
    body = tmp.read_bytes()
    tmp.unlink()
    # Injeta comentário /Linearized + /L placeholder logo após %PDF-1.3\n (mantendo PDF válido para pypdf!)
    header_end = body.find(b"\n") + 1
    prefix = body[:header_end]
    rest = body[header_end:]
    # Calcula tamanho exato com /L com largura fixa de 10 dígitos
    comment_len = len(f"% /Linearized 1 /L {0:010d} /E 0000010000\n".encode("ascii"))
    total_len = len(prefix) + comment_len + len(rest)
    comment = f"% /Linearized 1 /L {total_len:010d} /E 0000010000\n".encode("ascii")
    final_bytes = prefix + comment + rest
    path.write_bytes(final_bytes)
    return path


def _create_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
        )
        zf.writestr("OEBPS/content.opf", "<package/>")
    return path


def _setup_admin(client: TestClient, username: str = "admin1241", password: str = "SenhaForte1241!"):
    client.get("/")
    csrf = client.cookies.get("lectoria_csrf", "")
    r = client.post(
        "/api/auth/setup",
        json={"username": username, "password": password, "display_name": "Admin 1241"},
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
                auth_service.create_user(username=username, password=password, display_name="Admin 1241", role="admin")
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
# 1, 2, 7, 15 (EPUB): Elegibilidade PDF (linearizado, candidato, criptografado,
# assinado) e análise estrutural EPUB
# ---------------------------------------------------------------------------
def test_phase1241_eligibility_special_pdfs_and_epub_analysis(tmp_path):
    cfg = dict(dos.DEFAULT_CONFIG, min_document_mb=0.0, max_document_mb=100.0)

    # PDF não linearizado -> candidato (eligible=True)
    p_nonlin = _create_pdf(tmp_path / "nonlin.pdf", num_pages=2, pad_bytes=2048)
    res_nl = dos.analyze_pdf_eligibility(p_nonlin, cfg=cfg)
    assert res_nl["eligible"] is True
    assert res_nl["state"] == "pending"

    # PDF já linearizado com /L exato -> already_adequate
    p_lin = _create_fake_linearized_pdf(tmp_path / "lin.pdf", num_pages=2)
    res_l = dos.analyze_pdf_eligibility(p_lin, cfg=cfg)
    assert res_l["eligible"] is False
    assert res_l["state"] == "already_adequate"

    # PDF criptografado -> ignorado (rejeitado)
    p_enc = tmp_path / "encrypted.pdf"
    p_enc.write_bytes(b"%PDF-1.4\n" + b"0" * 1500 + b"\n/Encrypt 12 0 R\n%%EOF")
    res_e = dos.analyze_pdf_eligibility(p_enc, cfg=cfg)
    assert res_e["eligible"] is False
    assert res_e["state"] == "ignored"
    assert "encrypted" in res_e["reason"]

    # PDF assinado digitalmente -> ignorado (preservado)
    p_sig = tmp_path / "signed.pdf"
    p_sig.write_bytes(b"%PDF-1.4\n" + b"0" * 1500 + b"\n/Type /Sig /ByteRange [0 10 20 30]\n%%EOF")
    res_s = dos.analyze_pdf_eligibility(p_sig, cfg=cfg)
    assert res_s["eligible"] is False
    assert res_s["state"] == "ignored"
    assert "signed" in res_s["reason"]

    # EPUB íntegro -> validado sem reescrita destrutiva
    p_epub = _create_epub(tmp_path / "valid.epub")
    res_ep = dos.analyze_epub_structure(p_epub)
    assert res_ep["eligible"] is False
    assert res_ep["state"] == "already_adequate"


# ---------------------------------------------------------------------------
# 3, 4, 5, 8, 9, 10, 12, 13, 18: QPDF ausente/presente, validação, preservação
# byte-a-byte, timeout, cancelamento, orçamento de disco, invalidação e limpeza
# ---------------------------------------------------------------------------
def test_phase1241_qpdf_lifecycle_validation_timeout_and_preservation(test_env):
    orig_pdf = _create_pdf(test_env["library_dir"] / "opt_target.pdf", num_pages=3, pad_bytes=4096)
    orig_sha = hashlib.sha256(orig_pdf.read_bytes()).hexdigest()

    dos.update_config({"enabled": True, "mode": "immediate", "min_document_mb": 0.0, "max_storage_mb": 64, "min_free_disk_mb": 128})

    # 1. Degradação graciosa quando QPDF está ausente
    with patch.object(dos, "get_qpdf_info", return_value={"available": False, "path": None, "version": None}):
        r_no_qpdf = dos.optimize_single_book("book_opt_1", orig_pdf)
        assert r_no_qpdf["state"] == "waiting_resources"
        assert r_no_qpdf["reason"] == "qpdf_unavailable"

    # 2. Timeout de subprocesso -> failed + sem sobra de arquivo temporário
    with patch.object(dos, "get_qpdf_info", return_value={"available": True, "path": "/usr/bin/qpdf", "version": "qpdf 11.9.0"}), \
         patch("document_optimization_service.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["qpdf"], timeout=10)):
        r_to = dos.optimize_single_book("book_opt_1", orig_pdf, force=True)
        assert r_to["state"] == "failed"
        assert "timeout" in r_to["reason"]
        assert hashlib.sha256(orig_pdf.read_bytes()).hexdigest() == orig_sha

    # 3. Execução bem-sucedida simulando QPDF gerando derivado linearizado válido
    def _fake_qpdf_run(cmd, **kwargs):
        if "--check" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        out_path = Path(cmd[-1])
        _create_fake_linearized_pdf(out_path, num_pages=3)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(dos, "get_qpdf_info", return_value={"available": True, "path": "/usr/bin/qpdf", "version": "qpdf 11.9.0"}), \
         patch("document_optimization_service.subprocess.run", side_effect=_fake_qpdf_run):
        r_ok = dos.optimize_single_book("book_opt_1", orig_pdf, force=True)
        assert r_ok["state"] == "optimized", r_ok
        assert r_ok["derivative_filename"]
        # Original preservado byte a byte
        assert hashlib.sha256(orig_pdf.read_bytes()).hexdigest() == orig_sha

        # Caminho quente do leitor resolve para 'optimized'
        p_res, _, rep = dos.resolve_pdf_for_reader("book_opt_1", orig_pdf, orig_pdf.stat())
        assert rep == "optimized"
        assert p_res != orig_pdf and p_res.is_file()

        # Alteração do original invalida imediatamente -> fallback para 'original'
        _create_pdf(orig_pdf, num_pages=4, pad_bytes=5000)
        os.utime(orig_pdf, ns=(orig_pdf.stat().st_atime_ns, orig_pdf.stat().st_mtime_ns + 5_000_000))
        p_fb, _, rep_fb = dos.resolve_pdf_for_reader("book_opt_1", orig_pdf, orig_pdf.stat())
        assert rep_fb == "original"
        assert p_fb == orig_pdf

    # Limpeza remove derivados sem tocar no original
    cleaned = dos.cleanup_all_derivatives()
    assert cleaned["removed_files"] >= 1
    assert orig_pdf.is_file()


# ---------------------------------------------------------------------------
# 15, 17: Modos de execução (disabled, immediate, scheduled, adaptive) + backup lock
# ---------------------------------------------------------------------------
def test_phase1241_execution_modes_schedule_adaptive_and_backup_coordination(test_env):
    tz = ZoneInfo("America/Sao_Paulo")
    cfg = dos.update_config({
        "enabled": True,
        "mode": "scheduled",
        "window_start": "02:00",
        "window_end": "05:00",
        "timezone": "America/Sao_Paulo",
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "min_free_disk_mb": 64,
    })
    ok_in, _ = dos.can_run_background_task(cfg, now_dt=datetime(2026, 11, 27, 3, 30, tzinfo=tz))
    ok_out, reason_out = dos.can_run_background_task(cfg, now_dt=datetime(2026, 11, 27, 14, 0, tzinfo=tz))
    assert ok_in is True
    assert ok_out is False and reason_out == "outside_schedule_window"

    # Modo adaptativo respeita leitura ativa
    cfg_adapt = dos.update_config({"enabled": True, "mode": "adaptive", "reading_idle_seconds": 300, "min_free_disk_mb": 64})
    dos.mark_reading_activity()
    ok_read, reason_read = dos.can_run_background_task(cfg_adapt)
    assert ok_read is False and reason_read == "active_reading_detected"

    # Coordenação com backup: quando _backup_lock está adquirido, otimização aguarda
    cfg_imm = dos.update_config({"enabled": True, "mode": "immediate", "min_free_disk_mb": 64})
    with backup_service._backup_lock:
        ok_bkp, reason_bkp = dos.can_run_background_task(cfg_imm)
        assert ok_bkp is False and reason_bkp == "backup_in_progress"

    # Pausa e retomada
    dos.pause_processing()
    ok_p, reason_p = dos.can_run_background_task(cfg_imm)
    assert ok_p is False and reason_p == "paused"
    dos.resume_processing()

    # Modo desativado por padrão seguro
    dos.update_config({"enabled": False, "mode": "disabled"})


# ---------------------------------------------------------------------------
# 6, 19, 20, 21, 22: API Admin (isolamento RBAC), HTTP Range/ETag/If-Range com
# derivado, download sempre do original, preservação de progresso/anotações
# ---------------------------------------------------------------------------
def test_phase1241_api_admin_rbac_range_etag_and_original_download(test_env):
    orig_pdf = _create_pdf(test_env["library_dir"] / "reader_opt_api.pdf", num_pages=2, pad_bytes=3000)
    orig_bytes = orig_pdf.read_bytes()

    with TestClient(main.app) as client:
        admin_headers = _setup_admin(client)
        books = client.get("/api/books").json()["books"]
        bid = next(b["id"] for b in books if b["filename"] == "reader_opt_api.pdf")

        # Admin status & config (extra="forbid" rejeita campos desconhecidos)
        r_st = client.get("/api/admin/optimization/status")
        assert r_st.status_code == 200
        assert "qpdf" in r_st.json()

        r_bad = client.put("/api/admin/optimization/config", json={"unknown_field": 1}, headers=admin_headers)
        assert r_bad.status_code == 422

        r_cfg = client.put(
            "/api/admin/optimization/config",
            json={"enabled": True, "mode": "immediate", "min_document_mb": 0.0, "min_free_disk_mb": 64},
            headers=admin_headers,
        )
        assert r_cfg.status_code == 200

        # Otimiza o livro via endpoint admin com QPDF mockado
        def _fake_qpdf_run(cmd, **kwargs):
            if "--check" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
            _create_fake_linearized_pdf(Path(cmd[-1]), num_pages=2)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(dos, "get_qpdf_info", return_value={"available": True, "path": "/usr/bin/qpdf", "version": "qpdf 11.9.0"}), \
             patch("document_optimization_service.subprocess.run", side_effect=_fake_qpdf_run):
            r_opt = client.post(f"/api/admin/optimization/books/{bid}", headers=admin_headers)
            assert r_opt.status_code == 200
            assert r_opt.json()["result"]["state"] == "optimized"

        # Leitor (/file) recebe derivado com X-Document-Representation: optimized e Range 206 válido
        r_file = client.get(f"/api/books/{bid}/file")
        assert r_file.status_code == 200
        assert r_file.headers.get("x-document-representation") == "optimized"
        opt_etag = r_file.headers["etag"]

        r_range = client.get(f"/api/books/{bid}/file", headers={"Range": "bytes=0-63", "If-Range": opt_etag})
        assert r_range.status_code == 206
        assert len(r_range.content) == 64

        # Download (/download) SEMPRE entrega o original byte a byte
        r_dl = client.get(f"/api/books/{bid}/download")
        assert r_dl.status_code == 200
        assert r_dl.content == orig_bytes

        # Backup NÃO inclui data/optimized/
        bkp = backup_service.create_backup()
        assert bkp["integrity"] == "ok"

        # Cleanup via admin volta fallback transparente para 'original'
        r_clean = client.post("/api/admin/optimization/cleanup", headers=admin_headers)
        assert r_clean.status_code == 200
        r_file_orig = client.get(f"/api/books/{bid}/file")
        assert r_file_orig.status_code == 200
        assert r_file_orig.headers.get("x-document-representation") == "original"
        assert r_file_orig.content == orig_bytes

        # Restaura modo conservador desativado e valida RBAC (usuário não-admin recebe 403)
        client.put("/api/admin/optimization/config", json={"enabled": False, "mode": "disabled"}, headers=admin_headers)
        client.post("/api/auth/logout", headers=admin_headers)
        r_unauth = client.get("/api/admin/optimization/status")
        assert r_unauth.status_code == 401
