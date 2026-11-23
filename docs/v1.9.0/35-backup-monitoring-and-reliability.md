# Lectoria v1.9.0 — Monitoramento e Confiabilidade de Backups (Fase 8.D)

- **Data**: 2026-11-23
- **Decisão Operacional Vigente**: O backup externo automático Restic/Rclone para Google Drive (`nosreme-restic-backup.timer`) foi **descontinuado voluntariamente** pelo proprietário na Fase 8.D (preservando os snapshots antigos no Drive e os arquivos de configuração na VPS). O **backup local diário da Lectoria** é o mecanismo principal de recuperação, complementado por **cópia externa manual dos dados essenciais** antes de atualizações e migrações de schema.

---

## 1. Estratégia Oficial de Backup e Recuperação

| Camada | Mecanismo | Periodicidade / Status | Função |
| :--- | :--- | :--- | :--- |
| **Camada 1 — Principal (Local Automático)** | `lectoria-backup.timer` → `lectoria-backup.service` | Diário `03:00 UTC` (**`enabled` / `active`**) | Gera backup consistente do SQLite (`SQLite: backup íntegro.`) em `/srv/nosreme/lectoria/backups/<timestamp>/` em 2–4s, sem dependência de rede externa. |
| **Camada 2 — Pré-Upgrade (Manual Externo)** | Cópia manual via SSH/SCP/Rsync para máquina local do proprietário | Obrigatória antes de upgrades/migrações (`v12 → v13`) | Garante cópia off-site dos dados essenciais (`lectoria.db`, `covers/`, `compose.yaml`) fora da VPS antes de alterações estruturais. |
| **Camada Legada (Desativada)** | `nosreme-restic-backup.timer` (Restic/Rclone → Google Drive) | **`disabled` / `inactive`** (descontinuado voluntariamente) | Snapshots anteriores preservados no Drive; scripts/unidades preservados no disco caso o proprietário decida reativar futuramente. |

---

## 2. Rotina Leve de Verificação do Backup Local (Sem Stack Externa)

### 2.1 Checklist Rápido Semanal (ou Pré-Release)
```bash
# 1. Confirmar que o timer local esta ativo e agendado (NEXT != '-')
systemctl is-enabled lectoria-backup.timer
systemctl is-active lectoria-backup.timer
systemctl list-timers lectoria-backup.timer --all --no-pager

# 2. Confirmar ultima execucao com status=0/SUCCESS
systemctl show lectoria-backup.service \
  -p ActiveState -p SubState -p Result -p ExecMainExitTimestamp -p ExecMainStatus

# 3. Confirmar que o timer remoto descontinuado permanece desativado
systemctl is-enabled nosreme-restic-backup.timer || true
```

### 2.2 Sinais de Alerta
- `lectoria-backup.timer` com `NEXT = -` ou `inactive`.
- `lectoria-backup.service` com `Result != success` ou ausência da pasta diária em `/srv/nosreme/lectoria/backups/`.
- Espaço em disco de `/` (`/dev/sda1`, atualmente `30G` livres) abaixo de `5G`.

---

## 3. Critérios Obrigatórios Antes do Deploy da v1.9.0 (`v12 → v13`)

1. **Backup SQLite local íntegro**: confirmar execução recente bem-sucedida de `lectoria-backup.service` (`SQLite: backup íntegro.`) ou executar backup manual consistente imediatamente antes do upgrade.
2. **Cópia externa manual dos dados essenciais**: copiar para fora da VPS (ex.: máquina local do proprietário) o backup consistente de `lectoria.db`, o diretório `data/covers/` e `/srv/nosreme/lectoria/compose.yaml`.
3. **Plano de rollback da migração `v12 → v13` revisado**: manter preservado o arquivo pré-migração (`lectoria_pre_migration_v12_to_v13.db.bak`) e seguir o procedimento seguro de parada/restauração documentado em [32-production-configuration-proposal.md](32-production-configuration-proposal.md).

---

## 4. Referência Técnica Caso Deseje Reativar Backup Externo no Futuro

Todo o diagnóstico da Fase 8.D está preservado em [34-phase-8d-backup-recovery.md](34-phase-8d-backup-recovery.md). Caso o proprietário opte por reativar Restic/Rclone no futuro, recomenda-se aplicar previamente: `TimeoutStartSec=3h`, `Persistent=false`, `flock`, Client ID OAuth próprio (evitando o projeto compartilhado `202264815644`), `-o rclone.connections=2`, flags de controle de taxa (`--tpslimit`, `--timeout`, `--contimeout`) e atualização do `rclone v1.60.1-DEV`.
