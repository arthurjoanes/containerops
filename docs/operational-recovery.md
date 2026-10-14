# Preservar trabalhos durante rollback e restauração

O [manifesto de 22/09/2026, 06:17–06:20 UTC](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json), o [rollback](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/rollback.json) e o [restore](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/restore.json) registram a mesma tentativa histórica.

## Transporte e seleção no contrato atual

O procedimento atual aceita um pacote plano de transporte e seleção por ID.
`backup-export` cria pasta privada nova com manifesto v1, dump e metadados;
`backup-import` valida o pacote e registra uma cópia no runtime de destino.
Importar preserva os bytes da origem e o ponteiro da demonstração principal,
sem executar restore ou carregar imagem. O
[runbook do pacote](runbooks.md#pacote-portátil-de-backup) define comandos e limites.

Use `CONTAINEROPS_SESSION` diferente em origem/destino, sempre dentro da raiz
reservada. `restore-test --backup <id-importado> --trusted-source` exige uma cópia
catalogada e preflight de hash, ferramenta, schema e imagem. Um byte corrompido
falha antes da criação de stack ou volume. O destino usa credenciais próprias,
restaura como migrator, reaplica grants e compara o snapshot completo. Outro job
precisa concluir com contagem e checksum corretos; limpeza faz parte do sucesso.

Uma imagem local pode faltar em outro daemon. `backup-export --include-image`
inclui o archive da aplicação por ImageID; `backup-image-load --backup <id>
--trusted-image-source` valida suas identidades e aplica apenas a tag da sessão,
recusando colisões. O carregamento é explícito e separado da importação.
Banco/proxy e outras imagens auxiliares compatíveis precisam estar preparadas no
destino. A ferramenta não substitui imagem ausente por uma tag reconstruída.

O catálogo registra restore verificado somente depois de snapshot, novo job e
limpeza aprovados, com recibo ligado aos hashes dos artefatos. A prévia de retenção
protege esse último recibo por origem, além de latest, marcas e última cópia
válida. [Plano e recuperação de interrupção](runbooks.md#catálogo-e-retenção-de-arquivos).

Ensaiar dois contextos no mesmo host verifica transporte e procedimento. Proteção
contra perda física ainda exige outra máquina e cópia independente. Restore
executa conteúdo escolhido pela origem; hashes não autenticam dump e manifesto
adulterados conjuntamente. Aceite somente origem confiável e mantenha o destino
descartável. Os tempos dependem dos recursos compartilhados do host e não
estabelecem RTO garantido. A adoção como serviço principal tem procedimento próprio.

O rollback deve preservar os trabalhos já aceitos. A restauração deve devolver os dados corretos e permitir que outro job conclua.

A execução `fc39e58c890841a089c9b1173869b0aa`, em **22/09/2026, 06:17:46–06:20:05 UTC**, testou essa sequência com containers e PostgreSQL reais, textos sintéticos e namespaces descartáveis. Terminou aprovada em **139,750 s**, incluindo a limpeza. O [manifesto da operação](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json) registra fontes, imagens, etapas e artefatos. É um cenário limitado de operações, separado da prova completa de build, segurança, TLS e integração.

## 1. Identificar o trabalho antes de mudar a versão

A release 1 processou `Trabalho identificado e preservado`: **quatro palavras**, checksum dos bytes originais e ID `29a855d0-7e64-4055-9f9e-0004c4ff4175`. Esse registro permite conferir preservação por identidade e resultado, em vez de observar apenas uma mensagem de sucesso. [Job inicial](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/demo.json).

## 2. Voltar a imagem depois de uma falha real da candidata

A release 2 ampliou o schema e concluiu outro job. Em seguida, o runner provocou falha no teste de prontidão funcional da candidata (_smoke test_). A operação voltou API e worker à imagem anterior, sem rebaixar o schema nem restaurar dados antigos.

O job criado pela candidata, `d734dd01-4e3e-4d37-8f50-b2e6b930834b`, continuou consultável, com quatro palavras e o mesmo checksum. A release 1 também concluiu `Rollback preserva dados`, com três palavras. A origem passou a ter **três jobs concluídos**, preservando o inicial. [Resultado do rollback](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/rollback.json).

Essa estratégia depende da compatibilidade da migração expansiva. Não cobre uma mudança que remova campos necessários à versão anterior. `version` na resposta identifica a aplicação que respondeu à consulta; comparar o resultado persistido não exige que a versão da resposta continue sendo 2.0.0 após o rollback.

## 3. Restaurar a cópia e continuar trabalhando

O backup pausou admissões, drenou a fila e exportou o dump. Uma cópia do dump **e dos metadados** foi guardada em diretório privado novo, fora do repositório. Os hashes conferiram; as permissões Windows foram verificadas para a pasta e os dois arquivos, limitadas ao usuário atual e SYSTEM. Isso é controle de acesso local, não criptografia nem armazenamento independente do computador. [Cópia e proteção observadas](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/protected-copy.json).

O destino foi outro projeto e volume vazio. A restauração conferiu o checksum, os três jobs e o snapshot completo do contrato — IDs, proprietários, estados, tentativas, contagens, checksums, schema e pausa. Depois processou `Backup restaurado com sucesso`: **quatro palavras**, ID `5787bb14-bd06-4e2d-83b3-9a101a8a5b6d` e checksum esperado. [Resultado da restauração](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/restore.json).

A [conferência da origem](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/source-preservation.json) encontrou os mesmos jobs, imagens e estado de pausa após o ensaio, além do backup original íntegro. Os arquivos de estado do ambiente principal também foram comparados antes/depois. O destino e a origem descartáveis foram removidos; o resultado só passou a aprovado depois das verificações de limpeza.

## 4. O que os tempos medem

| Medida               | Observado | Fronteira                                                                                                      |
| -------------------- | --------: | -------------------------------------------------------------------------------------------------------------- |
| Cenário completo     | 139,750 s | Preparação e conferências, job, rollback, backup/cópia, controle negativo, restore e limpeza da origem         |
| Operação de rollback |  58,625 s | Chamada da operação de release com falha controlada e retorno validado                                         |
| Backup               |  10,968 s | Chamada de backup, incluindo retorno ao estado de pausa anterior                                               |
| Cópia protegida      |   0,516 s | Cópia e conferência de hashes/permissões                                                                       |
| Recuperação interna  |  27,437 s | Após preflight/configuração do destino até validar o novo job; campo histórico `recovery_seconds`              |
| Restore completo     |  29,578 s | Entrada no restore, checksum/preflight, recuperação, limpeza e conferência de ausência dos recursos do destino |

As fases internas constam no JSON. Elas não devem ser somadas aos totais acima como trabalho adicional: são intervalos contidos na mesma operação. São tempos de uma execução local com três jobs, não SLA ou comparação de desempenho com a [prova histórica de oito jobs](verification.md#execução-de-22092026-utc).

O corte dos dados foi observado entre **06:19:21.653220 e 06:19:26.546757 UTC**, durante pausa/drenagem e obtenção do snapshot. Quando o novo job foi validado, esse corte tinha idade entre **32,277 e 37,170 s**. A idade desde a criação dos metadados era 31,384 s; são marcos diferentes. Não houve escrita comercial contínua nesta fixture e esse resultado não estabelece uma política de RPO.

## 5. Controles e limites do registro

Uma cópia separada foi adulterada; o restore recusou seu checksum **antes de criar o destino**. O backup válido permaneceu intacto. Esse controle detecta divergência entre arquivo e manifesto; não autentica um par de arquivo/manifesto alterado conjuntamente. [Recusa registrada](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/corrupted-copy-rejected.json).

O restore só registra sucesso depois da limpeza. `recovery_seconds` preserva a duração interna; a duração total e as fases identificam os intervalos de preflight, recuperação e limpeza.

O cenário de operações usa `operations-proof-latest.json`. O cenário completo mantém `problem-proof-latest.json`. As contagens das suítes de testes são separadas dos quatro jobs observados entre origem e destino.

Os JSONs originais do Windows foram preservados privadamente. As cópias públicas usam LF e têm referências recalculadas, descritas no [registro de publicação](evidence/operations-publication.json). Foram recuperados os bytes de 62 dos 63 arquivos de fonte da execução; o CSS anterior conserva apenas seu hash histórico. Não há comprovação byte a byte do conjunto completo de fontes. Os hashes de dump, metadados privados e imagens permanecem os originais.

## 6. Como repetir o cenário histórico e o que ainda falta

Os comandos abaixo registram a preparação da tentativa histórica, no contexto
padrão. O nome `pf-containerops-builder` pertence à demonstração padrão; esse
bloco não é um roteiro para uma sessão isolada.

```sh
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
docker buildx stop pf-containerops-builder
python scripts/ops.py prove --scenario operations
```

Para uma execução nova em computador compartilhado, configure primeiro o contexto
de sessão dos [runbooks](runbooks.md#contexto-de-sessão). Mantenha essa seleção
durante os comandos abaixo, que derivam os nomes da sessão:

```sh
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
python scripts/ops.py prove --scenario operations
```

Não pare o builder padrão como parte dessa nova sessão. Qualquer intervenção
manual deve conferir o nome e a propriedade do recurso no contexto selecionado.

Os builds são preparação; o cenário confere as fontes dentro das imagens antes de reutilizá-las. Use uma janela sem outra operação no builder. As imagens desta prova foram `sha256:08fc96aefa056da9a3ab0d0869bd91cef7751caa4c8249d139d1bfcefb3836ed` e `sha256:ee23bc56b175a8c775240f5063e7d4baa242fd26addf1d7ba8b6da5c3f9268b8`. Não houve novo scan dentro desse cenário, e o scan histórico de outra imagem não passa a aprová-las.

O resultado informa a pasta imutável da tentativa. Dumps e metadados privados ficam no runtime reservado; publique apenas os resumos sanitizados. A cópia foi independente do volume de origem, mas permaneceu no mesmo host. O [roteiro para outro computador](runbooks.md#recuperação-em-outro-computador--roteiro-ainda-não-executado) continua pendente de execução. O ensaio também não mede uso por uma equipe real nem garante tempo de recuperação para uma carteira maior.

O manifesto vincula a execução aos JSONs, inclusive registros individuais que não contêm `run_id`.
