# Desempenho, Escalabilidade e Orçamento de Recursos — Lectoria v1.9.0

- **Versão Planejada**: `v1.9.0 — Conhecimento Conectado`
- **Ambiente de Referência**: VPS Linux com **4 GB de RAM** compartilhada com outros serviços (e compatibilidade com mini-PCs domésticos modestos).
- **Natureza do Documento**: Estratégias de engenharia de desempenho em SQLite e Vanilla JS, modelagem analítica de quatro cenários de volume (100 a 50.000 anotações) e hipóteses de desempenho a serem medidas durante a implementação.
- **Nota Metodológica e Ressalva Técnica (Seção 13.3)**: Qualquer valor numérico de latência (ex.: `< 15 ms`, `< 30 ms`), crescimento percentual de banco de dados (ex.: `15%–20%`), consumo incremental de RAM ou `0%` de CPU ociosa citado neste documento representa **hipótese arquitetural e meta de engenharia**, e **não** fato provado, até que seja medido empiricamente em testes automatizados e registrado nos relatórios de fase (`11-phase-1-report.md` e fases subsequentes).

---

## 1. Restrições do Ambiente e Orçamento de Recursos

A Lectoria opera em processo único (`Uvicorn` + `SQLite WAL`) dividindo uma VPS de 4 GB de RAM com outros serviços (como proxies reversos e servidores de mídia). Além disso, sua unidade `systemd` de referência aplica limite defensivo de `MemoryMax=512M` (`README.md:127`).

Para manter o consumo de memória RAM do processo Python estável (tipicamente entre **45 MB e 90 MB RSS**) e ~0% de CPU em ociosidade, a arquitetura da v1.9.0 adota dez diretrizes técnicas:

1. **Zero Grafo em Memória no Servidor**:
   - O backend Python **não** mantém um grafo residente em memória RAM para os usuários. Todas as consultas de vizinhança, backlinks e conceitos são resolvidas sob demanda pelo SQLite via índices B-Tree e liberadas imediatamente após serializar a resposta JSON.
2. **Prevenção Estrita de Consultas N+1**:
   - Ao listar destaques e notas de um livro (`get_book_annotations_summary`) ou listar conceitos (`list_concepts`), os vínculos e contadores são recuperados em **1 consulta agregada por lote (`WHERE source_id IN (...)`)**, jamais executando um `SELECT` dentro de um loop `for` em Python.
3. **Índices Compostos Cobrindo `user_id` na Primeira Coluna**:
   - Como 100% das consultas a `concepts`, `reflections` e `knowledge_links` são isoladas por usuário, todos os índices principais iniciam por `user_id`, reduzindo o espaço de busca no B-Tree apenas à partição daquele leitor:
     - `idx_concepts_user_norm ON concepts(user_id, normalized_name)`
     - `idx_concepts_user_updated ON concepts(user_id, is_archived, updated_at DESC)`
     - `idx_reflections_user_updated ON reflections(user_id, is_archived, updated_at DESC)`
     - `idx_reflections_user_book ON reflections(user_id, primary_book_id)`
     - `idx_klinks_user_source ON knowledge_links(user_id, source_type, source_id)`
     - `idx_klinks_user_target ON knowledge_links(user_id, target_type, target_id)`
     - `idx_klinks_user_rel ON knowledge_links(user_id, relation_type)`
4. **Paginação Obrigatória em Todas as Listagens**:
   - Endpoints de conceitos, reflexões, conexões e busca utilizam `limit` (padrão `30` a `50`, máximo `200`) e `offset`, com carregamento incremental (*Carregar mais*).
5. **Profundidade e Cardinalidade Limitadas nas Consultas de Grafo**:
   - O endpoint `/api/knowledge/graph` restringe a profundidade máxima a **`depth <= 2`** e o número máximo de nós retornados por requisição a **`max_nodes <= 80`** (configurável até um teto rígido de `120`).
   - Se um conceito central possuir mais conexões que o limite, o backend ordena por relevância/data (`updated_at DESC`), retorna os 80 nós principais e sinaliza `"truncated": true, "total_neighbors": N` para que a UI ofereça paginação ou filtragem na lista textual.
6. **Evitar `WITH RECURSIVE` Descontrolado**:
   - Como a profundidade máxima do grafo local é 2 saltos, a vizinhança pode ser obtida com **2 consultas indexadas por conjunto de IDs** (`hop 1`: `WHERE user_id = ? AND (source_id = ? OR target_id = ?)`; `hop 2`: `WHERE user_id = ? AND (source_id IN (...) OR target_id IN (...)) LIMIT 80`) ou uma CTE recursiva com cláusula `WHERE depth < 2 LIMIT 160`. Duas consultas por lote com `IN (...)` e `LIMIT` têm plano de execução 100% previsível no otimizador do SQLite.
7. **Pesquisa Local FTS5 com `LIMIT` no Operador `MATCH`**:
   - Consultas em `knowledge_fts` aplicam `LIMIT` diretamente na subconsulta FTS5 ordenada por `bm25(knowledge_fts)` antes de hidratar textos longos de reflexões.
8. **Exportação ZIP com Escrita Incremental**:
   - A geração do pacote `.zip` de conhecimento utiliza `SpooledTemporaryFile(max_size=4 * 1024 * 1024)` (fazendo *spill* para disco se ultrapassar 4 MB) e itera por lotes, evitando alocar dezenas de megabytes simultaneamente na RAM da VPS.
9. **Concorrência SQLite WAL**:
   - Leituras de conceitos, backlinks e grafos são puramente `SELECT` e ocorrem em paralelo no modo WAL sem bloquear salvamentos de progresso ou criação de notas. Escritas em `knowledge_links` e `knowledge_fts` são curtas (< 2 ms) e atômicas.
10. **Desempenho em Dispositivos Móveis**:
    - O grafo SVG limita-se a `<= 80` nós no DOM, com cálculo de layout síncrono de cota fixa (máximo 60 iterações, interrompido se atingir `12 ms`) e zero loops `requestAnimationFrame` contínuos.

---

## 2. Modelagem Analítica dos Quatro Cenários de Volume (Seção 10)

A tabela a seguir modela o impacto estrutural estimado em armazenamento SQLite e complexidade de consulta para quatro portes de base de dados (assumindo tamanho médio de ~450 bytes por anotação, ~600 bytes por conceito, ~2.500 bytes por reflexão e ~220 bytes por conexão `knowledge_links`, mais ~40% de overhead de índices B-Tree e FTS5):

| Métrica / Dimensão | Cenário 1: Leve | Cenário 2: Médio | Cenário 3: Intenso | Cenário 4: Extremo |
| :--- | :---: | :---: | :---: | :---: |
| **Anotações (`highlights` + `notes`)** | **100** | **1.000** | **10.000** | **50.000** |
| **Conceitos (`concepts` — proporção ~1:10)** | 10 | 100 | 1.000 | 5.000 |
| **Reflexões (`reflections` — proporção ~1:25)** | 4 | 40 | 400 | 2.000 |
| **Conexões (`knowledge_links` — proporção ~1,5:1)** | 150 | 1.500 | 15.000 | 75.000 |
| **Tamanho estimado em disco (tabelas + índices + FTS5)** | ~120 KB | ~1,2 MB | ~12 MB | ~62 MB |
| **Complexidade de busca de Backlinks de 1 item** | $O(\log N + k)$ | $O(\log N + k)$ | $O(\log N + k)$ | $O(\log N + k)$ |
| **Páginas B-Tree lidas em consulta de 1 Conceito** | 2–3 páginas (12 KB) | 3–4 páginas (16 KB) | 4–6 páginas (24 KB) | 5–8 páginas (32 KB) |
| **Comportamento do Grafo Local (`max_nodes = 80`)** | Exibe 100% da rede | Exibe vizinhança de 2 saltos completa | Exibe até 80 nós da vizinhança de 2 saltos | Corta em 80 nós (`truncated = true`) + paginação na lista |
| **Impacto de RAM no processo Python por requisição** | < 0,5 MB | < 1 MB | < 1,5 MB (paginado) | < 2 MB (paginado) |

### Análise Crítica do Cenário 4 (50.000 Anotações e 75.000 Conexões)
Mesmo no cenário extremo de **50.000 anotações, 5.000 conceitos, 2.000 reflexões e 75.000 conexões**:
1. O volume total em disco (~62 MB) cabe folgadamente no cache de páginas do sistema operacional Linux em uma VPS de 4 GB de RAM.
2. **Onde surgiria gargalo se não houvesse proteção?**
   - Se a interface tentasse carregar todas as 75.000 conexões para desenhar um "grafo global" no navegador (o que alocaria centenas de megabytes no cliente e travaria celulares).
   - Se um único "super-conceito" tivesse 2.000 anotações vinculadas e `get_concept_detail` tentasse retornar todas as 2.000 passagens sem `LIMIT`/`OFFSET`.
   - Se a busca textual usasse `LIKE '%termo%'` fazendo *full table scan* em 50.000 notas + 2.000 reflexões em vez de usar `knowledge_fts MATCH`.
3. **Como a arquitetura proposta neutraliza esses três gargalos**:
   - O grafo é sempre **local e limitado a `max_nodes <= 80`**.
   - As passagens associadas a um conceito são **paginadas (`limit = 30, offset = 0`)**.
   - A busca integrada utiliza o índice invertido **SQLite FTS5 (`knowledge_fts`)** com `LIMIT`.

---

## 3. Hipóteses de Desempenho a Serem Medidas na Implementação

Durante as Fases 1, 2 e 6 da implementação da v1.9.0, um script de verificação com banco sintético populado nos 4 cenários (100, 1.000, 10.000 e 50.000 anotações) deverá medir empiricamente as seguintes hipóteses:

| ID | Hipótese a Validar | Alvo de Aceitação em Hardware Modesto | Método de Medição Futuro |
| :-: | :--- | :--- | :--- |
| **HP-01** | Consulta de backlinks e conexões de uma anotação (`GET /api/knowledge/links`) no Cenário 4 (50k notas / 75k links). | Latência p95 `< 15 ms` e zero *full table scan* (`SEARCH TABLE knowledge_links USING INDEX`). | `EXPLAIN QUERY PLAN` + teste automatizado com fixture de 75k links. |
| **HP-02** | Carregamento do resumo de anotações de um livro no leitor (`GET /api/books/{id}/annotations`) enriquecido com chips de conceitos. | Aumento de latência `< 5 ms` em relação à v1.8.0 e máximo de 2 queries SQL adicionais em lote. | Contagem de statements SQL via `conn.set_trace_callback` em Pytest. |
| **HP-03** | Pesquisa integrada FTS5 (`GET /api/knowledge/search?q=...`) no Cenário 3 (10k notas) e Cenário 4 (50k notas). | Latência p95 `< 30 ms` com filtro de permissões ativo. | Benchmark Pytest sobre banco temporário com 50k registros indexados em `knowledge_fts`. |
| **HP-04** | Geração do subgrafo local de 2 saltos (`GET /api/knowledge/graph`) e layout SVG no cliente mobile (`360px`). | Backend `< 25 ms`; cálculo de layout JS no navegador `< 16 ms` (1 frame) e `0%` CPU após renderização. | Medição via Playwright (`performance.now()`) em viewport `360x800`. |
| **HP-05** | Exportação do pacote Markdown ZIP no Cenário 3 (10k anotações, 1k conceitos, 400 reflexões). | Pico incremental de memória RSS do processo Python `< 35 MB` durante a geração do ZIP. | Medição de `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` durante o teste de exportação. |
