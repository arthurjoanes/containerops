# Recortes da interface atual

## Revisão de UI/UX — 12/10/2026

A apresentação atual está no [relatório de cópias](evidence/uiux-20261012/view/docs/report.html#backups). Usa os mesmos JSONs operacionais já publicados em 12/10 e um relógio de geração compartilhado: **13:32:03 UTC**. A revisão visual não refez backup ou restore. [Proveniência e QA](evidence/uiux-20261012/review.md) registram os hashes das fontes congeladas e das capturas.

| Vista e viewport | Antes | Depois |
| --- | --- | --- |
| Catálogo, 1440 px | [Tabela](screenshots/uiux-20261012/before-backups-1440.png) | [Lista com contexto e filtros](screenshots/uiux-20261012/after-backups-1440.png) |
| Catálogo, 320 px | [Tabela comprimida](screenshots/uiux-20261012/before-backups-320.png) | [Identidade e estados legíveis](screenshots/uiux-20261012/after-backups-320.png) |
| Restore, 1440 px | [Três critérios](screenshots/uiux-20261012/before-recovery-1440.png) | [Quatro critérios e ID](screenshots/uiux-20261012/after-recovery-1440.png) |
| Restore, 320 px | [Antes](screenshots/uiux-20261012/before-recovery-320.png) | [Depois](screenshots/uiux-20261012/after-recovery-320.png) |
| Limpeza falhou, 320 px | [Aprovação incorreta](screenshots/uiux-20261012/before-cleanup-failed-320.png) | [Falha explícita](screenshots/uiux-20261012/after-cleanup-failed-320.png) |

Cada linha mantém fixture/viewport/estado e enquadramento equivalentes; o catálogo mudou de `#recovery` para `#backups`. Limpeza com falha é uma **fixture de apresentação**, identificada na tela, sem operação executada. [Catálogo em 390 px](screenshots/uiux-20261012/after-backups-390.png) é uma captura adicional, sem par de comparação. [Vista completa com a marca](screenshots/uiux-20261012/after-overview-backups-1440.png) é o novo destaque da README. A galeria e os recibos históricos abaixo permanecem preservados.

## Capturas históricas de 22/09/2026

O [manifesto](screenshots/focused-20260922/capture.json), as [entradas preservadas](screenshots/focused-20260922/inputs.json) e o [coletor](../scripts/capture_docs.py) documentam a origem das imagens. A data de renderização ou captura não substitui a data da operação.

Capturados em **22/09/2026, 17:37 UTC**, diretamente no navegador, sem montagem ou alteração dos dados. Cada imagem isola o trecho relacionado à explicação; páginas históricas completas ficam disponíveis por links.

O renderer daquela rodada leu os JSONs operacionais preservados. A restauração exibida pertence a **22/09/2026, 06:20 UTC**: **3 jobs / 27,4 s**; a cópia tem horário próprio de **06:19:27 UTC**. Capturar a página não executa restore, backup, jobs ou worker.

| Foco                                      | Imagem                                                                   |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| Contagem e três validações da restauração | [Abrir recorte](screenshots/focused-20260922/restauracao-foco.png)       |
| Mesmo resumo no celular                   | [Abrir recorte](screenshots/focused-20260922/restauracao-movel-foco.png) |
| Resultado do novo job                     | [Abrir recorte](screenshots/focused-20260922/novo-job-foco.png)          |
| Registro independente da cópia            | [Abrir recorte](screenshots/focused-20260922/copia-foco.png)             |

O [manifesto](screenshots/focused-20260922/capture.json) registra regiões, seletores, fontes e código; os [hashes de entrada](screenshots/focused-20260922/inputs.json) identificam os dados preservados. Fontes carregadas, ausência de erros JavaScript e reflow foram conferidos. Os recortes desktop têm altura/largura de no máximo 1,1; os móveis têm altura inferior a 650 px. A captura usa `clip` a partir dos limites reais dos elementos, sem reduzir um print longo para caber na documentação.

## Reproduzir

Requer Node.js, Playwright e Chromium/Edge. Defina `PLAYWRIGHT_MODULE` com o caminho absoluto do módulo e `PLAYWRIGHT_CHANNEL=msedge` para usar Edge. Requer também Python 3.11+. Os registros versionados são copiados para um diretório temporário ignorado e renderizados offline; os containers não são necessários. O auditor histórico percorre as oito vistas daquela rodada em 1120, 390 e 320 px, mas salva apenas os quatro recortes úteis abaixo.

```sh
python scripts/capture_docs.py .runtime/docs-captures/novo-recorte
```

Escolha uma saída nova: o script recusa sobrescrever imagens. Confira região, legibilidade e datas antes de promover outro recorte à README. O histórico não é substituído por capturas novas: seus manifests e pixels permanecem com a execução original.

## Conservação e limpeza

Capturas com hashes e pares de comparação continuam ligados às execuções originais. O inventário da limpeza e as cópias de segurança anteriores ficaram fora do repositório; não são uma prova pública de contagem de arquivos removidos. Para a proveniência das imagens publicadas, use o manifesto ligado acima, de **22/09/2026**.
