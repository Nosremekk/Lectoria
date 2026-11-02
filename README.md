# Lectoria — Servidor Pessoal de Livros Digitais (v1.1.1)

O **Lectoria** é um servidor pessoal de leitura focado em documentos PDF, projetado especificamente para operar com consumo mínimo de CPU, memória RAM e I/O de disco em hardware modesto (como computadores antigos com processador Intel Celeron, operando concomitantemente com o Jellyfin).

---

## 1. Principais Recursos da Versão 1.1

- **Biblioteca Pessoal Editorial com Múltiplas Fontes**:
  - **Identidade Visual Editorial Própria**: Paleta inspirada em encadernações e papelaria clássica nos temas Claro (Papel Marfim `#F6F3EC` e Tinta Carvão `#1C1B18` com acento Terracota `#9E3B24`) e Escuro (Obsidiana Quente `#141311` e Pergaminho `#EAE5DC`), usando pilhas tipográficas locais (Serifada Editorial para títulos, Sans-serif para controles e Monoespaçada Tabular para métricas).
  - Suporte a **múltiplas fontes e diretórios locais configuráveis**, com **Seletor Visual de Diretórios** integrado (navegador visual seguro pelo navegador web para acesso local ou em rede doméstica + integração com seletor nativo do sistema via Zenity/KDialog quando utilizado diretamente na máquina do servidor).
  - Varredura sob demanda (*on-demand scanner*) com extração rápida de metadados (`pypdf`), sem modificação dos arquivos originais.
  - Garantia estrita de preservação: remoção ou desativação de fontes **nunca apaga arquivos físicos do disco**.
  - **Sistema de Capas em 3 Camadas (`covers_service.py`)**:
    1. **Extração e Cache da Capa Real do PDF**: Extração automática sob demanda da imagem da primeira página do PDF (`JPEG`/`PNG`) ou cache da miniatura renderizada pelo PDF.js em `data/covers/` (ignorado pelo Git), com invalidação automática se o arquivo PDF mudar.
    2. **Capas Personalizadas**: Upload manual de capa (`PNG`, `JPEG`, `WebP` até 5 MB) com validação estrita de assinatura binária (*magic bytes*), substituição e restauração da capa automática a qualquer momento.
    3. **Capa Editorial Substituta Determinística (SVG)**: Geração vetorial determinística baseada em hash SHA-256 do livro quando o PDF não contém imagem de capa identificável.
  - Busca instantânea por título e autor, filtros por estado de leitura e ordenação multicritério.
  - Gestão de Coleções personalizadas com atribuição de livros em lote.
- **Download Direto de Documentos**:
  - Download rápido do PDF original diretamente da biblioteca (cards em grade e lista) ou da barra de ferramentas do leitor.
  - Streaming em chunks com consumo de memória RAM próximo de zero e cabeçalho `Content-Disposition: attachment`.
- **Perfis de Usuário e Autenticação Segura**:
  - Suporte a múltiplos perfis de usuário com dois papéis de acesso: Administrador (`admin`) e Leitor (`reader`).
  - Hashing seguro de senhas com `hashlib.scrypt` (da biblioteca padrão do Python) e tokens de sessão criptográficos (`lectoria_session`).
  - Proteção contra força bruta com rate-limiting em memória (bloqueio temporário após tentativas consecutivas).
  - Isolamento estrito de dados pessoais: catálogo de livros compartilhado, mas progresso, destaques, anotações, desenhos e coleções totalmente individuais para cada usuário.
  - Gerenciamento completo via interface web (modal administrativo) ou via terminal com `scripts/manage_users.py`.
- **Leitor Integrado de Alta Performance (PDF.js)**:
  - Modos de leitura em **Página Única** e **Rolagem Contínua Virtualizada** (descarregamento dinâmico de páginas fora do viewport).
  - Streaming eficiente via **HTTP Range Requests (206 Partial Content)**, evitando o carregamento do arquivo PDF inteiro na memória RAM.
  - Salvamento automático contínuo de progresso de leitura via API e Beacon API.
  - Temas Claro e Escuro com paletas de baixo contraste para conforto visual prolongado.
  - Gaveta lateral de miniaturas (*thumbnails*) com *lazy rendering* via `IntersectionObserver` e descarte automático de ciclo de CPU quando fechada.
  - Barra de progresso visual, histórico de navegação (`b`) e atalhos completos de teclado (`?`).
- **Caderno de Estudos e Anotações Avançado**:
  - Marca-texto com 5 cores e gravação de retângulos normalizados independentes de zoom ou resolução de tela.
  - Anotações e notas vinculadas a trechos destacados ou avulsas por página.
  - Desenho livre vetorial em SVG sobreposto às páginas com ferramenta de borracha geométrica de alta precisão.
  - Painel de estudos com busca local, filtros dinâmicos e navegação direta para os trechos anotados.
  - Exportação formatada em **Markdown (`.md`)** e exportação estruturada em **JSON (`.json`)**.
- **Segurança, Integridade e Estabilidade**:
  - Bloqueio rigoroso contra inclusão de diretórios de sistema (`/`, `/etc`, `/proc`, etc.) e pastas internas da aplicação (`data/`, `backups/`).
  - Proteção estrita contra *Path Traversal* em todas as rotas e resolução canônica de links simbólicos.
  - Importação de anotações com preview, limites de tamanho de corpo (15 MB), profundidade máxima (10 níveis) e transações atômicas com rollback.
  - Sistema de backup SQLite Online com proteção contra concorrência (`threading.Lock`), cópia prévia de salvaguarda e retenção configurável.
  - Unidade de serviço para inicialização em segundo plano via **systemd** com diretivas de sandboxing.

---

## 2. Controle de Versão Git e Sistema de Backups

O Lectoria conta com um repositório Git estritamente **local** para histórico de código e preservação do banco de dados da aplicação (`data/lectoria.db`).

### 2.1 Comandos Básicos do Git

- **Verificar o histórico de commits**:
  ```bash
  git log --oneline
  ```
- **Verificar o status dos arquivos**:
  ```bash
  git status
  ```
- **Verificar alterações não commitadas**:
  ```bash
  git diff
  ```
- **Criar um novo commit com suas alterações**:
  ```bash
  git add .
  git commit -m "tipo: descrição clara da alteração"
  ```
- **Recuperar arquivos ou restaurar uma versão anterior**:
  ```bash
  # Descartar alterações não salvas em um arquivo:
  git restore nome_do_arquivo

  # Voltar temporariamente a um commit anterior:
  git checkout <HASH_DO_COMMIT>
  ```

---

### 2.2 Gerenciamento de Backups (`scripts/backup.py`)

O Lectoria dispõe do módulo `backup_service.py` e da interface CLI `scripts/backup.py`, utilizando a **SQLite Online Backup API** com trava de exclusão mútua (`threading.Lock`). Isso permite gerar cópias de segurança consistentes mesmo durante execuções ativas ou em modo WAL, armazenando-as em `backups/` (ignorado pelo versionamento Git).

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

### 2.3 Procedimento Oficial de Restauração de Banco de Dados

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

> [!IMPORTANT]
> **Aviso de Segurança e Backup Externo**:
> O repositório Git local e a pasta `backups/` protegem contra alterações e exclusões acidentais de software. No entanto, **o Git local NÃO substitui uma rotina de backup externo** (como cópia periódica da pasta `data/` ou `backups/` para um pendrive ou armazenamento em nuvem pessoal) em caso de pane física no disco SSD ou queima de componentes da placa-mãe.

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
/home/vini/Jellyfin livros/
├── .gitignore                  # Regras do Git (inclui data/ e .env.example; ignora .env, books/*.pdf e backups/)
├── .env.example                # Modelo documentado de variáveis de ambiente para deploy
├── .venv/                      # Ambiente virtual Python
├── config.py                   # Configurações centralizadas com carregamento nativo de .env
├── database.py                 # Conexão SQLite (WAL mode), migrações seguras V1..V5 e reconciliação de caminhos
├── auth_service.py             # Autenticação scrypt, sessões SHA-256 e controle de papéis
├── sources_service.py          # Gestão de múltiplas fontes e validação canônica de caminhos
├── directory_picker_service.py # Navegador visual de diretórios e seletor nativo local (Zenity/KDialog)
├── covers_service.py           # Extração de capas de PDF, capas personalizadas e SVG editorial
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── annotations_service.py      # Serviço de marcações, anotações e desenhos livres
├── export_service.py           # Serviço de exportação (.md, .json) e importação segura
├── backup_service.py           # Serviço de backup SQLite + capas personalizadas com Lock e integridade
├── library.py                  # Scanner leve (pypdf), filtros, busca, ordenação e segurança
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências Python mínimas fixadas
├── CHANGELOG.md                # Histórico de alterações e versões
├── README.md                   # Documentação completa do projeto
├── deployment/                 # Scripts de implantação, manutenção e serviço systemd
│   ├── install.sh              # Instalação idempotente para Linux Mint XFCE / Ubuntu / Debian
│   ├── configure.sh            # Configuração interativa ou via flags de .env e lectoria.service
│   ├── update.sh               # Atualização segura com backup prévio obrigatório e migração de schema
│   ├── backup.sh               # Atalho para backup consistente (SQLite + capas personalizadas)
│   ├── restore.sh              # Atalho para restauração segura de backup e capas
│   ├── uninstall.sh            # Desinstalação do serviço preservando todos os dados do usuário
│   ├── diagnose.sh             # Atalho para diagnóstico completo do ambiente e desempenho
│   ├── lectoria.service        # Unidade systemd com sandboxing e limites cgroup (512 MB RAM)
│   └── README.md               # Guia operacional de implantação systemd
├── scripts/
│   ├── backup.py               # Utilitário CLI para backup, restauração e retenção
│   ├── diagnose.py             # Utilitário CLI de diagnóstico de ambiente, SQLite WAL e latência/RAM
│   └── manage_users.py         # Utilitário CLI para gestão de usuários, senhas e papéis
├── backups/                    # Armazenamento de backups do banco e capas customizadas (ignorado no Git)
├── books/                      # Diretório local padrão para arquivos PDF (mantido intacto)
├── data/                       # Banco de dados persistente versionado (lectoria.db) e cache local data/covers/ (ignorado no Git)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (acervo, capas, seletor de pastas, fontes, usuários)
│   ├── reader.html             # Interface do Leitor (PDF.js, Estudos, Miniaturas, Download)
│   ├── css/
│   │   └── style.css           # Design system editorial e temas claro/escuro
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca, capas, seletor de diretórios e administração
│   │   └── reader.js           # Lógica do leitor, cache de miniatura da 1ª página e anotações
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos offline
└── tests/                      # Suíte de testes automatizados (77 testes)
    ├── conftest.py             # Fixtures e isolamento de ambiente
    ├── test_annotations.py     # Testes de destaques, notas e desenhos vetoriais
    ├── test_collections.py     # Testes de CRUD e associação de coleções
    ├── test_filters_and_sorting.py # Testes de filtros de status e ordenação
    ├── test_iteration2_features.py # Testes de coleções em lote e PDFs grandes
    ├── test_iteration4.py      # Testes de exportação MD/JSON e backups
    ├── test_iteration5.py      # Testes de limites DoS, concorrência e segurança
    ├── test_iteration6.py      # Testes de downloads, múltiplas fontes, auth e isolamento
    ├── test_iteration7.py      # Testes de auditoria de segurança, CSRF, IDOR e sessões
    ├── test_iteration8.py      # Testes de capas de livros e seleção visual de diretórios
    ├── test_iteration9.py      # Testes de portabilidade (.env), migração segura e scripts de deploy
    ├── test_library.py         # Testes de catálogo e busca
    ├── test_progress.py        # Testes de progresso de leitura e beacon
    └── test_security.py        # Testes de Range requests, path traversal e integridade
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
source .venv/bin/activate
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

O Lectoria permite cadastrar múltiplos diretórios locais no servidor (ex: `/home/vini/Livros_Tecnicos`, `/mnt/storage/ebooks` ou a pasta padrão `books/`).

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
- **Streaming Seguro**: Os arquivos são servidos via streaming HTTP em chunks com o cabeçalho `Content-Disposition: attachment; filename="..."`, sem carregar o arquivo na memória RAM do Celeron 847.

---

## 8. Instalação e Execução Manual

### 5.1 Ativar Ambiente Virtual e Dependências

```bash
cd "/home/vini/Jellyfin livros"
source .venv/bin/activate
pip install -r requirements.txt
```

### 5.2 Iniciar o Servidor Manualmente

```bash
source .venv/bin/activate
python3 main.py
```

O servidor iniciará escutando em `http://0.0.0.0:8765`.

> **Coexistência com o Jellyfin**: A porta padrão do Lectoria é `8765`, não interferindo com as portas `8096` ou `8920` do Jellyfin.

---

## 9. Deploy no Positivo C1260 — Linux Mint XFCE

Esta seção descreve o procedimento completo de instalação, configuração, operação e homologação no servidor doméstico **Positivo C1260** (Intel Celeron 847 1.5 GHz, 8 GB DDR3L, SSD, Linux Mint XFCE com Jellyfin em execução).

### 9.1 Pré-requisitos do Linux Mint
Abra o terminal no Linux Mint XFCE e garanta que os pacotes básicos do sistema estejam instalados:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip sqlite3 zenity git
```

### 9.2 Instalação (`./deployment/install.sh`)
Copie ou clone a pasta do projeto para o Positivo C1260 (por exemplo, `/home/seu_usuario/lectoria`) e execute o instalador idempotente **com o seu usuário comum** (não use `sudo` nesta etapa):

```bash
cd /caminho/para/lectoria
./deployment/install.sh
```

O script `install.sh` executa automaticamente:
1. Verificação do Python 3, `venv`, `sqlite3` e `zenity`.
2. Criação (ou recriação automática, caso a pasta `.venv` tenha vindo de outra máquina) do ambiente virtual isolado `.venv/`.
3. Instalação das dependências fixadas em `requirements.txt` (`FastAPI`, `Uvicorn`, `pypdf`).
4. Geração do arquivo `.env` e ajuste automático dos caminhos e do usuário no arquivo `deployment/lectoria.service`.
5. Inicialização/migração segura do banco `data/lectoria.db` (Schema V5), reconciliação automática do caminho da `Biblioteca Principal` (`id=1`) para a nova máquina e verificação `PRAGMA integrity_check`.

### 9.3 Configuração (`./deployment/configure.sh`)
Para personalizar a porta HTTP, endereço de escuta ou caminhos de diretórios de forma interativa:

```bash
./deployment/configure.sh
```

Ou de forma não interativa via linha de comando:

```bash
./deployment/configure.sh --non-interactive \
  --port 8765 \
  --host 0.0.0.0 \
  --library-dir "/home/seu_usuario/Livros"
```

Todas as configurações ficam centralizadas no arquivo `.env` (baseado em `.env.example`) e são refletidas automaticamente em `deployment/lectoria.service`.

### 9.4 Diretórios Persistentes
Os dados do usuário são estritamente desacoplados do código e nunca são sobrescritos em atualizações:
- **`books/`** (ou `LECTORIA_LIBRARY_DIR`): Diretório padrão de arquivos PDF originais (somente leitura pela aplicação).
- **`data/lectoria.db`**: Banco de dados SQLite em modo WAL com livros, usuários, sessões, coleções, progresso, destaques, notas e desenhos.
- **`data/covers/`**: Cache de capas extraídas dos PDFs e capas personalizadas (`*_custom.*`).
- **`backups/`**: Cópias de segurança consistentes do banco SQLite e das capas personalizadas.

### 9.5 Inicialização Automática (`systemd`)
Após executar `./deployment/install.sh`, instale e ative o serviço para iniciar automaticamente no boot do Linux Mint:

```bash
sudo cp deployment/lectoria.service /etc/systemd/system/lectoria.service
sudo systemctl daemon-reload
sudo systemctl enable --now lectoria.service
```

Comandos úteis de gerenciamento do serviço:
```bash
# Verificar status e consumo de memória/CPU no cgroup
sudo systemctl status lectoria.service

# Acompanhar logs em tempo real
sudo journalctl -u lectoria.service -f

# Reiniciar ou parar o serviço
sudo systemctl restart lectoria.service
sudo systemctl stop lectoria.service
```

### 9.6 Atualização (`./deployment/update.sh`)
Para atualizar o código do Lectoria no Positivo C1260 preservando integralmente o banco de dados, usuários, anotações, capas e coleções:

```bash
./deployment/update.sh
sudo systemctl restart lectoria.service
```
*(Caso tenha atualizado os arquivos via cópia local/rsync sem Git remoto, utilize `./deployment/update.sh --skip-git`).*

### 9.7 Backup (`./deployment/backup.sh`)
Cria um backup online consistente do SQLite (`data/lectoria.db`) junto com todas as capas personalizadas (`data/covers/*_custom.*`) em `backups/`:

```bash
./deployment/backup.sh
```

Para listar os backups existentes ou limpar backups antigos mantendo os `N` mais recentes:
```bash
./deployment/backup.sh --list
./deployment/backup.sh --prune 5
```

### 9.8 Restauração (`./deployment/restore.sh`)
Para restaurar um backup anterior com validação prévia de integridade, cópia de salvaguarda (`data/lectoria_pre_restore.db.bak`), atualização automática de schema e recuperação das capas personalizadas:

```bash
sudo systemctl stop lectoria.service
./deployment/restore.sh --list
./deployment/restore.sh lectoria_backup_AAAAMMDD_HHMMSS.db
sudo systemctl start lectoria.service
```

### 9.9 Diagnóstico (`./deployment/diagnose.sh`)
Executa uma bateria de verificações no servidor (SO, CPU, RAM disponível, dependências Python, permissões `R/W` dos diretórios, integridade e modo WAL do SQLite, existência das pastas das fontes cadastradas, disponibilidade da porta `8765`, URL na rede local, consumo real de memória RSS e latência de consulta):

```bash
./deployment/diagnose.sh
# Ou em formato JSON estruturado:
./deployment/diagnose.sh --json
```

### 9.10 Desinstalação (`./deployment/uninstall.sh`)
Para remover o serviço automático do `systemd` **preservando integralmente** `data/`, `backups/`, `books/` e `.env`:

```bash
sudo ./deployment/uninstall.sh
# Opcional: para remover também a pasta .venv:
./deployment/uninstall.sh --remove-venv
```

### 9.11 Acesso pela Rede Local (LAN)
1. Execute `./deployment/diagnose.sh` para visualizar a linha `URL na Rede Local (LAN): http://192.168.X.X:8765` (ou execute `hostname -I`).
2. Caso o firewall do Linux Mint (`ufw`) esteja ativo, libere a porta `8765` apenas para a rede local:
   ```bash
   sudo ufw allow 8765/tcp comment "Lectoria PDF Server"
   ```
3. Em qualquer computador, celular ou tablet conectado ao mesmo Wi-Fi/rede cabeada, abra o navegador e acesse `http://<IP-DO-POSITIVO-C1260>:8765`.

### 9.12 Checklist de Homologação no Positivo C1260
Quando o projeto for transferido para o Positivo C1260, siga este roteiro passo a passo para homologar a instalação:

- [ ] **1. Instalação inicial**: Executar `./deployment/install.sh` e confirmar a mensagem `Instalação concluída com sucesso!`.
- [ ] **2. Diagnóstico de prontidão**: Executar `./deployment/diagnose.sh` e verificar `RESULTADO GLOBAL: APROVADO (OK)`.
- [ ] **3. Ativação do serviço systemd**: Copiar `deployment/lectoria.service` para `/etc/systemd/system/`, executar `sudo systemctl daemon-reload && sudo systemctl enable --now lectoria.service` e confirmar `active (running)` em `sudo systemctl status lectoria.service`.
- [ ] **4. Coexistência com o Jellyfin**: Verificar que o Jellyfin (`http://localhost:8096`) e o Lectoria (`http://localhost:8765`) respondem simultaneamente e que o consumo de RAM do Lectoria permanece abaixo de `100 MB` em `systemctl status lectoria.service`.
- [ ] **5. Autenticação e perfis**: Acessar `http://localhost:8765`, fazer login com o perfil `admin` e testar a criação/acesso de um perfil `reader`.
- [ ] **6. Fontes de biblioteca e Seletor de Diretórios**: No modal **Fontes**, clicar em **Selecionar pasta…**, navegar até uma pasta com PDFs (ou testar o botão **Janela do Sistema** localmente no XFCE), adicionar a fonte e clicar em **Escanear**.
- [ ] **7. Sistema de Capas**: Confirmar a exibição das capas na biblioteca, testar o envio de uma capa personalizada no botão **Capa** de um livro e depois testar a restauração da capa automática.
- [ ] **8. Leitura, Progresso e Estudos**: Abrir um PDF no leitor, navegar por páginas em Página Única e Rolagem Contínua, criar um destaque colorido, uma nota e um traço de desenho livre, apagar o traço com a borracha, fechar o leitor e reabrir para confirmar que a página atual e as anotações foram preservadas.
- [ ] **9. Download e Exportação**: Testar o botão de download do PDF original e a exportação do caderno de estudos em Markdown (`.md`) e JSON (`.json`).
- [ ] **10. Backup, Restauração e Reinício (Reboot)**: Executar `./deployment/backup.sh`, reiniciar o Positivo C1260 (`sudo reboot`) e, após o boot, acessar `http://<IP-LAN>:8765` a partir de outro dispositivo da casa para confirmar que o Lectoria subiu sozinho com todos os dados intactos.

---

## 10. Modelo de Segurança, Permissões e Acesso em Rede

### 10.1 Autenticação, Sessões e Proteção CSRF
O Lectoria conta com uma camada robusta de proteção em nível de aplicação:
- **Hashing de Senhas**: Utiliza `hashlib.scrypt` com salt individual de 16 bytes e parâmetros de derivação fortes (`N=16384, r=8, p=1`).
- **Hashing SHA-256 de Sessões**: Tokens de sessão são armazenados no banco de dados na forma de hash SHA-256 (`token_hash`), impedindo o sequestro de contas mesmo em caso de acesso físico ao arquivo de banco de dados ou backups.
- **Revogação Imediata**: Sessões ativas são automaticamente destruídas na troca de senha, na desativação de conta ou na exclusão do usuário.
- **Proteção Completa contra CSRF (Cross-Site Request Forgery)**:
  - Validação estrita de correspondência de `Origin` e `Referer` contra o cabeçalho `Host` em todas as rotas mutantes (`POST`, `PUT`, `DELETE`, `PATCH`).
  - Padrão *Double Submit Cookie* via cookie `lectoria_csrf` e injeção do cabeçalho `X-CSRF-Token` pelo frontend Vanilla JavaScript.
  - Compatibilidade com `emergencySave` em fechamento de página (`sendBeacon`) através de validação via parâmetro de consulta (`?csrf_token=...`).
- **Rate-Limiting em Memória**: Bloqueio temporário automático de 5 minutos contra tentativas sucessivas e contínuas de força bruta no login.
- **Controle de Acesso Baseado em Papéis (RBAC)**: Rotas de fontes, usuários e backups são restritas estritamente a administradores autenticados.
- **Sanitização de Informações**: O endpoint `/api/health` retorna exclusivamente o status do serviço sem expor a topologia de diretórios do servidor.

### 10.2 Permissões de Sistema de Arquivos (Menor Privilégio)
Para garantir a máxima segurança operacional no Linux Mint:
- **Diretórios com Permissão Apenas de Leitura (`r` / `rx`)**:
  - `books/` (e qualquer diretório cadastrado como fonte de biblioteca): O Lectoria opera sobre os arquivos PDF exclusivamente em modo de leitura. Arquivos físicos **nunca** são modificados, renomeados ou excluídos pelo servidor.
  - Diretórios de código (`main.py`, `static/`, `.venv/`).
- **Diretórios com Permissão de Escrita (`rw` / `rwx`)**:
  - `data/`: Armazenamento do banco SQLite (`lectoria.db`) e seus arquivos de journal (`lectoria.db-wal`, `lectoria.db-shm`).
  - `backups/`: Destino exclusivo para os arquivos de cópias de segurança gerados via API ou CLI.

### 10.3 Recomendações para Acesso em Rede Local e Externa
- **Rede Local (LAN)**: Por padrão, o Lectoria escuta na interface `0.0.0.0:8765`, permitindo acesso instantâneo por outros dispositivos da rede Wi-Fi/Ethernet doméstica (computadores, tablets e celulares).
- **Tráfego HTTP sem TLS/HTTPS**: Na rede local doméstica direta, o tráfego HTTP ocorre em texto claro. Caso haja necessidade de proteção contra tráfego não criptografado na rede interna ou acesso via internet:
  - **Opção Recomendada (Rede Mesh / VPN Pessoal)**: Utilize ferramentas seguras como **Tailscale** ou **WireGuard**. Isso permite acessar o servidor remotamente criptografado ponto-a-ponto sem abrir portas no roteador doméstico.
  - **Opção Alternativa (Reverse Proxy com TLS)**: Utilize um servidor reverso como Caddy ou Nginx com certificado Let's Encrypt (HTTPS) redirecionando para a porta `8765`.
- **Aviso**: **Nunca exponha a porta `8765` diretamente à Internet aberta** sem HTTPS e autenticação perimetral.

---

## 11. Atualização de uma Instalação Existente

Para atualizar o Lectoria para a versão mais recente em seu servidor doméstico:

1. **Acessar o diretório da aplicação e parar o serviço**:
   ```bash
   cd "/home/vini/Jellyfin livros"
   sudo systemctl stop lectoria.service
   ```

2. **Gerar um backup de segurança preventivo**:
   ```bash
   .venv/bin/python scripts/backup.py --backup
   ```

3. **Atualizar o repositório Git**:
   ```bash
   git pull origin main
   ```

4. **Atualizar as dependências no ambiente virtual (se houver)**:
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

5. **Reiniciar o serviço systemd**:
   ```bash
   sudo systemctl start lectoria.service
   sudo systemctl status lectoria.service
   ```

---

## 12. Atalhos de Teclado no Leitor

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

## 13. Execução dos Testes Automatizados

Com o ambiente virtual ativado:

```bash
source .venv/bin/activate
pytest -v tests/
```

Resultado atual: **77 testes aprovados (100% de sucesso)** cobrindo:
- Catálogo, scanner e metadados (`test_library.py`).
- Coleções e associações em lote (`test_collections.py`, `test_iteration2_features.py`).
- Filtros de estado de leitura, busca e ordenação (`test_filters_and_sorting.py`).
- Progresso de leitura e Beacon API (`test_progress.py`).
- Streaming por chunks HTTP Range e path traversal (`test_security.py`).
- Destaques, notas, desenhos vetoriais e algoritmo geométrico da borracha (`test_annotations.py`).
- Exportação Markdown/JSON, preview de importação e rollback (`test_iteration4.py`).
- Limites de DoS na importação, validação de profundidade JSON, concorrência de backups e retenção (`test_iteration5.py`).
- Download de PDFs originais, gerenciamento de fontes, symlinks, scrypt, rate limiting, controle de papéis e isolamento de dados por usuário (`test_iteration6.py`).
- Hashing de sessões, invalidação de sessões, CSRF Double Submit, Origin check, IDOR em anotações, probing de coleções, proteção de fontes (.venv, .git) e integridade em restauração multiusuário (`test_iteration7.py`).
- Extração de capas de PDF, cache em disco, invalidação por hash, upload/substituição/remoção de capas personalizadas, bloqueio de uploads maliciosos/acima do limite, fallback SVG editorial, navegador visual de diretórios e seletor nativo local (`test_iteration8.py`).
- Carregamento e precedência de `.env`, reconciliação automática do caminho da biblioteca principal entre máquinas, backup preventivo e preservação total de dados na migração de schema V3->V5, preservação de capas personalizadas no backup/restauração e validação dos scripts de deploy e diagnóstico (`test_iteration9.py`).

---

## 14. Desempenho e Limitações Conhecidas

### 14.1 Medições Reais e Perfil de Consumo
- **Consumo de Memória RAM (RSS medido via `scripts/diagnose.py`)**: **~54.5 MB a 62 MB** em processo único Uvicorn/FastAPI.
- **Latência de Listagem SQLite (WAL)**: **~0.5 ms**.
- **Consumo de CPU em Repouso**: **0.0%** (zero file watchers ou tarefas em background).
- **Coexistência com o Jellyfin no Positivo C1260 (Intel Celeron 847 1.5 GHz / 8 GB RAM / SSD)**: O limite `MemoryMax=512M` e `CPUWeight=100` no `systemd`, somado ao streaming por chunks sob demanda (HTTP Range) e à leitura parcial do cabeçalho PDF no scanner/extrator de capas, protege o servidor contra picos de memória e I/O. Para aferir os números exatos no Positivo C1260 após o deploy, basta executar `./deployment/diagnose.sh`.

### 14.2 Limitações Conhecidas da Versão 1.1.1
- **Suporte Exclusivo a PDFs**: Formatos como EPUB, MOBI ou CBZ não são suportados nativamente nesta versão.
- **Varredura Sob Demanda**: Para não degradar a CPU nem gastar ciclos com sentinelas contínuos de disco (`inotify`), novas adições de arquivos nas pastas de fontes são indexadas sob demanda ao clicar no botão "Escanear" na interface web ou via API.
