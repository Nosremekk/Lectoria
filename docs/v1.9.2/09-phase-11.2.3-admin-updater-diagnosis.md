# Lectoria — Fase 11.2.3: Diagnóstico Administrativo do Auto-Update e Preparação do Deploy

- **Decisão Recomendada**: **`BLOCKED`** *(bloqueio administrativo temporário: `sudo -n` exige senha na VPS, impedindo a leitura de `/usr/local/sbin/lectoria-auto-update`, `journalctl` e `docker inspect` para confirmar a causa da falha recorrente `status=1/FAILURE` do `lectoria-auto-update.service`)*
- **Data**: `2026-11-23 20:58 UTC` (`17:58 -03:00`)
- **Pull Request**: [PR #1](https://github.com/Nosremekk/Lectoria/pull/1) (`refactor/v1.9.2-ci-cd-hardening` @ `8698175` → `main` @ `dda6f27`)
- **Integridade da Produção**: 100% intacta (`v1.9.1`, Schema SQLite `13`, nenhum serviço reiniciado, nenhum timer pausado, nenhum container/banco alterado, nenhum merge/publicação executado).

---

## 1. Resumo Executivo

1. A aplicação em produção (`v1.9.1`) está **saudável** (`/api/health: healthy`, SQLite `PRAGMA integrity_check = ok`, Schema `13`).
2. **Falha recorrente confirmada**: `lectoria-auto-update.service` falhou **novamente** às `2026-11-23 20:52:52 UTC` (`PID 848046`, `code=exited, status=1/FAILURE`, `CPU: 7ms`), repetindo a falha de `20:37:48 UTC` (`PID 847313`, `CPU: 5ms`).
3. **Bloqueio de acesso administrativo**: a política `sudoers` temporária foi corretamente removida ao encerrar a Fase 8.D, e `sudo -n` retornou `sudo: a password is required`. Respeitando integralmente a proibição de contornar permissões, o script `/usr/local/sbin/lectoria-auto-update` (`0700 root:root`), os logs `journalctl`, o `docker inspect` local e o conteúdo interno dos diretórios de backup (`0700 root:root`) **não puderam ser lidos**.
4. **Recomendação**: manter o merge do PR #1 **`BLOCKED`** até que o proprietário execute os comandos somente-leitura com `sudo` listados na Seção 15 para identificar a causa exata do `exit 1` em `5–7 ms`.

---

## 2. Estado Inicial da VPS (`lectoria-vps`)

- **Aplicação**: `http://127.0.0.1:8765/api/health` → `{"status":"healthy","app_name":"Lectoria","version":"1.9.1"}`.
- **Banco SQLite**: `/srv/nosreme/lectoria/data/lectoria.db` (`6,5 MiB`, `PRAGMA integrity_check = ok`, `schema_migrations = 1..13`).
- **Backup remoto Restic/Rclone**: `nosreme-restic-backup.timer` confirmado **`disabled` / `inactive`**.
- **Backup local**: `lectoria-backup.timer` confirmado **`enabled` / `active (waiting)`** (`NEXT: Tue 2026-11-24 03:00:00 UTC`).

---

## 3. Diagnóstico do Auto-Update

### Fatos Verificados (Sem `sudo`)
- `lectoria-auto-update.timer`: `enabled`, `active (waiting)` desde `2026-11-08 22:07:59 UTC`; `OnUnitInactiveSec=15min`; próximo disparo em `2026-11-23 21:07:52 UTC`.
- `lectoria-auto-update.service`: `Type=oneshot`, `User=root`, `ExecStart=/usr/local/sbin/lectoria-auto-update`, `TimeoutStartSec=15min`.
- **Últimas execuções observadas**:
  - `2026-11-23 20:37:48 UTC` — `PID 847313` → **`failed (code=exited, status=1/FAILURE, CPU: 5ms)`**
  - `2026-11-23 20:52:52 UTC` — `PID 848046` → **`failed (code=exited, status=1/FAILURE, CPU: 7ms)`**
- **Evidência indireta de execuções anteriores bem-sucedidas**: existem diretórios de backup em `/srv/nosreme/lectoria/backups/` criados exatamente nas janelas de atualização anteriores (`2026-11-23T14-57-40Z` na `v1.9.0`, `2026-11-23T16-14-30Z` na `v1.9.1` run `#45210832288` e `2026-11-23T19-48-19Z` na `v1.9.1` run `#45248554299`).

### Limitações (Requer `sudo`)
- Não foi possível ler `/usr/local/sbin/lectoria-auto-update` (`-rwx------ root:root`, `2.130 B`) nem `journalctl -u lectoria-auto-update.service`.

---

## 4. Causa da Falha (Fatos vs. Hipóteses)

- **Causa raiz confirmada**: **NÃO IDENTIFICADA** (sem acesso aos logs/script por ausência de `sudo -n`).
- **Hipótese técnica (a confirmar via `journalctl` + leitura do script)**: o tempo de CPU de apenas **`5–7 ms`** antes do `exit 1` indica aborto imediato no início do script — compatível com: (a) arquivo de lock residual (`flock`/lockfile), (b) falha em pré-condição de arquivo/diretório ou comando inicial, ou (c) erro de ambiente/permissão. **Não atribuir como fato até leitura dos logs.**

---

## 5. Histórico de Execuções Observável

| Horário (UTC) | Evento Observado | Fonte |
| :--- | :--- | :--- |
| `2026-11-23 14:57:40Z` | Backup pré-update (`v1.9.0`, migração v12 → v13 concluída às `14:57:49Z`) | `/srv/nosreme/lectoria/backups/` + `schema_migrations` |
| `2026-11-23 16:14:30Z` | Backup pré-update (`v1.9.1`, run `#45210832288`) | `/srv/nosreme/lectoria/backups/` |
| `2026-11-23 19:48:19Z` | Backup pré-update (`v1.9.1`, run `#45248554299`) | `/srv/nosreme/lectoria/backups/` |
| `2026-11-23 20:37:48Z` | **`failed (exit 1, CPU 5ms)`** | `systemctl status lectoria-auto-update.service` |
| `2026-11-23 20:52:52Z` | **`failed (exit 1, CPU 7ms)`** | `systemctl status lectoria-auto-update.service` |

*(Histórico completo das últimas 10 execuções pendente de `sudo journalctl -u lectoria-auto-update.service -n 80 --no-pager`.)*

---

## 6. Avaliação de Segurança do Script de Auto-Update

- **Comprovado indiretamente**: o fluxo na VPS criou backups antes de atualizações (`14:57Z`, `16:14Z`, `19:48Z`) e preservou `/storage/books`, `/storage/data` e `/storage/backups`.
- **Não comprovado (pendente de leitura do script com `sudo`)**: comparação por digest vs. tag, prevenção de reinício desnecessário, healthcheck pós-recriação, preservação de referência à imagem anterior, rollback automático, lock de concorrência (`flock`) e tratamento de incompatibilidade de schema SQLite.

---

## 7. Estado do Docker em Produção

- **Verificado sem `sudo`**:
  - `/srv/nosreme/lectoria/compose.yaml`: `image: ghcr.io/nosremekk/lectoria:stable`, `container_name: lectoria`, `restart: unless-stopped`, `mem_limit: 768m`, porta `127.0.0.1:8765:8765`, bind mounts `./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups`.
  - Processo ativo (observado em 11.1/10.2): 1 worker `uvicorn`, `~65–82 MiB` RAM, `0` eventos OOM.
- **Não verificado (requer `sudo` para `/var/run/docker.sock`)**: `docker ps`, `docker inspect` (`ImageID` local exato, `RepoDigests`, `RestartCount`, `State.Health.Status`) e `docker logs lectoria`.

---

## 8. Identificadores de Imagem Conhecidos (GHCR — Verificados)

| Identificador | Valor Verificado no GHCR (`v1.9.1` / `1.9.1` / `stable`) |
| :--- | :--- |
| **OCI Image Index Digest** (atual — run `#45248554299`) | `sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253` |
| **OCI Image Index Digest** (anterior — run `#45210832288`) | `sha256:36af447d240272bab0cefb97bc0972534b77c87fe98d0d3cd383de9b3e4d3982` |
| **Manifesto `linux/amd64`** (idêntico em ambos os runs) | `sha256:b3ca97d1ba4c7fac50356e97e26fd0c2bd1644526926cd8796781541c3df0af9` |
| **Config Digest (`Image ID`)** (idêntico em ambos os runs) | `sha256:e4607f2adb7ee9179dd7db87204a6f12129151a4383f1b76b42bd6bf5eb346dd` |
| **Image ID local efetivo no container da VPS** | *Pendente de `sudo docker inspect lectoria --format '{{.Image}}'`* |

---

## 9. Estado do SQLite (Leitura Segura `mode=ro&immutable=1`)

- **Arquivo**: `/srv/nosreme/lectoria/data/lectoria.db` (`6.5M`, mtime `Nov 23 20:57`, sem arquivos `-wal`/`-shm` residuais visíveis no momento).
- **Integridade**: `PRAGMA integrity_check` → **`ok`**.
- **Histórico completo de `schema_migrations`**: versões `1` a `13` contínuas (`v1`–`v7` em `2026-11-07 18:32:14`; `v8` em `2026-11-08`; `v9` em `2026-11-09`; `v10` em `2026-11-10`; `v11` em `2026-11-14`; `v12` em `2026-11-17`; `v13` em `2026-11-23 14:57:49`).

---

## 10. Estado dos Backups

- **Serviço e Timer**: `lectoria-backup.timer` **`enabled` / `active (waiting)`** (`OnCalendar=*-*-* 03:00:00`, próximo em `Tue 2026-11-24 03:00:00 UTC`).
- **Arquivos existentes**: 18 diretórios timestamped em `/srv/nosreme/lectoria/backups/` (mais recente `2026-11-23T19-48-19Z`; diário `2026-11-23T03-00-37Z`), permissões `drwx------ root:root`.
- **Distinção obrigatória**:
  - **Existência dos diretórios de backup**: **CONFIRMADA**.
  - **Sucesso registrado pelo serviço (`journalctl`), integridade dos `.db` dentro dos diretórios e teste de restauração**: **NÃO VERIFICADOS** nesta fase devido à restrição de `sudo` (`drwx------ root:root`). Não declaramos esses backups restauráveis até validação com `sudo`.
- **Restic/Rclone**: permanece **`disabled` / `inactive`** (conforme Fase 8.D).

---

## 11. Riscos Operacionais

1. **Falha recorrente (`exit 1` a cada 15 min) em `lectoria-auto-update.service`**: operar um merge que atualiza `:stable` sem saber por que o atualizador está falhando em `5–7 ms` viola o princípio de previsibilidade operacional.
2. **Desconhecimento do comportamento interno do script `/usr/local/sbin/lectoria-auto-update`**: não sabemos se ele faz rollback em falha ou se trava em caso de erro parcial.
3. **Atualização automática pelo PR #1**: mesmo sem bump de `APP_VERSION` (`1.9.1`), os novos rótulos OCI no `Dockerfile` gerarão um novo digest em `:stable`.

---

## 12. Plano de Integração Controlada (NÃO EXECUTAR NESTA FASE)

### A. Pré-condições e Diagnóstico (Somente-Leitura — Executar com `sudo` na VPS)
```bash
# 1. Inspecionar script e logs da falha de 20:37Z / 20:52Z
sudo cat /usr/local/sbin/lectoria-auto-update
sudo journalctl -u lectoria-auto-update.service -n 80 --no-pager

# 2. Registrar Image ID e RepoDigests atuais do container
sudo docker ps --filter name=lectoria
sudo docker inspect lectoria --format 'ImageID={{.Image}} ConfigImage={{.Config.Image}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} Restarts={{.RestartCount}}'
sudo docker image inspect "$(sudo docker inspect lectoria --format '{{.Image}}')" --format 'Id={{.Id}} RepoDigests={{.RepoDigests}}'

# 3. Verificar conteúdo e integridade do backup mais recente
LATEST_BKP=$(sudo ls -1dt /srv/nosreme/lectoria/backups/20* | head -n 1)
sudo ls -lh "$LATEST_BKP"
sudo python3 -c "import glob, sqlite3; p=glob.glob('$LATEST_BKP/*.db')[0]; c=sqlite3.connect(f'file:{p}?mode=ro&immutable=1', uri=True); print(p, c.execute('PRAGMA integrity_check;').fetchone()[0])"
```

### B. Antes do Merge (Alteração de Estado — Somente Após Autorização)
```bash
# Pausar temporariamente o timer e confirmar ausência de execução em andamento
sudo systemctl stop lectoria-auto-update.timer
systemctl status lectoria-auto-update.service --no-pager
```

### C. Durante o Merge (GitHub — Somente Após Autorização)
1. Fazer merge do [PR #1](https://github.com/Nosremekk/Lectoria/pull/1) em `main` (sem criar tags Git).
2. Acompanhar `publish.yml` (`continuous`): confirmar que `ci.yml` **não** dispara em duplicidade; confirmar `preflight` → `_test-suite.yml` → build `:sha-<short>` → smoke test → promoção `:stable`.
3. Confirmar no `GITHUB_STEP_SUMMARY` e na API do GHCR que `:v1.9.1` e `:1.9.1` **permaneceram** em `sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253`.

### D. Depois do Merge (Atualização Controlada e Verificação na VPS)
```bash
# Atualização controlada (alteração de estado)
cd /srv/nosreme/lectoria && sudo docker compose pull && sudo docker compose up -d

# Verificação somente-leitura pós-update
curl -fsS http://127.0.0.1:8765/api/health
python3 -c "import sqlite3; c=sqlite3.connect('file:/srv/nosreme/lectoria/data/lectoria.db?mode=ro&immutable=1', uri=True); print('integrity:', c.execute('PRAGMA integrity_check;').fetchone()[0], 'schema:', c.execute('SELECT MAX(version) FROM schema_migrations;').fetchone()[0])"
# Somente após validação + correção da falha do script:
sudo systemctl start lectoria-auto-update.timer
```

---

## 13. Plano de Recuperação (Rollback)

```bash
# Caso o novo container falhe após o merge do PR #1 (Schema permanece v13 — compatível):
cd /srv/nosreme/lectoria
# 1. Fixar temporariamente no compose.yaml o digest conhecido da v1.9.1:
#    image: ghcr.io/nosremekk/lectoria@sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253
# 2. Subir o container anterior:
sudo docker compose up -d
# 3. Confirmar saúde e integridade:
curl -fsS http://127.0.0.1:8765/api/health
```
> **Atenção para releases futuras com migração de schema**: rollback de container **não** equivale a rollback de banco. Se uma release futura aplicar migração `v13 → v14` antes de falhar, será obrigatório parar o container, restaurar o snapshot SQLite pré-update verificado e só então subir a imagem anterior.

---

## 14. Recomendações para Instalações Self-Hosted

Documentar explicitamente 3 estratégias de imagem para administradores terceiros (Docker local, servidor doméstico, LAN, Tailscale, VPS própria, reverse proxy HTTPS), deixando a atualização automática como **escolha explícita do administrador**:
1. **Versão fixa (`ghcr.io/nosremekk/lectoria:vX.Y.Z`)** — recomendada por padrão para terceiros (estabilidade previsível, upgrade manual consciente).
2. **Canal atualizado (`ghcr.io/nosremekk/lectoria:stable`)** — para administradores que desejam acompanhar automaticamente a `main` validada por testes + smoke test.
3. **Digest imutável (`ghcr.io/nosremekk/lectoria@sha256:...`)** — para máxima reprodutibilidade e ambientes auditados.

---

## 15. Bloqueios Restantes

- **Único bloqueio para encerrar o diagnóstico e autorizar o merge**: ausência de leitura com `sudo` na VPS para inspecionar `/usr/local/sbin/lectoria-auto-update`, `journalctl -u lectoria-auto-update.service` (falhas `20:37:48Z` e `20:52:52Z`), `docker inspect lectoria` e o conteúdo interno do backup mais recente.

---

## 16. Decisão Recomendada

**`BLOCKED`** — aguardar o proprietário executar o bloco **A (Somente-Leitura)** da Seção 12 na VPS (ou conceder leitura temporária) para esclarecer a causa do `exit 1` recorrente em `lectoria-auto-update.service` antes de autorizar o merge controlado do PR #1.
