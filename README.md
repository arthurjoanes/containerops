# ContainerOps

Laboratório de operação de uma aplicação com trabalhos em segundo plano: aceitar um pedido, recuperar um worker interrompido, trocar imagens e restaurar dados, mantendo o resultado verificável. Inclui API, worker, comandos de operação e relatório de evidências.

<!-- Navegação do README -->
<p>
  <a href="#demonstração"><img src="docs/readme/badges/demo.svg" alt="Demonstração" width="139" height="28"></a>
  <a href="#arquitetura"><img src="docs/readme/badges/architecture.svg" alt="Arquitetura" width="126" height="28"></a>
  <a href="#executar-localmente"><img src="docs/readme/badges/run.svg" alt="Executar localmente" width="107" height="28"></a>
  <a href="#verificação-e-evidências"><img src="docs/readme/badges/evidence.svg" alt="Verificação e evidências" width="119" height="28"></a>
  <a href="https://www.linkedin.com/in/arthur-joanes-6a2967373/"><img src="docs/readme/badges/linkedin.svg" alt="Arthur Joanes no LinkedIn" width="108" height="28"></a>
</p>

## Visão geral

A API recebe texto; um worker conta palavras e calcula o SHA-256 dos bytes; PostgreSQL guarda fila e resultado. O cálculo pequeno permite conferir o que aconteceu depois da recuperação. Textos e falhas são sintéticos; HTTP, processos e banco executam localmente no mesmo computador.

Pelo [contrato](docs/data-contract.md), a mesma chave e conteúdo recuperam o job existente; conteúdo diferente conflita. O [repositório transacional](app/src/containerops/repository.py) controla uma posse temporária (_lease_) e seu token, impedindo que um [worker](app/src/containerops/worker.py) antigo sobrescreva a aquisição atual.

<a id="na-prática"></a>

## Demonstração

![Catálogo do ContainerOps com identificação das cópias e estados separados de integridade e restore](docs/screenshots/backups-desktop.png)

O catálogo reúne os registros da
[rodada de backup e transporte](docs/evidence/containerops-evolution-20261012/manifest.json).
Localize uma cópia por ID ou origem, confira integridade e recibo de restore e
abra os detalhes para consultar a fonte ou preparar um comando local. O último
ensaio tem painel próprio, com checksum, snapshot, novo job e limpeza separados.

[Abrir o relatório local](docs/examples/report.html#backups) ·
[catálogo no celular](docs/screenshots/backups-mobile.png) ·
[guia de leitura](docs/report-guide.md).

A [paleta da marca](docs/brand.md) usa azul profundo `#142D4E`, azul de ação
`#1F5FBF` e ciano `#0F798A`, sobre fundo nuvem e superfícies brancas. Os estados
mantêm verde, âmbar e vermelho com rótulos. Source Sans 3 e os assets locais
permanecem incorporados ao relatório para leitura offline.

### Registro histórico de 22/09/2026

O [registro de restauração](docs/evidence/restore.json) de 22/09/2026, às 06:20 UTC,
recuperou três jobs e concluiu o novo trabalho em uma janela de 27,4 s.

Seis chamadas concorrentes enviaram `Olá mundo! Café e ação. 東京 42` com a mesma chave. Receberam um único ID e sete palavras; trocar o conteúdo retornou 409. A [jornada de 22/09/2026, 12:34 UTC](docs/evidence/editorial-20260922/journey.json) registra as respostas segundo os [critérios do caso](docs/problem-solution.md). Esse resultado demonstra o cenário registrado, não execução única de qualquer efeito externo.

<a id="conferir-uma-operação"></a>

A [sequência operacional](docs/operational-recovery.md) relaciona o job, a
restauração e o rollback aos respectivos registros. O [guia de leitura](docs/report-guide.md)
explica como conferir a origem e a identidade de cada operação no relatório.

<a id="implementação"></a>

## Arquitetura

```mermaid
flowchart TB
  C["Cliente<br/>Bearer + chave idempotente"] -->|"HTTP :8105"| P
  subgraph FRONT["Rede front"]
    P["NGINX :8080<br/>limita entrada HTTP"]
    A["FastAPI :8000<br/>owner + contrato do job"]
    P -->|"POST / GET jobs"| A
  end
  subgraph DATA["Rede data interna"]
    D[("PostgreSQL · pgdata<br/>fila, lease e resultado")]
    W["Worker<br/>contagem Unicode + SHA-256"]
    W -->|"claim / conclusão com token"| D
  end
  A -->|"SQL: admissão e consulta"| D
  O["CLI ops.py<br/>migração e backup"] -->|"pausa / dreno / pg_dump"| D
  O --->|"dump + restore-test"| T[("Volume novo<br/>dados + job de verificação")]
  O -.->|"evidências → report.py"| R["Relatório HTML<br/>execuções identificadas"]
```

As setas contínuas representam chamadas e transferência de dados; as pontilhadas, geração de evidências. A API pertence às redes `front` e `data`; worker e banco apenas à `data`. API e worker usam a mesma imagem em processos separados. PostgreSQL guarda a fila e o resultado na mesma linha; o worker busca trabalho no banco, sem chamada direta da API. O [Compose](compose.yaml) mantém todos os serviços no mesmo host e publica HTTP em loopback; o banco não publica porta no host.

| Responsabilidade | Implementação e garantia |
| --- | --- |
| Aceitar e consultar | [API](app/src/containerops/api.py) + [repository](app/src/containerops/repository.py): `UNIQUE(owner, idempotency_key)`, comparação do conteúdo e limite de 100 pendentes globais/20 por owner na transação. Corpo até 32 KiB e texto até 16 KiB. Retorna 201 para criação, 200 para replay e 409 para conteúdo divergente. |
| Executar e recuperar | [Worker](app/src/containerops/worker.py): despacho favorece o owner menos recentemente ativo; calcula fora da transação e só grava com lease de 5 s/token válidos. Trabalho interrompido pode repetir, até três tentativas. |
| Persistir e operar | [Conexões](app/src/containerops/database.py) e [CLI](scripts/ops.py): papéis distintos para DML, migração e backup. Guarda dump/hash/snapshot no runtime local e restaura em volume novo. Release troca API/worker; rollback preserva o schema expansivo. |
| Observar e apresentar | [Logs estruturados](app/src/containerops/log.py), healthchecks e métricas internas; [relatório](scripts/report.py) lê arquivos de execuções identificadas e apresenta evidências estáticas. |

O job passa por POST autenticado → admissão transacional → `queued` → claim com lease → cálculo → `succeeded` → GET pelo mesmo owner. Se o worker morrer, o próximo claim após expirar a lease conserva o ID e incrementa a tentativa. [Sequências, permissões e recuperação](docs/architecture.md) mostram o caminho normal e as operações de manutenção.

## Stack e decisões

<a id="stack"></a>

<p>
  <img src="docs/stack/python.svg" alt="Python" width="64" height="64">
  <img src="docs/stack/fastapi.svg" alt="FastAPI" width="64" height="64">
  <img src="docs/stack/postgresql.svg" alt="PostgreSQL" width="64" height="64">
  <img src="docs/stack/nginx.svg" alt="NGINX" width="64" height="64">
  <img src="docs/stack/docker.svg" alt="Docker" width="64" height="64">
</p>

| Escolha                 | Motivo e compromisso                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| FastAPI + worker Python | Separa admissão de cálculo; exige posse, renovação e tentativas.                                                         |
| PostgreSQL              | Reúne fila, estado e transações; API e fila disputam recursos.                                                           |
| NGINX + Compose         | Entrada HTTP/TLS e processos separados; não protege contra perda do host.                                                |
| BuildKit + Trivy        | Identifica artefatos e verifica vulnerabilidades conhecidas; scan não é certificação nem garantia de ausência de falhas. |

<a id="o-que-eu-implementei"></a>
<a id="decisões-que-podem-ser-conferidas"></a>

[Decisões, código e testes](docs/decisoes-tecnicas.md) · [bases fixadas por digest](docker/images.lock.json). Versões fixadas não significam versões mais recentes.

<a id="executar-e-verificar"></a>
<a id="rodar"></a>

## Executar localmente

Use Docker com containers Linux em x86-64, Compose v2, Buildx e Python 3.11+. São os requisitos adotados pelos [comandos](scripts/ops.py), [imagens](docker/images.lock.json) e [CI](.github/workflows/verify.yml). Primeiro build e scan precisam baixar dependências.

```sh
python3 scripts/ops.py setup
python3 scripts/ops.py build
python3 scripts/ops.py verify
python3 scripts/ops.py start
python3 scripts/ops.py demo
python3 scripts/ops.py report
```

No Windows, use `python` ou o [wrapper PowerShell](scripts/containerops.ps1), com Docker Desktop em modo Linux. O [Compose](compose.yaml) publica a API em [localhost:8105](http://localhost:8105). Jobs exigem Bearer; o setup gera tokens fictícios fora do Git, conforme o [roteiro](docs/demo.md).

<a id="operações"></a>

[Runbooks](docs/runbooks.md) detalham `backup`, `restore-test`, `release`, `rollback`, `scan` e o runtime. `stop` preserva o volume; um comando de limpeza deve usar o projeto e o destino identificados pela operação.

### Operar em um contexto separado

Defina `CONTAINEROPS_SESSION` antes de executar a CLI. No Windows, o contexto fica
em `%USERPROFILE%\AppData\Local\ContainerOps-runtime\sessions\<slug>`; a demonstração
sem sessão conserva seu runtime e nomes. Use um basename de até 32 caracteres,
com letras minúsculas, dígitos e hífens, sem hífen nas extremidades. Links,
junções, traversal e caminhos externos são recusados.

```powershell
$env:CONTAINEROPS_SESSION = 'backup-lab-20261012'
python scripts/ops.py setup
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
python scripts/ops.py verify
python scripts/ops.py prove --scenario operations
```

O contexto tem lock, segredos, builder, tags, cache e evidências próprios. Portas
dos novos ambientes usam loopback e portas livres. Docker, CPU e memória continuam
compartilhados; execute builds, scans e carga em sequência. O relatório da sessão
fica em `sessions/<slug>/docs/report.html`, junto de suas evidências. Os aliases
históricos do repositório permanecem separados.

### Transportar e selecionar backups

`backups list` mostra IDs, origem, integridade e proteção. `backup-export --backup
<id> --destination <pasta-nova>` cria um pacote privado com dump, metadados e
manifesto versionado. `backup-import --source <pacote>` confere os bytes e registra
outra cópia no contexto ativo, sem restaurar nem carregar imagens. Use o ID
retornado em `restore-test --backup <id> --trusted-source` para um pacote de origem
confiável. O comando sem `--backup` conserva a seleção pelo ponteiro legado.

A opção `--include-image` exporta a imagem de aplicação por ID. Seu carregamento
exige `backup-image-load --backup <id> --trusted-image-source`, em uma sessão;
a tag aplicada pertence somente a essa sessão. Imagens auxiliares compatíveis
precisam estar preparadas no destino. [Formato, limites e roteiro](docs/runbooks.md#pacote-portátil-de-backup).

`backups prune --keep-last 3` gera uma prévia. Remoção exige `--apply --plan-id
<id-do-plano>` e nova conferência sob lock. Latest, marcas de proteção, última
cópia válida e último restore verificado por origem são preservados. A retenção
dos jobs no banco é independente da retenção desses arquivos.

<a id="repetir-a-prova-completa"></a>

## Verificação e evidências

A [rodada de 12/10/2026](docs/evidence/containerops-evolution-20261012/manifest.json)
verificou o backup portátil e o catálogo sobre
`ec090fc33b89c848113537cdcdcb42f9888cc1d9`: exportação/importação entre contextos,
snapshot igual, novo job, credenciais próprias e hashes originais preservados.
Builds das duas releases, auditorias OCI/SBOM/provenance, qualidade, PostgreSQL,
rollback, release 2, TLS e hardening passaram. Os scans finais da aplicação 1/2,
banco e proxy tiveram um MEDIUM cada, sem HIGH/CRITICAL, pela política daquela rodada.
O [relatório dessa rodada](docs/evidence/containerops-evolution-20261012/report.html)
mantém suas identidades separadas das provas históricas abaixo.

A limpeza conferiu os dez projetos UUID e o builder próprio. Estado, ponteiro,
backups, tags compartilhadas e evidências históricas da demonstração foram
preservados. Retenção destrutiva foi testada somente em fixtures temporárias.
O transporte ocorreu no mesmo host; outra máquina, cópia física independente e
preparação das imagens auxiliares para offline completo continuam necessários.

| Pergunta                              | Fonte e data da execução                                                                                                                 | Limite                                                                                 |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Worker interrompido recupera o job?   | [Antes/depois do SIGKILL](docs/evidence/editorial-20260922/recovery.json), 22/09/2026, 12:36 UTC                                     | Mesmo ID concluiu na tentativa 2; cálculo pode repetir.                                |
| Os dados voltam após restore?         | [Restore JSON](docs/evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/restore.json), 22/09/2026, 12:38 UTC                     | Três jobs comparados e novo trabalho concluído; volume novo no mesmo host.             |
| A admissão se distribui entre owners? | [Manifesto da medição](docs/evidence/admission-measurement/20260922T031250-0300-b87b5952/manifest.json), 22/09/2026, 06:12 a 06:15 UTC | Três repetições: 120 aceitos/concluídos e 24 recusados; cenário local com dois owners. |

Para repetir a prova completa, prepare antes a base Trivy com `scan`; Docker e OpenSSL são necessários:

```sh
python scripts/ops.py prove
```

O [runner](scripts/proof.py) cria projetos descartáveis e registra manifestos por tentativa. Para executar somente job, rollback e restauração com as imagens já preparadas:

```sh
python scripts/ops.py prove --scenario operations
```

[Verificação e histórico de correções](docs/verification.md) · [fontes e afirmações](docs/fontes-e-afirmacoes.md).

<a id="limites-e-manutenção"></a>
<a id="segurança-das-imagens"></a>
<a id="limites"></a>

## Limites e segurança

- 16 KiB por texto; 100 jobs pendentes globais; 20 por owner; 3 tentativas; retenção de 24 h. São limites do [contrato](docs/data-contract.md), implementados no [domínio](app/src/containerops/domain.py) e no [repositório](app/src/containerops/repository.py); não medem capacidade.
- A demo permite atraso de até 15 s; `DEMO_MODE=false` aceita duração zero. A distribuição não interrompe jobs em execução nem garante prazo. [Configuração](app/src/containerops/config.py) e [validação](app/src/containerops/api.py).
- Backup local compartilha a falha do computador. O pacote permite transporte; proteção contra perda do host exige cópia física e destino independentes, ainda fora da prova local. TLS termina no proxy; rollback troca imagens sem rebaixar schema. [Runbooks](docs/runbooks.md).
- O scanner bloqueia HIGH/CRITICAL, inclusive sem correção. Um resultado sem achados vale para imagem/base/data registradas. [Política e escopo](docs/supply-chain.md).
- A [revisão de segurança de 03/10/2026](docs/SECURITY-REVIEW-2026-10-03.md) registra a avaliação de 18 itens de segurança, rate limit, prazo total do corpo, erros privados e testes com banco descartável.

## Documentação

| Para entender                    | Guia                                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Problema, componentes e escolhas | [Casos](docs/problem-solution.md) · [arquitetura](docs/architecture.md) · [decisões](docs/decisoes-tecnicas.md)                 |
| API, fila e idempotência         | [Contrato de dados](docs/data-contract.md)                                                                                      |
| Executar e recuperar             | [Demonstração](docs/demo.md) · [runbooks](docs/runbooks.md) · [recuperação](docs/operational-recovery.md)                       |
| Conferir resultados e fontes     | [Verificação](docs/verification.md) · [admissão](docs/admission-measurement.md) · [fontes e datas](docs/fontes-e-afirmacoes.md) |
| Relatório e manutenção           | [Guia do relatório](docs/report-guide.md) · [marca e paleta](docs/brand.md) · [manutenção](docs/frontend-quality.md) · [capturas](docs/screenshots.md) |

## Autor e licença

Para conversar sobre containers, recuperação e operação deste laboratório:

<p><a href="https://www.linkedin.com/in/arthur-joanes-6a2967373/"><img src="docs/contact/linkedin.svg" alt="" width="24" height="24"> <strong>Arthur Joanes no LinkedIn</strong></a></p>

[Licença MIT](LICENSE). Ícones da stack e LinkedIn: [Devicon licença MIT](docs/stack/LICENSE.devicon).
