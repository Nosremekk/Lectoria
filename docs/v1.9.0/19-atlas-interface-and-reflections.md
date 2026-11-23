# Lectoria v1.9.0 — Documentação de Engenharia: Interface do Atlas e Editor de Reflexões
**Documento Técnico Especializado: Atlas UI, Gestão de Conceitos, Editor de Reflexões e Exploração Textual de Conexões**
**Fase 5 — Conhecimento Conectado**

---

## 1. Visão Geral Arquitetural

A Fase 5 da Lectoria v1.9.0 consolida a interface de usuário principal do domínio de conhecimento: a seção **Atlas** (`#atlas-section`), acessível pela barra de navegação global (`#tab-nav-atlas`).

O Atlas funciona como um espaço de trabalho intelectual unificado, pessoal e isolado para cada leitor, permitindo:
1. **Visão Geral (`overview`)**: Indicadores sintéticos do acervo intelectual (totais de conceitos, reflexões autorais, conexões semânticas, referências citadas e backlinks recebidos), atalhos de criação rápida e lista cronológica das reflexões e conceitos recentemente modificados.
2. **Gestão Independente de Conceitos (`concepts`)**: Criação, listagem com busca progressiva em memória, ordenação (por contagem de referências ou alfabética), edição de nome/descrição preservando UUIDs e integridade de conexões, contadores diretos de referências e backlinks, e exclusão com modal de confirmação explícita.
3. **Editor de Reflexões Autorais (`reflections`)**: Ambiente de redação com modos de edição (*Editar*, *Visualizar*, *Dividido*), contador de palavras e caracteres em tempo real, barra de ferramentas Markdown, inserção assistida de referências canônicas (`#atlas-assisted-reference-panel`), salvamento com trava contra múltiplos envios, detecção de concorrência em múltiplas abas via `updated_at`, e proteção contra perda acidental de alterações não salvas (`#atlas-unsaved-modal` e guarda `beforeunload`).
4. **Explorador Textual de Conexões (`connections`)**: Navegação por todas as conexões semânticas do usuário, filtros por tipo de relação (8 tipos fixos) e por tipo de entidade (`concept`, `reflection`, `highlight`, `note`, `book`), criação de conexões independentes entre entidades existentes e exclusão direta.

---

## 2. Renderizador Seguro de Markdown (`renderSafeMarkdownToDom`)

Conforme a Decisão **KD-005** e as exigências estritas de segurança da Lectoria, o editor de reflexões não depende de parsers externos pesados e adota uma política de **zero innerHTML**.

### 2.1 Princípios de Implementação
- **Construção 100% via DOM**: Cada elemento é criado por `document.createElement(tagName)` a partir de uma lista fechada de tags permitidas (`h1`, `h2`, `h3`, `h4`, `p`, `blockquote`, `ul`, `ol`, `li`, `pre`, `code`, `strong`, `em`, `hr`, `a`, `span`).
- **Bloqueio Total de HTML Bruto**: Qualquer tag HTML presente no texto do usuário (`<script>`, `<img onerror>`, `<svg onload>`, `<iframe>`, `<form>`, `<object>`) tem seus caracteres de controle escapados e é renderizada estritamente como texto puro via `document.createTextNode`.
- **Proteção contra DOM Clobbering**: Nenhum atributo `id` ou `name` arbitrário é injetado no DOM a partir do Markdown do usuário.
- **Sanitização Estrita de Links (`[texto](url)`)**: A URL é validada com o construtor nativo `new URL(rawUrl, window.location.origin)`. São aceitos apenas os esquemas `http:`, `https:` e `mailto:`, além de caminhos relativos internos iniciados por `/`. Esquemas perigosos como `javascript:`, `data:`, `vbscript:` ou variantes ofuscadas por caracteres de controle são bloqueados e renderizados como `.atlas-md-link-blocked` (texto puro sem atributo `href`). Links externos válidos recebem compulsoriamente `target="_blank"` e `rel="noopener noreferrer"`.
- **Bloqueio de Imagens Externas (`![alt](url)`)**: Para preservar a privacidade do usuário contra rastreamento por terceiros (IP leak e cookies de terceiros), a sintaxe de imagem é interceptada e convertida em um elemento neutro `<span class="atlas-md-image-blocked">[Imagem externa bloqueada por privacidade: {alt}]</span>`, sem emissão de elemento `<img>` ou disparo de requisições de rede.

---

## 3. Navegação Bidirecional e Deep Linking

### 3.1 Deep Linking via Parâmetros de URL
A aba principal suporta endereçamento profundo:
- `/?tab=atlas`: Abre o Atlas na subaba padrão (Visão Geral).
- `/?tab=atlas&subtab=concepts`: Abre a listagem de conceitos.
- `/?tab=atlas&subtab=concepts&id={concept_id}`: Abre diretamente a visualização do conceito com contadores e conexões associadas.
- `/?tab=atlas&subtab=reflections`: Abre a listagem de reflexões autorais.
- `/?tab=atlas&subtab=reflections&id={reflection_id}`: Carrega a reflexão no editor no modo de visualização segura.
- `/?tab=atlas&subtab=reflections&action=new`: Abre o editor limpo para criação de nova reflexão.
- `/?tab=atlas&subtab=connections`: Abre o explorador textual de conexões semânticas.

### 3.2 Navegação Reversa dos Leitores e Detalhes do Livro
- **Nos Leitores PDF e EPUB (`reader-knowledge.js`)**: Entidades do tipo `concept` ou `reflection` exibem a ação contextual *"Abrir no Atlas"*. O leitor salva o progresso atual de leitura de forma assíncrona e serializada via `await window.LectoriaReader.persistProgress()` antes de redirecionar para `/?tab=atlas&subtab=...&id=...`.
- **No Modal de Detalhes do Livro (`book-knowledge.js`)**: Na subaba de Conhecimento, conceitos associados exibem o link *"Abrir conceito no Atlas"*, direcionando diretamente ao conceito correspondente.

---

## 4. Concorrência e Salvamento Confiável de Reflexões

1. **Detecção de Concorrência Multi-aba**: A reflexão armazena o carimbo ISO `updated_at` retornado pela API. Antes de enviar um `PUT /api/knowledge/reflections/{id}`, o payload inclui o estado atual. Caso o backend detecte conflito de versão (ou se a requisição retornar erro por timestamp desatualizado), o editor alerta o usuário e preserva o texto digitado na tela, impedindo que o trabalho seja silenciosamente sobrescrito.
2. **Guarda de Alterações Não Salvas**: Caso o formulário possua edições pendentes (`isDirty`), qualquer tentativa de troca de aba interna no Atlas, navegação para outra seção da Lectoria ou fechamento da janela (`beforeunload`) intercepta a navegação e exibe o modal `#atlas-unsaved-modal` com opções claras de descartar ou cancelar e continuar editando.
3. **Trava de Submissão Dupla**: Botões de salvar reflexão e salvar conceito desabilitam-se no momento do clique e exibem indicador textual de processamento até a confirmação da resposta HTTP.

---

## 5. Resumo da API Utilizada no Atlas

| Endpoint | Método | Descrição |
| :--- | :--- | :--- |
| `/api/knowledge/summary` | `GET` | Indicadores agregados do Atlas e itens recentes do usuário. |
| `/api/knowledge/concepts` | `GET` | Lista todos os conceitos privados do usuário com contagens agregadas. |
| `/api/knowledge/concepts` | `POST` | Criação de novo conceito normalizado. |
| `/api/knowledge/concepts/{id}` | `PUT` | Edição de nome/descrição preservando UUID e conexões. |
| `/api/knowledge/concepts/{id}` | `DELETE` | Exclusão do conceito e remoção de suas conexões associadas. |
| `/api/knowledge/reflections` | `GET` | Lista reflexões autorais com paginação, filtros e busca. |
| `/api/knowledge/reflections` | `POST` | Criação de reflexão autoral em Markdown seguro. |
| `/api/knowledge/reflections/{id}` | `GET` | Obtenção completa da reflexão por ID. |
| `/api/knowledge/reflections/{id}` | `PUT` | Atualização atômica do título, conteúdo e metadados. |
| `/api/knowledge/reflections/{id}` | `DELETE` | Exclusão da reflexão e remoção de suas conexões associadas. |
| `/api/knowledge/links` | `GET` | Listagem das conexões semânticas com filtros relacionais. |
| `/api/knowledge/links` | `POST` | Criação de conexão semântica direcionada entre entidades. |
| `/api/knowledge/links/{id}` | `DELETE` | Exclusão de conexão semântica específica. |
| `/api/knowledge/entities/{type}/{id}/links` | `GET` | Conexões e backlinks de uma entidade específica com tratamento de referências indisponíveis. |
