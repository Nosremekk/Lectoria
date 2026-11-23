# Lectoria v1.9.0 — Handoff da Fase 8.C e Pré-requisitos para a Fase 9

- **Classificação Final da Fase 8.C**: **`PHASE_8C_AUDIT_WITH_LIMITATIONS`**
- **Data**: 2026-11-22
- **Branch Local**: `feature/v1.9.0-connected-knowledge`
- **Estado de Produção**: **Não pronta para release imediata** até a resolução dos bloqueadores operacionais abaixo e conclusão da Fase 9.

---

## 1. Bloqueadores Obrigatórios Antes do Deploy da v1.9.0

| ID | Bloqueador | Tipo | Ação Necessária (Proprietário / Janela Autorizada) |
| :--- | :--- | :--- | :--- |
| **BLK-01** | ~~`nosreme-restic-backup.service` em `activating (start)` há ~71h10m~~ — **RESOLVIDO (Fase 8.D)** | Operacional (Backup) | **Descontinuado voluntariamente** pelo proprietário (`nosreme-restic-backup.timer` confirmado `disabled`/`inactive`, zero processos `restic`/`rclone`, snapshots antigos preservados no Drive). Substituído pelo critério pré-deploy: **backup SQLite local íntegro (`lectoria-backup.service`) + cópia externa manual dos dados essenciais + plano de rollback**. |
| **BLK-02** | **Incompatibilidade do `docker-compose.v190-candidate.yml` com produção** | Configuração | Não usar `docker-compose.v190-candidate.yml` na VPS. Adotar a proposta adaptada de [32-production-configuration-proposal.md](32-production-configuration-proposal.md) preservando bind mounts `./data:/storage/data`, `./backups:/storage/backups`, `./books:/storage/books` e sem `cpus: 1.5`. |
| **BLK-03** | **Risco de deploy automático não supervisionado via `lectoria-auto-update.timer` (15 min)** | Release / CI | Antes de qualquer merge na `main`, pausar o timer na VPS ou fixar `:v1.8.0` em `/srv/nosreme/lectoria/compose.yaml` até a janela controlada de upgrade. |
| **BLK-04** | **Validação de `ENABLE_FTS5` na imagem Docker `python:3.12-slim`** | Compatibilidade | Executar o comando isolado (`docker run --rm ...`) sem volumes de produção assim que a imagem `v1.9.0` for construída/publicada (ou testar na imagem atual `v1.8.0`). |

---

## 2. Pendências de Visibilidade (Não Verificadas sem `sudo`)

1. **`docker inspect` / `docker logs` da Lectoria**: inacessíveis sem permissão em `/var/run/docker.sock`.
2. **Logs do `nosreme-restic-backup.service` e `lectoria-backup.service`**: inacessíveis sem pertencimento a `adm`/`systemd-journal` ou `sudo`.
3. **Conteúdo de `/usr/local/sbin/lectoria-auto-update` e `/usr/local/sbin/lectoria-backup`**: sem permissão de leitura sem `sudo` (confirmar se `lectoria-backup` usa SQLite Online Backup API e qual a política de retenção).
4. **Confirmação direta de `PRAGMA integrity_check` e `schema_migrations == 12`** no `lectoria.db` de produção (a executar em janela autorizada de baixo uso).

---

## 3. Riscos Residuais

- **Host de `1` vCPU compartilhado**: durante exportações pesadas do Atlas ou backup Restic/Rclone simultâneo, pode haver contenção pontual de CPU (já mitigada por `Nice=10` / `IOSchedulingClass=best-effort` no backup e pelo desbloqueio do event loop em `main.py` na Fase 8.B).
- **Ownership `root:root` em `./data`**: manter o container rodando como `root` na v1.9.0 elimina o risco de `readonly database`; migração para usuário não-root deve ser tratada como tarefa operacional isolada futura.
- **Acervo de `6,9 GB` incluído no Restic para Google Drive**: pode explicar lentidão ou throttling em uploads incrementais se muitos arquivos forem tocados/otimizados.

---

## 4. Decisões Necessárias do Proprietário

1. **Normalização do backup remoto (`BLK-01`)**: inspecionar o `journalctl` do `nosreme-restic-backup.service` e confirmar integridade de um snapshot recente.
2. **Política de `mem_limit` na v1.9.0**: manter `768m` (viável, `anon` atual `171 MiB`) ou adotar a recomendação de **`1g`** (`mem_reservation: 256m`) aproveitando os `2,9 GiB` livres no host.
3. **Estratégia da janela de release (`BLK-03`)**: pausar `lectoria-auto-update.timer` ou fixar tag imutável (`:v1.8.0` → `:v1.9.0`) antes do merge na `main`.
4. **Atualização do `docker-compose.v190-candidate.yml` local**: decidir na Fase 9 se deseja alinhar o arquivo candidato local ao layout `/storage/*` da VPS ou mantê-lo apenas como referência documental (junto à proposta em `32-production-configuration-proposal.md`).

---

## 5. Pré-requisitos para Iniciar a Fase 9 (Validação Final e Release `v1.9.0`)

- Revisão e aprovação deste relatório da Fase 8.C pelo proprietário.
- Na Fase 9 (ainda na branch `feature/v1.9.0-connected-knowledge`, sem push na `main` até liberação final):
  1. Sincronizar `config.APP_VERSION = "1.9.0"`, `data-app-version="1.9.0"` e query strings `?v=1.9.0`.
  2. Atualizar `AGENTS.md`, `README.md`, `CHANGELOG.md` e documentação canônica (`docs/*.md`).
  3. Executar bateria completa final (`305+` unitários/integração + `54` E2E, zero falhas e zero skips).

---

## 6. Critérios de Aprovação do Deploy (Gate de Produção — Atualizado na Fase 8.D)

1. **Backup SQLite local íntegro**: confirmar execução recente bem-sucedida de `lectoria-backup.service` (`SQLite: backup íntegro.`) e gerar backup consistente imediatamente antes do upgrade.
2. **Cópia externa manual dos dados essenciais**: transferir para fora da VPS (máquina local do proprietário) cópia verificada do backup SQLite, `data/covers/` e `/srv/nosreme/lectoria/compose.yaml`.
3. **Plano de rollback da migração `v12 → v13` revisado**: pronto para execução segura sem perda silenciosa de dados (conforme `32-production-configuration-proposal.md`).
4. **`BLK-03` ativo**: `lectoria-auto-update.timer` pausado ou imagem fixada em `:v1.8.0` antes do merge na `main`.
5. **`BLK-04` aprovado**: `ENABLE_FTS5` confirmado na imagem Docker `v1.9.0` isolada sem volumes de produção.
6. **`BLK-02` atendido**: `compose.yaml` da VPS preservando integralmente `./data:/storage/data`, `./backups:/storage/backups`, `./books:/storage/books` e `127.0.0.1:8765:8765` (sem `cpus: 1.5`).
