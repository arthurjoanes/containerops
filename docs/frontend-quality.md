# Qualidade da interface do ContainerOps

As [capturas de apresentação atuais](screenshots.md) foram refeitas em 22/09/2026 a partir do código atual. As comparações e provas abaixo são registros históricos das respectivas rodadas; seus arquivos e hashes não foram regravados.

## Direção visual atual — revisão de 22/09/2026

Esta seção descreve somente a rodada baseada em `243b1a44769b324faf28f1795f510a49cda5fbdf`; as seções seguintes são o registro da rodada anterior. O relatório continua estático, sem executar comandos ao abrir. Contratos, seleção de registros, auth, worker e operações não foram alterados.

### Diagnóstico, alternativas e resultado

A navegação repetia “Aprovado”; job pequeno ocupava um painel largo com palavras em tamanho de KPI; Artefatos repetia ausência em vários blocos. Backup e restauração eram separados, mas a hierarquia entre operação, limite e origem precisava de mais contraste. Foram comparados **A**, índice enxuto e dossiê de operação, e **B**, navegação horizontal por famílias. [A refinada](screenshots/art-direction/proposals/a.png) / [B](screenshots/art-direction/proposals/b.png). A foi escolhida pela estabilidade dos oito destinos. B comprimia rótulos em larguras intermediárias.

```text
Cabeçalho navy: marca / documentos / snapshot
Índice enxuto | Título da operação
              Operação branca delimitada | Cópia em cinza independente
              Resultado / contagem / duração
              Checklist contínuo
              Prova e detalhes compactos
```

O primeiro A foi criticado por excessos de branco e texto solto. O refinamento mantém uma superfície por operação, com faixa única de resultado e etapas contíguas; não usa cards dentro do card. A cópia tem superfície cinza distinta e datas próprias. No celular, estados permanecem alinhados ao respectivo título sem comprimi-lo. Os disclosures não reservam espaço vazio quando fechados.

O job tem conclusão e valores inline, com largura de leitura até 900 px; palavras não representam saúde. Artefatos apresenta uma conclusão de compatibilidade, com ênfase âmbar quando faltam provas. Critérios e identidades continuam disponíveis, sem repetir placeholders no topo. A navegação conserva exceções: “Revisar”, “Falhou”, “Inválido” ou “Ausente”; apenas o “Aprovado” repetitivo foi removido. Pendência de prova não significa runtime indisponível.

Paleta: navy `#1c2b42`, texto `#202b3a`, canvas neutro `#f2f3f5`, operação branca `#ffffff`, origem `#e7ebf0`, ação `#245ac0`; verde/vermelho/âmbar são acompanhados de rótulos. Fonte **Source Sans 3** variável, comparada com Plex/Segoe, verificada no navegador pelo CDP. Os glifos PT-BR e dígitos foram auditados; algarismos têm avanços iguais por padrão. Monoespaçada fica em comandos/identificadores. Corpo principal 16 px, descrições 14 px; metadados menores têm papel secundário. Transições de 180 ms e foco visível respeitam movimento reduzido.

A fonte original é incorporada ao HTML para manter abertura offline e arquivo autocontido. O relatório passou de 57.002 para aproximadamente 292 KB; esse custo é deliberado e medido em [audit.json](evidence/art-direction/audit.json). Fonte, CSS, marca e JS não precisam de rede. [SIL OFL e copyright](../scripts/assets/source-sans-LICENSE.md), incorporados também ao HTML; [origem e hashes](evidence/art-direction/assets.json). O símbolo representa camadas e operações de ida/volta; não marca aprovação de backup ou restauração.

### Proveniência e semântica dos dados

A captura usada na README **durante esta rodada visual** e os pares [antes](screenshots/art-direction/baseline-recovery-1440.png)/[depois](screenshots/art-direction/candidate-recovery-1440.png) usam **JSONs operacionais sintéticos já versionados**, não as fixtures de apresentação. O `restore.json` registra **3 jobs / 27,4 s**, conclusão em **22/09/2026 06:20:00 UTC**; o `backup.json` tem data própria **06:19:27 UTC**. A imagem histórica enviada com **8 jobs / 45,3 s** pertence a outra execução e não foi substituída como prova. Nenhuma operação foi refeita nesta rodada.

Na rodada editorial, a README usou a [captura editorial de restauração](screenshots/editorial-20260922/restore.png), de uma execução posterior: **3 jobs / 27,594 s internos**, registrada às **12:38:21 UTC** de 22/09/2026; o relatório arredonda a duração para 27,6 s. O [JSON da operação](evidence/problem-proof/53365744ff7d4897a142f3bf897dbf39/restore.json), o [recibo de execução](evidence/editorial-20260922/execution.json) e os [hashes da captura](evidence/editorial-20260922/captures.json) identificam essa prova. Ela não substitui os pares de comparação visual nem recebe o horário de 06:20 ou a duração de 27,4 s do registro anterior. A rodada editorial capturou somente três vistas; não repete a auditoria completa das oito operações.

[Inputs e hashes](evidence/art-direction/inputs.json) separam os 37 JSONs lidos diretamente dos arquivos históricos recursivos. A leitura antes/depois compartilha os mesmos arquivos e o mesmo instante de geração; datas das operações permanecem originais. `scripts/render_art_direction.py` carrega o renderer da base Git e gera os dois HTMLs. O horário de geração é atual e compartilhado, não uma nova execução de restore.

Para repetir essa comparação a partir da raiz, instale Playwright em um diretório de ferramentas e seu Chromium (`npm install playwright@1.63.0` e `npx playwright install chromium` nesse diretório). Defina `PLAYWRIGHT_MODULE` com o caminho absoluto do módulo instalado; `PLAYWRIGHT_CHANNEL=msedge` pode selecionar o Edge já instalado. Python 3.11+, Node.js e o commit histórico `243b1a4` no clone são pré-requisitos. Esta auditoria usou Node 24.19.0 e Playwright 1.63.0.

```sh
python scripts/render_art_direction.py
node scripts/audit_art_direction.cjs "<diretório de replay informado pelo comando anterior>"
```

O preparo cria `.runtime/art-direction/replay-<UTC>/` com cópias dos registros e um relógio compartilhado. HTMLs, capturas e `audit.json` novos ficam nesse diretório ignorado; repetir a comparação não substitui as provas publicadas. O auditor exige os dois HTMLs, texto/fontes efetivamente encontrados, 32 pares em quatro larguras e navegação offline. Contraste textual é uma amostra automatizada de cores opacas, não certificação WCAG ou teste com leitor de tela.

| Informação                       | Pergunta e limite                                                                                                |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Jobs restaurados                 | Quantos registros a operação recuperou; não é throughput, meta ou taxa                                           |
| Duração de restore + novo job    | Janela daquela operação, em segundos; não é RTO garantido                                                        |
| Palavras/tentativas/versão       | Resultado exato do job selecionado; versão não identifica a imagem de outro build                                |
| Backup e restore                 | Arquivos com datas/projetos independentes; vínculo só pelo caminho no registro de restore, nunca por proximidade |
| Auditoria e scan                 | Aplicam-se quando identidade e tempo coincidem com o build; zero achados difere de scan ausente                  |
| Ausência/falha/registro inválido | Mantêm textos diferentes; nenhum deles vira zero, aprovado ou indicador de disponibilidade                       |

Não há gráfico: as evidências são operações independentes e valores exatos, sem série comparável. Não há animação de atividade, promessa de dado ao vivo, nova fórmula ou controle de execução.

### Matriz da revisão atual — antes → depois

Legenda: **C** = Conforme no escopo descrito; **PC** = Parcialmente conforme; **NC** = Não conforme; **NV** = Não verificado; **NA** = Não aplicável, com motivo. Sem média ou certificação. Cada célula compara a base **`243b1a44769b324faf28f1795f510a49cda5fbdf` → candidato desta rodada**, com os mesmos JSONs e instante de geração. A matriz histórica abaixo usa outro baseline e não é tomada como o “antes” desta tabela.

A classificação anterior de hierarquia/tipo/layout vem dos pares e do diagnóstico desta rodada. Dados e navegação são comparados no [audit.json](evidence/art-direction/audit.json). Não há auditoria completa de acessibilidade ou medição de desempenho vinculada ao baseline `243b1a4`: esses campos anteriores ficam NV. As [provas atuais](evidence/art-direction/visual-review.json) sustentam somente o alcance indicado após a seta.

| Dimensão                    | Verificação | Job   | TLS   | Restore/cópia | Troca | Retorno | Artefatos | Arquivos |
| --------------------------- | ----------- | ----- | ----- | ------------- | ----- | ------- | --------- | -------- |
| 1. Objetivo e público       | C→C         | C→C   | C→C   | C→C           | C→C   | C→C     | C→C       | C→C      |
| 2. Hierarquia               | PC→C        | PC→C  | C→C   | PC→C          | C→C   | C→C     | PC→C      | C→C      |
| 3. Layout e densidade       | PC→C        | PC→C  | C→C   | PC→C          | C→C   | C→C     | PC→C      | C→C      |
| 4. Tipografia e cor         | PC→C        | PC→C  | PC→C  | PC→C          | PC→C  | PC→C    | PC→C      | PC→C     |
| 5. Indicadores/tabelas      | C→C         | C→C   | NA→NA | C→C           | C→C   | C→C     | PC→C      | C→C      |
| 6. Navegação e ações        | C→C         | C→C   | C→C   | C→C           | C→C   | C→C     | C→C       | C→C      |
| 7. Estados e atualização    | C→C         | C→C   | C→C   | C→C           | C→C   | C→C     | C→C       | C→C      |
| 8. Acessibilidade/reflow    | NV→PC       | NV→PC | NV→PC | NV→PC         | NV→PC | NV→PC   | NV→PC     | NV→PC    |
| 9. Desempenho               | NV→PC       | NV→PC | NV→PC | NV→PC         | NV→PC | NV→PC   | NV→PC     | NV→PC    |
| 10. Manutenção              | C→C         | C→C   | C→C   | C→C           | C→C   | C→C     | C→C       | C→C      |
| 11. Dados/regras/permissões | C→C         | C→C   | C→C   | C→C           | C→C   | C→C     | C→C       | C→C      |

TLS não tem métrica quantitativa/tabela, portanto a dimensão 5 é NA; seus textos, datas e estado são avaliados nas outras dimensões. Gráficos, filtros remotos, formulários, permissão de tela e ações de deploy são NA em todas as vistas: não existem no relatório. Fonte/carregamento offline, hash, detalhes, foco e impressão são aplicáveis. Leitor de tela, zoom nativo, outros motores, hardware lento e paginação integral do PDF continuam NV; não estão escondidos nos “C” visuais. Não se declara conformidade WCAG completa.

C→C não significa funcionalidade nova: a separação de operações, leitura sem comandos, navegação, contratos e estados já existia. A comparação preserva datas, códigos, valores e links de prova, inclusive nos detalhes recolhidos. Os 47 testes do renderer cobrem a seleção e os estados; a verificação editorial posterior das operações não amplia o alcance da auditoria de frontend. Em desempenho, PC cobre tamanho do HTML e ausência de chamadas externas, sem alegar melhora de velocidade.

### Problemas desta rodada, prioridades e validação

| Tela/componente                    | Evidência e impacto anterior                                                             | Prioridade | Correção                                                                     | Validação existente                                                                                 |
| ---------------------------------- | ---------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Navegação de todas as vistas       | “Aprovado” repetido disputava atenção com exceções reais                                 | P2         | Retirar a repetição positiva e manter falha, ausência, inválido e revisão    | Oito destinos nos pares; `visibleApprovalsInNav` em `audit.json` e estados no registro do navegador |
| Job                                | Contagem de palavras tinha escala visual de KPI em painel largo, sem representar saúde   | P2         | Conclusão e valores inline, limite de leitura e detalhes completos           | Pares de Job, valores/códigos preservados e fixtures de fila/falha/incompleto                       |
| Restore/cópia                      | Operação, limite e origem competiam com pouca distinção de superfícies                   | P2         | Resultado e checklist contínuo na operação; cópia em superfície independente | Desktop/celular, checks de dados/prova e cenários de restore falho/cópia isolada                    |
| Artefatos                          | Ausência repetida em blocos afastava a conclusão de compatibilidade                      | P1         | Conclusão única; critérios, registros e identidades por aprofundamento       | Pares de Artefatos; cenários de ausência/incompatibilidade; 47 testes do gerador                    |
| Fonte e estados de todas as vistas | Fonte do sistema e pouca diferenciação tipográfica reduziam consistência entre ambientes | P2         | Source Sans local, papéis tipográficos e estados acompanhados de texto       | Fonte efetiva via CDP, contraste textual computado, abertura offline e movimento reduzido           |

Próximo trabalho seguro para o escopo parcial: **P2**, leitor de tela/zoom nativo/outro motor; **P3**, hardware lento e PDF página a página. Uma sessão com operadores pode avaliar utilidade, mas não foi realizada nem é substituída por screenshots. Os registros históricos de limitações permanecem abaixo.

### Checks e limites

- [Navegador](evidence/art-direction/visual-review.json): 40 vistas + 39 cenários; oito operações em 1440/1024/768/390/320, sem erro JS/overflow. Inclui vazio, job em fila/execução/falha/incompleto, zero/número longo, restore com falha, backup isolado e release inválida.
- [Auditoria](evidence/art-direction/audit.json): 32 pares de viewport/operação com os mesmos horários, links de prova e códigos; contraste textual computado, fonte realmente carregada, reload e navegação offline, nenhuma requisição externa. São comparados também os detalhes fechados via DOM, não só o texto visível.
- Teclado, retorno/histórico, seis casos do link de salto, impressão e HTML sem JS passaram em `scripts/test_report_browser.cjs`. Zoom CSS 200% e movimento reduzido foram exercitados. Leitor de tela, zoom nativo e dispositivos lentos continuam não verificados; nenhum estudo com operadores foi realizado.
- [Checks locais preservados](evidence/art-direction/checks.json): 47 testes do gerador, Ruff de scripts/testes, formato dos Python alterados e sintaxe JS. **Retificação de 22/09:** os cinco comandos de contratos/ops/prova usaram `-s scripts` e descobriram zero testes; exit 0 não comprovava execução dessas suítes. A [nova verificação](evidence/editorial-20260922/execution.json) usa `-s tests` e registra 58 testes efetivamente descobertos e aprovados. O resultado anterior de 56 testes na revisão v3 usa outro comando e conserva a própria identidade histórica. A expectativa antiga de ponto colorido/Aprovado na navegação foi substituída por estado semântico e texto de exceção, mantendo todos os casos de job.
- Build do frontend = geração do HTML autocontido e fixtures, sem bundler nem TypeScript. Naquela rodada visual, API/worker/imagens não mudaram; não se repetiram stack, backup, restore, scan de imagens ou CI de infraestrutura. Os resultados históricos conservam seu escopo.

### Referências e escolhas

| Referência primária inspecionada                                                                                                                                                                                                                            | Aspecto observado → adaptação                                                                                                      | O que foi rejeitado                                                                                                 |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| [Linear, redesign descrito pela equipe](https://linear.app/now/how-we-redesigned-the-linear-ui) — imagem “After” do artigo                                                                                                                                  | Conteúdo principal, lista e metadados separados por luminosidade; reduzir chrome e dar uma superfície própria ao contexto auxiliar | Copiar a navegação em L, comentários, comandos ou identidade do produto                                             |
| [Grafana, estado de alertas](https://grafana.com/docs/grafana/latest/alerting/monitor-status/view-alert-state/) e [regra no Grafana Play](https://play.grafana.org/alerting/grafana/three-times-more-page-views-than-users/view) — sessão pública carregada | Estado/condição e histórico têm funções diferentes; aprofundamento técnico acessível sem transformar todo metadado em destaque     | Gráficos sem série, ações de silenciar/excluir inexistentes e tratar saúde da regra como disponibilidade do serviço |
| [Allure, demonstração oficial](https://allure-framework.github.io/allure3-demo/awesomeAll/) — relatório público                                                                                                                                             | Linhas comparáveis, estado junto do resultado e evidência por aprofundamento                                                       | Anel percentual, árvore de suítes e categorias sem correspondência com as operações deste projeto                   |
| [Swavee, portfolio dos autores](https://www.behance.net/gallery/241721015/Visual-Identity-design-for-Swavee) — imagens de identidade, não estudo de usuários                                                                                                | Consistência entre símbolo, wordmark e versões de aplicação                                                                        | Símbolo, vidro, gradientes, peças publicitárias e qualquer alegação de exclusividade jurídica                       |
| [Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md), [Carbon dashboards](https://carbondesignsystem.com/data-visualization/dashboards/)                                                                | Estados explícitos, agrupamento por decisão, foco, reflow e movimento funcional                                                    | Usar a referência como prova de aprovação por usuários ou copiar um dashboard completo                              |

Consulta visual em 22/09/2026. As capturas oficiais e o registro de navegação ficam no caderno externo de pesquisa `art-direction-api-container-20260922`; não integram os assets do produto. As adaptações são inferências de design verificadas no navegador, não resultados de estudo com usuários. Nenhum código ou asset desses produtos foi incorporado.

A revisão aplicou critérios de composição, semântica, foco e interação. React/Next.js continua não aplicável: o HTML é gerado em Python. A marca vetorial é original para este projeto; os glifos dos wordmarks derivam das fontes licenciadas abaixo. Os SVGs têm versões compacta, wordmark, clara e monocromática; o favicon usa o símbolo. O nome continua sendo texto real na interface. Sem garantia de exclusividade jurídica da marca.

## Registro anterior — preservado

Revisão de 22/09/2026. Este documento trata do relatório local de operações. A API, o worker, o banco, as imagens e a execução dos runbooks conservam seus contratos. O HTML é um snapshot: abrir a página não consulta serviços nem dispara comandos.

## Tarefa e inventário

Quem consulta é uma pessoa avaliando uma execução ou investigando suas evidências. A pergunta principal é **o que foi verificado nesta operação e qual arquivo comprova o resultado?** O relatório não permite operar a infraestrutura.

Todas as oito vistas foram examinadas no navegador, inclusive detalhes, navegação e estados aplicáveis:

| Vista / fragmento        | Informação e decisão                                                                                              | Origem e contrato preservado                                                                         |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Verificação / `overview` | Conferir resultado, idade da prova, etapas e limites dos containers; abrir a evidência de uma etapa               | Manifesto `verification-run`, arquivos vinculados por hash e registros de hardening/jornada/recovery |
| Job / `result`           | Distinguir fila, processamento, conclusão, falha e resultado incompleto; conferir palavras, tentativas e checksum | Job selecionado pelo gerador; sua versão não identifica outra imagem                                 |
| TLS / `tls`              | Conferir teste com CA local, registro e escopo da conexão                                                         | Registro TLS; não é monitoramento de disponibilidade                                                 |
| Restauração / `recovery` | Conferir checksum, igualdade do volume e novo job; identificar separadamente a cópia utilizada                    | `restore` e `backup` mantêm datas/projetos próprios; não são associados por proximidade              |
| Troca / `operations`     | Conferir a tentativa mais recente, incluindo falha, versão e imagens                                              | Seleção cronológica existente; tentativa inválida não herda sucesso antigo                           |
| Retorno / `rollback`     | Conferir retorno após falha controlada e o job resultante                                                         | Rollback de imagem; não implica downgrade do schema ou restore do banco                              |
| Artefatos / `artifacts`  | Conferir compatibilidade entre build, auditoria e scan; consultar achados, cache e snapshot separado              | Identidade e tempo existentes; scan de outra imagem não aprova a selecionada                         |
| Arquivos / `evidence`    | Encontrar o JSON pelo nome/data/projeto e abri-lo                                                                 | Catálogo dos registros válidos; presença no catálogo não aprova uma operação                         |

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

A composição e a revisão seguiram as referências acima. Orientações específicas de React/Next.js são **Não aplicáveis**: este frontend é gerado em Python com HTML/CSS/JavaScript. Nenhuma dependência de produção foi adicionada.

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

| Tela/componente            | Evidência anterior e impacto                                                         | Prioridade       | Correção                                                                                            | Validação                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------ | ---------------- | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Todas / índice e cabeçalho | Título da operação em y≈394 px no desktop; índice e metadados antecediam o resultado | P1               | Navegação lateral, título e resultado no início do conteúdo; seletor móvel informa a operação atual | 32 capturas comparáveis e medições de geometria                               |
| Recuperação                | Prova, metadados da cópia e resultado do novo job competiam sem agrupamento claro    | P1               | Resultado e três verificações juntos; cópia separada; identidade e novo job expansíveis             | Desktop/celular, expansão por teclado e cenário de restore com falha          |
| Verificação                | Identificação repetida e resultado afastado das etapas                               | P2               | Resumo e etapas na mesma superfície; identificação completa nos detalhes                            | 69 bindings equivalentes e 47 testes do gerador                               |
| Job, TLS, troca e retorno  | Conteúdo técnico disperso e pouca distinção entre resultado e registro               | P2               | Resultado agrupado com prova; contexto técnico acessível em detalhes                                | Oito vistas, histórico, link de salto, impressão e leitura sem JS             |
| Artefatos                  | Metadados de build, snapshot e leitura de scan disputavam a mesma hierarquia         | P1               | Estado/achados primeiro; identidade, arquivos, cache e snapshot explicitamente separados            | Ausência real de scan compatível preservada; cenários inválidos não aprovados |
| Tabelas e links            | Colunas numéricas alinhadas como texto; link curto com alvo estreito                 | P2               | Células numéricas à direita e links com área mínima de 24 px                                        | Contraste de 17 pares e medição de controles visíveis                         |
| Valores ausentes           | Texto “Não informado” recebia tamanho de métrica em duas colunas estreitas           | P2               | Estilo semântico de ausência, sem converter para zero                                               | Backup isolado, vazio e valores ausentes em 320/390/1440 px                   |
| Evidência histórica        | Checkout normalizado divergindo de quatro hashes do manifesto                        | P1, preexistente | Restauração dos bytes originais e atributos exatos separados da mudança visual                      | SHA-256 estrito; JSON e bytes normalizados equivalentes                       |

## Matriz antes → depois

Legenda: **C** = Conforme no escopo descrito; **PC** = Parcialmente conforme; **NC** = Não conforme; **NV** = Não verificado; **NA** = Não aplicável. Não há média nem nota global. Os resultados descrevem os critérios verificados, não uma aprovação de usuários reais.

| Dimensão                         | Verificação | Job   | TLS   | Restauração | Troca | Retorno | Artefatos | Arquivos |
| -------------------------------- | ----------- | ----- | ----- | ----------- | ----- | ------- | --------- | -------- |
| 1. Objetivo e público            | PC→C        | PC→C  | PC→C  | PC→C        | PC→C  | PC→C    | PC→C      | C→C      |
| 2. Hierarquia e organização      | NC→C        | PC→C  | PC→C  | NC→C        | PC→C  | PC→C    | NC→C      | PC→C     |
| 3. Layout e densidade            | PC→C        | PC→C  | PC→C  | NC→C        | PC→C  | PC→C    | PC→C      | PC→C     |
| 4. Tipografia e cor              | PC→C        | PC→C  | PC→C  | PC→C        | PC→C  | PC→C    | PC→C      | PC→C     |
| 5. Indicadores e tabelas         | PC→C        | C→C   | NA→NA | PC→C        | C→C   | C→C     | PC→C      | C→C      |
| 6. Navegação e ações             | C→C         | C→C   | C→C   | C→C         | C→C   | C→C     | C→C       | C→C      |
| 7. Estados e atualização         | C→C         | C→C   | C→C   | PC→C        | C→C   | C→C     | C→C       | C→C      |
| 8. Acessibilidade/responsividade | PC→PC       | PC→PC | PC→PC | PC→PC       | PC→PC | PC→PC   | PC→PC     | PC→PC    |
| 9. Desempenho                    | NV→PC       | NV→PC | NV→PC | NV→PC       | NV→PC | NV→PC   | NV→PC     | NV→PC    |
| 10. Manutenção/reutilização      | PC→C        | PC→C  | PC→C  | PC→C        | PC→C  | PC→C    | PC→C      | PC→C     |
| 11. Dados/regras/permissões      | PC→C        | C→C   | C→C   | C→C         | C→C   | C→C     | C→C       | C→C      |

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

| Prioridade | Verificação pendente                                         | Efeito na conclusão                                                                                          |
| ---------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| P2         | Leitor de tela, zoom nativo e navegadores adicionais         | Acessibilidade permanece parcialmente verificada; não há certificação AA                                     |
| P2         | Sessão com operadores/avaliadores reais                      | As decisões têm referência técnica e revisão, mas não representam aprovação ou ganho de produtividade medido |
| P3         | Desempenho em hardware lento e impressão PDF página a página | O relatório local foi exercitado; essas condições específicas não foram medidas                              |

Não foram encontrados P0 ou P1 pendentes na apresentação examinada. O estado **Parcial** de Artefatos permanece intencional porque os registros selecionados não contêm scan/auditoria compatíveis; uma melhoria visual não pode convertê-lo em aprovado.
