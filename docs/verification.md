# Verificação

## Interface do relatório — 22/09/2026

A apresentação passou de uma página contínua para seleção de operações, com
detalhe, etapas e arquivos de origem. O modelo de evidências e a execução da
aplicação não foram substituídos. A nova navegação é local e funciona como
melhoria progressiva: sem JavaScript, todos os resultados continuam disponíveis.

Nesta revisão foram executados 45 testes do gerador e 30 testes de operações no
host, separadamente. Os casos cobrem também seleção estática, identidade separada
de backup/restauração e tentativa de release inválida. As capturas em
`docs/screenshots/report-*.png` vêm do HTML gerado com os JSONs versionados,
sem executar novamente a prova Docker abaixo.

A revisão no Edge conferiu os oito painéis em 1440, 768, 390 e 320 px, navegação
por teclado, histórico do navegador, links locais e leitura sem JavaScript.
Também foram conferidos impressão e reflow em viewport equivalente a zoom de
200%. Não houve overflow horizontal na página nem erros no console.

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
