# Lectoria — Fase 11.2.5: Validação de Integração Docker/OCI e Preparação da Instalação Controlada

- **Status Final**: **`READY_FOR_CONTROLLED_INSTALL`**
- **Data**: `2026-11-23`
- **Branch / PR**: `refactor/v1.9.2-ci-cd-hardening` → [PR #1](https://github.com/Nosremekk/Lectoria/pull/1) (`DO NOT MERGE`)
- **Commit de Código Validado no CI Real**: `1853b098a3e7d7825233d7e29bbafff125b11176` (**`384 passed, 0 failed`**, incluindo testes de integração Docker Engine + Docker Compose v2 reais no `ubuntu-latest`)
- **Isolamento Confirmado**: `main` intacta em `dda6f27`, zero alterações na VPS, zero publicações GHCR, zero tags criadas, zero merge.

---

## 1. Resumo Executivo

A Fase 11.2.5 realizou a auditoria de segurança e *hardening* do atualizador versionado [`deployment/docker-auto-update.sh`](../../deployment/docker-auto-update.sh) e executou **testes de integração reais contra Docker Engine, Docker Compose v2 e um registry local efêmero (`registry:2`)** no runner `ubuntu-latest` do GitHub Actions (`test_v192_docker_real_integration_end_to_end`), aprovando **384/384 testes** sem qualquer publicação no GHCR ou alteração em produção.

---

## 2. Revisão Técnica do Fingerprint (`exec_fp`) e Identificadores Docker

- **Campos incluídos na canonicalização SHA-256 (`exec_fp`)**: `Os`, `Architecture`, `Variant`, `RootFS.Type`, `RootFS.Layers`, `Config.Entrypoint`, `Config.Cmd`, `Config.Env` (ordenado), `Config.WorkingDir`, `Config.User`, `Config.ExposedPorts` (ordenado), `Config.Volumes` (ordenado), `Config.StopSignal`, `Config.Healthcheck`, `Config.Labels`.
- **Campos deliberadamente excluídos**: `Id` externo (OCI Image Index no containerd image store), `Created` e *provenance attestations* externas (`unknown/unknown`).
- **Distinção entre stores**:
  - **Image store clássico**: `docker image inspect .Id` = Config digest.
  - **Containerd image store (Docker 25+/26+)**: `docker image inspect .Id` e `docker inspect .Image` podem refletir o OCI Image Index digest (alterado por attestations).
  - Em ambos os stores, `exec_fp` permanece **idêntico** quando o executável não muda e **muda deterministicamente** quando qualquer camada ou configuração executável é alterada.

---

## 3. Hardening de Segurança Aplicado em `deployment/docker-auto-update.sh` (Seções 6, 7 e 10)

1. **`set -Eeuo pipefail` + `umask 077` + trap `INT`/`TERM`/`EXIT`**: limpeza garantida de arquivos temporários e permissão restrita `0600` em `blocked`, `last-good.json` e `update.lock`.
2. **Escrita atômica**: `blocked` e `last-good.json` são escritos em `*.tmp.$$` no mesmo diretório e movidos via `mv -f` / `os.replace`, impedindo corrupção em quedas abruptas.
3. **Validação estrita de variáveis numéricas**: `LECTORIA_HEALTH_TIMEOUT_SEC` e `LECTORIA_HEALTH_INTERVAL_SEC` exigem inteiros positivos (`^[1-9][0-9]*$`).
4. **Diagnóstico explícito de `docker image inspect`**: falhas de inspeção registram o `stderr` real em vez de suprimi-lo.
5. **Auditoria de `LECTORIA_BACKUP_CMD`**: obrigatório quando `LECTORIA_REQUIRE_BACKUP=1`; falha aborta antes do deploy; executado via `bash -c -- "${LECTORIA_BACKUP_CMD}"` (na VPS: `systemctl start lectoria-backup.service`; em self-hosted sem systemd: script customizado; `/etc/default/lectoria-auto-update` deve ter permissão `0600 root:root`).
6. **Auditoria de `last-good.json` (rollback)**: grava `previous_image_id`, lista completa `previous_repo_digests`, `previous_immutable_ref` (`repo@sha256:...`), `previous_immutable_recoverable` (`true`/`false`) e nota explícita de que rollback de imagem **não** reverte migrações SQLite. Nunca sobrescreve `last-good.json` se o precheck falhar ou se `NO_EXECUTABLE_CHANGE`.

---

## 4. Ambiente de Testes e Resultados Reais

- **Host local (Fedora)**: `docker` e `podman` não estão instalados neste host (`test_v192_docker_real_integration_end_to_end` é pulado graciosamente com `skipif` sem mascarar resultado; `23 passed, 1 skipped` + `bash -n` aprovado). `shellcheck` indisponível neste host.
- **Runner real GitHub Actions (`ubuntu-latest` — Docker Engine + Docker Compose v2 + `registry:2` local efêmero em `127.0.0.1:<porta>`)**:

| Cenário | Execução Real (`ubuntu-latest`) | Evidência |
| :--- | :---: | :--- |
| **A — Imagem idêntica** | **APROVADO (Docker real)** | `NO_EXECUTABLE_CHANGE`, mesmo Container ID, zero backup |
| **B — Mudança apenas de OCI Index / attestation** | **Coberto por teste determinístico de regressão** | Reprodução exata do incidente `2026-11-23` (`36af447d` vs `da4dca9c`) em `test_v192_docker_auto_update.py` *(limitação transparente: `docker build` padrão no runner usa image store clássico sem gerar mutação isolada de attestation BuildKit; não declarado como teste real de OCI Index)* |
| **C — Mudança executável real (v1 → v2)** | **APROVADO (Docker real)** | `EXECUTABLE_CHANGE_DETECTED` → backup → `docker compose up -d --no-deps` → novo Container ID → `healthy` → `last-good.json` com `previous_repo_digests` real → `UPDATE_SUCCESS` |
| **D — Falha no backup** | **APROVADO (Docker real)** | `BACKUP_FAILED`, deploy cancelado, Container ID v1 preservado |
| **E — Falha no healthcheck (v3 unhealthy)** | **APROVADO (Docker real)** | Timeout detectado, criação atômica de `blocked` (`reason=HEALTHCHECK_TIMEOUT`), `last-good.json` preservado |
| **F — Atualizações concorrentes (`flock`)** | **APROVADO (Docker real)** | `CONCURRENT_RUN_SKIPPED` (`exit 0`), sem interferência |
| **G — Falha de rede / imagem indisponível** | **APROVADO (Docker real)** | `PULL_FAILED`, container anterior mantido `running`/`healthy` |
| **H — Container já atualizado (2ª execução)** | **APROVADO (Docker real)** | `NO_EXECUTABLE_CHANGE`, zero novo backup, mesmo Container ID v2 |

### Runs do GitHub Actions no Commit `1853b09`
- **[`#45277685286`](https://github.com/Nosremekk/Lectoria/actions/runs/45277685286)** (`CI` · `pull_request` @ `1853b09`): **`completed / success`** (`811 s`) — **`384 passed, 3 warnings in 775.13s (0:12:55)`**, incluindo `test_v192_docker_real_integration_end_to_end PASSED`.
- **[`#45277668507`](https://github.com/Nosremekk/Lectoria/actions/runs/45277668507)** (`CI` · `push` @ `1853b09`): **`completed / success`** (`874 s`).
- **`Publish Docker Image (GHCR)`**: **zero execuções** (nenhuma imagem publicada, `:stable` e `:v1.9.1` intocados).

---

## 5. Verificação de Backups e Compatibilidade com a VPS

- **Evidência de backup anterior (`2026-11-23 19:48 UTC`)**: continha `lectoria.db` (`PRAGMA integrity_check = ok`), `covers.tar.gz` e `commit.txt`. *Nota metodológica*: comprova integridade estrutural do arquivo SQLite analisado, mas não substitui um teste completo de restauração ponta a ponta da biblioteca.
- **Compatibilidade com a VPS**: o script suporta integralmente `/srv/nosreme/lectoria/compose.yaml`, `LECTORIA_BACKUP_CMD="systemctl start lectoria-backup.service"`, `/var/lib/lectoria-auto-update/blocked` e `/etc/default/lectoria-auto-update`, sem hardcoding no script versionado.

---

## 6. Commits Realizados na Branch `refactor/v1.9.2-ci-cd-hardening` (Fase 11.2.4 + 11.2.5)

- `24099e1` — `fix(deploy): compare Docker executable fingerprint instead of OCI index in auto-updater`
- `dd456ad` — `docs(v1.9.2): document phase 11.2.4 Docker/OCI auto-updater fix`
- `dd641eb` — `test(deploy): harden docker auto-updater and add real Docker integration tests`
- `928668b` — `fix(test): preserve per-version tags in local Docker integration test and log inspect stderr`
- `1853b09` — `fix(test): use fcntl.flock in Docker integration concurrency scenario to avoid leaked child fd`

---

## 7. Plano Futuro de Instalação Controlada na VPS (NÃO EXECUTAR NESTA FASE)

```bash
# --- 1 a 3: Pré-verificações somente-leitura ---
curl -fsS http://127.0.0.1:8765/api/health
LATEST_BKP=$(sudo ls -1dt /srv/nosreme/lectoria/backups/20* | head -n 1)
sudo ls -lh "$LATEST_BKP"
sudo python3 -c "import glob, sqlite3; p=glob.glob('$LATEST_BKP/*.db')[0]; c=sqlite3.connect(f'file:{p}?mode=ro&immutable=1', uri=True); print(p, c.execute('PRAGMA integrity_check;').fetchone()[0])"
sudo docker inspect lectoria --format 'ID={{.Id}} Image={{.Image}} ConfigImage={{.Config.Image}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'

# --- 4 a 5: Pausar timer e confirmar ausência de execução ativa ---
sudo systemctl stop lectoria-auto-update.timer
systemctl status lectoria-auto-update.service --no-pager || true

# --- 6 a 7: Preservar script anterior e unidades systemd existentes ---
TS=$(date -u +%Y%m%dT%H%M%SZ)
sudo cp -a /usr/local/sbin/lectoria-auto-update "/usr/local/sbin/lectoria-auto-update.bak.${TS}"
sudo cp -a /etc/systemd/system/lectoria-auto-update.service "/etc/systemd/system/lectoria-auto-update.service.bak.${TS}"
sudo cp -a /etc/systemd/system/lectoria-auto-update.timer "/etc/systemd/system/lectoria-auto-update.timer.bak.${TS}"

# --- 8 a 11: Instalar script corrigido, configurar /etc/default/lectoria-auto-update e validar sintaxe ---
sudo install -m 0700 -o root -g root deployment/docker-auto-update.sh /usr/local/sbin/lectoria-auto-update
sudo tee /etc/default/lectoria-auto-update >/dev/null <<'EOF'
LECTORIA_COMPOSE_DIR=/srv/nosreme/lectoria
LECTORIA_COMPOSE_FILE=/srv/nosreme/lectoria/compose.yaml
LECTORIA_SERVICE_NAME=lectoria
LECTORIA_CONTAINER_NAME=lectoria
LECTORIA_IMAGE_REF=ghcr.io/nosremekk/lectoria:stable
LECTORIA_STATE_DIR=/var/lib/lectoria-auto-update
LECTORIA_BACKUP_CMD="systemctl start lectoria-backup.service"
LECTORIA_REQUIRE_BACKUP=1
LECTORIA_HEALTH_URL=http://127.0.0.1:8765/api/health
EOF
sudo chmod 0600 /etc/default/lectoria-auto-update
sudo bash -n /usr/local/sbin/lectoria-auto-update
# Garantir EnvironmentFile=-/etc/default/lectoria-auto-update na unit (drop-in sem sobrescrever a unit original):
sudo mkdir -p /etc/systemd/system/lectoria-auto-update.service.d
printf '[Service]\nEnvironmentFile=-/etc/default/lectoria-auto-update\n' | sudo tee /etc/systemd/system/lectoria-auto-update.service.d/env.conf >/dev/null
sudo systemctl daemon-reload

# --- 12 a 18: Remover bloqueio de forma controlada e executar validação manual única ---
sudo cat /var/lib/lectoria-auto-update/blocked
BEFORE_CTR_ID=$(sudo docker inspect lectoria --format '{{.Id}}')
sudo rm -f /var/lib/lectoria-auto-update/blocked
sudo systemctl start lectoria-auto-update.service
sudo journalctl -u lectoria-auto-update.service -n 30 --no-pager
# Confirmar: log contém NO_EXECUTABLE_CHANGE, container ID == BEFORE_CTR_ID, nenhum backup extra criado
AFTER_CTR_ID=$(sudo docker inspect lectoria --format '{{.Id}}')
test "$BEFORE_CTR_ID" = "$AFTER_CTR_ID" && echo "CONTAINER_PRESERVED_OK"

# --- 19: Reativar timer somente após validação acima ---
sudo systemctl start lectoria-auto-update.timer
systemctl status lectoria-auto-update.timer --no-pager
```

### Plano de Recuperação (Caso a Instalação Falhe)
```bash
sudo systemctl stop lectoria-auto-update.timer
sudo cp -a "/usr/local/sbin/lectoria-auto-update.bak.${TS}" /usr/local/sbin/lectoria-auto-update
sudo rm -f /etc/systemd/system/lectoria-auto-update.service.d/env.conf
sudo systemctl daemon-reload
```

---

## 8. Critérios para Desbloquear o Atualizador e Autorizar o Merge do PR #1

1. **Desbloqueio do atualizador**: executar os passos 1–18 acima com aprovação explícita do proprietário e confirmar `NO_EXECUTABLE_CHANGE` + `CONTAINER_PRESERVED_OK`.
2. **Autorização do merge do PR #1**: após o novo atualizador estar validado na VPS (ou com o timer pausado durante a janela controlada), autorizar o merge do [PR #1](https://github.com/Nosremekk/Lectoria/pull/1) em `main` e acompanhar a primeira execução real de `publish.yml` (`continuous`).
