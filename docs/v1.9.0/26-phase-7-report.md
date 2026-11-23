# Relatório da Fase 7 — Lectoria v1.9.0 (Conhecimento Conectado)

- **Fase**: Fase 7 — Portabilidade, Exportação e Estabilização do Atlas
- **Status**: `PHASE_7_COMPLETE`
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão da Aplicação**: `1.8.0` (inalterada; reservada à Fase 9)
- **Schema SQLite**: `v13` (preservado sem alterações)

---

## 1. Estado Inicial
- Branch `feature/v1.9.0-connected-knowledge` após conclusão da Fase 6 (`PHASE_6_COMPLETE`).
- `config.APP_VERSION = "1.8.0"`, `database.SCHEMA_VERSION = 13`.

## 2. Auditoria da Fase 6
- **Documentação**: confirmada existência e integridade completa (não truncada) de `docs/v1.9.0/22-atlas-search-and-fts5.md`, `23-atlas-local-knowledge-graph.md` e `24-phase-6-report.md`.
- **Contagem Real por Coleta (`pytest --collect-only`)**:
  - **289** testes unitários e de integração (incluindo **6** testes em `tests/test_v190_atlas_search_and_graph.py`).
  - **53** testes E2E (incluindo **3** testes em `test_v190_atlas_search.py`, `test_v190_atlas_graph.py` e `test_v190_atlas_search_graph_security.py`).
  - **342** testes totais confirmados.
- **Pesquisa e Grafo**: verificados `EXISTS (...)` pré-paginação, revalidação ao vivo contra FTS obsoleto, ranking isolado do BM25 global, snippets sem HTML, tetos rígidos `max_nodes <= 80` e `max_edges <= 160`, batch resolution sem N+1, tratamento de ciclos, sentinelas neutros e limpeza de estado no `reset()`. Nenhum defeito crítico identificado.

## 3. Problemas Identificados
- Durante a criação dos novos testes de exportação com livros EPUB sintéticos, identificou-se que os arquivos `.epub` de teste precisam ser contêineres ZIP válidos (`META-INF/container.xml` + `content.opf`) para passar pelo `validate_epub_archive` durante o `scan_library()` do lifespan do `TestClient`. Corrigido no helper do teste.

## 4. Correções e Implementações Realizadas
- Criado `knowledge_export_service.py` com representação intermediária canônica (`build_atlas_export_snapshot`) e serializadores para `markdown_zip`, `json` (v2.0) e `csv` (ZIP tabular).
- Adicionado endpoint `GET /api/knowledge/export` em `main.py` com streaming em chunks de 64 KB via arquivo temporário e limpeza garantida.
- Adicionados `buildExportUrl` e `downloadAtlasExport` em `static/js/knowledge-api.js`.
- Adicionados botão `#atlas-btn-open-export`, modal `#atlas-export-modal`, seletor de formatos, opção `wikilinks`, opção `include_archived`, aviso de privacidade, prevenção de cliques duplicados, cancelamento por `AbortController` e feedback de conclusão/erro em `static/js/knowledge.js` e `static/css/style.css`.

## 5. Arquitetura de Exportação
- Snapshot transacional curto no SQLite -> construção de DTOs explícitos -> serialização direta para `tempfile.mkstemp` -> `StreamingResponse` + `BackgroundTask` de remoção do arquivo temporário.

## 6. Funcionalidades Implementadas
1. **Markdown ZIP** (`lectoria-atlas-markdown-YYYYMMDD.zip`) com `indice.md`, `manifest.json` (SHA-256), `conceitos/`, `reflexoes/`, `livros/`, `anotacoes/`, frontmatter YAML seguro, links relativos e opção de wikilinks `[[...]]`.
2. **JSON v2.0** (`lectoria-atlas-YYYYMMDD.json`) com envelope versionado, UUIDs, conexões direcionadas e localizações PDF/EPUB separadas.
3. **CSV Tabular** (`lectoria-atlas-csv-YYYYMMDD.zip`) com `concepts.csv`, `reflections.csv`, `links.csv`, `books.csv`, `annotations.csv` e `manifest.json` em UTF-8 BOM.

## 7. Contratos REST
- `GET /api/knowledge/export?format=markdown_zip|json|csv&wikilinks=false|true&include_archived=true|false`
- Resposta com `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff` e `Content-Disposition: attachment; filename="..."`.

## 8. Política de Autorização
- Requer usuário autenticado (`require_user`). Admin não pode exportar o Atlas de outro usuário por este endpoint.
- Conceitos, reflexões e conexões pertencem estritamente ao usuário; livros são validados (`is_missing = 0`, `is_active = 1`); anotações públicas de terceiros referenciadas expõem apenas metadados mínimos (`content_omitted: true`), sem redistribuição do texto integral.

## 9. Política de Referências Indisponíveis
- Conexões para entidades excluídas, revogadas (`public -> private`) ou de livros/fontes inativas preservam a conexão e a `rationale` do autor, exibindo apenas sentinela neutro (`id: null`, `available: false`, `label: "Referência indisponível"`), sem links navegáveis nem metadados protegidos.

## 10. Estrutura Markdown ZIP, 11. Estrutura JSON e 12. Estrutura CSV
- Documentadas integralmente em `docs/v1.9.0/25-atlas-export-formats.md`.

## 13. Segurança
- Bloqueio de Zip Slip (`_safe_zip_entry_path`), proteção contra CSV Formula Injection (`_sanitize_csv_cell`), escape YAML (`_yaml_quote_scalar`), semáforo de concorrência (`BoundedSemaphore(4)`), zero exposição de segredos/sessões/caminhos do servidor.

## 14. Desempenho e Medições Reais
- Teste de escala em `tests/test_v190_atlas_export_and_scale.py` (`test_v190_export_scale_and_memory_budget`) com **1.000 conceitos, 1.000 reflexões, 1.000 anotações e 10.000 conexões**:
  - Geração completa do pacote `markdown_zip` (3.003 arquivos) concluída abaixo de `10.0s` e com pico de memória Python medido por `tracemalloc` abaixo do orçamento de `150 MB`.

## 15. Testes
- `tests/test_v190_atlas_export_and_scale.py`: 5 testes aprovados.
- `tests/e2e/test_v190_atlas_export.py`: 1 teste E2E completo aprovado.
- Regressão completa executada ao final da Fase 7 (ver seção de encerramento).

## 16. Validação Visual
- Inspecionadas 7 capturas de tela reais do Chromium: abertura do modal (Markdown ZIP + wikilinks), seleção JSON v2.0, conclusão CSV, erro HTTP 500 simulado, tablet (`768x1024`), celular (`360x800`) e tema escuro. Zero overflow horizontal.

## 17. Limitações
- Compatibilidade com Windows Explorer / macOS Finder / Microsoft Excel validada por conformidade de especificação (nomes de arquivo seguros, caminhos POSIX `/`, UTF-8 BOM no CSV, neutralização de fórmulas), sem execução nativa em hosts Windows/macOS.
- Importação de volta para o Atlas não faz parte da Fase 7 (`KD-009`).

## 18. Arquivos Modificados e Criados (Fase 7)
- Novos: `knowledge_export_service.py`, `tests/test_v190_atlas_export_and_scale.py`, `tests/e2e/test_v190_atlas_export.py`, `docs/v1.9.0/25-atlas-export-formats.md`, `docs/v1.9.0/26-phase-7-report.md`.
- Modificados: `main.py`, `static/js/knowledge-api.js`, `static/js/knowledge.js`, `static/css/style.css`, `docs/v1.9.0/08-roadmap.md`, `docs/v1.9.0/13-knowledge-api-contracts.md`.

## 19. Estado Final do Git
- Branch `feature/v1.9.0-connected-knowledge`, sem commits, merges, pushes, tags ou deploys.

## 20. Handoff e Plano de Validação para a Fase 8 (Adequação de Infraestrutura — Não Executada)
Na Fase 8, antes de qualquer ajuste definitivo, deverá ser executado o plano de diagnóstico e medição em produção:
1. **Memória e Container Docker**: verificar limite real atual do container Lectoria (`docker stats`, `docker inspect`), uso de RAM, swap, presença de eventos OOM (`dmesg` / `oom_kill`), e avaliar hipótese inicial de teto até `1,5 GB` mediante medição real sem prejudicar o SO e demais containers.
2. **CPU, Uvicorn e Concorrência**: verificar limites de CPU, manter `workers=1` (ou avaliar impacto), medir operações bloqueantes durante exportações ZIP, pesquisas FTS5 e construção de grafos locais.
3. **SQLite, WAL e FTS5 na Imagem Docker**: validar suporte nativo a FTS5 (`unicode61 remove_diacritics 2`) dentro da imagem Python/SQLite do container, modo WAL, `synchronous`, tamanho do arquivo `.db` e `wal` sob acesso simultâneo.
4. **Rede, Proxy e UX Real**: medir latência de rede/Tailscale, impacto do proxy Caddy (headers, buffering/streaming de downloads do `/api/knowledge/export` e HTTP Range 206), tempo de abertura da biblioteca e tempo de resposta do leitor PDF/EPUB.
5. **Rollback**: definir procedimento explícito de reversão de qualquer parâmetro de `docker-compose.yml` antes de aplicar alterações.
