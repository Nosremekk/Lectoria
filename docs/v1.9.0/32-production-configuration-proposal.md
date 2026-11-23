# Lectoria v1.9.0 — Proposta de Configuração para Produção (Baseada na Auditoria da VPS)

- **Data**: 2026-11-22
- **Base de Evidências**: Auditoria real em [31-phase-8c-vps-audit.md](31-phase-8c-vps-audit.md) e benchmarks em [28-performance-benchmarks.md](28-performance-benchmarks.md).
- **Status**: **PROPOSTA DOCUMENTAL — NÃO APLICADA** (aguarda autorização explícita do proprietário e resolução dos bloqueadores antes da Fase 9).

---

## 1. Configuração Atual Verificada vs Configuração Recomendada

### 1.1 Resumo das Recomendações Classificadas

| Prioridade | Recomendação | Justificativa Baseada em Evidências |
| :--- | :--- | :--- |
| **OBRIGATÓRIA** | **Preservar os bind mounts e caminhos `/storage/*`** (`./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups` e variáveis `LECTORIA_*_DIR=/storage/*`) | Evita iniciar a v1.9.0 com banco SQLite vazio. **Nunca** substituir por volumes nomeados `/app/*`. |
| **OBRIGATÓRIA** | **Preservar `"127.0.0.1:8765:8765"` e o acesso via `tailscale serve`** | O tráfego privado entra exclusivamente via `tailscale serve` → `http://127.0.0.1:8765` (o Caddy é exclusivo de `site`/`blog`). |
| **OBRIGATÓRIA** | **Remover `cpus: 1.5` (não exceder `1` vCPU)** | A VPS possui `1` vCPU (`nproc = 1`). Não definir `cpus` acima de `1.0` (recomenda-se omitir ou usar no máximo `cpus: 0.9` se quiser reservar 10% para SO/Tailscale/Caddy). |
| **OBRIGATÓRIA** | **Diagnosticar e normalizar `nosreme-restic-backup.service` antes da migração `v12 → v13`** | Serviço em `activating (start)` há ~71h10m (`TimeoutStartSec=0`), travando o timer diário (`NEXT = -`). Não migrar schema sem backup remoto recente e confirmado. |
| **OBRIGATÓRIA** | **Controlar `lectoria-auto-update.timer` antes de qualquer push em `main` / `:stable`** | O timer executa a cada 15 min sobre `:stable`. Pausar/desabilitar temporariamente ou fixar tag imutável (`:v1.8.0` → `:v1.9.0`) durante a janela de release impede upgrade não supervisionado. |
| **OBRIGATÓRIA** | **Validar `ENABLE_FTS5` em imagem Docker isolada (`python:3.12-slim`) antes do upgrade** | Schema v13 exige `knowledge_fts` (`unicode61 remove_diacritics 2`). A validação isolada sem volumes de produção garante compatibilidade antes de tocar em `lectoria.db`. |
| **OBRIGATÓRIA** | **Manter execução como `root` na v1.9.0 (ou planejar migração de ownership separada)** | Todos os arquivos em `./data` (`lectoria.db`, `covers/`) pertencem a `root:root`. Alterar o `Dockerfile` para não-root sem `chown` coordenado quebraria escrita no SQLite. |
| **RECOMENDADA** | **Elevar `mem_limit` de `768m` para `1g` (1.024 MiB) com `mem_reservation: 256m`** | Embora `anon` atual seja `171,3 MiB` (e `768m` seja viável sem OOM), a v1.9.0 adiciona exportações paralelas (`VmHWM` local de `170,7 MiB`) e FTS5. Com `2,9 GiB` livres no host, `1g` amplia o page cache quente e dá margem de segurança sem competir com `nosreme-restic-backup` (`MemoryHigh=1536M`). |
| **RECOMENDADA** | **Adicionar `healthcheck` explícito com `start_period: 30s`, rotação de logs (`json-file` `10m`×3), `init: true`, `pids_limit: 128` e `no-new-privileges:true`** | `start_period: 30s` acomoda a migração `v12 → v13` + backfill FTS5; rotação de logs evita crescimento ilimitado no disco de 48 GB. |
| **RECOMENDADA** | **Ativar `LECTORIA_SECURE_COOKIES: "true"`** | Como o acesso real é 100% HTTPS via `tailscale serve`, cookies `Secure` endurecem a sessão sem quebrar conectividade. |
| **RECOMENDADA** | **Usar tag imutável (`ghcr.io/nosremekk/lectoria:v1.9.0`) na janela de upgrade** | Facilita rollback determinístico (`v1.9.0` → `v1.8.0`) e desacopla o deploy do ciclo automático de `:stable`. |
| **OPCIONAL** | **Avaliar montagem `:ro` em `./books`** | Só adotar após o proprietário confirmar que nenhum recurso em produção (upload ou otimização) escreve em `/storage/books`. |
| **OPCIONAL** | **Definir `TimeoutStartSec` razoável (ex.: `4h`–`6h`) em `nosreme-restic-backup.service`** | Evita que uma execução travada (ex.: throttling do Google Drive) bloqueie silenciosamente o timer diário por dias. |

---

## 2. Proposta de `compose.yaml` Adaptada à VPS Real (Para Revisão Futura)

> **Não aplicada.** Mantém 100% dos caminhos `/storage/*` e bind mounts da VPS real:

```yaml
services:
  lectoria:
    # Durante a janela de release, preferir tag imutável (`:v1.9.0`);
    # retornar a `:stable` somente após validação completa pós-upgrade.
    image: ghcr.io/nosremekk/lectoria:v1.9.0
    container_name: lectoria
    restart: unless-stopped
    init: true

    environment:
      LECTORIA_APP_NAME: Lectoria
      LECTORIA_HOST: 0.0.0.0
      LECTORIA_PORT: "8765"
      LECTORIA_LIBRARY_DIR: /storage/books
      LECTORIA_DATA_DIR: /storage/data
      LECTORIA_DB_PATH: /storage/data/lectoria.db
      LECTORIA_COVERS_DIR: /storage/data/covers
      LECTORIA_BACKUPS_DIR: /storage/backups
      LECTORIA_SECURE_COOKIES: "true"
      TZ: America/Sao_Paulo

    volumes:
      - ./books:/storage/books
      - ./data:/storage/data
      - ./backups:/storage/backups

    ports:
      - "127.0.0.1:8765:8765"

    mem_limit: 1g
    mem_reservation: 256m
    pids_limit: 128
    security_opt:
      - no-new-privileges:true

    healthcheck:
      test:
        [
          "CMD",
          "python3",
          "-c",
          "import urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2); assert r.status == 200",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

---

## 3. Plano de Validação Isolada (Executar Somente Após Aprovação Futura)

1. **Verificação privilegiada do backup remoto pelo proprietário na VPS**:
   - Inspecionar `sudo journalctl -u nosreme-restic-backup.service -n 50 --no-pager` para verificar se há `RATE_LIMIT_EXCEEDED` do Google Drive ou bloqueio de rede/lock.
   - Resolver a execução atual e confirmar um snapshot Restic íntegro antes de qualquer migração.
2. **Validação isolada de FTS5 na imagem Docker (sem volumes de produção)**:
   ```bash
   docker run --rm ghcr.io/nosremekk/lectoria:v1.9.0 python3 -c "import sqlite3; c=sqlite3.connect(':memory:'); opts={r[0] for r in c.execute('PRAGMA compile_options;')}; assert 'ENABLE_FTS5' in opts, opts; c.execute(\"CREATE VIRTUAL TABLE t USING fts5(x, tokenize='unicode61 remove_diacritics 2')\"); print('SQLite:', sqlite3.sqlite_version, 'FTS5 OK')"
   ```
3. **Teste de migração `v12 → v13` em cópia isolada**:
   - Usar um backup SQLite recente (gerado via SQLite Online Backup API) montado em diretório temporário isolado, nunca no `/srv/nosreme/lectoria/data/lectoria.db` de produção, para validar `PRAGMA integrity_check == ok` e tempo real de backfill FTS5 com os 5,5 MB reais.

---

## 4. Estratégia de Prevenção de Publicação Acidental e Rollback Seguro

### 4.1 Controle da Publicação Automática (`.github/workflows/publish.yml`)
- O workflow `publish.yml` dispara em qualquer `push` na branch `main` ou tag `v*`, publicando automaticamente `:vX.Y.Z`, `:X.Y.Z` e **`:stable`**.
- Como `lectoria-auto-update.timer` verifica `:stable` a cada 15 minutos na VPS:
  1. **Antes** de fazer merge para `main`, pausar temporariamente `lectoria-auto-update.timer` na VPS **ou** fixar `image: ghcr.io/nosremekk/lectoria:v1.8.0` no `/srv/nosreme/lectoria/compose.yaml`.
  2. Executar o upgrade da v1.9.0 em janela controlada, com backup local + remoto confirmados.
  3. Somente após validação completa, decidir se retorna para `:stable` e reativa o timer.

### 4.2 Rollback Seguro (`v1.9.0 / Schema v13` → `v1.8.0 / Schema v12`)
- **Nunca apagar `lectoria.db-wal` / `lectoria.db-shm` com o container em execução**.
- **Rollback Imediato (antes de uso em produção)**:
  1. Parar o container (`docker compose down`) para garantir fechamento limpo do SQLite.
  2. Mover `lectoria.db` (e eventuais `-wal`/`-shm`) da v1.9.0 para uma cópia forense (`lectoria_v13_aborted_<timestamp>.db`).
  3. Copiar o backup íntegro pré-migração (`/srv/nosreme/lectoria/data/lectoria_pre_migration_v12_to_v13.db.bak` ou o backup gerado por `lectoria-backup.service`) para `lectoria.db`.
  4. Apontar `compose.yaml` para `ghcr.io/nosremekk/lectoria:v1.8.0` e subir (`docker compose up -d`).
  5. Validar `/api/health` (`version: 1.8.0`), login, livros, capas e integridade do SQLite.
- **Rollback Tardio (após criação de dados no Atlas v1.9.0)**:
  1. **Não sobrescrever silenciosamente** o banco v13: antes de parar ou reverter, exportar o conhecimento do Atlas (`/api/knowledge/export?format=json`) ou gerar cópia consistente via SQLite Online Backup API do banco v13.
  2. Somente após preservar os dados novos e avisar os usuários, executar os passos 1–5 do rollback imediato.
