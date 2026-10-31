# Lectoria — Servidor Pessoal de Livros Digitais

Servidor pessoal de leitura focado exclusivamente em documentos PDF, projetado para operar com mínimo consumo de CPU, RAM e I/O de disco em hardware modesto (como computadores antigos com processador Intel Celeron, operando concomitantemente com o Jellyfin).

---

## 1. Novidades da Iteração 4: Estudos, Navegação e Confiabilidade

- **Caderno de Estudos Aprimorado**:
  - Agrupamento inteligente das anotações por página (`Página X`).
  - Diferenciação visual clara entre *Destaques* (com citação e cor), *Notas* (com texto e citações vinculadas) e *Desenhos Livres* (com indicação vetorial).
  - **Busca Instantânea Local**: pesquisa em tempo real no documento por texto de citações, notas e páginas.
  - Filtros dinâmicos: *Todas*, *Destaques*, *Notas* e *Desenhos*.
  - Exibição de carimbos de data/hora (`dd/mm/aaaa hh:mm`) de criação e última edição.
  - Salto imediato para a página ou trecho ao clicar em qualquer registro.
- **Exportação de Anotações**:
  - **Markdown (`.md`)**: relatório editorial formatado com sumário, metadados do documento (título, autor, arquivo), destaques em citação (`>`), notas associadas e anotações avulsas da página.
  - **JSON (`.json`)**: exportação completa estruturada preservando coordenadas normalizadas, retângulos, cores, notas e traços vetoriais SVG para portabilidade total.
- **Importação Confiável de Anotações**:
  - Validação estrita de formato e schema JSON.
  - **Detecção e Prevenção de Duplicatas**: identifica marcações idênticas já existentes e importa apenas novidades.
  - **Pré-visualização (Preview Modal)**: apresenta resumo detalhado (novos destaques, notas, desenhos e duplicatas ignoradas) antes de qualquer gravação.
  - **Transação Atômica no SQLite**: garantia de integridade com *rollback* imediato em caso de qualquer inconsistência.
- **Navegação Aprimorada no Leitor**:
  - **Barra de Progresso Visual**: indicador sutil de progresso percentual no topo da janela.
  - **Voltar à Última Página**: botão e atalho (`b`) para retornar instantaneamente à página visitada anteriormente.
  - **Miniaturas de Páginas com Lazy Loading**: gaveta lateral com miniaturas geradas sob demanda via `IntersectionObserver` em baixa resolução, com cache em memória (zero re-renderização desnecessária).
  - **Badges de Anotações**: indicadores visuais nas miniaturas sinalizando quais páginas possuem destaques, notas ou desenhos.
  - **Janela de Atalhos de Teclado**: modal de consulta rápida (`?` ou botão no cabeçalho).
- **Sistema de Backup Seguro e Auditoria**:
  - Módulo nativo `backup_service.py` utilizando a **SQLite Online Backup API**, garantindo cópias consistentes mesmo com o banco aberto e em modo WAL.
  - Utilitário de linha de comando `scripts/backup.py` para geração, listagem, verificação e restauração segura.
  - Armazenamento em `backups/` estritamente ignorado pelo Git, mantendo o histórico de código e o banco principal `data/lectoria.db` protegidos.

---

## 2. Controle de Versão Git e Backup dos Dados

O Lectoria conta com um repositório Git estritamente **local** para histórico de alterações e preservação do banco de dados da aplicação.

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

### 2.2 Sistema de Backup e Procedimento de Restauração

O Lectoria dispõe de um utilitário dedicado (`scripts/backup.py`) e de um módulo interno (`backup_service.py`) que utiliza a **SQLite Online Backup API**. Isso possibilita gerar snapshots 100% consistentes do banco de dados mesmo durante execuções ativas ou em modo WAL, armazenando-os em `backups/` (ignorado pelo versionamento Git).

#### Como gerar um backup:
```bash
python3 scripts/backup.py --backup
```
*Gera uma cópia íntegra em `backups/lectoria_backup_AAAAMMDD_HHMMSS.db` e valida sua consistência (`PRAGMA integrity_check`).*

#### Como listar backups existentes:
```bash
python3 scripts/backup.py --list
```

#### Como verificar a integridade da base atual:
```bash
python3 scripts/backup.py --verify
```

---

### 2.3 Procedimento Oficial de Restauração Passo a Passo

Caso seja necessário restaurar os dados a partir de um backup anterior, siga estritamente o procedimento:

1. **Parar o servidor Lectoria**:
   Pressione `Ctrl + C` no terminal onde o servidor está em execução, ou encerre o serviço do sistema.
   *(Isso garante que nenhuma escrita concorrente ocorra durante a restauração).*

2. **Identificar o arquivo de backup desejado**:
   ```bash
   python3 scripts/backup.py --list
   ```
   Localize o nome do arquivo, por exemplo: `lectoria_backup_20261031_185428.db`.

3. **Restaurar os dados**:
   Execute o utilitário informando o arquivo escolhido:
   ```bash
   python3 scripts/backup.py --restore lectoria_backup_20261031_185428.db
   ```
   O script solicitará confirmação explícita (`s/N`) para impedir substituições acidentais. Ele realiza:
   - Validação prévia de integridade do arquivo de backup;
   - Cópia segura para `data/lectoria.db`;
   - Limpeza de arquivos residuais órfãos do WAL (`lectoria.db-wal` e `lectoria.db-shm`).

4. **Verificar a integridade do banco restaurado**:
   ```bash
   python3 scripts/backup.py --verify
   ```
   Deverá retornar `[OK] Banco de dados íntegro e sem erros de consistência`.

5. **Iniciar novamente o servidor**:
   ```bash
   source .venv/bin/activate
   python3 main.py
   ```

> [!IMPORTANT]
> **Aviso de Segurança e Backup**:
> O repositório Git local e a pasta `backups/` protegem contra alterações e exclusões acidentais. No entanto, **o Git local NÃO substitui uma rotina de backup externo** (como cópia da pasta `data/` ou `backups/` para um pendrive ou armazenamento em nuvem pessoal) em caso de pane física no disco SSD ou no computador Positivo C1260.

---

## 3. Arquitetura e Decisões de Design

- **Backend**: Python 3 com FastAPI e Uvicorn em processo único (`workers=1`), sem threads em background ou workers concorrentes desnecessários.
- **Banco de Dados**: SQLite3 em modo **WAL** (`Write-Ahead Logging`), com `synchronous=NORMAL` e controle de migrações (`schema_migrations`), garantindo persistência rápida, sem bloqueios de leitura e com baixo desgaste de disco/SSD.
  - **Schema V1**: tabelas `users`, `books` e `reading_progress`.
  - **Schema V2**: tabelas `collections` e `collection_books`.
  - **Schema V3**: tabelas `highlights`, `notes` e `page_drawings`.
- **Frontend**: HTML5 semântico, CSS3 enxuto e JavaScript Vanilla (ES6). Zero frameworks pesados (sem React, Vue, Angular ou Node.js).
- **Leitor PDF**: Baseado em **PDF.js** estático local (`static/vendor/pdfjs/`). A renderização ocorre integralmente no navegador do cliente, sem requisições a CDNs externas e sem telemetria.
- **Streaming de Arquivos**: Suporte nativo a **HTTP Range Requests (206 Partial Content)**. O servidor serve blocos sob demanda, permitindo ao PDF.js abrir documentos grandes sem carregar o arquivo PDF inteiro na memória RAM do servidor.
- **Segurança**: Prevenção estrita contra *Path Traversal* com resolução canônica de caminhos (`pathlib.Path.resolve()`). Identificadores UUID estáveis em vez de expor caminhos do sistema operacional nas rotas públicas.
- **Zero Ociosidade Forçada**: A aplicação consome 0% de CPU quando ociosa (sem file watchers contínuos com `inotify`). O escaneamento é acionado sob demanda.

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
├── export_service.py           # Serviço de exportação (.md, .json) e importação transacional
├── backup_service.py           # Serviço de backup pontual com SQLite Online Backup API
├── library.py                  # Scanner leve (pypdf), filtros, busca, ordenação e segurança
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências Python mínimas fixadas
├── README.md                   # Documentação completa do projeto
├── scripts/
│   └── backup.py               # Utilitário CLI para backup e restauração manual
├── backups/                    # Armazenamento de backups manuais do banco (ignorado no Git)
├── books/                      # Diretório local para os arquivos PDF (mantido intacto)
├── data/                       # Banco de dados persistente versionado (lectoria.db)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (Tema Claro/Escuro, Coleções, Filtros, Backup)
│   ├── reader.html             # Interface do Leitor (Anotações, Marcações, Desenho, Miniaturas e Modais)
│   ├── css/
│   │   └── style.css           # Estilos e temas claro/escuro
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca (busca, coleções, filtros, ordenação)
│   │   └── reader.js           # Lógica do leitor (PDF.js, texto, marca-texto, SVG draw, drawer, miniaturas)
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos offline
└── tests/                      # Suíte de testes automatizados
    ├── conftest.py             # Fixtures e isolamento de ambiente
    ├── test_annotations.py     # Testes de destaques, notas e desenhos vetoriais
    ├── test_iteration4.py      # Testes de exportação MD/JSON, importação transacional e backups
    ├── test_library.py         # Testes de catálogo, metadados e busca
    ├── test_collections.py     # Testes de CRUD e associação de coleções
    ├── test_filters_and_sorting.py # Testes de filtros de status, ordenação e concorrência
    ├── test_iteration2_features.py # Testes de coleções em lote, validações e PDFs grandes
    ├── test_progress.py        # Testes de salvamento automático de leitura e beacon
    └── test_security.py        # Testes de Range requests, path traversal e integridade
```

---

## 5. Instalação e Execução (Linux Mint / Debian)

### 5.1 Ativar Ambiente Virtual e Dependências

```bash
cd "/home/vini/Jellyfin livros"
source .venv/bin/activate
pip install -r requirements.txt
```

### 5.2 Iniciar o Servidor

```bash
source .venv/bin/activate
python3 main.py
```

O servidor iniciará escutando em `http://0.0.0.0:8765`.

> **Nota de Compatibilidade com o Jellyfin**: A porta padrão do Lectoria é `8765`, não interferindo com a porta `8096` nem `8920` do Jellyfin.

---

## 6. Atalhos de Teclado no Leitor

| Tecla | Ação |
| :--- | :--- |
| `Seta Esquerda` / `PageUp` | Página anterior |
| `Seta Direita` / `PageDown` / `Espaço` | Próxima página |
| `b` | Voltar à última página lida / visitada |
| `+` ou `=` | Aumentar zoom |
| `-` | Diminuir zoom |
| `w` | Ajustar à largura da tela (*Fit Width*) |
| `p` | Ajustar à página inteira (*Fit Page*) |
| `r` | Modo Leitura |
| `h` | Modo Marca-texto |
| `d` | Modo Desenho Livre |
| `a` | Abrir / fechar Caderno de Estudos |
| `m` | Abrir / fechar Miniaturas de Páginas |
| `s` | Alternar entre Página Única e Rolagem Contínua |
| `t` | Alternar Tema Claro / Escuro |
| `?` | Abrir janela de ajuda de atalhos de teclado |
| `Esc` | Fechar modais, painéis laterais ou menus flutuantes |

---

## 7. Acesso por Outros Dispositivos na Rede Local

Como o servidor escuta por padrão em `0.0.0.0`:

1. Descubra o IP local do servidor Linux:
   ```bash
   hostname -I
   ```
2. Em qualquer celular, tablet ou computador conectado ao mesmo Wi-Fi, abra o navegador e acesse:
   ```text
   http://<IP-DO-SERVIDOR>:8765
   ```

---

## 8. Execução dos Testes Automatizados

Com o ambiente virtual ativado:

```bash
source .venv/bin/activate
pytest -v tests/
```

Todos os **33 testes** cobrem os fluxos de leitura, coleções, migrações V1, V2 e V3, destaques, anotações, desenhos livres, exportação Markdown/JSON, importação com prevenção de duplicatas e rollback, backups SQLite, integridade e segurança.

---

## 9. Medições Reais de Consumo no Hardware

Medições reais realizadas no processo do Lectoria em execução:

```bash
ps -o pid,ppid,%cpu,%mem,rss,cmd -p $(pgrep -f "main.py")
```

- **Consumo de Memória RAM**: **~57 MB a 60 MB** (RSS total).
- **Consumo de CPU em Repouso**: **0.0%**.
- **Impacto no Celeron 847**: Desprezível em repouso. O streaming por chunks sob demanda (HTTP Range) evita picos de leitura e o descarregamento de páginas na rolagem contínua previne estouro de memória no navegador.
