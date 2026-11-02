# Implantação e Operação do Lectoria (Positivo C1260 / Linux Mint XFCE)

Este diretório reúne os scripts de instalação, configuração, atualização, backup, restauração, diagnóstico e a unidade `systemd` do Lectoria.

## 1. Visão Geral dos Scripts

| Script | Finalidade |
| :--- | :--- |
| `./deployment/install.sh` | Instala de forma idempotente: valida dependências (`python3`, `venv`, `sqlite3`, `zenity`), cria/recria `.venv`, instala `requirements.txt`, gera `.env` + `lectoria.service` e inicializa/migra `data/lectoria.db`. |
| `./deployment/configure.sh` | Configura interativamente (ou via flags `--non-interactive`) porta, host, diretórios persistentes (`.env`) e reescreve `deployment/lectoria.service` com os caminhos e usuário reais da máquina. |
| `./deployment/update.sh` | Atualiza o código de forma segura: cria backup preventivo obrigatório, executa `git pull` (opcional `--skip-git`), atualiza dependências, reconcilia `.env` e aplica migrações de schema preservando todos os dados. |
| `./deployment/backup.sh` | Gera um backup online consistente do banco SQLite (`data/lectoria.db`) + capas personalizadas (`data/covers/*_custom.*`) em `backups/`. |
| `./deployment/restore.sh` | Lista ou restaura um backup validando integridade, gerando cópia de salvaguarda (`data/lectoria_pre_restore.db.bak`), atualizando schema e recuperando capas personalizadas. |
| `./deployment/diagnose.sh` | Executa `scripts/diagnose.py` para verificar hardware, dependências, permissões `R/W`, SQLite WAL/integridade, fontes de biblioteca, porta `8765`, URL LAN, memória RSS e latência. |
| `./deployment/uninstall.sh` | Remove a unidade `lectoria.service` do `systemd` preservando integralmente `data/`, `backups/`, `books/` e `.env`. |

## 2. Instalação Rápida no Positivo C1260

1. **Instalar pacotes básicos do Linux Mint (uma única vez)**:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip sqlite3 zenity git
   ```

2. **Executar o instalador como seu usuário comum**:
   ```bash
   ./deployment/install.sh
   ```

3. **Instalar e ativar o serviço automático no `systemd`**:
   ```bash
   sudo cp deployment/lectoria.service /etc/systemd/system/lectoria.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now lectoria.service
   ```

4. **Executar o diagnóstico de homologação**:
   ```bash
   ./deployment/diagnose.sh
   ```

## 3. Segurança e Convivência com o Jellyfin

O arquivo `deployment/lectoria.service` aplica isolamento e limites de recursos projetados para o Positivo C1260 (Intel Celeron 847 1.5 GHz / 8 GB DDR3L):
- `ProtectSystem=full` e `ProtectHome=read-only` com `ReadWritePaths` restrito apenas aos diretórios de dados (`data/`) e backups (`backups/`).
- `PrivateTmp=true` e `NoNewPrivileges=true`.
- `MemoryMax=512M` e `CPUWeight=100`, protegendo a execução simultânea do Jellyfin.
