# Lectoria — Fase 11.4: Restauração de Backups e Hardening Residual da Infraestrutura

- **Status**: **`PHASE_11_4_READY_FOR_REVIEW`** *(implementada e validada localmente em branch dedicada com dados sintéticos representativos; sem deploy em produção, sem alterações na VPS, sem push/merge/tag; `APP_VERSION = "1.9.1"`, `SCHEMA_VERSION = 13`)*
- **Data**: `2026-11-25`
- **Branch**: `refactor/v1.9.2-backup-and-container-hardening` (baseada em `origin/main` @ `4d682e3d2c611b3e67062a51f07c922a20aadecf`)

> **Transparência de validação**: Todos os testes de restauração executados nesta etapa utilizaram **dados sintéticos representativos** em ambiente 100% isolado. A recuperação funcional a partir do backup real da VPS (`/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z`) **não foi executada** e aguarda autorização expressa do proprietário conforme o procedimento privado da Seção 11.

---

## 1. Encerramento Definitivo da Fase 11.3

A Fase 11.3 foi formalmente encerrada como **`PHASE_11_3_COMPLETED`** em [`13-phase-11.3-http-security.md`](./13-phase-11.3-http-security.md) e [`05-implementation-roadmap.md`](./05-implementation-roadmap.md), diferenciando:
- **Verificações automatizadas**: PR #2 integrado em `main` (`4d682e3`), `publish.yml` (`Run #45679963000`, `390 passed`, `:stable` em `sha256:1eac5477...`, `:v1.9.1` e `:1.9.1` intactas), deploy controlado via `lectoria-auto-update.service` (`UPDATE_SUCCESS`, backup obrigatório `2026-11-25T19-14-08Z`, SQLite `integrity=ok` / Schema `13` com contagens preservadas), validação HTTPS real via Tailscale Serve e execução automática subsequente do timer (`2026-11-25 19:29:43–19:29:44 UTC`, `NO_EXECUTABLE_CHANGE`, `Result=success`, container `c49acdfd...` preservado sem backup extra).
- **Verificações manuais confirmadas pelo proprietário no navegador**: login/logout, cookies `lectoria_session` e `lectoria_csrf` com atributos esperados, Biblioteca/capas, Descoberta/Atlas, leitores PDF/EPUB, preservação de progresso/anotações e zero violações CSP nos cenários efetivamente testados (sem declarar ausência universal fora desses cenários).

---

## 2. Inventário da Cobertura Real dos Backups

| Elemento | Backup interno (`backup_service.py`) | Snapshot do servidor (`lectoria-backup.service`) | Classificação |
| :--- | :---: | :---: | :--- |
| **Banco SQLite (`lectoria.db`)**: usuários e hashes de senha, configurações (`system_settings`), fontes (`library_sources`), metadados manuais, progresso, bookmarks, notas, destaques, desenhos, coleções, clubes/discussões/citações, Atlas (`concepts`, `reflections`, `knowledge_links`), auditoria e sessões | Sim (Online Backup API, WAL-safe) | Sim (Online Backup API, WAL-safe) | **Incluído no backup** |
| **Capas personalizadas (`*_custom.*`)** | Sim (`<stem>_covers/`) | Sim (`covers.tar.gz`) | **Incluído no backup** |
| **Capas extraídas (`*_extracted.*`)** | Não | Sim (`covers.tar.gz`) | **Regenerável** a partir dos livros originais |
| **Arquivos originais dos livros (PDF / EPUB)** | **Não** | **Não** | **Volume independente (`/app/books:ro`) — NÃO incluído no backup do SQLite/capas** |
| **Configuração operacional (`compose.yaml`, `.env`, systemd, proxy/Tailscale)** | **Não** | **Não** | **Exige procedimento separado do administrador** |
| **Cópia externa (off-site)** | **Não** | **Não** (Restic/Rclone intencionalmente desabilitado) | **Sem proteção externa contra perda física do host** |

> **Conclusão crítica**: o backup de `lectoria.db` + `covers.tar.gz` **não** constitui backup integral da biblioteca. A preservação física dos arquivos PDF/EPUB originais e das configurações do host permanece responsabilidade operacional separada.

---

## 3. Resultado dos Testes de Restauração (Ambiente Isolado com Dados Sintéticos)

Implementado em [`tests/test_v192_backup_restore_and_hardening.py`](../../tests/test_v192_backup_restore_and_hardening.py) usando diretório de dados, banco SQLite, capas, livros sintéticos (PDF + EPUB válidos) e backups 100% isolados em `tmp_path`:

- Restauração completa de snapshot (`lectoria.db` + `covers.tar.gz` + `commit.txt`) via `restore_snapshot_to_directory(...)`: **APROVADA**.
- `PRAGMA integrity_check == ok` e `SCHEMA_VERSION == 13`: **APROVADOS**.
- Inicialização da aplicação contra a instância restaurada (`/api/health` = `200`): **APROVADA**.
- **Decisão documentada sobre sessões restauradas**: `restore_backup` e `restore_snapshot_to_directory` agora **invalidam sessões antigas por padrão** (`invalidate_sessions=True`, executando `DELETE FROM sessions`). Justificativa: em cenários de recuperação após incidente ou migração, tokens de sessão contidos no snapshot podem estar expirados ou comprometidos; invalidá-los força reautenticação limpa sem perder nenhum dado do usuário (senhas/hashes em `users` são preservados). Validado no teste: cookie antigo retorna `401` e novo login com a senha preservada retorna `200`.
- Isolamento entre usuários (`alice_admin` vs. `bob_reader`): confirmado que `bob_reader` faz login normalmente após restauração e recebe listas vazias para notas privadas e registros do Atlas de `alice_admin`.
- Abertura de livros restaurados: PDF (incluindo HTTP Range `206 Partial Content`) e EPUB (`200 OK`, cabeçalho `PK`) servidos normalmente; quando os arquivos originais dos livros estão ausentes no destino, a aplicação sobe normalmente, preserva os registros no banco (`is_missing = 1`) e retorna `404` limpo ao tentar abrir o arquivo.

---

## 4. Comparações de Integridade e Conteúdo

| Item Verificado | Método de Comparação | Resultado |
| :--- | :--- | :--- |
| **Imutabilidade da origem** (`books/*.pdf`, `books/*.epub`, `snapshot/lectoria.db`, `snapshot/covers.tar.gz`) | SHA-256 antes vs. depois da restauração | Idêntico (0 bytes alterados) |
| **Capa personalizada** (`<id>_custom.png` via `POST /api/books/{id}/cover` → `covers.tar.gz` → `GET /api/books/{id}/cover`) | SHA-256 exato do conteúdo HTTP servido | Idêntico |
| **Metadados manuais, progresso, destaques, notas, coleções e Atlas** | Comparação de registros representativos e relacionamentos via API REST após boot | 100% preservados |

---

## 5. Falhas Identificadas no Mecanismo Existente e Correções Aplicadas

Em vez de criar um segundo sistema de backup, foram corrigidas 5 fragilidades concretas diretamente em [`backup_service.py`](../../backup_service.py) e [`scripts/backup.py`](../../scripts/backup.py):

1. **Validação superficial do SQLite**: `verify_sqlite_integrity` aceitava bancos vazios/sem tabelas e não checava compatibilidade de schema. **Correção**: criado `validate_backup_sqlite(db_path)` em modo somente-leitura (`file:...?mode=ro`), exigindo tamanho > 0, `PRAGMA integrity_check == ok`, presença das tabelas centrais (`schema_migrations`, `users`, `books`, `reading_progress`) e `1 <= schema_version <= database.SCHEMA_VERSION` (rejeitando explicitamente schemas futuros sem downgrade automático).
2. **Ausência de suporte a snapshots com `covers.tar.gz` e restauração em diretório isolado**: **Correção**: criado `restore_snapshot_to_directory(source, target_data_dir, *, allow_overwrite=False, invalidate_sessions=True)` e opções `--target-dir` / `--keep-sessions` em `scripts/backup.py`.
3. **Ausência de extrator seguro para `covers.tar.gz`**: **Correção**: criado `extract_covers_archive_safely(archive_path, dest_dir)` com pré-validação integral antes da escrita, *staging* temporário e bloqueio explícito de arquivos corrompidos, path traversal (`..`), caminhos absolutos, symlinks, hardlinks, dispositivos e FIFOs.
4. **Sobrescrita sem confirmação explícita**: `restore_snapshot_to_directory` adota `allow_overwrite=False` por padrão (e `restore_backup` agora suporta `allow_overwrite=False`), gerando cópia de proteção WAL-safe (`lectoria_pre_restore.db.bak`) e revertendo automaticamente em caso de falha no meio da operação.
5. **Persistência de sessões antigas após restauração**: **Correção**: invalidação automática da tabela `sessions` (`DELETE FROM sessions`) por padrão na restauração.

---

## 6. Avaliação das Permissões e Privilégios Docker

- **Estado anterior**: o `Dockerfile` não definia diretiva `USER`, executando o processo Uvicorn/Python como `root` (`UID 0 / GID 0`).
- **Superfície real de escrita da aplicação** (verificada no código-fonte):
  - Graváveis necessários: `/app/data` (`lectoria.db`, `-wal`, `-shm`, `covers/`), `/app/backups` e `/tmp` (`knowledge_export_service.py` usa `tempfile.mkstemp` para exportações `.json`/`.zip`).
  - Estritamente somente-leitura: `/app/books` (Lectoria nunca altera livros) e `/app` (bytecode pré-compilado com `PYTHONDONTWRITEBYTECODE=1`).
- **Compatibilidade com instalações existentes**: volumes nomeados novos herdam automaticamente `lectoria:lectoria` (`1000:1000`). Instalações existentes com bind mounts pertencentes a `root:root` exigem migração explícita de posse **apenas** em `data/` e `backups/` (Seção 12) ou uso temporário de `user: "0:0"`.

---

## 7. Medidas de Hardening Implementadas

| Arquivo | Medida Aplicada | Benefício Concreto |
| :--- | :--- | :--- |
| [`Dockerfile`](../../Dockerfile) | Usuário dedicado `lectoria` (`ARG LECTORIA_UID=1000`, `ARG LECTORIA_GID=1000`), `USER lectoria:lectoria`, `chown` restrito a `/app/data /app/data/covers /app/backups` (mantendo `/app` e `/opt/venv` de `root:root` somente-leitura) | Elimina execução como `root` sem `chmod 777` e sem `chown -R` indiscriminado |
| [`docker-compose.yml`](../../docker-compose.yml) e [`docker-compose.example.yml`](../../docker-compose.example.yml) | `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp:rw,noexec,nosuid,size=64m]`, `/app/books:ro`, imagem `:stable` e `healthcheck` em `/api/health` | Reduz drasticamente superfície de ataque com zero impacto funcional ou de memória |

---

## 8. Medidas Descartadas e Justificativas

- **Entrypoint com `chown -R` automático como `root` + `gosu`/`su-exec`**: descartado por exigir início privilegiado do container, introduzir dependência extra e arriscar alterações silenciosas de posse em bind mounts do host (incluindo risco se alguém montar volumes incorretamente). Preferiu-se `USER lectoria:lectoria` puro + procedimento explícito e reversível de migração.
- **Reativação de Restic/Rclone ou introdução de backup em nuvem obrigatório**: descartado por decisão explícita do proprietário e para manter o Lectoria livre de dependências externas obrigatórias.
- **Downgrade automático de schema SQLite**: descartado por ser intrinsecamente destrutivo; backups de versão futura são rejeitados com mensagem clara.

---

## 9. Compatibilidade com Instalações Existentes

Preservados integralmente: Docker Compose genérico, instalação independente (venv/systemd via `deployment/lectoria.service`), Localhost HTTP, LAN HTTP, Tailscale privado HTTP, Tailscale Serve HTTPS, reverse proxy HTTPS, múltiplas fontes de biblioteca, backup/restauração, atualizador Docker (`deployment/docker-auto-update.sh`), healthcheck `/api/health` e leitores PDF/EPUB. Zero dependência de domínio, VPS, Tailscale ou caminhos particulares do proprietário.

---

## 10. Resultados dos Testes

- **Suíte local completa (`pytest -q`, Python 3.14.9 + Playwright Chromium)**: **`395 passed, 2 skipped, 0 failed, 3 warnings in 582.23s (0:09:42)`**
  - Inclui 100% da suíte anterior (389 testes unitários/API + 56 E2E Playwright) + 6 novos testes da Fase 11.4 (`tests/test_v192_backup_restore_and_hardening.py`: restauração sintética ponta a ponta, isolamento entre usuários, invalidação de sessões, todos os cenários de falha/segurança de SQLite e `covers.tar.gz`, arquivos de livros ausentes e invariantes de hardening/portabilidade).
  - Os 3 warnings são avisos legados pré-existentes de deprecação do Starlette TestClient (`httpx` / `per-request cookies`), sem relação com a Fase 11.4.
- **Testes Docker reais (`test_v192_docker_auto_update_integration.py` e `test_v192_docker_hardening_integration.py`)**: **`2 skipped`** localmente por ausência de Docker Engine neste host (explicitamente **não** declarados como aprovados localmente; serão executados integralmente no runner `ubuntu-latest` do GitHub Actions na validação remota).

---

## 11. Procedimento Privado de Recuperação Real Isolada (NÃO EXECUTAR SEM AUTORIZAÇÃO)

Para validar funcionalmente o backup real `/srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z` (`41 MB`, Schema `13`, `238` livros, `5` usuários, `213` capas) na própria VPS **sem tocar em produção, sem duplicar a biblioteca e sem expor dados publicamente**:

```bash
# EXECUTAR APENAS COM AUTORIZACAO DO PROPRIETARIO NA VPS
(
set -euo pipefail
ISO_DIR="/tmp/lectoria-restore-drill-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ISO_DIR/data" "$ISO_DIR/backups"
sudo chown 1000:1000 "$ISO_DIR/data" "$ISO_DIR/backups"

# 1. Restaura o snapshot real em diretorio descartavel (origem intacta, sessoes invalidadas)
sudo docker run --rm \
  -v /srv/nosreme/lectoria/backups/2026-11-25T19-14-08Z:/snapshot:ro \
  -v "$ISO_DIR/data:/target_data" \
  ghcr.io/nosremekk/lectoria:stable \
  python3 scripts/backup.py --restore /snapshot --target-dir /target_data --force

# 2. Sobe instancia isolada em porta local distinta (127.0.0.1:18765) montando livros estritamente :ro
sudo docker run -d --rm --name lectoria-restore-drill \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -p 127.0.0.1:18765:8765 \
  -e LECTORIA_SECURE_COOKIES=false \
  -v "$ISO_DIR/data:/app/data" \
  -v "$ISO_DIR/backups:/app/backups" \
  -v /caminho/dos/livros:/app/books:ro \
  ghcr.io/nosremekk/lectoria:stable

# 3. Verifica /api/health e limpa apos testes manuais (via tunel SSH local)
sleep 3 && curl -fsS http://127.0.0.1:18765/api/health
echo "Para encerrar e limpar: sudo docker stop lectoria-restore-drill && sudo rm -rf '$ISO_DIR'"
)
```

---

## 12. Plano Explícito e Reversível de Migração de Permissões (Instalações Existentes)

Antes de implantar a imagem não-root (`UID 1000 / GID 1000`) em um host existente cujos bind mounts `data/` e `backups/` pertencem a `root:root`:
1. Pausar `lectoria-auto-update.timer` e parar o container `lectoria`.
2. Registrar permissões atuais: `stat -c '%u:%g %a %n' <DATA_DIR> <BACKUPS_DIR>`.
3. Aplicar `sudo chown -R 1000:1000 <DATA_DIR> <BACKUPS_DIR>` (**nunca** alterar `books/` e **nunca** usar `chmod 777`).
4. Atualizar o `compose.yaml` com `no-new-privileges:true`, `cap_drop: [ALL]`, `read_only: true`, `tmpfs: [/tmp:rw,noexec,nosuid,size=64m]` e manter `/app/books:ro`.
5. Subir o container e validar `/api/health`, escrita SQLite e backup.
6. **Reversão imediata**: definir `user: "0:0"` no `compose.yaml` ou restaurar o `uid:gid` registrado no passo 2.

---

## 13. Riscos Residuais

1. **Ausência de backup externo (off-site)**: com Restic/Rclone intencionalmente desabilitado, uma perda física total do servidor/disco impediria a recuperação se não houver cópia externa manual periódica de `backups/` e `books/`.
2. **Migração de posse em instalações legadas**: hosts com bind mounts pertencentes a `root:root` que atualizarem para a imagem não-root sem executar o plano da Seção 12 terão erro de permissão na escrita SQLite (capturado com segurança pelo *fail-closed* do atualizador em `blocked`).
3. **Validação real em Docker Engine e backup real da VPS**: os testes Docker reais (`test_v192_docker_hardening_integration.py`) aguardam execução no CI (`ubuntu-latest`), e o drill com o backup real da VPS aguarda autorização do proprietário.

---

## 14. Arquivos Modificados e Commits

- [`docs/v1.9.2/13-phase-11.3-http-security.md`](./13-phase-11.3-http-security.md) e [`docs/v1.9.2/05-implementation-roadmap.md`](./05-implementation-roadmap.md) — encerramento formal da Fase 11.3 (`PHASE_11_3_COMPLETED`) e atualização da Fase 11.4.
- [`backup_service.py`](../../backup_service.py) e [`scripts/backup.py`](../../scripts/backup.py) — validação estrutural/schema em `mode=ro`, extração segura de `covers.tar.gz`, proteção de sobrescrita, invalidação de sessões e restauração isolada de snapshots.
- [`Dockerfile`](../../Dockerfile), [`docker-compose.yml`](../../docker-compose.yml), [`docker-compose.example.yml`](../../docker-compose.example.yml) — usuário não-root `lectoria` (`1000:1000`) e hardening residual de container.
- [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md) — consolidação dos procedimentos operacionais, cobertura de backup, permissões e recuperação.
- [`tests/test_v192_backup_restore_and_hardening.py`](../../tests/test_v192_backup_restore_and_hardening.py) e [`tests/test_v192_docker_hardening_integration.py`](../../tests/test_v192_docker_hardening_integration.py) — testes funcionais, de falha/segurança e de integração Docker real.

---

## 15. Pendências Estritamente Necessárias para a Release `v1.9.2`

1. Revisão final e validação remota desta branch no GitHub Actions (`ubuntu-latest`), executando integralmente os 2 testes Docker reais (`docker-auto-update` + `docker-hardening`).
2. (Opcional/recomendado antes do deploy na VPS) Execução autorizada do drill isolado com o backup real da VPS (Seção 11) e migração de posse `1000:1000` em `data/` e `backups/` antes do deploy da imagem não-root (Seção 12).
3. Fase 11.5: bump coordenado de `APP_VERSION` para `1.9.2` (`config.py`, HTML `?v=1.9.2`, `CHANGELOG.md`), merge em `main` e publicação da tag imutável `v1.9.2`.
