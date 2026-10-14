# Ler as operações registradas

O relatório é um snapshot: datas e resultados vêm dos JSONs de cada operação.
Abrir o HTML não consulta containers nem executa comandos. O [exemplo do catálogo](examples/report.html#backups)
contém registros operacionais de 12/10/2026; GitHub apresenta o HTML como código.
Depois de clonar ou baixar o repositório, abra o arquivo localmente no navegador.

O exemplo foi regenerado em 13/10/2026 com esses mesmos registros, sem executar
novamente as operações. Nele, os limites de CPU preservam a precisão dos
`NanoCpus`: proxy 0,25 e banco 0,75 CPU. Os HTMLs históricos de 22/09 e 12/10 foram
preservados com seus hashes; suas tabelas antigas arredondam esses valores para
0,2 e 0,8. Para conferir o limite configurado daquela execução, use o JSON de
hardening vinculado, não esse arredondamento histórico. Regenerar a apresentação
também não torna atuais a verificação nem o scan registrados.

A implementação separa [seleção das evidências](../scripts/report_evidence.py),
[resultado do restore](../scripts/report_recovery.py), [gerador](../scripts/report.py),
[template](../scripts/report.html) e [navegação](../scripts/report.js).

## Navegar

| Grupo | O que conferir |
| --- | --- |
| Verificação | Última execução validada pelo manifesto, isolamento, recuperação do worker, resultado do job e TLS |
| Recuperação | Catálogo das cópias e último ensaio de restore, incluindo limpeza |
| Release | Tentativa mais recente de troca de imagem e retorno à imagem anterior |
| Artefatos | Build, auditoria OCI, scan compatível e arquivos de evidência |

Selecione uma operação no índice lateral. No celular, abra o seletor
**Operação: nome da operação**. Links com fragmento, como `report.html#recovery`,
abrem a operação correspondente e podem ser guardados. Voltar e avançar percorrem
as seleções. Após selecionar uma operação pelo teclado, o foco segue para seu
título. **Ir para a operação** pula a navegação e mantém a seleção.

As nove vistas incluem **Cópias de backup** (`#backups`) e **Último ensaio de
restore** (`#recovery`). Um recibo do último ensaio pertence somente à cópia
identificada no registro.

## Localizar e conferir uma cópia

Busque pelo ID, projeto ou contexto e filtre pelo recibo de restore. As cópias
mais recentes aparecem primeiro. Os filtros usam `copy` e `receipt` na URL e
retornam ao recarregar a página. **Limpar filtros** restaura a listagem. Uma busca
sem resultados, um catálogo vazio e um catálogo inválido têm mensagens próprias.

Cada linha mostra ID completo, data, origem, integridade e recibo. Os detalhes
contêm schema, tamanho, importação, proteção e motivos de retenção. O catálogo é
o estado registrado pela CLI, sem atualização automática ao abrir o HTML.

Integridade confere os bytes da cópia. Restore verificado exige um ensaio
com snapshot igual, novo job concluído e limpeza aprovada. **Sem recibo no catálogo**
informa que não há recibo associado àquela cópia; isso não prova que ela nunca
foi ensaiada. Confira o ID, a origem e os hashes no JSON antes de relacionar registros.

**Copiar comando** copia somente texto. Quando o clipboard está indisponível, a
página seleciona o comando e orienta a cópia manual. Execute no terminal, no
contexto indicado, depois de conferir o catálogo atual. Uma cópia importada exige
verificar a origem antes de acrescentar `--trusted-source`. Integridade
desconhecida ou contexto inválido impede preparar o comando de restore por ID.

Cada operação oferece blocos identificados como **PowerShell** e **Shell POSIX**,
com botões de cópia independentes. Escolha o shell do terminal em que vai executar.
No POSIX, o comando configura a sessão apenas naquela execução; o contexto padrão
usa um subshell com `unset`, sem alterar o ambiente do terminal que o chamou.

Os comandos disponíveis permitem listar cópias, selecionar um ID para restore
e gerar uma prévia de retenção. Remoção exige `--apply` e o ID do plano.
Transporte e carregamento de imagem têm comandos separados nos
[runbooks](runbooks.md#pacote-portátil-de-backup).

## Interpretar o resultado

**Aprovado** descreve as verificações registradas daquela operação. **Parcial**,
**ausente**, **inválido**, **em andamento** e **falhou** conservam significados
distintos. Uma tentativa recente que falhou não herda o resultado de uma anterior.
JSONs inválidos aparecem no aviso da página, com acesso ao arquivo.

No job, **Na fila** e **Em andamento** conservam o estado registrado. Um job
concluído sem resultado completo aparece como **Parcial**; um estado desconhecido
aparece como **Inválido**. Esses casos são diferentes de falha de processamento.

O último ensaio só aparece como **Restauração verificada** quando registra
`status=passed`, checksum e snapshot aprovados, novo job completo e
`cleanup_succeeded=true`. Falha da tentativa ou da limpeza impede aprovação,
mesmo com dados e job aprovados. Registros antigos sem status final ou limpeza
ficam parciais, com o motivo e as verificações disponíveis.

Cada operação mantém sua data, projeto e execução. O JSON de restauração
identifica qual backup recebeu. O job tem sua versão; a release, suas imagens;
o scan aplica-se à imagem selecionada. Build, auditoria e scan precisam ter
identidade e tempo compatíveis. O snapshot do runtime fica separado em Artefatos.

As durações resumidas usam uma casa decimal; os detalhes e JSONs conservam
valores completos e hashes. São resultados de demonstrações locais, sem indicar
disponibilidade atual ou prazo garantido de recuperação. A
[sequência operacional histórica](operational-recovery.md) e o
[registro de verificação](verification.md) identificam as execuções preservadas.
Os percentis da [medição de admissão](admission-measurement.md) têm documento próprio.

## Gerar um relatório

```sh
python scripts/ops.py report
```

O comando relê os arquivos existentes. Para produzir novos resultados, siga os
[runbooks](runbooks.md): `demo` envia um job; `verify` testa a aplicação;
`prove` inclui imagens, restauração, TLS e release em projetos descartáveis.

Sem sessão, a saída local é `docs/report.html`. Com `CONTAINEROPS_SESSION`, o
comando lê somente `sessions/<slug>/docs/evidence` no runtime reservado e escreve
`sessions/<slug>/docs/report.html`. O caminho aparece no terminal.

O HTML incorpora fonte, estilos, marca e JavaScript. Na sessão, guias Markdown
selecionados acompanham a saída em `documentation/<hash>/`, com links relativos.
Links entre guias copiados continuam relativos. Código, imagens e outros arquivos
públicos não incluídos apontam para URLs do commit registrado e exigem rede.
Quando há alterações locais, o pacote avisa que esses links mostram a versão do
commit e não constituem permalinks das alterações. Referências a arquivos não
versionados ou fora do repositório interrompem a geração, sem copiá-los. Os dumps e
caminhos privados do computador ficam fora dos links públicos.

Sem JavaScript, todas as operações e detalhes nativos permanecem disponíveis;
filtros e controles de copiar texto ficam ocultos. Para manutenção, consulte
as [regras do relatório](frontend-quality.md) e a [paleta](brand.md).
