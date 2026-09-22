# Contrato de dados

O contrato é implementado pela [API](../app/src/containerops/api.py), pelo [domínio](../app/src/containerops/domain.py), pelo [repositório](../app/src/containerops/repository.py), pelo [worker](../app/src/containerops/worker.py) e pelas [migrações](../app/src/containerops/migrations/001.sql). Os exemplos são sintéticos; códigos HTTP e limites são regras locais.

## Entrada

A [validação HTTP](../app/src/containerops/api.py) aplica as [constantes do domínio](../app/src/containerops/domain.py); a [transação de admissão](../app/src/containerops/repository.py) resolve quotas e repetição da chave.

O cliente envia texto sintético por JSON. O banco guarda o texto para reprocessar o job; respostas HTTP, snapshots e logs não incluem o payload.

`POST /v1/jobs` exige `Authorization: Bearer <token>` e `Idempotency-Key` com
1 a 128 caracteres ASCII imprimíveis, sem aceitar uma chave composta só por espaços.
Cada um desses cabeçalhos deve aparecer uma única vez. O esquema Bearer não
distingue maiúsculas/minúsculas; o token continua sendo comparado exatamente.
Credencial inválida retorna 401 com WWW-Authenticate. Authorization duplicado
é recusado pela API (401); o Nginx pode recusá-lo antes (400). Chave duplicada
retorna 422. Espaços de borda em cabeçalhos são normalizados pelo protocolo HTTP;
prefira uma chave UUID sem espaços. As credenciais JSON locais associam um token
aleatório a um proprietário. A consulta autoriza pelo proprietário; outro usuário
recebe 404 mesmo quando o UUID existe. Não há cookies, cadastro ou CORS liberado.

```json
{
  "text": "Olá, mundo!",
  "demo_duration_seconds": 0
}
```

`text` é uma string Unicode válida com até 16.384 bytes em UTF-8; vazio é permitido.
Caractere NUL e surrogate isolado são rejeitados porque não representam texto
armazenável em PostgreSQL UTF-8. O corpo HTTP completo tem limite de 32.768 bytes,
inclusive para transferência em chunks. Campos desconhecidos são rejeitados.

`demo_duration_seconds` é numérico finito, entre 0 e 15; valores maiores que zero
exigem `DEMO_MODE=true` (padrão no Compose; fora dele, o padrão da aplicação é false).

Há no máximo 100 jobs queued/running em conjunto e 20 por proprietário. Os dois
limites incluem leases vivas ou expiradas ainda não finalizadas e são verificados
sob o mesmo lock transacional da inserção. Um proprietário não ocupa sozinho a
capacidade global; excesso de qualquer quota retorna 429 com Retry-After 2.
As quotas não reservam capacidade para todos os proprietários simultaneamente:
cinco proprietários podem preencher os 100 lugares. O escopo permanece local.

Mesma chave, proprietário, texto e duração retornam o mesmo job (HTTP 200).
Uma criação retorna 201; conteúdo ou duração diferentes sob a mesma chave retornam 409. A duração 1 equivale a 1.0, e -0.0 equivale a 0.0. O replay também reconhece
o hash de zero negativo persistido pela versão anterior, sem migrar nem duplicar jobs. A restrição UNIQUE(owner,idempotency_key) e o lock
transacional no singleton de operações protegem concorrência e ambos os limites da fila.

Nova admissão pausada retorna 503; fila cheia, 429; entrada inválida, 422; corpo
excessivo, 413; indisponibilidade, timeout ou lock de banco, 503 com Retry-After.
Um defeito SQL permanente retorna 500 sem instruir o cliente a repetir, e um
estado de schema incompatível retorna 503 sem expor nomes internos. Logs preservam
a categoria e o `request_id`, nunca a mensagem SQL completa.

Uma repetição idempotente continua consultável
durante pausa ou fila cheia. Não há retry automático de POST na API: o cliente
pode repetir com a mesma chave após erro de transporte.

## Cálculo

A função [`analyze_text`](../app/src/containerops/domain.py) e os [testes do domínio](../app/tests/test_domain.py) definem o algoritmo abaixo. Ele não representa uma regra universal de segmentação linguística.

Uma palavra começa com um caractere para o qual `str.isalnum()` é verdadeiro.
Outros alfanuméricos e marcas Unicode das categorias M continuam a palavra.
Marcas isoladas não iniciam palavra. Todos os demais caracteres separam palavras:
`d'água guarda-chuva` contém quatro; `café café` contém duas. Ideogramas contíguos
contam como uma sequência; o algoritmo não faz segmentação linguística.

O checksum SHA-256 usa exatamente os bytes UTF-8 recebidos, sem normalização Unicode,
trim ou alteração de espaços. `é` e `e` seguido de acento combinante têm checksums
diferentes. O identificador público do algoritmo é `unicode-alnum-marks-v1`.

```json
{
  "id": "<UUID>",
  "state": "succeeded",
  "attempts": 1,
  "result": {
    "word_count": 2,
    "checksum": "<64 caracteres hexadecimais>"
  },
  "error": null,
  "version": "1.0.0"
}
```

`result` é null até o sucesso. `error` é null nos estados queued/running/succeeded;
em failed, é `{"code":"attempts_exhausted"}`. Repetir a chave de um job failed
retorna o mesmo estado terminal, sem zerar tentativas nem reenfileirar. Um novo
trabalho, após corrigir a causa, precisa de outra chave. A release 2 acrescenta `algorithm` no nível superior
da resposta e pode gravá-lo em coluna opcional; a release 1 continua lendo schema 2.
A versão descreve a imagem que respondeu, não a versão original que criou o job.

## Estados e persistência

O [repositório](../app/src/containerops/repository.py) controla as transições usadas pelo [worker](../app/src/containerops/worker.py); o [schema](../app/src/containerops/migrations/001.sql) define os dados persistidos.

`jobs` contém UUID, proprietário, chave, texto, hash canônico do pedido, estado,
tentativas, token/validade da lease, timestamps UTC, duração demo e resultado.
`operations` possui uma única linha com o sinal de pausa; `schema_version` registra
a versão 1 ou 2 do schema. Todos os dados de negócio ficam no PostgreSQL.

Transições: queued → running → succeeded; running expirado pode ser adquirido
novamente com outro token; após três aquisições, uma lease expirada passa a failed
com `error_category=attempts_exhausted`.

O despacho usa o mesmo lock curto de
operações para que workers concorrentes observem a escolha anterior. Prioriza o
owner sem atividade anterior e, depois, o menor `max(updated_at)` dos jobs desse
owner com `attempts > 0`; dentro dessa prioridade, usa created_at/id. Aquisição,
renovação, conclusão e falha contam como atividade. O histórico expira com a retenção.
Isso evita despachar todo o backlog de um owner antes de atender outro; não é
reserva de workers, preempção ou garantia de latência. Após o despacho, workers
executam em paralelo. A pausa de admissão não impede o dreno.

O worker usa `FOR UPDATE OF j SKIP LOCKED`, lease de cinco segundos e renovação
a cada segundo durante a duração demo.

Renovação e conclusão exigem token atual **e** lease ainda válida. Um worker antigo
pode recalcular a função pura, mas não sobrescreve o resultado persistido.

Jobs terminais ficam por 24 horas após conclusão. O worker limpa a cada 60 segundos
e `manage cleanup` permite solicitar a limpeza. Pausa e limpeza compartilham o
lock de operações: durante pausa nenhuma limpeza ocorre. Após drenar a fila,
snapshot e dump permanecem estáveis até retomar. A remoção também remove a chave
de idempotência; repetir uma chave depois da retenção cria um novo trabalho.

## Operação, métricas e acesso

Os comandos de [manage](../app/src/containerops/manage.py) e os endpoints da [API](../app/src/containerops/api.py) operam com [papéis SQL](../docker/db/init-roles.sh) separados.

`python -m containerops.manage snapshot` escreve somente JSON determinístico no
stdout, com `schema_version`, `admission_paused`, `counts` para os quatro estados
e `jobs` ordenados por UUID. Cada job tem id, owner, state, attempts, word_count,
checksum e error_category. Não inclui timestamps, texto, credenciais nem chaves.
Logs são JSON em stderr. Snapshot usa uma transação REPEATABLE READ READ ONLY.

`/health/live` não consulta o banco. `/health/ready` verifica conexão e schema
compatível; pausa é reportada como `admission_paused`, sem tornar o processo
indisponível para consulta. `/internal/metrics` exige Bearer e é bloqueado pelo
proxy; só é alcançável na rede interna. Por decisão, retorna agregados da fila
inteira (contagens por estado, idade do mais antigo pendente, recuperações e
`mean_completion_seconds`), não recorta por owner e não expõe id, texto nem owner
de nenhum job. Serve como métrica operacional, não como consulta de dados de um
usuário. `mean_completion_seconds` é a latência desde criação até conclusão,
incluindo espera; o tempo de processamento isolado está nos logs
`job_completed.duration_ms`. Contagens e médias refletem somente os jobs ainda retidos.

O usuário `containerops_app` só tem SELECT em schema_version; SELECT/UPDATE em
operations; SELECT/INSERT/UPDATE/DELETE em jobs. `containerops_migrator` é dono do
schema e executa migrações explícitas com advisory lock; reaplica grants mesmo
quando o schema já está na versão solicitada. `containerops_backup` possui SELECT.
Downgrade de schema é rejeitado. Migração 2 apenas acrescenta uma coluna opcional.

## Matriz HTTP e persistência

| Entrada/estado            | Comportamento                                                                                                                                                                           | Teste                                                                                                                                                     |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authorization             | Ausente, vazio, Basic, token errado/não ASCII → 401 e challenge; esquema em caixa variada válido; duas ocorrências recusadas, iguais ou diferentes                                      | `test_http_contract.py`: autenticação e duplicatas; `checks.http_boundary_checks` pelo Nginx                                                              |
| Proprietário              | Token define o owner; UUID de outro owner → 404; mesma chave para Alice/Bob gera trabalhos distintos                                                                                    | `test_integration.py`: owner boundary e owner scoped keys                                                                                                 |
| Idempotency-Key           | Obrigatório; 1–128 ASCII imprimíveis; 129, vazio, só espaços, tabulação e não ASCII → 422; cabeçalho repetido → 422                                                                     | `test_http_contract.py`: limites e duplicatas; teste pelo proxy                                                                                           |
| text                      | String estrita; null, bool, número, lista/objeto, NUL e surrogate → 422; vazio permitido; UTF-8 com 16.384 bytes aceito e 16.386 rejeitado; emojis e caracteres combinantes preservados | `test_http_contract.py`; `test_domain.py`                                                                                                                 |
| Corpo HTTP                | JSON inválido, null/lista, campo ausente/extra → 422; 32.768 bytes aceitos e 32.769 → 413                                                                                               | Testes HTTP e limites pelo proxy                                                                                                                          |
| Frames ASGI               | Orçamento de bytes acumulado entre frames; desconexão parcial não chama a rota                                                                                                          | `test_body_limit.py`, sem depender de TestClient agrupar o corpo                                                                                          |
| demo_duration_seconds     | Número finito, 0–15 inclusive; bool/string/null/NaN/Infinity/fora do intervalo → 422; valor positivo exige DEMO_MODE                                                                    | `test_http_contract.py`; `test_domain.py`                                                                                                                 |
| Canonização e replay      | 1=1.0 e -0.0=0.0; mesmo pedido/owner/chave → 200 e mesmo UUID; alteração de texto/duração → 409                                                                                         | Unidade + integração com hash legado em PostgreSQL + jornada HTTP                                                                                         |
| job_id                    | UUID inválido → 422 sem consultar banco; inexistente ou não autorizado → 404                                                                                                            | Contrato HTTP e integração                                                                                                                                |
| Pausa/fila cheia          | Pausa → 503/Retry-After 2 para novos; limites de 100 globais e 20 por owner → 429/Retry-After 2; replay continua 200; pedido conflitante permanece 409                                  | Integração HTTP e corridas de ambas as quotas com PostgreSQL                                                                                              |
| Distribuição entre owners | Alice no limite não impede admissão de Bob; backlog mais antigo de Alice não toma os quatro despachos concorrentes; terminais liberam quota                                             | `test_one_owner_cannot_block_another_in_demo_mode`, `test_dispatch_shares_workers_between_owners`, `test_owner_quota_is_atomic_and_includes_running_jobs` |
| Resultado                 | Queued/running: result e error null; succeeded: contagem e SHA-256 dos bytes; failed: tentativas 3 e erro estável sem payload                                                           | Integração, algoritmo puro e jornada                                                                                                                      |
| Concorrência/lease        | Uma linha por chave; workers não tomam a mesma lease viva; token antigo não renova/conclui; 3 expirações produzem failed; replay não reenfileira                                        | Testes concorrentes com PostgreSQL real                                                                                                                   |
| Retenção/manutenção       | Limpeza de terminais após retenção; bloqueada durante pausa; snapshot estável após drenar                                                                                               | Integração; backup/restore e operação                                                                                                                     |
| Saúde/erro                | Live não depende de banco; ready observa schema e pausa; erro DB transitório → 503/Retry-After, SQL permanente → 500 sem retry                                                          | Testes HTTP; falhas reais de banco/schema no verify                                                                                                       |
| Encerramento              | API rejeita admissão quando encerrando; SIGTERM termina trabalho atual e preserva próximo; SIGKILL recupera lease                                                                       | Teste HTTP + experimento de recuperação                                                                                                                   |
| Métricas                  | API interna exige token; Nginx não publica /internal; contagens e latência respeitam dados retidos                                                                                      | Integração HTTP e jornada pelo proxy                                                                                                                      |

Nginx pode rejeitar Authorization duplicado com 400 antes da API, que retorna 401.

## Dependências e teste

Os lockfiles fixam as versões das dependências. Runtime: Python 3.13 Linux/amd64; checks locais: Python 3.11+. Atualizar exige nova resolução, `pip check`, testes, build e scan. colorama/tzdata ficam no lock compartilhado entre Windows e Linux e são instalados nos dois.

Testes unitários verificam Unicode, SHA-256 conhecido, bytes, autenticação e
validação. Integração exige PostgreSQL real e `CONTAINEROPS_TEST_DATABASE=1`, em
projeto/volume de teste isolado, com API/worker parados. A fixture migra e limpa
somente o banco fornecido; nunca habilite essa flag na demonstração. São exercitados
limite concorrente, conflito, lease antiga, três tentativas, permissões, retenção,
autorização e compatibilidade de schema.

Comportamento das bibliotecas: [transações psycopg](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
e [lifespan FastAPI](https://fastapi.tiangolo.com/advanced/events/).
