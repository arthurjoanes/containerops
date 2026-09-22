# Admissão e espera com dois proprietários

O [manifesto](evidence/admission-measurement/20260922T031250-0300-b87b5952/manifest.json) e as repetições [1](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-1.json), [2](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-2.json) e [3](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-3.json) registram a carga de **22/09/2026, 06:12–06:15 UTC**.

**Problema central:** limitar o número de pedidos aceitos não informa quanto cada proprietário espera para começar a executar. Este ensaio mede as duas coisas separadamente, em uma fila pequena e controlada, antes de propor mudanças na arquitetura.

Execução de **22/09/2026, 06:12:50–06:15:56 UTC**, aprovada em **185,922 s**, incluindo os três projetos descartáveis e suas limpezas. Foram **144 pedidos oferecidos: 120 aceitos e 24 recusados pela quota do proprietário**. Os 120 aceitos terminaram com o resultado esperado, sem erro de transporte, tentativa recuperada ou observação ausente. [Manifesto e fontes](evidence/admission-measurement/20260922T031250-0300-b87b5952/manifest.json).

## 1. O que foi fixado antes de medir

- Três repetições independentes, cada uma com banco vazio, dois proprietários (`alice` e `bob`), uma API e um worker.
- Cada proprietário oferece 24 pedidos com chaves distintas; a ordem de submissão alterna proprietários, com até oito chamadas HTTP simultâneas. Não há retry do gerador.
- O worker é parado graciosamente com fila vazia. A API continua aceitando pedidos. Depois da admissão, o worker é iniciado para drenar a fila. Portanto, parte da espera é provocada pelo próprio ensaio.
- Texto fixo `alpha beta gamma`: três palavras e SHA-256 dos bytes originais. Cada trabalho tem atraso sintético de 0,5 s, que não representa custo de CPU para contar palavras.
- Limites existentes preservados: 20 trabalhos pendentes por proprietário e 100 globais. Prazo de drenagem de 90 s; orçamento global de 600 s, incluindo reserva de limpeza. Timeouts e conferência de duração limitam o ensaio, sem garantia de tempo real contra travamento do sistema operacional.

Foram usados Windows/Python 3.11.9, Docker Linux/amd64 e Compose 5.5.0. Os limites inspecionados foram: API 0,5 CPU/192 MiB; worker 0,5 CPU/128 MiB; PostgreSQL 0,75 CPU/256 MiB; proxy 0,25 CPU/32 MiB. São limites configurados, não consumo de pico medido. O medidor recusaria iniciar com outros containers ativos.

## 2. Resultado por proprietário e repetição

Em **cada linha**, houve 24 pedidos: **20 respostas 201**, **4 respostas 429 por quota do proprietário** e **20 jobs concluídos**. A tabela separa o tempo das admissões e das recusas. O p95 ordena as observações e escolhe a posição `ceil(0,95 × n)`, sem interpolar. Com apenas quatro recusas, seu p95 é o maior dos quatro tempos; não é uma estimativa estável de tráfego real.

| Repetição | Proprietário | p95 aceitos, n=20 (ms) | p95 recusados, n=4 (ms) | p95 da espera total, n=20 (s) |
| --------- | ------------ | ---------------------: | ----------------------: | ----------------------------: |
| 1         | Alice        |                    516 |                     500 |                        27,185 |
| 1         | Bob          |                    500 |                     484 |                        27,633 |
| 2         | Alice        |                    407 |                     422 |                        27,241 |
| 2         | Bob          |                    500 |                     391 |                        27,609 |
| 3         | Alice        |                    500 |                     406 |                        27,120 |
| 3         | Bob          |                    531 |                     406 |                        27,579 |

Dados completos: [repetição 1](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-1.json), [repetição 2](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-2.json) e [repetição 3](evidence/admission-measurement/20260922T031250-0300-b87b5952/repetition-3.json). Contêm pedidos, estados, eventos do worker, limites, medianas, extremos e hashes. Os percentis foram recalculados a partir das amostras na revisão; não foi feita média de percentis para fabricar um resultado agregado.

O tempo HTTP usa o relógio monotônico do Python do host. Uma [inspeção posterior](evidence/admission-measurement/20260922T031250-0300-b87b5952/post-measurement-supplement.json), no mesmo interpretador, identificou `GetTickCount64` com resolução nominal de **15,625 ms**. Os inteiros da tabela são arredondamentos de apresentação; casas decimais no JSON não significam precisão de microssegundos.

## 3. Como interpretar a espera

**Espera total** é `job_started − created_at`: começa no registro do pedido no banco e termina no evento de início emitido pelo worker, depois de adquirir o job. Não usa `updated_at` como início, pois esse campo também muda durante execução e conclusão. A medição inclui o período em que o consumidor ficou intencionalmente parado.

Para separar a retomada, o medidor observa o relógio do banco antes e depois do comando de iniciar o worker. O instante exato em que ele volta a trabalhar fica dentro desse intervalo. Sua largura foi de **3,076 a 3,219 s** nas três repetições; por isso o resultado abaixo é um intervalo, não um valor exato de espera depois da retomada.

| Repetição | p95 após retomada — Alice (s) | p95 após retomada — Bob (s) |
| --------- | ----------------------------: | --------------------------: |
| 1         |                 19,465–22,684 |               20,020–23,239 |
| 2         |                 19,748–22,824 |               20,301–23,377 |
| 3         |                 19,528–22,731 |               20,081–23,285 |

Esses intervalos delimitam o efeito da incerteza sobre o instante de retomada; **não são intervalos estatísticos de confiança**. Incluem o custo de iniciar o processo. Os dados também preservam o limite inferior da espera induzida antes do comando. Banco e worker usam timestamps do mesmo host Docker; mudanças indevidas de relógio ou múltiplas aquisições tornam a interpretação inconclusiva.

Se um trabalho não tivesse início ou conclusão observados, seria contado como censurado, preservando o tempo mínimo conhecido. Ele não entraria como zero nem desapareceria do denominador de pedidos oferecidos. Nesta execução, todos os 120 trabalhos aceitos tiveram início e conclusão observados.

## 4. Conclusão e limite da escolha atual

O ensaio confirma, neste cenário, admissão limitada por proprietário e progresso dos dois conjuntos de trabalhos com os resultados corretos. Ele fornece uma referência inicial de espera; não demonstra melhora sobre a implementação anterior, pois não houve comparação controlada antes/depois.

Dois proprietários podem ocupar no máximo **40 dos 100 lugares**. A saturação global não foi exercitada. Pelo [contrato de admissão](data-contract.md) e por [`submit_job`](../app/src/containerops/repository.py), um pedido novo é recusado quando seu proprietário ou a fila global atinge o limite; replay idempotente não cria outro trabalho. O proprietário é conferido primeiro: a razão da recusa pode continuar sendo a sua quota mesmo com o global cheio.

A fila em PostgreSQL mantém pedido, admissão e posse no mesmo banco, com transações curtas. Isso basta para este laboratório. O resultado não demonstra necessidade de um broker adicional nem dispensa medir um histórico maior: agregação do histórico retido, vários workers, mais proprietários e chegada contínua não foram avaliados aqui. Também não há prazo máximo garantido, capacidade de produção ou comparação entre máquinas.

## 5. Identidade, dificuldades e reprodução

A imagem medida foi `sha256:08fc96aefa056da9a3ab0d0869bd91cef7751caa4c8249d139d1bfcefb3836ed`; os 12 arquivos da aplicação dentro dela coincidiram com as fontes. O manifesto identifica **44 arquivos operacionais**, iguais antes/depois, em uma árvore com base `35f3f69` e mudanças locais. A identidade vale para o snapshot medido; ajustes posteriores do roteiro de restore têm prova própria.

A [pré-checagem preservada](evidence/admission-measurement/preflight-stale-image-20260922.json) recusou a imagem antiga antes de enviar pedidos: três arquivos não correspondiam à aplicação atual. O build foi refeito e a comparação passou. Na revisão do desenho, `docker pause` foi descartado porque poderia congelar uma transação segurando o lock de admissão; esse foi um risco identificado por leitura, não uma falha HTTP observada no ensaio. O consumidor foi parado graciosamente.

Os [14 testes do medidor](evidence/admission-measurement/unit-checks-20260922.json) conferiram percentis, classificação, prazos e censura antes da execução. Não são somados aos 144 pedidos HTTP. O cenário não executou novo scan; as auditorias históricas pertencem às imagens nelas identificadas.

Na raiz do projeto, com os requisitos Docker/Python do README:

```sh
python scripts/ops.py build --version 1.0.0
docker buildx stop pf-containerops-builder
python scripts/measure_admission.py --image containerops-app:1.0.0
```

Use uma janela sem outra operação de build ou stack ativa. O segundo comando para apenas o builder deste projeto que o build deixou ligado; o medidor também recusaria esse container ocioso. Ele não tenta parar outros serviços automaticamente.

O [medidor versionado](../scripts/measure_admission.py) resolve a imagem para seu ID antes de iniciar, verifica as fontes e cria três namespaces próprios. A preparação da imagem não faz parte dos 185,922 s medidos. Preserve os JSONs de cada tentativa, inclusive falhas; não atribua automaticamente esta tabela a uma reexecução. O [índice das evidências](evidence/admission-measurement/index.json) inclui a revisão independente e as alterações posteriores restritas ao roteiro de restore, sem alegar reexecução do medidor sobre esses novos bytes.
