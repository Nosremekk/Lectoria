# Guia de Implantação, Operação e Recuperação do Lectoria

Este documento consolida os procedimentos oficiais de implantação, atualização, backup, recuperação e endurecimento operacional do **Lectoria** em Docker Compose e instalação independente (venv/systemd).

---

## 1. Instalação Limpa com Docker Compose

O repositório fornece [docker-compose.yml](../docker-compose.yml) e [docker-compose.example.yml](../docker-compose.example.yml) já configurados com:
- Execução como usuário não privilegiado **`lectoria` (`UID 1000 / GID 1000`)** definido no [Dockerfile](../Dockerfile);
- `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true` e `tmpfs: [/tmp:rw,noexec,nosuid,size=64m]`;
- Separação explícita entre volumes graváveis (`/app/data`, `/app/backups`) e acervo somente-leitura (`/app/books:ro`);
- Padrões conservadores de segurança HTTP (`LECTORIA_SECURE_COOKIES=auto`, `LECTORIA_FORWARDED_ALLOW_IPS=127.0.0.1,::1`, `LECTORIA_CSP_MODE=report-only`, `LECTORIA_HSTS_ENABLED=false`).

```bash
# 1. Prepare o diretório de livros (somente-leitura para o container)
mkdir -p ./books

# 2. Suba o serviço (volumes nomeados são inicializados automaticamente com dono lectoria:lectoria)
docker compose up -d

# 3. Verifique a saúde
curl -fsS http://127.0.0.1:8765/api/health
```

> **Instalação independente (sem Docker)**: utilize `.env.example`, instale dependências com `pip install -r requirements.txt -c constraints.txt` em um `.venv` dedicado e utilize [deployment/lectoria.service](../deployment/lectoria.service) (que já executa sob usuário `lectoria`, `NoNewPrivileges=true`, `PrivateTmp=true` e `ProtectSystem=full`).

---

## 2. Variáveis de Ambiente e Permissões

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `LECTORIA_HOST` / `LECTORIA_PORT` | `0.0.0.0` / `8765` | Endereço e porta TCP do Uvicorn (`--workers 1`). |
| `LECTORIA_DATA_DIR` | `/app/data` | Diretório gravável para `lectoria.db` (`-wal`/`-shm`) e `covers/`. |
| `LECTORIA_LIBRARY_DIR` | `/app/books` | Diretório padrão do acervo PDF/EPUB (recomendado montar com `:ro`). |
| `LECTORIA_BACKUPS_DIR` | `/app/backups` | Diretório gravável para backups SQLite e capas associadas. |
| `LECTORIA_SECURE_COOKIES` | `auto` | `auto` (`Secure` só em HTTPS efetivo), `true` (sempre `Secure`, ideal atrás de TLS exclusivo) ou `false`. |
| `LECTORIA_FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | IPs/CIDRs de proxies reversos confiáveis (ex.: gateway da rede bridge Docker). |
| `LECTORIA_CSP_MODE` | `report-only` | `off` \| `report-only` \| `enforce` para páginas HTML (`/` e `/reader`). |
| `LECTORIA_HSTS_ENABLED` | `false` | Emite HSTS apenas em HTTPS efetivo quando `true`. |

### Permissões Necessárias e Migração de Instalações Existentes (`root` → `lectoria`)
- **Instalações novas com volumes nomeados**: nenhuma ação manual necessária.
- **Bind mounts existentes criados por versões anteriores (executadas como `root`)**: antes de adotar a imagem não-root (`UID 1000 / GID 1000`), ajuste a posse **exclusivamente** nos diretórios graváveis de dados e backups — **nunca** use `chmod 777` e **nunca** altere permissões do diretório original de livros (`books/`):
  ```bash
  # 1. Pare o container
  docker compose stop lectoria

  # 2. Registre permissões atuais (para reversão se necessário)
  stat -c '%u:%g %a %n' /caminho/data /caminho/backups

  # 3. Aplique posse 1000:1000 somente nos dados e backups graváveis
  sudo chown -R 1000:1000 /caminho/data /caminho/backups

  # 4. Suba o serviço e valide /api/health
  docker compose up -d
  ```
- **Reversão imediata**: se precisar voltar temporariamente ao comportamento anterior sem alterar arquivos, defina `user: "0:0"` no serviço `lectoria` do `compose.yaml` ou restaure a posse anterior registrada no passo 2.
- **Preservação dos arquivos originais dos livros**: o Lectoria **nunca** modifica, renomeia ou apaga PDFs/EPUBs em `/app/books`. Montar `/app/books:ro` garante proteção no nível do kernel contra qualquer escrita acidental.

---

## 3. Referências Imutáveis de Imagens e Atualização

| Referência | Natureza | Uso Recomendado |
| :--- | :--- | :--- |
| `ghcr.io/nosremekk/lectoria@sha256:<digest>` | **Imutável** | Rollback determinístico e fixação auditável. |
| `ghcr.io/nosremekk/lectoria:vX.Y.Z` / `:X.Y.Z` | **Imutável** | Releases versionadas (nunca sobrescritas pelo CI). |
| `ghcr.io/nosremekk/lectoria:sha-<commit>` | **Imutável** | Artefato exato de um commit específico em `main`. |
| `ghcr.io/nosremekk/lectoria:stable` | **Mutável** | Canal estável atualizado após 100% dos testes + smoke test. |

### Atualização de Instalação Existente
1. **Sempre gere um backup consistente antes de atualizar**:
   ```bash
   docker compose exec lectoria python3 scripts/backup.py --backup
   ```
2. Atualize a imagem e recrie o container:
   ```bash
   docker compose pull
   docker compose up -d
   ```
3. Em ambientes com atualização automática via [deployment/docker-auto-update.sh](../deployment/docker-auto-update.sh), o script já exige backup preventivo obrigatório, compara `exec_fp` (evitando recriações por metadados OCI), grava referências imutáveis em `last-good.json` e aplica *fail-closed* (`blocked`) em caso de falha de healthcheck.

---

## 4. Backup: Criação, Cobertura Real e Limitações

### Criação de Backup Consistente (WAL-safe)
O SQLite opera em modo WAL (`lectoria.db`, `lectoria.db-wal`, `lectoria.db-shm`). **Nunca** copie apenas `lectoria.db` manualmente com o servidor ativo. Utilize sempre a SQLite Online Backup API:
```bash
# Via Docker
docker compose exec lectoria python3 scripts/backup.py --backup
docker compose exec lectoria python3 scripts/backup.py --verify

# Via instalação independente
./deployment/backup.sh
```

### Inventário do que é (e do que NÃO é) Protegido pelo Backup

| Categoria | Elementos | Proteção Atual |
| :--- | :--- | :--- |
| **Incluídos no backup SQLite** | Usuários e hashes de senha, configurações (`system_settings`), fontes (`library_sources`), metadados manuais de livros, progresso de leitura, bookmarks, notas, destaques, desenhos vetoriais, coleções, clubes/discussões/citações, Atlas (conceitos, reflexões, conexões), auditoria e sessões | **Protegidos** (`lectoria.db` via SQLite Online Backup API) |
| **Incluídos no backup de capas** | Capas personalizadas (`*_custom.*`) no `backup_service.py`; todas as capas (`covers.tar.gz`) em snapshots completos de servidor | **Protegidos** (capas `*_extracted.*` também são regeneráveis a partir dos livros) |
| **Preservados por volume independente** | Arquivos originais dos livros (**PDF e EPUB** em `/app/books`) | **NÃO incluídos no backup do SQLite/capas** — responsabilidade de cópia externa dedicada do administrador |
| **Regeneráveis** | Cache de capas extraídas (`*_extracted.*`), arquivos `-wal`/`-shm`, bytecode Python | Regenerados automaticamente pela aplicação |
| **Exigem procedimento próprio** | `compose.yaml` / `.env`, unidades `systemd`, certificados/configuração de proxy reverso ou Tailscale | Devem ser versionados ou copiados separadamente pelo administrador |

### Limitações do Backup Local e Responsabilidades do Administrador
- Backups gravados exclusivamente no mesmo servidor protegem contra erro operacional, falha de atualização ou corrupção lógica do banco, **mas NÃO protegem contra perda física do disco ou destruição do host**.
- O Lectoria não impõe dependência de nuvem obrigatória. Quando backup remoto (como Restic/Rclone) estiver desabilitado por decisão do administrador, **não é possível prometer recuperação integral após perda total do host** sem cópia externa (off-site) periódica tanto dos snapshots (`lectoria.db` + capas + configuração) quanto da biblioteca original de PDFs/EPUBs.

---

## 5. Recuperação: Rollback de Imagem vs. Restauração de Dados

> **Regra fundamental**: **rollback de imagem** e **restauração de dados** são operações distintas.
> - **Rollback de imagem**: retorna o container a um digest imutável anterior (`@sha256:...`) mantendo o banco atual intacto. Só é seguro quando o `SCHEMA_VERSION` é compatível (sem migração irreversível).
> - **Restauração de dados**: substitui `lectoria.db` (e capas) a partir de um backup validado. O Lectoria **não executa downgrade automático de schema** e rejeita backups cuja versão seja superior à suportada pelo código.

### 5.1 Recuperação em Nova Instalação (ou Ambiente Isolado)
Utilize `scripts/backup.py --restore` apontando para um arquivo `.db` ou diretório de snapshot (`lectoria.db` + `covers.tar.gz`) com `--target-dir`:
```bash
python3 scripts/backup.py \
  --restore /caminho/do/snapshot_ou_arquivo.db \
  --target-dir /caminho/novo/data
```
- O utilitário valida `PRAGMA integrity_check`, tabelas centrais, compatibilidade de schema e segurança do `covers.tar.gz` (bloqueando path traversal, caminhos absolutos e symlinks) em *staging* **antes** de gravar no destino.
- Por padrão, sessões antigas contidas no backup são **invalidadas** (`DELETE FROM sessions`) para evitar reuso de tokens antigos/comprometidos; todos os usuários fazem login normalmente com suas senhas preservadas.
- Se os arquivos originais dos livros ainda não estiverem montados no novo host, a aplicação inicia normalmente com catálogo, metadados, notas e Atlas acessíveis, retornando `404` limpo ao abrir arquivos ausentes até que `/app/books` seja restabelecido.

### 5.2 Recuperação Após Falha de Atualização
1. **Sem migração de schema**: pare o serviço, fixe o digest imutável anterior (`image: ghcr.io/nosremekk/lectoria@sha256:<digest_anterior>`) no `compose.yaml` e execute `docker compose up -d`.
2. **Com alteração/corrupção de dados**: pare o container, restaure o backup pré-atualização com `--force` (gera automaticamente cópia de proteção `lectoria_pre_restore.db.bak` antes de substituir) e suba a imagem correspondente à versão do schema do backup.

### 5.3 Recuperação Após Perda Total do Host
1. Provisione um novo host com Docker + Docker Compose (ou Python 3.10+ + venv).
2. Restaure a configuração operacional (`compose.yaml` / `.env` / proxy HTTPS).
3. Restaure a biblioteca original de livros PDF/EPUB em `/caminho/books` (montada como `:ro`) a partir da cópia externa do administrador.
4. Restaure o snapshot mais recente (`lectoria.db` + capas) com `scripts/backup.py --restore <snapshot> --target-dir /caminho/data`.
5. Ajuste permissões `1000:1000` em `data/` e `backups/` (se usando bind mounts) e suba o container (`docker compose up -d`), validando `/api/health`, login e abertura de um PDF e um EPUB.
