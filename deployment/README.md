# Implantação do Lectoria via systemd

Este diretório contém os arquivos necessários para configurar a execução do Lectoria como um serviço gerenciado pelo `systemd` no Linux Mint (ou qualquer distribuição compatível com systemd).

## 1. Instruções de Instalação

1. **Ajuste de Caminhos e Usuário**:
   Abra `deployment/lectoria.service` e verifique se o usuário (`User`), grupo (`Group`) e os caminhos em `WorkingDirectory` e `ExecStart` correspondem ao seu ambiente de instalação. Por padrão, está configurado para:
   - Usuário: `vini`
   - Diretório: `/home/vini/Jellyfin livros`
   - Virtualenv: `/home/vini/Jellyfin livros/.venv/bin/python3`

2. **Copiar o arquivo para o systemd do sistema**:
   ```bash
   sudo cp deployment/lectoria.service /etc/systemd/system/lectoria.service
   ```

3. **Recarregar os daemons do systemd**:
   ```bash
   sudo systemctl daemon-reload
   ```

4. **Habilitar a inicialização automática no boot**:
   ```bash
   sudo systemctl enable lectoria.service
   ```

5. **Iniciar o serviço**:
   ```bash
   sudo systemctl start lectoria.service
   ```

6. **Verificar o status e logs**:
   ```bash
   # Status do serviço
   sudo systemctl status lectoria.service

   # Acompanhamento de logs em tempo real
   sudo journalctl -u lectoria.service -f
   ```

## 2. Parada e Reinicialização

- Para reiniciar o Lectoria:
  ```bash
  sudo systemctl restart lectoria.service
  ```
- Para parar temporariamente:
  ```bash
  sudo systemctl stop lectoria.service
  ```
- Para desabilitar a inicialização automática:
  ```bash
  sudo systemctl disable lectoria.service
  ```

## 3. Segurança e Convivência com o Jellyfin

O arquivo de serviço possui diretivas de sandboxing configuradas:
- `ProtectSystem=full`: Monta os diretórios de sistema (`/usr`, `/boot`, `/etc`) como somente-leitura para o processo.
- `PrivateTmp=true`: Isola o `/tmp` do processo.
- `NoNewPrivileges=true`: Impede elevação de privilégios via SUID.
- `MemoryMax=512M`: Evita que picos de consumo consumam a memória do servidor doméstico (8 GB DDR3L), resguardando o Jellyfin.
- `CPUWeight=100`: Prioridade justa de CPU, mantendo fluidez do processador Intel Celeron 847.
