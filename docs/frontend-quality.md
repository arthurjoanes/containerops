# Qualidade da interface do ContainerOps

Revisão de 22/09/2026. Este documento trata do relatório local de operações. A API, o worker, o banco, as imagens e a execução dos runbooks conservam seus contratos. O HTML é um snapshot: abrir a página não consulta serviços nem dispara comandos.

## Tarefa e inventário

Quem consulta é uma pessoa avaliando uma execução ou investigando suas evidências. A pergunta principal é **o que foi verificado nesta operação e qual arquivo comprova o resultado?** O relatório não permite operar a infraestrutura.

Todas as oito vistas foram examinadas no navegador, inclusive detalhes, navegação e estados aplicáveis:

| Vista / fragmento | Informação e decisão | Origem e contrato preservado |
|---|---|---|
| Verificação / `overview` | Conferir resultado, idade da prova, etapas e limites dos containers; abrir a evidência de uma etapa | Manifesto `verification-run`, arquivos vinculados por hash e registros de hardening/jornada/recovery |
| Job / `result` | Distinguir fila, processamento, conclusão, falha e resultado incompleto; conferir palavras, tentativas e checksum | Job selecionado pelo gerador; sua versão não identifica outra imagem |
| TLS / `tls` | Conferir teste com CA local, registro e escopo da conexão | Registro TLS; não é monitoramento de disponibilidade |
| Restauração / `recovery` | Conferir checksum, igualdade do volume e novo job; identificar separadamente a cópia utilizada | `restore` e `backup` mantêm datas/projetos próprios; não são associados por proximidade |
| Troca / `operations` | Conferir a tentativa mais recente, incluindo falha, versão e imagens | Seleção cronológica existente; tentativa inválida não herda sucesso antigo |
| Retorno / `rollback` | Conferir retorno após falha controlada e o job resultante | Rollback de imagem; não implica downgrade do schema ou restore do banco |
| Artefatos / `artifacts` | Conferir compatibilidade entre build, auditoria e scan; consultar achados, cache e snapshot separado | Identidade e tempo existentes; scan de outra imagem não aprova a selecionada |
| Arquivos / `evidence` | Encontrar o JSON pelo nome/data/projeto e abri-lo | Catálogo dos registros válidos; presença no catálogo não aprova uma operação |

Filtros, paginação, formulários, ordenação interativa, autenticação, permissões de tela e exportações de dados: **Não aplicável** ao relatório estático. Impressão, links locais, detalhes nativos e histórico do navegador são aplicáveis e foram exercitados. Não foram acrescentados controles de deploy, atualização ao vivo ou consulta fictícia.

## Linha de base e direção

Base de apresentação: `32370e05ffc9f50cd67456df98505585b2a61f06`. O índice horizontal consumia quase a primeira metade da tela; em 1440 px, os oito títulos de operação começavam aproximadamente em y=394 px. Identidade, resultado, explicações e prova tinham delimitação insuficiente. O novo conteúdo começa em aproximadamente y=96 px, com navegação lateral e grupos delimitados por operação.

As duas versões foram renderizadas com o **mesmo instante de geração** e os **mesmos 225 arquivos JSON originais**. As datas das fontes não foram atualizadas. Os 69 campos de dados comuns do template permaneceram equivalentes; a comparação ignora somente a classe `numeric` adicionada às células. Três aliases redundantes de data/projeto/execução foram removidos do template, permanecendo no bloco de identidade. [Hashes e comparação](evidence/interface-v3/comparison.json).

Há uma correção de integridade separada da composição: quatro JSONs históricos tinham CRLF no checkout original, mas LF nos blobs Git, divergindo do manifesto. Os bytes originais foram restaurados **após conferência de SHA-256**, com exceções `-text` para esses quatro caminhos. Manifesto, validação e conteúdo JSON não mudaram. A linha de base visual recebeu os mesmos quatro arquivos para comparar interfaces com dados equivalentes. Portanto ela não representa um checkout Git totalmente sem ajustes. [Diagnóstico e hashes](evidence/interface-v3/integrity-eol.json).

Referências consultadas:

- [Allure — relatório funcional de testes](https://allure-framework.github.io/allure3-demo/awesomeAll/): navegação persistente, resultado próximo à sequência de verificações e aprofundamento em evidências. A interface não copia identidade, código ou recursos visuais do produto.
- [Carbon — dashboards](https://carbondesignsystem.com/data-visualization/dashboards/): prioridade pelo contexto, limitação de métricas e agrupamento das informações relacionadas.
- [Nielsen Norman Group — progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/): identidades e hashes completos sob demanda, mantendo falhas e ausência de prova aparentes.
- [Vercel — Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md): navegação nativa, foco, conteúdo extenso, redução de movimento e revisão dos controles. Fonte atual consultada em 22/09/2026.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/): referência para contraste, teclado, foco, alvo e reorganização. Esta revisão não é uma certificação WCAG.

As skills `frontend-design` e `web-design-guidelines` orientaram composição e revisão. `vercel-react-best-practices` é **Não aplicável**: este frontend é gerado em Python com HTML/CSS/JavaScript, sem React ou Next.js. Nenhuma dependência de produção foi adicionada.

## Padrões e semântica

- Navegação navy, superfície branca e fundo frio. Azul indica navegação; verde, âmbar e vermelho indicam estados acompanhados de texto. As cores não substituem os rótulos.
- Grade lateral de 232 px; em larguras intermediárias, 212 px. Conteúdo flexível, com limite de largura. A coluna da cópia desce antes de ficar comprimida; até 760 px a navegação vira seletor nativo.
- Uma superfície delimita cada conjunto de evidências. As etapas são linhas, não uma coleção de cards. Backup e restauração têm superfícies distintas porque são registros distintos.
- Corpo em Segoe UI, títulos em Bahnschrift/Segoe UI, identificadores em monoespaçada. Números comparáveis usam algarismos tabulares; colunas numéricas se alinham à direita. Valores ausentes têm tipografia de texto, sem aparência de indicador principal.
- A quantidade de jobs e a duração estão próximas às três verificações. Identidades e o novo job abrem em detalhes. Falha da restauração, JSON inválido, scan ausente e achados permanecem visíveis.
- Zero, ausência, falha, estado desconhecido e resultado incompleto mantêm significados diferentes. Datas de operação ficam nos links de prova; geração do HTML fica explicitamente no rodapé.
- Durações mantêm unidade e precisão preexistentes; valores exatos e SHA-256 continuam acessíveis. Não há gráficos: a tarefa é conferir estados e valores exatos, sem série comparável que justifique inventar visualizações.
- Não há animação contínua, fontes remotas, frameworks novos ou requisições externas no relatório. Foco visível e estados de interação são imediatos; `prefers-reduced-motion` é respeitado.

## Problemas, impacto e tratamento

| Tela/componente | Evidência anterior e impacto | Prioridade | Correção | Validação |
|---|---|---|---|---|
| Todas / índice e cabeçalho | Título da operação em y≈394 px no desktop; índice e metadados antecediam o resultado | P1 | Navegação lateral, título e resultado no início do conteúdo; seletor móvel informa a operação atual | 32 capturas comparáveis e medições de geometria |
| Recuperação | Prova, metadados da cópia e resultado do novo job competiam sem agrupamento claro | P1 | Resultado e três verificações juntos; cópia separada; identidade e novo job expansíveis | Desktop/celular, expansão por teclado e cenário de restore com falha |
| Verificação | Identificação repetida e resultado afastado das etapas | P2 | Resumo e etapas na mesma superfície; identificação completa nos detalhes | 69 bindings equivalentes e 47 testes do gerador |
| Job, TLS, troca e retorno | Conteúdo técnico disperso e pouca distinção entre resultado e registro | P2 | Resultado agrupado com prova; contexto técnico acessível em detalhes | Oito vistas, histórico, link de salto, impressão e leitura sem JS |
| Artefatos | Metadados de build, snapshot e leitura de scan disputavam a mesma hierarquia | P1 | Estado/achados primeiro; identidade, arquivos, cache e snapshot explicitamente separados | Ausência real de scan compatível preservada; cenários inválidos não aprovados |
| Tabelas e links | Colunas numéricas alinhadas como texto; link curto com alvo estreito | P2 | Células numéricas à direita e links com área mínima de 24 px | Contraste de 17 pares e medição de controles visíveis |
| Valores ausentes | Texto “Não informado” recebia tamanho de métrica em duas colunas estreitas | P2 | Estilo semântico de ausência, sem converter para zero | Backup isolado, vazio e valores ausentes em 320/390/1440 px |
| Evidência histórica | Checkout normalizado divergindo de quatro hashes do manifesto | P1, preexistente | Restauração dos bytes originais e atributos exatos separados da mudança visual | SHA-256 estrito; JSON e bytes normalizados equivalentes |

## Matriz antes → depois

Legenda: **C** = Conforme no escopo descrito; **PC** = Parcialmente conforme; **NC** = Não conforme; **NV** = Não verificado; **NA** = Não aplicável. Não há média nem nota global. Os resultados descrevem os critérios verificados, não uma aprovação de usuários reais.

| Dimensão | Verificação | Job | TLS | Restauração | Troca | Retorno | Artefatos | Arquivos |
|---|---|---|---|---|---|---|---|---|
| 1. Objetivo e público | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | C→C |
| 2. Hierarquia e organização | NC→C | PC→C | PC→C | NC→C | PC→C | PC→C | NC→C | PC→C |
| 3. Layout e densidade | PC→C | PC→C | PC→C | NC→C | PC→C | PC→C | PC→C | PC→C |
| 4. Tipografia e cor | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C |
| 5. Indicadores e tabelas | PC→C | C→C | NA→NA | PC→C | C→C | C→C | PC→C | C→C |
| 6. Navegação e ações | C→C | C→C | C→C | C→C | C→C | C→C | C→C | C→C |
| 7. Estados e atualização | C→C | C→C | C→C | PC→C | C→C | C→C | C→C | C→C |
| 8. Acessibilidade/responsividade | PC→PC | PC→PC | PC→PC | PC→PC | PC→PC | PC→PC | PC→PC | PC→PC |
| 9. Desempenho | NV→PC | NV→PC | NV→PC | NV→PC | NV→PC | NV→PC | NV→PC | NV→PC |
| 10. Manutenção/reutilização | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C | PC→C |
| 11. Dados/regras/permissões | PC→C | C→C | C→C | C→C | C→C | C→C | C→C | C→C |

TLS não tem métrica quantitativa ou tabela; sua verificação textual está nas dimensões 1, 2 e 7. A dimensão 8 permanece parcial: teclado, foco, controles, 320 px, contraste selecionado, zoom CSS e tabela rolável foram verificados, mas leitor de tela e ampliação nativa em diferentes navegadores não foram. A dimensão 9 cobre tamanho e ausência de novas dependências/rede: 54.002 → 57.002 bytes de HTML. Não houve medição de experiência em dispositivos lentos. Isolamento/autorização da API não foi reavaliado por esta mudança de apresentação.

O ajuste de integridade prévio explica o PC da dimensão 11 em Verificação. As métricas comerciais/operacionais, fórmulas, seleção de registros e validadores não foram alterados para obter aprovação.

## Evidências e comandos

- [Revisão no navegador](evidence/interface-v3/visual-review.json): 40 vistas, 39 cenários em 1440/390/320 px, zero erros JavaScript; impressão, zoom CSS 200%, movimento reduzido e ausência de JS.
- [Geometria, contraste e alvos](evidence/interface-v3/layout-accessibility.json): 32 vistas antes/depois, 17 pares de contraste e controles sem alvo pequeno fora das exceções de links em texto.
- [Axe-core](evidence/interface-v3/axe.json): 16 vistas visíveis (oito operações em desktop/celular), sem violações automáticas reportadas; resultados incompletos permanecem identificados e não equivalem a aprovação manual.
- [Comparação dos dados](evidence/interface-v3/comparison.json): hashes das entradas e equivalência dos bindings.
- [Antes da restauração](screenshots/interface-v3/before-recovery-1440.png) / [depois](screenshots/interface-v3/after-recovery-1440.png); [antes no celular](screenshots/interface-v3/before-recovery-390.png) / [depois](screenshots/interface-v3/after-recovery-390.png). Há pares com os mesmos nomes para todas as oito vistas em `docs/screenshots/interface-v3`.

Comandos executados nesta revisão:

```sh
python -m unittest discover -s scripts -p test_report.py
ruff check scripts/report.py scripts/render_review.py
node --check scripts/report.js
node --check scripts/visual-review.cjs
python scripts/render_review.py
node scripts/visual-review.cjs
node scripts/test_report_browser.cjs
```

Resultado: 47 testes Python passaram; Ruff e sintaxe JS passaram; seis casos do link de salto, seleção por teclado, histórico, impressão e leitura sem JS passaram. Para o navegador, `PLAYWRIGHT_MODULE` apontou para a instalação local de desenvolvimento e `PLAYWRIGHT_CHANNEL=msedge`. Playwright não faz parte do HTML. O gerador `report.generate` produziu ambas as versões com instante fixo compartilhado; não existe etapa de bundle ou typecheck TypeScript neste frontend.

Antes de integrar, também passaram 56 testes unitários de contratos de operações, prova e medição (`test_operations_proof`, `test_operations`, `test_proof`, `test_contracts`, `test_measure_admission`) e `ruff check scripts tests`. Esses testes usam fixtures temporárias e não representam outra execução de backup/restauração em Docker. [Comandos e fontes conferidas](evidence/interface-v3/checks.json).

As fixtures de apresentação ficam em `artifacts/interface-review`, identificadas como dados de teste. Incluem vazio, backup sem restore, release inválida/falha, job na fila/processando/concluído/falho/desconhecido/incompleto, zero, número extenso, identificador longo e restore com falha. Contagem negativa de palavras não é um caso válido do domínio; nenhum número foi transformado em negativo para produzir uma imagem.

Não foi repetida a prova de infraestrutura Docker/backup/release/TLS/scan nesta revisão do HTML. As evidências operacionais anteriores permanecem em seus diretórios e têm escopo próprio. CI/publicação desta rodada devem ser consultados pelo commit; não se presume sucesso futuro.

## Limitações e próximos checks

| Prioridade | Verificação pendente | Efeito na conclusão |
|---|---|---|
| P2 | Leitor de tela, zoom nativo e navegadores adicionais | Acessibilidade permanece parcialmente verificada; não há certificação AA |
| P2 | Sessão com operadores/avaliadores reais | As decisões têm referência técnica e revisão, mas não representam aprovação ou ganho de produtividade medido |
| P3 | Desempenho em hardware lento e impressão PDF página a página | O relatório local foi exercitado; essas condições específicas não foram medidas |

Não foram encontrados P0 ou P1 pendentes na apresentação examinada. O estado **Parcial** de Artefatos permanece intencional porque os registros selecionados não contêm scan/auditoria compatíveis; uma melhoria visual não pode convertê-lo em aprovado.
