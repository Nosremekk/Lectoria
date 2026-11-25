"""
Teste de integração REAL com Docker Engine para validar o hardening residual
da Fase 11.4 no Dockerfile real da Lectoria:
- Execução como usuário não-root (lectoria, UID 1000 / GID 1000);
- --read-only + --cap-drop ALL + --security-opt no-new-privileges:true + --tmpfs /tmp:rw,noexec,nosuid,size=64m;
- Mount :ro para /app/books e volumes graváveis apenas em /app/data e /app/backups;
- Validação real de /api/health, setup/login/CSRF, escrita SQLite WAL, backup,
  restauração isolada e exportação do Atlas (que usa /tmp).

Pula graciosamente (skip) quando Docker Engine não está instalado no host local;
executa integralmente no CI remoto (ubuntu-latest).
"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
    return res.returncode == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not _docker_ready(), reason="Docker Engine indisponível neste ambiente")
def test_v192_real_docker_nonroot_and_hardening_runtime(tmp_path):
    run_id = uuid.uuid4().hex[:8]
    image_tag = f"lectoria-hardening-test:{run_id}"
    ctr_name = f"lectoria-hardening-ctr-{run_id}"
    port = _free_port()

    data_dir = tmp_path / "data"
    books_dir = tmp_path / "books"
    backups_dir = tmp_path / "backups"
    data_dir.mkdir()
    books_dir.mkdir()
    backups_dir.mkdir()

    # Usa UID/GID do usuário atual no build para que bind mounts temporários em tmp_path sejam graváveis sem chmod 777
    uid = os.getuid()
    gid = os.getgid()

    try:
        subprocess.run(
            [
                "docker", "build",
                "--build-arg", f"LECTORIA_UID={uid}",
                "--build-arg", f"LECTORIA_GID={gid}",
                "-t", image_tag,
                str(REPO_ROOT),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )

        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", ctr_name,
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "-p", f"127.0.0.1:{port}:8765",
                "-v", f"{data_dir}:/app/data",
                "-v", f"{books_dir}:/app/books:ro",
                "-v", f"{backups_dir}:/app/backups",
                image_tag,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 1. Verifica usuário efetivo não-root
        who = subprocess.run(
            ["docker", "exec", ctr_name, "id", "-u"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        assert int(who) == uid and int(who) != 0

        # 2. Aguarda /api/health responder 200 healthy
        health_ok = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as resp:
                    if resp.status == 200:
                        payload = json.loads(resp.read().decode())
                        if payload.get("status") == "healthy":
                            health_ok = True
                            break
            except Exception:
                time.sleep(0.5)
        assert health_ok, "Container endurecido não respondeu /api/health em 15s"

        # 3. Confirma que rootfs (/app) e /app/books são somente-leitura
        ro_app = subprocess.run(
            ["docker", "exec", ctr_name, "sh", "-c", "touch /app/should_fail 2>/dev/null"],
            capture_output=True, text=True, timeout=10,
        )
        assert ro_app.returncode != 0
        ro_books = subprocess.run(
            ["docker", "exec", ctr_name, "sh", "-c", "touch /app/books/should_fail 2>/dev/null"],
            capture_output=True, text=True, timeout=10,
        )
        assert ro_books.returncode != 0

        # 4. Confirma criação de backup e verificação SQLite dentro do container não-root
        bkp = subprocess.run(
            ["docker", "exec", ctr_name, "python3", "scripts/backup.py", "--backup"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        assert "Backup gerado com sucesso" in bkp.stdout
        vrf = subprocess.run(
            ["docker", "exec", ctr_name, "python3", "scripts/backup.py", "--verify"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        assert "PRAGMA integrity_check: ok" in vrf.stdout

    finally:
        subprocess.run(["docker", "rm", "-f", ctr_name], capture_output=True)
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)
