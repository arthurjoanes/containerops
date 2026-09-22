# Problema e solução

## Tese e limite

O problema é recuperar um serviço assíncrono pequeno sem perder trabalhos aceitos,
confundir uma imagem testada com outra executada ou tratar um dump como recuperação
testada. A jornada é: cliente envia texto e chave → API persiste → worker adquire
lease → resultado fica consultável → operador recupera falha, restaura e troca versão.
O papel representado é o operador de uma aplicação local; não há validação com
uma equipe externa, tráfego de produção ou compromisso de disponibilidade.

Um script que executa a função e salva um JSON resolve o cálculo, mas não as
fronteiras de admissão concorrente, morte do processo, credenciais, imagem e estado
persistido. PostgreSQL é suficiente como fila deste laboratório. Não há justificativa
para acrescentar broker ou orquestrador distribuído.

## Exemplo: repetição da chamada e morte do worker

Considere Alice enviando `Olá, mundo!`, duração zero e chave `pedido-42`. A primeira admissão retorna 201 e um UUID; a mesma chave com o mesmo conteúdo retorna 200 e o mesmo UUID. Trocar o texto usando essa chave retorna 409. Bob pode ter sua própria `pedido-42`, mas consultar o UUID de Alice retorna 404. A chave é de cada proprietário, e o conteúdo inclui o atraso de demonstração: mudar a duração também muda o pedido.

[`submit_job`](../app/src/containerops/repository.py) resolve essa decisão na transação do banco, antes de consumir quota para um novo job. [`analyze_text`](../app/src/containerops/domain.py) conta duas palavras no exemplo e calcula SHA-256 dos bytes UTF-8 originais. A contagem tem uma regra explícita: `d'água guarda-chuva` conta quatro palavras. A regra não pretende fazer análise linguística. [Testes do contrato HTTP](../app/tests/test_http_contract.py), [domínio](../app/tests/test_domain.py) e [concorrência e isolamento entre proprietários](../app/tests/test_integration.py) permitem conferir cada fronteira.

Agora o worker morre com esse job em `running`. Após a lease expirar, [`claim_job`](../app/src/containerops/repository.py) pode assumir **o mesmo UUID**, aumentar a tentativa e emitir outro token. [`complete_job`](../app/src/containerops/repository.py) exige o token atual e lease válida: um worker antigo que volte não pode finalizar. O cálculo pode ocorrer mais de uma vez; o projeto não promete execução única. O [teste `test_stale_worker_cannot_renew_or_overwrite_result`](../app/tests/test_integration.py) verifica esse caso, enquanto a [prova histórica de recuperação](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-recovery.json) registra SIGKILL e retomada na tentativa 2 em containers reais.

## Exemplo: o dump existe, mas o serviço voltou?

Na [execução histórica](verification.md#execução-de-22092026-utc), o backup continha oito jobs. [`backup`](../scripts/ops.py) pausou novas admissões e drenou a fila para comparar um snapshot estável com o dump. [`restore_test`](../scripts/ops.py) conferiu o checksum, restaurou em volume novo, comparou os oito jobs e enviou `Backup restaurado com sucesso`. O novo resultado teve **quatro palavras**, com checksum conferido. Essa última etapa evita aprovar uma restauração que deixou dados legíveis, mas uma aplicação incapaz de trabalhar.

O [JSON de restauração](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/restore.json) registra 45,281 s observados, sem limpeza. Esse tempo não é um prazo garantido. O dump permanece no mesmo host; uma perda desse computador continua fora da proteção demonstrada.

## Exemplo: voltar a imagem sem apagar o trabalho recente

A release 2 amplia o schema e grava um job antes da falha controlada no smoke. [`release`](../scripts/ops.py) volta API e worker para a imagem anterior e confere que os resultados e o job criado pela candidata continuam presentes. A versão 1 funciona com o schema 2 porque a migração foi expansiva. [`rollback`](../scripts/ops.py) usa a mesma separação entre imagem e dados no retorno manual.

A [evidência de retorno](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/rollback.json) e o [teste de compatibilidade das releases](../app/tests/test_integration.py), `test_release_two_schema_keeps_release_one_compatible`, documentam essa escolha. Restaurar um backup antigo como rollback apagaria o trabalho recente; por isso não faz parte desse comando. Migrações destrutivas exigiriam outra estratégia.

No relatório, percorra **Resultado do job → Backup e restauração → Última troca de imagem → Imagem, auditoria e scan**. Confira em cada detalhe data, projeto e imagem: esses registros podem pertencer a operações diferentes. O estado do job não certifica a release, e um scan de outra imagem não aprova a candidata. [Guia de leitura](report-guide.md).

## Cenários

| Cenário | Como é testado | Critério |
|---|---|---|
| Repetir admissão não duplica trabalho | UNIQUE por owner/chave e lock em operations; seis POSTs concorrentes pelo proxy | Um UUID e um resultado com contagem e SHA-256; conflito 409 e outro owner 404 |
| Um owner não monopoliza a fila | Quota atômica de 20 pendentes por owner, 100 globais; despacho por atividade recente | Alice recebe 429 no limite enquanto Bob recebe 201; quatro despachos concorrentes com backlog de ambos distribuem dois para cada um; replay segue 200 |
| Worker morto deixa trabalho recuperável | Lease temporal e token; SIGKILL/SIGTERM no projeto descartável | Job running antes, nova tentativa depois; SIGTERM conclui o atual sem adquirir o próximo; tentativa antiga não finaliza |
| A imagem demonstrada contém o código atual | Build OCI, auditoria de manifest/config e SHA das fontes dentro das duas imagens; API e worker inspecionados | As duas imagens têm os arquivos esperados e os dois serviços usam o ID solicitado |
| Hardening e isolamento são efetivos | Inspeção de kernel, mounts, escrita negada, conectividade e papéis DB em containers reais | UID/caps/rootfs/limites/redes/permissões observados; YAML sozinho não conta |
| Backup recupera serviço utilizável | Pausa/drenagem, dump binário, checksum; restore em projeto/volume novos | Dump íntegro, snapshot igual e novo job com contagem/checksum corretos |
| Rollback preserva dados pós-migração | Migração expansiva; candidata cria job antes da falha; retorno à imagem 1 | Imagem 1 em API/worker, schema 2, resultados anteriores e job da candidata preservados, novo job concluído |
| Operador repete o teste sem tocar a demo | `prove` encadeia verificação, backup/restore, TLS e releases em projetos próprios, com manifesto por tentativa | Falha retorna código não zero; sucesso só após cleanup; tentativas anteriores preservadas |
| Artefato tem rastreabilidade e riscos visíveis | OCI/SBOM/provenance/sentinela/Trivy ligados ao mesmo config/manifest executado | Digests conferidos; scan completo com base dentro da política, ou falha explícita |

## Resultado esperado

O roteiro define os valores antes de executar: 3 jobs após a jornada (texto
concorrente, corpo no limite e replay de zero); 5 após candidata e retorno
automático; 6 após promoção; 7 após retorno manual; 8 após TLS. O restore deve
recuperar esses oito e processar um novo. Cada snapshot deve ter zero jobs
queued, running ou failed. Rejeições não podem adicionar linhas.

O [manifesto mais recente](evidence/problem-proof-latest.json) identifica a
tentativa, suas etapas e os hashes dos arquivos. A [verificação](verification.md)
registra os resultados da revisão para publicação. Tentativas anteriores ficam
preservadas em diretórios próprios; uma execução interrompida não herda o sucesso
de outra.

Os testes da aplicação, dos comandos de operação e do relatório são suítes
separadas. As falhas de processos, reinícios, restaurações e trocas de imagem
exercitam containers reais, além dos testes unitários. A aprovação vale para
este laboratório local e seus cenários documentados.
