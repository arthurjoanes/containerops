# Verificação

## Execução de 21/09/2026

`python scripts/ops.py prove` terminou com exit 0, 18 etapas aprovadas e
541,187 s, de `2026-09-21T06:39:25.126818+00:00` a
`2026-09-21T06:48:26.307017+00:00`.

Fontes: [manifesto da tentativa](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/manifest.json),
[log do console](evidence/problem-proof-console-20260921T063924859Z.log),
[ambiente](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/environment.json) e
[containers concorrentes no início](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/concurrent-containers.json).
Os 29 arquivos referenciados pelo manifesto tiveram seus SHA-256 conferidos ao
fim da execução. Depois dela, os caminhos locais dentro desses JSONs foram trocados
por `<localappdata>` e os hashes do manifesto foram atualizados para os arquivos
publicados. A árvore de trabalho não tinha commit; a execução usou as fontes com
fingerprint `8bdc1c955291394cbedee63315d1e4fa63ed68a022459a424f3bc601237ab322`;
`compose.yaml` mudou depois dela.
O mesmo conjunto de fontes foi conferido no início e no fim, e as fontes Python/SQL
foram comparadas dentro das duas imagens reais.

Ambiente: Windows com Python 3.11.9 no host; Docker Desktop Linux containers,
Engine 29.7.2, Compose 5.5.0, Buildx 0.36.1-desktop.1, plataforma linux/amd64.
Runtime Python 3.13.15, PostgreSQL 17.11 e Nginx 1.30.5. Bases/digests seguem
[images.lock.json](../docker/images.lock.json).

### Resultados separados por camada

- Aplicação: 102 casos aprovados, incluindo 14 integrações com PostgreSQL real;
  Ruff/formatação e mypy estrito aprovados. Duas advertências de depreciação
  Starlette/httpx e anyio continuam visíveis no resultado.
- Host/operação: 27 testes aprovados. Incluem worker com imagem divergente,
  perda/alteração do job candidato, ponteiro isolado de backup e manifesto que
  registra falha/interrupção sem reutilizar sucesso anterior.
- Relatório: 42 testes aprovados. Essas contagens são de suítes distintas;
  reexecuções de desenvolvimento não são somadas.

[Resultados dos comandos](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/verify-test-results.json).
O `verify` completo levou 186,812 s; seus experimentos operacionais são etapas
adicionais, não casos somados ao pytest.

| Contrato | Resultado | Arquivo |
|---|---|---|
| Jornada autorizada e idempotente | Seis POSTs retornaram um UUID; sete palavras e checksum esperado; conflito 409, anônimo 401, outro owner 404; exatamente três jobs no fim da jornada e nenhum criado pelas rejeições | [jornada](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/journey.json), [snapshot inicial](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/snapshot-before.json) |
| Lease e morte real do processo | Job observado running/tentativa 1 antes de SIGKILL; mesmo UUID succeeded/tentativa 2, cinco palavras e SHA-256 esperado. SIGTERM concluiu o atual e deixou o próximo queued/tentativa 0 até reiniciar | [recuperação](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/verify-recovery.json) |
| Readiness e persistência | Banco parado: live 200, ready/admissão 503. Schema incompatível também recusou readiness. Recriar containers preservou o snapshot de oito jobs desse experimento | [recuperação](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/verify-recovery.json) |
| Hardening efetivo | UID/caps/NNP, escrita negada, tmpfs/limites, secrets, redes e papéis SQL exercitados em containers reais | [hardening](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/verify-hardening.json) |
| Falha de atualização | Candidata 2 criou um job no schema 2; falha controlada retornou API e worker à imagem 1, preservando UUID, tentativa, contagem e checksum desse novo job | [rollback automático](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/rollback.json) |
| Promoção e retorno manual | Snapshots progrediram de 3→5→6→7 jobs sem alterar resultados anteriores; schema 2 permaneceu. TLS acrescentou o oitavo, usando CA explícita e recusando confiança padrão | [promoção](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/release.json), [retorno](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/manual-rollback.json), [TLS](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/tls.json) |
| Backup restaurado e utilizável | Checksum do dump válido, oito jobs iguais em projeto/volume novos e um novo job concluído: quatro palavras e checksum esperado. Recuperação observada: 27,016 s, sem incluir limpeza | [backup](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/backup.json), [restore](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/restore.json) |
| Isolamento da demonstração principal | Hashes de state.json e latest-backup.json iguais antes/depois; operações usaram apenas dois projetos de teste e um de restore. Zero recursos restantes desses três projetos; builder exclusivo parado | [antes](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/main-runtime-before.json), [depois](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/main-runtime-after.json), [observação posterior da limpeza](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/cleanup-observed.json) |

O job pós-migração preservado foi `ffd4f48f-a91e-4306-813c-144b96005b17`:
quatro palavras, checksum `282c8eb02af81f89071d6f5021a0813c5b1d21b67d73ce072e11add48ddd8895`.
A versão da resposta mudou de 2.0.0 para 1.0.0; o resultado persistido não mudou.

### Artefato e risco residual

API/worker foram inspecionados pelos IDs do daemon:

- 1.0.0: `sha256:8694067e01fcdeda30a787a34ca068d9e7d21c65ac09d63e5127186c01c991ba`.
- 2.0.0: `sha256:8820cba2722717b9ed45387b1cefa9456e8fe78febcf6eb60100f79add670f94`.

Esses IDs não são confundidos com config digest, manifesto de plataforma OCI ou
índice com attestations. As [auditorias 1](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/audit-1.0.0.json)
e [2](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/audit-2.0.0.json) ligaram
config/camadas exportadas pelo daemon ao OCI e seus subjects, verificaram 121 pacotes
no SBOM e as versões do lock, materiais da provenance e ausência da sentinela nos
lugares auditados. Não cobrem assinatura nem SLSA.

Cada artefato teve 247 ocorrências sem correção, incluindo 55 HIGH e 5 CRITICAL.
Não são 494 riscos distintos nem uma união de CVEs deduplicada. A base Trivy usada
nos [scans](evidence/problem-proof/aec402ee34b14bff88ac2050108dfd1b/scan-2.0.0.json)
foi atualizada em `2026-09-20T19:19:55.870896177Z`, com idade de cerca de 11,3 h,
sob limite de 72 h. A política passou porque não havia HIGH/CRITICAL corrigíveis.

### Limites

Os critérios da [tabela de cenários](problem-solution.md) passaram. O teste de
quatro builds de cache não foi repetido nesta execução. O domínio é sintético, os
resultados são locais e finitos, e não cobrem adoção externa, capacidade de
produção, HA, RTO contratual ou reprodutibilidade byte a byte. Backup no mesmo
host não cobre perda do host; RPO depende da frequência de backup. Rollback depende
desta migração expansiva específica, não resolve migrações destrutivas em geral.

O guia [demo](demo.md) usa esta execução em uma apresentação de 5–8 minutos.

O SIGTERM da API pode terminar com 143 nesta versão de Uvicorn após reemitir o sinal.
A aprovação exige logs de encerramento/lifespan, ausência de OOM e resultado após
reiniciar, não só código de saída. Healthcheck unhealthy sozinho não dispara
restart automático do Compose.

## Repetir

Na raiz do projeto, com Python 3.11+, Docker Linux containers e OpenSSL para TLS:

```powershell
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
python scripts/ops.py verify
python scripts/ops.py sbom --version 1.0.0
python scripts/ops.py sbom --version 2.0.0
python scripts/ops.py scan --version 1.0.0 --offline
python scripts/ops.py scan --version 2.0.0 --offline
python scripts/ops.py report
```

Offline exige base válida já preparada. Para atualizar uma stack com dados, use release 2 conforme os [runbooks](runbooks.md). `start` sem versão mantém a imagem salva; `stop` mantém o volume principal. Só volumes de projetos de teste são removidos automaticamente.
