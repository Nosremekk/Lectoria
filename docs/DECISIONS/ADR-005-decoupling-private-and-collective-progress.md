# ADR-005: Desacoplamento entre Progresso Privado e Metas Coletivas

- **Status**: Aceito (implementado na v1.7.0)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
O Lectoria tem como princípio pétreo a privacidade do leitor. No leitor digital, o usuário avança de página em página (`reading_progress.current_page`). Se esse avanço automático fosse sincronizado de forma transparente com as metas dos clubes de leitura, o hábito de leitura, horário e ritmo de estudo do usuário seriam expostos involuntariamente aos demais membros do clube.

## Decisão
Manter um **desacoplamento absoluto** entre o progresso pessoal de leitura e o progresso em metas coletivas:
- A tabela `reading_progress` pertence estritamente ao leitor individual e não possui chaves estrangeiras com clubes ou leituras.
- O progresso nas metas coletivas é gravado na tabela independente `collective_goal_progress`.
- O registro na meta é **estritamente voluntário**: o usuário precisa clicar conscientemente no botão de estado (*Pendente*, *Em andamento* ou *Concluída*) para compartilhar seu avanço com os demais membros.

## Consequências
### Positivas
- Garantia irrestrita de privacidade para o leitor.
- Ausência de ansiedade de leitura ou vigilância passiva em grupos colaborativos.
- Simplicidade relacional: a tabela `collective_goal_progress` opera de forma totalmente autônoma.

### Negativas / Limitações
- O usuário precisa atualizar manualmente o status de uma meta alcançada, em vez de contar com preenchimento automatizado pelo leitor.

*Nota: Decisão de arquitetura e privacidade deliberada e aprovada nas Fases 1 e 2 da v1.7.0.*
