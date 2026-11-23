# Lectoria v1.9.0 — Fase 7: Formatos de Exportação Aberta e Portabilidade do Atlas

## 1. Objetivos e Arquitetura (`KD-009`)

O subsistema de exportação do Atlas (`knowledge_export_service.py` + `GET /api/knowledge/export`) permite ao usuário autenticado preservar seus conceitos, reflexões autorais, conexões semânticas e referências bibliográficas fora da Lectoria sem depender de serviços externos.

Toda exportação segue um pipeline unificado em duas etapas:
1. **Construção de Snapshot Canônico (`build_atlas_export_snapshot`)**: leitura transacional consistente no SQLite sob a identidade autenticada (`user_id`), aplicando autorização em tempo de leitura e gerando uma representação intermediária única.
2. **Serializadores Específicos em Disco Temporário**:
   - `markdown_zip` (`_write_markdown_zip_export_to_tempfile`)
   - `json` (`_write_json_export_to_tempfile`)
   - `csv` (`_write_csv_zip_export_to_tempfile`)

Os arquivos são gravados em disco temporário (`tempfile.mkstemp`) e transmitidos via `StreamingResponse` em blocos de 64 KB com remoção garantida em `finally` e `BackgroundTask`, evitando manter pacotes inteiros duplicados em RAM.

---

## 2. Política de Autorização, Privacidade e Referências Indisponíveis

- **Dados Próprios**: conceitos, reflexões (`body_markdown` preservado literalmente), conexões (`knowledge_links`) e anotações próprias (`highlights`, `notes`) em livros acessíveis (`is_missing = 0`, `is_active = 1`) são exportados integralmente.
- **Anotações Públicas de Terceiros Referenciadas**: política conservadora (`content_omitted: true`, `omission_reason: "third_party_public_reference_only"`). Exportam-se apenas metadados estruturais mínimos (`id`, `type`, `book_id`, `page`/`epub_cfi`, `chapter_label`, `author_display_name`), sem redistribuir o texto integral de terceiros.
- **Referências Indisponíveis** (itens excluídos, revogados `public -> private`, livros ausentes ou fontes desativadas):
  - A conexão pessoal do usuário e sua `rationale` são preservadas.
  - O extremo indisponível é substituído por sentinela neutro (`available: false`, `id: null`, `label: "Referência indisponível"`), sem título, autor, trecho, página, CFI, motivo da indisponibilidade ou links navegáveis.
- **Exclusões Obrigatórias**: nunca exporta contas de terceiros, hashes de senha, sessões, tokens CSRF, cookies, logs administrativos, arquivos do banco SQLite, caminhos absolutos do servidor nem arquivos PDF/EPUB completos.

---

## 3. Formato 1: Markdown Interligado em ZIP (`format=markdown_zip`)

```text
lectoria-atlas-export/
├── indice.md
├── manifest.json
├── conceitos/
│   └── <uuid>.md
├── reflexoes/
│   └── <uuid>.md
├── livros/
│   └── <uuid>.md
└── anotacoes/
    └── <uuid>.md
```

- **Nomes de arquivos e links**: baseados nos UUIDs/IDs canônicos (`_safe_filename_stem`), utilizando links relativos multiplataforma (`../conceitos/<uuid>.md`). Quando `wikilinks=true`, adiciona `[[Título]]` ao lado do link Markdown padrão sem quebrar leitores convencionais.
- **Frontmatter YAML**: gerado por `_dump_yaml_frontmatter` com escape estrito de aspas, barras, quebras de linha, dois-pontos e delimitadores `---`.
- **Preservação do Markdown Autoral**: o `body_markdown` das reflexões é preservado literalmente, com o bloco `## Conexões e Referências` anexado após separador `---`.
- **Segurança ZIP**: `_safe_zip_entry_path` bloqueia `..`, caminhos absolutos, separadores `\`, caracteres de controle, entradas duplicadas e links simbólicos.
- **Manifesto (`manifest.json`)**: inclui `format`, `format_version: "2.0"`, `app_version`, `exported_at`, `wikilinks_enabled`, `location_conventions`, `policies`, `counts` e lista `files` com `path`, `sha256` e `size_bytes`.

---

## 4. Formato 2: JSON Estruturado v2.0 (`format=json`)

```json
{
  "format": "lectoria-atlas",
  "format_version": "2.0",
  "app_version": "1.8.0",
  "exported_at": "2026-11-21T16:00:00Z",
  "include_archived": true,
  "policies": {
    "unavailable_references": "neutral_sentinel_without_protected_metadata",
    "third_party_public_annotations": "minimal_structured_reference_without_full_content"
  },
  "counts": {
    "concepts": 1,
    "reflections": 1,
    "links": 1,
    "books": 1,
    "annotations": 1,
    "unavailable_link_endpoints": 0
  },
  "concepts": [],
  "reflections": [],
  "links": [],
  "books": [],
  "annotations": []
}
```

- Localizações PDF (`page`) e EPUB (`epub_cfi`) permanecem em campos distintos sem conversão artificial.
- DTOs explícitos impedem serialização acidental de colunas internas.

---

## 5. Formato 3: CSV Tabular (`format=csv`)

Empacota 5 planilhas UTF-8 com BOM (`utf-8-sig`) e `manifest.json` dentro de `lectoria-atlas-csv-YYYYMMDD.zip`:
- `concepts.csv`
- `reflections.csv`
- `links.csv` (preserva direção explícita: `source_type`, `source_id`, `source_available`, `relation_type`, `outgoing_label`, `target_type`, `target_id`, `target_available`, `rationale`)
- `books.csv`
- `annotations.csv`

**Proteção contra CSV Formula Injection (`_sanitize_csv_cell`)**: qualquer célula controlada pelo usuário cujo valor (ou valor após remoção de espaços/tabulações iniciais) comece com `=`, `+`, `-`, `@`, `\t`, `\r` ou `\n` recebe prefixo `'` apenas na serialização CSV, sem alterar o banco SQLite.

---

## 6. Limites, Concorrência e Compatibilidade

- **Tetos por exportação**: `50.000` conceitos, `20.000` reflexões, `100.000` conexões, `100.000` anotações e `20.000` livros.
- **Concorrência**: semáforo `_EXPORT_SEMAPHORE = BoundedSemaphore(4)` retorna `HTTP 503` se houver saturação simultânea.
- **Compatibilidade entre sistemas**: caminhos POSIX `/` no ZIP, UTF-8 (BOM no CSV para Excel Windows/macOS, UTF-8 puro em Markdown e JSON). Validação em Windows/macOS reais não executada neste ambiente Linux; testada via extração isolada e leitores padrão Python (`zipfile`, `csv`, `json`).
