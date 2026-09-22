# Recortes da interface atual

Proveniência conferida em **22/09/2026**: [manifesto](screenshots/focused-20260922/capture.json), [entradas preservadas](screenshots/focused-20260922/inputs.json) e [coletor](../scripts/capture_docs.py). A data de renderização/captura não substitui a data da operação.

Capturados em **22/09/2026, 17:37 UTC**, diretamente no navegador, sem montagem ou alteração dos dados. Cada imagem isola o trecho relacionado à explicação; páginas históricas completas ficam disponíveis por links.

O renderer atual lê os JSONs operacionais preservados. A restauração exibida pertence a **22/09/2026, 06:20 UTC**: **3 jobs / 27,4 s**; a cópia tem horário próprio de **06:19:27 UTC**. Capturar a página não executa restore, backup, jobs ou worker.

| Foco                                      | Imagem                                                                   |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| Contagem e três validações da restauração | [Abrir recorte](screenshots/focused-20260922/restauracao-foco.png)       |
| Mesmo resumo no celular                   | [Abrir recorte](screenshots/focused-20260922/restauracao-movel-foco.png) |
| Resultado do novo job                     | [Abrir recorte](screenshots/focused-20260922/novo-job-foco.png)          |
| Registro independente da cópia            | [Abrir recorte](screenshots/focused-20260922/copia-foco.png)             |

O [manifesto](screenshots/focused-20260922/capture.json) registra regiões, seletores, fontes e código; os [hashes de entrada](screenshots/focused-20260922/inputs.json) identificam os dados preservados. Fontes carregadas, ausência de erros JavaScript e reflow foram conferidos. Os recortes desktop têm altura/largura de no máximo 1,1; os móveis têm altura inferior a 650 px. A captura usa `clip` a partir dos limites reais dos elementos, sem reduzir um print longo para caber na documentação.

## Reproduzir

Requer Node.js, Playwright e Chromium/Edge. Defina `PLAYWRIGHT_MODULE` com o caminho absoluto do módulo e `PLAYWRIGHT_CHANNEL=msedge` para usar Edge. Requer também Python 3.11+. Os registros versionados são copiados para um diretório temporário ignorado e renderizados offline; os containers não são necessários. O auditor percorre as oito vistas em 1120, 390 e 320 px, mas salva apenas os quatro recortes úteis abaixo.

```sh
python scripts/capture_docs.py .runtime/docs-captures/novo-recorte
```

Escolha uma saída nova: o script recusa sobrescrever imagens. Confira região, legibilidade e datas antes de promover outro recorte à README. O histórico não é substituído por capturas novas: seus manifests e pixels permanecem com a execução original.

## Conservação e limpeza

Capturas com hashes e pares de comparação continuam ligados às execuções originais. O inventário da limpeza e as cópias de segurança anteriores ficaram fora do repositório; não são uma prova pública de contagem de arquivos removidos. Para a proveniência das imagens publicadas, use o manifesto ligado acima, de **22/09/2026**.
