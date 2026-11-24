"""
Suíte de testes automatizados isolados para o atualizador Docker/OCI
(`deployment/docker-auto-update.sh`) — Fase 11.2.4.

Utiliza mocks locais determinísticos de `docker`, `curl` e backup (sem tocar
containers reais nem acessar a VPS). Cobre todos os 18 cenários da tabela da
Seção 9, incluindo a reprodução exata do incidente de 2026-11-23.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATER_SCRIPT = REPO_ROOT / "deployment" / "docker-auto-update.sh"

# Identificadores reais do incidente de 2026-11-23
INCIDENT_OLD_INDEX = "sha256:36af447d240272bab0cefb97bc0972534b77c87fe98d0d3cd383de9b3e4d3982"
INCIDENT_NEW_INDEX = "sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253"
LAYER_A = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
LAYER_B = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
LAYER_C = "sha256:3333333333333333333333333333333333333333333333333333333333333333"


def _make_image_doc(
    image_id: str,
    layers=None,
    cmd=None,
    env=None,
    labels=None,
    repo_digests=None,
    created="2026-11-23T16:09:27Z",
):
    return {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "Created": created,
        "RepoDigests": repo_digests if repo_digests is not None else [f"ghcr.io/nosremekk/lectoria@{image_id}"],
        "RootFS": {"Type": "layers", "Layers": layers if layers is not None else [LAYER_A, LAYER_B]},
        "Config": {
            "Entrypoint": [],
            "Cmd": cmd if cmd is not None else ["uvicorn", "main:app", "--workers", "1"],
            "Env": env if env is not None else ["LECTORIA_PORT=8765"],
            "WorkingDir": "/app",
            "User": "",
            "ExposedPorts": {"8765/tcp": {}},
            "Healthcheck": {"Test": ["CMD", "python", "-c", "ok"]},
            "Labels": labels if labels is not None else {"org.opencontainers.image.title": "Lectoria"},
        },
    }


def _setup_mock_env(tmp_path: Path, scenario: dict):
    """Cria mock executável de docker/curl/backup e retorna (env, state_dir, calls_log)."""
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    compose_dir = tmp_path / "compose"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    compose_dir.mkdir(parents=True, exist_ok=True)

    compose_file = compose_dir / "compose.yaml"
    compose_file.write_text("services:\n  lectoria:\n    image: ghcr.io/nosremekk/lectoria:stable\n", encoding="utf-8")

    scenario_file = tmp_path / "scenario.json"
    calls_file = tmp_path / "calls.log"
    scenario_file.write_text(json.dumps(scenario), encoding="utf-8")

    # Mock docker em Python (sem tocar Docker real)
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(
        f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path

scenario_path = Path({str(scenario_file)!r})
calls_path = Path({str(calls_file)!r})
s = json.loads(scenario_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with calls_path.open("a", encoding="utf-8") as f:
    f.write("docker " + " ".join(args) + "\\n")

if args[:1] == ["pull"]:
    if s.get("pull_fail"):
        sys.stderr.write("Error response from daemon: network/registry error\\n")
        sys.exit(1)
    s["pulled"] = True
    scenario_path.write_text(json.dumps(s), encoding="utf-8")
    sys.exit(0)

if args[:2] == ["image", "inspect"]:
    ref = args[2]
    images = s.get("images", {{}})
    if ref not in images:
        sys.stderr.write(f"No such image: {{ref}}\\n")
        sys.exit(1)
    print(json.dumps([images[ref]]))
    sys.exit(0)

if args[:1] == ["inspect"]:
    # docker inspect <name> --format ...
    fmt = args[args.index("--format") + 1] if "--format" in args else ""
    c = s["container"]
    if c.get("missing"):
        sys.exit(1)
    if fmt == "{{{{.Image}}}}":
        print(c["image_id"])
    elif "State.Status" in fmt and "{{{{.Image}}}}" in fmt:
        print(f"{{c['status']}}|{{c['health']}}|{{c['image_id']}}")
    else:
        print(f"{{c['status']}}|{{c['health']}}")
    sys.exit(0)

if args[:1] == ["compose"]:
    if s.get("compose_fail"):
        sys.stderr.write("compose up failed\\n")
        sys.exit(1)
    if s.get("compose_updates_container", True):
        s["container"]["image_id"] = s.get("post_update_container_image_id", s["target_image_id"])
        s["container"]["status"] = s.get("post_update_status", "running")
        s["container"]["health"] = s.get("post_update_health", "healthy")
    scenario_path.write_text(json.dumps(s), encoding="utf-8")
    sys.exit(0)

sys.exit(0)
""",
        encoding="utf-8",
    )
    mock_docker.chmod(mock_docker.stat().st_mode | stat.S_IEXEC)

    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        f"""#!/usr/bin/env bash
exit {0 if scenario.get('curl_ok', True) else 1}
""",
        encoding="utf-8",
    )
    mock_curl.chmod(mock_curl.stat().st_mode | stat.S_IEXEC)

    backup_cmd = f"echo backup_ran >> {str(calls_file)!r}; exit {1 if scenario.get('backup_fail') else 0}"
    if scenario.get("omit_backup_cmd"):
        backup_cmd = ""

    env = os.environ.copy()
    env.update(
        {
            "LECTORIA_COMPOSE_DIR": str(compose_dir),
            "LECTORIA_COMPOSE_FILE": str(compose_file),
            "LECTORIA_SERVICE_NAME": "lectoria",
            "LECTORIA_CONTAINER_NAME": "lectoria",
            "LECTORIA_IMAGE_REF": scenario.get("image_ref", "ghcr.io/nosremekk/lectoria:stable"),
            "LECTORIA_STATE_DIR": str(state_dir),
            "LECTORIA_BACKUP_CMD": backup_cmd,
            "LECTORIA_REQUIRE_BACKUP": str(scenario.get("require_backup", "1")),
            "LECTORIA_HEALTH_TIMEOUT_SEC": str(scenario.get("health_timeout", "2")),
            "LECTORIA_HEALTH_INTERVAL_SEC": "1",
            "DOCKER_BIN": str(mock_docker),
            "CURL_BIN": str(mock_curl),
            "PYTHON_BIN": "python3",
        }
    )
    return env, state_dir, calls_file


def _run_updater(env):
    return subprocess.run(["bash", str(UPDATER_SCRIPT)], env=env, capture_output=True, text=True, timeout=15)


def test_updater_bash_syntax_and_selfhost_independence():
    """Valida sintaxe bash -n e ausência de referências hardcoded à VPS pessoal."""
    res = subprocess.run(["bash", "-n", str(UPDATER_SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    content = UPDATER_SCRIPT.read_text(encoding="utf-8")
    code_lines = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("nosreme.cloud", "tailf5c3ce", "100.82.105.34", "/srv/nosreme", "down -v"):
        assert forbidden not in code_lines


def test_incident_2026_11_23_oci_index_change_same_executable_no_deploy(tmp_path):
    """
    Reprodução exata do incidente de 2026-11-23 (19:48 UTC):
    OCI Index mudou (36af447d -> da4dca9c) por attestation de provenance,
    mas camadas/config executável são idênticos -> NO_EXECUTABLE_CHANGE,
    sem backup, sem compose up, sem timeout e sem arquivo blocked.
    """
    old_img = _make_image_doc(INCIDENT_OLD_INDEX, created="2026-11-23T16:09:27Z")
    new_img = _make_image_doc(INCIDENT_NEW_INDEX, created="2026-11-23T19:35:00Z")
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": INCIDENT_OLD_INDEX},
        "target_image_id": INCIDENT_NEW_INDEX,
        "images": {
            INCIDENT_OLD_INDEX: old_img,
            "ghcr.io/nosremekk/lectoria:stable": new_img,
            INCIDENT_NEW_INDEX: new_img,
        },
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)

    assert res.returncode == 0, res.stderr
    assert "NO_EXECUTABLE_CHANGE" in res.stdout
    calls = calls_file.read_text(encoding="utf-8")
    assert "backup_ran" not in calls
    assert "docker compose" not in calls
    assert not (state_dir / "blocked").exists()


def test_same_oci_index_no_deploy(tmp_path):
    """Mesmo OCI Index e container já atualizado -> sem backup e sem recriação."""
    img = _make_image_doc(INCIDENT_NEW_INDEX)
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": INCIDENT_NEW_INDEX},
        "target_image_id": INCIDENT_NEW_INDEX,
        "images": {INCIDENT_NEW_INDEX: img, "ghcr.io/nosremekk/lectoria:stable": img},
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 0
    assert "NO_EXECUTABLE_CHANGE" in res.stdout
    assert "docker compose" not in calls_file.read_text(encoding="utf-8")


def test_executable_layers_changed_triggers_backup_deploy_and_rollback_record(tmp_path):
    """Manifesto executável diferente (nova camada) -> backup obrigatório + compose up + registro imutável de rollback."""
    old_img = _make_image_doc("sha256:old1", layers=[LAYER_A, LAYER_B])
    new_img = _make_image_doc("sha256:new2", layers=[LAYER_A, LAYER_C])
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"},
        "target_image_id": "sha256:new2",
        "images": {
            "sha256:old1": old_img,
            "ghcr.io/nosremekk/lectoria:stable": new_img,
            "sha256:new2": new_img,
        },
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)

    assert res.returncode == 0, res.stderr
    assert "EXECUTABLE_CHANGE_DETECTED" in res.stdout
    assert "UPDATE_SUCCESS" in res.stdout
    calls = calls_file.read_text(encoding="utf-8")
    assert calls.index("backup_ran") < calls.index("docker compose")
    rollback = json.loads((state_dir / "last-good.json").read_text(encoding="utf-8"))
    assert rollback["previous_image_id"] == "sha256:old1"
    assert rollback["previous_immutable_ref"].startswith("ghcr.io/nosremekk/lectoria@sha256:")
    assert not (state_dir / "blocked").exists()


def test_config_digest_change_detected_as_real_update(tmp_path):
    """Config executável diferente (ex.: novo CMD/ENV/Label) -> detecta mudança real e atualiza."""
    old_img = _make_image_doc("sha256:cfg1", env=["LECTORIA_PORT=8765"])
    new_img = _make_image_doc("sha256:cfg2", env=["LECTORIA_PORT=8765", "MALLOC_ARENA_MAX=2"])
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:cfg1"},
        "target_image_id": "sha256:cfg2",
        "images": {
            "sha256:cfg1": old_img,
            "ghcr.io/nosremekk/lectoria:stable": new_img,
            "sha256:cfg2": new_img,
        },
    }
    env, state_dir, _ = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 0, res.stderr
    assert "UPDATE_SUCCESS" in res.stdout


def test_unhealthy_before_update_aborts_safely(tmp_path):
    """Container unhealthy antes da atualização -> falha segura sem pull nem deploy."""
    img = _make_image_doc("sha256:old1")
    scenario = {
        "container": {"status": "running", "health": "unhealthy", "image_id": "sha256:old1"},
        "target_image_id": "sha256:new2",
        "images": {"sha256:old1": img},
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 1
    assert "PRECHECK_UNHEALTHY" in res.stderr
    assert "docker pull" not in calls_file.read_text(encoding="utf-8")


def test_pull_or_network_failure_aborts_without_data_loss(tmp_path):
    """Falha no pull / rede -> sem backup, sem compose up, sem alterar container."""
    img = _make_image_doc("sha256:old1")
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"},
        "target_image_id": "sha256:new2",
        "pull_fail": True,
        "images": {"sha256:old1": img},
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 1
    assert "PULL_FAILED" in res.stderr
    calls = calls_file.read_text(encoding="utf-8")
    assert "backup_ran" not in calls
    assert "docker compose" not in calls


def test_backup_failure_cancels_deploy(tmp_path):
    """Falha no backup -> deploy cancelado antes de tocar no container."""
    old_img = _make_image_doc("sha256:old1", layers=[LAYER_A])
    new_img = _make_image_doc("sha256:new2", layers=[LAYER_C])
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"},
        "target_image_id": "sha256:new2",
        "backup_fail": True,
        "images": {"sha256:old1": old_img, "ghcr.io/nosremekk/lectoria:stable": new_img},
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 1
    assert "BACKUP_FAILED" in res.stderr
    assert "docker compose" not in calls_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "failure_mode,expected_reason",
    [
        ({"compose_fail": True}, "COMPOSE_UP_FAILED"),
        ({"post_update_health": "unhealthy"}, "HEALTHCHECK_TIMEOUT"),
        ({"compose_updates_container": False}, "IMAGE_IDENTITY_VERIFICATION_FAILED"),
    ],
)
def test_deploy_failures_create_blocked_file_with_diagnosis(tmp_path, failure_mode, expected_reason):
    """Falha no Compose, timeout de healthcheck ou falha na verificação de imagem -> bloqueia fail-closed."""
    old_img = _make_image_doc("sha256:old1", layers=[LAYER_A])
    new_img = _make_image_doc("sha256:new2", layers=[LAYER_C])
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"},
        "target_image_id": "sha256:new2",
        "images": {
            "sha256:old1": old_img,
            "ghcr.io/nosremekk/lectoria:stable": new_img,
            "sha256:new2": new_img,
        },
        **failure_mode,
    }
    env, state_dir, _ = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 1
    blocked = state_dir / "blocked"
    assert blocked.exists()
    blocked_text = blocked.read_text(encoding="utf-8")
    assert f"reason={expected_reason}" in blocked_text
    assert "previous_immutable_ref=" in blocked_text


def test_existing_blocked_file_prevents_retry(tmp_path):
    """Arquivo blocked existente -> encerra imediatamente sem nova tentativa."""
    scenario = {"container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"}, "images": {}}
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    (state_dir / "blocked").write_text("reason=PREVIOUS\n", encoding="utf-8")
    res = _run_updater(env)
    assert res.returncode == 1
    assert "BLOCKED_BY_PREVIOUS_FAILURE" in res.stderr
    assert not calls_file.exists()


def test_concurrent_execution_prevented_by_flock(tmp_path):
    """Atualização concorrente -> lock exclusivo flock impede conflito."""
    scenario = {"container": {"status": "running", "health": "healthy", "image_id": "sha256:old1"}, "images": {}}
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    lock_file = state_dir / "update.lock"
    holder = subprocess.Popen(["flock", str(lock_file), "sleep", "5"])
    try:
        time.sleep(0.2)
        res = _run_updater(env)
        assert res.returncode == 0
        assert "CONCURRENT_RUN_SKIPPED" in res.stdout
        assert not calls_file.exists()
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_custom_registry_and_no_systemd_standalone_mode(tmp_path):
    """Compatibilidade self-hosted: registry customizado e execução standalone sem systemd."""
    old_img = _make_image_doc("sha256:priv1", layers=[LAYER_A], repo_digests=["registry.local:5000/lectoria@sha256:priv1"])
    new_img = _make_image_doc("sha256:priv2", layers=[LAYER_B], repo_digests=["registry.local:5000/lectoria@sha256:priv2"])
    scenario = {
        "image_ref": "registry.local:5000/lectoria:stable",
        "container": {"status": "running", "health": "no_healthcheck", "image_id": "sha256:priv1"},
        "target_image_id": "sha256:priv2",
        "post_update_health": "no_healthcheck",
        "images": {
            "sha256:priv1": old_img,
            "registry.local:5000/lectoria:stable": new_img,
            "sha256:priv2": new_img,
        },
    }
    env, state_dir, _ = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 0, res.stderr
    assert "UPDATE_SUCCESS" in res.stdout


def test_missing_previous_image_metadata_fails_closed_safely(tmp_path):
    """Imagem anterior indisponível no daemon -> aborta fail-closed sem rollback inseguro."""
    scenario = {
        "container": {"status": "running", "health": "healthy", "image_id": "sha256:gone"},
        "target_image_id": "sha256:new2",
        "images": {},
    }
    env, state_dir, calls_file = _setup_mock_env(tmp_path, scenario)
    res = _run_updater(env)
    assert res.returncode == 1
    assert "docker compose" not in calls_file.read_text(encoding="utf-8")
