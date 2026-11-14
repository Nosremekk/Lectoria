# ADR-001: SQLite como Banco de Dados Primário

- **Status**: Aceito (implementado desde v1.0.0, expandido na v1.7.0)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
O Lectoria foi projetado para operar como um servidor pessoal auto-hospedado (*self-hosted*), rodando com frequência em hardware doméstico de baixa potência (ex: processadores Intel Celeron e mini-PCs) convivendo com servidores de mídia como o Jellyfin. A introdução de um SGBD cliente-servidor tradicional (PostgreSQL, MySQL ou MariaDB) exigiria processos adicionais em segundo plano consumindo entre 100 MB e 300 MB de memória RAM constante, além de requerer configuração de conexões de rede, credenciais e manutenção operacional externa.

## Decisão
Utilizar o **SQLite 3** em modo WAL (*Write-Ahead Logging*) como o único mecanismo de banco de dados do Lectoria:
- Configurado com `PRAGMA foreign_keys = ON;`, `PRAGMA journal_mode = WAL;` e `PRAGMA synchronous = NORMAL;`.
- Conexões isoladas gerenciadas por `database.get_connection()`.
- Versionamento incremental de schema via tabela `schema_migrations`.
- Backup atômico online via `sqlite3.Connection.backup` com lock de concorrência.

## Consequências
### Positivas
- Zero consumo de memória em repouso por processos externos de banco de dados.
- Implantação trivial: backup e portabilidade consistem em um único arquivo de banco de dados (`lectoria.db`).
- Latência de leitura inferior a 1 ms por eliminar roundtrips de socket TCP/Unix.
- Suporte nativo a busca textual com FTS5 sem necessidade de ElasticSearch ou MeiliSearch.

### Negativas / Limitações
- Concorrência de escrita limitada a um único escritor por vez (mitigada satisfatoriamente pelo modo WAL, transações curtas e público-alvo de servidor pessoal/comunitário).

*Nota: Justificativa histórica confirmada pelas diretrizes centrais do projeto em README.md e CONTRIBUTING.md.*
