# Changelog — Lectoria

Todas as alterações notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [1.1.1] - 2026-11-01

### Segurança e Auditoria
- **Hashing SHA-256 de Tokens de Sessão (`auth_service.py`)**:
  - Armazenamento de tokens na tabela `sessions` convertido para hashes SHA-256, impedindo o sequestro de sessões caso ocorra vazamento de backups ou leitura indevida do banco de dados SQLite.
  - Compatibilidade mantida para sessões preexistentes.
- **Revogação Automática de Sessões em Troca de Senha e Desativação (`auth_service.py`)**:
  - Alterações de senha (`new_password`) e desativação de conta (`is_active = False`) agora revogam imediatamente todas as sessões ativas do usuário do SQLite.
  - Exclusão de usuário (`delete_user`) limpa explicitamente todos os tokens de sessão associados.
  - Limpeza automática de sessões expiradas acionada durante autenticação e logout.
- **Proteção Completa contra CSRF (Cross-Site Request Forgery) (`main.py`, `app.js`, `reader.js`)**:
  - Middleware de segurança com validação rigorosa de `Origin` e `Referer` contra o cabeçalho `Host` em todas as requisições que modificam estado (`POST`, `PUT`, `DELETE`, `PATCH`).
  - Implementação de proteção *Double Submit Cookie* com cookie `lectoria_csrf` (`SameSite=Lax`, `Path=/`).
  - Interceptador JavaScript nativo em `app.js` e `reader.js` que injeta automaticamente o cabeçalho `X-CSRF-Token` em todas as requisições de mutação.
  - Suporte a `emergencySave` (`beforeunload`/`pagehide`) via `sendBeacon` e `fetch(keepalive)` com token CSRF via parâmetro de consulta (`?csrf_token=...`).
- **Correção de Probing e Vazamento de Dados entre Usuários em Coleções (`library.py`)**:
  - O filtro `collection_id` em `get_books` agora valida estritamente a posse da coleção pelo usuário (`WHERE c.id = ? AND c.user_id = ?`), impedindo que o Usuário A descubra quais livros estão presentes em coleções privadas do Usuário B.
- **Prevenção de IDOR e Vazamento de Texto Privado em Notas (`annotations_service.py`)**:
  - `create_note` agora valida explicitamente se o `highlight_id` informado pertence ao mesmo `user_id` e `book_id`, impedindo vinculação maliciosa a destaques de outros usuários e vazamento de trechos privados via `LEFT JOIN`.
  - Validação estrita de posse em `get_note_by_id`, `add_book_to_collection` e `remove_book_from_collection`.
- **Bloqueio de Pastas Críticas como Fontes de Biblioteca (`sources_service.py`)**:
  - Adicionado bloqueio explícito contra cadastro dos diretórios `.venv` e `.git` como fontes de livros.
- **Proteção contra Vazamento de Informações Internas (`main.py`)**:
  - Endpoint `GET /api/health` sanitizado para retornar apenas o status do serviço, nome da aplicação e versão, sem expor os caminhos absolutos do sistema de arquivos (`data_dir` e `library_dir`).
- **Endurecimento do Serviço systemd (`deployment/lectoria.service`)**:
  - Adicionadas diretivas `ProtectHome=read-only` e `ReadWritePaths` restritas a `data/` e `backups/`, garantindo o princípio do menor privilégio.

### Corrigido
- Remoção de código morto e duplicado na função `delete_collection` de `collections_service.py`.
- Suporte a caminho customizado de banco em `init_db(db_path)` e `get_db(db_path)` para testes isolados e auditorias.

### Testes
- Adicionados 9 testes automatizados na suíte `tests/test_iteration7.py`, cobrindo hashing de sessões, invalidação de sessões, validação de CSRF, prevenção de IDOR em notas, isolamento de coleções, bloqueio de pastas em fontes, integridade em backups multiusuário e integridade de `/api/health` (total: 61 testes aprovados).

---

## [1.1.0] - 2026-11-01

### Adicionado
- **Download Direto de Arquivos PDF Originais (`GET /api/books/{book_id}/download`)**:
  - Streaming eficiente de arquivos binários em chunks, mantendo consumo de memória RAM próximo de zero.
  - Cabeçalho `Content-Disposition: attachment; filename="..."` com sanitização segura de caracteres para início imediato do download no navegador.
  - Botão de download integrado nos cards da biblioteca (modos grade e lista) e na barra superior do leitor de PDFs.
  - Validações de segurança: verificação de existência física, confirmação de que a fonte associada está ativa e proteção estrita contra *Path Traversal*.
- **Gerenciamento de Múltiplas Fontes de Biblioteca (`sources_service.py` e Schema V4)**:
  - Tabela `library_sources` no SQLite permitindo múltiplos diretórios locais configuráveis.
  - Cadastro, listagem, ativação/desativação, varredura individual e exclusão de fontes via interface web (modal administrativo) e API.
  - Bloqueio rígido de segurança contra inclusão de diretórios de sistema (`/`, `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/proc`, `/root`, `/sys`, `/usr`) e pastas internas da aplicação (`data/`, `backups/`, `BASE_DIR`).
  - Resolução canônica de links simbólicos, ignorando e rejeitando arquivos que apontem para fora do diretório da fonte autorizada.
  - Garantia de preservação: a exclusão ou desativação de uma fonte no Lectoria **NUNCA apaga arquivos PDF físicos do disco**.
- **Autenticação, Perfis de Usuário e Papéis (`auth_service.py` e `scripts/manage_users.py`)**:
  - Perfis domésticos multiusuário com dois níveis de privilégio: Administrador (`admin`) e Leitor (`reader`).
  - Hashing seguro de senhas com `hashlib.scrypt` (salt aleatório de 16 bytes, `N=16384`, `r=8`, `p=1`) da biblioteca padrão do Python, sem dependências externas.
  - Sessões criptografadas com tokens seguros (`secrets.token_urlsafe(32)`) transmitidos via cookie `lectoria_session` com flags `HttpOnly`, `SameSite=Lax`.
  - Proteção contra ataques de força bruta com rate-limiting em memória (bloqueio temporário de 5 minutos após 5 falhas consecutivas).
  - Fluxo de configuração inicial (*setup wizard*) na primeira execução caso nenhum administrador esteja configurado.
  - Utilitário administrativo CLI completo em `scripts/manage_users.py` (`--create-admin`, `--create-user`, `--reset-password`, `--set-role`, `--activate`, `--deactivate`, `--list`).
- **Isolamento Estrito de Dados Pessoais de Leitura**:
  - Catálogo compartilhado de PDFs, mas com isolamento total por usuário para:
    - Progresso de leitura (`reading_progress`).
    - Destaques de texto / marca-texto (`highlights`).
    - Notas e anotações vinculadas (`notes`).
    - Desenhos livres vetoriais por página (`page_drawings` com chave única composta `book_id, page, user_id`).
    - Coleções personalizadas e atribuição de documentos (`collections` e `collection_books`).
- **Migração Transacional Não-Destrutiva de Banco de Dados (`_apply_migration_v4`)**:
  - Atualização do schema para a versão 4 preservando todo o acervo e atribuindo dados preexistentes ao usuário padrão.
- **Suíte de Testes Automatizados da Iteração 6 (`tests/test_iteration6.py`)**:
  - 8 novos testes cobrindo downloads, múltiplas fontes, rejeição de symlinks externos, preservação de arquivos no disco, fluxo de autenticação, força bruta, autorização de papéis e isolamento total de dados entre usuários (total da suíte: 52 testes aprovados).

---

## [1.0.0] - 2026-11-01

### Adicionado
- **Unidade de Serviço systemd (`deployment/lectoria.service`)**:
  - Inicialização automática no boot do sistema operacional com reinício sob falha (`Restart=on-failure`, `RestartSec=5`).
  - Sandboxing de segurança systemd (`ProtectSystem=full`, `PrivateTmp=true`, `NoNewPrivileges=true`).
  - Limites de recursos no cgroup (`MemoryMax=512M`, `CPUWeight=100`) para proteger a estabilidade do hardware Positivo C1260 e a coexistência com o Jellyfin.
  - Documentação completa em `deployment/README.md`.
- **Endurecimento de Segurança na Importação de JSON (`export_service.py` e `main.py`)**:
  - Limite rígido de tamanho do corpo HTTP de 15 MB (`HTTP 413 Content Too Large`) antes do parsing na memória.
  - Verificação recursiva de profundidade máxima de aninhamento JSON (`MAX_JSON_DEPTH = 10`) contra ataques de esgotamento de pilha (Stack Overflow DoS).
  - Limites rígidos de contagem de itens por importação: 5.000 destaques, 5.000 notas, 1.000 páginas de desenho, 500 traços por página e 5.000 pontos por traço.
  - Validação estrita de limites de caracteres (10.000 caracteres para texto selecionado, 20.000 para notas) e limites de coordenadas normalizadas (`-0.5` a `1.5`).
- **Segurança e Concorrência de Backup e Restauração (`backup_service.py` e `scripts/backup.py`)**:
  - Trava de exclusão mútua (`threading.Lock`) impedindo colisões e escritas concorrentes durante rotinas de backup e restauração.
  - Mecanismo de salvaguarda com geração de backup prévio automático (`data/lectoria_pre_restore.db.bak`) antes de substituir a base principal no `restore_backup`.
  - Reversão atômica automática (*rollback*) para o banco anterior se a integridade do banco restaurado falhar.
  - Funcionalidade de retenção manual de snapshots (`prune_backups` / `scripts/backup.py --prune <N>`).
- **Suíte de Testes da Versão 1.0 (`tests/test_iteration5.py`)**:
  - 11 novos testes automatizados elevando a cobertura para 44 testes (100% de aprovação).

### Modificado
- **Gerenciamento de Recursos no Frontend (`static/js/reader.js`)**:
  - Desconexão explícita de `thumbnailObserver` ao fechar a gaveta de miniaturas ou alternar de painel, eliminando o consumo ocioso de CPU e memória RAM no Intel Celeron 847.
  - Reconexão inteligente observando apenas miniaturas pendentes ao reabrir a gaveta.
  - Desconexão e liberação de `thumbnailObserver` e `continuousObserver` nos eventos `beforeunload` e `pagehide`.

---

## [0.4.0] - 2026-10-31

### Adicionado
- **Caderno de Estudos e Anotações Avançado**:
  - Agrupamento de anotações por página com ordenação natural e carimbos de data/hora.
  - Busca textual em tempo real no documento (citações, notas e números de página).
  - Filtros dinâmicos por tipo: *Todas*, *Destaques*, *Notas* e *Desenhos*.
- **Exportação e Importação de Estudos**:
  - Exportação em Markdown (`.md`) formatado e estruturado.
  - Exportação em JSON (`.json`) para migração e backup com preservação de geometrias normalizadas.
  - Importação com validação de schema, tela de pré-visualização (*preview modal*), detecção de duplicatas e transação atômica no SQLite com rollback.
- **Navegação e Usabilidade no Leitor**:
  - Barra de progresso visual percentual no topo do leitor.
  - Histórico de navegação com botão e atalho (`b`) para retornar à última página.
  - Gaveta lateral de miniaturas de páginas (*thumbnails*) com carregamento sob demanda (*lazy loading*) via `IntersectionObserver`.
  - Indicadores visuais coloridos (*badges*) nas miniaturas para páginas com anotações.
  - Modal com guia rápido de atalhos de teclado (`?`).
- **Sistema de Backup Nativo**:
  - Módulo `backup_service.py` utilizando a SQLite Online Backup API.
  - Script de linha de comando `scripts/backup.py` para criar, listar, verificar e restaurar cópias de segurança.

---

## [0.3.0] - 2026-10-31

### Adicionado
- **Anotações Textuais e Marca-texto**:
  - Ferramenta de marca-texto com paleta de cores (Amarelo, Verde, Azul, Rosa, Laranja).
  - Seleção de texto no PDF.js com gravação de coordenadas normalizadas independentes de zoom ou resolução.
  - Notas de estudo vinculadas a trechos destacados ou avulsas por página.
- **Desenho Livre Vetorial em SVG**:
  - Camada de desenho manual vetorial sobreposta a cada página do documento.
  - Persistência das coordenadas dos traços em SQLite (`page_drawings`).
  - Ferramenta de Borracha com detecção geométrica de colisão ponto-a-segmento para exclusão pontual de traços.
- **Versionamento Git Local**:
  - Inicialização do repositório Git local com rastreamento da base SQLite `data/lectoria.db` e proteção dos arquivos originais em `books/`.

---

## [0.2.0] - 2026-10-31

### Adicionado
- **Organização da Biblioteca e Coleções**:
  - Criação, edição e exclusão de coleções personalizadas.
  - Associação de múltiplos livros a coleções em lote.
  - Filtros por estado de leitura (*Todos*, *Não iniciados*, *Em andamento*, *Concluídos*).
  - Busca dinâmica por autor e título.
  - Ordenação por título, autor, páginas e progresso recente.
- **Aprimoramentos do Leitor PDF**:
  - Alternância entre visualização em Página Única e Rolagem Contínua virtualizada (com descarregamento dinâmico de páginas fora do viewport).
  - Ajustes de zoom (*Fit Width*, *Fit Page* e livre).
  - Suporte completo a temas Claro e Escuro persistidos em `localStorage`.

---

## [0.1.0] - 2026-10-31

### Adicionado
- **Fundação da Arquitetura**:
  - Servidor backend em Python com FastAPI e Uvicorn (processo único, econômico).
  - Banco de dados SQLite em modo WAL (`synchronous=NORMAL`) com migrações automáticas.
  - Scanner de biblioteca com extração de metadados via `pypdf` sem modificar PDFs originais.
  - Streaming de arquivos PDF com suporte nativo a HTTP Range Requests (206 Partial Content).
  - Leitor PDF baseado em PDF.js Vanilla offline local.
  - Salvamento automático de progresso de leitura via API e Beacon API.
