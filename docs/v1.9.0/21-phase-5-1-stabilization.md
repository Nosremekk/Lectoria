# Lectoria v1.9.0 — Fase 5.1: Estabilização Regressiva e Auditoria Técnica da Fase 5

- **Status da Fase 5.1**: `PHASE_5_1_COMPLETE`
- **Branch de Trabalho**: `feature/v1.9.0-connected-knowledge`
- **Versão da Aplicação**: `1.8.0` (preservada rigorosamente)
- **Schema SQLite**: Versão 13 (sem novas migrações)
- **Data**: 2026-11-21

---

## 1. Resumo Executivo

A Fase 5.1 realizou uma auditoria técnica e regressiva rigorosa sobre as entregas da Fase 5 e a estabilidade da suíte E2E completa da Lectoria, antes de iniciar a implementação da Fase 6.

Foram investigados, reproduzidos e solucionados **cinco problemas técnicos reais** (um na infraestrutura de subprocessos da suíte E2E e quatro na aplicação):

1. **Deadlock de Buffer de Pipe do Subprocesso Uvicorn em Suítes E2E Longas (`test_v160_reading_clubs_and_collective_reads_multiuser`)**:
   - A fixture module-scoped `e2e_server` em `tests/e2e/test_browser_e2e.py` iniciava o processo `uvicorn` com `stdout=subprocess.PIPE` e `stderr=subprocess.PIPE` sem nenhuma thread ou leitor consumindo os pipes.
   - Ao longo dos 31 testes de `test_browser_e2e.py` (especialmente após a adição dos novos arquivos estáticos e chamadas de API da v1.9.0), os logs de acesso HTTP do Uvicorn atingiam a capacidade do buffer de pipe do kernel Linux (**65.536 bytes / 64 KB**) durante o 31º teste (`test_v160_reading_clubs_and_collective_reads_multiuser`).
   - Quando o buffer de 64 KB enchia, a chamada `write(1, ...)` do logger do Uvicorn bloqueava a thread principal do event loop assíncrono no kernel, congelando o servidor HTTP logo após o envio do convite por `page_a` e impedindo que `page_b` recebesse resposta de `GET /api/reading-clubs/invites/mine`.
2. **Reinicialização Indevida do Estado do Atlas na Inicialização de Deep Links (`static/js/app.js`)**:
   - Na abertura inicial da página (`DOMContentLoaded`), `setMainTab(currentMainTab, initialTabOpts)` ativava `window.LectoriaAtlas.activate(initialTabOpts)` antes da resolução assíncrona de `initAuth()`.
   - Quando `initAuth()` recebia `/api/auth/status`, a condição `if (!currentUser || currentUser.id !== data.user.id)` avaliava como verdadeira (pois `currentUser` iniciava como `null`) e invocava `clearUserSessionCache()` → `window.LectoriaAtlas.reset()`, limpando `currentReflection` e `loadedUpdatedAt` ou abortando a requisição do deep link em trânsito.
3. **Controle Otimista de Concorrência Atômico no Backend (`knowledge_service.update_reflection` e `PATCH /api/knowledge/reflections/{id}`)**:
   - Na Fase 5, a verificação de `updated_at` ocorria apenas no cliente (`GET` prévio seguido de `PATCH` incondicional, sujeito a janela TOCTOU) e os carimbos de tempo tinham resolução de segundos (`%Y-%m-%d %H:%M:%S`).
   - Na Fase 5.1, o backend passou a gerar timestamps com resolução de microssegundos e avanço monotônico garantido (`_next_monotonic_iso`), e `update_reflection` passou a aceitar `expected_updated_at`, executando atualização condicional atômica (`UPDATE reflections ... WHERE id = ? AND user_id = ? AND updated_at = ?`) e retornando `HTTP 409 Conflict` (`KnowledgeConflictError`) caso `cursor.rowcount == 0`.
4. **Suporte Funcional Completo ao Filtro por `entity_type` Sem `entity_id` em `GET /api/knowledge/links` (`concept`, `reflection`, `highlight`, `note`, `book`)**:
   - Embora o seletor `#atlas-connections-entity-filter` em `static/js/knowledge.js` já contivesse as 5 opções aprovadas (incluindo `book`), `knowledge_service.list_knowledge_links` exigia que `entity_type` e `entity_id` fossem sempre passados juntos, retornando `HTTP 400` quando o explorador textual de conexões filtrava apenas por `entity_type`.
   - `list_knowledge_links` foi corrigido para permitir filtragem apenas por `entity_type` (`source_type = ? OR target_type = ?` respeitando `direction`), mantendo a validação combinada quando `entity_id` é informado.
5. **Condição de Corrida na Renderização de Página Única do PDF e Navegação Intra-Livro (`static/js/reader.js` e `static/js/reader-knowledge.js`)**:
   - Em `renderSinglePage(num)` (`static/js/reader.js`), havia três pontos de espera assíncrona (`await pdfDoc.getPage(num)`, `await page.getTextContent()` e `await setupDrawingLayerForPage(...)`) nos quais `singleRenderTask` era `null`. Navegações consecutivas rápidas podiam permitir que uma renderização anterior concluísse fora de ordem e sobrescrevesse `singleHighlightsLayer` e `pageNumInput.value`.
   - Adicionado contador de geração `singleRenderGen` validado após cada `await` em `renderSinglePage`, proteção `if (document.activeElement !== pageNumInput)` ao atualizar o valor do input, retorno explícito da Promise de `renderSinglePage` em `goToPage(num)` e fechamento do modal `#modal-reader-knowledge` após a conclusão de `await adapter.goToPage(navSpec.page)` em `handleNavigateToTarget`.

---

## 2. Investigação Detalhada da Suíte E2E (`test_v160_reading_clubs_and_collective_reads_multiuser`)

### 2.1. Evidências Coletadas e Diagnóstico
Durante a reprodução controlada em `tests/e2e/test_browser_e2e.py`:
- **Execução isolada (`-k test_v160_reading_clubs_and_collective_reads_multiuser`)**: Aprovada em `14.40s` com todas as respostas HTTP `200`/`201` entregues para `page_a`, `page_b` e `page_c`.
- **Execução parcial (`-k "test_v152 or test_v160"`, 6 testes)**: Aprovada em `23.15s`.
- **Execução completa do módulo (`tests/e2e/test_browser_e2e.py`, 31 testes)**: Instrumentando listeners de rede (`page.on("request")`, `page.on("response")`), constatou-se que `page_a` recebia `POST /api/reading-clubs/{id}/invites -> 201`, mas `page_b` não recebia resposta alguma ao navegar e consultar `/api/reading-clubs/invites/mine`.
- **Inspeção da fixture `e2e_server` (`tests/e2e/test_browser_e2e.py:164-170`)**:
  - O servidor `uvicorn` era instanciado com `stdout=subprocess.PIPE, stderr=subprocess.PIPE` (ao contrário de `test_v170_library_filters_e2e.py:176-177`, que já utilizava `stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`).
  - Com 31 testes compartilhando a mesma instância `e2e_server`, o volume acumulado de logs de acesso do Uvicorn atingia o limite de 64 KB do buffer de pipe do Linux exatamente durante o 31º teste, bloqueando a chamada de sistema `write()` no processo filho.

### 2.2. Hipóteses Descartadas
1. *Hipótese descartada 1 — "Apenas latência de CPU ou disco na máquina local"*: Descartada porque mesmo aumentando o timeout de `page_b.wait_for_selector("#club-invites-banner")` para `10.000 ms`, a resposta HTTP nunca chegava quando os 30 testes anteriores enchiam o pipe de `stdout` do Uvicorn.
2. *Hipótese descartada 2 — "Vazamento de cookies ou colisão de sessão entre usuários"*: Descartada porque `context_a`, `context_b` e `context_c` utilizam contextos Playwright independentes (`browser_instance.new_context()`) e tokens de sessão distintos.
3. *Hipótese descartada 3 — "Regressão funcional de rotas ou banco de dados introduzida pelo Atlas"*: Descartada porque nenhuma tabela ou rota de `reading_clubs` foi alterada e a API respondia normalmente assim que o pipe de saída deixou de bloquear o processo Uvicorn.

### 2.3. Correções Aplicadas nos Testes E2E
1. **Prevenção de Deadlock de Pipe (`subprocess.DEVNULL`)**:
   - Em `tests/e2e/test_browser_e2e.py` e nas demais fixtures E2E que utilizavam `subprocess.PIPE` sem consumidor, `stdout` e `stderr` do subprocesso Uvicorn foram configurados com `subprocess.DEVNULL`.
2. **Sincronização Baseada em Condições Observáveis (`test_v160_reading_clubs_and_collective_reads_multiuser`)**:
   - Substituídos sleeps fixos (`wait_for_timeout`) em pontos de carregamento assíncrono por esperas determinísticas baseadas no estado funcional do DOM:
     - `page_a.wait_for_selector("#modal-club-invite.active", state="visible", timeout=5000)` e `page_a.wait_for_function("() => (document.getElementById('invite-user-select')?.options.length || 0) > 1", timeout=5000)` antes de selecionar o usuário convidado.
     - `page_a.wait_for_function("() => !document.getElementById('modal-club-invite')?.classList.contains('active')", timeout=5000)` para confirmar a persistência do convite antes da consulta de `page_b`.
     - `page_b.wait_for_selector("#club-invites-banner", state="visible", timeout=5000)` após abrir a aba de clubes.
     - `page_a.wait_for_function("() => (document.getElementById('read-form-book-select')?.options.length || 0) > 1", timeout=5000)` para aguardar o preenchimento assíncrono de `/api/books` no modal de leitura coletiva.
     - `page_a.wait_for_function("() => (document.getElementById('club-reads-grid')?.textContent || '').includes('Leitura Conjunta: Filosofia e Métodos')", timeout=5000)` após salvar a leitura coletiva.

---

## 3. Revisão da Concorrência nas Reflexões (Seção 6)

- **Resolução e Monotonicidade de `updated_at`**:
  - `_now_iso()` em `knowledge_service.py` agora utiliza resolução de microssegundos (`%Y-%m-%d %H:%M:%S.%f`), acompanhada de `_next_monotonic_iso(previous_iso)` para garantir que atualizações consecutivas no mesmo microssegundo sempre produzam um `updated_at` estritamente maior que o anterior.
- **Controle Otimista de Concorrência Atômico no Backend**:
  - `ReflectionUpdatePayload` (`main.py`) e `knowledge_service.update_reflection` aceitam `expected_updated_at: Optional[str]`.
  - Quando informado, o SQLite executa atomicamente:
    ```sql
    UPDATE reflections
    SET title = ?, body_markdown = ?, primary_book_id = ?, tags_json = ?,
        is_pinned = ?, is_archived = ?, updated_at = ?
    WHERE id = ? AND user_id = ? AND updated_at = ?;
    ```
  - Se `cursor.rowcount == 0` (após confirmar que a reflexão existe e pertence a `user_id` na mesma transação), o serviço levanta `KnowledgeConflictError` (`HTTP 409 Conflict`).
- **Integração no Editor Frontend (`static/js/knowledge.js`)**:
  - `handleSaveReflection()` envia `expected_updated_at: loadedUpdatedAt` no payload do `PATCH`.
  - Ao receber `HTTP 409`, o editor preserva integralmente o texto local na `<textarea>`, marca o estado como `"Conflito detectado"` e exibe alerta instruindo o usuário a clicar novamente em *"Salvar reflexão"* caso deseje confirmar a sobrescrita.
- **Correção de Deep Link no Boot (`static/js/app.js`)**:
  - `initAuth()` agora só invoca `clearUserSessionCache()` quando há troca efetiva entre dois usuários autenticados distintos (`if (currentUser && currentUser.id !== data.user.id)`), preservando o estado carregado por deep links (`/?tab=atlas&subtab=reflections&id=...`).

---

## 4. Revisão da Renderização Markdown (Seção 7)

### 4.1. Subconjunto Markdown Efetivamente Suportado (`renderSafeMarkdownToDom`)
| Construção Markdown | Elemento DOM Criado | Comportamento |
| :--- | :--- | :--- |
| `#` a `####` no início da linha | `<h1>` a `<h4>` (`.atlas-md-h1`..`h4`) | Processa formatação inline no texto do título. |
| ```` ``` ```` (bloco cercado) | `<pre class="atlas-md-pre"><code class="atlas-md-code-block">` | Conteúdo inserido via `textContent` literal; blocos não fechados consomem até o fim do texto sem erro. |
| `---` ou `***` | `<hr class="atlas-md-hr">` | Separador horizontal. |
| `> texto` (linhas consecutivas) | `<blockquote class="atlas-md-blockquote">` | Agrupa linhas citadas e processa tokens inline. |
| `- item` ou `* item` | `<ul class="atlas-md-list"><li class="atlas-md-li">` | Lista não ordenada. |
| `1. item` | `<ol class="atlas-md-list"><li class="atlas-md-li">` | Lista ordenada. |
| Linhas de texto contínuas | `<p class="atlas-md-p">` | Parágrafo padrão. |
| `` `código` `` | `<code class="atlas-md-inline-code">` | Código inline via `textContent`. |
| `**negrito**` e `*itálico*` | `<strong>` e `<em>` | Ênfase tipográfica via `textContent`. |
| `[rótulo](url)` | `<a class="atlas-md-link">` ou `<span class="atlas-md-link-blocked">` | URL validada por `sanitizeMarkdownUrl`; externos recebem `target="_blank" rel="noopener noreferrer"`. |
| `![alt](url)` | `<span class="atlas-md-image-blocked">` | Bloqueia qualquer carregamento automático de imagem remota (`[Imagem externa bloqueada por privacidade: {alt}]`). |

### 4.2. Controles Empregados e Vetores Testados
- **Zero inserção de HTML**: Nenhum uso de `innerHTML`, `outerHTML`, `insertAdjacentHTML` ou `document.write`. Qualquer tag HTML bruta (`<script>`, `<img onerror>`, `<svg onload>`, `<iframe>`, `<form id="createElement"><input name="nodeName"></form>`, `<object>`) torna-se nó de texto literal (`document.createTextNode`).
- **Neutralização de protocolos e URLs ofuscadas (`sanitizeMarkdownUrl`)**:
  - Remove caracteres de controle ASCII e espaços/controles Unicode invisíveis (`\u0000-\u0020`, `\u007F-\u00A0`, `\u1680`, `\u2000-\u200F`, `\u2028-\u202F`, `\u205F-\u206F`, `\uFEFF`) antes da análise.
  - Permite apenas caminhos relativos internos iniciados por `/` (exceto `//` e `/\`) ou URLs absolutas com protocolo `http:`, `https:` ou `mailto:` validadas via `new URL()`.
  - Bloqueia `javascript:`, `vbscript:`, `data:`, `blob:`, `file:` e `about:`.
- **Complexidade Linear**: A expressão regular inline utiliza classes de caracteres negadas sem quantificadores aninhados, evitando *catastrophic backtracking* (ReDoS) mesmo em textos de 50.000 caracteres ou Markdown malformado.

---

## 5. Revisão dos Tipos de Entidade no Explorador de Conexões (Seção 8)

- Confirmado que o seletor `#atlas-connections-entity-filter` em `static/js/knowledge.js` inclui todos os 5 tipos de entidade aprovados: `concept`, `reflection`, `highlight`, `note` e `book`.
- Identificada e corrigida a lacuna funcional em `knowledge_service.list_knowledge_links` que exigia `entity_id` sempre que `entity_type` era informado. Agora `GET /api/knowledge/links?entity_type=book` (e demais tipos) filtra corretamente todas as conexões pessoais onde o tipo selecionado figura como origem ou destino, respeitando o parâmetro `direction`.
- Atualizada a documentação da Fase 5 (`19-atlas-interface-and-reflections.md` e `20-phase-5-report.md`) para listar explicitamente `book` entre os tipos filtráveis no explorador.

---

## 6. Revisão do Progresso de Leitura (Seção 9)

- Inspecionada e validada a implementação de `progressSaveChain` e `progressSaveSeq` em `static/js/reader.js`:
  - **Coalescência $O(1)$ sem fila ilimitada**: Chamadas intermediárias enfileiradas enquanto uma requisição está em trânsito verificam `if (mySeq !== progressSaveSeq) return;`, descartando estados obsoletos e enviando apenas a posição mais recente.
  - **Recuperação após falha e proteção contra bloqueio indefinido**: `progressSaveChain.then(runSave, runSave)` garante continuidade da fila mesmo se uma requisição falhar; adicionado `AbortController` com timeout de `8.000 ms` e envio explícito do cabeçalho `X-CSRF-Token` em `persistProgress`.
  - **Ressalva histórica preservada**: O fallback `emergencySave()` (`beforeunload`/`pagehide` via `sendBeacon`) permanece inalterado conforme documentado na Seção 4 de `06-security-and-privacy.md`, sem introduzir nenhuma nova ocorrência de CSRF em URL.

---

## 7. Resultados das Execuções Integrais da Fase 5.1 (Seção 5.2 e Seção 10)

| Comando Executado | Testes Coletados | Aprovados | Falhas | Ignorados | Warnings | Duração | Código de Saída |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `./.venv/bin/pytest --ignore=tests/e2e -q` | **283** | **283** | **0** | **0** | 3 | `75.48s` | **`0`** |
| `./.venv/bin/pytest tests/e2e -q` | **50** | **50** | **0** | **0** | 1 | `229.46s` | **`0`** |
| **Total Consolidado (Fase 5.1)** | **333** | **333** | **0** | **0** | 4 | `304.94s` | **`0`** |

- **Reexecuções parciais necessárias na validação final**: **0** (todos os 50 testes E2E e todos os 283 testes unitários/integração aprovaram em execução integral contínua única com código de saída `0`).

---

## 8. Limitações e Estado Final da Fase 5.1

- **Limitações Documentadas**:
  - O renderizador `renderSafeMarkdownToDom` suporta deliberadamente um subconjunto enxuto de Markdown editorial (títulos `#`–`####`, blocos de código cercados, linhas horizontais, citações `>`, listas simples `-`/`1.`, parágrafos, código inline, negrito, itálico e links validados). Tabelas HTML/GFM complexas, listas aninhadas em múltiplos níveis e imagens externas não são renderizadas como elementos ativos por decisão de simplicidade e privacidade.
  - O controle otimista de concorrência em reflexões baseia-se na coluna `updated_at` com resolução de microssegundos e incremento monotônico garantido em `knowledge_service.py`, sem adicionar coluna `version INTEGER` para preservar o schema SQLite v13 sem nova migração.
- **Classificação Final da Fase 5.1**: **`PHASE_5_1_COMPLETE`**.
- **Autorização de Avanço**: Critérios regressivos 100% satisfeitos; autorizado o início imediato da **Fase 6 — Pesquisa Integrada e Mapa de Conhecimento**.
