# Lectoria — Prontidão para Release v1.10.0 e Plano de Deploy Controlado

**Data:** 2026-11-27  
**Branch candidata:** `perf/v1.10.0-phase-12.5-validation-release`  
**Versão:** `APP_VERSION = 1.10.0` | **Schema SQLite:** `SCHEMA_VERSION = 13` (sem migração)  
**Produção atual:** `v1.9.2` (intacta; nenhum push, merge, tag ou deploy executado)

---

## 1. Checklist de Prontidão Técnica

| Critério | Estado | Evidência |
| :--- | :---: | :--- |
| Fases 12.1 a 12.5 preservadas e integradas | OK | Commits organizados na branch `perf/v1.10.0-phase-12.5-validation-release` |
| Versão alinhada (`config.py`, HTML, `app.js`, `CHANGELOG`, `README`) | OK | `APP_VERSION=1.10.0`, `SCHEMA_VERSION=13` |
| Imagem Docker candidata com QPDF 12.2.0 + Pillow WebP | OK | `279 MB`, `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges`, `UID/GID 1000` |
| Fixação imutável de representação (`?rep=<rep_id>`) | OK | 9 cenários testados + `length` exato no `reader.js` |
| Subprocessos sem `preexec_fn` (`scripts/bounded_exec.py`) | OK | Testado em container `--read-only` + teste unitário garantindo ausência de `preexec_fn` |
| Validação de integridade ampliada + rejeição de falso `/Linearized` | OK | `--check-linearization`, `CropBox`, texto, `/Annots` (com `.get_object()`), outlines, `/AcroForm` |
| Backups excluindo `thumbs/` e `optimized/` | OK | Inspeção real dos membros de `covers.tar.gz` |
| Dependências íntegras (`pip check`) | OK | `No broken requirements found` |
| Suíte final completa sobre código `v1.10.0` | OK | **419 passed, 2 skipped (Docker local), 0 failed** (363 unit/API + 56 E2E Playwright) + validação Docker real executada na VPS |

---

## 2. Configuração Administrativa Recomendada (Após Deploy)

A otimização de documentos sobe **desativada por padrão (`mode = "disabled"`)** para garantir comportamento conservador. Quando o proprietário desejar habilitá-la em produção, recomenda-se configurar via painel **Administração → Manutenção**:

- **Modo:** `adaptive` com opção **Restringir adaptativo à janela** marcada (`adaptive_use_window = true`)
- **Janela:** `01:00` às `05:00` (`America/Sao_Paulo`) — coordenada automaticamente com `backup_service._backup_lock` (caso o backup local rode às 03:00, a otimização aguarda a conclusão do backup antes de iniciar novo item)
- **Limite de derivados:** `9216 MB` (9 GB) ou `2048 MB` inicial para validação gradual
- **Ociosidade de leitura:** `120` segundos (padrão)

---

## 3. Procedimento de Deploy Controlado (Somente Após Autorização Expressa)

1. **Limpeza do ambiente de staging na VPS** (caso ainda ativo):
   ```bash
   sudo docker rm -f lectoria-p125-staging || true
   sudo docker rmi lectoria:v1.10.0-rc-p125 || true
   sudo rm -rf /tmp/lectoria-p125-staging
   ```
2. **Merge e tag imutável no repositório** (após revisão):
   - Merge da branch `perf/v1.10.0-phase-12.5-validation-release` em `main`.
   - Criar e enviar tag anotada `v1.10.0`.
   - Aguardar workflow `publish.yml` construir, testar por smoke test e promover `:stable` pelo digest verificado.
3. **Atualização na VPS:**
   - Deixar o `lectoria-auto-update.timer` aplicar a atualização com backup automático prévio, ou executar `sudo /usr/local/sbin/lectoria-auto-update` manualmente.
   - Validar `GET /api/health` (`"version": "1.10.0"`), abertura de Descoberta, Biblioteca, PDF e EPUB pelo Tailscale HTTPS.

---

## 4. Procedimento de Rollback Seguro

Como `SCHEMA_VERSION = 13` foi **integralmente preservado** e todos os derivados/miniaturas (`data/covers/thumbs/` e `data/optimized/`) são regeneráveis e não alteram o banco nem os originais:

1. **Desativação rápida da otimização (sem downgrade):** alterar modo para `disabled` e executar limpeza de derivados no painel administrativo.
2. **Rollback completo para `v1.9.2`:** fixar a imagem no digest estável anterior (`ghcr.io/nosremekk/lectoria@sha256:ccb39f08c9f796aa23e49c40e4c4a00fa45f84d8ab8817be7ae79bfee7866547`) e recriar o container (`docker compose up -d`). Nenhuma restauração de banco é necessária.

---

## 5. Pendências / Limitações Conhecidas

- Os 2 testes Docker do `pytest` (`test_v192_backup_restore_and_hardening.py`) permaneceram ignorados no host local por ausência de daemon Docker local; seus requisitos equivalentes (build, `ReadonlyRootfs`, `CapDrop=ALL`, `NoNewPrivs`, `UID/GID 1000`, `healthcheck`, `qpdf`, `Pillow WebP`, backup) foram validados diretamente no container real `lectoria-p125-staging` na VPS.
- A validação final sob topologia Tailscale HTTPS real ocorrerá naturalmente após a autorização de deploy da `v1.10.0` (sem alterar a rede de produção antes disso).
