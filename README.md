# Lectoria — Servidor Pessoal de Livros Digitais (v1.5.1 — Biblioteca Viva)

O **Lectoria** é um servidor pessoal auto-hospedado (*self-hosted*) para organização, identificação inteligente, automação e leitura colaborativa de documentos PDF e livros digitais EPUB, projetado com foco em sobriedade estética editorial, velocidade e consumo mínimo de recursos de CPU, memória RAM e I/O de disco.

Foi concebido para operar perfeitamente mesmo em hardware modesto — como computadores antigos e mini-PCs domésticos com processadores de baixo consumo (ex: Intel Celeron), convivendo de forma estável e silenciosa com serviços de mídia como o Jellyfin.

---

## 1. Principais Recursos

- **Biblioteca Viva: Camada Multiusuário e Social "Nossa Biblioteca" (v1.5.0)**:
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
- **Banco de Dados**: SQLite3 em modo **WAL** (`Write-Ahead Logging`), com `synchronous=NORMAL` e controle de migrações (`schema_migrations`), garantindo persistência rápida, sem bloqueios de leitura e com baixo desgaste de disco SSD.
  - **Schema V1**: tabelas `users`, `books` e `reading_progress`.
  - **Schema V2**: tabelas `collections` e `collection_books`.
  - **Schema V3**: tabelas `highlights`, `notes` e `page_drawings`.
  - **Schema V4**: tabelas `library_sources`, `sessions` e isolamento multiusuário (`user_id`, `role`, `password_hash`).
  - **Schema V5**: metadados de capas em `books` (`cover_type`, `cover_path`, `cover_source_hash`, `cover_updated_at`).
- **Frontend**: HTML5 semântico, CSS3 enxuto e JavaScript Vanilla (ES6). Zero frameworks pesados (sem React, Vue, Angular ou Node.js).
- **Leitor PDF**: Baseado em **PDF.js** estático local (`static/vendor/pdfjs/`). A renderização ocorre integralmente no navegador do cliente, sem requisições a CDNs externas e sem telemetria.
- **Streaming de Arquivos**: Suporte nativo a **HTTP Range Requests (206 Partial Content)**. O servidor serve blocos sob demanda, permitindo abrir documentos grandes sem carregar o arquivo PDF inteiro na memória RAM do servidor.
- **Segurança de Caminhos**: Prevenção estrita contra *Path Traversal* com resolução canônica de caminhos (`pathlib.Path.resolve()`). Identificadores UUID estáveis em vez de expor caminhos do sistema operacional nas rotas públicas.
- **Zero Ociosidade Forçada**: A aplicação consome 0% de CPU quando ociosa (sem file watchers contínuos com `inotify`). O escaneamento da biblioteca é acionado sob demanda pelo usuário.

---

## 4. Estrutura do Projeto

```text
lectoria/
├── .github/
│   └── workflows/
│       └── ci.yml              # Pipeline de Integração Contínua (Python 3.12 e 3.13)
├── .gitignore                  # Exclusões do Git (ignora data/, *.db, backups/, dist/)
├── .env.example                # Modelo documentado de variáveis de ambiente
├── config.py                   # Configurações centralizadas com carregamento de .env
├── database.py                 # Conexão SQLite (WAL mode), migrações V1..V5 e reconciliação
├── auth_service.py             # Autenticação scrypt, sessões SHA-256 e controle de papéis
├── sources_service.py          # Gestão de múltiplas fontes e validação de caminhos
├── directory_picker_service.py # Navegador visual de diretórios e seletor nativo (Zenity/KDialog)
├── covers_service.py           # Extração de capas de PDF, capas personalizadas e SVG editorial
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── annotations_service.py      # Serviço de marcações, anotações e desenhos livres
├── export_service.py           # Exportação (.md, .json) e importação segura de cadernos
├── backup_service.py           # Backup SQLite + capas personalizadas com Lock e integridade
├── library.py                  # Scanner leve (pypdf), filtros, busca e ordenação
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências mínimas de produção (FastAPI, Uvicorn, pypdf)
├── requirements-dev.txt        # Dependências de desenvolvimento e teste (pytest, httpx)
├── CHANGELOG.md                # Histórico de alterações e versões
├── CONTRIBUTING.md             # Guia de contribuição e ambiente de desenvolvimento
├── SECURITY.md                 # Política de reporte de vulnerabilidades
├── LICENSE                     # Licença de uso (MIT)
├── README.md                   # Documentação completa do projeto
├── deployment/                 # Scripts de implantação, manutenção e serviço systemd
│   ├── install.sh              # Instalação idempotente para Linux Mint / Ubuntu / Debian
│   ├── configure.sh            # Configuração de .env e lectoria.service
│   ├── update.sh               # Atualização com backup prévio e migração de schema
│   ├── backup.sh               # Atalho para backup consistente (SQLite + capas)
│   ├── restore.sh              # Atalho para restauração segura de backup e capas
│   ├── uninstall.sh            # Desinstalação preservando dados do usuário
│   ├── diagnose.sh             # Diagnóstico de ambiente e desempenho
│   ├── verify-package.sh       # Verificador de integridade de pacote de distribuição
│   ├── lectoria.service        # Unidade systemd com sandboxing e cgroup
│   └── README.md               # Guia operacional de implantação systemd
├── scripts/
│   ├── backup.py               # Utilitário CLI para backup, restauração e retenção
│   ├── diagnose.py             # Utilitário CLI de diagnóstico de ambiente e desempenho
│   ├── manage_users.py         # Utilitário CLI para gestão de usuários, senhas e papéis
│   └── package.py              # Utilitário de empacotamento para distribuição
├── backups/                    # Armazenamento de backups locais (ignorado no Git)
├── books/                      # Diretório local padrão para PDFs (mantido intacto)
├── data/                       # Dados persistentes locais (lectoria.db, covers/) (ignorado no Git)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (acervo, capas, seletor de pastas)
│   ├── reader.html             # Interface do Leitor (PDF.js, Estudos, Miniaturas)
│   ├── css/
│   │   └── style.css           # Design system editorial e responsividade móvel
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca, capas, administração
│   │   └── reader.js           # Lógica do leitor, toque/gestos móveis e anotações
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos (Licença Apache 2.0)
└── tests/                      # Suíte de testes automatizados (82 testes)
    ├── conftest.py             # Fixtures e isolamento de ambiente em tmp_path
    ├── test_annotations.py     # Destaques, notas e desenhos vetoriais
    ├── test_collections.py     # CRUD e associação de coleções
    ├── test_filters_and_sorting.py # Filtros de status e ordenação
    ├── test_iteration2_features.py # Coleções em lote e PDFs grandes
    ├── test_iteration4.py      # Exportação MD/JSON e backups
    ├── test_iteration5.py      # Limites DoS, concorrência e segurança
    ├── test_iteration6.py      # Downloads, múltiplas fontes, auth e isolamento
    ├── test_iteration7.py      # Auditoria de segurança, CSRF, IDOR e sessões
    ├── test_iteration8.py      # Capas de livros e seleção visual de diretórios
    ├── test_iteration9.py      # Portabilidade (.env), migração segura e scripts
    ├── test_iteration11.py     # Experiência e controles móveis do leitor
    ├── test_library.py         # Catálogo e busca
    ├── test_progress.py        # Progresso de leitura e beacon
    └── test_security.py        # Range requests, path traversal e integridade
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
source .venv/bin/activate
pytest -v tests/
```

A suíte completa possui **119 testes automatizados** aprovados, cobrindo testes unitários, testes de integração de sistema e testes de ponta a ponta (E2E) com navegador real Chromium:

```bash
# Executa os 106 testes unitários e de integração
pytest -v tests/test_*.py

# Executa os 13 testes de ponta a ponta (E2E) em navegador real Playwright Chromium
pytest -v tests/e2e/test_browser_e2e.py

# Executa toda a suíte (119 testes)
pytest -v tests/
```

> **Nota para execução de testes E2E:** Para executar os testes E2E com Chromium em ambiente local de desenvolvimento, instale as dependências com `pip install -r requirements-dev.txt` e baixe o binário do Chromium com `playwright install chromium`. Ambientes de produção não necessitam de Playwright nem Chromium.

A suíte cobre exaustivamente:
- Catálogo, scanner e metadados (`test_library.py`).
- Coleções e associações em lote (`test_collections.py`, `test_iteration2_features.py`).
- Filtros de estado de leitura, busca e ordenação (`test_filters_and_sorting.py`).
- Progresso de leitura e Beacon API (`test_progress.py`).
- Streaming por chunks HTTP Range e path traversal (`test_security.py`).
- Destaques, notas, desenhos vetoriais e algoritmo geométrico da borracha (`test_annotations.py`).
- Exportação Markdown/JSON, preview de importação e rollback (`test_iteration4.py`).
- Limites de DoS na importação, validação de profundidade JSON, concorrência de backups e retenção (`test_iteration5.py`).
- Download de documentos originais, gerenciamento de fontes, symlinks, scrypt, rate limiting, controle de papéis e isolamento de dados por usuário (`test_iteration6.py`).
- Hashing de sessões, invalidação de sessões, CSRF Double Submit, Origin check, IDOR em anotações, probing de coleções, proteção de fontes (.venv, .git) e integridade em restauração multiusuário (`test_iteration7.py`).
- Extração de capas de PDF, cache em disco, invalidação por hash, upload/substituição/remoção de capas personalizadas, bloqueio de uploads maliciosos, fallback SVG editorial, navegador visual de diretórios e seletor nativo local (`test_iteration8.py`).
- Carregamento e precedência de `.env`, reconciliação automática do caminho da biblioteca principal entre máquinas, backup preventivo e preservação total de dados na migração de schema V3->V5, preservação de capas personalizadas no backup/restauração e validação dos scripts de deploy e diagnóstico (`test_iteration9.py`).
- Experiência móvel, controles de toque e adaptação visual no leitor de PDF (`test_iteration11.py`).
- Suporte a EPUB, extração de capas, metadados, defesa contra ZIP bomb, path traversal e proteção contra ataques de entidade XML (`test_epub.py`).
- Migração de banco SQLite v5 para v6, cabeçalhos de segurança HTTP, integridade de scripts e PDFs sintéticos de 5 a 200 páginas (`test_iteration19.py`).
- Componentes e estilização do leitor adaptativo e travas de rolagem (`test_reader_interface.py`).
- Testes E2E reais no Chromium Playwright cobrindo 8 resoluções de tela, ausência de rolagem involuntária de PDF, gestos de desenho livre, ergonomia da barra de tablet e segurança de scripts no EPUB (`tests/e2e/test_browser_e2e.py`).

---

## 13. Desempenho e Limitações Conhecidas

### 13.1 Medições Reais e Perfil de Consumo
- **Consumo de Memória RAM (RSS medido via `scripts/diagnose.py`)**: **~54 MB a 65 MB** em processo único Uvicorn/FastAPI.
- **Latência de Listagem SQLite (WAL)**: **~0.5 ms**.
- **Consumo de CPU em Repouso**: **0.0%** (zero file watchers contínuos com `inotify`).
- **Coexistência com o Jellyfin**: Limites cgroup no `systemd` (`MemoryMax=512M`, `CPUWeight=100`), somados ao streaming sob demanda por blocos (HTTP Range), protegem o servidor doméstico contra sobrecargas.

### 13.2 Limitações Conhecidas da Versão 1.3.0
- **Livros Protegidos por DRM**: O Lectoria é projetado para bibliotecas pessoais e acervos abertos; livros comerciais protegidos por DRM proprietário (Adobe Adept, Apple FairPlay, Readium LCP com chave privada) não são descriptografados e exigem remoção prévia da proteção.
- **Varredura Sob Demanda**: Para não degradar a CPU nem gastar ciclos com sentinelas contínuos de disco, novos arquivos adicionados às pastas de fontes são indexados sob demanda ao clicar no botão "Escanear" na interface web ou via API.

---

## 14. Licenciamento

- **Lectoria**: Distribuído sob os termos da [Licença MIT](LICENSE) (Copyright © 2026 Emerson Vinicius Santos de Santana).
- **Componentes de Terceiros**:
  - [Mozilla PDF.js](https://github.com/mozilla/pdfjs-dist) v3.11.174 (`static/vendor/pdfjs/`): Distribuído sob a [Apache License 2.0](static/vendor/pdfjs/LICENSE).
  - [EPUB.js](https://github.com/futurepress/epub.js) v0.3.93 (`static/vendor/epubjs/epub.min.js`): Distribuído sob a [Licença FreeBSD (2-Clause)](static/vendor/epubjs/LICENSE) (Copyright © 2013-2023 Futurepress).
  - [JSZip](https://github.com/Stuk/jszip) v3.10.1 (`static/vendor/epubjs/jszip.min.js`): Distribuído sob a [Licença MIT](static/vendor/epubjs/LICENSE) (Copyright © 2009-2016 Stuart Knightley).
