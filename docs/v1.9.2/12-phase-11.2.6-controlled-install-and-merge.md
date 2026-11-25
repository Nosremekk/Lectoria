# Lectoria — Fase 11.2.6: Relatório Final de Instalação Controlada, Merge e Validação Operacional

- **Status Final**: **`PHASE_11_2_COMPLETED`**
- **Data de Conclusão**: `2026-11-24 20:38 UTC` (`17:38 -03:00`)
- **Pull Request Integrado**: [PR #1 — `refactor(ci): harden GHCR releases and deduplicate workflows`](https://github.com/Nosremekk/Lectoria/pull/1)
- **Commit Integrado em `main`**: `95274701282d0ff8f16b97a4aa621ab76fe72d7e` (`9527470`)
- **Versão em Produção**: **`1.9.1`** · **Schema SQLite**: **`13`** (sem migração, sem tags novas, sem release `v1.9.2`)

---

## 1. Consolidação dos Checkpoints A–J

| Checkpoint | Escopo | Evidência Principal | Resultado |
| :---: | :--- | :--- | :---: |
| **A** | Validação final do PR #1 | `b2f53ca` verde em `#45280421685` (`550 s`) e `#45280408562` (`822 s`, `384/384` testes); `main` em `dda6f27`; SHA-256 do script: `c8cdc5e7b1f6cc77505ba88717bdf10368537dab4032a284af533da6b3ace974` | **APROVADO** |
| **B** | Estado inicial da VPS | `/api/health: healthy (1.9.1)`, disco `39%` (`30G` livres), SQLite `ok` / Schema `13`, `lectoria-backup.service` `Type=oneshot`, Restic `disabled/inactive` | **APROVADO** |
| **C** | Pausa controlada do timer | `lectoria-auto-update.timer` pausado (`inactive`), nenhuma execução ativa | **APROVADO** |
| **D** | Preservação administrativa | Script antigo, units, estado e `blocked` preservados em `/root/lectoria-updater-backup-20261124T183111Z` (`0700 root:root`) | **APROVADO** |
| **E** | Instalação e configuração | Script instalado em `/usr/local/sbin/lectoria-auto-update` (`0700 root:root`, SHA-256 verificado pós-instalação), `/etc/default/lectoria-auto-update` (`0600 root:root`, `ENV_VARS_MATCH_SCRIPT=YES`, `LECTORIA_REQUIRE_BACKUP=1`, `LECTORIA_BACKUP_CMD="systemctl start lectoria-backup.service"`), drop-in `env.conf` aplicado | **APROVADO** |
| **F** | Validação do incidente original (`18:31 UTC`) | `CURRENT_ID=36af447d...` vs `TARGET_ID=da4dca9c...`, ambos com `exec_fp=sha256:dbca2369f77866e2739466ea505baf7ba0fa264af83e9cec6cacc4bafbe58d98`; **`NO_EXECUTABLE_CHANGE`**, `SVC_EXIT_CODE=0`, `CONTAINER_ID_MATCH=YES`, `CREATED_MATCH=YES`, `AFTER_RESTARTS=0`, `BLOCKED_AFTER=NO` | **APROVADO** |
| **G** | Backup pré-merge e autorização (`18:34:59 UTC`) | `/srv/nosreme/lectoria/backups/2026-11-24T18-34-59Z` (`41 MB`: `lectoria.db` `6,5 MB`, `covers.tar.gz` `35 MB` / `213` entradas, `commit.txt`); `PRAGMA integrity_check = ok`, `SCHEMA_MAX = 13`, `books = 238`, `users = 5`; autorização expressa do proprietário registrada | **APROVADO** |
| **H** | Merge em `main` e publicação GHCR (`18:36–18:53 UTC`) | Merge `9527470`; `ci.yml` **não** disparou em duplicidade; [Run #45461385371](https://github.com/Nosremekk/Lectoria/actions/runs/45461385371) (`888 s`) **`completed/success`**: `preflight` → `_test-suite.yml` (384 testes) → build `:sha-9527470` → smoke test `@digest` → promoção `:stable`; `:v1.9.1` e `:1.9.1` preservadas intactas | **APROVADO** |
| **I** | Deploy controlado na VPS (`19:18:51–19:19:15 UTC`) | `REMOTE_DIGEST_MATCH=YES`, `EXECUTABLE_CHANGE_DETECTED`, backup obrigatório `/srv/nosreme/lectoria/backups/2026-11-24T19-19-03Z`, `UPDATE_SUCCESS`, `OCI_REVISION_MATCH=YES`, novo container `6fca4563...`, `Health=healthy`, `Restarts=0`, `BLOCKED_EXISTS=NO`, SQLite `mode=ro` `ok` + contagens idênticas antes/depois, validação básica no navegador OK | **APROVADO** |
| **J** | Reativação do timer e execução automática (`20:38:46–20:38:48 UTC`) | Backup preventivo `19-19-03Z` validado (`PRAGMA integrity_check = ok`, `213` capas); timer `enabled / active (waiting)`; execução automática disparada pelo systemd (`ExecMainStatus=0`, `Result=success`, `2 s`) com **`NO_EXECUTABLE_CHANGE`**, `CONTAINER_ID_UNCHANGED=YES`, `BACKUP_UNCHANGED=YES`, `BLOCKED_EXISTS=NO`, `RESTARTS=0`, `HEALTH=healthy` | **APROVADO** |

---

## 2. Validação Real em Produção dos 3 Cenários do Atualizador

1. **Alteração isolada do OCI Index (Checkpoint F)**: `36af447d...` vs `da4dca9c...` com mesmo `exec_fp` (`sha256:dbca2369...`) → **`NO_EXECUTABLE_CHANGE`**, zero recriação, zero backup redundante, `exit 0`.
2. **Alteração efetiva da imagem executável (Checkpoint I)**: `sha256:dbca2369...` → `sha256:51fc906f1c435d3986341cddd8903f6bf698552ad1e4da51c38ca3820c11df4a` (novo `Dockerfile` com labels OCI de rastreabilidade do commit `9527470`) → **`EXECUTABLE_CHANGE_DETECTED`** → backup obrigatório (`19-19-03Z`) → `last-good.json` → `docker compose up -d --no-deps lectoria` → verificação de `exec_fp` + `healthy` → **`UPDATE_SUCCESS`**.
3. **Ausência de atualização em execução automática do timer (Checkpoint J)**: `2026-11-24 20:38:46 UTC` → **`NO_EXECUTABLE_CHANGE`** em `2 s`, mesmo container `6fca4563...`, zero backup extra, `Result=success`.

---

## 3. Validação do Novo Pipeline CI/CD em `main`

- **Desduplicação confirmada**: `ci.yml` não executou no push de merge em `main`; apenas `publish.yml` ([Run #45461385371](https://github.com/Nosremekk/Lectoria/actions/runs/45461385371), `888 s`) rodou.
- **Digests e Imutabilidade GHCR**:
  - **Anterior (v1.9.1 imutável — `:v1.9.1` e `:1.9.1`)**: `sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253` (**preservado sem sobrescrita**).
  - **Novo (`:sha-9527470` e `:stable`)**: `sha256:6659b146d3205d9b5543eeb58b973a1c8c7cae1b3a0691533a7c0ad4cd200cce` (`amd64`: `sha256:dd27533b...`, `config`: `sha256:fd99d24a...`, `org.opencontainers.image.revision = 95274701282d0ff8f16b97a4aa621ab76fe72d7e`, `version = 1.9.1`).

---

## 4. Estado do SQLite, Backups e Referência de Recuperação

- **SQLite ativo (`mode=ro` sem `immutable=1`, respeitando WAL)**: `PRAGMA integrity_check = ok`, `SCHEMA_VERSION = 13`. Contagens idênticas antes e depois do deploy: `books=238`, `users=5`, `reading_progress=54`, `highlights=0`, `notes=19`, `collections=0`, `concepts=4`, `reflections=1`, `knowledge_links=4`.
- **Backups locais**:
  - Pré-merge: `/srv/nosreme/lectoria/backups/2026-11-24T18-34-59Z` (`41 MB`, `PRAGMA integrity_check = ok`, `213` entradas em `covers.tar.gz`).
  - Preventivo automático do atualizador: `/srv/nosreme/lectoria/backups/2026-11-24T19-19-03Z` (`41 MB`, `PRAGMA integrity_check = ok`, `213` entradas em `covers.tar.gz`).
  - `lectoria-backup.timer`: `enabled / active` · `nosreme-restic-backup.timer`: `disabled / inactive`.
  - **Nota metodológica**: os backups passaram por validação estrutural (`PRAGMA integrity_check` e listagem `tar -tzf`), o que **não** equivale a uma restauração integral testada de toda a biblioteca.
- **Referência imutável de recuperação e limitação conhecida de `last-good.json`**:
  - A imagem anterior em produção (`36af447d...`) encontrava-se untagged localmente (`RepoDigests=[]`), registrando `previous_immutable_recoverable=false` em `last-good.json` conforme previsto. A referência imutável externa equivalente permanece preservada no GHCR: `ghcr.io/nosremekk/lectoria@sha256:da4dca9cac06d28817d62b0d48224c5063a3e993e115b39682d2e766c1b6a253`.
  - O novo container (`6fca4563...`) já possui `RepoDigests` preenchido (`ghcr.io/nosremekk/lectoria@sha256:6659b146...`), portanto próximas atualizações registrarão `previous_immutable_recoverable=true`.
- **Procedimento de recuperação do atualizador anterior** (sem alterar banco ou container): restaurar a partir de `/root/lectoria-updater-backup-20261124T183111Z`.

---

## 5. Limitações Remanescentes e Política de Commit Documental

- **Promoção de tag `vX.Y.Z` (`promote-release-tag` em `publish.yml`)**: ainda não executada em produção nesta fase (nenhuma tag criada); será validada pela primeira vez na release `v1.9.2` (Fase 11.5).
- **Restauração integral de backup**: apenas integridade estrutural foi validada; teste de restauração ponta a ponta em ambiente descartável segue no escopo operacional da Fase 11.4.
- **Política deste encerramento**: este relatório e a atualização do roadmap permanecem locais sem push isolado em `main`, evitando disparar uma publicação e atualização desnecessária do container em produção; serão integrados junto ao próximo ciclo funcional (Fase 11.3).
