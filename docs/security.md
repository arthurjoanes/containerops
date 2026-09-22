# Segurança e isolamento de capacidade

## COPS-01 — ocupação da fila por um proprietário

A revisão local encontrou um problema de disponibilidade: um Bearer válido podia
criar os 100 jobs pendentes permitidos e fazer outro proprietário receber 429.
No Compose, o atraso de demonstração aceita até 15 segundos por trabalho. A
seleção FIFO global ainda deixava o segundo proprietário atrás de todo o backlog
do primeiro, mesmo quando havia espaço para admitir seu pedido.

A correção mantém a demo e atua nos dois pontos:

- A transação de admissão conta queued/running globais e do proprietário. O limite
  é 100 globais e 20 por proprietário, sob o mesmo lock da inserção. Réplicas da API
  não conseguem ultrapassar o limite por uma corrida entre contagem e gravação.
- O despacho prioriza o proprietário sem execução anterior ou menos recentemente
  ativo. A escolha fica persistida antes do próximo despacho concorrente. Dentro
  da prioridade, o job mais antigo é escolhido. O cálculo continua fora do lock.
- Replay idempotente é resolvido antes das quotas: permanece 200 no limite;
  conteúdo conflitante permanece 409. Excesso novo é 429 com Retry-After 2.
- Jobs running consomem quota. Conclusão/falha terminal libera a vaga. Tentativas,
  lease, proteção contra worker antigo e o teto global permanecem ativos.

Não há migração de schema. API e worker precisam executar a imagem com a correção;
retornar a uma imagem anterior também retorna ao comportamento anterior de fila.

## Validação de 22/09/2026 UTC

A suíte completa com PostgreSQL real passou: **188 testes e 99 subtests**, sem
casos pulados. São 116 testes da aplicação (18 integrações), 30 da operação e
42 do relatório. Ruff, formatação e mypy estrito passaram. Os comandos executaram
o código do checkout montado em `/audit`, com a imagem local de ferramentas
`containerops-test:local`; não houve reconstrução das imagens de release nesta
validação. [Resultado JUnit](evidence/security-admission-20260922/tests.xml) e
[saída](evidence/security-admission-20260922/tests.log). O
[manifesto](evidence/security-admission-20260922/manifest.json) registra imagens,
comando, hashes das fontes/locks e resultados. Permanecem duas advertências de
depreciação de Starlette/httpx e AnyIO, sem falhas ou casos pulados.

As quatro regressões novas em `app/tests/test_integration.py` cobrem:

1. Oito admissões simultâneas disputam a última vaga de Alice: uma cria, sete
   recebem limite; um job running é contado e sua conclusão libera uma vaga.
2. Alice envia 20 trabalhos de demo de 15 s e recebe 429 no seguinte; Bob ainda
   recebe 201. Replay/conflict de Alice continuam 200/409. Bob é escolhido no
   segundo despacho, antes dos outros 19 jobs de Alice.
3. Quatro workers disputam a fila: quatro leases distintas, duas para cada owner,
   mesmo quando todos os jobs de Alice são mais antigos.
4. Despachos e conclusões sucessivos alternam entre dois backlogs, sem processar
   todos os jobs do primeiro proprietário antes do segundo.

O teste global existente agora preenche 99 vagas com vários proprietários e
mantém a disputa atômica da centésima. Os testes anteriores de lease obsoleta,
expiração, exaustão, pausa, retenção e schema compatível também passaram. A duração
15 s foi validada como entrada e persistência; estes testes não esperam 15 s por
job nem constituem um teste de carga ou medição de latência.

Para repetir pela operação suportada: `python scripts/ops.py build` seguido de
`python scripts/ops.py verify`. Isso cria a imagem atual e executa os testes de
integração em banco descartável. Nunca configure `CONTAINEROPS_TEST_DATABASE=1`
contra o banco da demonstração: a fixture limpa seus dados.

## Limites restantes

O Compose mantém `DEMO_MODE=true`; definir false recusa durações positivas, mas
não é necessário para que quota e distribuição funcionem. A fila não faz
preempção: se todos os workers já executam jobs, um novo proprietário aguarda
uma vaga de execução. A política baseada em atividade recente não é um SLA de
latência nem garantia de pesos iguais; renovação/conclusão também contam como
atividade, e o histórico some após a retenção de 24 horas.

Cinco owners podem consumir os 100 lugares juntos. A quota não é um limitador de
requisições por segundo e não impede abuso com várias credenciais administradas
fora da aplicação. Os tokens são locais e estáticos, o host é único, não há RLS
nem transporte TLS na rede interna, e o backup local não cobre perda da máquina.
Exposição pública exige revisar esses pressupostos, medir contenção no banco e
definir política de identidade/admissão compatível com o uso real.

Esta revisão não substitui a evidência de build, recuperação e imagens em
[verification.md](verification.md); aquela execução tem outro fingerprint e
continua identificada como histórica.
