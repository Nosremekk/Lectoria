# Lectoria v1.9.0 — Fase 6: Mapa Local de Conhecimento (SVG Determinístico e Alternativa Textual Acessível)

## 1. Objetivo e Escopo (`KD-006`)

O **Mapa Local de Conhecimento** (`GET /api/knowledge/graph` e subaba `#atlas-subtab-graph` / `#atlas-pane-graph`) oferece uma visualização estrutural focada, leve e legível da vizinhança de qualquer entidade autorizada do Atlas (`concept`, `reflection`, `highlight`, `note`, `book`) em **1 ou 2 saltos**.

Em conformidade com as restrições arquiteturais do Lectoria para hardware doméstico modesto (Intel Celeron 847 / 2 GB RAM):
- **Não** existe grafo global completo;
- **Não** há bibliotecas gráficas externas (D3, Cytoscape, Vis.js, Three.js, WebGL);
- **Não** há simulação física contínua nem loops `requestAnimationFrame` em repouso (`0%` de uso de CPU quando ocioso).

---

## 2. Algoritmo de Construção do Subgrafo Local (`knowledge_service.get_knowledge_graph`)

### 2.1. Parâmetros e Tetos Rígidos
| Parâmetro | Padrão | Limite Mínimo / Máximo | Semântica |
| :--- | :---: | :---: | :--- |
| `entity_type` | Obrigatório | `concept`, `reflection`, `highlight`, `note`, `book` | Tipo da entidade central (`hop = 0`) |
| `entity_id` | Obrigatório | `1..128` caracteres | Identificador da entidade central |
| `depth` | `1` | `1` ou `2` | Profundidade da busca em largura (BFS) |
| `max_nodes` | `40` | `1..80` (`HARD_MAX_GRAPH_NODES`) | Número máximo total de nós no subgrafo (**incluindo o nó central**) |
| `max_edges` | `120` | `1..160` (`HARD_MAX_GRAPH_EDGES`) | Número máximo de arestas direcionadas no subgrafo |
| `entity_types` | Todos os 5 | Lista CSV opcional | Filtra os tipos de nós vizinhos incluídos |
| `relation_types` | Todas as 8 | Lista CSV opcional | Filtra os tipos de relação semântica percorridos |

### 2.2. Resolução em Lote (Zero N+1) e Prevenção de Ciclos
1. **Validação da Entidade Central**: O serviço valida primeiramente o acesso à entidade central com `resolve_knowledge_entity(..., for_link_endpoint=False)`. Caso a entidade não exista ou pertença a outro usuário/fonte inativa, retorna `HTTP 404 Not Found` (anti-enumeração).
2. **Salto 1 (`hop = 1`)**: Consulta `knowledge_links` onde `user_id = :uid` e a entidade central figura como origem ou destino, respeitando os índices compostos `idx_klinks_user_source`, `idx_klinks_user_target` e `idx_klinks_user_rel`.
3. **Hidratação em Lote (`_batch_resolve_graph_entities`)**: Agrupa todos os identificadores por tipo de entidade e executa no máximo 1 consulta SQL por tipo (`concepts`, `reflections`, `books`, `highlights`, `notes`), sem jamais carregar `body_markdown` integral das reflexões.
4. **Salto 2 (`hop = 2`)**: Quando `depth = 2`, expande exclusivamente a partir dos vizinhos de 1º salto que estejam **disponíveis** (`available = True`), em uma única consulta SQL agregada. Arestas já visitadas no 1º salto são ignoradas e ciclos (`A -> B -> C -> A`) são resolvidos por conjunto de chaves visitadas (`node_hop` e `selected_link_rows`).
5. **Truncamento Determinístico**: Se novos nós ou arestas elegíveis excederem `max_nodes` ou `max_edges`, a resposta define `truncated = true` e popula `truncation_reasons` (`["max_nodes"]`, `["max_edges"]`).

---

## 3. Proteção de Referências Indisponíveis no Grafo

Quando uma conexão pessoal do usuário aponta para uma entidade que foi removida, teve visibilidade pública revogada (`public -> private`) ou cujo livro/fonte foi desativado:
- O nó recebe o sentinela neutro `_unavailable_entity_sentinel`:
  - `available: false`, `status: "unavailable"`, `label: "Referência indisponível"`, `type_prefix: "[Indisponível]"`;
  - `title: null`, `excerpt: null`, `book_id: null`, `book_title: null`, `page: null`, `epub_cfi: null`;
  - `expandable: false`, `navigable: false`.
- Nós indisponíveis **nunca** são expandidos no 2º salto e **nunca** oferecem botões de abertura ou recentralização na interface.

---

## 4. Layout Radial SVG Determinístico e Distinção Visual Acessível

No frontend (`static/js/knowledge.js`), `computeDeterministicRadialLayout` calcula coordenadas `(x, y)` puramente trigonométricas:
- **Centro (`hop = 0`)**: posicionado em `(width / 2, height / 2)` com destaque de borda.
- **1º Salto (`hop = 1`)**: distribuído na órbita interna (`r1`), com escalonamento radial automático em 2 ou 3 sub-raios quando há alta densidade de vizinhos (`>= 14` ou `>= 24` nós) para evitar colisão de rótulos.
- **2º Salto (`hop = 2`)**: distribuído na órbita externa (`r2`), ordenado pelo ângulo médio dos seus vizinhos de 1º salto para minimizar cruzamentos de arestas.

### Distinção Não Dependente Exclusivamente de Cor
Cada tipo de entidade possui simultaneamente **prefixo textual** e **forma geométrica SVG distinta**:
- `[C]` **Conceito**: Círculo (`<circle>`)
- `[R]` **Reflexão**: Retângulo arredondado horizontal (`<rect rx="8">`)
- `[L]` **Livro**: Retângulo vertical (`<rect rx="3">`)
- `[D]` **Destaque**: Losango (`<polygon>`)
- `[N]` **Nota**: Hexágono (`<polygon>`)
- `[Indisponível]` **Referência indisponível**: Círculo com contorno tracejado (`stroke-dasharray: 4 3`) e símbolo `!`
- **Arestas**: Linha contínua para o 1º salto (`atlas-edge-solid`), linha tracejada para o 2º salto (`atlas-edge-dashed`) e seta direcional (`marker-end="url(#atlas-graph-arrow)"`). Quando existem múltiplas relações entre o mesmo par de nós, as curvas quadráticas (`Q`) recebem deslocamento normal determinístico.

---

## 5. Alternativa Textual Acessível Obrigatória (`accessible_text_list`)

O botão `"Lista textual acessível"` (`#atlas-graph-view-list`) alterna o canvas SVG para a região `#atlas-graph-text-view`, que renderiza `accessible_text_list`:
- Representa **exatamente o mesmo subconjunto autorizado e filtrado** do subgrafo SVG;
- Exibe salto (`1º salto` / `2º salto`), origem com prefixo de tipo, rótulo de relação direcionada, destino com prefixo de tipo e justificativa;
- Permite abrir a entidade correspondente ou recentralizar o mapa diretamente por teclado (`Tab` / `Enter`).
