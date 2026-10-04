# Revisão de segurança do ContainerOps em 3 de outubro de 2026

A API mantém isolamento por proprietário, SQL parametrizado e idempotência transacional. Esta revisão acrescentou limites temporais de requisições, prazo total para receber o corpo e tratamento de exceções inesperadas sem mensagens privadas. A demonstração continua local, com dados sintéticos e atraso controlado disponível.

A imagem recebida contém 18 itens preenchidos; o item 19 está vazio. A revisão cobre o código, os locks e os testes em um banco PostgreSQL descartável, sem usar os dados da demonstração já em execução.

## Resultado por item

| Item | Resultado | Evidência e limite |
| --- | --- | --- |
| 1 Arquivo env exposto | Controlado | `.gitignore` exclui `.env`, runtime, segredos e backups. `.dockerignore` permite somente os arquivos necessários à imagem. Somente `.env.example` é rastreado; Gitleaks passou nos 30 commits do histórico. |
| 2 Validação frontend | Adequado ao relatório estático | A interface é um relatório de evidências, sem formulário de envio de jobs ou autenticação por navegador. O texto é renderizado com escape em `scripts/report.py`; a API valida seus próprios clientes. |
| 3 Validação backend | Controlado | `JobInput` rejeita extras, tipos ambíguos, Unicode inválido, NUL e texto acima de 16 KiB. Corpo limitado a 32 KiB; UUID e chave idempotente são validados. |
| 4 SQL injection | Controlado | `repository.py` vincula valores com `%s`; as interpolações usam somente `JOB_COLUMNS`, constante da aplicação. Migração e SQL administrativo não recebem entradas HTTP. |
| 5 Autenticação fraca | Controlado com ressalva local | Tokens aleatórios são gerados por `ops.setup`, guardados fora do Git e comparados com `hmac.compare_digest`. A configuração passou a recusar controles ASCII, espaços e tokens acima de 256 caracteres. É uma identidade estática de demonstração. |
| 6 IDOR | Controlado | `get_job` filtra UUID e proprietário derivado do token. Consulta de UUID de outro proprietário retorna 404; a integração `test_http_owner_boundary_and_result` verifica criação, replay e leitura. |
| 7 Senhas direto no banco | Sem cadastro de senhas humanas | Credenciais da API ficam em arquivo local, sem tabela de login. Senhas PostgreSQL são geradas em arquivos distintos por papel; o Compose configura autenticação SCRAM-SHA-256. |
| 8 Força bruta | Corrigido | Antes da leitura do corpo e da autenticação, `/v1/` tem 100 requisições/s por peer, rajada 200. Excesso retorna 429 e `Retry-After`; falsificar `X-Forwarded-For` não cria outro orçamento. |
| 9 Bloqueio durante envio | Controlado pelo backend | `UNIQUE(owner,idempotency_key)`, comparação do conteúdo e lock transacional impedem duplicidade em chamadas concorrentes. Não há botão de envio no relatório. Replay continua identificando o mesmo trabalho. |
| 10 CSRF | Não se aplica à autenticação atual | Não há cookie de sessão nem CORS liberado. Jobs exigem Bearer explícito e JSON; uma página externa não recebe autenticação ambiente para escrever. |
| 11 Upload sem validação | Não se aplica | Não existe upload de arquivo. O corpo JSON é limitado em bytes e agora tem prazo total de leitura de 3 s, retornando 408 se excedido. |
| 12 Revelação de informações em erros | Corrigido | Exceções inesperadas recebem resposta 500 genérica. Logs registram categoria e ID, sem mensagem da exceção ou texto enviado. Erros PostgreSQL existentes mantêm semântica 500/503; regressão confere marcador privado ausente. |
| 13 Dependências vulneráveis | Sem achados conhecidos no escopo auditado | pip-audit 2.10.1 consultou OSV separadamente para o lock runtime e o lock de testes. A nova imagem de aplicação foi examinada por Trivy. Scripts de navegador dependem de Playwright instalado externamente e não têm lock próprio neste repo. |
| 14 Tokens mal otimizados | Ressalva da demonstração | Tokens são secretos fora do Git, únicos por proprietário, validados na inicialização e comparados em tempo constante. Não têm expiração automática; substituir o arquivo e reiniciar revoga o anterior. Essa política estática deve ser substituída antes de uso compartilhado. |
| 15 Rate limit | Corrigido | Bearer válido tem limite de 20 requisições/s por proprietário, rajada 40, além do limite por peer. Quota de pendentes continua global e por proprietário, protegida no banco. A quota de fila e o limite temporal são controles distintos. |
| 16 Dados sensíveis expostos | Controlado no escopo local | `job_response`, logs e snapshot omitem texto, tokens e chave de idempotência. Respostas receberam `Cache-Control: no-store` e `nosniff`. `/internal/` é bloqueado no proxy; métricas internas são agregadas, sem payload. |
| 17 SSRF | Controlado | Entradas de jobs são texto para função pura, sem busca HTTP ou execução. O backend não aceita URL de destino. Scripts de operação exigem parâmetros locais e não são endpoints HTTP. |
| 18 Cookies inseguros | Não se aplica | A API e o relatório não usam cookie de autenticação. TLS opcional termina no proxy; a rede de dados interna mantém o escopo local documentado. |

## Correções e testes

O limitador em `app/src/containerops/rate_limit.py` usa tempo monotônico, lock e até 1.024 chaves mais um orçamento compartilhado de overflow. A entrada e a autenticação recusam excesso antes de consultar o banco. Um proprietário que esgota seu orçamento não esgota o de outro. Uvicorn passou a desativar proxy headers e a limitar concorrência, backlog e tempo de keepalive. Peers vistos pela API podem ser proxies; nesse caso, seus clientes compartilham esse orçamento.

`BodyLimitMiddleware` interrompe a recepção do corpo após 3 s, mesmo que o cliente continue enviando pequenos trechos. `request_log` trata falhas inesperadas sem colocar mensagens privadas no log ou na resposta. Os handlers existentes de banco continuam respondendo com seus códigos e `Retry-After` apropriados.

A execução com Python 3.13.15 e PostgreSQL 17.11 descartável passou 232 testes e 106 subtests, sem casos pulados; inclui as 18 integrações da aplicação: [JUnit](evidence/security-review-20261003/local-tests.xml). Ruff e mypy estrito passaram. A conferência ampla de formatação passou após aplicar Ruff a `scripts/capture_docs.py`; a [verificação da estrutura do código](evidence/security-review-20261003/format-capture-docs.json) confirmou AST idêntica antes e depois. A suíte funcional foi preservada, pois essa última alteração mudou apenas a formatação. Permanecem duas advertências de depreciação de Starlette/httpx e AnyIO.

Os novos testes cobrem concorrência e reposição do orçamento, memória limitada sob troca de peers, rate por proprietário, tentativas anônimas com cabeçalho falsificado, healthcheck disponível, segredo ausente em erro inesperado, configuração de token inválida e cancelamento de corpo lento. A integração existente continua verificando isolamento, idempotência, leases, quotas e distribuição da fila.

pip-audit examinou os [17 pacotes de runtime](evidence/security-review-20261003/python-runtime-dependencies.json) e as [14 dependências adicionais de teste](evidence/security-review-20261003/python-dependencies.json), sem achados conhecidos. A leitura dos arquivos separadamente evita confundir o escopo do include `-r` com o resultado do scanner. O [scan da nova imagem](evidence/security-review-20261003/runtime-image-vulnerabilities.json) registra versão, digest e pacotes; Trivy 0.74.0 usou a base consultada em 03/10/2026. Gitleaks 8.30.1 passou no [histórico](evidence/security-review-20261003/secrets-history.json), com redação habilitada.

Trivy não encontrou vulnerabilidades na imagem da aplicação. Gitleaks também passou no [snapshot dos arquivos publicáveis atuais](evidence/security-review-20261003/secrets-current.json). Os dois achados iniciais eram o fingerprint público da chave de assinatura Python, presente nos metadados da imagem e no [Dockerfile oficial](https://github.com/docker-library/python/blob/master/3.13/alpine3.24/Dockerfile). A exceção exige aquele valor literal no caminho exato do relatório. Um [token fictício inserido nesse mesmo caminho](evidence/security-review-20261003/secrets-positive-control.json) continuou sendo detectado no controle positivo. Trivy advertiu sobre a lista interna de fim de suporte do Alpine 3.24 e SBOM de terceiros; o resultado registra essas condições.

## Conferência complementar do CI

O mypy com a configuração estrita de `app/pyproject.toml`, usada pelo CI, aprovou 11 fontes: [conferência dos gates](evidence/security-continuation-20261003/ci-review.json). A jornada existente limita a criação concorrente a seis chamadas; seus pedidos e consultas cabem nos novos orçamentos. Esta conferência dispensou mudança de código e repetição da suíte funcional.

A geração de `docs/report.html` com os mesmos registros e o timestamp histórico reproduziu o HTML byte a byte: [prova de geração](evidence/security-continuation-20261003/generated-report.json). A página histórica permaneceu intacta. As referências locais da documentação e do relatório foram verificadas sem faltas; não apareceram arquivos temporários publicáveis fora das evidências selecionadas.

## Preparação para publicação

O relatório Trivy publicável mantém a identidade da imagem, o sistema operacional, os pacotes e os resultados da análise. Configuração de imagem, variáveis de ambiente e histórico de construção foram removidos dos metadados. Os testes e o scan continuam sendo as execuções registradas acima; essa redução não alterou a imagem nem seus resultados. O [recibo de preparação](evidence/security-review-20261003/publication-preparation.json) registra os hashes e a conferência dos resultados preservados.

## Revisão da prosa

O Humanizer 3.1.0 foi aplicado em modo de arquivo ao README, segurança, contrato de dados, problema e solução, e a este relatório. A revisão identificou rótulos em negrito, pontuação repetitiva e contrastes dispensáveis; reescreveu a prosa, conferiu os fatos e releu o resultado. Comandos, blocos de código, links, paths, números e artefatos históricos foram preservados. O [registro editorial](evidence/security-review-20261003/humanizer.json) informa o escopo e as verificações dos elementos protegidos.

## Limites da conclusão

Esta rodada não executou recuperação, backup, release, rollback, carga de longa duração ou nova medição da fila. Imagens de proxy e banco exigem seus próprios scans; o resultado da aplicação não as aprova. Os limites temporais são por processo, enquanto as quotas de fila são compartilhadas no PostgreSQL. Tokens estáticos, host único, TLS opcional e backup local continuam sendo condições da demonstração. Na rodada inicial, não houve commit ou push. A demonstração já em execução permaneceu intacta.
