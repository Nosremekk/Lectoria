# ADR-004: Preservação de Histórico por Exclusão Lógica (Soft Delete)

- **Status**: Aceito (implementado na v1.7.0)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
Em espaços de discussão comunitária e clubes de leitura, exclusões físicas (`DELETE FROM ...`) causam quebra imediata na coerência das conversas, gerando respostas órfãs, links quebrados e perda de contexto de moderação. Além disso, operações administrativas cotidianas (como arquivar um clube ou cancelar uma leitura que não prosseguiu) não devem apagar discussões ricas já realizadas pelos membros.

## Decisão
Adotar **exclusão lógica (*soft delete*) e preservação de histórico** nas entidades da Leitura Compartilhada:
- Tabelas `collective_discussion_topics`, `collective_discussion_posts` e `collective_shared_quotes` contam com a coluna `is_deleted INTEGER DEFAULT 0`.
- Ao excluir um item, o registro é preservado no SQLite e seu texto é mascarado na visualização (`[Mensagem removida pelo autor/moderação]`).
- A tabela `collective_reading_goals` utiliza `is_archived INTEGER DEFAULT 0`.
- Exclusões de contas de usuários aplicam `ON DELETE SET NULL` aos tópicos e postagens, mantendo as árvores de discussão intactas.
- O arquivamento de clubes (`is_archived = 1`) ou conclusão de leituras (`completed`) bloqueia novas alterações (`400 Bad Request`), mas mantém 100% dos dados acessíveis para leitura (`200 OK`).

## Consequências
### Positivas
- Preservação total da rastreabilidade e histórico literário da comunidade.
- Encadeamento de respostas em árvore nunca é quebrado por remoção da mensagem pai.
- Proteção contra perda acidental ou maliciosa de debates.

### Negativas / Limitações
- Consultas SQL precisam filtrar explicitamente `is_deleted = 0` quando se deseja ocultar itens removidos.
- Ligeiro acréscimo de armazenamento residual no SQLite (insignificante para textos curtos de debates).

*Nota: Decisão formulada na Fase 1 e validada na Fase 6 da v1.7.0.*
