# ADR-006: Fatiamento Otimizado por HTTP Range e Streaming Controlado de PDFs

- **Status**: Aceito (implementado na v1.6.1, mantido e validado na v1.7.0)
- **Data**: 2026-11-13 (formalização documental)

## Contexto
PDFs técnicos, acadêmicos ou literários frequentemente possuem tamanhos entre 50 MB e 500 MB. Em versões anteriores (v1.6.0 e anteriores), o leitor PDF.js executava streaming concorrente sem controle (`disableStream: false`) e utilizava chunks reduzidos de 64 KB, gerando centenas de requisições de rede consecutivas e tentando fazer download integral do arquivo em segundo plano. Em servidores domésticos com hardware modesto (ex: Intel Celeron com 2 GB de RAM e discos mecânicos), isso provocava lentidão severa, saturação de banda e tempos de espera de mais de 30 segundos para visualização da primeira página.

## Decisão
Implementar uma arquitetura de entrega sob demanda baseada em **HTTP Range Requests (RFC 9110)** com parâmetros defensivos:
1. No cliente PDF.js:
   - `disableStream: true`: Desativa o streaming progressivo automático em segundo plano.
   - `disableAutoFetch: true`: Impede pre-fetching especulativo descontrolado.
   - `rangeChunkSize: 524288` (512 KB): Aumenta o tamanho do bloco para reduzir o número de requisições de rede em até 8x sem saturar a memória.
2. No backend FastAPI (`main.py` e `library.py`):
   - Função `resolve_book_file_for_user`: Localiza e valida permissões de acesso ao arquivo em uma única consulta SQL leve, sem múltiplos JOINs desnecessários.
   - Suporte nativo a `Accept-Ranges: bytes`, `206 Partial Content`, `HEAD` e `304 Not Modified` via `ETag` determinístico.

## Consequências
### Positivas
- Redução do tempo de renderização da primeira página de dezenas de segundos para ~1 segundo em rede remota e < 0,3s em rede local.
- Consumo de memória RAM no servidor permanece estável e próximo de zero durante o envio de blocos.
- Compatibilidade plena com hardware doméstico econômico e servidores de mídia compartilhados.

### Negativas / Limitações
- Exige que o proxy reverso (se houver) repasse corretamente o cabeçalho `Range` e respeite respostas `206 Partial Content`.

*Nota: Decisão fundamentada pelos testes de benchmark da v1.6.1 (`tests/test_v161_pdf_performance.py`) e relatórios de auditoria.*
