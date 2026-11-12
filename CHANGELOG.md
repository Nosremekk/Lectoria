# Changelog — Lectoria

Todas as alterações notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [1.6.2] - 2026-11-11

### Corrigido e Otimizado
- **Correções de Interface Mobile e Eliminação de Defeitos Visuais (`static/css/style.css`)**:
  - **Eliminação de Defeitos Visuais no Chrome Android**: Remoção de `html, body { overflow-x: hidden; }` global, que causava falhas na árvore de camadas de renderização e gerava retângulos em branco/não-rasterizados (`#fcfbf7`) cobrindo elementos da tela.
  - **Hierarquia e Especificidade CSS Corrigidas**: Regras desktop isoladas em `@media (min-width: 769px)` e regras mobile em `@media (max-width: 768px)`, eliminando sobreposições acidentais de especificidade entre `.main-view-nav` e `.main-view-tabs`.
  - **Sinalização Visual de Rolagem Horizontal**: Adição de máscara de gradiente lateral (`-webkit-mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent 100%)`) e alinhamento por `scroll-snap-type: x proximity`, permitindo ao usuário identificar imediatamente que a barra de abas possui itens adicionais.
  - **Prevenção de Cortes no Mobile Estreito**: Adição de espaçamento final (`padding-right: 2rem`) em `.main-view-tabs` garantindo que o último botão ("Clubes de Leitura" e "Administração") nunca seja cortado.
  - **Contenção do Cabeçalho Administrativo e Perfis**: Aplicação de `flex-wrap: wrap` e contenção em `.admin-nav-group` e `.header-controls`, além de compactação do badge de usuário em telas de 320px a 360px, eliminando qualquer overflow horizontal residual no mobile.

- **Otimização da Biblioteca e Filtros Instantâneos (`static/js/app.js`)**:
  - **Cache Leve em Memória no Cliente (`libraryCache`)**: Armazenamento em memória dos livros do acervo associados ao `userId` ativo, eliminando requisições repetidas `/api/books` ao navegar entre abas ou alternar filtros.
  - **Filtragem e Ordenação no Cliente**: Mudanças de status (Todos, Em andamento, Não iniciados, Concluídos, Favoritos, Quero ler), categorias, coleções e ordenação executadas em menos de 1 ms no frontend, reduzindo as requisições de rede de 1,34 MB por clique para 0 B.
  - **Resiliência a Condições de Corrida**: Uso de `AbortController` para cancelar requisições assíncronas em trânsito caso o usuário troque de visão rapidamente.
  - **Invalidação e Limpeza de Sessão Rigorosas**: Invalidação imediata do cache em todas as operações de mutação (edição de metadados, capas, coleções, exclusão de livros, scan do acervo) e limpeza completa de cache no logout ou troca de usuário (`clearUserSessionCache`).

- **Renderização Progressiva e Otimização do DOM (`static/js/app.js`, `static/css/style.css`)**:
  - **Alternância Grade/Lista Pura via CSS**: Alternância instantânea (< 2 ms) entre Grid e List modificando apenas a classe do contêiner (`.books-grid` e `.books-list`), sem destruir ou reconstruir o DOM e com zero requisições HTTP.
  - **Renderização em Lotes (Chunking)**: Carregamento progressivo em lotes de 36 livros (`BOOKS_CHUNK_SIZE = 36`) com sentinela `#books-scroll-sentinel` e `IntersectionObserver`, reduzindo o volume inicial de nós do DOM de >25.000 para ~900 em acervos de 1.000 livros.
  - **Delegação Centralizada de Eventos**: Substituição de centenas de listeners individuais nos cards por um único listener centralizado em `booksContainer`.

- **Testes e Qualidade**:
  - Nova suíte de testes `tests/test_v162_interface_performance.py` validando cache, invalidação, isolamento de sessão, integridade do schema v10 e regras CSS.
  - Script de benchmark e validação `tests/run_v162_benchmarks.py` validando zero overflow em 7 resoluções (320px, 360px, 375px, 390px, 412px, 768px e 1366px) e Chrome Android emulado.
  - 100% de aprovação na suíte de testes automatizados (206 testes aprovados).

## [1.6.1] - 2026-11-11

### Otimizado
- **Otimização de Desempenho do Leitor PDF (`static/js/reader.js`)**:
  - `disableStream: true`: eliminação do streaming progressivo concorrente não intencional de 100% do arquivo, evitando saturação de banda e permitindo abertura de PDFs grandes em ~1 segundo.
  - `rangeChunkSize: 524288` (512 KB): fatiamento otimizado que reduz os saltos sequenciais de rede (RTT) em até 8x comparado ao valor histórico de 64 KB.
  - `length`: declaração antecipada do tamanho do documento fornecido pelo acervo, acelerando o cálculo de intervalos do motor PDF.js.
  - **Carregamento Progressivo de Anotações**: busca assíncrona de destaques e notas em segundo plano, eliminando o bloqueio visual da pintura do primeiro frame do canvas.
  - **Paralelização de Inicialização**: requisições de metadados do livro e progresso salvo recuperadas simultaneamente via `Promise.all`.
  - **Modo Contínuo Otimizado**: instanciação de nós de páginas via `DocumentFragment`, reduzindo reflows do DOM de centenas de operações para uma única mutação em lote.

- **API de Entrega de Documentos e Políticas de Cache (`main.py`, `library.py`)**:
  - Nova função `resolve_book_file_for_user`: substituição de `get_book()` por consulta direta, reduzindo a sobrecarga no SQLite de 8 consultas SQL e 4 conexões para 1 consulta leve sem comprometer segurança, isolamento ou checagem de fontes ativas.
  - Suporte ao método `HEAD` em `/api/books/{book_id}/file`: inspeção rápida de cabeçalhos sem download de corpo.
  - Políticas de Cache Seguras e Conservadoras: `Cache-Control: private, no-cache`, `Accept-Ranges: bytes`, `ETag` determinístico e `Last-Modified`.
  - Revalidação condicional compatível com RFC 9111 (`304 Not Modified` para `If-None-Match` e conformidade com `If-Range` / `416 Range Not Satisfiable`).

- **Testes e Qualidade**:
  - Nova suíte `tests/test_v161_pdf_performance.py` com 7 testes dedicados para streaming, Range requests, cache condicional, códigos 200/206/304/416 e segurança de autenticação.
  - 100% de aprovação na suíte completa (202 testes automatizados aprovados).

## [1.6.0] - 2026-11-10

### Adicionado
- **Lectoria v1.6.0 — Clubes de Leitura & Leitura Coletiva**:
  - **Clubes de Leitura (`reading_clubs`, `reading_club_members`, `reading_club_invites`)**:
    - Comunidades temáticas permanentes na instância com visibilidade pública ou privada.
    - Modelo de governança com três papéis: `owner` (Proprietário), `moderator` (Moderador) e `member` (Participante).
    - Gestão completa de ciclo de vida: criação, edição de propósito, arquivamento/desarquivamento e transferência de titularidade de posse sem risco de orfandade de dados.
  - **Convites Direcionados e Proteção Anti-Enumeração em Clubes Privados**:
    - Mecanismo seguro de convites para usuários cadastrados na instância (`reading_club_invites`).
    - Proprietários e moderadores autorizados podem emitir convites; destinatários recebem alerta em banner dedicado com ações de aceitar ou recusar.
    - Convites pendentes podem ser revogados pelo clube; convites não respondidos de clubes arquivados são bloqueados.
    - Proteção estrita contra vazamento de metadados (*anti-enumeration*): clubes privados são invisíveis em listagens públicas e requisições diretas de não-membros retornam estritamente `HTTP 404 Not Found`.
  - **Leituras Coletivas por Obra (`collective_reads`, `collective_read_members`)**:
    - Atividades de leitura em grupo vinculadas a obras existentes no acervo da biblioteca (`books.id`), mantendo identificadores textuais íntegros (strings hexadecimais de 16 caracteres).
    - Ciclo de vida com 4 estados bibliográficos: Planejada (`planned`), Em andamento (`in_progress`), Concluída (`completed`) e Cancelada (`cancelled`).
    - Adesão e desligamento simplificado de participantes; criador automaticamente cadastrado como primeiro participante ativo.
    - Preservação de histórico: leituras concluídas ou canceladas permanecem acessíveis no histórico do clube para consulta de metas e debates.
  - **Filtro "Leitura Coletiva" na Biblioteca e Badges Coletivos nos Cards**:
    - Nova aba de status `[data-status="collective_reads"]` na biblioteca listando exclusivamente obras vinculadas a leituras coletivas ativas em que o usuário autenticado participa.
    - Badge visual `Coletiva` nos cards de livros com caixa detalhada indicando nome do clube, estado da leitura e contagem de participantes.
    - Isolamento individual rigoroso: progresso de leitura individual (página atual, porcentagem), destaques, anotações e avaliações de cada leitor continuam estritamente privados e isolados de outros participantes.
  - **Migração SQLite Schema v9 → v10 (`database.py`)**:
    - Criação idempotente e com salvaguarda automática das tabelas `reading_clubs`, `reading_club_members`, `reading_club_invites`, `collective_reads` e `collective_read_members`, com índices de alta performance para filtros e consultas multi-usuário.
  - **Interface Web e Experiência Mobile-First (`static/index.html`, `static/css/style.css`, `static/js/app.js`)**:
    - Nova aba principal `#tab-nav-clubs` na navegação de topo com seção dedicada `#clubs-section`.
    - Banner reativo de convites recebidos com botões rápidos de Aceitar e Recusar.
    - Visão dividida: Listagem geral com filtros "Meus Clubes" / "Todos os Clubes" e Painel Detalhado do Clube com abas "Leituras Coletivas" e "Participantes & Convites".
    - Cinco modais contextuais: Criação/Edição de Clube, Nova Leitura Coletiva, Detalhes da Leitura com Ação de Ingressar/Sair, Convidar Leitor e Transferência de Propriedade.
    - Zero overflow horizontal em todas as resoluções (320px a desktop ultrawide) e consistência visual com o tema escuro editorial (*obsidian, ivory, terracotta*).
  - **Prontidão Open Source e Docker Compose (`docker-compose.yml`, `docker-compose.example.yml`)**:
    - Arquivos de orquestração Docker Compose oficiais para rápida implantação por novos usuários da comunidade open source, com mapeamento correto de volumes (`lectoria_data`, `lectoria_backups`) e verificação de saúde (*healthcheck*).
  - **Suíte de Testes Expandida**:
    - 8 novos testes de integração em `tests/test_v160_reading_clubs.py`.
    - 1 novo teste E2E multi-usuário real com Playwright Chromium em `tests/e2e/test_browser_e2e.py` validando os fluxos concorrentes de 3 usuários distintos.
    - Total de **194 testes automatizados aprovados sem regressões**.

---

## [1.5.2] - 2026-11-09

### Adicionado
- **Experiência Mobile Refinada e Layout Mobile-First (`static/css/style.css`, `static/index.html`, `static/js/app.js`)**:
  - Layout mobile-first completo validado em viewports de 320px, 375px, 390px, 768px e 1280px com ausência estrita de overflow horizontal (`scrollWidth <= clientWidth`).
  - Painel compacto colapsável de filtros (`#collapsible-filters-panel`) em telas pequenas acionado por botão dedicado (`#btn-toggle-filters-mobile`) com contador dinâmico de filtros ativos (`#active-filters-count-badge`).
  - Prateleiras horizontais com rolagem estritamente contida em `.shelf-row` e controles de navegação suaves sem vazamento de largura.
  - Botão **"Ver todos"** (`.shelf-see-all-btn`) em cada prateleira de descoberta, permitindo navegar instantaneamente para a biblioteca com o filtro temático correspondente já aplicado.
- **Gestão de Categorias Customizáveis e Migração SQLite Schema v9 (`database.py`, `taxonomy_service.py`, `main.py`)**:
  - Nova tabela `categories` com `id`, `name`, `description`, `display_order` e `is_custom`.
  - Nova tabela associativa `book_categories` para vinculação multi-categoria com suporte a trava manual (`is_manual = 1`).
  - Migração idempotente e segura `SCHEMA_VERSION = 9` com salvaguarda automática (`lectoria_pre_migration_v8_to_v9_*.db.bak`).
  - Aba de **Gestão de Categorias** no Painel Administrativo com operações completas: criação, edição de nome/descrição, reordenação por botões Subir/Descer, mesclagem (*merge*) de categorias e exclusão segura (com desvinculação em cascata e garantia absoluta de nunca excluir livros do disco ou banco).
  - Suporte ao filtro **"Sem categoria"** (`__uncategorized__`) na barra de filtros e nas prateleiras.
  - Gerenciamento de tags de categoria diretamente no modal de detalhes da obra com adição e remoção instantânea.
- **Enriquecimento com BrasilAPI e Normalização de Metadados (`metadata_service.py`, `background_enricher.py`)**:
  - Novo adapter de integração com a **BrasilAPI** (`https://brasilapi.com.br/api/isbn/v1/{isbn}`) com telemetria de latência e erros na tabela `metadata_provider_stats`.
  - Normalização de autores compostos e brasileiros no formato "Sobrenome, Nome" (ex: "Machado de Assis, Joaquim Maria" → "Joaquim Maria Machado de Assis").
  - Consulta de fallback automática à Open Library Work API para recuperação de sinopse e assuntos quando a edição não os contém.
  - Regras contextuais expandidas para autores canônicos (Tanenbaum → Ciência e tecnologia > Computação, Platão → Filosofia, Guidorizzi → Ciência e tecnologia > Matemática/Cálculo).
- **Contrato de Leitura e Correção de Conclusão Prematura (`progress.py`, `library.py`)**:
  - Prevenção de falso auto-completar: documentos de página única ou abertura inicial de EPUBs não são marcados como "Concluído" indevidamente (`percentage = 0.0` para documentos com `<= 1` página).
  - Campo `is_completed_manual` na tabela `reading_progress` e botão de alternância manual de status (`#btn-details-toggle-status`) no modal de detalhes da obra e biblioteca (`completed`, `in_progress`, `unread`).
  - Isolamento estrito de progresso de leitura por usuário autenticado.

### Modificado
- Suíte de testes expandida para **185 testes aprovados** (com testes de unidade, integração e validação visual responsiva Playwright em 5 resoluções de tela).
- Atualização da versão do projeto para `1.5.2` em backend, frontend e empacotamento.

## [1.5.1] - 2026-11-09

### Corrigido
- **Alinhamento de Contratos das Funcionalidades Sociais (`main.py`, `static/js/app.js`, `social_service.py`, `auth_service.py`)**:
  - **Avaliações, Favoritos e "Quero Ler" (`405 Method Not Allowed`)**: Adicionado suporte explícito aos métodos `PUT /api/books/{book_id}/reviews` e `PUT /api/books/{book_id}/reviews/me` além de `POST`, com persistência de `is_public` no `BookReviewPayload` sem sobrescrever `share_reviews` ao alternar apenas *Favorito* ou *Quero Ler*.
  - **Compartilhamento de Citações e Anotações (`422 Unprocessable Entity` / `[object Object]`)**: Removido `parseInt(..., 10)` sobre `book_id` hexadecimal de 16 caracteres em `app.js`, alinhado o schema `SharedAnnotationCreatePayload` para aceitar `book_id` textual (`str`) e `content`/`comment_text`, e criado `formatApiError()` no frontend para formatar listas de validação do FastAPI/Pydantic em mensagens legíveis em vez de `[object Object]`.
  - **Recomendações de Leitura (`422 Unprocessable Entity`)**: Removido `parseInt` do `book_id` no modal de recomendação, adicionado carregamento de destinatários via `GET /api/social/users` com suporte a recomendação direta (`to_user_id`) ou para o mural da comunidade, e normalizado `feed.recommendations` como lista no endpoint `GET /api/social/feed`.
  - **Persistência Visual de Perfil e Privacidade ao Reabrir Modal**: Corrigido contrato de `GET /api/profile/me` e `openProfileModal()` (`data.profile.privacy` e `data.privacy` com `cache: "no-store"`), garantindo que os checkboxes de privacidade (`share_current_reading`, `share_reading_history`, `share_reviews`, `share_lists`, `share_annotations_default`) reflitam fielmente o estado salvo no SQLite ao reabrir o painel ou recarregar a página.
  - **Sincronização de Versão Frontend/Backend (`1.5.1`)**: Atualizado `APP_VERSION = "1.5.1"`, `window.LECTORIA_FRONTEND_VERSION = "1.5.1"` e query strings de cache-busting (`?v=1.5.1`) em `static/index.html`.
- **Testes de Integração e E2E de Contratos Sociais (`tests/test_v151_social_contracts.py`)**:
  - Adicionados 5 novos testes cobrindo os contratos reais entre dois usuários distintos e teste E2E em navegador real (Playwright Chromium), totalizando **174 testes aprovados**.

## [1.5.0] - 2026-11-07

### Adicionado
- **Lectoria v1.5.0 — Biblioteca Viva (Pipeline Bibliográfico de 5 Estágios, Automação Persistente, Painel Administrativo Dedicado e Camada Social "Nossa Biblioteca")**:
- **Pipeline Progressivo de Identificação Bibliográfica em 5 Estágios (`metadata_service.py`, `background_enricher.py`)**:
  - **Estágio 1 (Extração Local Profunda)**: Varredura expandida de PDFs (páginas 1 a 10 + últimas 2 páginas de ficha catalográfica/colofão), EPUB OPF e normalização reversível de nomes de arquivos (`Autor - Título (Edição) [Coleção]`), preservando `raw_filename`, `raw_parent_dir` e `extracted_title/author` na tabela `books`.
  - **Estágio 2 (Normalização Reversível)**: Extração estruturada de autor, título limpo, subtítulo e edição sem perda dos dados originais do arquivo.
  - **Estágio 3 (Adapters Multi-Provedor com Diagnóstico)**: Integração com **Open Library**, **Google Books** e **Crossref API** (novo provedor para obras acadêmicas e técnicas), com registro de métricas de latência, rate limit (HTTP 429) e falhas na tabela `metadata_provider_stats`.
  - **Estágio 4 (Pontuação Explicável e Coerência Multi-Fonte)**: `score_candidate_detailed()` com retorno detalhado do breakdown de pontos (`isbn_points`, `title_points`, `author_points`, `edition_points`, `completeness_points`, `coherence_bonus`) e suporte a correspondência de alta precisão para títulos clássicos ou sem autor no nome do arquivo.
  - **Estágio 5 (Estados de Decisão e Proveniência por Campo)**: Novos status `identified`, `needs_review`, `ambiguous`, `not_found`, `rate_limited`, `technical_error` e `manual_override`, com rastreabilidade completa na tabela `metadata_provenance`.
- **Validação Visual Pura de Capas e Prevenção de Capas Pretas/CMYK Invertidas (`covers_service.py`)**:
  - Inspeção binária em Python puro (`struct` + `zlib`) de marcadores JPEG (`SOF0/SOF1/SOF2`, `APP14 Adobe`) e buffers de pixels brutos (`FlateDecode`), rejeitando automaticamente imagens CMYK/YCCK de 4 canais ou máscaras `/SMask` pretas extraídas de PDFs.
  - Fallback imediato para capa externa validada ou **Capa Editorial SVG Determinística** enriquecida com Categoria, Título, Autor e paleta cromática editorial.
- **Reconciliação Inteligente de Arquivos Movidos ou Renomeados (`library.py`)**:
  - Detecção de livros movidos ou renomeados via `file_hash` + `filesize` durante `scan_library()`, atualizando `filepath` e `filename` **sem recriar o `book_id`**, preservando 100% do progresso de leitura, destaques, anotações, desenhos, resenhas e coleções.
  - Proteção por `threading.Lock` (`_scan_lock`) contra varreduras concorrentes.
- **Automação da Biblioteca e Fila Persistente de Tarefas (`automation_service.py`)**:
  - Agendador configurável persistido em `scheduled_jobs` com modos `manual`, `interval`, `daily`, `weekly` e `custom_days`, horário preferencial (`run_time`) e detecção automática de mudanças nas pastas monitoradas.
  - Seis rotinas automatizadas: `scan_library`, `enrich_metadata`, `update_missing_covers`, `rebuild_search_index`, `db_maintenance` e `auto_backup`.
  - Fila persistente em `background_jobs` com recuperação automática de tarefas interrompidas (`running` -> `interrupted`) ao iniciar o servidor e cancelamento seguro.
- **Painel Administrativo Dedicado de 6 Seções (`#admin-section`)**:
  - Substituição dos modais fragmentados por uma área administrativa dedicada com 6 abas completas: *Dashboard*, *Gestão da Biblioteca*, *Gestão de Usuários*, *Automação & Fila*, *Revisão Bibliográfica* e *Manutenção & Auditoria*.
  - Registro de ações administrativas na tabela `admin_audit_log`.
- **Camada Multiusuário e Social "Nossa Biblioteca" com Privacidade por Padrão (`social_service.py`)**:
  - Perfis de usuário (`user_profiles`) com nome de exibição, bio, cor de avatar e livro favorito.
  - Controles granulares de privacidade (`user_privacy_settings`) **100% desativados por padrão (`0`)**: compartilhamento de atividade de leitura, progresso, avaliações, citações e perfil visível apenas mediante consentimento explícito.
  - Isolamento estrito de anotações privadas: administradores **não** possuem acesso irrestrito às anotações privadas de outros usuários; apenas trechos explicitamente compartilhados em `shared_annotations` ficam visíveis na comunidade.
  - Compartilhamento opcional de citações/anotações com visibilidade `private`, `users` ou `selected_users`, comentários em trechos compartilhados (`annotation_comments`), avaliações de 1 a 5 estrelas + resenhas + *Quero Ler* + *Favorito* (`book_reviews`), recomendações diretas entre leitores (`book_recommendations`) e feed comunitário **"Nossa Biblioteca"** (`reading_activity`).
- **Migração Automática e Segura SQLite Schema v7 → v8 (`database.py`)**:
  - Backup preventivo automático pré-migração (`lectoria_pre_migration_v7_to_v8_*.db.bak`) e criação idempotente de 13 novas tabelas e índices.
- **Suíte Completa de Testes (169 Testes Aprovados)**:
  - Novos testes unitários e de integração em `tests/test_v150_biblioteca_viva.py` cobrindo migração v7→v8, pipeline bibliográfico, validação anti-CMYK de capas, reconciliação de arquivos movidos, agendador/fila de tarefas, isolamento de privacidade por padrão e recursos sociais.

### Corrigido
- **Prateleiras de Descoberta (`"Erro ao carregar prateleiras de descoberta"`)**:
  - Correção da condição de corrida no frontend (`initAuth()` agora recarrega as prateleiras após confirmar a sessão autenticada) e eliminação do gargalo N+1 em `library.get_discovery_shelves()`, reduzindo ~60 queries SQL por requisição para uma única leitura em memória com isolamento `try/except` por prateleira.

## [1.4.0] - 2026-11-06

### Adicionado
- **Descoberta Inteligente e Prateleiras Dinâmicas (Estilo Streaming / Jellyfin)**:
  - Navegação visual fluida na tela inicial com carrosséis horizontais dedicados: *Continuar Lendo*, *Adicionados Recentemente*, *Categorias Principais*, *Autores em Destaque*, *Para Começar a Ler*, *Leituras Concluídas* e *Coleções*.
  - Garantia de que nenhuma prateleira vazia é exibida, com rolagem suave por toque (*swipe*) ou controles direcionais.
  - Alternância imediata entre a visualização de **Descoberta** e a visão tradicional de **Biblioteca & Explorar** (Grade e Lista).
- **Taxonomia Hierárquica e Classificação Multidimensional**:
  - Árvore canônica estruturada em cinco grandes eixos: *Filosofia*, *Literatura*, *Ciências Humanas*, *Ciência e Tecnologia*, e *Artes e Cultura*, com dezenas de subcategorias temáticas.
  - Separação multidimensional de facetas bibliográficas: *Categoria*, *Subcategoria*, *Gênero Literário* (Romance, Ensaio, Conto, Poesia, etc.), *Origem / Nacionalidade* (Literatura brasileira, latino-americana, etc.) e *Período Histórico*.
  - Sincronização automática na varredura e suporte a vínculos manuais protegidos (`is_manual = 1`).
  - Filtros combinados instantâneos na aba de Exploração com dropdowns dinâmicos baseados no acervo real (`GET /api/taxonomy/tree`).
- **Motor de Busca Textual FTS5 Nativamente Integrado**:
  - Tabela virtual `books_fts` em SQLite para busca instantânea em frações de milissegundo por título, autor, sinopse, editora e assuntos, com suporte a prefixos e termos múltiplos.
- **Identificação Bibliográfica e Reconciliação Externa**:
  - Consulta automática e manual a fontes públicas confiáveis: **Open Library API** (primária, com rate limit defensivo de 1 req/s) e **Google Books API** (complementar).
  - Algoritmo de correspondência inteligente (*Smart Matcher*) com pontuação ponderada (0 a 100):
    - *Reliable* (Score >= 85): correspondência segura por ISBN e título/autor.
    - *Needs Review* (50 <= Score < 85): correspondência por obra/título que exige revisão manual.
    - *Insufficient* (Score < 50): descartado automaticamente para evitar dados incorretos.
  - Auditoria e histórico de candidatos registrados na tabela `metadata_candidates`.
- **Fila Assíncrona de Enriquecimento em Segundo Plano**:
  - Worker sequencial com taxa controlada e baixa prioridade de CPU/IO, ideal para hardware modesto como o Positivo C1260.
  - **Central de Metadados** acessível por administradores com métricas em tempo real (total, processados, identificados, revisão necessária, falhas) e controles de iniciar, pausar, cancelar e enfileirar.
  - Cache local com TTL de 7 dias (`metadata_api_cache`) para evitar consultas redundantes de rede.
- **Modal Rico de Detalhes da Obra**:
  - Exibição de sinopse detalhada, capa ampliada, tags taxonômicas clicáveis, editora, ano de publicação, idioma, ISBN-10, ISBN-13 e formato.
  - Subabas para *Visão Geral*, *Reconciliação Externa* (com busca interativa e aplicação de candidatos) e *Edição Manual*.
  - Sistema de travas manuais (*field locking*): proteção de campos editados pelo usuário contra sobrescrita em novas varreduras.
- **Migração do Banco de Dados SQLite Schema v7**:
  - Adição de colunas bibliográficas estruturadas (`isbn10`, `isbn13`, `publisher`, `publish_year`, `language`, `description`, `identification_status`, `identification_score`, `manual_lock_fields`, etc.).
  - Criação das tabelas `subjects`, `book_subjects`, `metadata_candidates`, `metadata_api_cache` e `books_fts`.
  - Backup preventivo automático pré-migração `lectoria_pre_migration_v6_to_v7.db.bak` com validação de integridade.

- **Segurança Reforçada e Proteção Anti-SSRF**:
  - Validação estrita de URLs de capas externas (`is_safe_external_url`), bloqueando loopback (`localhost`, `127.0.0.1`, `::1`), metadados de nuvem (`169.254.169.254`), faixas privadas (`10.x`, `192.168.x`, `172.16-31.x`) e esquemas não-HTTP.
  - Controle de acesso administrativo estrito nas rotas da fila de enriquecimento (`require_admin`).
  - Resiliência contra limites de taxa: cooldown automático de 60 segundos para HTTP 429 na Google Books API e cache de resultados negativos em SQLite, reduzindo lookups repetidos de ISBNs não catalogados de 1.6s para 0.5ms.
- **Suíte de Testes e Validação Completa (158 Testes Aprovados)**:
  - Inclusão de testes E2E com Chromium em Desktop, Tablet e Smartphone para carrosséis, modais e atalhos de teclado.
  - Testes de migração de schema SQLite v6 para v7 em ambiente isolado com validação de integridade.

## [1.3.0] - 2026-11-05

### Adicionado
- **Suíte E2E Estendida com Playwright e Chromium Real**:
  - Testes automatizados cobrindo reset imediato de scroll em navegação PDF, estabilidade da rolagem contínua com preservação de zoom, e verificação sistemática de ausência de overflow horizontal em 8 perfis de resolução.
  - Teste de geometria e ergonomia validando matematicamente folga horizontal e zero sobreposição entre o dock de tablet (`#tablet-dock-toolbar`) e o documento, tanto na ancoragem à direita quanto à esquerda.
- **Acessibilidade Completa na Barra de Ferramentas de Tablet**:
  - Implementação de semântica ARIA completa: `role="toolbar"`, `aria-orientation="vertical"`, `aria-label`, e sincronização de `aria-pressed="true|false"` nos botões de modo (Leitura, Destaque, Desenho, Borracha).
  - Anéis de foco `:focus-visible` de alto contraste e conformidade com leitores de tela no indicador de página (`aria-live="polite"`).

### Modificado / Corrigido
- **Eliminação Definitiva do Deslocamento Involuntário ao Trocar de Página no PDF**:
  - Correção do alinhamento vertical ao navegar para novas páginas: reset síncrono imediato (`scrollTop = 0; scrollLeft = 0;`) com opção `resetScroll` para chamadas de zoom/redimensionamento.
  - Substituição da diretiva conflitante `scroll-behavior: smooth` por `scroll-behavior: auto` no viewport, eliminando descidas graduais induzidas pelo navegador.
  - Desativação de ancoragem errática do navegador (`overflow-anchor: none`) no viewport, contêiner de página única e slots de rolagem contínua.
- **Estabilização da Rolagem Contínua e Preservação Visual no Zoom**:
  - Preservação da fração de rolagem relativa (`getContinuousRelativeOffset`), permitindo aumentar ou diminuir zoom no modo contínuo sem perder a posição de leitura ou saltar de página.
  - Tolerância defensiva contra microvariações de altura de slots geradas por subpixels (> 4px).
  - Debounce otimizado de 120ms na detecção da página visível, sincronizando controles e miniaturas sem concorrência.
- **Compactação e Reorganização Ergonômica do Dock de Tablet**:
  - Redução da altura da barra acoplável de 537px para ~240px no modo leitura padrão.
  - Remoção de controles redundantes (botão de tema unificado no cabeçalho superior).
  - Exibição condicional da borracha (`#btn-tablet-tool-eraser`), visível exclusivamente durante o modo de desenho.
  - Margens defensivas dinâmicas no viewport (`padding-right: 5rem` para `dock-right` e `padding-left: 5rem` para `dock-left`), garantindo leitura sem obstrução de texto.
  - Correção na lógica de alternância de lado (`targetSide`) no JavaScript para aplicação consistente das classes de estilo.
- **Responsividade e Prevenção de Overflow Horizontal no Cabeçalho**:
  - Novo breakpoint CSS dedicado para tablets (768px a 1100px), ocultando rótulos textuais de botões secundários e aplicando truncamento com ellipsis no título da obra.
  - Garantia de `scrollWidth <= clientWidth` em todas as resoluções de referência (de celulares 360px a monitores 1920px).

## [1.2.0] - 2026-11-04

### Adicionado
- **Suporte Nativo ao Formato EPUB (Iterações 18 e 19)**:
  - Renderização fluida de livros digitais em formato `.epub` no navegador via integração limpa com EPUB.js e JSZip locais (sem chamadas a servidores externos ou CDNs).
  - Extração automática de metadados editoriais (título, autor, idioma, descrição) e capa embutida no arquivo EPUB.
  - Navegação hierárquica por índice de capítulos (Table of Contents / TOC) em painel lateral dedicado.
  - Personalização da leitura: controle de tamanho de fonte (A- e A+), alternância tipográfica (fontes Serif e Sans-Serif) e três temas visuais otimizados (Claro, Escuro e Sépia).
  - Filtro e visualização unificada na biblioteca: suporte simultâneo a documentos PDF e livros EPUB com indicadores visuais de formato e filtragem instantânea na interface.
  - Nota de compatibilidade: EPUBs comerciais protegidos por DRM (Adobe / FairPlay / LCP) não são suportados.
- **Persistência de Progresso com Canonical Fragment Identifier (CFI)**:
  - Rastreamento e persistência bidirecional de progresso para livros EPUB utilizando o padrão oficial EPUB-CFI (`reading_progress.cfi`), sincronizado automaticamente com a biblioteca e com a API do servidor.
- **Barra de Ferramentas Acoplável para Tablets (Tablet Dock Toolbar)**:
  - Barra de ações compacta e flutuante (`#tablet-dock-toolbar`) adaptada para telas sensíveis ao toque entre 768px e 1280px (iPad Mini, iPad padrão, iPad Air/Pro e tablets Android).
  - Botão de alternância de ancoragem lateral (esquerda ou direita) para máxima ergonomia de leitura com uma das mãos, persistido no armazenamento local (`localStorage`).
- **Leitor Adaptativo para Smartphones**:
  - Barra inferior fixa (`#reader-mobile-bottom-bar`) com botões táteis de 44x44px, indicador de página centralizado em formato de pílula (pill) e modal de navegação numérica direta otimizado para teclado virtual de celular.
  - Gaveta inferior expansível (Mobile Bottom Sheet) com acesso rápido a modos de estudo, zoom e opções específicas de tipografia do EPUB.
- **Validação de Ponta a Ponta com Navegador Real (E2E Playwright - Iteração 20)**:
  - Suíte completa de 13 testes de navegador em Chromium real (`tests/e2e/test_browser_e2e.py`), validando visualmente e funcionalmente 8 resoluções de referência, ausência de rolagem involuntária, isolamento de scripts maliciosos em iframes e concorrência de leitura.

### Modificado / Corrigido
- **Correção da Rolagem Involuntária em PDFs Extensos**:
  - Resolução definitiva de corrida entre scroll virtualizado, renderização de slots e persistência de progresso em documentos de 50 a 200+ páginas.
  - Adição de travas atômicas (`isProgrammaticScroll`, `isRestoringProgress`), cancelamento explícito de debounce de rolagem e compensação dinâmica de altura em slots virtualizados.
  - Isolamento de gestos manuais: adição de `touch-action: none !important;` na camada de desenho livre vetorial e borracha, eliminando arrastos involuntários do viewport durante anotações.
- **Migração do Banco de Dados SQLite v5 para v6**:
  - Migração idempotente e transacional com cópia de segurança preventiva antes da aplicação de DDL.
  - Adição da coluna `cfi` na tabela `reading_progress` e da coluna `format` na tabela `books`.
- **Hardening de Segurança para Arquivos EPUB e XML**:
  - Defesa contra ZIP bombs (limite de entradas e taxa de descompactação), rejeição de ataques de Path Traversal em caminhos zip e sanitização rigorosa de entidades XML (proteção contra Billion Laughs e expansão externa DTD).
  - Sanitização de conteúdos HTML em capítulos EPUB contra injeções de script (`<script>`) e links com esquemas inseguros (`javascript:`).
- **Otimização de Carregamento de Recursos**:
  - Carregamento assíncrono e sob demanda dos scripts do motor EPUB (`jszip` e `epub.js`), garantindo que a abertura de arquivos PDF permaneça ultraleve e sem nenhum overhead de memória ou CPU.

---

## [1.1.2] - 2026-11-03

### Adicionado
- **Aprimoramentos de Leitura em Dispositivos Móveis (Iteração 11)**:
  - Otimizações de renderização, suporte a gestos de toque, zoom responsivo e barra de ferramentas adaptativa para smartphones e tablets no leitor de PDF.
  - Suíte de testes dedicada para funcionalidades móveis (`tests/test_iteration11.py`).
- **Higienização Completa de Histórico Git (Iteração 13)**:
  - Expurgado banco de dados SQLite real do histórico Git em todos os commits anteriores via `git-filter-repo`.
  - `.gitignore` atualizado para exclusão permanente de bancos (`data/`, `*.db`, `*.sqlite*`, `backups/`, `dist/`).
- **Portabilidade e Preparação para Publicação Aberta (Iteração 14)**:
  - Parametrização completa do empacotador (`scripts/package.py --output-dir`) com padrão em `./dist` e remoção de caminhos absolutos locais.
  - Separação de dependências: `requirements.txt` enxuto para produção (`fastapi`, `uvicorn`, `pypdf`) e `requirements-dev.txt` para desenvolvimento e testes (`pytest`, `httpx`).
  - Licenciamento explícito: Licença MIT formalizada para o projeto (`LICENSE`) e Licença Apache 2.0 atribuída ao Mozilla PDF.js (`static/vendor/pdfjs/LICENSE`).
  - Integração Contínua (CI) com GitHub Actions (`.github/workflows/ci.yml`) testando em Python 3.12 e 3.13.
  - Documentação comunitária: `CONTRIBUTING.md` e `SECURITY.md`.
  - Isolamento de backups de teste em diretório temporário (`tests/conftest.py`).
  - Atualização do serviço systemd (`deployment/lectoria.service`) para caminho padrão `/opt/lectoria` e usuário `lectoria`.

---

## [Iteração 10 — Empacotamento e Preparação para Transferência] - 2026-11-02

### Adicionado
- **Gerador de Pacote de Transferência (`scripts/package.py`)**:
  - Script determinístico que constrói o pacote de distribuição limpo (`Lectoria-1.1.2-linux-x86_64/`), compacta em `.tar.gz` e `.zip` preservando permissões UNIX executáveis (`0755`), e gera o pacote opcional de migração da biblioteca (`Lectoria-1.1.2-migration-bundle/`).
- **Verificador de Integridade do Pacote (`deployment/verify-package.sh` e atalho na raiz)**:
  - Script autônomo que valida presença da estrutura de pastas (`app/`, `data/`, `books/`, `backups/`), integridade criptográfica SHA-256 (`SHA256SUMS`), permissões executáveis em todos os scripts e integridade do banco SQLite (`PRAGMA integrity_check`).
- **Modalidade A (Instalação Limpa)**:
  - Pacote independente de código em `app/` com pastas `data/`, `books/` e `backups/` acompanhadas de documentação orientativa, pronto para inicialização automática no destino via `./install.sh`.
- **Modalidade B (Migração da Biblioteca Existente)**:
  - Pacote de migração com cópia consistente do banco via SQLite Online Backup API, higienização estrita de segurança com revogação de todas as sessões ativas (`DELETE FROM sessions;`), preservação de capas personalizadas/extraídas (`covers/`), integridade SQLite validada e suporte no instalador (`./install.sh --with-migration <pasta>`).
- **Documentação de Transferência e Instalação (`TRANSFER.txt`, `INSTALL.txt`, `VERSION.txt`, `manifest.json`)**:
  - Instruções detalhadas para cópia física via pendrive/HD externo, opções de preservação de PDFs existentes, homologação com Jellyfin e primeiro boot no Positivo C1260.

### Modificado
- **Resolução de Diretórios de Armazenamento (`config.py`, `deployment/configure.sh`, `deployment/install.sh`)**:
  - Detecção automática de execução dentro da pasta `app/` de um pacote distribuído, mantendo armazenamento em `data/`, `books/` e `backups/` na raiz do pacote e respeitando `.env`.
  - Suporte ao argumento `--with-migration <caminho>` em `deployment/install.sh` para importação direta de dados e capas.
  - Correção na verificação de migração de ambiente virtual em `deployment/install.sh` utilizando `sys.prefix` canônico do Python em vez de leitura ingênua da shebang do pip.

---

## [Iteração 9 — Preparação para Deploy e Homologação no Positivo C1260] - 2026-11-02

### Adicionado
- **Configuração Portável e Suporte a `.env` (`config.py`, `.env.example`)**:
  - Carregador nativo `load_env_file()` sem dependências externas, permitindo configurar `LECTORIA_HOST`, `LECTORIA_PORT`, `LECTORIA_LIBRARY_DIR`, `LECTORIA_DATA_DIR`, `LECTORIA_COVERS_DIR`, `LECTORIA_DB_PATH` e `LECTORIA_BACKUPS_DIR` via arquivo `.env` ou variáveis de ambiente do sistema.
  - Função `ensure_runtime_directories()` para inicialização automática dos diretórios persistentes.
- **Portabilidade Automática de Banco entre Máquinas (`database.py`)**:
  - Reconciliação automática (`_reconcile_default_library_source`) durante `init_db()`: quando o banco SQLite é transferido para outro computador/caminho onde a pasta `books` anterior da fonte inicial `id=1` (`Biblioteca Principal`) não existe mais, o caminho é atualizado automaticamente para o `config.LIBRARY_DIR` da nova máquina.
  - Backup preventivo automático pré-migração (`lectoria_pre_migration_vX_to_vY.db.bak`) via SQLite Online Backup API e verificação `PRAGMA integrity_check` antes e depois de qualquer migração de schema.
- **Preservação de Capas Personalizadas no Backup e Restauração (`backup_service.py`, `scripts/backup.py`)**:
  - `create_backup()` agora copia automaticamente todas as capas personalizadas (`data/covers/*_custom.*`) para um diretório complementar `backups/<nome_do_backup>_covers/`.
  - `restore_backup()` executa `database.init_db()` após a restauração (garantindo migração automática caso um backup antigo seja restaurado) e recupera as capas personalizadas associadas.
  - `prune_backups()` remove também os diretórios de capas associados aos backups antigos descartados.
- **Scripts de Implantação, Manutenção e Diagnóstico (`deployment/` e `scripts/diagnose.py`)**:
  - `deployment/install.sh`: instalador idempotente para Linux Mint XFCE / Ubuntu / Debian com detecção e recriação automática de `.venv` migrado de outra máquina.
  - `deployment/configure.sh`: configuração interativa ou não interativa (`--non-interactive`) de `.env` e geração automática de `deployment/lectoria.service` com caminhos entre aspas e usuário/grupo reais do hospedeiro.
  - `deployment/update.sh`: atualização segura com backup prévio obrigatório, reconciliação de configuração e migração de schema sem tocar nos dados do usuário.
  - `deployment/backup.sh`, `deployment/restore.sh` e `deployment/uninstall.sh` (desinstalação segura que preserva `data/`, `backups/`, `books/` e `.env`).
  - `scripts/diagnose.py` e `deployment/diagnose.sh`: diagnóstico completo de hardware, dependências, permissões `R/W`, modo WAL, integridade SQLite, fontes de biblioteca, rede/LAN, memória RSS e latência de consulta, com suporte a saída `--json`.
- **Documentação de Deploy e Checklist de Homologação (`README.md`, `deployment/README.md`)**:
  - Nova seção dedicada `Deploy no Positivo C1260 — Linux Mint XFCE` e checklist completo de homologação em 10 etapas.
- **Testes Automatizados (`tests/test_iteration9.py`)**:
  - 5 novos testes cobrindo `.env`, reconciliação de caminhos entre máquinas, migração V3->V5 com backup automático e preservação integral de dados, backup/restauração de capas personalizadas e execução dos scripts de deploy/diagnóstico (total da suíte: 77 testes aprovados).

### Corrigido
- **Proteção contra `ON DELETE CASCADE` Acidental em Reconstrução de Tabela (`database.py`)**:
  - Desativação temporária controlada de `PRAGMA foreign_keys=OFF` fora de transação durante migrações de schema (`init_db`), impedindo que o `DROP TABLE books` na reconstrução `books -> books_v4` apague registros filhos em `reading_progress`, `collection_books`, `highlights`, `notes` e `page_drawings`.
- **Caminhos com Espaços no `systemd` (`deployment/lectoria.service`)**:
  - Aspas explícitas no binário `ExecStart`, adição de `EnvironmentFile=-.../.env`, `LECTORIA_HOST=0.0.0.0`, `LECTORIA_PORT=8765` e integração com `journalctl` (`SyslogIdentifier=lectoria`).

---

## [Iteração 8 — Redesign Editorial, Capas e Seletor de Diretórios] - 2026-11-02

### Adicionado
- **Identidade Visual Editorial Própria (`static/css/style.css`, `static/index.html`, `static/reader.html`)**:
  - Novo sistema de cores editorial nos temas Claro (Papel Marfim `#F6F3EC`, Tinta Carvão `#1C1B18`, Terracota Clássico `#9E3B24`) e Escuro (Obsidiana Quente `#141311`, Pergaminho `#EAE5DC`, Terracota Cobreado `#D46A4E`), substituindo a estética genérica de painel azul-neon.
  - Hierarquia tipográfica exclusivamente local/offline: Serifada Editorial (`--font-serif`) para títulos e cabeçalhos, Humanista Sans (`--font-sans`) para controles de interface e Monoespaçada Tabular (`--font-mono`) para páginas, porcentagens, tamanhos e caminhos.
  - Suporte completo a `:focus-visible` para navegação por teclado e `@media (prefers-reduced-motion: reduce)`.
- **Sistema de Capas em 3 Camadas (`covers_service.py`, `database.py` Schema V5, `main.py`)**:
  - **Prioridade 1 (Capa Real do PDF + Cache)**: Extração sob demanda de imagens da primeira página do PDF (`/DCTDecode` JPEG e `/FlateDecode` RGB/Gray convertidos para PNG puro em Python) e endpoint `POST /api/books/{book_id}/cover/cache` para cachear miniaturas da 1ª página renderizadas pelo PDF.js no cliente. Cache persistido em `data/covers/` (ignorado pelo Git) e invalidado automaticamente quando o `file_hash` do PDF muda.
  - **Prioridade 2 (Capa Personalizada)**: Endpoints `POST /api/books/{book_id}/cover` e `DELETE /api/books/{book_id}/cover` com validação estrita de assinatura binária (*magic bytes* para PNG, JPEG e WebP), verificação de dimensões mínimas/máximas, bloqueio de HTML/SVG/scripts e limite de 5 MB (`HTTP 413`).
  - **Prioridade 3 (Capa Substituta Editorial Determinística)**: Geração determinística de capa SVG editorial (`generate_editorial_svg_cover`) baseada no hash SHA-256 do livro para PDFs sem capa extraível, corrompidos ou protegidos por senha.
- **Seleção Visual de Diretórios (`directory_picker_service.py`, `main.py`, `static/js/app.js`)**:
  - **Navegador Visual de Diretórios pelo Backend (`GET /api/sources/browse`)**: Permite a administradores explorar pastas autorizadas no servidor tanto localmente quanto pela rede doméstica, exibindo atalhos rápidos, contagem de arquivos `.pdf` por pasta e bloqueando diretórios reservados do sistema, pastas internas (`data/`, `backups/`, `data/covers/`, `.venv/`, `.git/`), links simbólicos e tentativas de *path traversal* (`..`).
  - **Seletor Nativo do Sistema Operacional (`POST /api/sources/pick-native`)**: Aciona `zenity` ou `kdialog` com `shell=False` e timeout exclusivamente quando a requisição parte de `localhost`/`127.0.0.1` e há sessão gráfica ativa (`DISPLAY`/`WAYLAND_DISPLAY`), bloqueando com `403 Forbidden` qualquer tentativa de abertura remota.
- **Testes Automatizados (`tests/test_iteration8.py`)**:
  - 11 novos testes abrangendo todos os cenários de extração/cache/invalidação/upload/remoção de capas e navegação/seleção/segurança de diretórios (total da suíte: 72 testes aprovados).

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
