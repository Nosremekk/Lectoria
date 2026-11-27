# Lectoria — Fase 12.3: Pipeline de Miniaturas e Otimização do Sistema de Capas (`v1.10.0`)

**Data:** 2026-11-27  
**Branch de implementação:** `perf/v1.10.0-phase-12.3-cover-thumbnails` (derivada de `perf/v1.10.0-phase-12.2-http-cache` @ `e838a92`)  
**Versões preservadas:** `APP_VERSION = 1.9.2` | `SCHEMA_VERSION = 13`  
**Produção:** Intocada (nenhum deploy, nenhuma alteração em capas/banco/compose de produção).

---

## 1. Arquitetura Escolhida e Dependência Adicionada

- **Módulo dedicado [`thumbnail_service.py`](../../thumbnail_service.py):** centraliza geração, resolução de caminho seguro, gravação atômica, controle de concorrência, invalidação e limpeza de órfãos, mantendo `covers_service.py` e `main.py` desacoplados.
- **Dependência adicionada (`Pillow`):**
  - `requirements.txt`: `Pillow>=11.1.0,<13.0.0`
  - `constraints.txt`: `Pillow==12.3.0`
  - **Justificativa:** wheel `manylinux` pré-compilada para Python 3.12/3.14 com `libwebp`, `libjpeg-turbo` e `zlib` embutidos — **zero pacotes `apt` extras** no `Dockerfile`, versão recente (12.3.0) sem CVEs abertas conhecidas e suporte nativo a WebP (com transparência RGBA), JPEG progressivo, `ImageOps.exif_transpose` e proteção contra *decompression bombs* (`Image.MAX_IMAGE_PIXELS`).

---

## 2. Preservação Absoluta dos Originais e Armazenamento dos Derivados

- **Originais intocados:** nenhuma capa original (extraída, personalizada, importada ou restaurada) em `data/covers/` é sobrescrita, recomprimida ou removida. Os livros em `/storage/books` permanecem estritamente somente-leitura.
- **Diretório de derivados:** `data/covers/thumbs/` (derivado dinamicamente de `config.COVERS_DIR / "thumbs"`).
- **Identidade determinística do derivado:**
  ```
  <safe_book_id>_<fingerprint12>_<variant>_v1.<ext>
  ```
  - `fingerprint12`: `sha256(f"{st_mtime_ns}:{st_size}:{st_ino}")[:12]` — elimina leitura do arquivo original em cache hits e invalida automaticamente se o arquivo mudar no disco.
  - `variant`: `card` (largura máx. **360 px**) ou `detail` (largura máx. **600 px**), preservando proporção e **nunca ampliando (no-upscale)** imagens menores.
  - `v1`: versão explícita do algoritmo de geração.
- **Invalidação ativa:** `save_custom_cover()`, `save_rendered_cover_cache()`, `delete_custom_cover()` e nova extração invocam `invalidate_book_thumbnails(book_id)` antes de pré-gerar os novos derivados.
- **Regeneração completa:** excluir `data/covers/thumbs/` preserva 100% das capas originais; os derivados são recriados transparentemente sob demanda.

---

## 3. Estratégia de Geração, Formatos e Concorrência

- **Estratégia híbrida (sem Redis/Celery e sem tempestade no startup):**
  1. **Pré-geração na origem:** ao extrair, enviar capa personalizada ou receber cache renderizado do PDF.js, pré-gera `card` + `detail` (em `try/except` não-bloqueante).
  2. **Geração controlada sob demanda:** para capas já existentes em produção (213 arquivos) ou após limpeza de `thumbs/`, gera sob demanda no primeiro acesso com **lock por chave** (impede geração duplicada simultânea da mesma capa) e **`BoundedSemaphore(2)` global** (protege a VPS de 1 vCPU).
  3. **Gravação atômica:** escreve em arquivo temporário `.tmp.<pid>.<tid>.<rand>` no mesmo filesystem e promove via `os.replace()`.
  4. **Fallback seguro:** se a geração falhar (imagem corrompida/truncada), retorna a capa original sem comprometer a disponibilidade da biblioteca.
- **Formato escolhido (WebP q=80 method=4, fallback automático JPEG q=82 progressivo):**
  - WebP superou JPEG em todos os perfis medidos (**13% a 42% menor** que JPEG com qualidade equivalente) e preserva transparência RGBA nativamente.

---

## 4. Otimização do Caminho Quente e Política HTTP/Cache

### 4.1. Correção em `covers_service.py`
- **Removido do caminho quente (`GET /cover`):** `cached_path.read_bytes()` (até 3,44 MB em RAM por request), `is_extracted_cover_visually_valid(cached_bytes)` e `zlib.decompress()` em PNGs a cada requisição. A validação visual ocorre uma única vez na extração/upload/cache.
- **Fast-path:** em cache hit de thumbnail existente, o custo em memória cai para um único `stat()` (**0,065 ms** mediana vs **20,05 ms** no caminho antigo de PNG XL — **~308× mais rápido em processo**).

### 4.2. Contrato HTTP (`/api/books/{book_id}/cover`)
- Suporta `GET` e `HEAD` com query opcional `variant=original|card|detail` (padrão `original` preservando 100% de retrocompatibilidade).
- **Segurança e isolamento multiusuário:**
  - `require_user(request)` e `resolve_book_path(book_id)` executam **sempre antes** de qualquer `304 Not Modified`.
  - Cabeçalhos: `Cache-Control: private, no-cache`, `Vary: Cookie`, `ETag` (`sha256(st_mtime_ns-st_size-st_ino-variant)[:24]`), `Last-Modified`, `X-Content-Type-Options: nosniff`, `X-Cover-Type`, `X-Cover-Variant`.
  - Integrado com `Clear-Site-Data: "cache"` em `POST /api/auth/logout` (Fase 12.2): após logout, qualquer `GET /cover` com `If-None-Match` antigo retorna `401 Unauthorized`, nunca `304`.
  - Nunca comprimido com `gzip` (`/cover` excluído no `SelectiveGZipMiddleware`).

### 4.3. Integração Mínima no Frontend
- [`static/js/app.js`](../../static/js/app.js): cards do Acervo (`width="180" height="255"`) e prateleiras da Descoberta (`width="160" height="225"`) solicitam `variant=card` com `decoding="async"`; modal de detalhes solicita `variant=detail`; modal de personalização de capa mantém `original`.
- [`static/js/reader.js`](../../static/js/reader.js): miniatura do cabeçalho do leitor solicita `variant=card` com `decoding="async"`.

---

## 5. Segurança, Backup e Restore

- **Proteção contra imagens maliciosas:** `Image.MAX_IMAGE_PIXELS = 40_000_000` (anti decompression-bomb), limite de 12.000 px por dimensão, correção EXIF via `ImageOps.exif_transpose`, validação de magic bytes no upload, contenção estrita `is_relative_to()` contra path traversal e zero comandos externos (`shell=True`).
- **Backup e Restore:** `backup_service.create_backup()` copia exclusivamente `config.COVERS_DIR.glob("*_custom.*")` (nível raiz), **excluindo automaticamente `data/covers/thumbs/`** do backup obrigatório. Restaurar um backup antigo restaura os originais e regenera os thumbnails sob demanda sem perda de dados.
- **Docker / `ReadonlyRootfs=true`:** todos os derivados são escritos exclusivamente dentro do volume gravável `/app/data/covers/thumbs/` (UID/GID 1000:1000).

---

## 6. Benchmarks Medidos (Antes × Depois)

Medições em ambiente isolado com perfis representativos S/M/L/XL (padrões com ruído fotográfico de alta entropia; capas editoriais reais comprimem ainda melhor):

| Perfil | Original (Dim / Fmt / Peso) | `card` WebP (360 px) | Redução `card` | `card` JPEG (360 px) | `detail` WebP (600 px) | Geração (1º hit) | Fast-Path (hit) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| **`COVER-S`** | 480×700 JPEG / **108,0 KB** | 360×525 / **39,5 KB** | **-63,4%** | 46,9 KB (-56,6%) | 480×700 / 50,8 KB | 22,8 ms | **0,066 ms** |
| **`COVER-M`** | 650×950 JPEG / **193,1 KB** | 360×526 / **38,0 KB** | **-80,3%** | 43,8 KB (-77,3%) | 600×877 / 98,7 KB | 26,8 ms | **0,067 ms** |
| **`COVER-L`** | 1300×1900 JPEG / **709,0 KB** | 360×526 / **23,5 KB** | **-96,7%** | 31,4 KB (-95,6%) | 600×877 / 90,9 KB | 51,5 ms | **0,069 ms** |
| **`COVER-XL`** | 1800×2500 PNG / **1.282,0 KB** | 360×500 / **14,1 KB** | **-98,9%** | 24,3 KB (-98,1%) | 600×833 / 67,5 KB | 80,8 ms | **0,065 ms** |
| **Agregado (4 perfis)** | **2.292,0 KB** | **115,1 KB** | **-95,0%** | 146,3 KB (-93,6%) | 307,9 KB (-86,6%) | — | **0,067 ms** |

- **Custo de armazenamento adicional estimado para 213 capas:** ~4–6 MB (vs. 35,52 MB de originais e 29 GB livres em disco na VPS).
- **Redução agregada medida:** **-95,0%** nos perfis S/M/L/XL (superando amplamente o critério de ≥ 80%).

---

## 7. Resultados da Suíte de Testes Automatizados

- **Novos testes da Fase 12.3 (`tests/test_v1100_phase123_cover_thumbnails.py`):** **3/3 funções cobrindo os 18 cenários obrigatórios aprovadas** (juntamente com 6/6 da Fase 12.2 = **9/9 em 1,85 s**).
- **Suíte unitária e de integração (`--ignore=tests/e2e`):** **351 passed, 2 skipped** (os 2 testes que exigem daemon Docker local ativo), **0 failed**.
- **Suíte E2E Playwright (`tests/e2e`):** **56 passed, 0 failed**.
- **Total consolidado local:** **407 passed, 2 skipped, 0 failed**.

---

## 8. Limitações, Riscos Residuais, Reversão e Próximos Passos

- **Limitações:** a primeira visita a uma capa legada ainda sem thumbnail gera o derivado sob demanda (23–81 ms em 1 vCPU, limitado a 2 gerações concorrentes); visitas subsequentes usam o fast-path (`0,067 ms`). A meta ponta-a-ponta de "primeiras 12 capas visíveis em ≤ 3 s sobre Tailscale" depende também da redução de cascata/concorrência das prateleiras planejada para a **Fase 12.4** e será validada integralmente na **Fase 12.5**.
- **Reversão:** `git revert` dos commits da branch `perf/v1.10.0-phase-12.3-cover-thumbnails` e remoção opcional de `data/covers/thumbs/` (zero impacto em banco v13 ou capas originais).
- **Roadmap atualizado:** registrada a etapa futura aprovada **Fase 12.4.1 — Otimização automática de documentos em segundo plano** em [`07-prioritized-optimization-roadmap.md`](07-prioritized-optimization-roadmap.md) (somente documentação; sem QPDF/linearização nesta fase).
- **Recomendações para a Fase 12.4:** paralelizar boot do `initAuth()`, carregamento sob demanda de `knowledge.js`/`shared_reading.js`, carregamento condicional de `pdf.min.js` vs `jszip`+`epub.min.js`, adiamento de chamadas secundárias no leitor até a pintura da 1ª página e controle de concorrência nas prateleiras da Descoberta.
