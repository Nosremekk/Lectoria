# Lectoria — Servidor Pessoal de Livros Digitais

Servidor pessoal de leitura focado exclusivamente em documentos PDF, projetado para operar com mínimo consumo de CPU, RAM e I/O de disco em hardware modesto (como computadores antigos com processador Intel Celeron, operando concomitantemente com o Jellyfin).

---

## 1. Novidades da Iteração 3

- **Marca-Texto Inteligente**:
  - Seleção de texto na camada de texto nativa do PDF.js.
  - Barra flutuante de marcação com paleta de 5 cores (Amarelo, Verde, Azul, Rosa e Laranja).
  - Coordenadas normalizadas percentuais `(0.0 a 1.0)`, garantindo posicionamento exato independente de zoom, dispositivo ou redimensionamento.
  - Remoção de marcação com um clique.
- **Anotações Textuais e Notas de Estudo**:
  - Comentários vinculados diretamente a um trecho destacado ou notas avulsas vinculadas à página.
  - Edição e exclusão simples pelo leitor e pelo painel de estudos.
  - Preservação perpétua no SQLite sem tocar no arquivo PDF original.
- **Desenho Livre Vetorial (SVG)**:
  - Desenho fluido e responsivo com mouse, toque em celulares/tablets ou caneta digital.
  - Formato 100% vetorial com coordenadas normalizadas (pesando apenas poucos kilobytes em JSON no banco e sem perda de nitidez com zoom).
  - Seleção de cores, seletor de espessura de traço (Fino, Médio e Grosso) e ferramenta de borracha para apagar traços individuais.
  - Botão de limpeza de desenhos da página com confirmação.
  - Salvamento agrupado por debounce de 1 segundo (não realiza requisições a cada pixel).
- **Painel Lateral de Anotações (Drawer)**:
  - Gaveta lateral retrátil no leitor exibindo todos os destaques, citações e notas do documento.
  - Abas de navegação: *Todas*, *Destaques* e *Notas*.
  - Ao clicar em qualquer anotação ou destaque, o leitor salta imediatamente para a página correspondente.
- **Barra de Modos de Uso**:
  - *Modo Leitura*: navegação e rolagem natural sem interrupções.
  - *Modo Marca-texto*: destaque rápido ao selecionar textos.
  - *Modo Desenho*: ativação dos traços SVG e proteção contra rolagem acidental no toque.
- **Repositório Git Local e Backup de Dados**:
  - Versionamento completo das fontes e inclusão explícita da pasta `data/` com o banco SQLite.

---

## 2. Controle de Versão Git e Backup dos Dados

O Lectoria conta com um repositório Git estritamente **local** para histórico de alterações e preservação do banco de dados da aplicação.

### 2.1 Comandos Básicos

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

> [!IMPORTANT]
> **Aviso de Segurança e Backup**:
> O repositório Git local protege contra alterações acidentais no código e no banco de dados. No entanto, **o Git local NÃO substitui uma rotina de backup externo** (como cópia da pasta `data/` para um pendrive ou armazenamento em nuvem pessoal) em caso de pane física no disco SSD ou no computador Positivo C1260.

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
├── .gitignore                  # Regras do Git (inclui data/ e ignora books/*.pdf)
├── .venv/                      # Ambiente virtual Python
├── config.py                   # Configurações centralizadas (diretórios, porta, host)
├── database.py                 # Conexão SQLite (WAL mode) e migrações V1, V2 e V3
├── collections_service.py      # Serviço de coleções (CRUD e associações de livros)
├── annotations_service.py      # Serviço de marcações, anotações e desenhos livres
├── library.py                  # Scanner leve (pypdf), filtros, busca, ordenação e segurança
├── progress.py                 # Persistência e restauração do progresso de leitura
├── main.py                     # API FastAPI, rotas estáticas e streaming com Range
├── pytest.ini                  # Configurações do framework de testes
├── requirements.txt            # Dependências Python mínimas fixadas
├── README.md                   # Documentação completa do projeto
├── books/                      # Diretório local para os arquivos PDF (mantido intacto)
├── data/                       # Banco de dados persistente versionado (lectoria.db)
├── static/                     # Frontend estático
│   ├── index.html              # Interface da Biblioteca (Tema Claro/Escuro, Coleções, Filtros)
│   ├── reader.html             # Interface do Leitor (Anotações, Marcações, Desenho e Painel)
│   ├── css/
│   │   └── style.css           # Estilos e temas claro/escuro
│   ├── js/
│   │   ├── app.js              # Lógica da biblioteca (busca, coleções, filtros, ordenação)
│   │   └── reader.js           # Lógica do leitor (PDF.js, texto, marca-texto, SVG draw, drawer)
│   └── vendor/
│       └── pdfjs/              # PDF.js e worker embutidos offline
└── tests/                      # Suíte de testes automatizados
    ├── conftest.py             # Fixtures e isolamento de ambiente
    ├── test_annotations.py     # Testes de destaques, notas e desenhos vetoriais
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
| `+` ou `=` | Aumentar zoom |
| `-` | Diminuir zoom |
| `w` | Ajustar à largura da tela (*Fit Width*) |
| `p` | Ajustar à página inteira (*Fit Page*) |
| `d` | Alternar Modo Desenho Livre |
| `h` | Alternar Modo Marca-texto |
| `Esc` | Fechar modais, painel lateral ou barra de seleção |

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

Todos os **22 testes** cobrem os fluxos de leitura, coleções, migrações V1, V2 e V3, destaques, anotações, desenhos livres, integridade e segurança.

---

## 9. Medições Reais de Consumo no Hardware

Medições reais realizadas no processo do Lectoria em execução:

```bash
ps -o pid,ppid,%cpu,%mem,rss,cmd -p $(pgrep -f "main.py")
```

- **Consumo de Memória RAM**: **~57 MB a 60 MB** (RSS total).
- **Consumo de CPU em Repouso**: **0.0%**.
- **Impacto no Celeron 847**: Desprezível em repouso. O streaming por chunks sob demanda (HTTP Range) evita picos de leitura e o descarregamento de páginas na rolagem contínua previne estouro de memória no navegador.
