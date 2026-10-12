# Identidade visual do ContainerOps

ContainerOps é um laboratório de operação local. Sua interface ajuda o operador
a localizar uma cópia e o avaliador técnico a conferir o que foi ensaiado. A
marca usa o símbolo de camadas já presente nos assets e uma paleta fria, com
estados operacionais separados das cores de navegação.

## Paleta

Os tokens estão em [report.css](../scripts/report.css). As cores têm papéis
definidos para manter a mesma identidade no catálogo, nos recibos e na navegação.

| Cor | Hex | Token | Uso |
| --- | --- | --- | --- |
| Azul profundo | `#142D4E` | `--brand-deep` | Cabeçalho e estrutura da marca |
| Azul de ação | `#1F5FBF` | `--brand-action` | Links, seleção e ações locais como copiar texto |
| Ciano | `#0F798A` | `--brand-accent` | Acento de identidade e contexto |
| Nuvem | `#F0F4F8` | `--brand-cloud` | Fundo da página |
| Papel | `#FFFFFF` | `--brand-paper` | Superfícies de leitura e registros |
| Tinta | `#24374B` | `--brand-ink` | Texto principal |
| Texto secundário | `#526478` | `--muted` | Datas, descrições e metadados |

| Estado | Hex | Significado no relatório |
| --- | --- | --- |
| Verde | `#16604B` | Critério registrado como aprovado |
| Âmbar | `#7A4E0B` | Resultado parcial, pendência ou ausência de registro |
| Vermelho | `#AC2E3B` | Falha ou dado inválido |

Rótulos acompanham as cores. Integridade aprovada, recibo de restore e proteção
são informações distintas; um verde de checksum não aprova a recuperação inteira.
A marca e seus acentos não representam aprovação operacional.

## Tipografia e assets

Source Sans 3 permanece como fonte principal, com Segoe UI/sans-serif como
fallback. A fonte variável e a [licença SIL OFL](../scripts/assets/source-sans-LICENSE.md)
são incorporadas ao HTML para leitura offline. Comandos e identificadores usam
a família monoespaçada já configurada; datas e valores comparáveis usam
algarismos tabulares. Títulos, texto de leitura e metadados têm escalas próprias,
sem transformar quantidade de palavras ou jobs em indicadores de capacidade.

O [símbolo claro](../scripts/assets/mark-light.svg) e o
[favicon](../scripts/assets/favicon.svg) continuam locais. O catálogo conserva
IDs completos e detalhes nativos; fontes técnicas permanecem próximas da
informação que sustentam. No celular, a navegação recolhe as operações e as
linhas de cópia acomodam identidade e estados sem abreviar o ID essencial.

## Aplicação e limites

As nove vistas compartilham os mesmos tokens. Catálogo e último ensaio têm
destinos próprios; a hierarquia começa pela tarefa e deixa hashes, comandos e
papéis técnicos nos detalhes correspondentes. O HTML consulta registros e copia
texto para o terminal; a página não opera Docker nem executa backup ou restore.

A [revisão de qualidade](frontend-quality.md) registra capturas, critérios de
contraste, teclado, reflow e limitações do escopo verificado. A paleta documenta
a identidade adotada; não é uma certificação de acessibilidade nem uma medição
de satisfação ou produtividade.
