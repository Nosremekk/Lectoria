# Guia de Implantação e Operação do Lectoria

Este documento descreve os procedimentos de implantação do **Lectoria** via Docker Compose, configuração de variáveis de ambiente, volumes persistentes, rotinas de backup e diretrizes para operação em produção.

---

## 1. Implantação com Docker Compose

O repositório disponibiliza um arquivo padronizado [docker-compose.yml](file:///home/vini/Jellyfin%20livros/docker-compose.yml) e um modelo de referência [docker-compose.example.yml](file:///home/vini/Jellyfin%20livros/docker-compose.example.yml).

### 1.1 Exemplo de `docker-compose.yml`
```yaml
services:
  lectoria:
    image: ghcr.io/nosremekk/lectoria:latest
    container_name: lectoria
    restart: unless-stopped
    ports:
      - "8765:8765"
    environment:
      - LECTORIA_HOST=0.0.0.0
      - LECTORIA_PORT=8765
      - LECTORIA_DATA_DIR=/app/data
      - LECTORIA_LIBRARY_DIR=/app/books
      - LECTORIA_BACKUPS_DIR=/app/backups
      - TZ=America/Sao_Paulo
    volumes:
      # Banco de dados SQLite, sessões e capas personalizadas
      - lectoria_data:/app/data
      # Diretório de livros digitais (PDF e EPUB) no host (somente-leitura)
      - ./books:/app/books:ro
      # Cópias de segurança automáticas e pré-migração
      - lectoria_backups:/app/backups
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/books', timeout=2)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  lectoria_data:
    name: lectoria_data
  lectoria_backups:
    name: lectoria_backups
```

---

## 2. Variáveis de Ambiente Suportadas

O Lectoria lê configurações a partir de variáveis de ambiente do sistema ou de um arquivo local `.env` (via [config.py](file:///home/vini/Jellyfin%20livros/config.py)):

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `LECTORIA_HOST` | `0.0.0.0` | Endereço IP de escuta HTTP do Uvicorn. |
| `LECTORIA_PORT` | `8765` | Porta TCP padrão (escolhida para evitar conflitos com Jellyfin 8096/8920). |
| `LECTORIA_DATA_DIR` | `<STORAGE_ROOT>/data` | Diretório raiz para arquivos mutáveis de dados. |
| `LECTORIA_DB_PATH` | `<DATA_DIR>/lectoria.db` | Caminho do arquivo SQLite principal. |
| `LECTORIA_COVERS_DIR` | `<DATA_DIR>/covers` | Cache de miniaturas de capa e capas customizadas. |
| `LECTORIA_LIBRARY_DIR` | `<STORAGE_ROOT>/books` | Diretório padrão de livros para o acervo. |
| `LECTORIA_BACKUPS_DIR` | `<STORAGE_ROOT>/backups` | Diretório de destino para backups SQLite. |
| `TZ` | `UTC` | Fuso horário para registro de datas e horários nos logs. |

---

## 3. Gestão do SQLite em Produção

1. **Modo WAL Automático**:
   - O Lectoria inicializa o banco com `PRAGMA journal_mode = WAL;`.
   - Isso gera os arquivos auxiliares `lectoria.db-wal` e `lectoria.db-shm` no volume persistente.
   - **Atenção**: Nunca copie apenas `lectoria.db` com o servidor em execução, pois transações recentes podem estar pendentes no arquivo `-wal`.
2. **Backups Seguros em Produção**:
   - Utilize sempre o utilitário interno de backup:
     ```bash
     docker compose exec lectoria python3 scripts/backup.py --backup
     ```
   - O script utiliza a **SQLite Online Backup API**, garantindo consistência atômica mesmo com usuários ativos no leitor.
3. **Verificação de Integridade**:
   - Para inspecionar a integridade lógica e estrutural do banco ativo:
     ```bash
     docker compose exec lectoria python3 scripts/backup.py --verify
     ```

---

## 4. Atualização de Versões e Migração

1. **Migração Automática**:
   - Ao iniciar um contêiner com uma nova imagem, o Lectoria executa `database.init_db()`.
   - Se a versão do schema no banco for inferior à versão suportada pelo código (ex: v10 → v11):
     1. Cria um backup pré-migração em `/app/backups/lectoria_pre_migration_v10_to_v11_<timestamp>.db.bak`.
     2. Executa o DDL da migração dentro de uma transação atômica.
     3. Em caso de sucesso, atualiza `schema_migrations` para a nova versão.
     4. Em caso de falha, emite `ROLLBACK`, preservando o banco na versão anterior.
2. **Procedimento de Atualização Padrão**:
   ```bash
   docker compose pull
   docker compose up -d
   ```
3. **Rollback de Emergência**:
   - Caso seja necessário reverter uma atualização:
     1. Pare o contêiner: `docker compose down`.
     2. Substitua `lectoria.db` pela cópia pré-migração salva em `lectoria_backups`.
     3. Aponte a imagem no `docker-compose.yml` para a tag estável anterior (ex: `v1.6.3`).
     4. Suba novamente o serviço: `docker compose up -d`.

---

## 5. Diretrizes de Segurança e Rede

- **Reverse Proxy**: Recomenda-se publicar o Lectoria atrás de um proxy reverso (como Nginx, Caddy ou Traefik) com terminação TLS/HTTPS para tráfego seguro em redes públicas.
- **Proteção do Acervo Original**: O volume de livros (`/app/books`) deve ser montado preferencialmente com a flag `:ro` (somente leitura), garantindo que os arquivos originais nunca sofram alterações.
- **Segredos e Credenciais**: Nenhuma credencial ou chave privada deve ser fixada em código ou versionada no Git.
