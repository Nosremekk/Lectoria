# 09 — Registro das Decisões do Proprietário (Owner Decisions Log — v1.9.0)

> **Fase**: 1 (Consolidação das Decisões, Fundação do Domínio e Migração SQLite v13)  
> **Branch**: `feature/v1.9.0-connected-knowledge`  
> **Versão Base**: `v1.8.0` (Schema SQLite v12 → v13 em implementação na Fase 1)  
> **Data de Consolidação**: 2026-11-18  
> **Estado Geral**: `ALL_DECISIONS_APPROVED` (11 de 11 decisões `APPROVED`)

---

## 1. Objetivo deste Registro

Este documento é a **fonte única de verdade** para as decisões de produto, escopo, modelo de dados, experiência do usuário e arquitetura da **Lectoria v1.9.0 — Conhecimento Conectado (Connected Knowledge)**.

Todas as 11 decisões (`KD-001` a `KD-011`) foram deliberadas e **aprovadas explicitamente pelo proprietário** antes do início da codificação da Fase 1:
- **Lote 1 (Fase 0.1)**: `KD-001`, `KD-002`, `KD-003`, `KD-004`, `KD-005` e `KD-011`.
- **Lote 2 (Autorização da Fase 1)**: `KD-006`, `KD-007`, `KD-008`, `KD-009` e `KD-010`.

---

## 2. Quadro Resumo das 11 Decisões

| ID | Título da Decisão | Prioridade | Alternativa Aprovada pelo Proprietário | Estado Atual |
| :--- | :--- | :--- | :--- | :--- |
| **KD-001** | Escopo Funcional da Release v1.9.0 | **Imprescindível** | **Alternativa 2 — Escopo B (Equilibrado: Conceitos, Conexões, Referências, Backlinks, Reflexões, Pesquisa Integrada, Grafo Local e Exportação)** | `APPROVED` |
| **KD-002** | Modelo de Conceitos e Isolamento por Usuário | **Imprescindível** | **Alternativa 1 — Conceitos como Entidade Própria, Privada e Isolada por Usuário (`concepts`)** | `APPROVED` |
| **KD-003** | Vocabulário de Tipos de Conexão Semântica | **Imprescindível** | **Alternativa 1 — Oito Tipos Fixos de Relação Direcionada com Justificativa Opcional** | `APPROVED` |
| **KD-004** | Sintaxe e Persistência de Referências Internas e Backlinks | **Imprescindível** | **Alternativa 1 — Referências por UUIDs Estáveis em `knowledge_links` com Inserção Assistida pela Interface** | `APPROVED` |
| **KD-005** | Modelo de Reflexões Autorais e Nível de Formatação | **Desejável** | **Alternativa 1 — Reflexões Autorais como Entidade Independente (`reflections`) com Editor Markdown Seguro** | `APPROVED` |
| **KD-006** | Formato da Visualização de Conexões (Grafo Local vs. Lista Estruturada) | **Desejável** | **Alternativa 1 — Grafo Local em SVG Nativo (1–2 saltos, limite de 80 nós, expansão progressiva) + Lista Textual Estruturada Acessível** | `APPROVED` |
| **KD-007** | Modelo de Trilhas de Estudo vs. Coleções Existentes | **Opcional** | **Alternativa 1 — Reutilizar Conceitos e Coleções Existentes; Não Criar `study_trails` nem Subsistemas Redundantes** | `APPROVED` |
| **KD-008** | Motor de Busca Unificada (SQLite FTS5 vs. `LIKE`) | **Desejável** | **Alternativa 1 — SQLite FTS5 (`knowledge_fts` com `unicode61 remove_diacritics 2`) Integrado na Migração v13 com Validação Relacional e Fallback Documentado** | `APPROVED` |
| **KD-009** | Formatos de Exportação Aberta e Interoperabilidade | **Imprescindível** | **Alternativa 1 — Pacote ZIP Markdown Interligado + JSON Estruturado v2.0 + CSV Tabular + Opção de Wikilinks Obsidian** | `APPROVED` |
| **KD-010** | Nomenclatura da Nova Seção na Navegação Principal | **Desejável** | **Alternativa 5 — O Nome Oficial da Nova Seção Principal Será `Atlas` (Mantendo `knowledge` nos Identificadores Técnicos)** | `APPROVED` |
| **KD-011** | Estratégia de Migração do Schema SQLite (`v12 → v13`) | **Imprescindível** | **Alternativa 1 — Migração Incremental Aditiva do Schema SQLite `v12 → v13` (`_apply_migration_v13`)** | `APPROVED` |

---

## 3. Registro Integral das 11 Decisões Aprovadas (`KD-001` a `KD-011`)

### KD-001 — Escopo Funcional da Release v1.9.0
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 2 — Escopo B (Equilibrado)**: Incluir na v1.9.0:
    1. Conceitos pessoais (`concepts`).
    2. Conexões semânticas tipadas (`knowledge_links`) e backlinks bidirecionais.
    3. Referências internas assistidas a trechos, notas, livros, conceitos e reflexões.
    4. Reflexões autorais longas (`reflections`).
    5. Pesquisa integrada da memória de leitura (FTS5 com remoção de diacríticos).
    6. Visualização de conexões por grafo local em SVG nativo (1 ou 2 saltos, teto de 80 nós) acompanhado de lista estruturada acessível.
    7. Exportação aberta e portável (Markdown em ZIP, JSON v2.0, CSV e opção Obsidian).
- **Alternativas Descartadas**:
  - *Alternativa 1 — Escopo A (Essencial / Mínimo Viável)*.
  - *Alternativa 3 — Escopo C (Expandido / Ambicioso com grafo global e coautoria em tempo real)*.
- **Impacto Técnico, Visual e de Complexidade**:
  - Adiciona 3 tabelas relacionais (`concepts`, `reflections`, `knowledge_links`) e 1 tabela virtual (`knowledge_fts`) na migração v13, módulo `knowledge_service.py`, endpoints REST `/api/knowledge/*` (Fase 2), integração no leitor PDF/EPUB e Caderno de Estudos (Fase 3) e seção principal **Atlas** no shell da biblioteca (Fases 4–6).

---

### KD-002 — Modelo de Conceitos e Isolamento por Usuário
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Conceitos como Entidade Própria, Privada e Isolada por Usuário (`concepts`)**:
    - Cada usuário possui seu próprio vocabulário conceitual privado (`user_id`), com identidade UUID estável (`id`), nome (`name`), nome normalizado (`normalized_name` em Unicode NFC para unicidade case-insensitive por usuário), descrição autoral (`description`), marcadores (`tags_json`), cor (`color`), estado de arquivamento (`is_archived`) e carimbos de tempo (`created_at`, `updated_at`).
    - Totalmente independente das tabelas globais de catalogação de acervo (`categories` e `subjects`), que permanecem intactas.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Conceitos Globais Compartilhados entre Todos os Usuários*.
  - *Alternativa 3 — Conceitos com Visibilidade Dupla (`private` / `public`) na v1.9.0*.

---

### KD-003 — Vocabulário de Tipos de Conexão Semântica
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Oito Tipos Fixos de Relação Direcionada com Justificativa Opcional**:
    - Tipos canônicos validados por `CHECK` no SQLite e nos serviços:
      1. `complements` (*Complementa*)
      2. `contradicts` (*Contradiz*)
      3. `exemplifies` (*Exemplifica*)
      4. `grounds` (*Fundamenta*)
      5. `develops` (*Desenvolve*)
      6. `references` (*Referencia*)
      7. `questions` (*Questiona*)
      8. `relates_to` (*Relaciona-se com* — padrão neutro)
    - Campo opcional `rationale` (até 2.000 caracteres) para o usuário justificar o vínculo com suas próprias palavras.
    - Toda conexão é persistida uma única vez (`source → target`) e consultável tanto na direção de saída (*conexões de saída*) quanto na direção inversa (*backlinks / conexões de entrada*), sem duplicar arestas no banco.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Tipo Único Genérico (`linked_to`)*.
  - *Alternativa 3 — Tipos de Relação Totalmente Livres Digitados pelo Usuário*.

---

### KD-004 — Sintaxe e Persistência de Referências Internas e Backlinks
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Referências por UUIDs Estáveis em `knowledge_links` com Inserção Assistida pela Interface**:
    - A tabela `knowledge_links` é a **fonte única de verdade** para todas as conexões e referências entre entidades (`concept`, `reflection`, `highlight`, `note`, `book`).
    - As referências utilizam **UUIDs imutáveis**, garantindo que renomear um conceito ou o título de uma reflexão jamais quebre referências ou backlinks existentes.
    - Na interface (Fases 3 e 5), o usuário insere referências por meio de seletor assistido com busca instantânea, sem precisar memorizar ou digitar UUIDs manualmente.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Wikilinks Textuais Baseados Apenas no Nome (`[[Nome do Conceito]]`)*.
  - *Alternativa 3 — Apenas Vínculos de Rodapé sem Citação no Corpo da Reflexão*.

---

### KD-005 — Modelo de Reflexões Autorais e Nível de Formatação
- **Prioridade**: **Desejável**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Reflexões Autorais como Entidade Independente (`reflections`) com Editor Markdown Seguro**:
    - Entidade dedicada `reflections`, privada por `user_id`, separada das notas rápidas de margem (`notes`).
    - Possui `id` (UUID), `user_id`, `title` (1–300 caracteres), `body_markdown` (até 50.000 caracteres), `primary_book_id` opcional (`ON DELETE SET NULL`), `tags_json`, `is_pinned`, `is_archived`, `created_at` e `updated_at`.
    - O texto é armazenado como Markdown puro (`body_markdown`). A camada de persistência preserva o texto exato do autor (normalizado em Unicode NFC), enquanto a camada de visualização frontend (Fase 5) aplicará obrigatoriamente uma política estrita de segurança contra XSS, bloqueio de HTML bruto e filtragem de esquemas de URL (`javascript:`, `data:`, `vbscript:`).
- **Alternativas Descartadas**:
  - *Alternativa 2 — Reutilizar a Tabela `notes` com uma Flag `is_essay = 1`*.
  - *Alternativa 3 — Editor Rico Baseado em HTML Bruto (`contenteditable` / WYSIWYG pesado)*.

---

### KD-006 — Formato da Visualização de Conexões (Grafo Local vs. Lista Estruturada)
- **Prioridade**: **Desejável**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Grafo Local em SVG Nativo (1 ou 2 saltos, limite de 80 nós, expansão progressiva) + Lista Textual Estruturada Acessível**:
    - O mapa visual de conexões do **Atlas** será centrado na entidade em foco (conceito, reflexão, livro ou anotação), exibindo vizinhança local de 1 ou 2 saltos com teto rígido de **80 nós** e **160 arestas**, com expansão progressiva ao clicar em um nó e sem simulação física contínua em repouso.
    - Será sempre acompanhado de uma **Visão em Lista Estruturada** completa e acessível por teclado e leitores de tela (`role="list"`).
    - **Escopo de Fase**: O endpoint REST e o renderizador SVG serão implementados nas Fases 2 e 6; a Fase 1 fornece as primitivas de consulta de conexões de saída, conexões de entrada e backlinks.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Apenas Lista Estruturada Agrupada (Sem Grafo Visual na v1.9.0)*.
  - *Alternativa 3 — Grafo Global Completo com Biblioteca Externa (D3.js / Cytoscape.js)*.

---

### KD-007 — Modelo de Trilhas de Estudo vs. Coleções Existentes
- **Prioridade**: **Opcional**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Reutilizar Conceitos e Coleções Existentes; Não Criar `study_trails` nem Subsistemas Redundantes**:
    - As coleções pessoais (`collections` e `collection_items`) permanecem focadas no agrupamento de livros do acervo.
    - Os conceitos (`concepts`), reflexões (`reflections`) e vínculos (`knowledge_links`) organizam temas transversais que cruzam livros, capítulos e trechos.
    - Nenhuma tabela ou serviço `study_trails` será criado, evitando duplicação conceitual e mantendo o modelo enxuto.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Criar Entidade Dedicada `study_trails` e `study_trail_steps`*.
  - *Alternativa 3 — Converter `collection_items` em Tabela Polimórfica*.

---

### KD-008 — Motor de Busca Unificada (SQLite FTS5 vs. `LIKE`)
- **Prioridade**: **Desejável**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — SQLite FTS5 (`knowledge_fts` com `tokenize='unicode61 remove_diacritics 2'`) Integrado na Migração v13 com Validação Relacional e Fallback Documentado**:
    - A tabela virtual `knowledge_fts` é criada diretamente na migração `v12 → v13` (`_apply_migration_v13`), mantendo todas as estruturas persistentes da v1.9.0 consolidadas na versão 13 do schema.
    - Utiliza `tokenize='unicode61 remove_diacritics 2'` para pesquisa insensível a maiúsculas/minúsculas e acentuação em português (ex.: buscar `tecnica` encontra `técnica`; buscar `educacao` encontra `educação`; buscar `racionalizacao` encontra `racionalização`).
    - **Regra de Segurança (Dupla Barreira)**: O índice FTS5 funciona exclusivamente como **localizador de candidatos**. Todo candidato retornado pelo FTS5 passa obrigatoriamente pela validação de propriedade, visibilidade e acesso ao livro (`resolve_knowledge_entity` / tabelas relacionais originais) antes que qualquer título, trecho, contagem ou metadado seja retornado ao chamador.
    - **Política de Fallback**: Se o FTS5 estiver indisponível no binário SQLite do ambiente na hora da migração v13, a migração falha de forma explícita e atômica (com rollback para v12), não declarando o schema v13 concluído sem sua estrutura obrigatória. Adicionalmente, o serviço `knowledge_service.py` dispõe de busca auxiliar com normalização Unicode NFD em Python para consultas de seleção assistida e contingência.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Busca com `LIKE` + Normalização em Memória como Motor Principal*.

---

### KD-009 — Formatos de Exportação Aberta e Interoperabilidade
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Pacote ZIP Markdown Interligado + JSON Estruturado v2.0 + CSV Tabular + Opção de Wikilinks Obsidian**:
    - Planejados para a Fase 6:
      1. Pacote `.zip` contendo arquivos Markdown (`.md`) com *frontmatter* YAML para conceitos, reflexões e cadernos por livro, com opção de ativar wikilinks `[[...]]` compatíveis com Obsidian/Logseq.
      2. Exportação JSON estruturada (`schema_version: "2.0"`) com todas as entidades e arestas autorizadas.
      3. Exportação CSV tabular de conexões e anotações.
    - **Nota Técnica**: A exportação JSON/ZIP é uma ferramenta de portabilidade e interoperabilidade, não substituindo o backup transacional do banco (`backup_service.py`) enquanto não existir um importador validado. Nenhum exportador final é implementado na Fase 1.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Apenas JSON e Markdown Único Consolidado (Sem ZIP)*.
  - *Alternativa 3 — Exportação Direta para PDF Editorial Renderizado no Servidor*.

---

### KD-010 — Nomenclatura da Nova Seção na Navegação Principal
- **Prioridade**: **Desejável**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 5 — O Nome Oficial da Nova Seção Principal Será `Atlas`**:
    - Na interface de usuário e na documentação funcional, a nova seção principal da Lectoria será denominada **Atlas**.
    - Na navegação principal do shell da biblioteca (a ser implementada na Fase 4), o item utilizará o rótulo **Atlas** (`data-nav="knowledge"`).
    - Nos módulos Python (`knowledge_service.py`), tabelas SQLite (`knowledge_links`, `knowledge_fts`) e futuros endpoints REST (`/api/knowledge/*`), o identificador técnico permanece `knowledge` para clareza semântica e estabilidade de código.
- **Alternativas Descartadas**:
  - *Alternativa 1 — Conhecimento*.
  - *Alternativa 2 — Caderno*.
  - *Alternativa 3 — Sínteses*.
  - *Alternativa 4 — Ideias*.

---

### KD-011 — Estratégia de Migração do Schema SQLite (`v12 → v13`)
- **Prioridade**: **Imprescindível**
- **Estado**: `APPROVED`
- **Alternativa Aprovada pelo Proprietário**:
  - **Alternativa 1 — Migração Incremental Aditiva do Schema SQLite `v12 → v13` (`_apply_migration_v13`)**:
    - Incrementa `SCHEMA_VERSION` de `12` para `13` em `database.py`.
    - Cria de forma estritamente aditiva as tabelas `concepts`, `reflections`, `knowledge_links`, a tabela virtual `knowledge_fts`, índices compostos iniciados por `user_id` e triggers de integridade e sincronização FTS.
    - Preserva 100% dos dados existentes das tabelas v1–v12, com backup automático pré-migração (`lectoria_pre_migration_v12_to_v13_<timestamp>.db`) e rollback transacional completo em caso de erro.
- **Alternativas Descartadas**:
  - *Alternativa 2 — Banco SQLite Separado (`knowledge.db`) via `ATTACH DATABASE`*.

---

## 4. Verificação de Compatibilidade entre as 11 Decisões Aprovadas

| Par / Grupo de Decisões | Análise de Compatibilidade | Resultado |
| :--- | :--- | :--- |
| **KD-001 (Escopo B) + KD-006 (Grafo SVG Local) + KD-008 (FTS5) + KD-009 (Exportação ZIP/JSON/CSV)** | Todas as entregas previstas no Escopo B (`KD-001`) estão perfeitamente alinhadas com as escolhas técnicas de visualização (`KD-006`), busca textual (`KD-008`) e portabilidade (`KD-009`). | **100% Compatível** |
| **KD-002 (Conceitos Privados) + KD-005 (Reflexões Privadas) + KD-008 (FTS5 com Dupla Barreira)** | O isolamento por `user_id` em `concepts` e `reflections` combina com a validação relacional obrigatória em tempo de leitura sobre os candidatos retornados por `knowledge_fts`. | **100% Compatível** |
| **KD-003 (8 Tipos Fixos) + KD-004 (UUIDs em `knowledge_links`) + KD-007 (Sem `study_trails`)** | A tabela `knowledge_links` unifica conexões entre conceitos, reflexões, livros, destaques e notas usando os 8 tipos semânticos fixos e UUIDs estáveis, dispensando tabelas paralelas de trilhas (`KD-007`). | **100% Compatível** |
| **KD-008 (FTS5 na v13) + KD-011 (Migração Aditiva v12 → v13)** | A criação de `knowledge_fts` dentro de `_apply_migration_v13` garante que toda a estrutura persistente da v1.9.0 nasça consolidada no schema v13, sem necessidade de editar uma migração já publicada em fases posteriores. | **100% Compatível** |
| **KD-010 (Seção `Atlas`) + Arquitetura `knowledge_*`** | O uso do nome editorial **Atlas** na interface e na documentação convive harmoniosamente com os identificadores internos `knowledge_service.py`, `knowledge_links` e `knowledge_fts`. | **100% Compatível** |
