# Verificação

## Rollback e restauração — 22/09 às 06:17 UTC

A [sequência operacional](operational-recovery.md) passou em 139,750 s: job inicial identificado, candidata gravando antes da falha controlada, retorno à imagem anterior com dados preservados, cópia privada e restore em volume novo. Três jobs foram comparados e outro foi concluído no destino. O restore levou 27,437 s no intervalo interno e 29,578 s incluindo preflight/limpeza. Origem e backup original permaneceram iguais; a cópia adulterada foi recusada antes de criar o destino.

[Manifesto](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json) e [capturas do relatório](operational-recovery.md) identificam fontes, imagens, marcos de tempo e limites. `operations-proof-latest.json` é o ponteiro desse cenário curto; a prova completa histórica abaixo permanece separada. Não houve novo scan, teste com outra pessoa ou recuperação em outro computador nesta execução.

Depois do ajuste de serialização LF, passaram no host 14 testes da nova prova, 28 de operações existentes e 47 do relatório: [resultados e hashes](evidence/operations-regression/post-serialization-results.json). As seis capturas finais registram três painéis em desktop e celular. O [registro de publicação](evidence/operations-publication.json) preserva a diferença entre os bytes executados e os arquivos publicados, incluindo a lacuna do CSS anterior. O relatório principal foi regenerado com os aliases atuais; seus painéis históricos continuam com datas e identidades próprias. Essa regeneração não reexecuta as operações nem os scans.

O primeiro scan de histórico dessa publicação apontou 13 ocorrências nos hashes de `api.py` e `test_api.py`. Os valores foram recalculados a partir das fontes antes de acrescentar exceções limitadas aos dois caminhos de manifesto e aos dois hashes exatos. O scan local do histórico passou; um controle separado confirmou que uma credencial sintética diferente no mesmo caminho continuava detectável. [Classificação e controle](evidence/operations-regression/secret-fingerprint-review.json). Esse resultado não é scan das imagens.

## Admissão e espera — 22/09 às 06:12 UTC

A [medição limitada](admission-measurement.md) executou três repetições com banco vazio, dois proprietários e 48 pedidos por repetição. Foram 120 admissões, 24 recusas por quota do proprietário e 120 resultados corretos, sem erros de transporte ou observações censuradas. O worker foi parado graciosamente durante admissão; a espera induzida e a incerteza do instante de retomada estão explícitas na análise. Não houve saturação global ou teste com histórico crescente.

O [manifesto](evidence/admission-measurement/20260922T031250-0300-b87b5952/manifest.json) identifica imagem, fontes, ambiente, amostras e limpeza. Os 14 testes do medidor e os pedidos HTTP são denominadores diferentes. A imagem foi reconstruída após recusa de uma versão antiga; esta medição não executou novo scan. Ajustes posteriores de `ops.py`/`proof.py` para a restauração estão no [suplemento datado](evidence/admission-measurement/20260922T031250-0300-b87b5952/post-measurement-supplement.json); não são apresentados como bytes reexecutados pelo medidor.

## Revisão de explicações e jornadas — 22/09/2026

README, problema/solução e decisões técnicas passaram a ligar os exemplos às funções e regressões existentes: admissão idempotente, lease/token, quota, restauração com novo job e retorno de imagem com dados preservados. Os oito jobs restaurados e os 45,281 s são da prova histórica identificada abaixo; esta revisão não executou outra operação Docker.

O gerador atual releu os JSONs existentes em uma pasta isolada. O Edge percorreu os oito painéis em 1440, 390 e 320 px e conferiu foco, teclado, seleção no celular, link de salto, imagens e projetos das operações, checksum completo do job restaurado, links de arquivos e rolagem interna. São 25 registros de verificação, incluindo histórico, oito painéis sem JavaScript, impressão, ampliação CSS de 200% e reflow a 640 CSS px. [Registro e hashes](evidence/interface-journeys/review.json), [roteiro e três capturas](report-guide.md#revisão-anterior-de-jornadas-em-2209). Não foi encontrada regressão funcional nesse escopo; não houve mudança nas fontes da aplicação ou interface. Não foi feita auditoria com leitor de tela ou zoom nativo. As provas e contagens anteriores abaixo foram preservadas.

## Interface do relatório — 22/09/2026

A apresentação passou de uma página contínua para seleção de operações, com
detalhe, etapas e arquivos de origem. O modelo de evidências e a execução da
aplicação não foram substituídos. A nova navegação é local e funciona como
melhoria progressiva: sem JavaScript, todos os resultados continuam disponíveis.

O gerador passou em 46 testes, incluindo sete subcasos que distinguem job na fila,
em execução, concluído, incompleto, falho, desconhecido e ausente. A etapa inicial
da adaptação também executou os 30 testes de operações no host; essa suíte não foi
repetida após os ajustes restritos de navegação e rótulo do job. Os casos do gerador
cobrem seleção estática, identidade separada de backup/restauração e tentativa de
release inválida. As capturas em
`docs/screenshots/report-*.png` vêm do HTML gerado com os JSONs versionados,
sem executar novamente a prova Docker abaixo.

A revisão no Edge conferiu os oito painéis em 1440, 768, 390 e 320 px, navegação
por teclado, histórico do navegador, links locais e leitura sem JavaScript.
Também foram conferidos impressão e reflow em viewport equivalente a zoom de
200%. Não houve overflow horizontal na página nem erros no console.

A revisão posterior corrigiu dois comportamentos: jobs pendentes ou incompletos
não aparecem mais como falha na lista, e **Ir para a operação** mantém o painel
selecionado, focando o conteúdo sem mudar o fragmento da URL. O teste opcional
`node scripts/test_report_browser.cjs` confere seis casos do atalho (três painéis
em desktop e celular), seleção por teclado, histórico, impressão e leitura sem
JavaScript. Ele usa o HTML local, sem servidor. Playwright é ferramenta de
desenvolvimento: `PLAYWRIGHT_MODULE` pode indicar uma instalação externa e
`PLAYWRIGHT_CHANNEL=msedge` seleciona Edge. Ruff check/format e `node --check`
passaram. O relatório e as cinco capturas foram regenerados com os mesmos JSONs;
nenhuma prova Docker foi executada.

## Revisão posterior de isolamento de capacidade

A correção COPS-01 acrescenta quota por proprietário e distribuição de despacho.
A validação posterior passou em 188 testes e 99 subtests com PostgreSQL real,
Ruff, formatação e mypy. [Escopo, regressões e limites](security.md), com JUnit e
saída publicados. A prova completa de imagens abaixo corresponde às fontes e
fingerprint daquela execução; ela não atesta o build desta alteração posterior.

## Execução de 22/09/2026 (UTC)

`python scripts/ops.py prove` terminou com código 0 e 19 etapas aprovadas em 746.562 segundos. A execução partiu de um clone local limpo, acrescido somente dos arquivos candidatos à publicação, sem `.env`, ambiente virtual ou caches do diretório de desenvolvimento. O runtime e a base Trivy ficaram fora desse clone.

O [manifesto completo](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/manifest.json) registra início `2026-09-22T00:07:05.748701+00:00`, fim `2026-09-22T00:19:32.313316+00:00` e fingerprint de fontes `448e9db89e4821a7f128f2a31d8fb35842812eb6c4b5a8fdb34ffcbc7a43b43d`. As fontes foram conferidas no início/fim e dentro das duas imagens. A revisão Git registrada ainda estava com alterações locais; o fingerprint identifica os bytes efetivamente testados.

Ambiente: Windows, Python 3.11.9 no host, Docker Desktop com Engine 29.7.2, Compose 5.5.0 e Buildx 0.36.1, plataforma linux/amd64. Runtime Python 3.13.15, PostgreSQL 17.11 e Nginx 1.30.5; [bases fixadas por digest](../docker/images.lock.json).

### Resultados

- Aplicação: 112 casos aprovados, incluindo 14 integrações com PostgreSQL real; Ruff, formatação e mypy estrito aprovados.
- Operação no host: 30 testes aprovados, incluindo validação de camadas gzip/tar e rejeição de conteúdo divergente.
- Gerador de relatório: 42 testes aprovados, incluindo evidência incompleta, antiga, contraditória ou com checksum alterado.

São suítes distintas; reexecuções não são somadas. Duas advertências de depreciação Starlette/httpx e AnyIO permanecem nos [resultados dos comandos](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-test-results.json).

| Cenário | Resultado e evidência |
|---|---|
| Jornada e autorização | Seis POSTs idempotentes retornaram o mesmo UUID; contagem/checksum corretos; conflito 409, anônimo 401 e outro owner 404. [Jornada](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/journey.json). |
| Falha e recuperação de processos | SIGKILL recuperou o mesmo job na tentativa 2. SIGTERM concluiu o atual e deixou o próximo aguardando reinício. [Recuperação](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-recovery.json). |
| Banco e persistência | Banco parado e schema incompatível recusaram readiness; recriação preservou dados. [Recuperação](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-recovery.json). |
| Isolamento e permissões | UID, capabilities, NNP, mounts somente leitura, secrets, limites, redes e papéis SQL exercitados em containers reais. [Hardening](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-hardening.json). |
| Atualização e rollback | A candidata gravou no schema 2; falha controlada restaurou API/worker anteriores preservando o novo job. Promoção e rollback manual também preservaram resultados. [Falha controlada](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/rollback.json), [promoção](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/release.json), [retorno manual](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/manual-rollback.json). |
| TLS | Conexão aprovada com CA explícita e recusada pela confiança padrão; outro job concluído. [TLS](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/tls.json). |
| Backup e restore | Checksum correto, 8 jobs iguais em volume novo e outro job processado após restauração. Recuperação observada: 45.281 s, sem incluir limpeza. [Backup](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/backup.json), [restore](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/restore.json). |
| Preservação da demonstração principal | Hashes de state.json e latest-backup.json iguais antes/depois; somente projetos temporários foram removidos. [Antes](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/main-runtime-before.json), [depois](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/main-runtime-after.json). |

### Imagens e segurança

As duas releases da aplicação, a imagem PostgreSQL e o proxy tiveram zero achados em todas as severidades no Trivy 0.74.0. A política bloqueia qualquer HIGH/CRITICAL, inclusive sem correção. A base utilizada foi atualizada em `2026-09-21T07:13:21.413091201Z`, dentro do limite de 72 horas.

Os [scans da aplicação 1](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/scan-1.0.0.json), [aplicação 2](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/scan-2.0.0.json) e [banco/proxy](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/scan-services.json) registram imagem, config digest, base e hashes dos relatórios. As auditorias [1](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/audit-1.0.0.json) e [2](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/audit-2.0.0.json) conferem OCI, camadas, SBOM, provenance, versões do lock e ausência da sentinela nos locais inspecionados.

A cobertura dos avisos Alpine difere da Debian. O resultado do scanner descreve as imagens e a base daquela execução; não comprova ausência de vulnerabilidades desconhecidas. Ferramentas de build, scanner e testes não estão incluídas como imagens de runtime. [Detalhes da cadeia de suprimentos](supply-chain.md).

### Evidências e limites

Tentativas anteriores, incluindo falhas corrigidas, foram preservadas. Caminhos pessoais nos JSONs publicados foram substituídos por marcadores locais e inventários de containers alheios ao projeto foram omitidos; hashes das referências de artefatos foram recalculados. Resultados, hashes das fontes e identidades das imagens foram preservados.

O domínio usa dados sintéticos. Esta prova local não mede capacidade de produção, HA ou RTO contratual; backup no mesmo host não cobre perda do host. O rollback foi validado para esta migração expansiva, não para migrações destrutivas. O experimento separado de quatro builds de cache não foi repetido nesta execução. SBOM/provenance não equivalem a assinatura nem certificação SLSA.

Instalações com o volume PostgreSQL Debian antigo precisam da [migração lógica documentada](runbooks.md#troca-da-base-postgresql-debian-para-alpine). A imagem atual recusa o volume incompatível antes de modificar seus arquivos.

## Repetir

Com Python 3.11+, Docker Linux containers e OpenSSL disponíveis:

```sh
python scripts/ops.py setup
python scripts/ops.py build --version 1.0.0
python scripts/ops.py scan --version 1.0.0
python scripts/ops.py prove
python scripts/ops.py report
```

O primeiro scan prepara a base. `prove` usa scan offline e recusa base ausente ou vencida. O [guia de demonstração](demo.md) apresenta os cenários; os [runbooks](runbooks.md) detalham operações e recuperação.

## Refinamento do relatório — 22/09/2026

Esta revisão muda o template, o CSS e a redação do relatório, sem alterar as operações Docker nem a seleção de evidências. Campos ausentes deixam de virar frases como “Não informado jobs”; métricas ausentes usam texto menor, e o bloco de scan aprovado não recebe cor de falha. `docs/report.html` foi gerado dos mesmos JSONs; as datas, IDs, resultados e arquivos históricos foram preservados. Não houve execução de serviços, builds, backup ou release nesta rodada.

- **47 testes do gerador aprovados** ([saída](evidence/interface-v2/report-tests.txt)); Ruff e formato dos scripts aprovados. Os testes cobrem tentativa inválida/falha, identidade independente e ausência de vínculo entre registros.
- **40 combinações** dos oito painéis com 1440, 1024, 768, 390 e 320 px, mais **20 combinações** de dez estados em desktop/celular: ausência, cópia sem restore, release inválida/falha e job na fila, em andamento, concluído, falho, desconhecido ou incompleto. **19 capturas**. [Registro de navegador](evidence/interface-v2/visual-review.json).
- Seis regressões de link de salto, navegação por teclado/histórico, seletor móvel, arquivos locais, impressão, detalhes sem JavaScript e transições com redução de movimento aprovados. [Script de regressão](../scripts/test_report_browser.cjs).

Para repetir somente a apresentação: `python scripts/report.py`, `python scripts/render_review.py`, `node scripts/visual-review.cjs` e `node scripts/test_report_browser.cjs`. Playwright é ferramenta opcional de desenvolvimento; `PLAYWRIGHT_MODULE` aceita um caminho externo e `PLAYWRIGHT_CHANNEL=msedge` seleciona Edge. Os cenários adicionais ficam em `artifacts/interface-review`, marcados como dados de teste; não entram na pasta de evidência operacional.

[Escopo e hashes da fonte](evidence/interface-v2/review-manifest.json). Ampliação por CSS a 200% foi conferida; zoom nativo, leitor de tela e paginação integral de PDF não foram auditados. Esses resultados não reatestam a prova operacional nem os scans descritos nas seções anteriores.

## Revisão editorial e demonstração real — 22/09, 12:31 UTC

O [registro novo](evidence/editorial-20260922/execution.json) identifica a base `46d43bf`, fontes sem mudança e builds locais com cache: versões 1/2, testes, banco e proxy. A API, worker e comandos não foram modificados nesta rodada. Novos projetos Docker executaram instalação, migração, HTTP, falhas controladas e restore. Não atribuo SBOM, attestations, scan ou CI históricos a essas imagens novas.

- `python -m unittest discover -s tests -v`: **58 casos**, efetivamente descobertos e aprovados (inclui dois casos OCI além dos cinco módulos de contratos).
- `python -m unittest discover -s scripts -p test_report.py -v`: **47 casos aprovados**.
- Aplicação e PostgreSQL: **116 aprovados**, sem skips; dois avisos de depreciação, registrados nos logs.
- Ruff, formato de 17 arquivos e mypy de 10 fontes: aprovados. HTTP concorrente e isolamento, SIGKILL/SIGTERM, readiness do banco e recriação: aprovados pela verificação completa.
- Operações: candidata falha de modo controlado; imagem anterior preserva seus dados; backup copiado, controle negativo, restore em outro volume, três jobs comparados e novo job concluído. Cleanup e restauração dos aliases originais conferidos.

**Retificação do registro visual:** [art-direction/checks.json](evidence/art-direction/checks.json) permanece intacto. Cinco comandos daquela rodada usaram `-s scripts` para suítes em `tests` e executaram zero testes. Seu código de saída zero não comprovava as suítes de contratos. A nova execução correta acima supre essa lacuna sem reescrever o passado; os 56 testes registrados na revisão v3 usaram outro comando e conservam seu escopo.

A [demonstração comentada](demo.md#execução-editorial-de-22092026) contém os valores pequenos, capturas junto dos casos e limites. Captura, execução de serviços, teste automatizado e medição histórica continuam separados. Os dados são sintéticos; todo o ambiente está no mesmo computador. A rodada não estabelece recuperação em outro host, capacidade máxima ou aprovação humana da interface.


### Scan posterior e correção restrita à imagem de testes

Analisei por ID as cinco imagens executadas, com Trivy 0.74.0 e base atualizada em 22/09/2026 às 07:24 UTC, todas as severidades, achados sem correção incluídos e arquivo de exclusões vazio. As versões 1/2 da aplicação, banco e proxy tiveram zero achados. A imagem de testes apresentou dois HIGH e um MEDIUM no inventário de dependências vendorizadas do pip; isso não invalida os resultados funcionais anteriores, mas impedia declarar a imagem pronta pelo gate de segurança.

Removi pip/ensurepip **depois** da instalação do lock no target `test`, assim como o target runtime já fazia. Não atualizei bibliotecas da aplicação nem alterei os contratos. A imagem corrigida foi construída em tag própria; lint, formato, tipos, ausência do instalador e os 116 testes com PostgreSQL passaram novamente (dois avisos de depreciação, sem skips). O novo scan teve zero achados. O projeto de teste foi removido; não foi necessário repetir SIGKILL, backup ou rollback, cujos processos e fontes ficaram intactos.

O [recibo de segurança](evidence/editorial-20260922/security.json) liga IDs, Dockerfile, comando, base, relatórios antes/depois e verificação da correção. `exit 0` da coleta JSON não é aprovação por si só: o gate foi conferido no conteúdo completo. Os relatórios anteriores permanecem presentes. O scan não substitui uma auditoria nova de attestations nem cobre deploy em produção.


Gitleaks 8.30.1 aprovou o histórico acessível e a cópia final dos arquivos públicos. O recibo novo continha dois hashes SHA-256 de fontes interpretados como chave pela regra genérica; recomputei ambos e acrescentei somente o caminho exato do recibo à regra existente, que exige os dois valores literais. Um controle positivo com token fictício no mesmo caminho continuou sendo detectado. [Escopo, capturas e revisão final](evidence/editorial-20260922/review.json). Isso não é uma exclusão de diretório nem de qualquer valor com 64 caracteres.
