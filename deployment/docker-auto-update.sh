#!/usr/bin/env bash
# ==============================================================================
# Lectoria — Atualizador Automático Seguro para Docker / OCI (Self-Hosted)
# ==============================================================================
# Compara a identidade EXECUTÁVEL da imagem (RootFS layers + arquitetura + config
# executável), evitando falsos positivos e timeouts causados por mudanças apenas
# no OCI Image Index ou em attestations de provenance do BuildKit.
#
# Independente de domínio, IP, Tailscale ou caminhos específicos de VPS.
# Pode ser executado via systemd timer, cron ou manualmente.
# ==============================================================================
set -Eeuo pipefail
umask 077

# ------------------------------------------------------------------------------
# Configuração parametrizável (valores padrão neutros para self-hosting)
# ------------------------------------------------------------------------------
LECTORIA_COMPOSE_DIR="${LECTORIA_COMPOSE_DIR:-/opt/lectoria}"
LECTORIA_COMPOSE_FILE="${LECTORIA_COMPOSE_FILE:-${LECTORIA_COMPOSE_DIR}/compose.yaml}"
LECTORIA_SERVICE_NAME="${LECTORIA_SERVICE_NAME:-lectoria}"
LECTORIA_CONTAINER_NAME="${LECTORIA_CONTAINER_NAME:-lectoria}"
LECTORIA_IMAGE_REF="${LECTORIA_IMAGE_REF:-ghcr.io/nosremekk/lectoria:stable}"
LECTORIA_STATE_DIR="${LECTORIA_STATE_DIR:-/var/lib/lectoria-auto-update}"
LECTORIA_LOCK_FILE="${LECTORIA_LOCK_FILE:-${LECTORIA_STATE_DIR}/update.lock}"
LECTORIA_BLOCKED_FILE="${LECTORIA_BLOCKED_FILE:-${LECTORIA_STATE_DIR}/blocked}"
LECTORIA_ROLLBACK_FILE="${LECTORIA_ROLLBACK_FILE:-${LECTORIA_STATE_DIR}/last-good.json}"
LECTORIA_BACKUP_CMD="${LECTORIA_BACKUP_CMD:-}"
LECTORIA_REQUIRE_BACKUP="${LECTORIA_REQUIRE_BACKUP:-1}"
LECTORIA_HEALTH_URL="${LECTORIA_HEALTH_URL:-http://127.0.0.1:8765/api/health}"
LECTORIA_HEALTH_TIMEOUT_SEC="${LECTORIA_HEALTH_TIMEOUT_SEC:-120}"
LECTORIA_HEALTH_INTERVAL_SEC="${LECTORIA_HEALTH_INTERVAL_SEC:-3}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CURL_BIN="${CURL_BIN:-curl}"

TEMP_FILES=()

cleanup_temps() {
  local f
  for f in "${TEMP_FILES[@]:-}"; do
    if [[ -n "${f}" && -f "${f}" ]]; then
      rm -f "${f}" || true
    fi
  done
}

on_signal() {
  err "Sinal de interrupcao recebido; encerrando com seguranca."
  cleanup_temps
  exit 130
}

trap cleanup_temps EXIT
trap on_signal INT TERM

log() {
  printf '[%s] [lectoria-auto-update] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

err() {
  printf '[%s] [lectoria-auto-update] [ERRO] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2
}

validate_positive_int() {
  local name="$1"
  local val="$2"
  if [[ ! "${val}" =~ ^[1-9][0-9]*$ ]]; then
    err "Variavel ${name} invalida ('${val}'): esperado inteiro positivo."
    exit 1
  fi
}

# Escrita atômica (arquivo temporário no mesmo diretório + mv -f)
write_blocked() {
  local reason="$1"
  local prev_id="${2:-unknown}"
  local target_id="${3:-unknown}"
  local prev_digest="${4:-unknown}"
  mkdir -p "${LECTORIA_STATE_DIR}"
  local tmp_file="${LECTORIA_BLOCKED_FILE}.tmp.$$"
  TEMP_FILES+=("${tmp_file}")
  cat > "${tmp_file}" <<EOF
timestamp=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
reason=${reason}
container=${LECTORIA_CONTAINER_NAME}
image_ref=${LECTORIA_IMAGE_REF}
previous_image_id=${prev_id}
previous_immutable_ref=${prev_digest}
target_image_id=${target_id}
rollback_note=Rollback de container NAO reverte migracoes de schema SQLite. Valide schema_migrations e backup antes de reverter manualmente.
EOF
  chmod 0600 "${tmp_file}"
  mv -f "${tmp_file}" "${LECTORIA_BLOCKED_FILE}"
  err "Atualizacoes automaticas bloqueadas em ${LECTORIA_BLOCKED_FILE}: ${reason}"
}

# Calcula fingerprint executável canônico a partir do JSON de `docker image inspect`
# Inclui: Os, Architecture, Variant, RootFS.Layers e Config executável
# (Entrypoint, Cmd, Env, WorkingDir, User, ExposedPorts, Volumes, StopSignal,
# Healthcheck, Labels).
# Ignora: Id externo (OCI Index), Created e provenance attestations externas.
compute_image_metadata() {
  local image_ref="$1"
  local inspect_json
  if ! inspect_json="$("${DOCKER_BIN}" image inspect "${image_ref}" 2>&1)"; then
    err "docker image inspect falhou para '${image_ref}': ${inspect_json}"
    return 1
  fi

  "${PYTHON_BIN}" -c '
import hashlib, json, sys

data = json.loads(sys.stdin.read())
if isinstance(data, list):
    if not data:
        sys.exit(1)
    img = data[0]
else:
    img = data

cfg = img.get("Config") or {}
rootfs = img.get("RootFS") or {}
layers = rootfs.get("Layers") or []

canonical = {
    "os": img.get("Os") or "",
    "architecture": img.get("Architecture") or "",
    "variant": img.get("Variant") or "",
    "rootfs_type": rootfs.get("Type") or "",
    "layers": layers,
    "entrypoint": cfg.get("Entrypoint") or [],
    "cmd": cfg.get("Cmd") or [],
    "env": sorted(cfg.get("Env") or []),
    "working_dir": cfg.get("WorkingDir") or "",
    "user": cfg.get("User") or "",
    "exposed_ports": sorted((cfg.get("ExposedPorts") or {}).keys()),
    "volumes": sorted((cfg.get("Volumes") or {}).keys()),
    "stop_signal": cfg.get("StopSignal") or "",
    "healthcheck": cfg.get("Healthcheck") or {},
    "labels": cfg.get("Labels") or {},
}
fp = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
repo_digests = img.get("RepoDigests") or []
immutable_ref = repo_digests[0] if repo_digests else (img.get("Id") or "unknown")
out = {
    "image_id": img.get("Id") or "",
    "exec_fp": f"sha256:{fp}",
    "immutable_ref": immutable_ref,
    "repo_digests": repo_digests,
    "immutable_recoverable": bool(repo_digests),
    "layers_count": len(layers),
}
print(json.dumps(out))
' <<< "${inspect_json}"
}

json_field() {
  local json_str="$1"
  local field="$2"
  "${PYTHON_BIN}" -c 'import json, sys; val = json.loads(sys.argv[1]).get(sys.argv[2], ""); print(json.dumps(val) if isinstance(val, (list, dict, bool)) else val)' "${json_str}" "${field}"
}

check_container_health() {
  local status_line
  status_line="$("${DOCKER_BIN}" inspect "${LECTORIA_CONTAINER_NAME}" --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no_healthcheck{{end}}' 2>/dev/null)" || return 1
  local state="${status_line%%|*}"
  local health="${status_line##*|}"

  if [[ "${state}" != "running" ]]; then
    return 1
  fi
  if [[ "${health}" == "healthy" ]]; then
    return 0
  fi
  if [[ "${health}" == "no_healthcheck" ]]; then
    "${CURL_BIN}" -fsS --max-time 4 "${LECTORIA_HEALTH_URL}" >/dev/null 2>&1
    return $?
  fi
  return 1
}

main() {
  # ----------------------------------------------------------------------------
  # Etapa A — Pré-verificação, Lock Exclusivo e Bloqueio Fail-Closed
  # ----------------------------------------------------------------------------
  validate_positive_int "LECTORIA_HEALTH_TIMEOUT_SEC" "${LECTORIA_HEALTH_TIMEOUT_SEC}"
  validate_positive_int "LECTORIA_HEALTH_INTERVAL_SEC" "${LECTORIA_HEALTH_INTERVAL_SEC}"

  mkdir -p "${LECTORIA_STATE_DIR}"

  if ! command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
    err "Binario docker nao encontrado (${DOCKER_BIN})."
    exit 1
  fi
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    err "Binario python3 nao encontrado (${PYTHON_BIN})."
    exit 1
  fi

  # Lock exclusivo não-bloqueante para impedir atualizações concorrentes
  exec 9>"${LECTORIA_LOCK_FILE}"
  if ! flock -n 9; then
    log "CONCURRENT_RUN_SKIPPED: outra instancia do atualizador ja esta em execucao."
    exit 0
  fi

  if [[ -f "${LECTORIA_BLOCKED_FILE}" ]]; then
    err "BLOCKED_BY_PREVIOUS_FAILURE: arquivo ${LECTORIA_BLOCKED_FILE} presente. Resolva a falha anterior antes de liberar novas atualizacoes."
    exit 1
  fi

  if [[ ! -f "${LECTORIA_COMPOSE_FILE}" ]]; then
    err "Arquivo Docker Compose nao encontrado: ${LECTORIA_COMPOSE_FILE}"
    exit 1
  fi

  # Verifica se o container existe e está rodando saudável antes de qualquer mudança
  local container_state
  if ! container_state="$("${DOCKER_BIN}" inspect "${LECTORIA_CONTAINER_NAME}" --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no_healthcheck{{end}}|{{.Image}}' 2>/dev/null)"; then
    err "Container '${LECTORIA_CONTAINER_NAME}' nao encontrado."
    exit 1
  fi

  local pre_state="${container_state%%|*}"
  local rest="${container_state#*|}"
  local pre_health="${rest%%|*}"
  local current_container_image_id="${rest##*|}"

  if [[ "${pre_state}" != "running" ]] || [[ "${pre_health}" == "unhealthy" ]]; then
    err "PRECHECK_UNHEALTHY: container '${LECTORIA_CONTAINER_NAME}' em estado '${pre_state}/${pre_health}'. Atualizacao abortada por seguranca."
    exit 1
  fi

  # ----------------------------------------------------------------------------
  # Etapa B — Inspeção da Imagem Atual e Pull da Imagem do Canal
  # ----------------------------------------------------------------------------
  local current_meta
  if ! current_meta="$(compute_image_metadata "${current_container_image_id}")"; then
    err "Falha ao inspecionar metadados da imagem em execucao (${current_container_image_id})."
    exit 1
  fi
  local current_exec_fp
  local current_immutable_ref
  local current_repo_digests
  current_exec_fp="$(json_field "${current_meta}" "exec_fp")"
  current_immutable_ref="$(json_field "${current_meta}" "immutable_ref")"
  current_repo_digests="$(json_field "${current_meta}" "repo_digests")"

  log "Imagem atual: id=${current_container_image_id} exec_fp=${current_exec_fp}"
  log "Verificando atualizacoes em ${LECTORIA_IMAGE_REF}..."

  if ! "${DOCKER_BIN}" pull "${LECTORIA_IMAGE_REF}"; then
    err "PULL_FAILED: falha ao baixar ${LECTORIA_IMAGE_REF}. Nenhum container ou backup foi alterado."
    exit 1
  fi

  local target_meta
  if ! target_meta="$(compute_image_metadata "${LECTORIA_IMAGE_REF}")"; then
    err "Falha ao inspecionar metadados da imagem baixada (${LECTORIA_IMAGE_REF})."
    exit 1
  fi
  local target_image_id
  local target_exec_fp
  local target_immutable_ref
  target_image_id="$(json_field "${target_meta}" "image_id")"
  target_exec_fp="$(json_field "${target_meta}" "exec_fp")"
  target_immutable_ref="$(json_field "${target_meta}" "immutable_ref")"

  # ----------------------------------------------------------------------------
  # Etapa C — Decisão Baseada em Conteúdo Executável (Não Apenas OCI Index)
  # ----------------------------------------------------------------------------
  if [[ "${current_exec_fp}" == "${target_exec_fp}" ]]; then
    if [[ "${current_container_image_id}" != "${target_image_id}" ]]; then
      log "NO_EXECUTABLE_CHANGE: OCI Index/ID mudou (${current_container_image_id} -> ${target_image_id}), mas o conteudo executavel e identico (${current_exec_fp}). Nenhum backup ou reinicio necessario."
    else
      log "NO_EXECUTABLE_CHANGE: container ja executa a imagem mais recente (${current_exec_fp})."
    fi
    exit 0
  fi

  log "EXECUTABLE_CHANGE_DETECTED: ${current_exec_fp} -> ${target_exec_fp} (id ${current_container_image_id} -> ${target_image_id})"

  # Backup obrigatório antes de qualquer atualização real
  if [[ "${LECTORIA_REQUIRE_BACKUP}" == "1" ]]; then
    if [[ -z "${LECTORIA_BACKUP_CMD}" ]]; then
      err "BACKUP_NOT_CONFIGURED: LECTORIA_REQUIRE_BACKUP=1 exige LECTORIA_BACKUP_CMD configurado. Deploy cancelado."
      exit 1
    fi
    log "Executando backup preventivo obrigatorio..."
    if ! bash -c -- "${LECTORIA_BACKUP_CMD}"; then
      err "BACKUP_FAILED: backup preventivo falhou. Deploy cancelado sem alterar o container."
      exit 1
    fi
    log "Backup preventivo concluido com sucesso."
  fi

  # Gravação atômica da referência imutável de rollback (nunca depende de tag mutável)
  "${PYTHON_BIN}" -c '
import datetime, json, os, sys

repo_digests = json.loads(sys.argv[2])
doc = {
    "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "previous_image_id": sys.argv[1],
    "previous_repo_digests": repo_digests,
    "previous_immutable_ref": sys.argv[3],
    "previous_immutable_recoverable": bool(repo_digests),
    "previous_exec_fp": sys.argv[4],
    "target_image_id": sys.argv[5],
    "target_immutable_ref": sys.argv[6],
    "target_exec_fp": sys.argv[7],
    "note": "Rollback de imagem nao reverte migracoes de schema SQLite."
}
dest = sys.argv[8]
tmp = f"{dest}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, dest)
' "${current_container_image_id}" "${current_repo_digests}" "${current_immutable_ref}" "${current_exec_fp}" \
  "${target_image_id}" "${target_immutable_ref}" "${target_exec_fp}" "${LECTORIA_ROLLBACK_FILE}"

  # Atualização controlada (preserva volumes persistentes; nunca remove volumes)
  log "Aplicando atualizacao controlada via docker compose up -d --no-deps ${LECTORIA_SERVICE_NAME}..."
  if ! "${DOCKER_BIN}" compose -f "${LECTORIA_COMPOSE_FILE}" up -d --no-deps "${LECTORIA_SERVICE_NAME}"; then
    write_blocked "COMPOSE_UP_FAILED" "${current_container_image_id}" "${target_image_id}" "${current_immutable_ref}"
    exit 1
  fi

  # Aguarda convergência de identidade executável + saúde do container
  local elapsed=0
  local verified_fp=0
  local verified_health=0

  while (( elapsed < LECTORIA_HEALTH_TIMEOUT_SEC )); do
    local new_container_image_id
    new_container_image_id="$("${DOCKER_BIN}" inspect "${LECTORIA_CONTAINER_NAME}" --format '{{.Image}}' 2>/dev/null || true)"
    if [[ -n "${new_container_image_id}" ]]; then
      local new_meta
      if new_meta="$(compute_image_metadata "${new_container_image_id}" 2>/dev/null)"; then
        local new_exec_fp
        new_exec_fp="$(json_field "${new_meta}" "exec_fp")"
        if [[ "${new_exec_fp}" == "${target_exec_fp}" ]]; then
          verified_fp=1
        fi
      fi
    fi

    if [[ "${verified_fp}" -eq 1 ]] && check_container_health; then
      verified_health=1
      break
    fi

    sleep "${LECTORIA_HEALTH_INTERVAL_SEC}"
    elapsed=$(( elapsed + LECTORIA_HEALTH_INTERVAL_SEC ))
  done

  if [[ "${verified_fp}" -ne 1 ]]; then
    write_blocked "IMAGE_IDENTITY_VERIFICATION_FAILED" "${current_container_image_id}" "${target_image_id}" "${current_immutable_ref}"
    exit 1
  fi

  if [[ "${verified_health}" -ne 1 ]]; then
    write_blocked "HEALTHCHECK_TIMEOUT" "${current_container_image_id}" "${target_image_id}" "${current_immutable_ref}"
    exit 1
  fi

  log "UPDATE_SUCCESS: container '${LECTORIA_CONTAINER_NAME}' atualizado e saudavel (exec_fp=${target_exec_fp}, id=${target_image_id})."
}

main "$@"
