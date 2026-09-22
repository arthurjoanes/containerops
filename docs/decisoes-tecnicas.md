# Decisões técnicas

As escolhas abaixo explicam o que implementei e os compromissos atuais. Elas não descrevem incidentes de clientes nem uma comparação histórica de alternativas que não foi registrada. Os [casos novos](problem-solution.md) têm entradas sintéticas pequenas, execução real e prova separada das capturas.

## Problemas que orientaram a implementação

| Problema | Decisão e motivo | Custo ou limite; como conferir |
|---|---|---|
| Duas chamadas podem verificar a mesma chave antes de qualquer inserção. | [`submit_job`](../app/src/containerops/repository.py) resolve replay, conflito, quotas e inserção na transação; a restrição única por owner/chave mantém a invariável no banco. | A admissão é serializada em uma seção curta. Não se espera o cálculo sob o lock. [`test_concurrent_idempotency_is_one_row`](../app/tests/test_integration.py). |
| O worker pode morrer e depois voltar com uma posse antiga. | [`claim_job`, `renew_lease` e `complete_job`](../app/src/containerops/repository.py) exigem lease válida e token da aquisição. Isso permite recuperar trabalho sem deixar o processo antigo sobrescrever o atual. | Cálculo pode repetir; são três tentativas. Não é garantia de efeito único em sistemas externos. [`test_stale_worker_cannot_renew_or_overwrite_result`](../app/tests/test_integration.py). |
| Um cliente podia ocupar todos os lugares e atrasar os demais. | Quota de 20 pendentes por owner e despacho pela atividade recente em [`submit_job` e `claim_job`](../app/src/containerops/repository.py), usando o lock já existente. | Mantém o limite global de 100; não há prazo garantido nem defesa contra várias credenciais da mesma pessoa. [Regressões de concorrência e despacho](../app/tests/test_integration.py), [validação em PostgreSQL](security.md). |
| Gerar um dump não prova que será possível continuar trabalhando. | [`backup` e `restore_test`](../scripts/ops.py) pausam/drenam, conferem checksum e igualdade do snapshot, depois exigem um novo job no volume restaurado. | Há pausa de admissão e o backup continua local. [Resultado observado](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/restore.json). |
| Uma candidata pode gravar dados antes de falhar no smoke. | [`release` e `rollback`](../scripts/ops.py) trocam imagens e preservam dados, com migração expansiva compatível com a versão anterior. | Não rebaixam schema e não cobrem migrações destrutivas. [`test_release_two_schema_keeps_release_one_compatible`](../app/tests/test_integration.py) e [falha controlada real](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/rollback.json). |
| JSON antigo ou de outra imagem pode produzir uma aprovação falsa. | [`verification_result` e `select_artifacts`](../scripts/report_evidence.py) verificam manifesto, hashes, janela temporal e identidade; [`generate`](../scripts/report.py) mantém falha de release recente e metadados de cada operação. | São arquivos de um laboratório local, não atestação assinada. [Regressões do relatório](../scripts/test_report.py) cobrem checksum, imagem divergente e tentativa inválida. |
| A página longa dificultava associar uma operação à sua evidência. | [`report.html`](../scripts/report.html) organiza seleção, resultado, etapas e arquivos; [`report.js`](../scripts/report.js) seleciona o painel e move o foco, sem chamadas ao backend. | Snapshot sem controles de deploy ou terminal. Sem JavaScript, os oito painéis permanecem legíveis. [Regressões de navegação](../scripts/test_report_browser.cjs). |

Essas escolhas são proporcionais a uma aplicação local pequena. A [verificação](verification.md) separa os testes da aplicação, das operações e da apresentação, com a fonte e o período a que cada resultado se aplica.

## Dificuldades registradas e o que elas ensinam

| Dificuldade observada | Resposta técnica | O que ainda precisa ser distinguido |
| --- | --- | --- |
| Um proprietário podia consumir a fila global | Limitar admissão por proprietário e considerar atividade recente no despacho, usando a transação existente | Quota limita ocupação; não demonstra, sozinha, espera justa ou prazo máximo. As regressões estão em [segurança](security.md). |
| Uma candidata podia produzir dados antes de falhar | Fazer migração expansiva, voltar a imagem e conferir o job criado pela candidata | Compatibilidade desta mudança não autoriza downgrade de schema ou migração destrutiva. [Prova de rollback](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/rollback.json). |
| O BuildKit recusou o caminho com acento usado no desenvolvimento | Copiar os inputs de build para um caminho temporário ASCII, mantendo a identificação das fontes | É uma adaptação do ambiente de build, não melhora medida no desempenho da aplicação. Implementação em [ops.py](../scripts/ops.py). |
| Uma apresentação podia misturar resultados de operações ou imagens diferentes | Manter identidade e datas por operação e validar referências antes de apresentar aprovação | Regerar HTML ou fotografá-lo não repete backup, scan ou release. [Guia do relatório](report-guide.md). |

As notas explicam os motivos técnicos verificáveis no código e nos artefatos. Não atribuem ao autor uma experiência com clientes nem benefícios financeiros que não foram medidos.

## Fluxo

O cliente envia texto sintético ao proxy com Bearer e chave de idempotência. A API autentica o proprietário, valida tamanho/duração e grava o job no PostgreSQL. Uma restrição única sobre proprietário e chave protege requisições concorrentes. Repetir a chave com o mesmo conteúdo devolve o job existente; conteúdo diferente conflita.

O worker assume um job em transação, grava token e lease, calcula palavras e SHA-256 e salva o resultado se a lease ainda for válida. Após uma falha, o cálculo pode repetir; o resultado salvo permanece único. API e worker usam a mesma imagem.

PostgreSQL guarda fila, resultados e estado operacional. O proxy usa a rede front; o banco, a rede data interna. Migração, testes, dump, restore e scan rodam sob demanda.

## Conceitos

| Pergunta | Explicação aplicada ao projeto | Como conferir |
|---|---|---|
| Imagem, container e volume são a mesma coisa? | A imagem é o artefato imutável com código e dependências. O container é uma execução desse artefato. O volume PostgreSQL preserva estado fora do filesystem efêmero da aplicação. | Recriação com snapshot igual em `evidence/recovery.json`. |
| O que protege a idempotência concorrente? | A restrição única no banco, a comparação do payload e a transação; uma checagem prévia somente na API teria corrida. | Testes de integração e seis POST concorrentes na jornada operacional. |
| Por que token além de lease? | O prazo define quando uma posse expira; o token identifica qual aquisição pode finalizar. Um worker antigo não pode concluir usando a posse de outro. | Teste de token obsoleto e recuperação após SIGKILL. |
| `USER 10001` torna o Docker rootless? | Não. Define o usuário do processo dentro do container. Rootless é propriedade da execução do daemon/runtime no host. O laboratório não muda o daemon. | UID efetivo, `CapEff` e `NoNewPrivs` em `evidence/hardening.json`. |
| Filesystem somente leitura basta? | Não. É combinado com usuário não root, capabilities removidas, rede restrita e limites. `/tmp` é um tmpfs pequeno e explícito. | Tentativas reais de escrever em `/app`, `/etc` e `/`; escrita permitida somente em `/tmp`. |
| Healthcheck reinicia o serviço? | Compose usa healthcheck para estado/ordem inicial. `unhealthy` não implica reinício automático. A política de restart trata processo encerrado. | Banco parado produz live 200 e ready 503; o teste recupera o fluxo depois de reiniciar o banco. |
| Digest é só uma tag longa? | Tag pode ser movida. Config digest, manifesto de plataforma e índice com attestations identificam objetos diferentes. No Docker 29/containerd deste host, o ID devolvido pelo daemon é um manifesto, não o config digest. | Metadados de build e `audit-*.json`, comparando config e camadas exportadas do daemon; detalhes em [supply-chain.md](supply-chain.md). |
| SBOM e provenance mostram o quê? | SBOM descreve componentes observados; provenance descreve materiais e execução do build. As attestations são ligadas ao artefato auditado. Isso não é assinatura nem certificação SLSA. | Inspeção do OCI e subjects das attestations. |
| Um scan sem achados é sempre aprovado? | Só se completou com base identificada e política satisfeita. Base ausente, execução interrompida ou achados HIGH/CRITICAL não são aprovação. | `scan-*.json`, base/idade e relatório completo do Trivy. |
| Backup funcionando equivale a recuperação? | Não. Criar um dump só mostra que a exportação funciona; restaurar em outro volume, comparar resultados e processar novo job testa a recuperação. | `backup.json` e `restore.json`, com checksum e duração observada. |
| Rollback reverte o banco? | Aqui troca a imagem da aplicação. A expansão de schema da segunda release permite a primeira continuar funcionando. Não há downgrade destrutivo nem sobrescrita de dados recentes. | `release.json` e `rollback.json`; IDs anteriores e posteriores, schema e jobs preservados. |
| Compose local fornece alta disponibilidade? | Não. Todos os serviços e o backup local dependem de um único computador. Restart e recuperação de lease reduzem alguns incidentes de processo, não perda do host. | Limite explícito na arquitetura e nos runbooks. |

## Decisões

**Fila no PostgreSQL.** Escolhi manter transações, leases e resultados no mesmo banco. Isso dispensa um broker, mas fila e API disputam recursos. Um serviço de mensagens separado seria uma alternativa para outro volume ou topologia; não foi comparado em benchmark nesta rodada.

**Quota por proprietário e despacho compartilhado.** O limite global sozinho
permitia a um token preencher todos os 100 lugares com jobs de demo de 15 segundos.
Agora cada owner pode manter 20 pendentes; a contagem e a inserção compartilham o
lock existente. O despacho prioriza quem não teve atividade recente e mantém a
seleção persistida antes de outro worker escolher. O lock cobre apenas transações
curtas, sem aguardar o cálculo. Reutilizar timestamps dos jobs evita alterar schema
ou inventar um broker para a fila pequena; exige agregar o histórico retido e deve
ser reavaliado com medições se o volume crescer. Não há garantia de prazo, reserva
por owner nem proteção contra operadores que distribuam várias credenciais a uma
mesma pessoa. [Regressão em PostgreSQL](security.md).

A [medição posterior de admissão e espera](admission-measurement.md) estabelece uma referência local com histórico vazio, dois proprietários e atraso sintético fixo. As três repetições tiveram 20 admissões e quatro recusas por proprietário, e todos os aceitos concluíram. Ela não compara a versão anterior nem mede crescimento do histórico; não permite atribuir um ganho numérico à correção ou prometer prazo máximo.

**Alpine na aplicação.** Reduz os componentes do sistema distribuídos com a API e o worker. As dependências nativas usam wheels musllinux, verificadas no build. Deps/test ficam separados do runtime, e Python continua disponível para os healthchecks.

**Readiness consulta o banco.** Liveness verifica só o processo da API. Com o banco fora, o cliente recebe erro e pode repetir usando a mesma chave.

**Secrets por serviço.** API/worker recebem apenas a credencial da aplicação. O banco recebe os arquivos para criar as roles. As permissões dos mounts são testadas porque Docker Desktop e Linux diferem.

**Migração separada.** App faz DML; migrator faz DDL; backup tem SELECT. A API controla o acesso por owner, sem RLS no banco.

**Pausa durante o backup.** Pausei novas admissões e drenei a fila para que snapshot e dump descrevam o mesmo estado. A pausa dura até terminar a cópia. Uma estratégia sem essa pausa exigiria outra forma de comparar um banco que continua mudando; este laboratório prioriza uma prova local pequena e verificável.

**TLS no proxy.** O cliente recebe a CA local explicitamente. A rede interna não usa TLS.


### Instaladores ficam na preparação

O scan da rodada editorial encontrou dependências vendorizadas vulneráveis dentro do pip da imagem de testes. Como pytest, Ruff e mypy já estavam instalados pelo lock, retirei pip e ensurepip do target executável depois da instalação, seguindo a separação já aplicada ao runtime em [app.Dockerfile](../docker/app.Dockerfile). Mantive as versões das bibliotecas da aplicação. O compromisso é que uma dependência nova exige rebuild; não se instala pacote dentro desse contêiner de teste. A [verificação posterior](verification.md#scan-posterior-e-correção-restrita-à-imagem-de-testes) repete os 116 testes com banco, lint/tipos e scan; o registro inicial com achados não foi apagado.
