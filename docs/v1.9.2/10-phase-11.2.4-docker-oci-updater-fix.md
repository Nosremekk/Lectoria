# Lectoria — Fase 11.2.4: Correção do Auto-Update Docker/OCI

- **Status**: **`READY_FOR_STAGING`** *(implementação e testes concluídos localmente na branch `refactor/v1.9.2-ci-cd-hardening`; zero alterações na VPS, zero merge/push/tag/publicação GHCR)*
- **Data**: `2026-11-23`
- **Branch**: `refactor/v1.9.2-ci-cd-hardening` (PR #1 — `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)

---

## 1. Resumo Executivo

Corrigido o bug estrutural que provocou o falso timeout e o bloqueio (`/var/lib/lectoria-auto-update/blocked`) do atualizador automático em `2026-11-23 19:48 UTC`. O novo atualizador versionado ([`deployment/docker-auto-update.sh`](../../deployment/docker-auto-update.sh)) compara o **fingerprint executável canônico** da imagem (`Os`, `Architecture`, `RootFS.Layers` e `Config` executável) em vez de depender apenas da igualdade de strings de `Id` / OCI Image Index. Assim, mudanças exclusivamente no OCI Index ou em attestations de provenance do BuildKit encerram com **`NO_EXECUTABLE_CHANGE`** (`exit 0`, sem backup redundante, sem recriação de container e sem falso timeout), enquanto mudanças reais de camadas ou configuração disparam backup obrigatório, registro imutável de rollback e verificação de saúde.

---

## 2. Causa do Incidente e Evidências

Em `2026-11-23 19:48 UTC`, o workflow antigo da `v1.9.1` reconstruiu a imagem ao receber a tag `v1.9.1` (run `#45248554299`), gerando uma nova *provenance attestation* (`unknown/unknown`) e consequentemente um novo OCI Image Index digest:
- **OCI Index anterior** (`ContainerImageID` no container `lectoria`): `sha256:36af447d240272bab0cefb97bc0972534b77c87fe98d0d3cd383de9b3e4d3982`
- **OCI Index novo** (`LocalImageID` de `ghcr.io/nosremekk/lectoria:stable` após pull): `sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253`
- **Manifesto `linux/amd64` (idêntico em ambos)**: `sha256:b3ca97d1ba4c7fac50356e97e26fd0c2bd1644526926cd8796781541c3df0af9`
- **Config Digest (idêntico em ambos)**: `sha256:e4607f2adb7ee9179dd7db87204a6f12129151a4383f1b76b42bd6bf5eb346dd`

O atualizador antigo na VPS (não versionado anteriormente) comparava `ContainerImageID == LocalImageID`. Como o Docker Compose manteve corretamente o container existente (executável inalterado), o loop de verificação deu timeout após ~150 s e criou `/var/lib/lectoria-auto-update/blocked`, bloqueando as execuções subsequentes (`20:37:48Z`, `20:52:52Z`). A Lectoria permaneceu saudável e o backup gerado passou em `PRAGMA integrity_check = ok`.

---

## 3. Explicação dos Identificadores OCI / Docker

| Identificador | O que representa | Comportamento no Image Store Clássico vs. Containerd |
| :--- | :--- | :--- |
| **OCI Image Index digest** | Hash da lista multi-plataforma + attestations (`unknown/unknown`) | No **containerd image store** (Docker 25+/26+), `docker image inspect` e `docker inspect --format '{{.Image}}'` podem expor este digest; muda sempre que uma attestation muda, mesmo sem alterar 1 byte do executável. |
| **OCI Platform Manifest digest** | Hash do manifesto específico da plataforma (ex.: `linux/amd64`) | Estável quando camadas e config não mudam (`b3ca97d1...`). |
| **Image Config digest / `RootFS.Layers`** | Hash da configuração executável (`Cmd`, `Entrypoint`, `Env`, `Healthcheck`, `Labels`) e lista ordenada de camadas do filesystem | Representa fielmente o conteúdo executável real em **ambos** os stores (clássico e containerd). |
| **Docker Image ID** (`docker image inspect .Id`) | No store clássico = Config digest; no containerd store = OCI Index digest | **Não é seguro comparar diretamente** entre tag recém-baixada e container em execução sem inspecionar o conteúdo executável. |
| **`RepoDigests`** | Referência `repo@sha256:...` do registro de origem | Útil como referência imutável de rollback (`@sha256:...`), mas reflete o OCI Index. |
| **`Config.Image`** (no container) | String original da tag usada ao criar o container (ex.: `:stable`) | Mutável; nunca indica qual versão/digest está rodando. |

---

## 4. Estratégia Selecionada

Em [`deployment/docker-auto-update.sh`](../../deployment/docker-auto-update.sh), a função `compute_image_metadata()` lê o JSON completo de `docker image inspect` (disponível tanto no store clássico quanto no containerd, sem depender de acesso extra ao registry após o pull) e calcula um **SHA-256 canônico (`exec_fp`)** sobre:
- `Os`, `Architecture`, `Variant`
- `RootFS.Type` e `RootFS.Layers` (todas as camadas do sistema de arquivos)
- `Config.Entrypoint`, `Config.Cmd`, `Config.Env` (ordenado), `Config.WorkingDir`, `Config.User`, `Config.ExposedPorts`, `Config.Healthcheck` e `Config.Labels`.

Ignora deliberadamente o `Id` externo (OCI Index), `Created` e provenance attestations externas.
- **Limitação documentada**: alterações puramente em `Config.Labels` (ex.: `org.opencontainers.image.revision` do novo `Dockerfile` no PR #1) **alteram** `exec_fp` e disparam atualização — comportamento desejado para garantir rastreabilidade do commit, enquanto re-atestações idênticas (como o incidente de 2026-11-23) mantêm `exec_fp` idêntico (`NO_EXECUTABLE_CHANGE`).

---

## 5. Arquivos Criados / Modificados

| Arquivo | Finalidade |
| :--- | :--- |
| [`deployment/docker-auto-update.sh`](../../deployment/docker-auto-update.sh) | Atualizador Docker/OCI versionado, parametrizável, lock `flock`, comparação por `exec_fp`, backup obrigatório, rollback metadata imutável e bloqueio `fail-closed`. |
| [`deployment/systemd/lectoria-auto-update.service`](../../deployment/systemd/lectoria-auto-update.service) | Template de unidade `systemd` opcional com `EnvironmentFile=-/etc/default/lectoria-auto-update`. |
| [`deployment/systemd/lectoria-auto-update.timer`](../../deployment/systemd/lectoria-auto-update.timer) | Template de timer `systemd` opcional (`OnUnitInactiveSec=15min`). |
| [`tests/test_v192_docker_auto_update.py`](../../tests/test_v192_docker_auto_update.py) | 15 testes isolados com mocks de `docker`/`curl`/backup cobrindo os 18 cenários + reprodução exata do incidente de `2026-11-23`. |
| [`docs/v1.9.2/05-implementation-roadmap.md`](./05-implementation-roadmap.md) | Atualizado com o andamento da Fase 11.2.4. |

---

## 6. Algoritmo de Atualização, Backup e Rollback

1. **Etapa A (Pré-verificação)**: cria diretório de estado, valida binários (`docker`, `python3`), adquire lock exclusivo não-bloqueante (`flock -n`), aborta se `blocked` existir, valida `compose.yaml` e aborta (`PRECHECK_UNHEALTHY`) se o container já estiver parado ou `unhealthy`.
2. **Etapa B (Inspeção)**: calcula `current_exec_fp` da imagem do container, executa `docker pull` (falha de rede/pull encerra sem alterar nada) e calcula `target_exec_fp`.
3. **Etapa C (Decisão)**:
   - Se `current_exec_fp == target_exec_fp` → registra **`NO_EXECUTABLE_CHANGE`**, não faz backup, não recria container, sai com `0`.
   - Se diferente → executa `LECTORIA_BACKUP_CMD` obrigatório (se falhar ou não configurado com `LECTORIA_REQUIRE_BACKUP=1`, cancela deploy), grava `$STATE_DIR/last-good.json` com `previous_image_id` e `previous_immutable_ref` (`repo@sha256:...`), executa `docker compose up -d --no-deps <service>` (nunca `down -v`), e aguarda convergência de **`exec_fp == target_exec_fp`** + saúde do container (`healthy` ou `/api/health` 200).
4. **Política de Rollback (Fail-Closed)**: qualquer falha pós-`compose up` (erro do Compose, divergência de identidade ou timeout de healthcheck) cria `$STATE_DIR/blocked` com diagnóstico completo e referência imutável anterior, preserva backups e volumes, e **não** executa downgrade automático cego de schema SQLite.

---

## 7. Testes Executados e Resultados

- `bash -n deployment/docker-auto-update.sh`: **APROVADO (`exit 0`)**.
- `shellcheck`: **não instalado neste host** (limitação registrada; sintaxe e segurança shell revisadas manualmente com `set -euo pipefail` e quoting rigoroso).
- `tests/test_v192_docker_auto_update.py` (15 testes cobrindo os 18 cenários + reprodução do incidente de 2026-11-23): **15/15 aprovados**.
- `tests/test_v192_ci_cd_architecture.py`: **8/8 aprovados**.
- Suíte completa da Lectoria (unitários/API + E2E Playwright): **383/383 aprovados (`0` falhas)** — `328` unitários/API (`349,00 s`) + `55` E2E Playwright (`237,76 s`).

---

## 8. Compatibilidade Self-Hosted e Riscos Remanescentes

- **100% independente** de `nosreme.cloud`, IPs, Tailscale ou caminhos `/srv/nosreme`: todos os caminhos, registry/imagem, comando de backup e timeouts são configuráveis via variáveis de ambiente (`/etc/default/lectoria-auto-update`), funcionando com `systemd`, `cron` ou execução manual (opcional; quem fixa `:vX.Y.Z` ou `@sha256:...` não precisa ativar o atualizador).
- **Riscos remanescentes**: (1) requer `python3` e `flock` no host Linux; (2) se o container não tiver `HEALTHCHECK` Docker, depende de `curl` acessível em `LECTORIA_HEALTH_URL`; (3) rollback de imagem em caso de falha continua sendo uma operação assistida pelo administrador para proteger a integridade de migrações de schema SQLite.

---

## 9. Plano de Instalação Futura na VPS e Condições de Liberação (NÃO EXECUTAR AGORA)

### Instalação futura (somente após autorização expressa)
```bash
# 1. Backup do script atual na VPS
sudo cp -a /usr/local/sbin/lectoria-auto-update /usr/local/sbin/lectoria-auto-update.bak.$(date -u +%Y%m%dT%H%M%SZ)

# 2. Instalar nova versão versionada e arquivo de configuração da VPS
sudo install -m 0700 -o root -g root deployment/docker-auto-update.sh /usr/local/sbin/lectoria-auto-update
# Criar /etc/default/lectoria-auto-update com:
#   LECTORIA_COMPOSE_DIR=/srv/nosreme/lectoria
#   LECTORIA_COMPOSE_FILE=/srv/nosreme/lectoria/compose.yaml
#   LECTORIA_BACKUP_CMD=/usr/local/sbin/lectoria-backup
#   LECTORIA_IMAGE_REF=ghcr.io/nosremekk/lectoria:stable
```

### Condições para remover `/var/lib/lectoria-auto-update/blocked`
1. Novo script instalado e configurado em `/etc/default/lectoria-auto-update`.
2. Container `lectoria` confirmado `healthy` em `v1.9.1` e SQLite `PRAGMA integrity_check = ok`.
3. Execução controlada única após remover `blocked`, confirmando saída **`NO_EXECUTABLE_CHANGE`** sem recriar o container.

### Condições para liberar o merge do PR #1
1. Validação do novo atualizador na VPS (ou pausa manual do timer durante o merge).
2. Autorização expressa do proprietário para merge do [PR #1](https://github.com/Nosremekk/Lectoria/pull/1) em `main`.
