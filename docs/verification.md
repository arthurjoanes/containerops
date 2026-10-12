# Verificação

## Relatório e critérios de restore

O [relatório de exemplo](examples/report.html) apresenta os registros
da [rodada operacional de 12/10/2026](evidence/containerops-evolution-20261012/manifest.json).
Abrir o HTML consulta evidências salvas e não executa operações. Sem JavaScript,
todas as vistas e os detalhes dos registros continuam disponíveis.

O resultado de restore exige status aprovado, checksum válido, snapshot equivalente, novo job concluído e limpeza confirmada. Falha de limpeza invalida a aprovação; registro legado sem limpeza permanece parcial. O catálogo é um registro de consulta com contexto próprio, sem executar operações. O relatório de sessão acompanha textos Markdown selecionados em `docs/documentation/<digest>/`; os uploads do CI incluem esse diretório para resolver os links diretos dos guias.

A [prova completa](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/manifest.json), a [execução local](evidence/editorial-20260922/execution.json) e o [scan/correção](evidence/editorial-20260922/security.json) registram as rodadas de **22/09/2026**. Contagens descrevem as suítes e versões identificadas nesses recibos.

## Verificar sessão, transporte e catálogo

As novas suítes em `tests/test_runtime_context.py`, `tests/test_backup_package.py`
e `tests/test_backup_catalog.py` cobrem resolução e locks sem Docker, validação
do formato/cópia, seleção por ID e planos de retenção em fixtures temporárias.
Os casos de pacote incluem corrupção, conjuntos incompletos, schema futuro,
imagem ausente/incompatível, corte inválido, links/junções e falhas de cópia.
Retenção confere prévia sem escrita, IDs esperados, proteções obrigatórias,
mudanças desde o plano, arquivos desconhecidos e retomada por journal. Esses
ensaios não removem backups existentes do usuário.

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s scripts -p test_report.py -v
$env:CONTAINEROPS_SESSION = 'verificacao-20261012'
python scripts/ops.py setup
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
python scripts/ops.py sbom --version 1.0.0
python scripts/ops.py sbom --version 2.0.0
python scripts/ops.py scan --version 1.0.0
python scripts/ops.py scan --version 2.0.0
python scripts/scan_services.py --offline
python scripts/ops.py verify
python scripts/ops.py prove --scenario operations
python scripts/ops.py report
```

Execute build/scan/carga em sequência, com as fontes estáveis. `verify` roda Ruff,
formato, mypy e testes da aplicação com PostgreSQL descartável, além das suítes do
host e relatório. O cenário curto compara as fontes atuais às duas imagens e
provoca rollback real, mas exige scans/SBOM separados para essas mesmas imagens.
A existência dos comandos ou de provas históricas não aprova uma imagem nova.

Complete a rodada com exportação/importação entre dois contextos reservados,
restore por ID, snapshot igual e novo job correto. Registre hashes antes/depois,
credenciais próprias do destino, estado da origem e ausência de recursos
temporários. A corrupção deve falhar antes de criar stack/volume. O
[runbook](runbooks.md#pacote-portátil-de-backup) fornece a sequência de transporte;
offline completo depende de imagens auxiliares compatíveis preparadas à parte.

O manifesto da prova registra fontes, revisão Git, hashes de diff/status das
fontes e imagem; HEAD sozinho não identifica alterações locais. Edição durante a
rodada invalida os checks afetados. Evidências da sessão ficam no runtime próprio
e não substituem aliases históricos globais. Publique resumos curados com fases,
comandos, hashes, falhas e limpeza; mantenha dump, credenciais, paths pessoais e
inventários de outros projetos privados.

Transporte entre contextos locais valida o procedimento no mesmo computador.
Outro host e armazenamento físico independente continuam necessários para
proteção contra perda da máquina. Tempos sob concorrência não são RTO garantido.
Os registros históricos seguintes conservam suas fontes, datas e limites.

## Rollback e restauração — 22/09 às 06:17 UTC

A [sequência operacional](operational-recovery.md) passou em 139,750 s: job inicial identificado, candidata gravando antes da falha controlada, retorno à imagem anterior com dados preservados, cópia privada e restore em volume novo. Três jobs foram comparados e outro foi concluído no destino. O restore levou 27,437 s no intervalo interno e 29,578 s incluindo preflight/limpeza. Origem e backup original permaneceram iguais; a cópia adulterada foi recusada antes de criar o destino.

[Manifesto](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json) e [sequência operacional](operational-recovery.md) identificam fontes, imagens, marcos de tempo e limites. `operations-proof-latest.json` é o ponteiro desse cenário curto; a prova completa histórica abaixo permanece separada. Não houve novo scan nem recuperação em outro computador nesta execução.

Depois do ajuste de serialização LF, passaram no host 14 testes da nova prova, 28 de operações existentes e 47 do relatório: [resultados e hashes](evidence/operations-regression/post-serialization-results.json).

O primeiro scan de histórico dessa publicação apontou 13 ocorrências nos hashes de `api.py` e `test_api.py`. Os valores foram recalculados a partir das fontes antes de acrescentar exceções limitadas aos dois caminhos de manifesto e aos dois hashes exatos. O scan local do histórico passou; um controle separado confirmou que uma credencial sintética diferente no mesmo caminho continuava detectável. [Classificação e controle](evidence/operations-regression/secret-fingerprint-review.json). Esse resultado não é scan das imagens.

## Admissão e espera — 22/09 às 06:12 UTC

A [medição limitada](admission-measurement.md) executou três repetições com banco vazio, dois proprietários e 48 pedidos por repetição. Foram 120 admissões, 24 recusas por quota do proprietário e 120 resultados corretos, sem erros de transporte ou observações censuradas. O worker foi parado graciosamente durante admissão; a espera induzida e a incerteza do instante de retomada estão explícitas na análise. Não houve saturação global ou teste com histórico crescente.

O [manifesto](evidence/admission-measurement/20260922T031250-0300-b87b5952/manifest.json) identifica imagem, fontes, ambiente, amostras e limpeza. Os 14 testes do medidor e os pedidos HTTP são denominadores diferentes. A imagem foi reconstruída após recusa de uma versão antiga; esta medição não executou novo scan. Ajustes posteriores de `ops.py`/`proof.py` para a restauração estão no [suplemento datado](evidence/admission-measurement/20260922T031250-0300-b87b5952/post-measurement-supplement.json); não são apresentados como bytes reexecutados pelo medidor.

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

| Cenário                               | Resultado e evidência                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Jornada e autorização                 | Seis POSTs idempotentes retornaram o mesmo UUID; contagem/checksum corretos; conflito 409, anônimo 401 e outro owner 404. [Jornada](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/journey.json).                                                                                                                                                                                                                                      |
| Falha e recuperação de processos      | SIGKILL recuperou o mesmo job na tentativa 2. SIGTERM concluiu o atual e deixou o próximo aguardando reinício. [Recuperação](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-recovery.json).                                                                                                                                                                                                                                     |
| Banco e persistência                  | Banco parado e schema incompatível recusaram readiness; recriação preservou dados. [Recuperação](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-recovery.json).                                                                                                                                                                                                                                                                 |
| Isolamento e permissões               | UID, capabilities, NNP, mounts somente leitura, secrets, limites, redes e papéis SQL exercitados em containers reais. [Hardening](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/verify-hardening.json).                                                                                                                                                                                                                               |
| Atualização e rollback                | A candidata gravou no schema 2; falha controlada restaurou API/worker anteriores preservando o novo job. Promoção e rollback manual também preservaram resultados. [Falha controlada](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/rollback.json), [promoção](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/release.json), [retorno manual](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/manual-rollback.json). |
| TLS                                   | Conexão aprovada com CA explícita e recusada pela confiança padrão; outro job concluído. [TLS](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/tls.json).                                                                                                                                                                                                                                                                               |
| Backup e restore                      | Checksum correto, 8 jobs iguais em volume novo e outro job processado após restauração. Recuperação observada: 45.281 s, sem incluir limpeza. [Backup](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/backup.json), [restore](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/restore.json).                                                                                                                                   |
| Preservação da demonstração principal | Hashes de state.json e latest-backup.json iguais antes/depois; somente projetos temporários foram removidos. [Antes](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/main-runtime-before.json), [depois](evidence/problem-proof/881fdd3dd92f4b7f86c6862e9022a589/main-runtime-after.json).                                                                                                                                              |

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

## Execução de 22/09, 12:31 UTC

O [registro novo](evidence/editorial-20260922/execution.json) identifica a base `46d43bf`, fontes sem mudança e builds locais com cache: versões 1/2, testes, banco e proxy. A API, worker e comandos não foram modificados nesta rodada. Novos projetos Docker executaram instalação, migração, HTTP, falhas controladas e restore. SBOM, attestations, scan e CI históricos não atestam essas imagens novas.

- `python -m unittest discover -s tests -v`: **58 casos**, efetivamente descobertos e aprovados (inclui dois casos OCI além dos cinco módulos de contratos).
- `python -m unittest discover -s scripts -p test_report.py -v`: **47 casos aprovados**.
- Aplicação e PostgreSQL: **116 aprovados**, sem skips; dois avisos de depreciação, registrados nos logs.
- Ruff, formato de 17 arquivos e mypy de 10 fontes: aprovados. HTTP concorrente e isolamento, SIGKILL/SIGTERM, readiness do banco e recriação: aprovados pela verificação completa.
- Operações: candidata falha de modo controlado; imagem anterior preserva seus dados; backup copiado, controle negativo, restore em outro volume, três jobs comparados e novo job concluído. Cleanup e restauração dos aliases originais conferidos.

A [demonstração comentada](demo.md#execução-editorial-de-22092026) contém os valores pequenos, JSONs de cada caso e limites. Os dados são sintéticos; todo o ambiente está no mesmo computador. A rodada não estabelece recuperação em outro host nem capacidade máxima.

### Scan posterior e correção restrita à imagem de testes

O scan analisou por ID as cinco imagens executadas, com Trivy 0.74.0 e base atualizada em 22/09/2026 às 07:24 UTC, todas as severidades, achados sem correção incluídos e arquivo de exclusões vazio. As versões 1/2 da aplicação, banco e proxy tiveram zero achados. A imagem de testes apresentou dois HIGH e um MEDIUM no inventário de dependências vendorizadas do pip; isso não invalida os resultados funcionais anteriores, mas impedia declarar a imagem pronta pelo gate de segurança.

A correção removeu pip/ensurepip **depois** da instalação do lock no target `test`, assim como o target runtime já fazia. As bibliotecas da aplicação e os contratos permaneceram iguais. A imagem corrigida foi construída em tag própria; lint, formato, tipos, ausência do instalador e os 116 testes com PostgreSQL passaram novamente (dois avisos de depreciação, sem skips). O novo scan teve zero achados. O projeto de teste foi removido; não foi necessário repetir SIGKILL, backup ou rollback, cujos processos e fontes ficaram intactos.

O [recibo de segurança](evidence/editorial-20260922/security.json) liga IDs, Dockerfile, comando, base, relatórios antes/depois e verificação da correção. `exit 0` da coleta JSON não é aprovação por si só: o gate foi conferido no conteúdo completo. Os relatórios anteriores permanecem presentes. O scan não substitui uma auditoria nova de attestations nem cobre deploy em produção.

Gitleaks 8.30.1 aprovou o histórico acessível e a cópia final dos arquivos públicos. O recibo novo continha dois hashes SHA-256 de fontes interpretados como chave pela regra genérica. Ambos foram recalculados a partir das fontes, e a exceção foi limitada ao caminho exato do recibo e aos dois valores literais na regra existente. Um controle positivo com token fictício no mesmo caminho continuou sendo detectado. [Registro do scan de segredos](evidence/editorial-20260922/review.json). Isso não é uma exclusão de diretório nem de qualquer valor com 64 caracteres.

## Entrega verificada em 12/10/2026

O [manifesto da rodada](evidence/containerops-evolution-20261012/manifest.json)
identifica fontes estáveis em `ec090fc33b89c848113537cdcdcb42f9888cc1d9`, imagens,
comandos, falhas, hashes e limpeza. Foram executados 127 testes unittest no host,
com dois skips por privilégio de symlink; os testes reais de junções Windows e
hardlinks passaram. Os 48 testes do relatório e 126 pytest da aplicação com
PostgreSQL passaram, com dois avisos no pytest. Ruff check/formato e mypy passaram.

As duas releases tiveram build e auditoria OCI com SBOM/provenance. `verify`,
`prove --scenario operations` e transferência para um contexto reservado novo
exercitaram rollback provocado, release 2 bem-sucedida, TLS e hardening.
Exportação/importação e restore por ID conferiram snapshot completo, novo job,
credenciais independentes, hashes originais e preservação da origem. A retenção
destrutiva ficou limitada às fixtures; não houve pruning dos backups do usuário.

O primeiro scan bloqueou CVE-2026-4775 no tiff 4.7.1-r0 do proxy. O pin restrito
para 4.7.2-r0 foi seguido de nova execução de todos os gates sobre as fontes
estáveis acima. Os scans finais da aplicação 1/2, banco e proxy registraram,
cada um, MEDIUM 1, HIGH 0 e CRITICAL 0. A política de bloqueio e as exclusões não
foram afrouxadas.

A conferência final encontrou ausentes os dez projetos UUID e removeu somente o
builder da rodada. Estado, latest e backups da demonstração preservaram bytes e
mtime nas visões nativa e do app; tags compartilhadas e `docs/evidence`/relatório
históricos permaneceram iguais. O
[HTML da tentativa](evidence/containerops-evolution-20261012/report.html) usa
resumos curados, sem publicar dumps, credenciais ou caminhos pessoais.

O ensaio de transporte usou dois contextos no mesmo computador. Outro host,
cópia física independente e imagens auxiliares preparadas para offline completo
continuam necessários. As durações observadas não estabelecem RTO garantido.

### Correção de permissões e regressão Linux

O primeiro CI Ubuntu recusou o lock da sessão, criado com modo 0644. A correção
em `faba051d31fb586777dc7dcca436937a84173acc` cria o lock e os JSONs privados com
0600 e protege o runtime com 0700. A validação de setup também aceita seus
subdiretórios em comandos repetidos. Os fixtures de contexto agora informam a
mesma base ao processo filho no Windows e no Linux.

O [recibo complementar](evidence/containerops-evolution-20261012/posix-followup.json)
registra fontes estáveis, 128 testes do host nas duas plataformas e 48 testes
do relatório em cada uma, além de Ruff e formato. Windows teve três skips;
Linux teve dois casos exclusivos de junções Windows, com permissões POSIX e
symlinks efetivamente testados. IDs da demo, estado, ponteiros, backups, tags
compartilhadas e evidências históricas permaneceram iguais. Esse recibo cobre
a correção; a prova completa anterior continua ligada a `ec090fc`.

O CI seguinte passou por verify e prova de operações, mas recusou o export da
imagem no store clássico do Docker. Nesse store, ImageID é o digest da config;
o archive contém um manifesto OCI sintetizado com outro digest. A correção em
`a88b0b8437a1d1ab67cac70196c8279c9f998fd7` confere essa relação e os descriptors
de config/camadas por conteúdo, tamanho e mediaType, mantendo a recusa de tags.
O formato v1 do pacote permanece igual.

O [recibo de compatibilidade](evidence/containerops-evolution-20261012/ci-archive-compatibility.json)
registra 131 testes de host nas duas plataformas (três skips no Windows, dois no
Linux), 48 testes do relatório em cada uma e 24 casos focados do pacote. A nova
inspeção também aprovou o archive real do store containerd, sem alterar seus
bytes. As provas completas, os recibos de regressão e o CI têm seus próprios
escopos e referências de fonte.
