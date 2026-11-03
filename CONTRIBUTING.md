# Contribuindo com o Lectoria

Agradecemos o interesse em contribuir com o **Lectoria**! Este projeto valoriza código enxuto, baixo consumo de recursos, segurança e estabilidade operacional.

---

## 1. Princípios de Design do Projeto

Ao propor melhorias ou correções, mantenha em mente as seguintes diretrizes:

1. **Baixo consumo de recursos**: O Lectoria é projetado para operar em hardware doméstico de baixo consumo (como processadores Intel Celeron e arquiteturas similares) coexistindo com outros servidores como o Jellyfin.
2. **Dependências mínimas**: Evite introduzir novas dependências no runtime de produção a menos que estritamente justificável.
3. **Frontend limpo (Vanilla JS)**: O frontend utiliza HTML5 semântico, CSS3 e JavaScript moderno puro (ES6+), sem frameworks pesados (React, Vue, Angular) nem etapa de compilação/bundling (Webpack, Vite).
4. **Preservação de dados**: O servidor opera sobre os PDFs originais exclusivamente em modo somente-leitura. Nunca altere ou remova arquivos de livros do usuário.

---

## 2. Configurando o Ambiente de Desenvolvimento

### Pré-requisitos
- Python 3.10 ou superior
- SQLite 3
- Git

### Passos de Instalação Local

1. Clone o repositório ou use sua cópia local de desenvolvimento:
   ```bash
   git clone https://github.com/Nosremekk/Lectoria.git
   cd Lectoria
   ```

2. Crie e ative o ambiente virtual:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Instale as dependências de desenvolvimento e testes:
   ```bash
   pip install -r requirements-dev.txt
   ```

4. Inicie o servidor de desenvolvimento:
   ```bash
   python3 main.py
   ```
   Acesse no navegador: `http://localhost:8765`.

---

## 3. Executando os Testes Automatizados

Antes de abrir qualquer Pull Request, certifique-se de que todos os testes estejam passando:

```bash
pytest -v tests/
```

Caso crie novas rotas, serviços ou funcionalidades, adicione testes correspondentes na pasta `tests/`.

---

## 4. Diagnóstico de Ambiente

Para verificar se seu ambiente local atende aos requisitos operacionais e de banco de dados:

```bash
python3 scripts/diagnose.py
```

---

## 5. Padrões de Código e Commits

- Siga a [PEP 8](https://peps.python.org/pep-0008/) para código Python.
- Escreva docstrings explicativas para módulos e funções principais.
- Mantenha mensagens de commit claras e estruturadas (ex: `feat: adiciona filtro por ano`, `fix: corrige zoom em telas pequenas`).

