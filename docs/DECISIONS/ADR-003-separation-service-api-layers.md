# ADR-003: Separação Rígida entre Camada de Serviços e API REST

- **Status**: Aceito (implementado e reforçado na v1.7.0)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
Na evolução de aplicações FastAPI, é comum acoplar regras de negócio diretamente nas funções de rota (`@app.get`, `@app.post`), manipulando requisições, banco de dados e respostas HTTP no mesmo bloco de código. Esse padrão dificulta a escrita de testes unitários rápidos, impede o reuso de regras de negócio por rotinas de automação (background jobs ou CLIs) e torna o código difícil de auditar.

## Decisão
Manter uma **separação unidirecional estrita** entre a camada HTTP e a camada de serviços:
- `main.py` atua estritamente como adaptador HTTP (validação com Pydantic, verificação de CSRF, injeção de sessão e serialização de respostas).
- Todo o processamento de regras de negócio e consultas ao banco residem em módulos especializados (`shared_reading_service.py`, `clubs_service.py`, `auth_service.py`, `library.py`, etc.).
- Os serviços nunca importam `main.py` ou objetos do FastAPI (`Request`, `Response`, `HTTPException`). Erros são comunicados exclusivamente por exceções Python de domínio (`ClubPermissionError`, `InvalidOperationError`, `ValueError`).

## Consequências
### Positivas
- Alta testabilidade: os serviços podem ser exaustivamente testados sem necessidade de instanciar servidores HTTP ou TestClients.
- Tratamento consistente de erros: a função auxiliar `_handle_shared_reading_error` em `main.py` mapeia exceções de domínio para códigos HTTP de forma unificada.
- Manutenibilidade e baixo acoplamento.

### Negativas / Limitações
- Necessidade de definir exceções personalizadas e manter o mapeamento explícito de exceções para códigos HTTP em `main.py`.

*Nota: Decisão observada na arquitetura da v1.6.0 e consolidada na v1.7.0.*
