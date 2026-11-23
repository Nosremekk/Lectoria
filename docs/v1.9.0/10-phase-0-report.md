# Relatório Consolidado da Fase 0 — Lectoria v1.9.0: Conhecimento Conectado

- **Versão Atual em Produção**: `v1.8.0` (Schema SQLite `v12`, commit `ddffdaa4`)
- **Versão Planejada**: `v1.9.0 — Conhecimento Conectado (Connected Knowledge)`
- **Branch de Desenvolvimento Criada**: `feature/v1.9.0-connected-knowledge`
- **Natureza da Fase 0**: Exclusivamente investigativa, arquitetural e documental. Nenhum arquivo de código, banco de dados, teste ou configuração foi modificado.

---

## 1. Estado Inicial do Repositório e Branch Utilizada

1. **Verificação inicial**:
   - Repositório na branch `main`, sincronizada com `origin/main` (`ddffdaa437a7e884c1a474926181371c047aefdf`), com working tree 100% limpo (`nothing to commit, working tree clean`).
2. **Criação da branch isolada**:
   - Confirmada a disponibilidade do nome e criada a branch `feature/v1.9.0-connected-knowledge` a partir da `main` atualizada.
   - Nenhum commit, merge, push, tag ou deploy foi realizado.

---

## 2. Principais Descobertas da Auditoria (`00-current-state-audit.md`)

1. **Fundação técnica de ancoragem pronta para reuso direto**:
   - A v1.8.0 consolidou identificadores UUID estáveis para livros (`books.id`, reconciliados mesmo quando o arquivo muda de nome ou pasta), destaques (`highlights.id`) e notas (`notes.id`), além de navegação precisa por página PDF (`#page=N`) e fragmento canônico EPUB (`#cfi=epubcfi(...)`).
2. **Isolamento atual das anotações por livro**:
   - Embora os leitores PDF e EPUB sejam colaborativos e maduros, todas as consultas de `annotations_service.py` e do Caderno de Estudos (`#annotations-drawer`) operam estritamente dentro de um único `book_id`. Não existe hoje mecanismo para conectar anotações de livros diferentes, criar conceitos transversais ou redigir reflexões multi-livro.
3. **Inadequação das tabelas `categories` e `subjects` para Conceitos pessoais**:
   - `categories` e `subjects` (`taxonomy_service.py`) são tabelas globais da instância voltadas à catalogação bibliográfica de livros inteiros e reescritas pelo scanner automático. Conceitos intelectuais do leitor exigem isolamento por `user_id`, descrição autoral privada e vínculo direto com passagens (`highlights`/`notes`).
4. **Capacidade das Coleções existentes (`collections`)**:
   - A tabela `collections` já oferece agrupamento privado de livros por usuário, tornando desnecessária a criação de um subsistema paralelo pesado de "Trilhas Temáticas" na v1.9.0.
5. **Observação de manutenção da v1.8.0 registrada**:
   - Documentada em `06-security-and-privacy.md` a presença do parâmetro `?csrf_token=...` na URL do `navigator.sendBeacon` em `static/js/reader.js:605-610` (`emergencySave()`), com recomendação de migração futura para `fetch(..., { keepalive: true })` com cabeçalho `X-CSRF-Token`.

---

## 3. Documentos Criados na Fase 0 (`docs/v1.9.0/`)

| # | Documento | Conteúdo Principal |
| :-: | :--- | :--- |
| **00** | [00-current-state-audit.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/00-current-state-audit.md) | Auditoria dos 16 eixos arquiteturais da v1.8.0 e matriz de classificação de componentes com evidências de código. |
| **01** | [01-product-vision.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/01-product-vision.md) | Distinção entre Biblioteca, Leitor e Ambiente de Conhecimento Conectado; 10 princípios e fronteiras de identidade. |
| **02** | [02-feature-exploration.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/02-feature-exploration.md) | Análise das 8 hipóteses funcionais (`5.1` a `5.8`) e comparação detalhada entre os Escopos A, B e C. |
| **03** | [03-user-journeys-and-ux.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/03-user-journeys-and-ux.md) | Integração de UX nos 4 contextos, avaliação de nomes para a nova seção, wireframes documentais e 7 jornadas completas. |
| **04** | [04-domain-and-data-model.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/04-domain-and-data-model.md) | Comparação das 4 abordagens relacionais em SQLite, especificação do modelo híbrido recomendado, ERD e análise da migração `v12 → v13`. |
| **05** | [05-technical-architecture.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/05-technical-architecture.md) | Arquitetura de `knowledge_service.py`, rotas REST `/api/knowledge/*`, FTS5 (`knowledge_fts`), parser Markdown seguro e Grafo Local SVG + Lista Textual. |
| **06** | [06-security-and-privacy.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/06-security-and-privacy.md) | Análise dos 13 vetores de ameaça (IDOR, XSS, CSRF, backlinks, grafos, revogação `public → private`), matriz de autorização e nota de manutenção v1.8.0. |
| **07** | [07-performance-and-scalability.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/07-performance-and-scalability.md) | Orçamento para VPS de 4 GB de RAM, modelagem dos 4 cenários (100, 1.000, 10.000 e 50.000 anotações) e 5 hipóteses de benchmark (`HP-01` a `HP-05`). |
| **08** | [08-roadmap.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/08-roadmap.md) | Planejamento incremental das Fases 0 a 8 com pré-requisitos, entregáveis, riscos, testes e validação regressiva. |
| **09** | [09-owner-decisions.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/09-owner-decisions.md) | Registro estruturado das 11 decisões do proprietário (`KD-001` a `KD-011`) com status `PENDING_OWNER_DECISION`. |
| **10** | [10-phase-0-report.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/10-phase-0-report.md) | Este relatório executivo consolidado da Fase 0. |

---

## 4. Síntese da Arquitetura e Escopo Recomendados

### 4.1. Arquitetura Recomendada
1. **Modelo Relacional Híbrido Aditivo (Schema SQLite `v13`)**:
   - Mantém 100% intactas as tabelas existentes (`books`, `highlights`, `notes`, `page_drawings`, `bookmarks`).
   - Introduz `concepts` e `reflections` (entidades fortemente tipadas e privadas por `user_id`) e `knowledge_links` (tabela unificada de relações direcionadas entre `concept`, `reflection`, `highlight`, `note` e `book`, protegida por triggers SQLite e índices compostos iniciados por `user_id`).
   - Introduz `knowledge_fts` (SQLite FTS5 com `tokenize='unicode61 remove_diacritics 2'`) para pesquisa integrada insensível a acentos em português.
2. **Autorização em Tempo de Leitura (*Read-Time Authorization*)**:
   - Nenhuma conexão privada copia o texto de anotações de terceiros nem concede acesso permanente: se o autor de uma anotação pública revogar sua visibilidade para `private` ou excluí-la, qualquer vínculo de outro usuário passa imediatamente a exibi-la como `[Referência externa indisponível]`, sem vazar dados e sem quebrar a justificativa autoral de quem criou o link.
3. **Visualização Leve (Grafo Local SVG + Alternativa Textual)**:
   - Grafo centrado na entidade em foco (1 a 2 saltos, teto de 80 nós, layout determinístico com 0% de CPU após renderizar), acompanhado de uma **Visão em Lista Estruturada** acessível por teclado e leitores de tela, sem bibliotecas JS externas.

### 4.2. Escopo Recomendado (`KD-001`)
- **Escopo B — Equilibrado** (Conceitos, Conexões entre anotações, Backlinks, Reflexões Autorais, Pesquisa Integrada FTS5, Grafo Local em SVG + Alternativa Textual e Exportação Markdown ZIP / JSON / CSV).
- **Funcionalidades recomendadas para adiamento (`v1.9.x` / `v2.0`)**:
  - Grafo Global massivo em Canvas/WebGL para dezenas de milhares de nós simultâneos.
  - Subsistema separado de Trilhas Temáticas (atendido na v1.9.0 pela combinação de Conceitos e Coleções).
  - Compartilhamento público/social de grafos conceituais inteiros entre usuários.

---

## 5. Consolidação das 11 Decisões do Proprietário (`ALL_DECISIONS_APPROVED`)

- **Quantidade total de decisões deliberadas**: **11 decisões** (`KD-001` a `KD-011` em `09-owner-decisions.md`).
- **Status Consolidado (pós-Fase 0.1 e Autorização da Fase 1)**: Todas as 11 decisões encontram-se no estado **`APPROVED`**:
  - `KD-001` (`APPROVED`): Escopo B — Equilibrado (conceitos, conexões, referências internas, backlinks, reflexões autorais, pesquisa integrada, grafo local e exportação aberta).
  - `KD-002` (`APPROVED`): Conceitos como entidade própria (`concepts`), privada e isolada por usuário (`user_id`).
  - `KD-003` (`APPROVED`): Oito tipos fixos de relação direcionada (`complements`, `contradicts`, `exemplifies`, `grounds`, `develops`, `references`, `questions`, `relates_to`), com justificativa opcional (`rationale`).
  - `KD-004` (`APPROVED`): Referências internas estruturadas por UUIDs estáveis em `knowledge_links` com inserção assistida pela interface.
  - `KD-005` (`APPROVED`): Reflexões autorais como entidade independente (`reflections`), com coluna `body_markdown` e editor Markdown seguro.
  - `KD-006` (`APPROVED`): Grafo local em SVG nativo (1 ou 2 saltos, limite de 80 nós, expansão progressiva) acompanhado de lista textual estruturada acessível.
  - `KD-007` (`APPROVED`): Reutilizar conceitos e coleções existentes; não criar `study_trails` nem subsistemas redundantes.
  - `KD-008` (`APPROVED`): SQLite FTS5 (`knowledge_fts` com `tokenize='unicode61 remove_diacritics 2'`) integrado na migração v13, com validação de permissões nas entidades relacionais originais e fallback seguro documentado.
  - `KD-009` (`APPROVED`): Exportação planejada em pacote ZIP Markdown interligado, JSON estruturado v2.0 e CSV tabular, com opção de wikilinks Obsidian.
  - `KD-010` (`APPROVED`): O nome oficial da nova seção principal será **Atlas** (mantendo `knowledge` nos identificadores técnicos).
  - `KD-011` (`APPROVED`): Migração incremental aditiva do schema SQLite `v12 → v13` (`_apply_migration_v13`).

---

## 6. Transição para a Fase 1

Com todas as 11 decisões aprovadas em [09-owner-decisions.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/09-owner-decisions.md), a execução avança para a **Fase 1 — Consolidação das Decisões, Fundação do Domínio e Migração SQLite v13**, registrada em [11-phase-1-report.md](file:///home/vini/Jellyfin%20livros/docs/v1.9.0/11-phase-1-report.md).
