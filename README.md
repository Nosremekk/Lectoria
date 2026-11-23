# Lectoria — Servidor Pessoal de Livros Digitais, Leitura Colaborativa e Conhecimento Conectado (v1.9.0 — Atlas)

O **Lectoria** é um servidor pessoal auto-hospedado (*self-hosted*) para organização, identificação inteligente, automação, clubes de leitura, leitura colaborativa e **gestão pessoal de conhecimento conectado (Atlas)** para documentos PDF e livros digitais EPUB, projetado com foco em sobriedade estética editorial, velocidade e consumo mínimo de recursos de CPU, memória RAM e I/O de disco.

Foi concebido para operar perfeitamente mesmo em hardware modesto — incluindo VPS de 1 vCPU / 4 GB RAM e mini-PCs domésticos (ex: Intel Celeron), sem depender de IA externa, bancos vetoriais ou frameworks pesados.

---

## 1. Principais Recursos

- **Atlas — Conhecimento Conectado (v1.9.0 — Schema SQLite v13)**:
  - **Conceitos Pessoais (`concepts`) e Reflexões Autorais (`reflections`)**: entidades estritamente privadas por usuário, com editor Markdown seguro construído 100% via DOM, controle otimista de concorrência (`HTTP 409`) e inserção assistida de referências canônicas por UUIDs estáveis.
  - **Conexões Semânticas Direcionadas e Backlinks (`knowledge_links`)**: 8 tipos fixos de relação (*Complementa*, *Contradiz*, *Exemplifica*, *Fundamenta*, *Desenvolve*, *Referencia*, *Questiona*, *Relaciona-se com*) ligando conceitos, reflexões, destaques, notas e livros, com *Read-Time Authorization* e sentinelas neutros para referências indisponíveis.
  - **Integração nos Leitores PDF/EPUB, Caderno de Estudos e Biblioteca**: associação contextual de conceitos e conexões diretamente durante a leitura (`#modal-reader-knowledge`), filtros de anotações conectadas/não conectadas no Caderno de Estudos e subaba *Conhecimento* sob demanda nos detalhes do livro.
  - **Pesquisa Integrada FTS5 e Mapa de Conhecimento SVG**: busca unificada com `unicode61 remove_diacritics 2` (insensível a acentos em português), dupla barreira de autorização relacional, ranking determinístico sem vazamento BM25 entre usuários, snippets estruturados e grafo local interativo em SVG (1–2 saltos, teto de 80 nós / 160 arestas) acompanhado de alternativa textual acessível.
  - **Exportação Aberta e Portável**: exportação do Atlas pessoal em **Markdown interligado (`.zip`)** com opção de wikilinks compatíveis com Obsidian, **JSON estruturado v2.0** e **CSV tabular (`.zip`)** com proteção contra *formula injection*.
  - **Otimizações de Desempenho e Política de Backups**: event loop desbloqueado no Uvicorn (`--workers 1`), eliminação de consultas N+1, redução de 87,8% no payload inicial da interface, backups locais diários consistentes (`SQLite Online Backup API`) e cópia externa manual recomendada antes de upgrades/migrações.
- **Experiência de Leitura & Anotações Colaborativas no Livro (v1.8.0 — Schema SQLite v12)**:
  - **Privacidade por Padrão e Publicação Explícita (`visibility = 'private' | 'public'`)**: Todas as anotações antigas, novas e importadas nascem estritamente privadas; o autor escolhe explicitamente o que publicar no livro e pode revogar a publicação a qualquer momento.
  - **Leitor PDF Colaborativo**: Camadas duplas de desenho vetorial SVG (`.public-drawing-layer` somente-leitura abaixo de `.drawing-layer` editável) com `viewBox` isotrópico imune a zoom e redimensionamento, destaques públicos diferenciados com `#highlight-popover` contextual, pins de notas na margem (`#page-note-popover`), modo marca-texto ativo (`#btn-tool-highlight`), controle de camadas (*Minhas + Públicas*, *Somente minhas*, *Ocultar anotações*) isolado por usuário, busca textual incremental (`Ctrl+F` / `/`) e correção de `dockMargin` em tablets no modo contínuo.
  - **Leitor EPUB Colaborativo**: Seleção de texto habilitada para **todos** os livros EPUB (sem exigir clube de leitura), persistência e renderização de destaques próprios e públicos por `epub_cfi` via `epubRendition.annotations.highlight()` com tolerância a reflow, navegação direta por CFI com destaque temporário e fallback seguro, e controles de espaçamento entre linhas (`1.4`, `1.65`, `1.9`) e margens (`compact`, `normal`, `wide`).
  - **Caderno de Estudos Colaborativo e Marcadores Pessoais (`bookmarks`)**: Seletor independente de escopo (`Minhas`, `Públicas`, `Todas visíveis`), abas `Todas`, `Destaques`, `Notas`, `Desenhos` e `Marcadores` (sempre privados), pesquisa por conteúdo/autor com debounce e `AbortController`, ordenação por posição/CFI numérico ou mais recentes, paginação incremental (*Carregar mais*) e botão de Marcador Rápido (`k`) no cabeçalho.
- **Leitura Compartilhada (v1.7.0 — Schema SQLite v11)**:
  - **Discussões por Leitura Coletiva (`discussion_topics` e `discussion_replies`)**: Fóruns estruturados vinculados a cada leitura coletiva, com suporte a seção/capítulo de referência (`chapter_or_section`), página ou intervalo (`page_reference`), marcação de spoilers (`has_spoilers`) com ocultação visual segura no frontend, respostas encadeadas (`parent_reply_id` validado contra o mesmo tópico), fixação (`is_pinned`), trancamento de tópicos (`is_locked`) e exclusão lógica (`is_deleted = 1`) que preserva o histórico de conversas encadeadas.
  - **Metas Coletivas e Progresso Voluntário (`collective_reading_goals` e `collective_goal_progress`)**: Criação de marcos e cronogramas coletivos por proprietários ou moderadores do clube (`owner`/`moderator`), nos quais cada participante da leitura coletiva registra **voluntariamente** seu status (`planned`, `in_progress`, `completed`) e nota opcional — sem qualquer sincronização automática ou compulsória com o progresso privado (`reading_progress`).
  - **Citações Compartilhadas (`shared_quotes`)**: Mural colaborativo de trechos marcantes da obra dentro da leitura coletiva, com comentário opcional do participante, referência de página/seção, posição técnica (`position_ref`), filtro e máscara anti-spoiler.
  - **Compartilhamento Direto a partir dos Leitores PDF.js e EPUB.js**: Botão contextual *"Compartilhar com leitura coletiva"* ao selecionar um trecho no leitor PDF ou EPUB, bem como a partir de notas privadas do próprio usuário no Caderno de Estudos, preenchendo automaticamente texto, página ou EPUB-CFI e listando apenas as leituras coletivas ativas elegíveis daquele livro.
  - **Navegação Direta de Citação para o Leitor**: Ação *"Abrir no leitor"* em cada citação compartilhada que abre diretamente o documento na página exata (`#page=N` para PDF) ou localização canônica (`#cfi=epubcfi(...)` para EPUB), com fallback gracioso caso a obra esteja ausente ou a referência seja inválida.
  - **Controles Rígidos de Privacidade, Permissões e Modo Somente-Leitura**: Isolamento absoluto de clubes privados (`HTTP 404 Not Found` para não-membros), proteção anti-IDOR nas notas privadas e política estrita somente-leitura para leituras coletivas `completed`, `cancelled` ou pertencentes a clubes arquivados (`is_archived = 1`), permitindo consulta histórica completa (`GET 200`) e bloqueando mutações (`HTTP 400 Bad Request`).
  - **Correção e Robustez dos Filtros Taxonômicos da Biblioteca**: Paridade exata entre os contadores da árvore taxonômica (`/api/taxonomy/tree`) e a listagem de obras (`/api/books`), desacoplamento de facetas ortogonais (`genre`, `origin`, `period`) em `taxonomy_service.py` para evitar sobrescrita cruzada entre livros da mesma subcategoria, invalidação precisa do cache em memória no frontend (`static/js/app.js`) e ferramenta de diagnóstico read-only (`scripts/diagnose_taxonomy.py`).
- **Correção de Renderização de Capas em Grade e Lista (v1.6.3)**:
  - Unificação estrutural dos cards de livros entre os modos Grade e Lista, garantindo proporção editorial `1 / 1.42` e prevenindo encolhimento de capas para dimensões de miniatura na visualização em grade.
- **Otimização de Desempenho do Leitor PDF (v1.6.1)**:
  - **Abertura Ultrarrápida de Documentos**: Redução drástica do tráfego inicial para renderizar a 1ª página em mais de 90%, passando de dezenas de segundos para ~1 segundo em redes remotas e < 0,3s em rede local.
  - **Eliminação de Streaming Monolítico (`disableStream: true`)**: Evita o download concorrente não intencional de 100% do arquivo enquanto a primeira página é requisitada.
  - **Fatiamento Equilibrado (`rangeChunkSize: 524288`)**: Adoção de blocos de 512 KB reduzindo em até 8x as viagens de ida e volta (RTT) de rede necessárias para leitura de catálogo e referências cruzadas (`xref`).
  - **Resolução Leve de Arquivos no Backend**: Rota de streaming otimizada com `resolve_book_file_for_user`, substituindo consultas pesadas com múltiplos JOINs por uma consulta única e direta de segurança e caminho em disco.
  - **Cache HTTP Seguro e Conservador**: Cabeçalhos padronizados `Cache-Control: private, no-cache`, `Accept-Ranges: bytes`, `ETag` determinístico e suporte nativo a revalidação condicional `HTTP 304 Not Modified` e requisições `HEAD`.
  - **Carregamento Progressivo da Interface**: Inicialização paralela de metadados e progresso de leitura via `Promise.all`, e carregamento assíncrono de anotações e destaques em segundo plano sem bloquear a primeira pintura visual.
- **Clubes de Leitura e Leitura Coletiva (v1.6.0)**:
  - **Clubes de Leitura Públicos e Privados (Schema v10)**: Criação de comunidades temáticas permanentes na instância com modelo de governança em 3 papéis (`owner`, `moderator`, `member`), arquivamento seguro e transferência de titularidade de posse sem orfandade de dados.
  - **Convites Direcionados e Proteção Anti-Enumeração**: Convites para usuários da instância com aceite ou recusa, revogação de convites pendentes e blindagem estrita contra enumeração de dados (clubes privados são invisíveis para não-membros e acessos diretos não autorizados retornam `HTTP 404 Not Found`).
  - **Leituras Coletivas por Obra**: Atividades de grupo organizadas por clube vinculadas a livros reais do acervo (`books.id`), com 4 estados de ciclo de vida (*Planejada*, *Em andamento*, *Concluída*, *Cancelada*), adesão/desligamento simplificado e histórico preservado.
  - **Filtro "Leitura Coletiva" na Biblioteca**: Aba de filtro exclusiva que exibe apenas as obras das leituras coletivas em que o usuário autenticado participa, com selos contextuais de clube e participantes nos cards.
  - **Privacidade Individual Rigorosa**: O progresso pessoal de leitura (página atual, porcentagem), destaques, anotações e avaliações permanecem 100% privados e isolados de outros participantes.
  - **Prontidão Docker Compose**: Arquivos `docker-compose.yml` e `docker-compose.example.yml` para inicialização imediata e padronizada em contêineres Docker.
- **Refinamento Mobile, Categorias Customizáveis e BrasilAPI (v1.5.2)**:
  - **Experiência Mobile-First**: Interface de descoberta e biblioteca otimizada para smartphones e tablets (320px a 1280px), sem qualquer overflow horizontal (`scrollWidth <= clientWidth`), com painel compacto colapsável de filtros e prateleiras horizontais com rolagem contida.
  - **Gestão Completa de Categorias Customizáveis (Schema v9)**: Criação, edição, reordenação prioritária (Subir/Descer), mesclagem e exclusão segura de categorias no painel administrativo, suporte a livros com múltiplas categorias, filtro dedicado "Sem categoria" e editor de tags de categoria diretamente no modal de detalhes da obra.
  - **Enriquecimento com BrasilAPI e Normalização de Autores**: Suporte nativo à BrasilAPI para busca de metadados por ISBN, normalização reversível de nomes de autores nacionais/compostos ("Sobrenome, Nome") e regras contextuais refinadas de classificação.
  - **Correção do Contrato de Leitura**: Prevenção de falso auto-completar em obras de página única ou abertura inicial de EPUBs, novo controle manual de status de leitura (*Não lido*, *Lendo*, *Concluído*) e isolamento multiusuário estrito.
- **Biblioteca Viva: Camada Multiusuário e Social "Nossa Biblioteca" (v1.5.0/v1.5.1)**:
  - Espaço comunitário opcional (**Nossa Biblioteca**) com feed de atividades de leitura, avaliações (1 a 5 estrelas), resenhas, listas de *Favoritos* e *Quero Ler*, recomendações diretas entre leitores e mural de citações/anotações compartilhadas com comentários.
  - **Privacidade Estrita por Padrão (`user_privacy_settings`)**: Todos os recursos de compartilhamento nascem desativados (`0`). Anotações, destaques, desenhos e progresso permanecem 100% privados por usuário, e nem mesmo administradores têm acesso irrestrito às anotações privadas de outros leitores.
- **Pipeline Bibliográfico Progressivo de 5 Estágios e Proveniência (v1.5.0)**:
  - Extração local profunda (páginas 1 a 10 + últimas 2 páginas de ficha catalográfica/colofão de PDFs, EPUB OPF e normalização reversível de nomes de arquivos preservando `raw_filename` e `raw_parent_dir`).
  - Integração multi-provedor com **Open Library**, **Google Books** e **Crossref API**, com telemetria de latência, rate limit e falhas por provedor (`metadata_provider_stats`).
  - Pontuação explicável (`score_candidate_detailed`) com bônus de coerência multi-fonte e rastreabilidade por campo na tabela `metadata_provenance`.
- **Validação Visual Pura de Capas Anti-CMYK e Reconciliação de Arquivos Movidos (v1.5.0)**:
  - Rejeição automática em Python puro (`struct` + `zlib`) de capas PDF extraídas com canais CMYK/YCCK invertidos ou máscaras `/SMask` pretas, com fallback automático para capa externa ou Capa Editorial SVG com Categoria, Título e Autor.
  - Reconciliação automática de arquivos movidos ou renomeados via `file_hash` + `filesize` sem recriar o `book_id`, preservando todo o progresso, anotações e histórico.
- **Automação da Biblioteca, Fila Persistente e Painel Administrativo Dedicado (v1.5.0)**:
  - Agendador persistente (`scheduled_jobs`) com suporte a intervalos, execução diária/semanal, horário preferencial e detecção automática de mudanças nas pastas para 6 rotinas (`scan_library`, `enrich_metadata`, `update_missing_covers`, `rebuild_search_index`, `db_maintenance`, `auto_backup`).
  - Fila de tarefas (`background_jobs`) com recuperação automática de jobs interrompidos no boot.
  - **Painel Administrativo Dedicado** em tela cheia com 6 seções: *Dashboard*, *Gestão da Biblioteca*, *Gestão de Usuários*, *Automação & Fila*, *Revisão Bibliográfica* e *Manutenção & Auditoria*.
- **Descoberta Inteligente e Prateleiras Horizontais (Estilo Streaming / Jellyfin — v1.4.0+)**:
  - Experiência visual rica com carrosséis horizontais dinâmicos: *Continuar Lendo*, *Adicionados Recentemente*, *Categorias do Acervo*, *Autores em Destaque*, *Para Começar a Ler*, *Leituras Concluídas* e *Coleções*.
  - Garantia estrita de que nenhuma prateleira vazia é renderizada.
  - Alternância instantânea de visão entre a aba de **Descoberta** e a visão clássica de **Biblioteca & Explorar** (Grade e Lista).
- **Taxonomia Hierárquica e Classificação Multidimensional (v1.4.0)**:
  - Árvore taxonômica editorial estruturada em 5 grandes eixos canônicos (*Filosofia*, *Literatura*, *Ciências Humanas*, *Ciência e Tecnologia*, *Artes e Cultura*) e dezenas de subcategorias temáticas.
  - Separação multidimensional de facetas: *Categoria*, *Subcategoria*, *Gênero Literário*, *Origem / Nacionalidade* e *Período Histórico*.
  - Filtros combinados instantâneos na interface web alimentados dinamicamente pelo endpoint `/api/taxonomy/tree`.
- **Motor de Busca Textual FTS5 Nativamente Integrado (v1.4.0)**:
  - Tabela virtual `books_fts` em SQLite permitindo busca instantânea em frações de milissegundo por título, autor, sinopse, editora e assuntos, com suporte a prefixos e termos múltiplos.
- **Identificação Bibliográfica e Reconciliação Externa (v1.4.0)**:
  - Consulta automática e manual a fontes públicas confiáveis: **Open Library API** (primária, com taxa defensiva de 1 req/s) e **Google Books API** (complementar).
  - Algoritmo de correspondência inteligente (*Smart Matcher*) com pontuação ponderada de 0 a 100 (*reliable*, *needs_review*, *insufficient*), evitando correspondências equivocadas.
  - Auditoria e histórico de candidatos registrados na tabela `metadata_candidates`.
- **Fila Assíncrona de Enriquecimento em Segundo Plano (v1.4.0)**:
  - Worker sequencial com taxa controlada e baixa prioridade de CPU/IO, preservando recursos em computadores modestos.
  - **Central de Metadados** administrativa na interface web com métricas de progresso em tempo real e controles de pausar, retomar, cancelar e enfileirar.
  - Cache local com TTL de 7 dias (`metadata_api_cache`) para evitar consultas de rede redundantes.
- **Modal Rico de Detalhes da Obra (v1.4.0)**:
  - Exibição de sinopse detalhada, capa ampliada, tags taxonômicas clicáveis, editora, ano de publicação, idioma, ISBN-10, ISBN-13 e formato.
  - Subabas de *Visão Geral*, *Reconciliação Externa* (com busca interativa e aplicação de candidatos) e *Edição Manual*.
  - Sistema de travas manuais (*field locking*): proteção de campos editados pelo usuário contra sobrescrita em novas varreduras.
- **Biblioteca Pessoal Editorial com Múltiplas Fontes**:
  - **Identidade Visual Editorial Própria**: Paleta inspirada em encadernações e papelaria clássica nos temas Claro (Papel Marfim `#F6F3EC` e Tinta Carvão `#1C1B18` com acento Terracota `#9E3B24`), Escuro (Obsidiana Quente `#141311` e Pergaminho `#EAE5DC`) e Sépia Conforto, usando pilhas tipográficas locais (Serifada Editorial para títulos, Sans-serif para controles e Monoespaçada Tabular para métricas).
  - Suporte simultâneo a **arquivos PDF e livros EPUB nativos**, com filtros rápidos por formato na interface web.
  - Suporte a **múltiplas fontes e diretórios locais configuráveis**, com **Seletor Visual de Diretórios** integrado (navegador visual seguro pelo navegador web para acesso local ou em rede doméstica + integração com seletor nativo do sistema via Zenity/KDialog quando utilizado diretamente na máquina do servidor).
  - Varredura sob demanda (*on-demand scanner*) com extração rápida de metadados (`pypdf` para PDF e parser XML/ZIP nativo para EPUB), sem modificação dos arquivos originais.
  - Garantia estrita de preservação: remoção ou desativação de fontes **nunca apaga arquivos físicos do disco**.
  - **Sistema de Capas em 3 Camadas (`covers_service.py`)**:
    1. **Extração e Cache da Capa Real (PDF e EPUB)**: Extração automática sob demanda da imagem da primeira página do PDF ou da capa embutida no EPUB (`JPEG`/`PNG`) ou cache da miniatura renderizada em `data/covers/` (ignorado pelo Git), com invalidação automática se o arquivo mudar.
    2. **Capas Personalizadas**: Upload manual de capa (`PNG`, `JPEG`, `WebP` até 5 MB) com validação estrita de assinatura binária (*magic bytes*), substituição e restauração da capa automática a qualquer momento.
    3. **Capa Editorial Substituta Determinística (SVG)**: Geração vetorial determinística baseada em hash SHA-256 do livro quando o arquivo não contém imagem de capa identificável.
  - Busca instantânea por título e autor, filtros por estado de leitura e ordenação multicritério.
  - Gestão de Coleções personalizadas com atribuição de livros em lote.
- **Suporte Nativo a EPUB com EPUB.js e JSZip Locais**:
  - Renderização completa de arquivos `.epub` diretamente no navegador, sem conversores intermediários e sem requisições para a internet.
  - Navegação hierárquica por capítulos (Sumário / TOC) com gaveta lateral expansível.
  - Ajustes tipográficos em tempo real: aumento/diminuição de fonte (A-/A+), alternância entre famílias tipográficas (Serif e Sans-serif) e três temas visuais (Claro, Escuro e Sépia).
  - Persistência e restauração de progresso de leitura contínua via padrão oficial **EPUB-CFI** (*Canonical Fragment Identifier*).
  - *Nota:* EPUBs com proteção anticópia/DRM comercial (Adobe / FairPlay / LCP) não são suportados.
- **Download Direto de Documentos**:
  - Download rápido do PDF ou EPUB original diretamente da biblioteca (cards em grade e lista) ou da barra de ferramentas do leitor.
  - Streaming em chunks com consumo de memória RAM próximo de zero e cabeçalho `Content-Disposition: attachment`.
- **Perfis de Usuário e Autenticação Segura**:
  - Suporte a múltiplos perfis de usuário com dois papéis de acesso: Administrador (`admin`) e Leitor (`reader`).
  - Hashing seguro de senhas com `hashlib.scrypt` (da biblioteca padrão do Python) e tokens de sessão criptográficos armazenados como hashes SHA-256 (`sessions`).
  - Proteção contra força bruta com rate-limiting em memória (bloqueio temporário após tentativas consecutivas).
  - Isolamento estrito de dados pessoais: catálogo de livros compartilhado, mas progresso, destaques, anotações, desenhos e coleções totalmente individuais para cada usuário.
  - Gerenciamento completo via interface web (modal administrativo) ou via terminal com `scripts/manage_users.py`.
- **Leitor Adaptativo de Alta Performance (PDF e EPUB)**:
  - Modos de leitura em **Página Única** e **Rolagem Contínua Virtualizada** com proteção avançada contra saltos involuntários em documentos extensos (200+ páginas).
  - **Barra de Ferramentas Acoplável para Tablets (`#tablet-dock-toolbar`)**: Ancoragem flutuante compacta (~240px) e acessível (ARIA toolbar), com margem defensiva e alternância entre esquerda e direita sem sobreposição de conteúdo em iPads e tablets Android.
  - **Experiência Móvel Completa em Smartphones**: Barra inferior tátil de 44px (`#reader-mobile-bottom-bar`), gaveta expansível de opções e modal com teclado puramente numérico para pulo direto de páginas.
  - Streaming eficiente de PDFs via **HTTP Range Requests (206 Partial Content)**, evitando o carregamento do arquivo inteiro na memória RAM.
  - Salvamento automático contínuo de progresso de leitura via API e Beacon API.
  - Gaveta lateral de miniaturas (*thumbnails*) para PDFs com *lazy rendering* via `IntersectionObserver`.
  - Barra de progresso visual, histórico de navegação (`b`) e atalhos completos de teclado (`?`).
- **Caderno de Estudos e Anotações Avançado**:
  - Marca-texto com 5 cores e gravação de retângulos normalizados independentes de zoom ou resolução de tela.
  - Anotações e notas vinculadas a trechos destacados ou avulsas por página.
  - Desenho livre vetorial em SVG sobreposto às páginas com ferramenta de borracha geométrica de alta precisão e proteção `touch-action: none` contra rolagem involuntária.
  - Painel de estudos com busca local, filtros dinâmicos e navegação direta para os trechos anotados.
  - Exportação formatada em **Markdown (`.md`)** e exportação estruturada em **JSON (`.json`)**.
- **Segurança, Integridade e Estabilidade**:
  - Bloqueio rigoroso contra inclusão de diretórios de sistema (`/`, `/etc`, `/proc`, etc.) e pastas internas da aplicação (`data/`, `backups/`).
  - Proteção estrita contra *Path Traversal* em todas as rotas e resolução canônica de links simbólicos.
  - Importação de anotações com preview, limites de tamanho de corpo (15 MB), profundidade máxima (10 níveis) e transações atômicas com rollback.
  - Sistema de backup SQLite Online com proteção contra concorrência (`threading.Lock`), cópia prévia de salvaguarda e retenção configurável.
  - Unidade de serviço para inicialização em segundo plano via **systemd** com diretivas de sandboxing e limites de cgroup (RAM máx. 512 MB).

---

## 2. Sistema de Backups e Integridade

O Lectoria armazena dados persistentes (usuários, sessões, progresso, anotações, coleções e metadados) no banco SQLite `data/lectoria.db` (operando em modo WAL). O diretório `data/` é estritamente ignorado pelo versionamento Git para garantir privacidade e segurança.

### 2.1 Gerenciamento de Backups (`scripts/backup.py`)

O Lectoria dispõe do módulo `backup_service.py` e da interface CLI `scripts/backup.py`, utilizando a **SQLite Online Backup API** com trava de exclusão mútua (`threading.Lock`). Isso permite gerar cópias de segurança consistentes mesmo durante execuções ativas ou em modo WAL, armazenando-as em `backups/` junto com as capas personalizadas correspondentes.

#### Criar um novo backup:
```bash
python3 scripts/backup.py --backup
```
*Gera uma cópia em `backups/lectoria_backup_AAAAMMDD_HHMMSS.db` e valida sua integridade.*

#### Listar backups existentes:
```bash
python3 scripts/backup.py --list
```

#### Verificar a integridade do banco ativo:
```bash
python3 scripts/backup.py --verify
```

#### Limpar backups antigos (retenção manual):
```bash
# Preserva apenas os 5 backups mais recentes, removendo os excedentes antigos:
python3 scripts/backup.py --prune 5
```
> *Nota: O Lectoria nunca remove backups automaticamente sem autorização explícita do usuário.*

---

### 2.2 Procedimento Oficial de Restauração de Banco de Dados

Caso seja necessário restaurar os dados a partir de um backup anterior, siga estritamente o procedimento:

1. **Parar o servidor Lectoria**:
   ```bash
   sudo systemctl stop lectoria.service
   # Ou pressione Ctrl + C no terminal se estiver rodando em primeiro plano
   ```

2. **Identificar o arquivo de backup desejado**:
   ```bash
   python3 scripts/backup.py --list
   ```

3. **Executar a restauração**:
   ```bash
   python3 scripts/backup.py --restore lectoria_backup_YYYYMMDD_HHMMSS.db
   ```
   *O processo cria automaticamente uma cópia de segurança prévia (`data/lectoria_pre_restore.db.bak`) antes de substituir os arquivos, garantindo recuperação em caso de falha de I/O.*

4. **Verificar a integridade do banco restaurado**:
   ```bash
   python3 scripts/backup.py --verify
   ```

5. **Reiniciar o servidor**:
   ```bash
   sudo systemctl start lectoria.service
   ```

---

## 3. Arquitetura e Decisões de Design

- **Backend**: Python 3.10+ com FastAPI e Uvicorn em processo único (`workers=1`), sem threads em background ou workers concorrentes desnecessários.
- **Banco de Dados**: SQLite3 em modo **WAL** (`Write-Ahead Logging`), com `synchronous=NORMAL`, `foreign_keys=ON` e controle incremental de migrações (`schema_migrations` — **Schema Atual: v11**), garantindo persistência rápida, sem bloqueios de leitura e com baixo desgaste de disco SSD.
  - **Schema V1**: tabelas `users`, `books` e `reading_progress`.
  - **Schema V2**: tabelas `collections` e `collection_books`.
  - **Schema V3**: tabelas `highlights`, `notes` e `page_drawings`.
  - **Schema V4**: tabelas `library_sources`, `sessions` e isolamento multiusuário (`user_id`, `role`, `password_hash`).
  - **Schema V5**: metadados de capas em `books` (`cover_type`, `cover_path`, `cover_source_hash`, `cover_updated_at`).
  - **Schema V6**: suporte nativo a EPUB (`format`, `cfi_progress`).
  - **Schema V7**: taxonomia multidimensional (`subjects`, `book_subjects`, `metadata_candidates`, `metadata_jobs`, `metadata_api_cache`, `books_fts`).
  - **Schema V8**: camada comunitária "Nossa Biblioteca", automação e proveniência (`book_reviews`, `book_lists`, `shared_annotations`, `book_recommendations`, `user_activity_events`, `user_privacy_settings`, `metadata_provenance`, `scheduled_jobs`, `background_jobs`).
  - **Schema V9**: categorias customizáveis administráveis (`custom_categories`).
  - **Schema V10**: clubes de leitura e leituras coletivas (`reading_clubs`, `reading_club_members`, `reading_club_invites`, `collective_reads`, `collective_read_participants`).
  - **Schema V11 (v1.7.0)**: Leitura Compartilhada (`discussion_topics`, `discussion_replies`, `collective_reading_goals`, `collective_goal_progress`, `shared_quotes`) com triggers SQLite de integridade cruzada e backup preventivo com sufixo único.
- **Frontend**: HTML5 semântico, CSS3 enxuto e JavaScript Vanilla (ES6). Zero frameworks pesados (sem React, Vue, Angular ou Node.js).
- **Leitores PDF e EPUB**: Baseados em **PDF.js** (`static/vendor/pdfjs/`) e **EPUB.js + JSZip** (`static/vendor/epubjs/`) estáticos locais. A renderização ocorre integralmente no navegador do cliente, sem requisições a CDNs externas e sem telemetria.
- **Streaming de Arquivos**: Suporte nativo a **HTTP Range Requests (206 Partial Content)** com blocos de 512 KB (`rangeChunkSize: 524288`) e `disableStream: true`. O servidor serve blocos sob demanda, permitindo abrir documentos grandes sem carregar o arquivo PDF inteiro na memória RAM.
- **Segurança de Caminhos**: Prevenção estrita contra *Path Traversal* com resolução canônica de caminhos (`pathlib.Path.resolve()`). Identificadores UUID estáveis em vez de expor caminhos do sistema operacional nas rotas públicas.
- **Zero Ociosidade Forçada**: A aplicação consome ~0% de CPU quando ociosa (sem file watchers contínuos com `inotify` e sem polling constante no frontend).

---

## 4. Estrutura do Projeto

```text
lectoria/
├── AGENTS.md                   # Guia de governança e arquitetura para agentes e mantenedores
├── Dockerfile                  # Imagem de contêiner enxuta (Python 3.12-slim)
├── docker-compose.yml          # Orquestração Docker Compose pronta para uso self-hosted
├── docker-compose.example.yml  # Modelo comentando volumes de acervo, dados e backups
├── config.py                   # Configurações centralizadas com carregamento de .env (APP_VERSION=1.7.0)
├── database.py                 # Conexão SQLite (WAL mode), migrações V1..V11 e triggers de integridade
├── main.py                     # API FastAPI, CSRF, autenticação, streaming HTTP Range e rotas v1.7.0
├── shared_reading_service.py   # Regras de negócio da Leitura Compartilhada (tópicos, respostas, metas, citações)
├── clubs_service.py            # Ciclo de vida de Clubes de Leitura, papéis, convites e leituras coletivas
├── social_service.py           # Camada comunitária "Nossa Biblioteca" e configurações de privacidade
├── taxonomy_service.py         # Árvore taxonômica, facetas desacopladas e categorias customizáveis
├── metadata_service.py         # Enriquecimento bibliográfico multi-provedor e fila assíncrona
├── automation_service.py       # Agendador persistente e fila de tarefas em segundo plano
├── auth_service.py             # Autenticação scrypt, sessões SHA-256, rate limiting e papéis
├── sources_service.py          # Gestão de múltiplas fontes e validação de caminhos
├── directory_picker_service.py # Navegador visual de diretórios e seletor nativo (Zenity/KDialog)
├── covers_service.py           # Extração em 3 camadas de capas (PDF/EPUB, personalizada e SVG)
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── annotations_service.py      # Destaques, notas privadas e desenhos vetoriais
├── export_service.py           # Exportação (.md, .json) e importação segura de cadernos
├── backup_service.py           # Backup SQLite Online API + capas personalizadas com Lock
├── library.py                  # Scanner leve (pypdf/EPUB), filtros taxonômicos, busca FTS5 e ordenação
├── progress.py                 # Persistência e restauração do progresso individual de leitura
├── docs/                       # Documentação técnica e relatórios de validação
│   ├── ARCHITECTURE.md         # Visão arquitetural e diagramas de componentes
│   ├── DATABASE.md             # Documentação do Schema SQLite v11, FKs e migrações
│   ├── AUTHORIZATION.md        # Matriz de permissões, CSRF, anti-IDOR e privacidade
│   ├── READER.md               # Arquitetura dos leitores PDF.js e EPUB.js, HTTP Range e citações
│   ├── FRONTEND.md             # Estado Vanilla JS, filtros, máscaras de spoiler e responsividade
│   ├── API.md                  # Contratos detalhados dos endpoints REST
│   ├── TESTING.md              # Guia das suítes Pytest e Playwright E2E
│   ├── DEPLOYMENT.md           # Guia de implantação Docker, systemd, backup e atualização
│   └── DECISIONS/              # Architecture Decision Records (ADR-001 a ADR-006)
├── scripts/
│   ├── backup.py               # Utilitário CLI para backup, restauração e retenção
│   ├── diagnose.py             # Utilitário CLI de diagnóstico de ambiente e desempenho
│   ├── diagnose_taxonomy.py    # Diagnóstico read-only de consistência taxonômica do banco SQLite
│   ├── manage_users.py         # Utilitário CLI para gestão de usuários, senhas e papéis
│   └── package.py              # Utilitário de empacotamento para distribuição
├── static/                     # Frontend estático Vanilla HTML5/CSS3/ES6
│   ├── index.html              # Interface principal da Biblioteca e Clubes
│   ├── reader.html             # Interface do Leitor Adaptativo (PDF.js e EPUB.js)
│   ├── css/style.css           # Folha de estilo editorial (Claro, Escuro, Sépia)
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca, filtros taxonômicos, clubes e admin
│   │   ├── shared_reading.js   # Painel de Leitura Compartilhada (Visão Geral, Discussões, Metas, Citações)
│   │   └── reader.js           # Motor do leitor PDF/EPUB, seleção e compartilhamento de citações
│   └── vendor/                 # PDF.js, EPUB.js e JSZip locais
└── tests/                      # Suíte de testes automatizados (248 testes: 211 unit/int + 37 E2E)
```

---

## 5. Perfis de Usuário, Autenticação e Gestão de Contas

O Lectoria suporta múltiplos perfis de usuário com autenticação local segura, permitindo o compartilhamento do servidor com familiares e outros leitores, mantendo a privacidade de anotações e progresso.

### 5.1 Papéis de Acesso (*Roles*)
- **`admin`**: Acesso irrestrito a todas as funcionalidades de leitura, além de gestão de fontes de biblioteca, criação e edição de usuários, redefinição de senhas e backups.
- **`reader`**: Acesso completo ao catálogo compartilhado de documentos, com seu próprio progresso de leitura, marcações, anotações, desenhos e coleções isoladas. Não tem acesso a configurações de fontes, outros usuários ou backups.

### 5.2 Configuração Inicial do Administrador
Na primeira execução do Lectoria, caso nenhum administrador tenha sido configurado, a interface web apresentará automaticamente uma tela de configuração (*setup wizard*) para criação da conta mestre.

Alternativamente, a conta administradora pode ser criada via terminal com o script CLI:
```bash
python3 scripts/manage_users.py --create-admin --username admin --display-name "Administrador"
```
*(O script solicitará a senha de acesso de forma segura sem ecoar caracteres no terminal).*

### 5.3 Comandos da CLI de Gestão de Usuários (`scripts/manage_users.py`)

- **Listar todos os usuários**:
  ```bash
  python3 scripts/manage_users.py --list
  ```
- **Criar um novo usuário leitor**:
  ```bash
  python3 scripts/manage_users.py --create-user joao --display-name "João Silva"
  ```
- **Redefinir senha de um usuário**:
  ```bash
  python3 scripts/manage_users.py --reset-password joao
  ```
- **Alterar papel de um usuário (promover ou rebaixar)**:
  ```bash
  python3 scripts/manage_users.py --set-role joao admin
  ```
- **Desativar ou reativar uma conta**:
  ```bash
  python3 scripts/manage_users.py --deactivate joao
  python3 scripts/manage_users.py --activate joao
  ```

---

## 6. Múltiplas Fontes de Biblioteca e Segurança de Diretórios

O Lectoria permite cadastrar múltiplos diretórios locais no servidor (ex: `~/Livros`, `/mnt/storage/ebooks` ou a pasta padrão `books/`).

### 6.1 Adicionando e Gerenciando Fontes (Seletor Visual de Diretórios)
Pela interface web (acessível no menu **Fontes**, restrito a administradores):
1. Clique em **Selecionar pasta…** para abrir o navegador visual de diretórios do servidor (`GET /api/sources/browse`), que exibe atalhos rápidos (Pasta Pessoal, Biblioteca Padrão, `/media`, `/mnt`), subpastas legíveis e a contagem de arquivos `.pdf` em cada pasta — funcionando tanto localmente quanto a partir de qualquer dispositivo da rede doméstica.
2. Caso esteja acessando o Lectoria diretamente no navegador da própria máquina do servidor com interface gráfica ativa (`DISPLAY`/`WAYLAND_DISPLAY`), o botão **Janela do Sistema** (`POST /api/sources/pick-native`) também estará disponível para abrir o seletor nativo do Linux (`zenity` / `kdialog`). Por segurança, requisições vindas de outros dispositivos na rede jamais podem abrir janelas gráficas no servidor.
3. Também continua sendo possível digitar ou editar manualmente o caminho no campo de texto antes de clicar em **Adicionar**.
4. O sistema realiza varredura recursiva sob demanda à procura de arquivos `.pdf`. É possível ativar, desativar temporariamente ou escanear fontes individualmente.

> [!IMPORTANT]
> **Preservação de Arquivos Físicos**:
> Remover uma fonte no Lectoria desvincula seus livros do catálogo da aplicação, mas **NUNCA apaga ou altera nenhum arquivo PDF físico do disco**.

### 6.2 Regras Estritas de Segurança de Fontes
Para proteção total do sistema operacional hospedeiro:
- Diretórios reservados do sistema (`/`, `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/proc`, `/root`, `/sys`, `/usr`) são **rigorosamente bloqueados**.
- Pastas internas da aplicação (`data/`, `backups/`, diretório raiz do projeto) são **rejeitadas**.
- Links simbólicos (*symlinks*) são resolvidos canonicamente; se um link apontar para fora do diretório da fonte autorizada, ele é descartado silenciosamente para impedir vazamento de arquivos.

---

## 7. Sistema de Capas e Download Direto de Documentos PDF

### 7.1 Funcionamento do Sistema de Capas (`data/covers/`)
O Lectoria exibe capas reais para os livros em três níveis de prioridade, sem alterar os arquivos PDF originais:
1. **Capa Personalizada (`custom`)**: Qualquer volume pode receber uma capa enviada pelo usuário clicando no botão **Capa** sobre o livro (`POST /api/books/{id}/cover`). O servidor valida a assinatura binária real (*magic bytes* de `PNG`, `JPEG` ou `WebP`, até 5 MB) e rejeita arquivos executáveis, HTML ou SVG. Para voltar à capa automática, basta clicar em **Restaurar Capa Automática** (`DELETE /api/books/{id}/cover`).
2. **Capa Extraída ou Renderizada da 1ª Página (`extracted`)**: Quando o livro não possui capa personalizada, o Lectoria extrai sob demanda a imagem incorporada na primeira página do PDF (ou armazena a miniatura renderizada pelo PDF.js ao abrir o livro) e grava em cache em `data/covers/` (diretório ignorado pelo Git). Se o PDF original for atualizado no disco (`file_hash` alterado), o cache é invalidado e regenerado automaticamente.
3. **Capa Editorial Substituta Determinística (`fallback`)**: Caso o PDF não possua imagem de capa extraível ainda, o servidor gera uma capa vetorial SVG editorial com paleta clássica, moldura tipográfica e ornamento determinístico calculado a partir do hash SHA-256 do livro.

### 7.2 Download Direto de Documentos PDF
- **Na Biblioteca**: Cada card de livro (nas visualizações em Grade e em Lista) conta com a ação **PDF**, iniciando o download direto do arquivo original no dispositivo do usuário.
- **No Leitor**: O cabeçalho superior inclui o botão **Baixar PDF Original** para baixar o arquivo que está sendo lido.
- **Streaming Seguro**: Os arquivos são servidos via streaming HTTP em chunks com o cabeçalho `Content-Disposition: attachment; filename="..."`, sem sobrecarregar a memória RAM do servidor.

---

## 8. Instalação e Execução Manual

### 8.1 Preparar Ambiente Virtual e Dependências

```bash
# Criar o ambiente virtual isolado
python3 -m venv .venv
source .venv/bin/activate

# Para executar em produção:
pip install -r requirements.txt

# Para rodar testes e desenvolvimento:
pip install -r requirements-dev.txt
```

### 8.2 Iniciar o Servidor Manualmente

```bash
source .venv/bin/activate
python3 main.py
```

O servidor iniciará escutando em `http://0.0.0.0:8765`.

> **Coexistência com o Jellyfin**: A porta padrão do Lectoria é `8765`, não interferindo com as portas `8096` ou `8920` do Jellyfin.

### 8.3 Execução via Docker Compose

O repositório inclui `Dockerfile` e `docker-compose.yml` prontos para implantação auto-hospedada:

```bash
# Construir e subir o contêiner em segundo plano
docker compose up -d --build

# Acompanhar logs de inicialização e migração automática SQLite (v10 -> v11)
docker compose logs -f

# Verificar saúde do serviço
curl -fsS http://127.0.0.1:8765/api/health
```

Os volumes persistentes são mapeados por padrão para `./books:/books:ro` (acervo somente leitura), `./data:/data` (banco SQLite `lectoria.db` e capas) e `./backups:/backups`. Consulte [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) para detalhes de operação e atualização.

---

## 9. Implantação no Servidor Doméstico (Linux / systemd)

Esta seção descreve o procedimento de instalação, configuração e operação do serviço em distribuições Linux baseadas em Debian/Ubuntu (como Linux Mint).

### 9.1 Pré-requisitos
Abra o terminal e garanta que os pacotes básicos estejam instalados:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip sqlite3 zenity git
```

### 9.2 Instalação Automatizada (`./deployment/install.sh`)
Execute o instalador idempotente **com o seu usuário comum** (sem `sudo` nesta etapa):

```bash
./deployment/install.sh
```

O script `install.sh` executa automaticamente:
1. Verificação do Python 3, `venv`, `sqlite3` e `zenity`.
2. Criação (ou recriação automática, caso a pasta `.venv` tenha vindo de outra máquina) do ambiente virtual `.venv/`.
3. Instalação das dependências fixadas em `requirements.txt`.
4. Geração do arquivo `.env` e ajuste automático dos caminhos e usuário no arquivo `deployment/lectoria.service`.
5. Inicialização/migração segura do banco `data/lectoria.db` (Schema V5), reconciliação automática do caminho da biblioteca principal e verificação `PRAGMA integrity_check`.

### 9.3 Configuração (`./deployment/configure.sh`)
Para personalizar a porta HTTP, endereço de escuta ou caminhos de diretórios:

```bash
./deployment/configure.sh
```

Ou de forma não interativa:

```bash
./deployment/configure.sh --non-interactive \
  --port 8765 \
  --host 0.0.0.0 \
  --library-dir "~/Livros"
```

### 9.4 Ativação do Serviço Automático (`systemd`)
Após a instalação:

```bash
sudo cp deployment/lectoria.service /etc/systemd/system/lectoria.service
sudo systemctl daemon-reload
sudo systemctl enable --now lectoria.service
```

Comandos úteis de gerenciamento:
```bash
# Verificar status e consumo de recursos
sudo systemctl status lectoria.service

# Acompanhar logs em tempo real
sudo journalctl -u lectoria.service -f

# Reiniciar ou parar o serviço
sudo systemctl restart lectoria.service
sudo systemctl stop lectoria.service
```

### 9.5 Diagnóstico de Prontidão (`./deployment/diagnose.sh`)
Executa uma bateria completa de verificações (SO, CPU, RAM disponível, permissões, integridade e modo WAL do SQLite, portas, uso de memória RSS e latência):

```bash
./deployment/diagnose.sh
# Ou formato JSON:
./deployment/diagnose.sh --json
```

---

## 10. Modelo de Segurança, Permissões e Acesso em Rede

### 10.1 Autenticação, Sessões e Proteção CSRF
- **Hashing de Senhas**: Utiliza `hashlib.scrypt` com salt individual de 16 bytes e parâmetros fortes (`N=16384, r=8, p=1`).
- **Hashing SHA-256 de Sessões**: Tokens de sessão são armazenados no banco de dados na forma de hash SHA-256 (`token_hash`), impedindo o sequestro de contas mesmo em caso de leitura indevida do banco ou de backups.
- **Revogação Imediata**: Sessões ativas são automaticamente destruídas na troca de senha, desativação de conta ou exclusão do usuário.
- **Proteção Completa contra CSRF (Cross-Site Request Forgery)**:
  - Validação estrita de correspondência de `Origin` e `Referer` contra o cabeçalho `Host` em todas as rotas mutantes (`POST`, `PUT`, `DELETE`, `PATCH`).
  - Padrão *Double Submit Cookie* via cookie `lectoria_csrf` e injeção do cabeçalho `X-CSRF-Token` pelo frontend Vanilla JavaScript.
  - Compatibilidade com salvamento em fechamento de janela (`sendBeacon`) através de validação via parâmetro de consulta (`?csrf_token=...`).
- **Rate-Limiting em Memória**: Bloqueio temporário automático de 5 minutos contra tentativas sucessivas e contínuas de força bruta no login.
- **Controle de Acesso Baseado em Papéis (RBAC)**: Rotas de fontes, usuários e backups são restritas estritamente a administradores autenticados.
- **Sanitização de Informações**: O endpoint `/api/health` retorna exclusivamente o status do serviço sem expor a topologia de diretórios do servidor.

### 10.2 Permissões de Sistema de Arquivos (Menor Privilégio)
- **Modo Somente Leitura**: As pastas de livros (`books/` e fontes cadastradas) são acessadas exclusivamente em modo de leitura. O Lectoria **nunca** apaga, renomeia ou altera arquivos PDF originais.
- **Escrita Restrita**: Apenas as pastas `data/` (banco de dados e cache de capas) e `backups/` requerem permissão de escrita.

### 10.3 Acesso em Rede Local e Externa
- **Rede Local (LAN)**: Por padrão, o Lectoria escuta na interface `0.0.0.0:8765`, permitindo acesso instantâneo por outros dispositivos da rede Wi-Fi/Ethernet doméstica.
- **Firewall Local**: Caso o firewall (`ufw`) esteja ativo no servidor:
  ```bash
  sudo ufw allow 8765/tcp comment "Lectoria PDF Server"
  ```
- **Acesso Externo Seguro**: Não exponha a porta `8765` diretamente à Internet sem criptografia TLS. Recomenda-se o uso de VPNs de malha (como **Tailscale** ou **WireGuard**) ou um *Reverse Proxy* (Caddy/Nginx) com certificado HTTPS e autenticação perimetral.

---

## 11. Atalhos de Teclado no Leitor

| Tecla | Ação |
| :--- | :--- |
| `Seta Esquerda` / `PageUp` | Página anterior |
| `Seta Direita` / `PageDown` / `Espaço` | Próxima página |
| `b` | Voltar à última página visitada |
| `+` ou `=` | Aumentar zoom |
| `-` | Diminuir zoom |
| `w` | Ajustar à largura da janela (*Fit Width*) |
| `p` | Ajustar à página inteira (*Fit Page*) |
| `r` | Modo Leitura |
| `h` | Modo Marca-texto |
| `d` | Modo Desenho Livre SVG |
| `a` | Abrir / fechar Caderno de Estudos |
| `m` | Abrir / fechar Miniaturas de Páginas |
| `s` | Alternar entre Página Única e Rolagem Contínua |
| `t` | Alternar Tema Claro / Escuro |
| `?` | Abrir guia de atalhos de teclado |
| `Esc` | Fechar modais e painéis laterais |

---

## 12. Execução dos Testes Automatizados

Com o ambiente virtual ativado e as dependências de desenvolvimento instaladas (`requirements-dev.txt`):

```bash
# Executa os 211 testes unitários e de integração de API/persistência
./.venv/bin/pytest --ignore=tests/e2e -q

# Executa os 37 testes de ponta a ponta (E2E) em navegador real Playwright Chromium
./.venv/bin/pytest tests/e2e -q
```

A suíte completa da **v1.7.0** possui **248 testes automatizados aprovados** (`211` unitários/integração + `37` E2E no Chromium headless), cobrindo:
- **Leitura Compartilhada (v1.7.0)**: migração SQLite v10 → v11 com backup pré-migração único e rollback em falha injetada (`test_v170_shared_reading_schema.py`), regras de negócio e permissões de tópicos, respostas, metas, progresso voluntário e citações (`test_v170_shared_reading_service.py`), 19 endpoints REST, CSRF, anti-IDOR, validação Pydantic `extra="forbid"` e política somente-leitura para leituras concluídas/canceladas/arquivadas (`test_v170_shared_reading_api.py`), interface web desktop/mobile da leitura coletiva (`test_v170_shared_reading_ui_e2e.py`), compartilhamento direto de citações nos leitores PDF.js e EPUB.js e navegação reversa (`test_v170_reader_shared_reading_e2e.py`) e endurecimento de segurança (`test_v170_phase6_hardening.py`).
- **Filtros Taxonômicos da Biblioteca (v1.7.0)**: paridade entre contadores da árvore taxonômica e listagem filtrada, desacoplamento de facetas ortogonais, diagnóstico read-only e filtros combinados desktop/mobile (`test_v170_library_filters_regression.py` e `test_v170_library_filters_e2e.py`).
- **Clubes de Leitura, Nossa Biblioteca, Leitor Adaptativo, Segurança e Persistência (v1.0.0–v1.6.3)**: cobertura integral de regressão em todos os subsistemas anteriores.

---

## 13. Documentação Técnica e Arquitetural

Para desenvolvedores, mantenedores e agentes de IA, consulte o guia mestre e os documentos especializados:

- **[AGENTS.md](AGENTS.md)**: Ponto de entrada obrigatório, regras de governança e mapa de módulos.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Arquitetura geral do sistema e diagramas de fluxo.
- **[docs/DATABASE.md](docs/DATABASE.md)**: Schema SQLite v11, tabelas, FKs, triggers e política de migração.
- **[docs/AUTHORIZATION.md](docs/AUTHORIZATION.md)**: Matriz de permissões, autenticação scrypt, CSRF, anti-IDOR e privacidade.
- **[docs/READER.md](docs/READER.md)**: Leitores PDF.js e EPUB.js, HTTP Range (512 KB), citações e navegação.
- **[docs/FRONTEND.md](docs/FRONTEND.md)**: Arquitetura Vanilla JS, cache em memória, máscaras de spoiler e responsividade.
- **[docs/API.md](docs/API.md)**: Contratos da API REST e modelos de requisição/resposta.
- **[docs/TESTING.md](docs/TESTING.md)**: Organização das suítes Pytest e Playwright E2E.
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**: Implantação via Docker Compose, systemd, backups e atualização em produção.
- **[docs/DECISIONS/](docs/DECISIONS/)**: Registros de Decisões Arquiteturais (`ADR-001` a `ADR-006`).

---

## 14. Desempenho e Limitações Conhecidas

### 14.1 Medições Reais e Perfil de Consumo
- **Consumo de Memória RAM (RSS medido via `scripts/diagnose.py`)**: **~54 MB a 65 MB** em processo único Uvicorn/FastAPI.
- **Latência de Listagem SQLite (WAL)**: **~0.5 ms**.
- **Consumo de CPU em Repouso**: **0.0%** (zero file watchers contínuos com `inotify`).
- **Coexistência com o Jellyfin**: Limites cgroup no `systemd` (`MemoryMax=512M`, `CPUWeight=100`), somados ao streaming sob demanda por blocos (HTTP Range), protegem o servidor doméstico contra sobrecargas.

### 14.2 Limitações Conhecidas
- **Livros Protegidos por DRM**: O Lectoria é projetado para bibliotecas pessoais e acervos abertos; livros comerciais protegidos por DRM proprietário (Adobe Adept, Apple FairPlay, Readium LCP com chave privada) não são descriptografados e exigem remoção prévia da proteção.
- **Varredura Sob Demanda ou Agendada**: Para não degradar a CPU nem gastar ciclos com sentinelas contínuos de arquivos, novos arquivos adicionados às pastas de fontes são indexados sob demanda (botão "Escanear") ou pelo agendador leve de diretórios (`automation_service.py`).

---

## 15. Licenciamento

- **Lectoria**: Distribuído sob os termos da [Licença MIT](LICENSE) (Copyright © 2026 Emerson Vinicius Santos de Santana).
- **Componentes de Terceiros**:
  - [Mozilla PDF.js](https://github.com/mozilla/pdfjs-dist) v3.11.174 (`static/vendor/pdfjs/`): Distribuído sob a [Apache License 2.0](static/vendor/pdfjs/LICENSE).
  - [EPUB.js](https://github.com/futurepress/epub.js) v0.3.93 (`static/vendor/epubjs/epub.min.js`): Distribuído sob a [Licença FreeBSD (2-Clause)](static/vendor/epubjs/LICENSE) (Copyright © 2013-2023 Futurepress).
  - [JSZip](https://github.com/Stuk/jszip) v3.10.1 (`static/vendor/epubjs/jszip.min.js`): Distribuído sob a [Licença MIT](static/vendor/epubjs/LICENSE) (Copyright © 2009-2016 Stuart Knightley).
