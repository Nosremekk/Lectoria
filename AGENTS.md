# Lectoria — Guia para Agentes de IA (AGENTS.md)

Este documento é o **ponto de entrada obrigatório** para agentes de IA que analisam, refatoram, testam ou expandem o código-fonte do **Lectoria**.

Antes de propor ou aplicar qualquer modificação, consulte os documentos técnicos especializados listados neste guia.

---

## 1. Visão Geral do Projeto

O **Lectoria** é uma aplicação *self-hosted* de biblioteca digital e leitura colaborativa para documentos **PDF** e livros digitais **EPUB**.
- **Foco Arquitetural**: Sobriedade estética editorial, velocidade, baixo consumo de memória RAM, CPU e I/O de disco, permitindo convivência pacífica com servidores de mídia (como Jellyfin) em hardware doméstico modesto (ex: Intel Celeron 847).
- **Licença**: Código aberto sob licença MIT.
- **Versão Atual**: `1.8.0` (base estável anterior: `1.7.0`).
- **Schema SQLite**: Versão 12.

---

## 2. Stack Tecnológica

| Camada | Tecnologia | Justificativa / Diretriz |
| :--- | :--- | :--- |
| **Backend** | Python 3.10+ / FastAPI / Uvicorn | Roteamento assíncrono enxuto, tipagem estrita com Pydantic v2. |
| **Persistência** | SQLite 3 (modo WAL) | Banco embutido de arquivo único, zero dependência externa de SGBD. |
| **Frontend** | Vanilla HTML5 / CSS3 / ES6+ | Zero frameworks (sem React/Vue), zero ferramentas de build (sem Vite/Webpack). |
| **Leitor PDF** | PDF.js (Mozilla) | Renderização local com suporte nativo a HTTP Range (206) e chunking de 512 KB. |
| **Leitor EPUB** | EPUB.js + JSZip | Renderização pura no navegador, sem conversão intermediária no backend. |
| **Containers** | Docker / Docker Compose | Implantação auto-hospedada em contêiner único ou via compose. |
| **Testes** | Pytest + Starlette TestClient + Playwright | Testes unitários, de integração de API e E2E no Chromium headless. |

---

## 3. Mapa de Módulos do Sistema

### Backend (Python)
- `main.py`: Ponto de entrada FastAPI, injeção de dependências (`require_user`, `require_admin`), middleware CSRF, rotas REST e resolução leve de arquivos (`/api/books/{id}/file`).
- `database.py`: Inicialização do banco, schema SQLite, migrações incrementais (`_apply_migration_v11`), triggers e foreign keys.
- `shared_reading_service.py`: Regras de negócio da Leitura Compartilhada (tópicos, respostas encadeadas, metas, progresso voluntário e citações).
- `clubs_service.py`: Ciclo de vida de Clubes de Leitura, papéis (`owner`, `moderator`, `member`), convites e leituras coletivas.
- `social_service.py`: Camada comunitária "Nossa Biblioteca" (resenhas, avaliações, favoritos, recomendações).
- `auth_service.py`: Autenticação, hash scrypt, sessões criptográficas (tokens SHA-256) e rate limiting.
- `library.py`: Varredura de acervo, metadados de arquivos, resolução leve de caminhos e integridade.
- `covers_service.py`: Extração em 3 camadas de capas (capa real, personalizada ou SVG determinístico).
- `annotations_service.py`: Destaques, notas privadas e desenhos vetoriais do usuário.
- `backup_service.py`: Backup consistente via SQLite Online Backup API com trava de concorrência.
- `config.py`: Variáveis de ambiente (`LECTORIA_*`), diretórios persistentes e constantes de runtime.

### Frontend (Vanilla JS & HTML)
- `static/index.html`: Shell principal da biblioteca, abas de Descoberta, Acervo, Nossa Biblioteca e Clubes.
- `static/reader.html`: Interface do leitor adaptativo para PDF e EPUB (desktop, tablet e mobile).
- `static/js/app.js`: Lógica da aplicação principal, cache leve em memória, renderização progressiva em lotes (36 livros).
- `static/js/shared_reading.js`: Painel integrado da Leitura Coletiva (Visão Geral, Discussões, Metas, Citações), controle de spoilers e `AbortController`.
- `static/js/reader.js`: Motor do leitor híbrido (PDF.js/EPUB.js), navegação por hash (`#page=N`, `#cfi=...`), seleção e compartilhamento.
- `static/css/style.css`: Folha de estilo consolidada com temas Claro, Escuro e Sépia, contenção de layout e responsividade (320px–1920px).

---

## 4. Regras Obrigatórias de Governança para Agentes

1. **Preservação Arquitetural**:
   - Nunca introduza frameworks frontend (React, Vue, Svelte) ou etapas de compilação/bundling (npm, Vite, Webpack).
   - Nunca adicione SGBDs pesados (PostgreSQL, MySQL, Redis) — o SQLite é a escolha canônica de persistência.
   - Mantenha serviços desacoplados em arquivos `*_service.py`; não sobrecarregue `main.py` com regras de negócio.
2. **Segurança e Isolamento**:
   - Nunca derrote ou contorne verificações de segurança, sessões, papéis ou tokens CSRF.
   - Rotas de escrita (`POST`, `PUT`, `PATCH`, `DELETE`) exigem sessão autenticada e cabeçalho `x-csrf-token`.
   - Modelos Pydantic de entrada devem ter `ConfigDict(extra="forbid")` para impedir *Mass Assignment*.
   - Clubes privados são invisíveis a não-membros e devem retornar `HTTP 404 Not Found` (anti-enumeração).
   - Notas privadas de um usuário nunca podem ser acessadas ou publicadas por outro (anti-IDOR).
3. **Privacidade Absoluta**:
   - O progresso pessoal (`reading_progress`) e as anotações privadas do leitor **nunca** devem ser sincronizados compulsoriamente com feeds ou metas coletivas.
   - Toda participação em metas e citações é estritamente **voluntária**.
4. **Política de Leitura Somente-Leitura**:
   - Leituras coletivas nos estados `completed`, `cancelled` ou pertencentes a clubes com `is_archived = 1` permanecem acessíveis para consulta (`GET 200`), mas rejeitam qualquer alteração colaborativa (`HTTP 400 Bad Request`).
5. **Preservação de Histórico e Soft Delete**:
   - Exclusões de tópicos, respostas e citações utilizam exclusão lógica (`is_deleted = 1`).
   - O arquivamento de um clube ou cancelamento de uma leitura nunca remove fisicamente tópicos, respostas, metas ou citações.
   - A exclusão de usuários desassocia autoria (`ON DELETE SET NULL`), preservando o encadeamento das discussões.
6. **Desempenho em Hardware Modesto**:
   - PDFs utilizam requisições HTTP Range (206) em blocos de 512 KB (`rangeChunkSize: 524288`) com streaming desativado (`disableStream: true`), evitando downloads desnecessários de centenas de megabytes.
   - A biblioteca utiliza renderização em lotes de 36 livros e cache em memória com invalidação precisa.
   - Zero *polling* contínuo no frontend; requisições são disparadas sob demanda.
7. **Regras de Versão e Cache-Busting**:
   - O teste `test_v151_frontend_backend_version_alignment` exige sincronia entre `config.APP_VERSION`, o atributo `data-app-version` no HTML e a query string `?v=X.Y.Z` dos scripts. Alterações de versão são coordenadas na fase de release.

---

## 5. Índice de Documentação Especializada

Antes de intervir em qualquer subsistema, o agente **deve ler o documento correspondente**:

- **Arquitetura Geral e Diagramas**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Banco de Dados, Schema v11 e Migrações**: [docs/DATABASE.md](docs/DATABASE.md)
- **Segurança, Autenticação e Matriz de Permissões**: [docs/AUTHORIZATION.md](docs/AUTHORIZATION.md)
- **Leitor PDF.js e EPUB.js, Range e Navegação**: [docs/READER.md](docs/READER.md)
- **Frontend Vanilla, Estado, Spoilers e Responsividade**: [docs/FRONTEND.md](docs/FRONTEND.md)
- **Contratos da API REST e Modelos**: [docs/API.md](docs/API.md)
- **Guia de Testes Automatizados e E2E**: [docs/TESTING.md](docs/TESTING.md)
- **Implantação Docker, Volumes e Operação**: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **Decisões Arquiteturais Registradas (ADRs)**: [docs/DECISIONS/](docs/DECISIONS/)

---

## 6. Procedimento de Trabalho para Agentes

1. **Inspeção Prévia**: Consulte o status da branch (`git status`) e leia o registro mais recente em [docs/v1.7.0-progress.md](docs/v1.7.0-progress.md).
2. **Execução Basal**: Execute a suíte de testes antes de iniciar qualquer modificação para confirmar a estabilidade da branch.
3. **Modificação Focada**: Edite apenas os arquivos necessários para o escopo autorizado.
4. **Validação**: Execute os testes unitários, testes de API e testes E2E do Playwright correspondentes.
5. **Atualização Documental**: Registre novos testes, comportamentos ou decisões nos documentos pertinentes de `docs/`.
