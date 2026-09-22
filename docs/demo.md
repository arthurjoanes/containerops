# Demo de 5–8 minutos

O roteiro usa os [comandos](../scripts/ops.py) e o [runner](../scripts/proof.py); uma execução está registrada no [recibo editorial de 22/09/2026](evidence/editorial-20260922/execution.json). Os 5–8 minutos são uma duração sugerida para apresentar o projeto, não um tempo medido de instalação ou restauração.

Abra o [caderno de operações](report.html) para navegar entre **Verificação**,
**Recuperação**, **Release** e **Artefatos**. A seleção mostra resultado, identidade
do registro e arquivos de origem. O HTML é um snapshot; os comandos abaixo são
executados separadamente no terminal. [Guia de leitura](report-guide.md).

Para uma apresentação centrada no problema, use primeiro a [sequência de job, rollback e restauração](operational-recovery.md): três trabalhos preservados e um novo concluído no destino, com capturas e tempos explicados. A [medição de admissão e espera](admission-measurement.md) mostra separadamente o comportamento de dois proprietários sob uma carga pequena e controlada. Esses ensaios têm manifestos próprios; não substituem a prova completa descrita abaixo.

Prepare fora da apresentação, na raiz do projeto:

```powershell
python scripts/ops.py setup
python scripts/ops.py build
python scripts/ops.py scan --version 1.0.0
python scripts/ops.py prove
python scripts/ops.py report
```

O scan inicial baixa a base. `prove` exige essa base dentro da política e roda o
scan offline, constrói as duas versões, verifica a aplicação e executa falhas,
release, rollback, TLS e restore em projetos descartáveis. Não exige que a stack
principal esteja ligada. A duração de preparação não é o tempo da apresentação.

```powershell
$manifest = Get-Content -Raw docs/evidence/problem-proof-latest.json | ConvertFrom-Json
$run = Join-Path 'docs/evidence/problem-proof' $manifest.run_id
$manifest | Select-Object run_id,status,started_at,completed_at,elapsed_seconds
$manifest.steps | Select-Object name,status,elapsed_seconds
```

1. **0:00–1:00 — problema e fronteira.** Mostre a [tabela de cenários](problem-solution.md): o
   cálculo é simples; preservar um trabalho aceito durante falhas exige protocolos.
   Confira `status`, data, projeto e hashes do manifesto. Falha ou `in_progress`
   não é aprovação, mesmo que outro JSON antigo tenha passado.
2. **1:00–2:00 — jornada.** Abra `journey.json` dentro de `$run`: seis admissões
   concorrentes, um UUID, sete palavras, SHA-256 do texto e rejeições 409/401/404.
   O caminho usa proxy, API, PostgreSQL e worker reais.
3. **2:00–3:00 — recuperação e hardening.** Mostre `verify-recovery.json`: job
   running antes de SIGKILL, tentativas depois, SIGTERM preservando o atual sem
   adquirir o próximo, live/ready durante indisponibilidade e snapshot após
   recriação. Em `verify-hardening.json`, selecione escrita negada, UID/caps e
   tentativa de acesso proxy→banco. YAML sozinho não mostra enforcement.
4. **3:00–4:30 — atualização com falha.** Em `rollback.json`, compare imagens de
   API/worker, `candidate_job` e `preserved_candidate_job`. A candidata criou um
   resultado no schema 2; a versão 1 volta e esse resultado continua igual.
   Compare os snapshots antes/depois. `manual-rollback.json` registra o retorno
   depois de uma promoção aprovada. Nenhum caminho restaura um backup antigo.
5. **4:30–5:30 — recuperação dos dados.** `backup.json` registra checksum/schema e
   o snapshot estável; `restore.json` registra outro projeto, igualdade dos dados
   e um novo job com contagem e checksum. O backup no mesmo computador não
   cobre perda do computador; o intervalo entre backups limita o RPO.
6. **5:30–7:00 — artefato e limites.** Cruze `sources-*.json`, `build-*.json`,
   `audit-*.json` e `scan-*.json`: fontes, imagem executada, config/manifest e
   attestations. Mostre vulnerabilidades sem correção também. `tls.json` exige
   CA explícita; TLS termina no proxy. Cite que o teste é local, limitado e sem
   validação de produção. Não cobre CI remoto nem reprodutibilidade byte a byte.

Os arquivos acima pertencem à mesma tentativa. Aliases fora de `$run` podem mudar.
`cleanup` aprovado confirma remoção apenas dos projetos temporários da tentativa.
Credenciais e dumps ficam no runtime reservado; não os exiba na apresentação.

Para operar a demo principal, use `start`, `demo`, `backup`, `release` e `rollback`
conforme os [runbooks](runbooks.md). `stop` preserva seus volumes. Para demonstrar
falhas, use `prove`; não é necessário parar ou fazer rollback da demo principal.

## Execução editorial de 22/09/2026

Executei primeiro a verificação da aplicação (`a1560b0d…`, 12:31:52–12:36:05 UTC) e depois a sequência de operações (`53365744…`, 12:36:06–12:38:26 UTC). A base foi `46d43bf`, sem alterações nas fontes da aplicação, do relatório ou dos comandos. As edições de documentação vieram depois. O [recibo](evidence/editorial-20260922/execution.json) registra hashes das fontes, locks, digests e limites; o [manifesto operacional original](evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/manifest.json) foi copiado byte a byte, com LF.

A instalação construiu versões 1 e 2, a imagem de testes, banco e proxy a partir dos Dockerfiles/locks atuais, usando cache e tags próprias. Não foi um build frio. Para chamar os helpers existentes, redirecionei apenas a pasta de evidências e apontei temporariamente seus aliases fixos às imagens novas. Os IDs anteriores foram registrados, conferidos antes de cada troca e restaurados no final. Não removi imagens históricas nem volumes compartilhados. O wrapper e logs integrais permanecem no registro externo da revisão; os JSONs públicos não incluem credenciais ou dumps.

Esse build local usou `--provenance=false`: **não produziu uma nova prova de SBOM/attestations**. O Trivy posterior está registrado separadamente em [segurança](evidence/editorial-20260922/security.json). Os scans históricos e o CI de outra imagem não passam a aprovar estas imagens. A prova demonstra instalação e comportamento local do código atual; a cadeia de suprimentos completa continua sendo o cenário padrão `prove`.

| Caso e esperado independente                          | Observado                                                                      | Evidência                                                                                                                               |
| ----------------------------------------------------- | ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Seis POSTs, mesma chave e texto de sete palavras      | Um UUID; sete palavras; conteúdo divergente 409; outro proprietário 404        | [journey.json](evidence/editorial-20260922/journey.json)                                                                                |
| `Recuperar depois de término abrupto`: cinco palavras | Mesmo UUID, running/tentativa 1 antes do SIGKILL, succeeded/tentativa 2 depois | [Antes/depois](evidence/editorial-20260922/recovery.json)                                                                               |
| Candidata 2 falha após gravar seu job                 | Imagem 1 retomada, schema 2 e três jobs preservados                            | [Rollback](screenshots/editorial-20260922/rollback.png), [JSON](evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/rollback.json)  |
| Dump restaurado em volume novo                        | Três jobs iguais, novo job com quatro palavras, cleanup concluído              | [Restauração](screenshots/editorial-20260922/restore.png), [JSON](evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/restore.json) |
| Cópia separada adulterada                             | Checksum recusado antes de criar destino                                       | [Controle negativo](evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/corrupted-copy-rejected.json)                               |

A conta de palavras foi definida antes: `Olá / mundo / Café / e / ação / 東京 / 42` são sete; `Backup / restaurado / com / sucesso` são quatro. O runner compara também SHA-256 dos bytes originais. Não há ganho financeiro, capacidade de produção ou prazo de recuperação inferido desses números.

A verificação executou **58 testes de comandos**, **47 do relatório** e **116 da aplicação**, além da jornada HTTP, hardening, SIGKILL/SIGTERM e recuperação do banco. Lint, formato e tipos passaram. Os dois avisos de depreciação da aplicação foram preservados; não houve skip nessa suíte. O teste de posse antiga usa PostgreSQL real, mas não simula um efeito externo. Contagens de testes, jobs e requisições não são somadas.

O cenário de operações levou 140,703 s, incluindo limpeza. O restore registra 27,594 s internos e 29,656 s na chamada completa; são fronteiras distintas, não valores concorrentes de um SLA. A conferência da origem verificou dados, imagens, pausa e backup original inalterados. Os resultados não repetem a medição de quota nem o ensaio histórico de oito jobs.

Para repetir pelos comandos públicos, use uma janela reservada e siga os requisitos do README:

```sh
python scripts/ops.py setup
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
python scripts/ops.py verify
python scripts/ops.py prove --scenario operations
```

Esses comandos geram novas identidades; `verify` e `prove` criam projetos descartáveis. Os aliases de evidência fora da pasta de cada tentativa representam a execução mais recente. Preserve a pasta imutável indicada no manifesto para apresentar uma rodada anterior. O wrapper desta revisão isolou também os aliases de arquivos e de imagens, para não substituir os da demonstração histórica.

As três capturas vêm de um HTML gerado com os JSONs reais dessa rodada. Navegação e expansão foram feitas no navegador, sem trocar dados no DOM. A [fonte das imagens](evidence/editorial-20260922/captures.json) identifica relatórios, hashes e dimensões. O [relatório da operação](evidence/editorial-20260922/operations-view/docs/report.html) abre offline. A captura do [job inicial](screenshots/editorial-20260922/initial-job.png) mostra quatro palavras da operação; a jornada concorrente de sete palavras está registrada em seu JSON, com outra identidade. Abrir ou capturar esses HTMLs não executa novamente os serviços.
