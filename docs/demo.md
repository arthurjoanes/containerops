# Demo de 5–8 minutos

Abra o [caderno de operações](report.html) para navegar entre **Verificação**,
**Recuperação**, **Release** e **Artefatos**. A seleção mostra resultado, identidade
do registro e arquivos de origem. O HTML é um snapshot; os comandos abaixo são
executados separadamente no terminal. [Guia de leitura](report-guide.md).

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
