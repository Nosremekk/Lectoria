# Lectoria v1.9.0 — Prontidão para Produção, Runbook VPS e Plano de Rollback (Fase 8)

- **Data**: 2026-11-22
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Status da Etapa C (VPS)**: **NÃO EXECUTADA** — aguarda autorização explícita do proprietário e conclusão da Fase 9 (versionamento `1.9.0` e release).
- **Arquivo candidato preparado localmente**: [`docker-compose.v190-candidate.yml`](../../docker-compose.v190-candidate.yml) (`docker-compose.yml` e `Dockerfile` reais permanecem intocados).

---

## 1. Pré-requisitos Obrigatórios Antes de Qualquer Alteração em Produção

1. Concluir a **Fase 9** (sincronização de `APP_VERSION = "1.9.0"`, cache-busting `?v=1.9.0`, `CHANGELOG.md`, docs canônicos e bateria final 100% verde).
2. Executar a **Etapa C (Inspeção Read-Only na VPS)** via SSH sobre Tailscale antes do deploy.
3. Confirmar backup verificado (local + remoto via Restic/Rclone) do banco v12 (`lectoria.db`) antes de subir a imagem v1.9.0.
4. Confirmar disponibilidade de `ENABLE_FTS5` dentro da imagem publicada `ghcr.io/nosremekk/lectoria:v1.9.0` (ou `:stable`).

---

## 2. Runbook Classificado para a VPS (Etapa C — NÃO EXECUTADO)

Todos os comandos abaixo são **propostas documentadas** para execução futura autorizada pelo proprietário.

### 2.1 Comandos Somente-Leitura (Baixo Custo — Seguros para Diagnóstico Inicial)
```bash
# 1. Recursos gerais do host (RAM, swap, disco, carga)
free -h && df -h && uptime

# 2. Estado atual dos contêineres, imagem, healthcheck e limites configurados
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
docker inspect lectoria --format '{{json .HostConfig.Memory}} {{json .HostConfig.NanoCpus}} {{json .State.Health.Status}}'

# 3. Consumo instantâneo de CPU/RAM dos contêineres
docker stats --no-stream

# 4. Verificar tamanho real do banco SQLite, WAL e backups no volume
docker exec lectoria ls -lh /app/data/ /app/backups/

# 5. Verificar SQLite e FTS5 dentro do contêiner em execução
docker exec lectoria python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); print(sqlite3.sqlite_version, 'FTS5:', 'ENABLE_FTS5' in {r[0] for r in c.execute('PRAGMA compile_options;')})"

# 6. Verificar eventos OOM recentes no kernel e logs do contêiner
dmesg -T | grep -i -E 'oom|killed process' | tail -n 20
docker logs --tail 80 lectoria

# 7. Verificar serviços systemd/timers de backup (Restic/Rclone) e Caddy/UFW
systemctl list-timers --all | grep -i -E 'backup|restic|rclone|lectoria'
systemctl status caddy --no-pager
sudo ufw status
```

### 2.2 Comandos de Verificação de Custo Moderado (Executar em Horário de Baixo Uso)
```bash
# Integridade do banco atual (somente-leitura, mas lê todas as páginas do DB)
docker exec lectoria python3 -c "import sqlite3; c=sqlite3.connect('/app/data/lectoria.db'); print(c.execute('PRAGMA integrity_check;').fetchone(), c.execute('SELECT MAX(version) FROM schema_migrations;').fetchone())"

# Testar restauração de snapshot Restic mais recente em diretório temporário isolado (sem tocar produção)
# restic snapshots --latest 5
```

### 2.3 Comandos que Modificam Estado (Somente com Autorização Explícita na Janela de Release)
1. Criar backup manual pré-upgrade via UI/API ou `docker exec lectoria python3 scripts/backup.py`.
2. Copiar backup pré-upgrade para fora do volume e disparar sincronização remota (Restic/Rclone).
3. Validar `docker-compose.v190-candidate.yml` (adaptando caminhos de volume/rede reais da VPS) via `docker compose -f docker-compose.v190-candidate.yml config`.
4. Baixar a imagem da release (`docker compose pull`) e testar `ENABLE_FTS5` na nova imagem antes de recriar o contêiner:
   ```bash
   docker run --rm ghcr.io/nosremekk/lectoria:v1.9.0 python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); assert 'ENABLE_FTS5' in {r[0] for r in c.execute('PRAGMA compile_options;')}; print('FTS5 OK', sqlite3.sqlite_version)"
   ```
5. Subir a atualização (`docker compose up -d`), acompanhar logs (`docker logs -f lectoria`) até `/api/health` reportar `healthy` e validar acesso HTTPS via Caddy (`nosreme.cloud`).

---

## 3. Configuração Candidata de Contêiner e Patch Não-Root do `Dockerfile`

### 3.1 Diferenças do `docker-compose.v190-candidate.yml` vs `docker-compose.yml`
| Item | `docker-compose.yml` atual | `docker-compose.v190-candidate.yml` |
| :--- | :--- | :--- |
| **Tag da imagem** | `:latest` (inexistente no CI) | `:stable` (ou `:v1.9.0` imutável) |
| **Healthcheck** | `/api/books` (401 → `unhealthy`) | `/api/health` (200 sem auth, `start_period: 30s`) |
| **Bind de porta** | `"8765:8765"` (`0.0.0.0`) | `"127.0.0.1:8765:8765"` (loopback atrás do Caddy) |
| **Limites de recursos** | Nenhum | `mem_limit: 1g`, `mem_reservation: 256m`, `cpus: 1.5`, `pids_limit: 128` |
| **Hardening / init** | Nenhum | `init: true`, `security_opt: [no-new-privileges:true]`, `LECTORIA_SECURE_COOKIES=true` |
| **Rotação de logs** | Sem limite | `json-file` (`max-size: 10m`, `max-file: 3`) |

### 3.2 Patch Candidato Não-Root para `Dockerfile` (Opcional na Fase 9 / Etapa C)
> **Atenção**: Mudar o `USER` para UID 1000 em um volume existente pertencente a `root:root` exige ajustar a posse (`chown -R 1000:1000 /app/data /app/backups`) durante a janela de manutenção, caso contrário o SQLite falhará com `readonly database`.
```dockerfile
RUN groupadd -g 1000 lectoria && useradd -u 1000 -g 1000 -m -s /bin/sh lectoria \
    && chown -R lectoria:lectoria /app
USER lectoria
```

---

## 4. Plano de Rollback Coerente (`v1.9.0 / Schema v13` → `v1.8.0 / Schema v12`)

### Princípio de Segurança (Sem Perda Silenciosa de Dados)
- A migração v12 → v13 é **aditiva** (cria `concepts`, `reflections`, `knowledge_links`, `knowledge_fts` e triggers), mas **não existe downgrade automático seguro** de v13 para v12 caso usuários já tenham criado dados no Atlas após a subida.
- Portanto, o rollback segue **dois cenários explícitos**:

#### Cenário A — Rollback Imediato na Janela de Deploy (Antes de Uso em Produção)
1. Parar o contêiner v1.9.0 (`docker compose down`).
2. Preservar o banco v13 atual em cópia forense (`lectoria_v13_aborted_<timestamp>.db`).
3. Restaurar o arquivo pré-migração íntegro gerado automaticamente (`lectoria_pre_migration_v12_to_v13.db.bak`) ou o backup manual pré-upgrade para `/app/data/lectoria.db`, removendo `lectoria.db-wal` e `lectoria.db-shm`.
4. Fixar a imagem anterior `ghcr.io/nosremekk/lectoria:v1.8.0` no `docker-compose.yml` e subir (`docker compose up -d`).
5. Validar `PRAGMA integrity_check == ok`, `schema_migrations == 12`, `/api/health == 200` e login na v1.8.0.

#### Cenário B — Rollback Tardio (Após Usuários Criarem Dados no Atlas v1.9.0)
1. **Nunca sobrescrever silenciosamente** o banco v13 com o backup v12 sem preservar os dados novos.
2. Antes de restaurar o backup v12, exportar os dados do Atlas (ou arquivar cópia completa do banco v13 com `sqlite3.Connection.backup()`) para permitir reimportação/migração posterior.
3. Comunicar os usuários sobre o retorno temporário à v1.8.0 e seguir os passos 3–5 do Cenário A.

### Rollback de Ajustes de Infraestrutura (Sem Reverter Versão)
- Caso `mem_limit: 1g` ou `cpus: 1.5` apresentem pressão inesperada sob carga real na VPS, basta ajustar o valor no `docker-compose.yml` (ex.: `1.5g` ou remover `cpus`) e executar `docker compose up -d` sem tocar no banco SQLite.
