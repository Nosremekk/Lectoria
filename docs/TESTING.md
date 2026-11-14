# Guia de Testes Automatizados do Lectoria

Este documento descreve a organização das suítes de teste, o uso de fixtures, geradores sintéticos de PDF/EPUB, testes com banco temporário, testes E2E com Playwright e os critérios para liberação de versões.

---

## 1. Organização das Suítes de Teste

A suíte de testes do Lectoria totaliza **242 testes automatizados (100% aprovados)**, divididos em dois grandes blocos:

```
tests/
├── conftest.py                           # Fixtures globais de app, cliente e banco de teste
├── test_*.py                             # 206 testes unitários e de integração de API
└── e2e/                                  # 36 testes End-to-End no navegador real (Chromium headless)
    ├── test_browser_e2e.py               # E2E do leitor PDF/EPUB, anotações e descoberta
    ├── test_v170_shared_reading_ui.py    # E2E do painel de Leitura Compartilhada (Fase 4)
    ├── test_v170_reader_shared_quotes.py # E2E da integração de citações no leitor (Fase 5)
    └── test_v170_phase6_validation_e2e.py# E2E de validação final, spoilers, 8 larguras e capas (Fase 6)
```

---

## 2. Comandos de Execução

Todos os comandos devem ser executados no ambiente virtual do projeto:

### 2.1 Executar Apenas Testes Unitários e de Integração (Rápido, ~25s)
```bash
./.venv/bin/pytest --ignore=tests/e2e -v
```

### 2.2 Executar Apenas Testes End-to-End com Playwright (~1m50s)
```bash
./.venv/bin/pytest tests/e2e -v
```

### 2.3 Executar a Suíte Completa Consolidada (242 Testes)
```bash
./.venv/bin/pytest -v
```

### 2.4 Executar uma Suíte Específica
```bash
./.venv/bin/pytest tests/test_v170_phase6_security_and_matrix.py -v
```

---

## 3. Padrões de Fixtures e Isolamento

1. **Bancos de Dados Temporários (`tmp_path` / `tmp_path_factory`)**:
   - Os testes **nunca** utilizam nem modificam o banco de dados em `data/lectoria.db`.
   - Cada teste ou módulo instancia um arquivo SQLite efêmero em diretório temporário, inicializado via `database.init_db(temp_db_path)` e restaurando `config.DB_PATH` no `finally`.
2. **Geradores Sintéticos de Documentos**:
   - Para evitar arquivos binários pesados no repositório Git, os testes geram PDFs e EPUBs válidos sob demanda:
     - `_create_text_pdf(path, pages)`: Constrói PDFs com fontes Type1 reais e stream de texto legível para o `.textLayer` do PDF.js.
     - `_create_large_pdf(path, pages)`: Gera documentos de 25+ páginas para auditoria de Range requests.
     - `_create_synthetic_epub(path)`: Monta arquivos `.epub` válidos (mimetype, container XML, OPF, XHTML e sumário) via `zipfile`.
3. **Servidor Efêmero para Testes E2E (`_find_free_port`)**:
   - Os testes Playwright inicializam instâncias Uvicorn isoladas em portas TCP livres alocadas dinamicamente via `socket.bind(("127.0.0.1", 0))`, garantindo que testes concorrentes ou servidores locais em execução (porta 8765) não sofram conflitos.

---

## 4. Categorias Principais de Testes

### 4.1 Persistência e Migrações (`test_v170_shared_reading_persistence.py`)
- Validação da migração SQLite v10 → v11.
- Teste de falha injetada com garantia de `ROLLBACK` e integridade do banco anterior.
- Backup pré-migração sem sobrescrita.
- Verificação de constraints de chaves estrangeiras (`PRAGMA foreign_key_check;`).
- Triggers contra respostas cruzadas em tópicos distintos.

### 4.2 Segurança, Permissões e IDOR (`test_v170_phase6_security_and_matrix.py`)
- Anti-enumeração: retorno estrito de `404` para não-membros em clubes privados.
- Anti-IDOR: bloqueio de compartilhamento com nota de terceiros ou livro divergente.
- Proteção CSRF: rejeição de chamadas mutáveis sem `x-csrf-token`.
- Rejeição de Mass Assignment (`ConfigDict(extra="forbid")` retornando `422`).
- Política de leitura somente-leitura em `completed`, `cancelled` e clubes arquivados (`400`).

### 4.3 Desempenho e Streaming PDF (`test_v161_pdf_performance.py`)
- Verificação de cabeçalhos `Accept-Ranges: bytes` e respostas `206 Partial Content`.
- Resolução leve em uma única consulta SQL (`resolve_book_file_for_user`).
- Validação de revalidação condicional `304 Not Modified` via `ETag`.

### 4.4 Interface e Responsividade (`test_v170_phase6_validation_e2e.py`)
- Auditoria de spoilers reais no DOM (conteúdo ausente da árvore de acessibilidade antes da ativação por clique ou Enter).
- Prevenção de duplo clique em publicação via `withSubmitLock`.
- Responsividade validada sem overflow horizontal (`scrollWidth <= innerWidth`) nas 8 larguras: 320px, 360px, 375px, 390px, 412px, 768px, 1280px e 1920px.
- Guarda de regressão do tamanho e aspect-ratio das capas na biblioteca (v1.6.3).

---

## 5. Critérios Obrigatórios para Aprovação de Release

Antes de qualquer tag ou release oficial:
1. **100% de Aprovação na Suíte**: Zero falhas nos testes unitários e E2E.
2. **Alinhamento de Versão**: O teste `test_v151_frontend_backend_version_alignment` deve passar, assegurando que `config.APP_VERSION`, `data-app-version` e `?v=X.Y.Z` estejam perfeitamente sincronizados.
3. **Zero Alterações no Acervo**: Os testes nunca devem escrever nas pastas de livros do host.
