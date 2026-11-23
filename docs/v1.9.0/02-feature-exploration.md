# Exploração de Funcionalidades e Alternativas de Escopo — Lectoria v1.9.0

- **Versão Planejada**: `v1.9.0 — Conhecimento Conectado` (Seção principal oficial: **Atlas**)
- **Natureza do Documento**: Avaliação técnica e de produto das hipóteses funcionais, comparação de alternativas, análise de custos e comparação entre os Escopos A, B e C.
- **Status Consolidado (Fase 1)**: Todas as 11 decisões de produto (`KD-001` a `KD-011`) foram deliberadas e **aprovadas pelo proprietário** em `09-owner-decisions.md` (`ALL_DECISIONS_APPROVED`).

---

## 1. Exploração Detalhada das Hipóteses de Produto

### 1.1. Conceitos (`Concepts`)

#### Hipótese de Produto
Permitir que o usuário crie e cultive entidades conceituais transversais que não pertencem a um único livro (exemplos: *Alienação*, *Modernidade*, *Poder disciplinar*, *Técnica*, *Capital simbólico*, *Materialismo histórico*, *Continuidade uniforme*).

Atributos potenciais de um conceito:
- Identificador estável (`id` UUID v4).
- Usuário proprietário (`user_id`).
- Nome canônico (`name`) e nome normalizado para busca/unicidade por usuário.
- Descrição ou definição autoral (`description`, com suporte a texto/Markdown leve).
- Etiquetas leves (`tags`, para agrupamento rápido como `filosofia`, `sociologia`, `cálculo`).
- Datas de criação e atualização (`created_at`, `updated_at`).
- Associações com destaques (`highlights`), notas (`notes`), livros (`books`), reflexões (`reflections`) e outros conceitos.

#### Análise Crítica: Entidade Própria vs. Extensão da Taxonomia Existente
- **Fato verificado no código (`taxonomy_service.py`, `database.py:625-655, 1039-1145`)**:
  - As tabelas `categories` e `subjects` existentes não possuem `user_id`: são estruturas **globais da instância** voltadas à catalogação de livros inteiros (`book_categories` e `book_subjects`).
  - Além disso, `subjects` é reescrita automaticamente por `sync_book_taxonomy()` durante varreduras e enriquecimento bibliográfico.
- **Avaliação**:
  - Reutilizar `subjects` para Conceitos criaria colisão entre usuários diferentes na mesma instância, impediria definições autorais privadas e misturaria catalogação bibliográfica com estudo analítico de passagens.
  - Portanto, **Conceitos devem constituir uma entidade própria (`concepts`) isolada por `user_id`**.

#### Diferenciação Semântica (Prevenção de Duplicação)

| Estrutura | Escopo de Posse | Granularidade Alvo | Papel Semântico Claro |
| :--- | :--- | :--- | :--- |
| **Categoria / Assunto (`categories`, `subjects`)** | Global da instância | Livro inteiro (`books.id`) | Classificação catalográfica de estante (ex.: *Filosofia > Fenomenologia*). |
| **Coleção (`collections`)** | Privada por usuário | Livro inteiro (`books.id`) | Agrupamento ou lista pessoal de obras/trilha de estudo (ex.: *Bibliografia do TCC*). |
| **Conceito (`concepts` — Novo)** | Privado por padrão (`user_id`) | Trecho (`highlight`), Nota (`note`), Reflexão ou Livro | Ideia, termo teórico, definição ou tema intelectual investigado através de várias obras (ex.: *Técnica*). |
| **Etiqueta de Conhecimento (`tags`)** | Privada por usuário | Atributo leve em Conceitos e Reflexões | Rótulo curto de organização transversal sem descrição própria (ex.: `#revisar`, `#aula-04`). |

- **Custo de implementação**: Baixo a médio.
- **Decisão associada**: `KD-002` (`APPROVED` — Alternativa 1: Conceitos como entidade própria, privada e isolada por usuário).

---

### 1.2. Conexões entre Anotações (`Knowledge Links`)

#### Hipótese de Produto
Permitir que o leitor estabeleça ligações explícitas e justificadas entre elementos de seu estudo — especialmente entre anotações de livros diferentes (exemplo: relacionar uma nota em *Vigiar e Punir* a um destaque em *A Sociedade do Espetáculo*), bem como entre conceitos e reflexões.

#### Vocabulário de Relações Avaliado
O conjunto de tipos de relação proposto:
1. `complements` (*Complementa*): uma passagem amplia ou acrescenta perspectiva à outra.
2. `contradicts` (*Contradiz*): uma passagem diverge ou refuta a tese da outra.
3. `exemplifies` (*Exemplifica*): um caso concreto ilustra uma definição ou tese geral.
4. `grounds` (*Fundamenta*): fornece a base teórica, premissa ou demonstração para outra nota/reflexão.
5. `develops` (*Desenvolve*): desdobra cronológica ou logicamente um argumento anterior.
6. `references` (*Referencia*): faz alusão direta ou citação cruzada.
7. `questions` (*Questiona*): levanta objeção, problema ou dúvida sobre o elemento alvo.
8. `relates_to` (*Relaciona-se com*): vínculo geral não-tipado ou neutro (padrão quando o usuário não deseja especificar nuance).

#### Alternativas de Modelagem de Tipos e Direcionalidade
- **Alternativa 1 (Tipos fixos com padrão neutro `relates_to` — Recomendada)**:
  - Conjunto fechado de 8 verbos canônicos no banco (`CHECK`), sendo `relates_to` o padrão pré-selecionado para não exigir escolha obrigatória em conexões rápidas.
  - **Vantagens**: consistência visual nas etiquetas, filtros determinísticos no grafo/caderno, prevenção de proliferação caótica de verbos sinônimos, simplicidade de interface.
- **Alternativa 2 (Tipos totalmente livres digitados pelo usuário)**:
  - **Desvantagens**: fragmentação ("contradiz" vs. "opõe-se" vs. "refuta"), maior complexidade de filtro e UI.
- **Direcionalidade**:
  - Relações como *Exemplifica*, *Fundamenta*, *Desenvolve* e *Questiona* são intrinsecamente **direcionadas** (`origem → destino`), enquanto *Relaciona-se com* e *Contradiz* têm leitura simétrica.
  - **Solução técnica**: armazenar toda conexão com `source_*` e `target_*` bem definidos, mas sempre consultar e exibir **ambas as direções** (na origem como relação de saída, e no destino como relação recebida/backlink inverso: ex. *"Fundamenta X"* na origem e *"Fundamentado por Y"* no destino).
- **Justificativa textual (`rationale` / `note`)**:
  - Campo opcional de texto (`0..2000` caracteres) onde o leitor explica *por que* conectou aqueles dois trechos. Esse texto é frequentemente o embrião de uma futura reflexão ou ensaio.
- **Custo de implementação**: Médio.
- **Decisão associada**: `KD-003` (`APPROVED` — Alternativa 1: Oito tipos fixos de relação direcionada com justificativa opcional).

---

### 1.3. Referências Internas e Backlinks

#### Hipótese de Produto
Ao inspecionar qualquer anotação, conceito, reflexão ou livro, o usuário deve descobrir imediatamente:
- Quais elementos aquele item referencia (saídas).
- Quais elementos fazem referência a ele (**backlinks** / entradas).
- Quais livros estão indiretamente conectados àquela ideia.
- Em quais reflexões autorais aquele destaque ou nota foi utilizado.

#### Comparação: Referências Estruturadas por ID vs. Sintaxe Textual Estilo Wikilinks (`[[...]]`)

| Critério | A. Referências Estruturadas por ID Estável (UI assistida) | B. Sintaxe Textual Pura `[[Nome]]` | C. Abordagem Híbrida (Vínculos por ID + Autocompletar `@` ou `[[` no editor) |
| :--- | :--- | :--- | :--- |
| **Resistência a renomeações** | **Total**: renomear um conceito ou editar uma nota nunca quebra o link. | **Frágil**: renomear um conceito exige reescrever textos em dezenas de notas. | **Total**: o editor insere ou sincroniza o ID estável na tabela relacional. |
| **Referência a destaques sem título** | **Natural**: destaques e notas não têm "título único", mas têm UUID e trecho. | **Ruim**: impossível digitar `[[...]]` para um destaque de 3 linhas sem título. | **Natural**: seletor busca pelo trecho destacado ou livro e grava o UUID. |
| **Desempenho no SQLite** | **Excelente**: consulta indexada `O(log N)` na tabela de links. | **Lento**: exige re-parsing constante de strings e regex. | **Excelente**: links extraídos no momento do salvamento e consultados por índice. |
| **Curva de aprendizado** | **Imediata**: botões *"Vincular Conceito"* e *"Inserir Citação/Referência"*. | Exige memorizar sintaxe de wiki. | Atende tanto usuários visuais quanto quem gosta de atalhos de teclado. |

- **Recomendação técnica**: Adotar **referências estruturadas por identificadores estáveis (UUID)** como fonte única de verdade no banco SQLite, acompanhadas no editor de reflexões por um botão/atalho rápido (*"Inserir referência a trecho/conceito"*) que insere um bloco ou token legível e registra a relação estruturada no banco. Não impor sintaxe obrigatória do Obsidian.
- **Custo de implementação**: Médio.
- **Decisão associada**: `KD-004` (`APPROVED` — Alternativa 1: Referências por UUIDs estáveis em `knowledge_links` com inserção assistida pela interface).

---

### 1.4. Reflexões Autorais (`Reflections`)

#### Hipótese de Produto
Criar uma entidade para textos autorais mais longos ou transversais que **não pertencem a uma única página de um único livro**: fichamentos, sínteses comparativas entre dois autores, hipóteses de pesquisa, rascunhos de ensaios ou resumos de capítulos.

#### Análise Crítica: Estender `notes` vs. Entidade Independente `reflections`
- **Fatos verificados no código (`database.py:1490-1506`, `annotations_service.py:483-556`)**:
  - A tabela `notes` exige `book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE` e `page INTEGER NOT NULL` (`page >= 1`).
  - Se um livro for removido de uma fonte, `ON DELETE CASCADE` excluiria notas presas a ele.
  - Uma reflexão que compara 3 livros (ex.: *Ellul, Heidegger e Simondon sobre a Técnica*) não tem um único `book_id` nem uma única `page`.
  - Além disso, uma reflexão possui **título próprio** (`title`), enquanto uma nota de página é apenas um comentário curto ancorado a um trecho.
- **Avaliação**:
  - **Reflexões devem ser uma entidade independente (`reflections`)**, com `title`, `body_markdown` (Markdown seguro), `user_id`, opcionalmente vinculada a zero, um ou vários livros, conceitos, destaques e notas por meio das relações de conhecimento.
  - **Prevenção de complexidade excessiva no editor**:
    - O editor de reflexões deve ser um campo de escrita limpo e tipograficamente confortável (Markdown simples: títulos, negrito, itálico, listas, blocos de citação `>` e botão *"Citar trecho do caderno / Vincular conceito"*), com alternância simples *Editar / Ler*, **sem** barra WYSIWYG pesada, sem tabelas complexas e sem blocos arrastáveis.
- **Custo de implementação**: Médio.
- **Decisão associada**: `KD-005` (`APPROVED` — Alternativa 1: Reflexões autorais como entidade independente com editor Markdown seguro).

---

### 1.5. Mapa de Conhecimento (`Knowledge Map / Graph`)

#### Hipótese de Produto
Visualização gráfica interativa das conexões entre **Conceitos**, **Livros**, **Destaques/Notas** e **Reflexões**, permitindo explorar a vizinhança intelectual de uma ideia e saltar para os textos originais.

#### Comparação Técnica: SVG Nativo vs. HTML5 Canvas vs. Bibliotecas Externas

| Tecnologia | Consumo de RAM / Bundle | Acessibilidade DOM | Limite Fluido em Mobile | Avaliação para Lectoria |
| :--- | :--- | :--- | :--- | :--- |
| **Bibliotecas externas (Cytoscape, D3, Vis.js, Sigma)** | Alto (`150 KB` a `800 KB` JS + overhead de objetos em memória) | Baixa (Canvas opaco) | Variável | **Descartado por padrão**: viola a diretriz de zero dependências pesadas quando um grafo focado resolve com muito menos código. |
| **SVG Nativo (Vanilla JS)** | Muito baixo (zero dependências, ~300 linhas JS) | **Alta**: cada nó `<g>` e aresta `<line>` é inspecionável, estilizado com variáveis CSS (`--bg`, `--accent`) e navegável por `tabindex`/`aria-label`. | Até ~120–150 nós simultâneos com excelente fluidez. | **Ideal para Grafo Local / Centrado** (vizinhança de 1 ou 2 saltos com limite de 60–100 nós e expansão progressiva ao clicar). |
| **HTML5 Canvas 2D (Vanilla JS)** | Muito baixo em memória para milhares de pontos | Baixa (exige hit-testing manual e árvore paralela de acessibilidade). | Até ~1.000+ nós. | Útil apenas se houver necessidade de um Grafo Global massivo (> 200 nós simultâneos). |

#### Diretrizes para um Grafo Útil (e Não Decorativo)
1. **Grafo Local Centrado por Padrão (Vizinhança de 1–2 Saltos)**:
   - Grafos globais "bola de pelos" (*hairball*) com milhares de nós são ilegíveis em telas pequenas, consomem CPU com simulação física contínua e pouco ajudam no estudo real.
   - Um **Grafo Contextual / Local** centrado no Conceito, Livro ou Reflexão atual (mostrando suas conexões diretas de profundidade 1 e 2, com limite de segurança de ex.: 80 nós visíveis e expansão progressiva ao clicar em um nó) é legível, instantâneo em celulares e consome memória desprezível.
2. **Simulação de Layout com Parada Determinística (Zero CPU Ociosa)**:
   - O cálculo de posições (layout radial hierárquico ou *force-directed* leve com 80–120 iterações síncronas/curtas) deve **convergir e parar imediatamente**, consumindo **0% de CPU** após renderizado (respeitando a regra de *Zero Ociosidade Forçada* do `AGENTS.md`).
3. **Alternativa Textual e Navegável Obrigatória ("Visão Estruturada / Sumário de Conexões")**:
   - Todo usuário (inclusive em leitores de tela ou dispositivos modestos) poderá alternar entre **Mapa Visual** e **Lista Estruturada de Conexões**, que apresenta os mesmos vínculos agrupados por tipo de relação (*Fundamenta*, *Contradiz*, *Conceitos associados*, *Obras citadas*, *Backlinks*).
- **Custo de implementação**: Médio (Grafo Local SVG + Alternativa Textual) a Alto (Grafo Global).
- **Decisão associada**: `KD-006` (`APPROVED` — Alternativa 1: Grafo local em SVG nativo com 1–2 saltos e limite de 80 nós + lista textual estruturada acessível).

---

### 1.6. Trilhas Temáticas vs. Coleções Existentes

#### Hipótese de Produto
Organizar roteiros ou dossiês de estudo sobre um assunto (ex.: *Filosofia da Técnica*, *Fundamentos da Matemática*), reunindo livros, capítulos, anotações, conceitos e reflexões.

#### Avaliação Crítica contra o Sistema de Coleções Existente (`collections_service.py`)
- **Fatos verificados no código (`database.py:288-326`, `collections_service.py`)**:
  - A tabela `collections` já pertence ao usuário (`user_id`), tem `name` e `description`, aparece na aba de Descoberta (`get_discovery_shelves` seção 9) e nos filtros da Biblioteca.
  - Entretanto, `collections` hoje vincula apenas `collection_books(collection_id, book_id)`.
- **Análise de Alternativas**:
  - **Opção 1 (Não criar subsistema paralelo; estender `collections` ou usar Conceitos + Reflexões)**:
    - Criar um quarto mecanismo de agrupamento (além de *Categorias*, *Coleções* e *Conceitos*) aumentaria a carga cognitiva do usuário (*"Devo criar uma Coleção, um Conceito ou uma Trilha?"*).
    - Na prática, um **Conceito** já agrega anotações, livros e reflexões sobre um tema, enquanto uma **Coleção** já agrupa os livros de um plano de estudos.
    - Se o usuário desejar ordenar livros ou anexar conceitos/reflexões a um plano de estudos, uma **extensão simples de `collections`** (adicionando `sort_order` e nota/objetivo de estudo em `collection_books`, ou vinculando conceitos à coleção) resolve 100% do caso de uso sem criar um subsistema redundante.
  - **Opção 2 (Criar entidade separada `study_trails` / Trilhas Temáticas)**:
    - Só se justifica se houver necessidade de etapas sequenciais com check-list próprio (semelhante a um curso individual). Mesmo assim, pode ser adiada para `v1.9.x` ou absorvida por `collections`.
- **Recomendação técnica**: **Não criar um novo subsistema separado de Trilhas Temáticas na v1.9.0**; cumprir essa função combinando **Conceitos** (para o eixo intelectual) e as **Coleções existentes**.
- **Decisão associada**: `KD-007` (`APPROVED` — Alternativa 1: Reutilizar conceitos e coleções existentes; não criar `study_trails` nem subsistemas redundantes).

---

### 1.7. Pesquisa Integrada (`Unified Knowledge Search`)

#### Hipótese de Produto
Permitir pesquisar em um único ponto:
- Livros (título, autor, assunto — já em `books_fts`).
- Destaques (`highlights.selected_text`, `text_context`, `chapter_label`).
- Notas (`notes.content`, `chapter_label`).
- Conceitos (`concepts.name`, `description`, `tags`).
- Reflexões (`reflections.title`, `content`, `tags`).
- Justificativas de conexões (`knowledge_links.rationale`).

#### Viabilidade Técnica com SQLite FTS5 e Língua Portuguesa
1. **Disponibilidade do FTS5**:
   - Verificado em `database.py:698`: o ambiente Python 3.12 / SQLite já compila e utiliza `fts5` nativamente.
2. **Acentos e Normalização em Português (`unicode61 remove_diacritics 2`)**:
   - O tokenizador padrão `unicode61` do FTS5 no SQLite 3 suporta a diretiva `tokenize='unicode61 remove_diacritics 2'`, que normaliza automaticamente caracteres acentuados do português (`"técnica"` encontra `"tecnica"` e vice-versa; `"alienação"` encontra `"alienacao"`), sem precisar de extensões C externas ou Elasticsearch.
3. **Filtragem Estrita por Permissões (Segurança no Índice)**:
   - Um índice FTS5 **nunca** deve retornar linhas diretamente sem validação relacional.
   - Na arquitetura proposta, a consulta FTS5 faz `JOIN` obrigatório com as tabelas base (ou filtra por `owner_user_id = :viewer_id` para conceitos/reflexões privadas e `(user_id = :viewer_id OR visibility = 'public')` combinado com livro ativo `b.is_missing = 0 AND s.is_active = 1` para anotações).
- **Custo de implementação**: Médio.
- **Decisão associada**: `KD-008` (`APPROVED` — Alternativa 1: SQLite FTS5 `knowledge_fts` com `unicode61 remove_diacritics 2` integrado na migração v13, validação relacional em tempo de leitura e fallback documentado).

---

### 1.8. Exportação e Portabilidade

#### Hipótese de Produto
Permitir ao usuário exportar sua base de conhecimento pessoal em formatos abertos e duráveis:
1. **Pacote ZIP de Documentos Markdown Interligados (`.zip`)**:
   - Estrutura de pastas limpa: `livros/`, `conceitos/`, `reflexoes/`, `indice.md`.
   - Cada arquivo Markdown inclui cabeçalho YAML (*frontmatter*) com metadados (`id`, `type`, `created_at`, `tags`, `book`, `page`, `epub_cfi`) e links relativos padrão Markdown (`[Conceito](../conceitos/tecnica.md)`) ou compatibilidade opcional com wikilinks (`[[Técnica]]`) para abertura direta no Obsidian/Logseq/Zettlr.
2. **JSON Estruturado Completo (`.json`)**:
   - Evolução do formato `lectoria_study_export` (v2.0) contendo livros anotados, destaques, notas, desenhos, marcadores, conceitos, reflexões e arestas de conexão com seus UUIDs preservados.
3. **CSV Tabular (`.csv`)**:
   - Exportação tabular de anotações, conceitos e relações para análise em planilhas ou scripts Python/R.
- **Regra absoluta de privacidade**: A exportação pessoal inclui estritamente os itens pertencentes ao usuário autenticado (`user_id == current_user.id`). Caso uma conexão pessoal aponte para uma anotação pública de outro leitor que ainda esteja pública e acessível no momento da exportação, ela pode ser identificada claramente como citação pública de terceiro (ou omitida caso o usuário escolha exportar apenas autoria própria), mas **jamais** inclui qualquer dado privado ou revogado de terceiros.
- **Custo de implementação**: Médio (streaming em memória/arquivo temporário limitado para não pressionar os 4 GB de RAM da VPS).
- **Decisão associada**: `KD-009` (`APPROVED` — Alternativa 1: Pacote ZIP Markdown interligado + JSON estruturado v2.0 + CSV tabular + opção de wikilinks Obsidian).

---

## 2. Avaliação Comparativa de Escopos de Produto (Seção 11)

Conforme solicitado, comparamos os três escopos possíveis para a **Lectoria v1.9.0**:

- **Escopo A — Minimalista**: Conceitos, relações entre anotações e backlinks.
- **Escopo B — Equilibrado**: Conceitos, relações entre anotações, backlinks, reflexões autorais, pesquisa integrada (FTS5) e grafo local centrado (com alternativa textual).
- **Escopo C — Expandido**: Todas as funcionalidades do Escopo B, além de grafo global completo, subsistema próprio de trilhas temáticas avançadas e exportação multi-formato abrangente com múltiplos perfis de compatibilidade.

### 2.1. Matriz Comparativa dos Escopos

| Critério | Escopo A — Minimalista | Escopo B — Equilibrado (Recomendado) | Escopo C — Expandido |
| :--- | :--- | :--- | :--- |
| **Valor intelectual para o usuário** | **Médio**: conecta notas e conceitos, mas não oferece onde escrever sínteses multi-livro (Reflexões) nem busca transversal rápida. | **Muito Alto**: fecha o ciclo completo de estudo (*ler → destacar/anotar → conceituar → conectar → sintetizar em reflexão → pesquisar e explorar*). | **Alto, porém com retorno marginal decrescente**: grafo global e trilhas separadas adicionam complexidade visual e cognitiva. |
| **Complexidade de implementação** | Baixa (3–4 fases curtas). | **Moderada e bem modularizada** (implementável com segurança em SQLite + Vanilla JS). | Alta (exige motor de física/Canvas para grafo global e novo subsistema de trilhas). |
| **Riscos técnicos** | Muito baixos. | **Baixos e controlados** (grafo limitado à vizinhança local em SVG; FTS5 nativo do SQLite). | Médios/Altos (degradação de performance do grafo global em mobile, duplicação conceitual entre Coleções e Trilhas). |
| **Dependências externas** | Zero. | **Zero** (100% Python stdlib/FastAPI + SQLite FTS5 + Vanilla JS/SVG). | Zero ou risco de tentação por bibliotecas gráficas. |
| **Impacto sobre a arquitetura** | Aditivo mínimo (`concepts` + `knowledge_links`). | **Coeso**: adiciona `knowledge_service.py`, 3 tabelas enxutas + índice FTS5 e uma seção limpa na UI. | Elevado: aumenta significativamente a superfície de rotas, tabelas e estados de UI. |
| **Manutenção futura** | Simples. | **Sustentável**: modelo de dados enxuto e ortogonal às tabelas existentes. | Mais custosa devido à sobreposição entre Trilhas, Coleções e Conceitos. |
| **Segurança e Privacidade** | Superfície pequena (validação de vínculos entre anotações e conceitos). | **Bem delimitada**: regras uniformes de autorização para Conceitos, Reflexões, Links, Backlinks e FTS5. | Maior superfície de vazamento (agregações de grafo global e permissões mistas em trilhas). |
| **Desempenho (VPS 4 GB RAM e Mobile)** | Excelente (< 5 ms por consulta). | **Excelente**: consultas indexadas, FTS5 em milissegundos e Grafo Local limitado a <= 80 nós. | Risco de picos de memória/CPU no cliente mobile ao renderizar grafos globais com milhares de nós. |
| **Coerência com a identidade da Lectoria** | Parcial (falta o espaço de síntese autoral). | **Máxima**: preserva a sobriedade editorial e transforma leituras em conhecimento estruturado. | Risco de aproximar a interface de um software pesado de gestão de projetos/grafos. |

---

### 2.2. Escopo Aprovado pelo Proprietário (`KD-001: APPROVED`)

- **Decisão Consolidada**: **Alternativa 2 — Escopo B (Equilibrado)**, incluindo Conceitos, Conexões Tipadas, Referências Internas por UUID, Backlinks, Reflexões Autorais, Pesquisa Integrada FTS5, Grafo Local em SVG nativo com Lista Estruturada e Exportação Aberta (ZIP Markdown, JSON v2.0, CSV e opção Obsidian), reunidos na nova seção principal **Atlas** (`KD-010: APPROVED`).
- **Justificativa**:
  1. Sem **Reflexões Autorais** e sem **Pesquisa Integrada**, o usuário do Escopo A criaria conceitos e conexões, mas continuaria sem um espaço para redigir fichamentos/sínteses multi-livro e sem conseguir localizar ideias rapidamente através do acervo.
  2. Por outro lado, o **Grafo Local Centrado** (com alternativa textual estruturada) entrega 95% do valor intelectual de descoberta de relações sem os custos de memória, ilegibilidade mobile e complexidade do Grafo Global do Escopo C.

### 2.3. Funcionalidades Recomendadas para Adiamento (`v1.9.x` ou `v2.0`)

1. **Grafo Global com milhares de nós simultâneos em Canvas/WebGL** (adiar para `v1.9.x`/`v2.0`; na `v1.9.0`, priorizar o Grafo Local de vizinhança com expansão progressiva nó a nó e filtro de visão geral dos principais conceitos).
2. **Subsistema independente de Trilhas Temáticas com etapas sequenciais e progresso próprio** (adiar ou avaliar após observar o uso real de Conceitos + Coleções).
3. **Compartilhamento colaborativo de Conceitos e Reflexões em Clubes de Leitura (`visibility = 'club'` ou `'public'`)** (na `v1.9.0`, manter Conceitos, Reflexões e Conexões estritamente privados por usuário por padrão, permitindo apenas que apontem para anotações públicas existentes; compartilhamento social de redes conceituais fica para `v2.0`).
4. **Sugestão heurística/estatística automática de conexões (ex.: TF-IDF em background)** (adiar; na `v1.9.0`, todas as conexões são explícitas e criadas pelo leitor).
