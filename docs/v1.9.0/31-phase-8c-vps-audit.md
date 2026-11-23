# Lectoria v1.9.0 — Auditoria Supervisionada da VPS de Produção (Fase 8.C)

- **Classificação Final**: **`PHASE_8C_AUDIT_WITH_LIMITATIONS`**
- **Data da Auditoria**: 2026-11-22 (20:51–21:18 UTC)
- **Branch Local**: `feature/v1.9.0-connected-knowledge`
- **Versão em Produção Verificada**: `1.8.0` (confirmada via `http://127.0.0.1:8765/api/health`)
- **Escopo**: Auditoria técnica real, **estritamente somente-leitura**, sem `sudo`, com aprovação humana prévia por lote e execução sequencial (um comando por vez). **Nenhuma modificação foi realizada na VPS.**

---

## 1. Resumo Executivo

A auditoria supervisionada na VPS de produção revelou diferenças estruturais críticas entre as premissas documentadas na Fase 8 (Etapas A/B) e o ambiente real em operação:

1. **Topologia de volumes incompatível com `docker-compose.v190-candidate.yml`**: produção utiliza **bind mounts em `/storage/*`** (`./data:/storage/data`, `./backups:/storage/backups`, `./books:/storage/books`), e **não** volumes nomeados em `/app/*`. Aplicar o Compose candidato como está iniciaria a Lectoria com um **banco SQLite vazio**.
2. **Hardware real possui apenas `1` vCPU (`nproc = 1`)**: o Compose candidato propunha `cpus: 1.5`, acima da capacidade do host.
3. **Acesso privado via `tailscale serve` (e não via Caddy)**: a Lectoria escuta em `127.0.0.1:8765` e é publicada exclusivamente na tailnet por `tailscale serve`. O Caddy (`caddy:2` em `/srv/nosreme/compose.yaml`) serve apenas o site e o blog públicos, totalmente isolado da Lectoria.
4. **Análise real de memória (`memory.stat` cgroup v2)**: apesar de `memory.current` estar em `753,2 MiB / 768 MiB` (`memory.events: max 1322`), **76,9% (`578,9 MiB`) corresponde a page cache reciclável (`file`, sendo `489,4 MiB` `inactive_file`)** e apenas **`171,3 MiB` (`22,3%`) é consumo anônimo real (`anon`)** do Python/Uvicorn, com `oom = 0`, `oom_kill = 0`, `swap = 0` e apenas `226` major page faults.
5. **Alerta operacional crítico no backup remoto (`nosreme-restic-backup.service`)**: o serviço encontra-se em estado **`activating (start)` há ~71h10m** (desde `2026-11-19 22:06:01 UTC`), com `TimeoutStartSec=0`, impedindo novas execuções diárias do timer (`NEXT = -`).
6. **Risco de atualização automática**: `lectoria-auto-update.timer` executa a cada 15 minutos (`status=0/SUCCESS`) sobre `ghcr.io/nosremekk/lectoria:stable`. Qualquer push na `main` dispararia atualização automática em até 15 minutos.

---

## 2. Data e Escopo

- **Execução**: 2026-11-22, em 4 lotes sequenciais (15 comandos SSH individuais aprovados pelo proprietário).
- **Restrições cumpridas**: zero uso de `sudo`, zero comandos de escrita, zero `docker exec`, zero `docker inspect` completo, zero leitura de `.env`, credenciais ou conteúdo de livros/notas.

---

## 3. Método de Acesso

- Conexão via `ssh lectoria-vps` (alias configurado pelo proprietário, sem `-t`, sem `StrictHostKeyChecking=no`, sem modificação de `known_hosts`).
- Usuário SSH não privilegiado (`emerson`, UID 1000), sem acesso a `unix:///var/run/docker.sock`, sem pertencimento aos grupos `adm`/`systemd-journal` e sem permissão de leitura em `/usr/local/sbin/lectoria-*`.

---

## 4. Inventário do Host

| Propriedade | Valor Observado |
| :--- | :--- |
| **Hostname** | `srv2137600` |
| **Sistema Operacional** | Ubuntu `24.04.5 LTS` (`VERSION_ID="24.04"`) |
| **Kernel** | `Linux 6.8.0-149-generic` |
| **Uptime / Load Average** | `15 dias, 2h34` · `0.00, 0.00, 0.00` |
| **vCPUs (`nproc`)** | **`1`** |
| **RAM Total / Usada / Disponível** | `3.8 GiB` total · `928 MiB` usada · `2.9 GiB` disponível (`buff/cache`) |
| **Swap** | `2.0 GiB` total · `512 KiB` usada (`2.0 GiB` livre) |
| **Disco `/` (`/dev/sda1`, `ext4`)** | `48G` total · `18G` usada (`38%`) · `30G` livre |
| **Inodes `/`** | `65K / 6.2M` (`2%` em uso) |
| **Tailscale** | Ativo (`tailscale0`, IP sanitizado) |
| **Docker / Docker Compose** | `Docker 29.9.3` · `Docker Compose v5.7.0` |

---

## 5. Containers e Serviços

Como o usuário SSH não possui permissão em `/var/run/docker.sock`, a lista de containers via `docker ps`/`docker stats` não pôde ser obtida diretamente. Os serviços ativos foram identificados via estrutura de `/srv/nosreme`, `compose.yaml` e processos do host (`ps`):

| Serviço | Localização / Origem | Processo Principal (RSS) | Observação |
| :--- | :--- | :--- | :--- |
| **Lectoria** | `/srv/nosreme/lectoria/compose.yaml` (`ghcr.io/nosremekk/lectoria:stable`) | `uvicorn` (`186,4 MiB` RSS, `1` processo, `root`) | `/api/health` = `healthy`, `v1.8.0` |
| **AdGuard Home** | `/srv/nosreme/adguard/` | `AdGuardHome` (`108,6 MiB` RSS) | DNS/rede local (`:53` e `:8081` na `tailscale0`) |
| **Caddy** | `/srv/nosreme/compose.yaml` (`caddy:2`) | `caddy` (`27,7 MiB` RSS) | Proxy público (`:80`, `:443`) para `site` e `blog` |
| **Backup Externo** | `nosreme-restic-backup.service` | `restic` (`55,0 MiB`) + `rclone` (`54,6 MiB`) | Em `activating (start)` há ~71h10m |
| **Infraestrutura Base** | Host (`systemd`) | `dockerd` (`67,0 MiB`), `tailscaled` (`45,7 MiB`), `containerd` (`33,5 MiB`), `fail2ban-server` (`31,1 MiB`) | Operação normal |

---

## 6. Recursos e Limites (Cgroup v2 Real da Lectoria)

Leitura direta de `/sys/fs/cgroup/system.slice/docker-<id>.scope/`:

| Métrica Cgroup v2 | Valor Bruto | Equivalente | Interpretação |
| :--- | ---: | ---: | :--- |
| `memory.max` | `805.306.368 B` | **768,0 MiB** | Confirma `mem_limit: 768m` do `compose.yaml` |
| `memory.current` | `789.741.568 B` | **753,2 MiB** | 98,1% do teto (`anon` + `file` cache) |
| `memory.peak` | `805.306.368 B` | **768,0 MiB** | Atingiu 100% do teto |
| `anon` | `179.662.848 B` | **171,3 MiB** | **Consumo real do Python/Uvicorn (22,3% do teto)** |
| `file` | `607.027.200 B` | **578,9 MiB** | **Page cache de arquivos (76,9% do `memory.current`)** |
| `inactive_file` | `513.167.360 B` | **489,4 MiB** | Cache frio imediatamente reciclável pelo kernel |
| `active_file` | `93.859.840 B` | **89,5 MiB** | Cache ativo |
| `file_dirty` | `0 B` | **0 MiB** | Nenhuma escrita suja pendente |
| `kernel` / `slab` | `3.051.520 B` / `2.373.192 B` | **2,9 MiB / 2,3 MiB** | Irrelevante |
| `memory.swap.current` | `0 B` | **0 MiB** | Sem uso de swap pelo container |
| `memory.events` | `max 1322`, `oom 0`, `oom_kill 0` | — | Reclaim normal de `inactive_file` ao atingir 768 MiB; **zero OOM** |
| `pgfault` / `pgmajfault` | `77.521.926` / `226` | — | Apenas 226 major page faults acumulados |
| `cpu.max` | `max 100000` | Sem limite | 1 vCPU compartilhada no host |
| `pids.current` / `pids.max` | `5` / `4653` | — | Folga ampla |

**PSI de Memória do Host (`/proc/pressure/memory`)**: `avg10/60/300 = 0.00` em repouso (`some total=107,6s`, `full total=54,1s` acumulados em 15 dias).

**Conclusão sobre Memória**: a proximidade de `memory.current` ao teto de `768 MiB` e os `1.322` eventos `max` **decorrem predominantemente de page cache limpo (`inactive_file = 489,4 MiB`)** de PDFs/EPUBs/SQLite, e **não** de exaustão de memória anônima (`anon = 171,3 MiB`). Assim, não há evidência de pressão OOM atual na v1.8.0.

---

## 7. Volumes Persistentes

Todos os dados persistentes da Lectoria em produção utilizam **bind mounts locais** relativos a `/srv/nosreme/lectoria/`:

| Recurso | Caminho no Host | Caminho no Container | Modo | Ownership / Permissão | Tamanho Observado |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Banco SQLite** | `/srv/nosreme/lectoria/data/lectoria.db` | `/storage/data/lectoria.db` | RW | `root:root` (`-rw-r--r--`) | `5,5 MB` |
| **WAL / SHM** | `/srv/nosreme/lectoria/data/lectoria.db-{wal,shm}` | `/storage/data/...` | RW | Não presentes no momento (checkpointados) | `0 B` |
| **Backups pré-migração** | `/srv/nosreme/lectoria/data/lectoria_pre_migration_*.db.bak` | `/storage/data/...` | RW | `root:root` | `v7→v8` até `v11→v12` (`3,7 MB`) |
| **Capas** | `/srv/nosreme/lectoria/data/covers` | `/storage/data/covers` | RW | `root:root` (`drwxr-xr-x`) | `37 MB` |
| **Livros (PDF/EPUB)** | `/srv/nosreme/lectoria/books` | `/storage/books` | **RW** | `emerson:emerson` (`drwxr-xr-x`) | **`6,9 GB`** |
| **Backups Lectoria** | `/srv/nosreme/lectoria/backups` | `/storage/backups` | RW | Subdirs `root:root` (`drwx------`) | `5,6 MB` visível (subdirs `root` não contabilizados) |

---

## 8. Caddy e Rede

- **Acesso à Lectoria**: **`tailscale serve`** (`https://<tailnet-host> (tailnet only)` → `proxy http://127.0.0.1:8765`).
- **Porta da Lectoria**: `127.0.0.1:8765` (loopback exclusivo — **zero exposição pública**).
- **Caddy**: container `caddy:2` em `/srv/nosreme/compose.yaml` (rede `nosreme-public`), ouvindo no IP público `:80` e `:443` apenas para `nosreme.cloud` (`site`) e `blog.nosreme.cloud` (`blog`).
- **Resquício legado**: `/srv/nosreme/lectoria/caddy/Caddyfile` (datado de `Nov 7`, porta `8443`) não está ativo (`8443` não está em escuta).

---

## 9. SQLite e FTS5

- **Banco de produção**: `lectoria.db` (`5,5 MB`), não aberto por scripts remotos (conforme regra).
- **Schema esperado**: presença de `lectoria_pre_migration_v11_to_v12.db.bak` (em `Nov 17`) constitui evidência indireta forte de **Schema v12**, coerente com `version = 1.8.0` em `/api/health`.
- **Compatibilidade FTS5 na imagem Docker**: **PENDENTE / NÃO VERIFICADA DIRETAMENTE** (sem permissão em `docker.sock` na VPS e sem `docker` no host Fedora). A imagem oficial utiliza `python:3.12-slim` (Debian Bookworm), cujo SQLite normalmente compila com `ENABLE_FTS5`, mas a validação isolada continua **obrigatória antes do deploy** (ver `32-production-configuration-proposal.md`).

---

## 10. Backups

| Mecanismo | Agendamento | Última Execução / Estado | Classificação |
| :--- | :--- | :--- | :--- |
| **`lectoria-backup.service`** (local) | Diário `03:00:00` | `2026-11-22 03:00:04 UTC` (`status=0/SUCCESS`, `4.341s`); pasta `2026-11-22T03-00-00Z` criada | **Parcialmente verificado** (execução OK, integridade/restauração e script `/usr/local/sbin/lectoria-backup` não inspecionados sem `sudo`) |
| **`nosreme-restic-backup.service`** (remoto Google Drive via `rclone`) | Diário `04:00:00 UTC` | **`activating (start)` desde `2026-11-19 22:06:01 UTC` (~71h10m)**; `TimeoutStartSec=0`; timer com `NEXT = -`; `restic` (`31s` CPU) + `rclone` (`2m55s` CPU) em `SNl` (`0.0%` CPU) | **Com falha identificada / anomalia operacional crítica** (não concluído há ~71h; causa raiz no `journalctl` requer privilégio `adm`/`systemd-journal` ou `sudo` pelo proprietário) |

---

## 11. Logs e Saúde

- `/api/health`: `200 OK` (`{"status":"healthy","app_name":"Lectoria","version":"1.8.0"}`).
- `docker logs` e `journalctl` de serviços `root`: **não acessíveis** sem `docker.sock`/`systemd-journal`/`sudo` (limitação registrada).

---

## 12. Comparação do `docker-compose.v190-candidate.yml` vs Produção Real

| Campo | Produção Real (`/srv/nosreme/lectoria/compose.yaml`) | Candidato Local (`docker-compose.v190-candidate.yml`) | Classificação |
| :--- | :--- | :--- | :--- |
| **Volumes / Caminhos** | Bind mounts `./books:/storage/books`, `./data:/storage/data`, `./backups:/storage/backups` | Volumes nomeados `/app/data`, `/app/backups` + `./books:/app/books:ro` | **BLOQUEADORA** |
| **Variáveis `LECTORIA_*_DIR`** | `/storage/{books,data,backups}` + `LECTORIA_DB_PATH`, `LECTORIA_COVERS_DIR` | `/app/{books,data,backups}` | **BLOQUEADORA** |
| **`cpus`** | Não definido (host tem **`1` vCPU**) | `cpus: 1.5` | **BLOQUEADORA** |
| **Usuário não-root (`Dockerfile`)** | `root` (arquivos em `./data` são `root:root`) | Proposto como opcional | **BLOQUEADORA sem `chown`** |
| **Modo `:ro` em `./books`** | RW (`./books:/storage/books`) | `:ro` | **Exige validação** (confirmar se algo escreve em `./books`) |
| **Porta** | `"127.0.0.1:8765:8765"` | `"127.0.0.1:8765:8765"` | **Segura (já idêntica)** |
| **Healthcheck** | Usa `Dockerfile` (`/api/health`, `start_period: 15s`) | `/api/health`, `start_period: 30s` | **Segura para adoção** |
| **`mem_limit`** | `768m` | `1g` (`mem_reservation: 256m`) | **Exige adaptação / Recomendada** |
| **Tag da imagem** | `ghcr.io/nosremekk/lectoria:stable` (com auto-update a cada 15 min) | `:stable` | **Exige controle** (preferir tag imutável `:v1.9.0` na janela de upgrade) |
| **Hardening / Logging / `init`** | Ausentes | `init: true`, `no-new-privileges`, `json-file` (`10m`×3), `pids_limit: 128`, `LECTORIA_SECURE_COOKIES=true` | **Segura para adoção** (`LECTORIA_SECURE_COOKIES=true` exige confirmar HTTPS via Tailscale Serve — confirmado!) |

---

## 13. Riscos Identificados e 14. Recomendações

Detalhados e classificados (`Obrigatória` / `Recomendada` / `Opcional`) em [32-production-configuration-proposal.md](32-production-configuration-proposal.md) e [33-phase-8c-handoff.md](33-phase-8c-handoff.md).

---

## 15. Evidências Sanitizadas e 16. Limitações

- **Sanitização aplicada**: omitidos/mascarados o IP público da VPS, o IP Tailscale completo, o domínio `.ts.net` completo e qualquer diretiva potencialmente sensível.
- **Limitações**:
  1. Sem acesso a `/var/run/docker.sock` (`permission denied`): `docker ps`, `docker stats`, `docker inspect` e `docker logs` não executados.
  2. Sem acesso a `journalctl` de serviços `root` (`adm`/`systemd-journal`): logs do `nosreme-restic-backup.service` (ex.: `RATE_LIMIT_EXCEEDED`) não puderam ser lidos diretamente.
  3. Sem permissão de leitura em `/usr/local/sbin/lectoria-auto-update` e `/usr/local/sbin/lectoria-backup`.
  4. `ENABLE_FTS5` na imagem `python:3.12-slim` de produção não verificado diretamente.

---

## 17. Comandos Remotos Executados (Todos Somente-Leitura, Aprovados por Lote)

- **Lote 1**: `1.1` (hostname/SO/kernel/uptime/nproc/free/df), `1.2` (tailscale/docker versions), `1.3` (`docker ps`/`stats` → `permission denied`), `1.4` (`ls -la /srv/nosreme/lectoria`).
- **Lote 2**: `2.1` (`compose.yaml` sanitizado + `ls /srv/nosreme`), `2.2` (`ls`/`du` em `data`/`backups`/`books`), `2.3` (Caddy sanitizado + `ss -tln`), `2.4` (`ps` sem `args` + `systemctl list-timers` + `/proc/pressure/memory`).
- **Lote 3**: `3.1` (`curl /api/health` + `tailscale serve status` + `/srv/nosreme/compose.yaml` estrutural), `3.2` (`systemctl status` dos 3 serviços), `3.3` (cgroup v2 da Lectoria), `3.4` (`systemctl cat` estrutural das 6 unidades).
- **Lote 4**: `4.1` (`journalctl` + `/var/log` + `ps` de `restic`/`rclone`), `4.2` (checagem de leitura de `/usr/local/sbin/lectoria-*`), `4.3` (`memory.stat` do cgroup v2 da Lectoria).

---

## 18. Confirmação de Ausência de Modificações

Confirmo que **nenhuma alteração** foi efetuada na VPS, nos containers, nos volumes, nos serviços `systemd`, nos backups, no Caddy, no Tailscale, nos arquivos `compose.yaml`/`Dockerfile`/workflows ou no código-fonte/versão (`APP_VERSION = "1.8.0"`, `SCHEMA_VERSION = 13`), e **nenhum commit, merge, push, tag ou deploy** foi realizado.
