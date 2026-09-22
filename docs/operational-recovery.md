# Preservar trabalhos durante rollback e restauração

O [manifesto de 22/09/2026, 06:17–06:20 UTC](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json), o [rollback](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/rollback.json) e o [restore](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/restore.json) registram a mesma tentativa histórica.

**Registro histórico:** as imagens desta página pertencem às execuções identificadas abaixo e preservam seus bytes originais. Veja a [galeria da interface atual](screenshots.md) para a apresentação do código atual.

**Problema central:** voltar a versão do programa não pode apagar o trabalho que ela já aceitou. E encontrar um dump no disco não basta: o serviço restaurado precisa devolver os dados corretos e aceitar um novo trabalho.

A execução `fc39e58c890841a089c9b1173869b0aa`, em **22/09/2026, 06:17:46–06:20:05 UTC**, testou essa sequência com containers e PostgreSQL reais, textos sintéticos e namespaces descartáveis. Terminou aprovada em **139,750 s**, incluindo a limpeza. O [manifesto da operação](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json) registra fontes, imagens, etapas e artefatos. É um cenário limitado de operações, separado da prova completa de build, segurança, TLS e integração.

## 1. Identificar o trabalho antes de mudar a versão

A release 1 processou `Trabalho identificado e preservado`: **quatro palavras**, checksum dos bytes originais e ID `29a855d0-7e64-4055-9f9e-0004c4ff4175`. Esse registro permite conferir preservação por identidade e resultado, em vez de observar apenas uma mensagem de sucesso. [Job inicial](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/demo.json).

[Captura histórica completa: Job inicial concluído, com quatro palavras, ID e checksum visíveis](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/job-1440.png)

Captura do relatório gerado em 22/09 às 06:29 UTC a partir dos JSONs reais desta execução. O relatório é uma leitura de resultados salvos, sem consulta ao serviço ao abrir a página. Os estados ausentes de TLS, scan e prova completa não são apresentados como aprovados nesse cenário curto.

## 2. Voltar a imagem depois de uma falha real da candidata

A release 2 ampliou o schema e concluiu outro job. Em seguida, o runner provocou falha no teste de prontidão funcional da candidata (_smoke test_). A operação voltou API e worker à imagem anterior, sem rebaixar o schema nem restaurar dados antigos.

O job criado pela candidata, `d734dd01-4e3e-4d37-8f50-b2e6b930834b`, continuou consultável, com quatro palavras e o mesmo checksum. A release 1 também concluiu `Rollback preserva dados`, com três palavras. A origem passou a ter **três jobs concluídos**, preservando o inicial. [Resultado do rollback](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/rollback.json).

Essa estratégia depende da compatibilidade da migração expansiva. Não cobre uma mudança que remova campos necessários à versão anterior. `version` na resposta identifica a aplicação que respondeu à consulta; comparar o resultado persistido não exige que a versão da resposta continue sendo 2.0.0 após o rollback.

[Captura histórica completa: Retorno após falha controlada com imagens identificadas e novo job concluído pela versão anterior](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/rollback-1440.png)

A imagem mostra o retorno após falha, não uma promoção bem-sucedida. Os 44,9 s exibidos são o intervalo interno após o backup preliminar, incluindo troca, verificações e retorno ao estado de pausa anterior. A chamada completa de release/rollback levou 58,625 s. Esse intervalo não mede indisponibilidade contínua para o usuário.

## 3. Restaurar a cópia e continuar trabalhando

O backup pausou admissões, drenou a fila e exportou o dump. Uma cópia do dump **e dos metadados** foi guardada em diretório privado novo, fora do repositório. Os hashes conferiram; as permissões Windows foram verificadas para a pasta e os dois arquivos, limitadas ao usuário atual e SYSTEM. Isso é controle de acesso local, não criptografia nem armazenamento independente do computador. [Cópia e proteção observadas](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/protected-copy.json).

O destino foi outro projeto e volume vazio. A restauração conferiu o checksum, os três jobs e o snapshot completo do contrato — IDs, proprietários, estados, tentativas, contagens, checksums, schema e pausa. Depois processou `Backup restaurado com sucesso`: **quatro palavras**, ID `5787bb14-bd06-4e2d-83b3-9a101a8a5b6d` e checksum esperado. [Resultado da restauração](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/restore.json).

A [conferência da origem](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/source-preservation.json) encontrou os mesmos jobs, imagens e estado de pausa após o ensaio, além do backup original íntegro. Os arquivos de estado do ambiente principal também foram comparados antes/depois. O destino e a origem descartáveis foram removidos; o resultado só passou a aprovado depois das verificações de limpeza.

[Captura histórica completa: Três jobs restaurados e um novo trabalho com quatro palavras no volume de destino](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/restore-new-job-1440.png)

A captura expõe os três critérios: cópia conferida, dados comparados e novo job concluído. Os 27,4 s representam a recuperação interna; 29,578 s incluem preflight e limpeza. O registro da cópia mostra 8,4 s internos antes da finalização do backup; a chamada completa levou 10,968 s. Essas fronteiras estão separadas na tabela abaixo.

[Versão no celular: job](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/job-390.png) · [rollback](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/rollback-390.png) · [restore](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/restore-new-job-390.png). O [manifesto das capturas](evidence/operations-captures/fc39e58c890841a089c9b1173869b0aa-848eec21/manifest.json) identifica renderer, navegador, JSONs e hashes. Capturar novamente o relatório não repete as operações Docker.

## 4. O que os tempos medem

| Medida               | Observado | Fronteira                                                                                                      |
| -------------------- | --------: | -------------------------------------------------------------------------------------------------------------- |
| Cenário completo     | 139,750 s | Preparação e conferências, job, rollback, backup/cópia, controle negativo, restore e limpeza da origem         |
| Operação de rollback |  58,625 s | Chamada da operação de release com falha controlada e retorno validado                                         |
| Backup               |  10,968 s | Chamada de backup, incluindo retorno ao estado de pausa anterior                                               |
| Cópia protegida      |   0,516 s | Cópia e conferência de hashes/permissões                                                                       |
| Recuperação interna  |  27,437 s | Após preflight/configuração do destino até validar o novo job; campo histórico `recovery_seconds`              |
| Restore completo     |  29,578 s | Entrada no restore, checksum/preflight, recuperação, limpeza e conferência de ausência dos recursos do destino |

As fases internas constam no JSON. Elas não devem ser somadas aos totais acima como trabalho adicional: são intervalos contidos na mesma operação. São tempos de uma execução local com três jobs, não SLA ou comparação de desempenho com a [prova histórica de oito jobs](verification.md#execução-de-22092026-utc).

O corte dos dados foi observado entre **06:19:21.653220 e 06:19:26.546757 UTC**, durante pausa/drenagem e obtenção do snapshot. Quando o novo job foi validado, esse corte tinha idade entre **32,277 e 37,170 s**. A idade desde a criação dos metadados era 31,384 s; são marcos diferentes. Não houve escrita comercial contínua nesta fixture e esse resultado não estabelece uma política de RPO.

## 5. Controles negativos e dificuldades

Uma cópia separada foi adulterada; o restore recusou seu checksum **antes de criar o destino**. O backup válido permaneceu intacto. Esse controle detecta divergência entre arquivo e manifesto; não autentica um par de arquivo/manifesto alterado conjuntamente. [Recusa registrada](evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/corrupted-copy-rejected.json).

A revisão encontrou três problemas de apresentação/registro que foram tratados: o restore podia gravar resultado antes da limpeza terminar; a nova idade em intervalo precisava respeitar o contrato do parser do relatório; e um cenário curto precisava de ponteiro próprio para não substituir o atalho da prova completa. A implementação preserva `recovery_seconds` e acrescenta duração total/fases, deixando explícito quando cada medição começa.

O novo cenário usa `operations-proof-latest.json`. O cenário padrão continua completo e mantém `problem-proof-latest.json`. Os resultados de testes dos comandos e do relatório têm sua própria identidade; não são somados aos quatro jobs observados entre origem e destino.

A publicação também exigiu cuidado com as quebras de linha: os JSONs originais do Windows foram preservados privadamente, e as cópias públicas usam LF, com referências recalculadas. O [registro de publicação](evidence/operations-publication.json) distingue essas representações e as mudanças posteriores no gravador/coletor. Dos 63 arquivos de fonte da execução, 62 tiveram os bytes originais recuperados para conferência; o CSS anterior não foi recuperado. Seu hash histórico foi mantido como registro, e as capturas finais identificam o CSS posterior. Não se declara que o conjunto final de fontes é idêntico ao executado. Os hashes de dump, metadados privados e imagens não foram substituídos.

## 6. Como repetir e o que ainda falta

Na raiz, com Docker/Compose/Buildx e Python nos requisitos do README:

```sh
python scripts/ops.py build --version 1.0.0
python scripts/ops.py build --version 2.0.0
docker buildx stop pf-containerops-builder
python scripts/ops.py prove --scenario operations
```

Os builds são preparação; o cenário confere as fontes dentro das imagens antes de reutilizá-las. Use uma janela sem outra operação no builder. As imagens desta prova foram `sha256:08fc96aefa056da9a3ab0d0869bd91cef7751caa4c8249d139d1bfcefb3836ed` e `sha256:ee23bc56b175a8c775240f5063e7d4baa242fd26addf1d7ba8b6da5c3f9268b8`. Não houve novo scan dentro desse cenário, e o scan histórico de outra imagem não passa a aprová-las.

O resultado informa a pasta imutável da tentativa. Dumps e metadados privados ficam no runtime reservado; publique apenas os resumos sanitizados. A cópia foi independente do volume de origem, mas permaneceu no mesmo host. O [roteiro para outro computador](runbooks.md#recuperação-em-outro-computador--roteiro-ainda-não-executado) continua pendente de execução. O ensaio também não mede uso por uma equipe real nem garante tempo de recuperação para uma carteira maior.

Para gerar as capturas do relatório salvo, é necessário Node, Playwright instalado e um navegador Chromium/Edge. No PowerShell, indique os caminhos da sua instalação:

```powershell
$env:PLAYWRIGHT_MODULE = '<diretorio-do-modulo-playwright>'
$env:PLAYWRIGHT_EXECUTABLE_PATH = '<caminho-do-executavel-do-navegador>'
python scripts/capture_operations.py docs/evidence/problem-proof/fc39e58c890841a089c9b1173869b0aa/manifest.json
```

Esse comando cria outra tentativa de captura e confere seus links; não restaura novamente o banco. O manifesto é o vínculo entre a execução e os JSONs. Alguns registros individuais não contêm `run_id`, por isso o relatório mostra **Execução: não informado** nesses painéis; o projeto e a data continuam visíveis.
