# Lectoria — Servidor Pessoal de Livros Digitais

Servidor pessoal de leitura focado exclusivamente em documentos PDF, projetado para operar com mínimo consumo de CPU, RAM e I/O de disco em hardware modesto (como computadores antigos com processador Intel Celeron, operando concomitantemente com o Jellyfin).

---

## 1. Novidades da Iteração 2

- **Coleções e Categorias Personalizadas**:
  - Criação, renomeação e exclusão de coleções sem apagar documentos físicos.
  - Associação de um PDF a múltiplas coleções com gerenciamento em lote.
  - Filtro instantâneo por coleção na biblioteca.
- **Filtros de Leitura e Ordenação Avançada**:
  - Abas de status: *Todos os livros*, *Em andamento*, *Não iniciados* e *Concluídos*.
  - Ordenação por: Título (A-Z, Z-A), Autor (A-Z, Z-A), Data de adição (Recém-adicionados), Última leitura e Mais lidos.
  - Busca rápida combinada por título e autor sem indexadores pesados.
- **Aprimoramentos do Leitor PDF**:
  - **Modo Página Única**: renderização rápida e nítida com foco na página atual.
  - **Modo Rolagem Contínua**: fluxo vertical contínuo com *lazy rendering* virtualizado (utilizando `IntersectionObserver`), renderizando apenas as páginas visíveis e descartando bitmaps distantes para poupar a memória RAM do computador modesto.
  - **Controles de Zoom**: Aumentar (+), Diminuir (-), Ajustar à Largura (w) e Ajustar à Página (p).
  - **Atalhos de Teclado**: navegação e zoom ergonômicos pelo teclado.
  - **Interface Responsiva**: adaptação completa para toques em celulares e tablets.
- **Identidade Visual Refinada**:
  - Suporte completo a **Tema Claro** e **Tema Escuro** com persistência no navegador.
  - Capas editoriais procedurais baseadas no título do documento com paleta harmônica e relevo gráfico.
  - Exibição de metadados completos: tamanho do arquivo, número de páginas, data de adição, última leitura e badges de coleções.

---

## 2. Arquitetura e Decisões de Design

- **Backend**: Python 3 com FastAPI e Uvicorn em processo único (`workers=1`), sem threads em background ou workers concorrentes desnecessários.
- **Banco de Dados**: SQLite3 em modo **WAL** (`Write-Ahead Logging`), com `synchronous=NORMAL` e controle de migrações (`schema_migrations`), garantindo persistência rápida, sem bloqueios de leitura e com baixo desgaste de disco/SSD.
  - **Schema V1**: tabelas `users`, `books` e `reading_progress`.
  - **Schema V2**: adição de `collections` e `collection_books` sem afetar registros existentes.
- **Frontend**: HTML5 semântico, CSS3 enxuto e JavaScript Vanilla (ES6). Zero frameworks pesados (sem React, Vue, Angular ou Node.js).
- **Leitor PDF**: Baseado em **PDF.js** estático local (`static/vendor/pdfjs/`). A renderização ocorre integralmente no navegador do cliente, sem requisições a CDNs externas e sem telemetria.
- **Streaming de Arquivos**: Suporte nativo a **HTTP Range Requests (206 Partial Content)**. O servidor serve blocos sob demanda, permitindo ao PDF.js abrir documentos grandes sem carregar o arquivo PDF inteiro na memória RAM do servidor.
- **Segurança**: Prevenção estrita contra *Path Traversal* com resolução canônica de caminhos (`pathlib.Path.resolve()`). Identificadores UUID estáveis em vez de expor caminhos do sistema operacional nas rotas públicas.
- **Zero Ociosidade Forçada**: A aplicação consome 0% de CPU quando ociosa (sem file watchers contínuos com `inotify`). O escaneamento é acionado sob demanda.

---

## 3. Estrutura do Projeto

```text
/home/vini/Jellyfin livros/
├── .venv/                      # Ambiente virtual Python
├── config.py                   # Configurações centralizadas (diretórios, porta, host)
├── database.py                 # Conexão SQLite (WAL mode) e migrações V1 e V2
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── library.py                  # Scanner leve (pypdf), filtros, busca, ordenação e segurança
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências Python mínimas fixadas
├── README.md                   # Documentação do projeto
├── books/                      # Diretório padrão para os arquivos PDF
├── data/                       # Armazenamento do banco de dados (lectoria.db)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (Tema Claro/Escuro, Coleções, Filtros)
│   ├── reader.html             # Interface do Leitor de PDF (Página Única / Rolagem Contínua)
│   ├── css/
│   │   └── style.css           # Estilos e temas claro/escuro
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca (busca, coleções, filtros, ordenação)
│   │   └── reader.js           # Lógica do leitor (PDF.js, zoom, modos de visão, auto-save)
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos offline
└── tests/                      # Suíte de testes automatizados
    ├── conftest.py             # Fixtures e isolamento de ambiente
    ├── test_library.py         # Testes de catálogo, metadados e busca
    ├── test_collections.py     # Testes de CRUD e associação de coleções
    ├── test_filters_and_sorting.py # Testes de filtros de status, ordenação e concorrência
    ├── test_iteration2_features.py # Testes de coleções em lote, validações e PDFs grandes
    ├── test_progress.py        # Testes de salvamento automático de leitura e beacon
    └── test_security.py        # Testes de Range requests, path traversal e integridade
```

---

## 4. Instalação e Execução (Linux Mint / Debian)

### 4.1 Criar o Ambiente Virtual e Instalar Dependências

Abra um terminal no diretório do projeto:

```bash
cd "/home/vini/Jellyfin livros"

# Ativar ambiente virtual
source .venv/bin/activate

# Instalar dependências (caso não tenham sido instaladas)
pip install -r requirements.txt
```

### 4.2 Iniciar o Servidor

```bash
source .venv/bin/activate
python3 main.py
```

O servidor iniciará escutando em `http://0.0.0.0:8765`.

> **Nota de Compatibilidade com o Jellyfin**: A porta padrão do Lectoria é `8765`, não interferindo com a porta `8096` nem `8920` do Jellyfin.

---

## 5. Atalhos de Teclado no Leitor

| Tecla | Ação |
| :--- | :--- |
| `Seta Esquerda` / `PageUp` | Página anterior |
| `Seta Direita` / `PageDown` / `Espaço` | Próxima página |
| `+` ou `=` | Aumentar zoom |
| `-` | Diminuir zoom |
| `w` | Ajustar à largura da tela (*Fit Width*) |
| `p` | Ajustar à página inteira (*Fit Page*) |
| `Esc` | Fechar modais da biblioteca |

---

## 6. Acesso por Outros Dispositivos na Rede Local

Como o servidor escuta por padrão em `0.0.0.0`:

1. Descubra o IP local do servidor Linux:
   ```bash
   hostname -I
   ```
   *(Exemplo: `192.168.1.15`)*

2. Em qualquer celular, tablet ou computador conectado ao mesmo Wi-Fi, abra o navegador e acesse:
   ```text
   http://192.168.1.15:8765
   ```

---

## 7. Como Executar os Testes Automatizados

Com o ambiente virtual ativado:

```bash
source .venv/bin/activate
pytest -v tests/
```

Todos os **18 testes** cobrem os fluxos de leitura, coleções, migrações, filtros, ordenação, integridade de arquivos, concorrência e segurança.

---

## 8. Medições Reais de Consumo no Hardware

Medições reais realizadas no processo do Lectoria em execução:

```bash
ps -o pid,ppid,%cpu,%mem,rss,cmd -p $(pgrep -f "main.py")
```

- **Consumo de Memória RAM**: **~57 MB a 59 MB** (RSS total).
- **Consumo de CPU em Repouso**: **0.0%**.
- **Impacto no Celeron 847**: Nulo em repouso. O streaming por chunks sob demanda (HTTP Range) evita picos de leitura e o descarregamento de páginas na rolagem contínua previne estouro de memória no navegador.
