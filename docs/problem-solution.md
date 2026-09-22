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

## Cenários

| Cenário | Como é testado | Critério |
|---|---|---|
| Repetir admissão não duplica trabalho | UNIQUE por owner/chave e lock em operations; seis POSTs concorrentes pelo proxy | Um UUID e um resultado com contagem e SHA-256; conflito 409 e outro owner 404 |
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
