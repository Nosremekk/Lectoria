# Lectoria — Servidor Pessoal de Livros Digitais (v1.1.0)

O **Lectoria** é um servidor pessoal de leitura focado em documentos PDF, projetado especificamente para operar com consumo mínimo de CPU, memória RAM e I/O de disco em hardware modesto (como computadores antigos com processador Intel Celeron, operando concomitantemente com o Jellyfin).

---

## 1. Principais Recursos da Versão 1.1

- **Biblioteca Pessoal sem Telemetria e com Múltiplas Fontes**:
  - Suporte a **múltiplas fontes e diretórios locais configuráveis**, permitindo organizar acervos em diferentes discos ou pastas.
  - Varredura sob demanda (*on-demand scanner*) com extração rápida de metadados (`pypdf`), sem modificação dos arquivos originais.
  - Garantia estrita de preservação: remoção ou desativação de fontes **nunca apaga arquivos físicos do disco**.
  - Capas editoriais procedurais em CSS/SVG com tipografia adaptativa baseada no título.
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
- **Frontend**: HTML5 semântico, CSS3 enxuto e JavaScript Vanilla (ES6). Zero frameworks pesados (sem React, Vue, Angular ou Node.js).
- **Leitor PDF**: Baseado em **PDF.js** estático local (`static/vendor/pdfjs/`). A renderização ocorre integralmente no navegador do cliente, sem requisições a CDNs externas e sem telemetria.
- **Streaming de Arquivos**: Suporte nativo a **HTTP Range Requests (206 Partial Content)**. O servidor serve blocos sob demanda, permitindo abrir documentos grandes sem carregar o arquivo PDF inteiro na memória RAM do servidor.
- **Segurança de Caminhos**: Prevenção estrita contra *Path Traversal* com resolução canônica de caminhos (`pathlib.Path.resolve()`). Identificadores UUID estáveis em vez de expor caminhos do sistema operacional nas rotas públicas.
- **Zero Ociosidade Forçada**: A aplicação consome 0% de CPU quando ociosa (sem file watchers contínuos com `inotify`). O escaneamento da biblioteca é acionado sob demanda pelo usuário.

---

## 4. Estrutura do Projeto

```text
/home/vini/Jellyfin livros/
├── .gitignore                  # Regras do Git (inclui data/ e ignora books/*.pdf e backups/)
├── .venv/                      # Ambiente virtual Python
├── config.py                   # Configurações centralizadas (diretórios, porta, host)
├── database.py                 # Conexão SQLite (WAL mode) e migrações V1, V2 e V3
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── annotations_service.py      # Serviço de marcações, anotações e desenhos livres
├── export_service.py           # Serviço de exportação (.md, .json) e importação segura
├── backup_service.py           # Serviço de backup SQLite com Lock e integridade
├── library.py                  # Scanner leve (pypdf), filtros, busca, ordenação e segurança
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências Python mínimas fixadas
├── CHANGELOG.md                # Histórico de alterações e versões
├── README.md                   # Documentação completa do projeto
├── deployment/                 # Arquivos de implantação em produção
│   ├── lectoria.service        # Unidade systemd com sandboxing e limites cgroup
│   └── README.md               # Guia de implantação systemd
├── scripts/
│   ├── backup.py               # Utilitário CLI para backup, restauração e retenção
│   └── manage_users.py         # Utilitário CLI para gestão de usuários, senhas e papéis
├── backups/                    # Armazenamento de backups manuais do banco (ignorado no Git)
├── books/                      # Diretório local padrão para arquivos PDF (mantido intacto)
├── data/                       # Banco de dados persistente versionado (lectoria.db)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (autenticação, fontes, usuários)
│   ├── reader.html             # Interface do Leitor (PDF.js, Estudos, Miniaturas, Download)
│   ├── css/
│   │   └── style.css           # Estilos e temas claro/escuro
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca, autenticação e painéis de administração
│   │   └── reader.js           # Lógica do leitor e anotações
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos offline
└── tests/                      # Suíte de testes automatizados (52 testes)
    ├── conftest.py             # Fixtures e isolamento de ambiente
    ├── test_annotations.py     # Testes de destaques, notas e desenhos vetoriais
    ├── test_collections.py     # Testes de CRUD e associação de coleções
    ├── test_filters_and_sorting.py # Testes de filtros de status e ordenação
    ├── test_iteration2_features.py # Testes de coleções em lote e PDFs grandes
    ├── test_iteration4.py      # Testes de exportação MD/JSON e backups
    ├── test_iteration5.py      # Testes de limites DoS, concorrência e segurança
    ├── test_iteration6.py      # Testes de downloads, múltiplas fontes, auth e isolamento
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

### 6.1 Adicionando e Gerenciando Fontes
Pela interface web (acessível no menu **Fontes**, restrito a administradores):
1. Informe um rótulo amigável (ex: *Literatura Geral*) e o caminho absoluto ou relativo no servidor.
2. O sistema realiza varredura recursiva sob demanda à procura de arquivos `.pdf`.
3. É possível ativar, desativar temporariamente ou escanear fontes individualmente.

> [!IMPORTANT]
> **Preservação de Arquivos Físicos**:
> Remover uma fonte no Lectoria desvincula seus livros do catálogo da aplicação, mas **NUNCA apaga ou altera nenhum arquivo PDF físico do disco**.

### 6.2 Regras Estritas de Segurança de Fontes
Para proteção total do sistema operacional hospedeiro:
- Diretórios reservados do sistema (`/`, `/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/proc`, `/root`, `/sys`, `/usr`) são **rigorosamente bloqueados**.
- Pastas internas da aplicação (`data/`, `backups/`, diretório raiz do projeto) são **rejeitadas**.
- Links simbólicos (*symlinks*) são resolvidos canonicamente; se um link apontar para fora do diretório da fonte autorizada, ele é descartado silenciosamente para impedir vazamento de arquivos.

---

## 7. Download Direto de Documentos PDF

- **Na Biblioteca**: Cada card de livro (nas visualizações em Grade e em Lista) conta com a ação **Baixar**, iniciando o download direto do arquivo original no dispositivo do usuário.
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

## 9. Implantação em Produção com systemd (Linux Mint / Debian)

Para garantir que o Lectoria inicialize automaticamente junto com o computador e se recupere caso ocorra qualquer imprevisto, configure o serviço systemd:

1. **Copiar o arquivo de serviço para o diretório do systemd**:
   ```bash
   sudo cp deployment/lectoria.service /etc/systemd/system/lectoria.service
   ```

2. **Recarregar a configuração do systemd**:
   ```bash
   sudo systemctl daemon-reload
   ```

3. **Habilitar a inicialização automática no boot do sistema**:
   ```bash
   sudo systemctl enable lectoria.service
   ```

4. **Iniciar o serviço imediatamente**:
   ```bash
   sudo systemctl start lectoria.service
   ```

5. **Verificar o status e logs**:
   ```bash
   sudo systemctl status lectoria.service
   sudo journalctl -u lectoria.service -f
   ```

---

## 10. Modelo de Segurança e Acesso em Rede

### 10.1 Autenticação e Sessões Seguras
O Lectoria conta com camada completa de autenticação integrada:
- **Hashing de Senhas**: Utiliza `scrypt` com parâmetros robustos e salt criptográfico individual.
- **Sessões HTTP**: Cookies seguros com token opaco aleatório de 256 bits, `HttpOnly` e `SameSite=Lax`.
- **Rate-Limiting**: Bloqueio temporário automático de 5 minutos contra tentativas excessivas e contínuas de adivinhação de senhas.
- **Controle de Acesso Baseado em Papéis (RBAC)**: Rotas de fontes, usuários e backups são estritamente autorizadas no servidor apenas para usuários administradores.

### 10.2 Recomendações para Acesso Externo (WAN / Internet)
> [!WARNING]
> **Nunca exponha a porta `8765` diretamente à Internet** sem criptografia de transporte (HTTPS / TLS).

Se desejar acessar seus livros fora de casa:
1. **Opção Recomendada (Rede Mesh / VPN Pessoal)**: Utilize ferramentas seguras como **Tailscale** ou **WireGuard**. Isso permite acessar o servidor remotamente criptografado ponto-a-ponto sem abrir portas no roteador doméstico.
2. **Opção Alternativa (Reverse Proxy com TLS)**: Utilize um servidor reverso como Caddy ou Nginx com certificado Let's Encrypt (HTTPS) redirecionando para a porta `8765`.

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

Com o ambiente virtual ativado:

```bash
source .venv/bin/activate
pytest -v tests/
```

Resultado atual: **52 testes aprovados (100% de sucesso)** cobrindo:
- Catálogo, scanner e metadados (`test_library.py`).
- Coleções e associações em lote (`test_collections.py`, `test_iteration2_features.py`).
- Filtros de estado de leitura, busca e ordenação (`test_filters_and_sorting.py`).
- Progresso de leitura e Beacon API (`test_progress.py`).
- Streaming por chunks HTTP Range e path traversal (`test_security.py`).
- Destaques, notas, desenhos vetoriais e algoritmo geométrico da borracha (`test_annotations.py`).
- Exportação Markdown/JSON, preview de importação e rollback (`test_iteration4.py`).
- Limites de DoS na importação, validação de profundidade JSON, concorrência de backups e retenção (`test_iteration5.py`).
- Download de PDFs originais, gerenciamento de fontes, symlinks, scrypt, rate limiting, controle de papéis e isolamento de dados por usuário (`test_iteration6.py`).

---

## 13. Desempenho Real e Limitações Conhecidas

### 13.1 Medições Reais no Hardware Alvo (Intel Celeron 847 1.5 GHz / 8 GB RAM / SSD)
- **Consumo de Memória RAM**: **~58 MB a 62 MB** (RSS total em repouso).
- **Consumo de CPU em Repouso**: **0.0%**.
- **Impacto no Jellyfin**: Desprezível. O streaming por chunks sob demanda (HTTP Range) evita picos de leitura e o descarregamento dinâmico de páginas fora do viewport na rolagem contínua previne saturação do navegador do cliente.

### 13.2 Limitações Conhecidas da Versão 1.1
- **Suporte Exclusivo a PDFs**: Formatos como EPUB, MOBI ou CBZ não são suportados nativamente nesta versão.
- **Varredura Sob Demanda**: Para não degradar a CPU nem gastar ciclos com sentinelas contínuos de disco (`inotify`), novas adições de arquivos nas pastas de fontes são indexadas sob demanda ao clicar no botão "Escanear" na interface web ou via API.
