# Relatório da Fase 6 — Lectoria v1.9.0 (Conhecimento Conectado)

- **Fase**: Fase 6 — Pesquisa Integrada (`FTS5`) e Mapa Local de Conhecimento (`SVG` + Alternativa Textual Acessível)
- **Status**: `PHASE_6_COMPLETE`
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão da Aplicação**: `1.8.0` (inalterada até a Fase 9)
- **Schema SQLite**: `v13` (sem alterações estruturais ou novas migrações)

---

## 1. Resumo Executivo

A Fase 6 foi concluída integralmente com aprovação de 100% dos testes unitários, de integração de API, de escala quantitativa e E2E no Chromium headless. Foram entregues:

1. **Pesquisa Integrada do Atlas (`GET /api/knowledge/search` — `KD-008`)**:
   - Busca unificada sobre `concept`, `reflection`, `highlight`, `note`, `link` (via `knowledge_fts` com `unicode61 remove_diacritics 2`) e metadados de obras acessíveis (`book`, marcada com `index_source = "books_metadata"`).
   - **Dupla Barreira integrada antes da paginação**: predicados relacionais `EXISTS (...)` diretamente na consulta SQL de candidatos, garantindo que volumes dominantes de registros privados ou inacessíveis de terceiros nunca esgotem slots de paginação.
   - **Revalidação ao vivo e isolamento de relevância**: verificação de tokens sobre o texto relacional vigente (proteção contra índice FTS5 obsoleto) e pontuação determinística `_compute_authorized_relevance_score` independente das estatísticas globais IDF de `bm25(knowledge_fts)`.
   - **Snippets seguros**: geração de `snippet` e `snippet_segments` (`[{"text": str, "highlight": bool}]`) sem HTML bruto, renderizados no cliente via `createTextNode` e `<mark class="atlas-search-mark">`.
   - **Fallback relacional controlado**: acionado apenas quando FTS5 não está disponível (`force_fallback` ou ausência do módulo/tabela), preservando propagação de erros de corrupção estrutural.

2. **Mapa Local de Conhecimento (`GET /api/knowledge/graph` — `KD-006`)**:
   - Subgrafo centrado em qualquer uma das 5 entidades autorizadas (`concept`, `reflection`, `highlight`, `note`, `book`) em **1 ou 2 saltos** (`depth in {1, 2}`).
   - Resolução em lote (`_batch_resolve_graph_entities`, zero N+1), tetos rígidos (`max_nodes <= 80` incluindo o centro e `max_edges <= 160`), tratamento seguro de ciclos e sinalização explícita de `truncated` + `truncation_reasons`.
   - Proteção rigorosa de referências indisponíveis por sentinela neutro (`label = "Referência indisponível"`, `expandable = false`, `navigable = false`, nunca expandidas no 2º salto).
   - **Layout Radial SVG Determinístico**: formas geométricas e prefixos textuais distintos por tipo (`[C]`, `[R]`, `[L]`, `[D]`, `[N]`, `[Indisponível]`), setas direcionais, arestas contínuas (1º salto) vs. tracejadas (2º salto), curvas para múltiplas relações paralelas, zoom/pan, painel lateral sob demanda e **0% de CPU em repouso** (sem simulação física contínua).
   - **Alternativa Textual Acessível obrigatória (`#atlas-graph-view-list` / `#atlas-graph-text-view`)**: representa exatamente o mesmo subconjunto autorizado do grafo SVG, navegável por teclado.

---

## 2. Arquivos Modificados e Criados

### Backend
- `knowledge_service.py`: `_extract_folded_tokens`, `_text_matches_all_folded_tokens`, `_build_safe_snippet_segments`, `_compute_authorized_relevance_score`, atualização de `search_knowledge_candidates`, `search_knowledge`, `_search_books_metadata_with_cursor`, `_search_knowledge_relational_fallback`, `_batch_resolve_graph_entities`, `_format_graph_node` e `get_knowledge_graph`; rejeição de surrogates UTF-16 órfãos em `normalize_unicode_nfc`.
- `main.py`: endpoints `GET /api/knowledge/search` e `GET /api/knowledge/graph` com `require_user`, validação Query estrita e `Cache-Control: private, no-store`.

### Frontend
- `static/js/knowledge-api.js`: `searchKnowledge(params, signal)` e `getKnowledgeGraph(params, signal)`.
- `static/js/knowledge.js`: subabas `Pesquisa` (`#atlas-subtab-search`, `#atlas-pane-search`) e `Mapa` (`#atlas-subtab-graph`, `#atlas-pane-graph`), layout radial SVG, controles de zoom/pan, painel de detalhes sob demanda, lista textual acessível, ações contextuais `"Explorar no Mapa"` e `LectoriaAtlas.openGraph`.
- `static/js/app.js`: suporte a deep links `?tab=atlas&subtab=search&q=...` e `?tab=atlas&subtab=graph&type=...&id=...`.
- `static/css/style.css`: estilos responsivos (360px–1920px) para pesquisa, `<mark class="atlas-search-mark">`, grafo SVG, legenda, painel de detalhes, lista acessível e alias `--text-primary` nos temas escuro, claro e sépia.

### Testes e Documentação
- `tests/test_v190_atlas_search_and_graph.py` (6 testes unitários, API, segurança e escala até 10k conceitos / 2k reflexões / 10k notas / 50k links + `EXPLAIN QUERY PLAN`).
- `tests/e2e/test_v190_atlas_search.py`
- `tests/e2e/test_v190_atlas_graph.py`
- `tests/e2e/test_v190_atlas_search_graph_security.py`
- `docs/v1.9.0/22-atlas-search-and-fts5.md`
- `docs/v1.9.0/23-atlas-local-knowledge-graph.md`
- `docs/v1.9.0/13-knowledge-api-contracts.md`
- `docs/v1.9.0/08-roadmap.md`
- `docs/v1.9.0/24-phase-6-report.md`

---

## 3. Evidências de Validação Automatizada e Visual

- **Compilação Python (`py_compile`)**: executada com exit code `0`.
- **Suíte Unitária e de Integração Contígua (`./.venv/bin/pytest --ignore=tests/e2e -q`)**: **289 passed**, 0 failed, 0 skipped (`211.50s`).
- **Suíte E2E Contígua (`./.venv/bin/pytest tests/e2e -q`)**: **53 passed**, 0 failed, 0 skipped (`233.35s`).
- **Total Consolidado**: **342 testes aprovados**, 0 falhas, 0 ignorados.
- **Inspeção Visual**: todas as 14 capturas de tela da Fase 6 foram geradas e inspecionadas (pesquisa variada, busca vazia, filtrada, snippet destacado, mapa 1 salto, mapa 2 saltos, múltiplas relações, mapa truncado 40/40, referência indisponível, lista textual acessível, painel de detalhes, tablet 768x1024, mobile 360x800 e tema escuro).

---

## 4. Handoff para a Fase 7

- Próxima fase no roadmap (`docs/v1.9.0/08-roadmap.md`): **Fase 7 — Portabilidade (Exportação Markdown ZIP / JSON / CSV) e Estabilização**.
- Pré-requisitos preservados: `APP_VERSION = "1.8.0"`, `SCHEMA_VERSION = 13`, nenhum commit/merge/tag/deploy realizado.
