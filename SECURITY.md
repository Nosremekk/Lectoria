# Política de Segurança

A segurança e a privacidade dos dados de leitura são prioridades fundamentais no **Lectoria**.

---

## 1. Versões Suportadas

Correções de segurança são aplicadas nas seguintes versões:

| Versão | Suportada          |
| :---   | :---               |
| 1.7.x  | :white_check_mark: |
| 1.6.x  | :white_check_mark: |
| < 1.6  | :x:                |

---

## 2. Reportando uma Vulnerabilidade

Se você identificou uma possível vulnerabilidade de segurança no Lectoria:

1. **Não abra uma *Issue* pública**.
2. Utilize o recurso nativo de **Private Vulnerability Reporting** do GitHub na aba *Security* do repositório, ou entre em contato diretamente com o mantenedor responsável pelo repositório.
3. Descreva detalhadamente:
   - A natureza do problema (ex: SQL injection, Cross-Site Scripting, quebra de autorização, bypass de autenticação, IDOR).
   - Passos ou script mínimo para reproduzir a falha.
   - Impacto potencial estimado.

---

## 3. Diretrizes de Segurança do Lectoria

O Lectoria adota por padrão:
- Armazenamento de senhas via `hashlib.scrypt` com salt individual de 16 bytes.
- Armazenamento de tokens de sessão na forma de hash SHA-256 no SQLite.
- Proteção contra CSRF com validação de `Origin`/`Referer` e *Double Submit Cookie* (`x-csrf-token`).
- Prevenção de Mass Assignment com Pydantic `ConfigDict(extra="forbid")`.
- Sandboxing de sistema de arquivos: bloqueio de diretórios de sistema (`/`, `/etc`, `/proc`, etc.) e resolução estrita de links simbólicos contra *Path Traversal*.
- Proteção contra força bruta com *rate-limiting* automático no endpoint de login.
- Anti-enumeração em clubes privados (`404 Not Found` para não-membros).
- Isolamento estrito de anotações privadas e progresso de leitura contra IDOR.

Para a matriz de permissões detalhada e arquitetura de segurança, consulte [docs/AUTHORIZATION.md](docs/AUTHORIZATION.md).

