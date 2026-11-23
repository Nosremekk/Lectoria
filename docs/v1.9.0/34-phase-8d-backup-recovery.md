# Lectoria v1.9.0 — Fase 8.D: Diagnóstico e Descontinuação Voluntária do Backup Restic/Rclone

- **Data de Encerramento**: 2026-11-23 (12:12 UTC)
- **Status Final**: **`PHASE_8D_COMPLETE` — Backup remoto Restic/Rclone descontinuado voluntariamente por decisão do proprietário (infraestrutura simplificada)**
- **Estado Final Verificado na VPS**:
  - `nosreme-restic-backup.timer`: **`disabled`** e **`inactive`** (sem `mask`; preservado no disco para eventual reativação futura).
  - `nosreme-restic-backup.service`: **`inactive`** (estado `failed` limpo; zero processos `restic`/`rclone` ativos).
  - `lectoria-backup.timer` / `lectoria-backup.service` (local): **`enabled`** e **`active`**, próximo disparo `2026-11-24 03:00:00 UTC`, última execução `2026-11-23 03:00:37 UTC` (`SQLite: backup íntegro.`, `status=0/SUCCESS`).
  - Lectoria (`/api/health`): `{"status":"healthy","app_name":"Lectoria","version":"1.8.0"}` · `AdGuardHome`, `caddy`, `dockerd` e `uvicorn` operacionais.
  - Snapshots antigos no Google Drive: **preservados intactos** (nenhuma operação `forget`, `prune`, `unlock` ou remoção foi executada).
  - Política `sudoers` temporária (`/etc/sudoers.d/99-lectoria-phase8d-diag` e `revoke-phase8d-sudo.timer`): **revogada e removida** (`sudo -n` confirmado bloqueado).

---

## 0. Decisão do Proprietário e Desfecho da Fase 8.D

Após a conclusão do diagnóstico técnico da Etapa A e da validação de compatibilidade da Etapa C.1, o proprietário decidiu **descontinuar voluntariamente o backup externo automático Restic/Rclone para Google Drive**, priorizando uma infraestrutura mais simples para a VPS pessoal:
- O **backup local diário da Lectoria** (`lectoria-backup.timer` → `lectoria-backup.service`) permanece como **mecanismo principal de recuperação**.
- Antes de atualizações relevantes ou migrações de schema (`v12 → v13`), será exigida **cópia externa manual dos dados essenciais** (`lectoria.db`, `covers/` e `compose.yaml`) além do backup SQLite local íntegro e do plano de rollback.
- Toda a documentação técnica do diagnóstico (Seções 1–4 abaixo) foi **preservada integralmente** para consulta ou eventual reativação futura.

---

## 1. Evidências Coletadas na Etapa A (Fatos Confirmados vs Hipóteses)

### 1.1 Fatos Confirmados (Observados Diretamente)
1. **Versões instaladas na VPS**:
   - **`restic 0.16.4`** (compilado com `go1.22.2`, `linux/amd64`).
   - **`rclone v1.60.1-DEV`** (pacote padrão Ubuntu 24.04 — release de 2022, desatualizada em relação à linha atual do Rclone).
2. **Falha da execução de `2026-11-19 05:14 UTC` (PID `579870`) — Causa comprovada nos logs**:
   - O `rclone` utiliza o **Client ID OAuth público compartilhado padrão do Rclone (`project_number:202264815644`)**, dividido por milhares de usuários no mundo.
   - O Google Drive retornou `HTTP 403`: `Quota exceeded for quota metric 'Queries' and limit 'Previous quota: Requests per minute' of service 'drive.googleapis.com' for consumer 'project_number:202264815644'` (`quota_limit: defaultPerMinutePerProject`, `reason: RATE_LIMIT_EXCEEDED`).
   - Ocorreram erros HTTP/2 (`stream error: stream ID 421; CANCEL`), `couldn't list directory: context canceled` e `500 Internal Server Error` ao salvar packs e lock (`Fatal: unable to save snapshot`).
   - Essa execução atingiu **`2.5G memory peak`** antes de falhar (`code=exited, status=1/FAILURE`) — o que motivou o `override.conf` com `MemoryHigh=1536M`/`MemoryMax=2048M`.
3. **Execução travada de `2026-11-19 22:06:01 UTC` (PID `624544` / `restic 624565` / `rclone 624574`)**:
   - Registrou `[1/3] Criando backup consistente do Lectoria...`, `[2/3] Enviando backup criptografado ao Google Drive...` e, às `22:07:51 UTC`, `using parent snapshot 15b83e2b`.
   - **Nenhuma linha adicional de log foi emitida por 71 horas e 23 minutos** até o `SIGTERM` manual em `2026-11-22 21:30:57 UTC` (`Consumed 3min 28.579s CPU time, 355.3M memory peak`).
   - **`TimeoutStartSec=0` (`TimeoutStartUSec=infinity`)** permitiu que o bloqueio durasse indefinidamente e travou o timer diário (`NEXT = -`).
   - **`Persistent=true`** no timer disparou uma nova execução imediatamente (`21:30:57 UTC`, PID `791107`) após a primeira interrupção, até o timer ser parado (`21:31:12 UTC`) e a nova execução encerrada (`21:31:30 UTC`).
4. **Integração com backup local**:
   - A etapa `[1/3]` de `nosreme-restic-backup` aciona `lectoria-backup.service` (confirmado pelo log de `2026-11-22 21:30:57 UTC`: `SQLite: backup íntegro.` → `2026-11-22T21-30-57Z`).
5. **Flag `--b2-hard-delete`**:
   - Presente no comando `rclone serve restic --stdio --b2-hard-delete gdrive:Backups-VPS-Nosreme`; é específica de Backblaze B2 e sem efeito no Google Drive.

### 1.2 Hipóteses (Aguardando Confirmação Adicional — Não Tratadas Como Fato)
1. **Causa raiz do travamento silencioso de 71h23m em `2026-11-19 22:06 UTC`**:
   - **Importante**: Diferentemente da execução de `05:14 UTC`, **não há registro de `RATE_LIMIT_EXCEEDED` nos logs da execução das `22:06 UTC`**.
   - **Hipótese mais provável**: deadlock ou espera indefinida no transporte `restic 0.16.4` ↔ `rclone v1.60.1-DEV serve restic --stdio` (HTTP/2 sem `--timeout` / `--contimeout` efetivo ou falha de conexão silenciosa após throttling/reset do Google Drive), agravada pela ausência de timeout finito (`TimeoutStartSec=0`).
2. **Existência de locks órfãos no repositório remoto**:
   - Como as execuções de `19/11 22:06` e `22/11 21:30` receberam `SIGTERM` durante a etapa `[2/3]`, é provável que existam um ou mais locks não liberados em `gdrive:Backups-VPS-Nosreme/locks/` — **a confirmar somente-leitura com `restic --no-lock list locks` na Etapa C**.

---

## 2. Informações Adicionais do Script Necessárias (Sem Expor Segredos)

Como a política temporária excluiu intencionalmente `sudo cat` de `/usr/local/sbin/nosreme-restic-backup`, para finalizar o ajuste exato na Etapa C precisamos apenas que o proprietário confirme (revisando manualmente sem colar credenciais):
1. O que faz a etapa **`[3/3]`** do script (ex.: `restic forget --prune`, `restic check`, limpeza de backups locais ou notificação)?
2. Como as credenciais são carregadas (ex.: `EnvironmentFile` externo ou `RESTIC_PASSWORD_FILE` + `RCLONE_CONFIG`)?
3. O script já passa alguma flag `-o rclone.args=...`, `-o rclone.connections=...` ou `flock`?
4. Há algum `client_id` OAuth próprio configurado no `rclone.conf` (os logs de `19/11 05:14` indicam que não — estava usando `project_number:202264815644`)?

---

## 3. Proposta Final de Correção (Etapa C — Aguardando Aprovação Explícita)

### 3.1 Medidas Obrigatórias (Eliminam o Travamento Indefinido e o Erro de Quota)
1. **Timeout finito e encerramento limpo (`systemd`)**:
   - Substituir `TimeoutStartSec=0` por **`TimeoutStartSec=3h`** (ou `4h`), adicionar `TimeoutStopSec=5min`, `KillMode=control-group` e `Restart=no`.
   - Alterar `Persistent=true` para **`Persistent=false`** no `nosreme-restic-backup.timer` (impede disparo automático surpresa ao encerrar manualmente durante manutenção).
   - Preservar `MemoryHigh=1536M`, `MemoryMax=2048M`, `Nice=10`, `IOSchedulingClass=best-effort`, `IOSchedulingPriority=7`.
2. **Prevenção de concorrência (`flock`)**:
   - Envolver o corpo de `/usr/local/sbin/nosreme-restic-backup` com `flock -n /run/nosreme-restic-backup.lock` (falha limpa e imediata se já houver backup em andamento).
3. **Resolução definitiva do `RATE_LIMIT_EXCEEDED` (`project_number:202264815644`)**:
   - **Recomendação principal**: configurar um **Client ID / Client Secret OAuth próprio** (gratuito no Google Cloud Console) no `rclone.conf` do remote `gdrive:`, eliminando a disputa pela cota compartilhada global do Rclone.
   - **Complemento obrigatório no comando `restic` / `rclone`**:
     - Remover `--b2-hard-delete`.
     - Reduzir concorrência do `restic`: `-o rclone.connections=2`.
     - Passar flags de resiliência e controle de taxa ao `rclone`:
       `--tpslimit 4 --tpslimit-burst 8 --drive-pacer-min-sleep 100ms --drive-chunk-size 32M --timeout 5m --contimeout 1m --retries 5 --low-level-retries 10 --retries-sleep 10s`.
     - Manter saída informativa (`--verbose=1` no `restic`) para o `journald` registrar progresso real e resumo final.
4. **Atualização do `rclone` (Recomendada)**:
   - `rclone v1.60.1-DEV` (de 2022) tem histórico conhecido de melhorias importantes de estabilidade no modo `serve restic --stdio` e HTTP/2 nas versões `v1.66+` / `v1.68+`. Avaliar atualização controlada do binário oficial do Rclone (fora do escopo mínimo, mas fortemente recomendada se o travamento se repetir).

### 3.2 Sequência Controlada de Execução da Etapa C (Somente Após Aprovação)
1. **Backup das configurações atuais**: copiar `/usr/local/sbin/nosreme-restic-backup`, `nosreme-restic-backup.service`, `override.conf` e `nosreme-restic-backup.timer` para `/root/backup-config-phase8d-<timestamp>/`.
2. **Verificação somente-leitura do repositório remoto** (confirmado zero processos ativos):
   - `restic --no-lock snapshots --compact` (confirmar último snapshot concluído — ex.: `15b83e2b`).
   - `restic --no-lock list locks` (identificar locks órfãos; se houver, inspecionar com `cat lock <ID>` e somente com aprovação específica executar `restic unlock`).
   - `restic --no-lock check` (integridade estrutural básica, sem baixar dados).
3. **Aplicar os ajustes aprovados** (script + unit + timer + `systemctl daemon-reload`).
4. **Teste de validação mínimo**: backup isolado de `/srv/nosreme/lectoria/compose.yaml` (`--tag validation-8d`) + teste de leitura real (`restic --no-lock dump latest /srv/nosreme/lectoria/compose.yaml >/dev/null`).
5. **Execução controlada de 1 backup completo**: acompanhar `journalctl` até concluir com `status=0/SUCCESS`, validar novo snapshot e leitura.
6. **Reativar o timer** (`systemctl start nosreme-restic-backup.timer`) e confirmar `NEXT` válido em `systemctl list-timers`.
7. **Revogar a política `sudoers` temporária** imediatamente após encerrar a manutenção.

---

## 4. Plano de Rollback

Se qualquer alteração na Etapa C causar erro inesperado:
1. `sudo systemctl stop nosreme-restic-backup.timer nosreme-restic-backup.service`.
2. Restaurar integralmente os arquivos de `/root/backup-config-phase8d-<timestamp>/` para `/usr/local/sbin/` e `/etc/systemd/system/`.
3. `sudo systemctl daemon-reload`.
4. Manter o timer parado até nova análise (nunca reativar com `TimeoutStartSec=0` sem supervisão).
5. Nenhum dado do repositório remoto, da Lectoria ou do `lectoria-backup.service` local é alterado.
