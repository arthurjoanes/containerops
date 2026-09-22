# Runbooks

Os procedimentos usam os [comandos e validações](../scripts/ops.py), o [Compose](../compose.yaml), o [runner de prova](../scripts/proof.py) e a [entrada do PostgreSQL](../docker/db/entrypoint.sh). São instruções de operação local; roteiros ainda não executados continuam identificados.

Execute os comandos na raiz do projeto. Anote horário, versão e job antes de intervir.

Operações de escrita usam um lock no runtime. Aguarde a atual terminar; `status`, `logs` e `report` continuam disponíveis. O lock é liberado ao sair do processo. Não apague `operation.lock` nem execute comandos Docker manuais durante backup, restore ou troca de release.

`start` sem `--version` conserva a imagem salva e deriva dela a migração necessária.
Em um ambiente novo, a imagem padrão é 1.0.0. `start --version 2.0.0` seleciona
explicitamente a imagem 2 e schema 2; para atualizar uma stack com dados, use
`release --version 2.0.0`, que inclui backup e retorno automático. `release` sem
flag também seleciona 2.0.0. O retorno à imagem anterior usa `rollback`, nunca
`release --version 1.0.0` nem downgrade de schema.

```powershell
$project = (Get-Location).Path   # raiz do clone
Set-Location -LiteralPath $project
$env:CONTAINEROPS_RUNTIME = Join-Path $env:USERPROFILE 'AppData\Local\ContainerOps-runtime'
python .\scripts\ops.py status
python .\scripts\ops.py logs
```

A função abaixo fixa o projeto Compose.

```powershell
function dc {
    & docker compose --project-directory $project -p pf-containerops -f "$project\compose.yaml" @args
    if ($LASTEXITCODE -ne 0) { throw "Compose falhou: $LASTEXITCODE" }
}
```

## Opções e resultados

Use `python scripts/ops.py <comando> --help` para ver apenas as opções daquele
comando. `--version` pertence a build/start/scan/sbom/release; release aceita
somente 2.0.0. `--offline` pertence a scan e `--inject-smoke-failure` a release.
Opções desconhecidas ou sem efeito encerram com código 2 antes de tocar Docker.
Portas HTTP e TLS precisam ser diferentes, entre 1 e 65535 e fora da lista de
portas recusadas em `scripts/ops.py` (usadas por outros serviços locais). A ferramenta nunca tenta liberar uma porta ocupada por outro processo.

`test` roda qualidade e HTTP em projeto novo; `verify` acrescenta hardening e recuperação. `docs/evidence/verification-run.json` guarda run_id, project, início/fim UTC, status, full e SHA-256 dos arquivos. Falha no host ou na limpeza deixa status failed; passed só é gravado após remover os recursos temporários.

Os JSONs da execução ficam em `docs/evidence/runs/<run_id>/`. Os arquivos journey/hardening/recovery na raiz de evidence podem pertencer a outras execuções. Se o processo for interrompido, o estado pode ficar in_progress e o projeto de teste pode continuar existindo. Confira o project no manifesto antes da limpeza.

Um job failed encerra a espera da CLI. Falhas de transporte permitem repetir GET até o timeout.

## Processo encerrado

**Sintoma:** API indisponível ou fila sem progresso; `status` mostra processo encerrado/reiniciando. **Hipótese:** erro no processo, configuração ou término abrupto. Um healthcheck `unhealthy` com processo vivo é outro caso: Compose não reinicia automaticamente por esse motivo.

1. Execute `python .\scripts\ops.py logs` e `dc ps -a`. Examine categoria de erro, versão, `job_id` e tentativa, sem imprimir segredos.
2. Confira a saída do processo: `docker inspect --format '{{json .State}}' (dc ps -a -q worker)`. Substitua apenas pelo serviço afetado.
3. Corrija a causa indicada pelos logs e use `python .\scripts\ops.py start`. Esse caminho aguarda banco, executa migração explícita e aguarda readiness.
4. Execute `python .\scripts\ops.py demo` para conferir um novo job.

`verify` testa SIGKILL em um worker do projeto de teste, recupera a lease e confere o resultado.

## Banco indisponível

**Sintoma:** liveness continua respondendo; readiness e novas admissões retornam 503. **Hipótese:** banco parado, segredo incorreto ou conectividade perdida. O proxy pode responder 502 quando a própria API não está disponível; isso difere de readiness 503 emitida pela aplicação.

```powershell
dc ps -a db api worker
dc logs --tail 60 db api worker
dc exec -T db pg_isready -U containerops_admin -d containerops
```

Se o banco apenas foi parado, `dc up -d --wait db` o inicia preservando seu volume. Aguarde readiness e envie um novo job com `demo`. Se a senha mudou no arquivo, reiniciar não altera automaticamente o papel existente: os scripts de bootstrap rodam somente no primeiro volume. Corrija a divergência de credencial de forma explícita; não apague o volume para silenciar erro de autenticação.

Resultado do teste de banco indisponível: `evidence/recovery.json`.

## Permissão no volume

**Sintoma:** PostgreSQL registra `Permission denied` ou recusa ownership de `PGDATA`; helper não grava o dump. **Hipótese:** mount ou ownership divergente do contrato.

```powershell
dc logs --tail 60 db
docker inspect --format '{{json .Mounts}}' (dc ps -a -q db)
dc exec -T db sh -ec 'id; ls -ld /var/lib/postgresql/data /var/run/postgresql; grep "^Uid:" /proc/1/status'
```

O volume `pgdata` é exclusivo do banco. O volume `artifacts` dos helpers é outro volume, montado no mesmo caminho interno. A imagem própria mantém ownership do UID 999 em ambos. Confirme o nome real antes de qualquer alteração.

Corrija o mount/ownership e reinicie o serviço afetado. Não use `chmod 777` nem remova o volume. Se a causa continuar incerta, teste restore de um backup em outro projeto. PostgreSQL deve rodar com UID 999; o entrypoint inicializa o ownership antes disso.

## Troca da base PostgreSQL Debian para Alpine

As versões antigas usavam PostgreSQL Debian. A versão atual mantém PostgreSQL 17.11, mas muda libc e locale ao usar Alpine. Não reutilize diretamente o diretório de dados antigo. O entrypoint recusa PGDATA existente sem `.containerops-platform` com valor `alpine3.24`; essa recusa acontece antes de modificar os arquivos.

Antes de atualizar uma instalação com dados, execute `backup` na versão anterior e guarde dump e metadados. Construa a versão nova e use `restore-test` para verificar o dump em um volume novo: o comando compara snapshots e processa outro job. O teste não troca o volume principal.

A adoção definitiva do volume restaurado exige migração manual planejada: interromper escritas na versão anterior, gerar o backup final, restaurar em volume vazio com a nova imagem e conferir os resultados antes de mudar a configuração da stack principal. Preserve o volume anterior para retorno. Não crie o marcador manualmente para contornar a recusa; isso não converte formato, locale ou collation.

## Falha de migração

**Sintoma:** `start` encerra com código diferente de zero na etapa `migrate`; API pode marcar schema incompatível. **Hipótese:** SQL inválido, credencial de aplicação usada para DDL ou schema não compatível com a release.

```powershell
dc logs --tail 60 db
python .\scripts\ops.py start
```

`start` consulta o schema e mantém o target compatível. A imagem 1 lê schema 2; downgrade para 1 é recusado. Migração usa a credencial de migrator; API/worker usam a de aplicação. Se a migração falhar, corrija o erro e repita a etapa transacional. Não altere schema_version manualmente.

A migração é explícita e única na sequência de início, não executada concorrentemente em cada réplica. Restore usa banco novo e depois reaplica os grants via migração idempotente.

## Job parado

**Sintoma:** job permanece `queued` ou `running`, ou encerra como `failed`. **Hipótese:** worker ausente, lease ainda vigente, falha de banco ou limite de tentativas atingido. Pausar admissão recusa jobs novos; o worker continua drenando os que já estão na fila.

```powershell
dc run --rm --no-deps manage python -m containerops.manage snapshot
dc logs --tail 80 worker
dc ps worker
```

Compare estado, tentativas e logs pelo mesmo `job_id`. A duração sintética é limitada a 15 segundos; recuperar um worker pode aguardar o lease vencer. Um lease expirado permite nova tentativa, mas o token anterior não pode persistir a conclusão.

Se o worker foi parado, inicie pelo caminho normal e consulte o job novamente. Se a pausa não pertence a uma operação de backup/release em andamento, `dc run --rm --no-deps manage python -m containerops.manage resume` retoma admissão. Não retome durante a captura consistente do backup. Um job terminal falho não deve ter o contador zerado manualmente; investigue a causa e envie um novo job depois da correção.

## Imagem incompatível ou smoke reprovado

**Sintoma:** imagem candidata não atende readiness, informa versão inesperada ou reprova smoke. **Hipótese:** incompatibilidade de código/schema, imagem errada ou falha de aplicação.

```powershell
python .\scripts\ops.py status
python .\scripts\ops.py logs
docker inspect --format '{{.Image}}' (dc ps -q api)
```

`release` salva o ID anterior, faz backup, pausa/drena, migra e troca API/worker. Se o smoke falhar, volta à imagem anterior. Confira `evidence/release-failed.json` ou `evidence/rollback.json`, readiness e um novo job. `--inject-smoke-failure` provoca a falha do teste.

Para retornar uma atualização já concluída, use `python .\scripts\ops.py rollback`. O script consulta a imagem anterior salva e preserva o banco. Não faça downgrade automático de schema. Uma migração destrutiva ou contrato de dados incompatível impediria esse tipo de retorno: exigiria expansão/contração planejada e outro procedimento de recuperação.

Rollback manual compara imagem e jobs, processa novo texto e confere contagem/SHA-256 antes de salvar o estado. Se falhar, tenta repor a imagem ativa e retorna o erro. A pausa anterior é restaurada mesmo se a drenagem falhar. O último retorno concluído fica em `evidence/manual-rollback.json`.

## Backup ou restore reprovado

Para testar falha, release e restore sem alterar a demonstração principal, use
`python scripts/ops.py prove`. O scan desse comando é offline: prepare a base com
`python scripts/ops.py scan --version 1.0.0` depois de construir essa versão. Base
ausente ou fora da política encerra o comando como falha, não como scan aprovado.
Os backups do teste não substituem o ponteiro de backup da stack principal.

**Sintoma:** checksum divergente, `pg_restore` não conclui, snapshot restaurado difere ou novo job falha. **Hipótese:** artefato incompleto, ferramenta incompatível, backup inconsistente ou grants ausentes.

```powershell
python .\scripts\ops.py backup
python .\scripts\ops.py restore-test
```

Backup pausa e drena a fila até terminar snapshot e dump. Restore confere checksum, cria projeto/volume, restaura como migrator, compara os dados e processa um novo job. Se uma etapa falhar, o teste falha.

O artefato binário é exportado com `docker cp`; não use redirecionamento textual do PowerShell para `pg_dump`. Guarde o JSON de metadados junto ao dump. Backup no mesmo computador não protege contra perda do computador.

## Recuperação em outro computador — roteiro ainda não executado

**Objetivo:** conferir se uma cópia fora da máquina original permite retomar trabalho quando ela não está disponível. A prova local em volume novo valida o procedimento de restauração, mas não satisfaz esse objetivo. É necessário um destino independente, armazenamento protegido da cópia e acesso às imagens compatíveis.

1. Na origem, registre versão do schema, imagem, intervalo de captura e hashes do dump/metadados. Guarde também a referência das fontes e o artefato de imagem correspondente; reconstruir uma tag não garante obter a mesma imagem.
2. Transfira a cópia por um meio protegido e confira os hashes no destino. Mantenha dump e metadados privados: podem conter textos dos jobs e identificadores. Copiar apenas o arquivo JSON não restaura os dados.
3. Prepare um runtime novo, credenciais próprias e as imagens conferidas. Verifique a compatibilidade das ferramentas PostgreSQL e da plataforma antes de restaurar. Não reutilize um volume já preenchido nem o runtime principal da outra máquina.
4. Execute a restauração pelo contrato de destino descartável de `restore_test`, usando explicitamente a cópia escolhida. Confira IDs, proprietários, estados, contagens e checksums dos jobs restaurados; depois exija um novo job concluído.
5. Registre o tempo desde o início da preparação até o novo resultado, com fases separadas: obtenção de imagem/cópia, configuração, restore, validação e limpeza. Registre também a idade dos dados no ponto de corte. O tempo interno de `pg_restore` sozinho não representa o tempo total de recuperação.
6. Preserve o relatório, eventuais falhas e a conferência de que a origem permaneceu intacta. Remova somente os recursos descartáveis identificados. A adoção do destino como serviço principal exige um procedimento próprio de troca de acesso; este ensaio não faz essa mudança.

**Aceite pendente:** destino e cópia independentes da origem, hashes válidos, dados conferidos, novo trabalho concluído e durações observadas. Sem esse ensaio, não há RTO (prazo de recuperação) nem RPO (perda de dados tolerada) contratual demonstrado. Um teste em VM no mesmo host ajuda a examinar portabilidade, mas continua compartilhando a falha física.

## Parar e limpar

`python .\scripts\ops.py stop` mantém os volumes. Os scripts apagam só volumes dos projetos temporários que criaram. Não use `docker system prune` para limpar este projeto.

Runtime Windows: `%USERPROFILE%\AppData\Local\ContainerOps-runtime`.
