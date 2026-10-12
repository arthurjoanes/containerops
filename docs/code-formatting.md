# Código no relatório

O [gerador](../scripts/report.py), o [template](../scripts/report.html) e o [CSS](../scripts/report.css) definem os blocos de comando do relatório.

Os comandos de geração do relatório e de scan aparecem em blocos Shell compactos.
Comando, caminho, opção e valor a substituir recebem cores distintas. O texto
continua selecionável e preserva exatamente o comando original.

O marcador `<versão>`
continua indicando o valor que deve ser substituído antes da execução no terminal.
Abrir o relatório não executa comandos.

O destaque faz parte do [HTML gerado](../scripts/report.html) e do
[renderer](../scripts/report.py), com [cores locais](../scripts/report.css).
Não exige JavaScript, conexão externa ou biblioteca adicional. Identificadores,
digests e valores de estado continuam como texto monoespaçado. Linhas extensas
rolam somente dentro do bloco, que aceita foco por teclado.

Os testes do renderer verificam o texto e o escape dos comandos:

```sh
python -m unittest discover -s scripts -p test_report.py -q
```
