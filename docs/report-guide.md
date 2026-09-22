# Ler as operações registradas

Abra `docs/report.html` no navegador depois de clonar ou baixar o repositório.
O HTML inclui estilos e navegação; os arquivos de evidência precisam permanecer
na pasta `docs/evidence`. A página não consulta containers nem executa operações.

A [sequência operacional de 22/09](operational-recovery.md) tem um [relatório isolado](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/view/docs/report.html) com somente os quatro JSONs daquela tentativa. Use-o para relacionar job inicial, rollback e restore. O relatório principal reúne registros de momentos diferentes; cada painel conserva a identidade da sua operação. Os percentis da [medição de admissão](admission-measurement.md) ficam no documento próprio.

| Grupo | O que conferir |
|---|---|
| Verificação | Última execução validada pelo manifesto, etapas de isolamento e recuperação, resultado do job e teste TLS |
| Recuperação | Checksum do backup, igualdade dos dados restaurados e processamento de um novo job |
| Release | Tentativa mais recente de troca de imagem, inclusive falha, e teste de retorno à imagem anterior |
| Artefatos | Identidade do build, auditoria OCI, scan compatível e catálogo dos JSONs |

Selecione uma operação no índice lateral organizado em quatro grupos. No celular, abra o seletor **Operação: nome da operação**. Os links
com fragmento, como `report.html#recovery`, abrem o detalhe correspondente e podem
ser guardados. Voltar/avançar no navegador percorre as seleções. O teclado alcança
a lista, os arquivos e os detalhes; após selecionar, o foco segue para o título
do resultado. Sem JavaScript, todas as operações ficam visíveis na mesma página.
O atalho **Ir para a operação** pula a navegação e mantém o resultado selecionado.

## Interpretar o resultado

**Aprovado** descreve as verificações registradas daquela operação. **Parcial**,
**ausente**, **inválido**, **em andamento** e **falhou** não equivalem a aprovação.
Um arquivo antigo aprovado não substitui uma tentativa recente que falhou. JSONs
inválidos aparecem no aviso acima dos detalhes, com acesso ao arquivo.

No resultado do job, **Na fila** e **Em andamento** conservam o estado registrado.
Um job marcado como concluído, mas sem resultado completo, aparece como **Parcial**;
um estado desconhecido aparece como **Inválido**. Esses casos não são rotulados
como falha de processamento.

Cada operação mantém sua data, projeto e identificação de execução, quando
presentes. Backup e restauração não são unidos apenas porque aparecem juntos;
o JSON de restauração registra qual backup recebeu. Da mesma forma, o job tem sua
versão, a release tem suas imagens e o scan se aplica à imagem selecionada.
A seleção do build exige compatibilidade de identidade e tempo para auditoria e
scan. O snapshot do runtime fica separado, dentro de Artefatos.

As durações resumidas usam uma casa decimal. A verificação conserva a duração
exata nos detalhes. Os JSONs mantêm os valores completos, hashes e campos que
não aparecem na página. Esses dados são demonstrações locais, não disponibilidade
atual, SLA ou certificação das imagens.

## Atualizar

```sh
python scripts/ops.py report
```

O comando relê os arquivos existentes. Para produzir novos resultados, siga os
[runbooks](runbooks.md). `demo` envia um job; `verify` testa a aplicação;
`prove` inclui imagens, restauração, TLS e release em projetos descartáveis.
O [registro de verificação](verification.md) distingue as execuções históricas
das verificações de apresentação.

## Revisão anterior de jornadas em 22/09

1. Em **Resultado do job**, diferencie o estado concluído da versão que o processou. Essa versão não identifica automaticamente a imagem do painel Artefatos.
2. Abra **Backup e restauração** e expanda o resultado do job e seus detalhes (naquela composição, **Job processado após a restauração → Detalhes do job**). O resultado salvo registra oito jobs restaurados e quatro palavras no novo trabalho; o checksum completo pode ser conferido com o JSON. O projeto do backup e o da restauração permanecem separados.
3. Em **Última troca de imagem** e **Retorno após falha**, confira candidata, imagem anterior e os arquivos da operação antes de associar o resultado a uma release.
4. Em **Imagem, auditoria e scan**, compare o ID do build e da auditoria e abra **Arquivos de build, auditoria e scan**. O catálogo contém outros registros; estar listado não significa aprovar a imagem selecionada.

A [revisão de jornadas](evidence/interface-journeys/review.json) percorreu os oito painéis por teclado em 1440, 390 e 320 px, com seleção/foco, link de salto, histórico, arquivos locais, rolagem interna da tabela, impressão e leitura sem JavaScript. O HTML foi renderizado novamente em pasta isolada com os mesmos JSONs; as datas das operações não foram atualizadas. Nenhum serviço foi iniciado.

Capturas: [restauração no celular com o job expandido](screenshots/journey-restore-390.png), [troca de imagem](screenshots/journey-release.png) e [identidade e arquivos do build](screenshots/journey-artifacts.png).

## Composição anterior — versão 2

O índice horizontal substitui a coluna lateral permanente. No desktop, etapas e resultado ficam na coluna principal; datas, projeto e identidade de imagem ficam ao lado da operação correspondente. No celular, o seletor nativo recolhe a lista e os metadados seguem o resultado. A página continua sendo um snapshot, sem botões de deploy, restart ou terminal.

Em recuperação, **jobs restaurados** vem antes da duração. A sequência é **cópia → dados → novo job**: verificar o checksum, comparar o volume restaurado e conferir o trabalho processado depois da restauração. O novo resultado começa aberto; hash e ID do job ficam em seus detalhes. Um registro de backup isolado deixa as verificações de restauração **sem resultado**. Data e projeto do backup continuam próprios.

[Desktop](screenshots/interface-v2/recovery-1440.png), [celular](screenshots/interface-v2/recovery-390.png), [tentativa de release](screenshots/interface-v2/operations-1440.png) e [backup sem prova de restauração — cenário de apresentação](screenshots/interface-v2/fixture-backup-only-390.png). [Verificação desta composição](evidence/interface-v2/visual-review.json).

Os corpos de texto, linhas divisórias e títulos compartilham eixos e espaçamentos; hover e foco usam 180 ms, com transições removidas quando o sistema pede menos movimento. Sem JavaScript, todas as operações aparecem e os detalhes nativos funcionam. A impressão expõe também o conteúdo dos detalhes fechados.

## Limpeza anterior de regra sem consumidores

A variável CSS `--surface`, sem uso, foi removida da fonte e do HTML publicado. O restante do HTML, incluindo datas, identidades e JavaScript, foi preservado. O [registro da limpeza](evidence/interface-v2/cleanup.json) descreve o delta e as verificações estáticas; as provas anteriores não foram reexecutadas nem substituídas.

## Composição atual — versão 3

A navegação lateral ocupa a altura do documento e mantém as oito operações identificáveis. O título e o resultado da operação iniciam o conteúdo. No celular, o seletor informa a operação atual e recolhe a lista.

Cada conjunto de verificações tem uma superfície delimitada. Na restauração, quantidade de jobs, duração e as três conferências ficam juntos; o registro da cópia ocupa uma superfície própria. Identidades, hashes e o novo job abrem em detalhes nativos. Resultados de falha, registros inválidos e ausência de scan continuam visíveis sem expansão. A versão 3 não acrescenta animações decorativas.

[Restauração desktop](screenshots/interface-v3/after-recovery-1440.png) · [celular](screenshots/interface-v3/after-recovery-390.png) · [matriz antes/depois, decisões e validação](frontend-quality.md).
