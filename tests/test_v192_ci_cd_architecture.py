"""
Testes estruturais e comportamentais da arquitetura de CI/CD, imutabilidade de releases
e rastreabilidade OCI (Fase 11.2 — v1.9.2).

Valida os Cenários A–J definidos na especificação da Fase 11.2 sem depender de
execução remota no GitHub Actions ou alterações na VPS.
"""

from pathlib import Path
import re
import subprocess

import config
import database


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
TEST_SUITE_WF = WORKFLOWS_DIR / "_test-suite.yml"
CI_WF = WORKFLOWS_DIR / "ci.yml"
PUBLISH_WF = WORKFLOWS_DIR / "publish.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _read(path: Path) -> str:
    assert path.exists(), f"Arquivo obrigatório ausente: {path}"
    return path.read_text(encoding="utf-8")


def _validate_yaml_syntax(path: Path) -> None:
    text = _read(path)
    # 1. Zero tabs em arquivos YAML
    for idx, line in enumerate(text.splitlines(), start=1):
        assert "\t" not in line, f"Tab proibido em YAML: {path}:{idx}"
    # 2. Expressões GitHub Actions ${{ ... }} sempre fechadas na mesma linha (sem confundir com Go templates {{ ... }})
    for idx, line in enumerate(text.splitlines(), start=1):
        opens = line.count("${{")
        closed_exprs = len(re.findall(r"\$\{\{[^{}]*\}\}", line))
        assert opens == closed_exprs, f"Expressão GitHub Actions desbalanceada em {path}:{idx}: {line}"
    # 3. Se PyYAML estiver instalado no ambiente, valida parsing completo
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict), f"Raiz YAML inválida em {path}"
    except ModuleNotFoundError:
        pass


def test_v192_phase112_yaml_and_version_invariants():
    """Valida sintaxe YAML dos workflows/Compose e preservação de APP_VERSION=1.9.2 e SCHEMA_VERSION=13."""
    for target in (TEST_SUITE_WF, CI_WF, PUBLISH_WF, COMPOSE_FILE):
        _validate_yaml_syntax(target)

    assert config.APP_VERSION == "1.9.2"
    assert database.SCHEMA_VERSION == 13


def test_v192_reusable_test_suite_contract():
    """Valida o workflow reutilizável _test-suite.yml (workflow_call, Python 3.12, permissões mínimas, timeout)."""
    text = _read(TEST_SUITE_WF)
    assert "workflow_call:" in text
    assert "contents: read" in text
    assert "packages: write" not in text
    assert "timeout-minutes:" in text
    assert 'python-version: "3.12"' in text
    assert "python -m py_compile *.py scripts/*.py" in text
    assert "pytest -v tests/" in text
    assert "python scripts/diagnose.py --json" in text
    assert "playwright install --with-deps chromium" in text


def test_v192_scenarios_a_and_b_ci_dev_branches_and_pr_without_publish():
    """Cenários A e B: push em branches dev e PR para main executam _test-suite.yml sem publicar imagens."""
    ci_text = _read(CI_WF)
    pub_text = _read(PUBLISH_WF)

    assert "feat/*" in ci_text
    assert "fix/*" in ci_text
    assert "refactor/*" in ci_text
    assert "pull_request:" in ci_text
    assert "uses: ./.github/workflows/_test-suite.yml" in ci_text
    assert "packages: write" not in ci_text
    assert "docker/build-push-action" not in ci_text
    # publish.yml não dispara em pull_request
    assert "pull_request:" not in pub_text


def test_v192_scenario_c_main_push_deduplicated_and_no_versioned_tag_overwrite():
    """Cenário C: push em main executa testes 1 vez, publica :sha-* + :stable e nunca sobrescreve :vX.Y.Z."""
    ci_text = _read(CI_WF)
    pub_text = _read(PUBLISH_WF)

    # ci.yml não dispara em push para main (evita duplicação com publish.yml)
    push_block = ci_text.split("pull_request:")[0]
    assert '"main"' not in push_block and "'main'" not in push_block

    # publish.yml chama _test-suite.yml no modo continuous
    assert "uses: ./.github/workflows/_test-suite.yml" in pub_text
    assert "needs: [preflight, test]" in pub_text

    # No job contínuo, build-push-action envia APENAS :sha-* (nunca :vX.Y.Z nem :stable antes do smoke test)
    cont_block = pub_text.split("build-and-publish-continuous:")[1].split("promote-release-tag:")[0]
    assert "ghcr.io/nosremekk/lectoria:${{ needs.preflight.outputs.sha_tag }}" in cont_block
    assert "raw_version" not in cont_block
    assert "clean_version" not in cont_block


def test_v192_scenarios_d_f_h_tag_promotion_immutability_and_smoke_gate():
    """Cenários D, F, G, H: promoção sem rebuild, recusa de sobrescrita e :stable protegido por smoke test."""
    pub_text = _read(PUBLISH_WF)

    cont_block = pub_text.split("build-and-publish-continuous:")[1].split("promote-release-tag:")[0]
    rel_block = pub_text.split("promote-release-tag:")[1]

    # Cenário H: smoke test ocorre ANTES da promoção para :stable no fluxo contínuo
    smoke_pos = cont_block.index("Run smoke test against exact built digest")
    promote_pos = cont_block.index("Promote validated digest to :stable")
    assert smoke_pos < promote_pos

    # Cenário D: promote-release-tag não usa docker/build-push-action (promove digest sem rebuild)
    assert "docker/build-push-action" not in rel_block
    assert "docker buildx imagetools create" in rel_block
    assert 'org.opencontainers.image.revision' in rel_block

    # Cenário F: recusa explícita e fail-closed se :vX.Y.Z ou :X.Y.Z já existir (ou erro transitório) no GHCR
    immut_pos = rel_block.index("Enforce immutability")
    rel_promote_pos = rel_block.index("Promote digest to immutable release tags")
    assert immut_pos < rel_promote_pos
    assert "imagetools inspect" in rel_block
    assert "Sobrescrita recusada" in rel_block
    assert "not found|manifest unknown" in rel_block
    # Proteção contra regressão de :stable quando tag aponta para commit anterior ao HEAD de main
    assert "is_main_head" in rel_block
    assert 'if [ "${IS_MAIN_HEAD}" = "true" ]; then' in rel_block

    # Smoke test na promoção de release também precede criação das tags
    rel_smoke_pos = rel_block.index("Run smoke test against promoted digest")
    assert rel_smoke_pos < rel_promote_pos


def test_v192_scenarios_e_and_j_preflight_validation_behavior():
    """Cenários E e J: valida lógica real de SemVer, alinhamento com APP_VERSION e bloqueio de branch fora da main."""
    app_ver = config.APP_VERSION

    # Simula a regra de preflight para tag divergente (Cenário E) e inválida
    script = """
set -euo pipefail
APP_VER="$1"
RAW_VERSION="$2"
if ! [[ "$RAW_VERSION" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then
  echo "INVALID_SEMVER"
  exit 1
fi
if [ "$RAW_VERSION" != "v${APP_VER}" ]; then
  echo "VERSION_MISMATCH"
  exit 1
fi
echo "VALID_MATCH"
"""
    ok_res = subprocess.run(["bash", "-c", script, "_", app_ver, f"v{app_ver}"], capture_output=True, text=True)
    assert ok_res.returncode == 0 and "VALID_MATCH" in ok_res.stdout

    mismatch_res = subprocess.run(["bash", "-c", script, "_", app_ver, "v9.9.9"], capture_output=True, text=True)
    assert mismatch_res.returncode == 1 and "VERSION_MISMATCH" in mismatch_res.stdout

    invalid_res = subprocess.run(["bash", "-c", script, "_", app_ver, "v1.9"], capture_output=True, text=True)
    assert invalid_res.returncode == 1 and "INVALID_SEMVER" in invalid_res.stdout

    # Cenário J: workflow_dispatch contínuo bloqueia refs fora de refs/heads/main
    pub_text = _read(PUBLISH_WF)
    assert 'if [ "$FULL_REF" != "refs/heads/main" ]; then' in pub_text
    assert "git merge-base --is-ancestor" in pub_text


def test_v192_scenario_i_concurrency_and_oci_traceability():
    """Cenário I + Seção 7: concorrência serializada sem cancelamento e metadados OCI completos."""
    pub_text = _read(PUBLISH_WF)
    df_text = _read(DOCKERFILE)

    assert "group: ghcr-publish-lectoria" in pub_text
    assert "cancel-in-progress: false" in pub_text
    assert "GITHUB_STEP_SUMMARY" in pub_text

    for label in (
        "org.opencontainers.image.source",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.version",
        "org.opencontainers.image.created",
    ):
        assert label in df_text, f"Label OCI ausente no Dockerfile: {label}"
        assert label in pub_text, f"Label OCI ausente no publish.yml: {label}"

    for arg in ("ARG VCS_REF", "ARG APP_VERSION", "ARG BUILD_DATE"):
        assert arg in df_text, f"Build-arg ausente no Dockerfile: {arg}"


def test_v192_generic_docker_compose_aligned():
    """Seção 8: docker-compose.yml usa :stable e healthcheck em /api/health (sem :latest nem /api/books)."""
    compose_text = _read(COMPOSE_FILE)
    assert "ghcr.io/nosremekk/lectoria:stable" in compose_text
    assert "ghcr.io/nosremekk/lectoria:latest" not in compose_text
    assert "http://127.0.0.1:8765/api/health" in compose_text
    assert "/api/books" not in compose_text
