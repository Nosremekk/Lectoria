# Lectoria v1.9.0 — Relatório de Validação Final Pré-Release (Fase 9.A)

- **Data**: 2026-11-23
- **Branch**: `feature/v1.9.0-connected-knowledge`
- **Versão no código (`config.APP_VERSION`)**: `1.8.0` *(mantida inalterada conforme restrição da Fase 9.A)*
- **Schema SQLite (`database.SCHEMA_VERSION`)**: `13`
- **Recomendação Final**: **`CONDITIONAL GO`** *(código local, migração isolada, segurança, desempenho e 359 testes automatizados 100% aprovados; publicação condicionada aos itens obrigatórios da Fase 9.B e do Gate de Produção)*

---

## 1. Matriz de Testes Executados, Aprovados, Reprovados e Não Executados

| Camada / Suíte | Comando / Origem | Executados | Aprovados | Reprovados | Ignorados | Duração | Status |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| **Compilação Python (`py_compile`)** | `*.py scripts/*.py tests/test_v190_*.py tests/e2e/test_v190_*.py tests/performance/*.py` | Todos | Todos | 0 | 0 | < 1s | **APROVADO** |
| **Testes Unitários e de Integração** | `pytest --ignore=tests/e2e -q` | **305** | **305** | **0** | **0** | `341,44s` | **APROVADO** |
| **Testes E2E (Playwright / Chromium)** | `pytest tests/e2e -q` | **54** | **54** | **0** | **0** | `231,01s` | **APROVADO** |
| **Total Automatizado Local** | Suítes contíguas completas | **359** | **359** | **0** | **0** | `572,45s` | **100% APROVADO** |
| **Migração Isolada `v12 → v13` (porte real)** | Banco sintético `4,24 MB` (600 livros, 6.500 anotações) em `/tmp` isolado | 1 | 1 | 0 | 0 | `82,83 ms` | **APROVADO** |
| **Benchmark Reproduzível (`--quick`)** | `tests/performance/benchmark_v190_atlas.py --quick` | 8 cenários | 8 | 0 | 0 | ~45s | **APROVADO** |
| **Validação `ENABLE_FTS5` em container Docker `python:3.12-slim`** | Requer runtime `docker` (ausente no Fedora local; proibido na VPS nesta etapa) | 0 | — | — | — | — | **NÃO EXECUTADO (Pendente Fase 9.B / Gate)** |

*Warnings observados*: apenas 3 warnings pré-existentes de depreciação (`StarletteDeprecationWarning` / `TestClient` `httpx` e `cookies=` em `test_audit_v140.py`), sem impacto funcional.

---

## 2. Evidências dos Testes Funcionais (Cobertura Consolidada)

- **Regressão v1.8.0 (legado integral)**: biblioteca, descoberta, taxonomia, coleções, clubes de leitura, leitura coletiva, notas/destaques/desenhos, leitores PDF/EPUB, autenticação, CSRF e administração — **0 regressões** (todas as suítes legadas unitárias e E2E aprovadas).
- **Atlas — Domínio, API REST e Segurança (Fases 1–2)**: conceitos privados (`KD-002`), reflexões autorais (`KD-005`), conexões semânticas tipadas (`KD-003`), referências UUID/backlinks (`KD-004`), anti-IDOR, *Read-Time Authorization*, `ConfigDict(extra="forbid")`, CSRF e `Cache-Control: private, no-store` — **100% aprovados**.
- **Leitores PDF/EPUB, Caderno de Estudos e Biblioteca (Fases 3–4)**: modal compartilhado `#modal-reader-knowledge`, filtros `connected`/`unconnected`, resumos em lote sem N+1 e subaba contextual na Biblioteca — **100% aprovados** (incluindo E2E Chromium).
- **Interface Principal do Atlas, Pesquisa FTS5 e Grafo SVG (Fases 5–6)**: 6 subabas (`Visão geral`, `Conceitos`, `Reflexões`, `Conexões`, `Pesquisa`, `Mapa`), editor Markdown seguro sem XSS, controle otimista `409 Conflict`, FTS5 com remoção de diacríticos (`unicode61 remove_diacritics 2`) + fallback relacional controlado, grafo local SVG (1–2 saltos, teto 80 nós / 160 arestas) + alternativa textual acessível — **100% aprovados**.
- **Exportações Portáveis (Fase 7)**: Markdown interligado (`.zip`, com opção wikilinks Obsidian), JSON v2.0, CSV tabular (anti-formula injection), política conservadora de terceiros e streaming em disco temporário — **100% aprovados**.
- **Infraestrutura e Otimizações (Fase 8)**: `tests/test_v190_phase8_infra_and_migration.py` (11 testes: migração v12→v13, rollback/disk-full, FTS5, backup/restore WAL-safe, zero N+1, `EXPLAIN QUERY PLAN`, thread-safety do rate limiter, auditoria AST de `main.py` e concorrência real em Uvicorn `--workers 1`) — **11/11 aprovados**.

---

## 3. Resultado da Migração SQLite `v12 → v13` em Ambiente Isolado

Executada exclusivamente sobre cópia temporária isolada em `/tmp` (removida imediatamente após o teste), simulando um banco v12 com volume comparável ao `lectoria.db` de produção (`5,5 MB` na VPS):

| Métrica | Resultado Observado |
| :--- | :--- |
| **Volume sintético v12** | `4,24 MB` (600 livros, 4.000 highlights, 2.500 notes = 6.500 anotações) |
| **Tempo total da migração `v12 → v13`** | **`82,83 ms`** (incluindo backup pré-migração + DDL + 13 triggers + backfill FTS5) |
| **Backup pré-migração automático** | `lectoria_pre_migration_v12_to_v13.db.bak` criado (`4,24 MB`, íntegro) |
| **Versão final / Integridade / FKs** | `schema_version = 13` · `PRAGMA integrity_check = ok` · `foreign_key_check = 0 erros` |
| **Backfill `knowledge_fts`** | `6.500` linhas indexadas automaticamente |
| **Validação funcional pós-migração** | Busca FTS5 sem acentos (`"epistemologica tecnica"` → `search_engine = fts5`, `2.000` candidatos), criação de conceito/link, grafo local (`2` nós) e snapshot de exportação validados |

Adicionalmente, os testes automatizados validam rollback atômico em falha simulada (`database or disk is full`), preservando o banco em v12 íntegro.

---

## 4. Benchmarks e Comparativo (Metodologia e Resultados)

- **Metodologia**: script reproduzível `tests/performance/benchmark_v190_atlas.py --quick` em AMD Ryzen 5 5600GT, Python `3.14.9`, SQLite `3.53.4`, comparado com o baseline pré-otimização (Fase 8.A) e o consumo real da v1.8.0 observado na VPS (Fase 8.C).

| Cenário / Métrica | Baseline v1.8.0 / Pré-Otimização (8.A) | v1.9.0 Validada (Fase 9.A) | Avaliação |
| :--- | ---: | ---: | :--- |
| **Uvicorn `/api/health` idle (mediana)** | 0,712 ms | **0,663 ms** | Excelente (< 1 ms) |
| **Uvicorn `/api/health` máx. durante exportação** | 495,468 ms (bloqueado) | **3,477 ms** | **Event loop desbloqueado (~142× menor)** |
| **6× exportações concorrentes (`BoundedSemaphore(4)`)** | `{'200': 6}` (serializado) | **`{'200': 4, '503': 2}`** | Concorrência e proteção efetivas |
| **Uvicorn `VmHWM` pico (4× exports paralelos)** | 97,92 MB (serializado) | **161,55 MB** | Folga ampla mesmo em `768m` / `1g` |
| **`list_concepts(50)` (1k conceitos)** | 15,98 ms | **2,554 ms** (5 SQLs) | **-84,0%** |
| **`list_reflections(50)` (1k reflexões)** | 50,20 ms (2.004 SQLs) | **2,853 ms** (6 SQLs) | **-94,3% / zero N+1** |
| **Exports 1k/1k/10k (`md_zip` / `json` / `csv`)** | 1.657 / 1.097 / 745 ms | **1.533 / 1.014 / 647 ms** | Streaming em disco, estável |
| **FTS5 termo único / multi-termo (10k/50k)** | 229,8 / 1.041,8 ms | **143,3 / 537,8 ms** | **-37,6% / -48,4%** |
| **Grafo 2 saltos 80n/160e (50k links)** | 60,05 ms | **33,13 ms** (12 SQLs) | **-44,8%** |
| **Trigger FTS5 insert (1k / 10k / 50k linhas)** | 0,23 / 1,81 / 9,64 ms | **0,22 / 1,77 / 9,84 ms** | Estável |

---

## 5. Consumo de Recursos vs Requisitos da VPS (1 vCPU / 3,8 GiB RAM)

- **Realidade da VPS (verificada na Fase 8.C)**: `1` vCPU, `3,8 GiB` RAM (`2,9 GiB` livres), `2,0 GiB` swap (`512 KiB` usado), `30G` disco livre. Na v1.8.0 em produção, a Lectoria consome **`171,3 MiB` `anon`** + `578,9 MiB` `file` cache dentro de `mem_limit: 768m` (`oom = 0`, `oom_kill = 0`).
- **Adequação da v1.9.0**: com `VmHWM` local de **`161,55 MB`** sob 4 exportações simultâneas e `list_concepts`/`list_reflections` < `3 ms`, a v1.9.0 é **plenamente compatível com 1 vCPU e 4 GB RAM**, mantendo `mem_limit: 768m` viável e **`1g` (`mem_reservation: 256m`) recomendado** (sem `cpus: 1.5`).

---

## 6. Problemas Classificados por Severidade (Sem Correção Automática na 9.A)

| Severidade | Item | Diagnóstico e Impacto |
| :--- | :--- | :--- |
| **ALTO (Bloqueador de Deploy)** | **`docker-compose.v190-candidate.yml` incompatível com a VPS real** | Usa volumes nomeados `/app/*` e `cpus: 1.5`, enquanto produção usa bind mounts `/storage/*` e `1` vCPU. Não deve ser usado na VPS; adotar a proposta de `32-production-configuration-proposal.md`. |
| **ALTO (Bloqueador de Deploy)** | **`ENABLE_FTS5` não testado na imagem Docker `python:3.12-slim`** | Sem runtime Docker no Fedora local; obrigatório testar em container isolado sem volumes de produção antes do upgrade. |
| **ALTO (Risco Operacional)** | **`lectoria-auto-update.timer` (15 min) sobre `:stable`** | Qualquer push em `main` publicará `:stable` e disparará upgrade automático na VPS em até 15 min se o timer não for pausado/controlado antes. |
| **BAIXO (Informativo)** | **Warnings `StarletteDeprecationWarning` na suíte de testes** | Origem em `tests/conftest.py` (`httpx` / `TestClient`); não afeta runtime de produção nem falhas de teste. |

---

## 7. Política de Backups Atualizada nos Critérios de Release

Refletindo a conclusão da **Fase 8.D (`PHASE_8D_COMPLETE`)**, o backup remoto Restic/Rclone foi descontinuado voluntariamente (`disabled`/`inactive`). Os critérios obrigatórios pré-deploy da v1.9.0 são:
1. **Backup SQLite local íntegro** (`lectoria-backup.service` com `SQLite: backup íntegro.` verificado imediatamente antes do upgrade).
2. **Cópia externa manual dos dados essenciais** (`lectoria.db`, `data/covers/` e `/srv/nosreme/lectoria/compose.yaml`) transferida para fora da VPS antes de iniciar a migração `v12 → v13`.
3. **Plano de rollback da migração `v12 → v13` revisado** (preservando `lectoria_pre_migration_v12_to_v13.db.bak` e sem perda silenciosa de dados).

---

## 8. Pendências Obrigatórias para a Fase 9.B (Preparação de Release)

1. Sincronizar `config.APP_VERSION = "1.9.0"`, `data-app-version="1.9.0"` e strings `?v=1.9.0` (`test_v151_frontend_backend_version_alignment`).
2. Atualizar `AGENTS.md`, `README.md`, `CHANGELOG.md` e documentação técnica canônica (`docs/*.md`).
3. Decidir o destino de `docker-compose.v190-candidate.yml` (atualizar ou remover para evitar confusão com o layout `/storage/*` da VPS).
4. Reexecutar a suíte completa de testes após o bump de versão `1.9.0`.
5. Antes do merge em `main` / deploy: cumprir os 6 itens do Gate de Produção em [33-phase-8c-handoff.md](33-phase-8c-handoff.md) (incluindo controle do auto-update, backup local + cópia externa manual e validação isolada de `ENABLE_FTS5`).

---

## 9. Recomendação Final: **`CONDITIONAL GO`**

A base de código da **Lectoria v1.9.0** encontra-se tecnicamente estável, segura e performática (**359/359 testes aprovados**, migração isolada `v12 → v13` em **82,83 ms** sem erros e event loop desbloqueado). Recomenda-se **`CONDITIONAL GO`** para avançar à **Fase 9.B (Versionamento e Preparação Final de Release)**, com o deploy em produção estritamente condicionado ao cumprimento integral das pendências da Seção 8 e do Gate de Produção.
