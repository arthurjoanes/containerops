# Decisões técnicas

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
| Um scan sem achados é sempre aprovado? | Só se completou com base identificada e política satisfeita. Base ausente, execução interrompida ou findings corrigíveis bloqueantes não são aprovação. | `scan-*.json`, base/idade e relatório completo do Trivy. |
| Backup funcionando equivale a recuperação? | Não. Criar um dump só mostra que a exportação funciona; restaurar em outro volume, comparar resultados e processar novo job testa a recuperação. | `backup.json` e `restore.json`, com checksum e duração observada. |
| Rollback reverte o banco? | Aqui troca a imagem da aplicação. A expansão de schema da segunda release permite a primeira continuar funcionando. Não há downgrade destrutivo nem sobrescrita de dados recentes. | `release.json` e `rollback.json`; IDs anteriores e posteriores, schema e jobs preservados. |
| Compose local fornece alta disponibilidade? | Não. Todos os serviços e o backup local dependem de um único computador. Restart e recuperação de lease reduzem alguns incidentes de processo, não perda do host. | Limite explícito na arquitetura e nos runbooks. |

## Decisões

**Fila no PostgreSQL.** Transações, leases e resultados ficam no mesmo banco. Dispensa um broker, mas fila e API disputam recursos.

**Debian slim.** Aproveita wheels e Python para os healthchecks. Deps/test ficam separados do runtime.

**Readiness consulta o banco.** Liveness verifica só o processo da API. Com o banco fora, o cliente recebe erro e pode repetir usando a mesma chave.

**Secrets por serviço.** API/worker recebem apenas a credencial da aplicação. O banco recebe os arquivos para criar as roles. As permissões dos mounts são testadas porque Docker Desktop e Linux diferem.

**Migração separada.** App faz DML; migrator faz DDL; backup tem SELECT. A API controla o acesso por owner, sem RLS no banco.

**Pausa durante o backup.** Pausar a admissão e drenar a fila mantém snapshot e dump no mesmo estado. A pausa dura até terminar a cópia.

**TLS no proxy.** O cliente recebe a CA local explicitamente. A rede interna não usa TLS.
