"""
Testes de integração REAL com Docker Engine + Docker Compose + Registry local efêmero
para `deployment/docker-auto-update.sh` (Fase 11.2.5).

- Executa automaticamente em ambientes com Docker Engine disponível (ex.: GitHub Actions `ubuntu-latest`).
- Pula graciosamente (skip) em máquinas locais sem Docker instalado, sem mascarar resultados.
- Utiliza nomes, porta, registry local (`127.0.0.1:<porta>`), compose e diretórios 100% isolados.
- Nunca utiliza `docker system prune` nem interfere em containers existentes.
"""

import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATER_SCRIPT = REPO_ROOT / "deployment" / "docker-auto-update.sh"


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        return False
    comp = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=10)
    return comp.returncode == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd, env=None, check=True, timeout=60):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=check, timeout=timeout)


@pytest.mark.skipif(not _docker_ready(), reason="Docker Engine / Docker Compose indisponível neste ambiente")
def test_v192_docker_real_integration_end_to_end(tmp_path):
    """
    Valida Cenários A, C, D, E, F, G e H contra Docker Engine + Docker Compose + Registry local reais.
    """
    run_id = uuid.uuid4().hex[:8]
    reg_port = _free_port()
    reg_name = f"lectoria-test-reg-{run_id}"
    svc_name = "lectoria-test"
    ctr_name = f"lectoria-test-ctr-{run_id}"
    image_ref = f"127.0.0.1:{reg_port}/lectoria-test:stable"

    state_dir = tmp_path / "state"
    compose_dir = tmp_path / "compose"
    build_dir = tmp_path / "build"
    state_dir.mkdir()
    compose_dir.mkdir()
    build_dir.mkdir()

    compose_file = compose_dir / "compose.yaml"
    compose_file.write_text(
        f"""services:
  {svc_name}:
    image: {image_ref}
    container_name: {ctr_name}
""",
        encoding="utf-8",
    )

    backup_marker = tmp_path / "backup_count.log"

    def make_env(backup_ok=True, img_ref=image_ref, timeout_sec=20):
        env = os.environ.copy()
        env.update(
            {
                "LECTORIA_COMPOSE_DIR": str(compose_dir),
                "LECTORIA_COMPOSE_FILE": str(compose_file),
                "LECTORIA_SERVICE_NAME": svc_name,
                "LECTORIA_CONTAINER_NAME": ctr_name,
                "LECTORIA_IMAGE_REF": img_ref,
                "LECTORIA_STATE_DIR": str(state_dir),
                "LECTORIA_BACKUP_CMD": f"echo bkp >> '{backup_marker}'; exit {0 if backup_ok else 1}",
                "LECTORIA_REQUIRE_BACKUP": "1",
                "LECTORIA_HEALTH_TIMEOUT_SEC": str(timeout_sec),
                "LECTORIA_HEALTH_INTERVAL_SEC": "1",
            }
        )
        return env

    def build_and_push(version_tag: str, healthy: bool = True):
        df = build_dir / f"Dockerfile.{version_tag}"
        hc_cmd = "true" if healthy else "false"
        df.write_text(
            f"""FROM alpine:3.20
RUN echo "{version_tag}" > /version.txt
HEALTHCHECK --interval=1s --timeout=1s --start-period=0s --retries=1 CMD {hc_cmd}
CMD ["sh", "-c", "trap 'exit 0' TERM INT; while true; do sleep 1; done"]
""",
            encoding="utf-8",
        )
        ver_ref = f"127.0.0.1:{reg_port}/lectoria-test:{version_tag}"
        _run(["docker", "build", "-f", str(df), "-t", ver_ref, str(build_dir)], timeout=120)
        _run(["docker", "tag", ver_ref, image_ref], timeout=30)
        _run(["docker", "push", image_ref], timeout=60)

    def get_ctr_id() -> str:
        return _run(["docker", "inspect", ctr_name, "--format", "{{.Id}}"]).stdout.strip()

    try:
        # 1. Sobe registry local efêmero em 127.0.0.1:<reg_port>
        _run(
            ["docker", "run", "-d", "--name", reg_name, "-p", f"127.0.0.1:{reg_port}:5000", "registry:2"],
            timeout=120,
        )
        # Aguarda registry pronto
        for _ in range(20):
            chk = subprocess.run(["curl", "-fsS", f"http://127.0.0.1:{reg_port}/v2/"], capture_output=True)
            if chk.returncode == 0:
                break
            time.sleep(0.5)

        # 2. Publica v1 saudável e sobe container inicial via Docker Compose real
        build_and_push("v1", healthy=True)
        _run(["docker", "compose", "-f", str(compose_file), "up", "-d"])
        for _ in range(20):
            st = _run(["docker", "inspect", ctr_name, "--format", "{{.State.Health.Status}}"]).stdout.strip()
            if st == "healthy":
                break
            time.sleep(0.5)
        assert st == "healthy"
        ctr_id_v1 = get_ctr_id()

        # Cenário A — Imagem idêntica: NO_EXECUTABLE_CHANGE, sem backup, mesmo Container ID
        res_a = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(), check=False)
        assert res_a.returncode == 0, res_a.stderr
        assert "NO_EXECUTABLE_CHANGE" in res_a.stdout
        assert get_ctr_id() == ctr_id_v1
        assert not backup_marker.exists()

        # Cenário G — Falha de rede / imagem indisponível: container anterior segue operacional
        res_g = _run(
            ["bash", str(UPDATER_SCRIPT)],
            env=make_env(img_ref="127.0.0.1:1/inexistente/lectoria:stable"),
            check=False,
        )
        assert res_g.returncode == 1
        assert "PULL_FAILED" in res_g.stderr
        assert get_ctr_id() == ctr_id_v1

        # Cenário D — Mudança real (v2) com falha no backup: deploy cancelado
        build_and_push("v2", healthy=True)
        res_d = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(backup_ok=False), check=False)
        assert res_d.returncode == 1
        assert "BACKUP_FAILED" in res_d.stderr
        assert get_ctr_id() == ctr_id_v1

        # Cenário C — Mudança executável real (v2) com backup OK: atualiza e converge
        backup_marker.unlink(missing_ok=True)
        res_c = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(backup_ok=True), check=False)
        assert res_c.returncode == 0, res_c.stderr
        assert "EXECUTABLE_CHANGE_DETECTED" in res_c.stdout
        assert "UPDATE_SUCCESS" in res_c.stdout
        ctr_id_v2 = get_ctr_id()
        assert ctr_id_v2 != ctr_id_v1
        assert backup_marker.exists() and len(backup_marker.read_text().splitlines()) == 1
        rollback_doc = json.loads((state_dir / "last-good.json").read_text(encoding="utf-8"))
        assert rollback_doc["previous_immutable_recoverable"] is True
        assert rollback_doc["previous_repo_digests"]

        # Cenário H — Segunda execução já atualizado: NO_EXECUTABLE_CHANGE, sem novo backup
        res_h = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(backup_ok=True), check=False)
        assert res_h.returncode == 0, res_h.stderr
        assert "NO_EXECUTABLE_CHANGE" in res_h.stdout
        assert get_ctr_id() == ctr_id_v2
        assert len(backup_marker.read_text().splitlines()) == 1

        # Cenário F — Execuções concorrentes: flock impede conflito (lock via fcntl sem filho órfão)
        lock_file = state_dir / "update.lock"
        with open(lock_file, "a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                res_f = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(), check=False)
                assert res_f.returncode == 0
                assert "CONCURRENT_RUN_SKIPPED" in res_f.stdout
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

        # Cenário E — Healthcheck propositalmente inválido (v3): timeout + arquivo blocked
        build_and_push("v3-unhealthy", healthy=False)
        res_e = _run(["bash", str(UPDATER_SCRIPT)], env=make_env(backup_ok=True, timeout_sec=4), check=False)
        assert res_e.returncode == 1
        assert (state_dir / "blocked").exists()
        assert "reason=HEALTHCHECK_TIMEOUT" in (state_dir / "blocked").read_text(encoding="utf-8")

    finally:
        # Limpeza cirúrgica apenas dos recursos descartáveis deste teste (sem prune global)
        subprocess.run(["docker", "compose", "-f", str(compose_file), "down"], capture_output=True)
        subprocess.run(["docker", "rm", "-f", ctr_name, reg_name], capture_output=True)
        for tag in ("stable", "v1", "v2", "v3-unhealthy"):
            subprocess.run(["docker", "rmi", "-f", f"127.0.0.1:{reg_port}/lectoria-test:{tag}"], capture_output=True)
