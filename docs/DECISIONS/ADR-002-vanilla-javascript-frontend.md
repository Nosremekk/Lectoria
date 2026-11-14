# ADR-002: Frontend em Vanilla JavaScript (Sem Build Pipeline)

- **Status**: Aceito (implementado e preservado em todas as versões)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
Aplicações web modernas frequentemente utilizam frameworks SPA (React, Vue, Angular) com ecossistemas complexos baseados em Node.js, npm, Webpack ou Vite. Esse modelo introduz centenas de dependências no desenvolvimento, complexidade de compilação, fragilidade de empacotamento em contêineres Docker leves e peso excessivo de JavaScript para dispositivos móveis modestos.

## Decisão
Construir e manter toda a interface do Lectoria em **HTML5 semântico, CSS3 e JavaScript puro (ES6+)**:
- Código servido diretamente como arquivos estáticos (`/static`) pelo FastAPI.
- Sem etapas de transpile, bundling ou minificação externa.
- Arquitetura baseada em módulos funcionais e closures (`app.js`, `reader.js`, `shared_reading.js`).
- Renderização eficiente no cliente com chunks (`BOOKS_CHUNK_SIZE = 36`) e delegação de eventos no DOM.

## Consequências
### Positivas
- Dockerfile extremamente enxuto (imagem baseada apenas em Python 3.13 slim, sem Node.js instalado).
- Depuração direta no navegador através de arquivos-fonte legíveis idênticos aos do repositório.
- Inicialização imediata sem tempo de espera de compilação durante o desenvolvimento.
- Desempenho ágil em dispositivos móveis e navegadores embarcados.

### Negativas / Limitações
- Ausência de checagem estática de tipos no frontend (compensada por suíte de testes E2E com Playwright Chromium).
- Gerenciamento manual de mutações do DOM e listeners de eventos.

*Nota: Decisão inferida da implementação atual e documentada formalmente em CONTRIBUTING.md.*
