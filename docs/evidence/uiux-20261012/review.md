# Revisão de UI/UX do ContainerOps — 12/10/2026

Esta entrega melhora localizar cópias, conferir o ensaio e preparar a CLI. O catálogo virou destino próprio; integridade, recibo e proteção permanecem separados. O último ensaio exige checksum, snapshot, novo job e limpeza, além de status final aprovado. A [paleta](../../brand.md) dá papéis consistentes à marca, navegação e estados.

## Proveniência

Base da UI: `4816852ed7623be1e844d6fc1a2311bcaf486114`. Implementação: `af73ded`, com ajuste da marca impressa em `02a9c56`. O candidato final foi congelado no HEAD `02a9c5662e2eef7870540e3782be1fa0e0fc5725`, sem diff nos arquivos do renderer. O [manifesto](provenance.json) identifica os bytes executados por SHA-256; o antes usa o commit base acima. As rodadas preliminares tinham diff e módulo novo não rastreado, e não são atribuídas ao candidato final.

Os registros reais foram copiados, byte por byte, do acervo público `containerops-evolution-20261012/evidence`. São provas de operações já salvas em 12/10; suas datas não foram renovadas. Antes e depois usam os mesmos hashes de entrada e a mesma geração **2026-10-12T13:32:03Z**. Os cenários adicionais alteram somente cópias desses JSONs e mostram o aviso “Cenário de apresentação”; não executam operações.

Capturas e recibos anteriores ficaram preservados. Rodadas preliminares e fontes congeladas permanecem nos diretórios ignorados `artifacts/uxq*`; esta pasta publica somente a base corrigida do auditor e o candidato final. Correções do harness não foram tratadas como falhas do produto: um probe inicial de teclado começava no fim do documento, e a comparação de clipboard precisava reconhecer CRLF do Windows como LF do texto exibido. A segunda tentativa da base foi interrompida por um erro de inicialização de Firefox antes da rejeição nativa; a base final terminou com o segundo motor marcado como não verificado, sem nova tentativa de início. Ambos foram corrigidos em saídas novas, sem regravar recibos.

## Diagnóstico, jornadas e decisão

A [revisão de qualidade](../../frontend-quality.md#revisão-de-12102026-localizar-cópias-e-conferir-restore) descreve cinco jornadas e compara duas composições. A composição B dá ao catálogo uma vista própria, evitando depender do último restore para escolher uma cópia. A [galeria](../../screenshots.md#revisão-de-uiux--12102026) contém pares com a mesma fixture, viewport e estado, além de recortes de erro/ausência.

Na base, 320 px produziam uma página de 378 px no restore; uma tentativa com limpeza falha aparecia como “Passou”; o catálogo capturado em outra sessão era apresentado como padrão. O candidato corrige esses três problemas comprovados e torna data/origem/ID mais fáceis de conferir. Não foram feitas entrevistas nem medições de satisfação, conversão ou produtividade.

## Testes e revisão visual

Os recibos [antes](before-browser.json) e [depois](after-browser.json) guardam cada verificação, cenário, versão de navegador, fonte do harness, fonte de axe e captura com hash. [Inspeção visual](visual-inspection.json) registra imagens abertas em tamanho legível e os resultados inconclusivos. [Testes funcionais](functional.json) separa host, renderer e gates de sintaxe/formato.

O escopo de navegador inclui nove vistas em 320, 390, 768, 1024 e 1440 CSS px; teclado e foco; histórico; filtros preservados; detalhes nativos; copiar texto e fallback com seleção; leitura sem JavaScript; impressão e contraste do cabeçalho; movimento reduzido; texto CSS a 200%; links e ausência de chamadas externas. Cenários: catálogo vazio e malformado, integridade sem recibo, limpeza com falha, registro legado sem limpeza, IDs extensos e importados. A base tem 209 verificações e dez achados: nove de reflow/semântica e um alvo local de contraste da marca decorativa na impressão. Esse último não é uma violação axe/WCAG. O candidato passou em 238 verificações, com 24 varreduras axe sem violações, além dos oito casos de skip link do teste existente. Os resultados inconclusivos permanecem no recibo.

O relatório é estático e não tem formulário de operação, conta, sessão autenticada, loading/timeout de API ou modal. Filtros e clipboard são as únicas ações novas de JavaScript; nenhuma delas envia dados ou opera Docker. O comando importado não ganha `--trusted-source` automaticamente. Contexto inválido impede sugerir restore por ID. Markdown selecionado acompanha o relatório de sessão por digest de conteúdo; conflitos e hardlinks são recusados e o relatório histórico do repositório é preservado.

Resultados funcionais locais: **140 testes do host aprovados e três skips de plataforma**, **71 testes do renderer aprovados**, Ruff e sintaxe JavaScript aprovados. Os três skips são dois casos que exigem privilégio de symlink no Windows e um de permissões POSIX. As tentativas com fontes estáveis, horários e hashes de logs estão no recibo funcional. O CI completo do código publicado é acompanhado na [PR #1](https://github.com/arthurjoanes/containerops/pull/1); sua execução é separada desta reprodução visual.

O teste existente teve uma tentativa interrompida por sincronização do auditor na troca de 390 para 1440 px: ele focava o link antes do evento nativo abrir o índice. A versão corrigida aguarda a visibilidade/abertura antes de enviar Enter e mantém as mesmas asserções de foco, histórico, impressão e leitura sem JavaScript. O [recibo de regressão](browser-regression.json) preserva o resultado e o hash da tentativa anterior.

## Limites

axe sem violações não certifica acessibilidade integral. Nós inconclusivos de contraste ainda exigiram inspeção das cores e leitura. CSS a 200% verifica ampliação de texto, não zoom nativo. **Zoom nativo, preferências do sistema e leitor de tela não foram verificados.**

Firefox não foi executado. A revisão automática rejeitou a inicialização nativa do processo com “blocked by policy”, sem motivo detalhado. Nenhum launcher alternativo foi tentado depois da rejeição. Chromium e o navegador integrado são as superfícies efetivamente usadas. Uma sondagem adicional de cores no Chromium, depois da QA principal, também foi rejeitada com “blocked by policy”, sem motivo detalhado; não foi repetida. As amostras anteriores de cores da tela e a medição efetiva da marca impressa na QA final estão identificadas no recibo visual.

Esta revisão não repetiu backup, restore ou operações Docker locais. As provas históricas continuam com seus próprios commits e datas; o CI da entrega verifica o código publicado em uma execução separada. Os guias copiados são textos selecionados; imagens históricas e código citados nesses textos permanecem no repositório.

## Reproduzir o candidato

Use Python 3.11+ e Node.js. Instale Playwright 1.63.0 e axe-core 4.14.0 em um diretório de ferramentas isolado; Chromium compatível foi usado nesta rodada. Defina `PLAYWRIGHT_MODULE`, `PLAYWRIGHT_EXECUTABLE_PATH` e `AXE_CORE_MODULE` com os caminhos dessa instalação, sem adicionar dependências à aplicação.

```powershell
python scripts/render_report_review.py artifacts/review-nova --generated-at 2026-10-12T13:32:03Z
# Em outro terminal, mantenha aberto o servidor; use a porta que ele informa.
python scripts/serve_report_review.py artifacts/review-nova
node scripts/review_report_browser.cjs http://127.0.0.1:<porta> artifacts/browser-review-nova
node scripts/test_report_browser.cjs http://127.0.0.1:<porta>/real/docs/report.html
python scripts/test_report.py
python -m unittest discover -s tests -v
```

Renderer e harness recusam saídas existentes. Congele as fontes e registre o manifesto antes de capturar; não use somente HEAD para atribuir código com mudanças locais. Para reconstruir o antes, use os arquivos de renderer do commit base em um checkout temporário separado, com o mesmo coletor/cenários e os JSONs/hash deste manifesto. Não abra o repositório inteiro no servidor de revisão nem inclua o diretório `frozen` ou runtime privado. O harness aceita apenas loopback HTTP. O servidor dedicado rejeita listagem de diretórios, fonte congelada, manifestos e travessia de caminhos; nove requisições de aceite/recusa foram conferidas. Encerre o servidor com Ctrl+C depois da revisão.
