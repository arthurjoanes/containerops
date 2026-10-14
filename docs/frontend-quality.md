# Manutenção do relatório

O relatório usa HTML, CSS e JavaScript locais, gerados em Python. Ao mudar a
apresentação, preserve a seleção dos registros e os significados descritos no
[guia de leitura](report-guide.md). Fonte, licença e assets locais estão no [guia de identidade](brand.md).

## Estrutura e estados

Mantenha as nove âncoras de operação e os links para os JSONs. Cada resultado
pertence à data, ao projeto e à imagem registrados; a proximidade entre arquivos
não cria vínculo. Integridade da cópia e recibo de restore são informações
separadas. Ausência, zero, falha, dado inválido e resultado incompleto devem
continuar distinguíveis por texto.

Use elementos nativos para navegação, filtros e detalhes. Copiar um comando
transfere somente texto; a execução ocorre no terminal. Conserve a seleção manual
quando o clipboard estiver indisponível. Busca e filtro de recibo mantêm os
parâmetros `copy` e `receipt` na URL.

## Teclado e leitura

Preserve o link de salto, o foco visível e a ordem de navegação por Tab e
Shift+Tab. A troca de operação leva o foco ao título correspondente; voltar e
avançar recuperam a seleção e os filtros. Rótulos devem identificar busca,
recibo, comandos e regiões com rolagem.

Textos e IDs extensos precisam caber em telas estreitas. Comandos conservam a
formatação e usam rolagem local acessível pelo teclado. Acompanhe verde, âmbar e
vermelho com rótulos; respeite `prefers-reduced-motion`. A [WCAG 2.2](https://www.w3.org/TR/WCAG22/)
e os [padrões ARIA](https://www.w3.org/WAI/ARIA/apg/patterns/) são referências para
esses requisitos.

## Saída offline e impressão

Sem JavaScript, todas as operações e os detalhes nativos ficam disponíveis;
controles dependentes de script permanecem ocultos. A impressão inclui as
operações e os detalhes, com texto e símbolo legíveis em fundo branco.

Fonte, estilos, marca e navegação são incorporados ao HTML. Mantenha a
[licença SIL OFL da Source Sans 3](../scripts/assets/source-sans-LICENSE.md)
junto da fonte e no HTML distribuído. Links locais devem continuar relativos,
sem caminhos privados de dumps ou do computador.

Para alterar o gerador, use as fontes em `scripts/report.py`,
`scripts/report_evidence.py`, `scripts/report_recovery.py`, `scripts/report.html`,
`scripts/report.css` e `scripts/report.js`. Confira teclado, texto ampliado,
impressão, leitura sem JavaScript e estados incompletos ao modificar esses arquivos.
