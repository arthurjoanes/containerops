# Build e scan

O build gera um OCI e carrega a mesma imagem no Docker. Plataforma, bases e dependências ficam fixadas.

## Comandos e arquivos

Na raiz do projeto, com Docker Desktop Linux containers e Python 3.11+ no host:

```powershell
python -B scripts/supply.py build --version 1.0.0
python -B scripts/supply.py audit --version 1.0.0
python -B scripts/supply.py scan --version 1.0.0
python -B scripts/supply.py cache
# Depois de preparar a base de vulnerabilidades:
python -B scripts/supply.py scan --version 1.0.0 --offline
```

Runtime Windows: `%USERPROFILE%\AppData\Local\ContainerOps-runtime`. No Linux CI, configure `CONTAINEROPS_RUNTIME` com um nome que contenha `containerops`. `scripts/ops.py` chama os mesmos módulos.

Buildx recusou o caminho Unicode em `x-docker-expose-session-sharedkey`. O script copia os inputs permitidos de `app/`, `docker/` e `.dockerignore` para um caminho ASCII temporário no runtime. Recusa links/junctions e exclui caches, secrets, Git e docs. Remove a cópia após o build.

`docker/images.lock.json` fixa bases, Trivy 0.69.3, BuildKit 0.28.0 e Syft por digest. O build registra a versão do Buildx e usa `pf-containerops-builder`, com driver `docker-container`, 1,5 GiB de memória e 1,5 CPU. Se o builder tiver outro driver ou imagem, o script falha.

O Dockerfile separa deps, test e runtime. Copiar o lock antes das fontes permite cache de dependências; o cache mount do pip evita downloads repetidos. Runtime recebe venv e fontes. Debian slim aproveita wheels e o Python dos healthchecks.

Arquivos em `artifacts/<versão>/` no runtime: `image.oci.tar`, `image.docker.tar`, `filesystem.tar`, `build.log`, `build-metadata.json`, `sbom-0.json`, `provenance-0.json`, `history.jsonl` e `scanner/trivy-report.json`. Resumos: `docs/evidence/`. Cache Trivy: `trivy-cache/`.

## Imagem e OCI

| Identidade | Significado | Verificação |
|---|---|---|
| SHA-256 do índice OCI | Agrega manifests de plataforma e attestations | Hash dos bytes de `index.json`; não é o ID Docker |
| Digest do manifesto linux/amd64 | Referencia config e camadas comprimidas | Verificação SHA-256 e tamanho de cada descritor; subject das attestations deve corresponder |
| Config digest | Configuração executável, histórico e diff_ids | SHA-256 dos bytes do config em `docker image save` precisa coincidir com config OCI |
| ID apresentado pelo daemon | Config digest no store clássico; manifesto no Docker 29/containerd observado | Manifesto exportado precisa ter esse digest e referenciar o config esperado |

`oci_audit.py` converte OCI para `docker load` mantendo config e camadas descomprimidas. Confere blob digest, `diff_id` e `RootFS.Layers`; depois exporta a imagem pelo ID do daemon e confere config/camadas de novo. No Docker 29/containerd deste host, `inspect.Id` é o digest do manifesto: o manifesto exportado deve apontar ao config OCI.

Audit exige SBOM SPDX e provenance, confere subjects, versões de `requirements-runtime.lock` e base Python com digest. Para bases multiarch, o lock pode usar o digest do índice e a provenance indicar o manifesto da plataforma.

## Sentinela

Setup cria `secrets/sentinel` no runtime. O build recebe o arquivo por `--secret`; `RUN --mount=type=secret,id=sentinel,required=true` testa se ele existe sem copiar ou imprimir o conteúdo.

Audit procura os bytes da sentinela nas camadas descomprimidas, config/histórico, SBOM/provenance, histórico do daemon e filesystem exportado de um container. Guarda o hash para identificar o teste e remove o container pelo ID.

## Política de vulnerabilidades

Trivy roda sem socket Docker, com filesystem somente leitura, capabilities removidas, `no-new-privileges` e limites de recursos. Só cache, relatório e `/tmp` são graváveis. `Metadata.ImageID` deve corresponder ao config digest do build; o scan precisa incluir SO e dependências.

O primeiro scan baixa a base e registra versão/schema, `UpdatedAt`, idade e SHA-256. A análise usa essa base sem atualizar e confere o hash ao terminar. `--offline` exige base disponível com até 72 horas.

HIGH/CRITICAL com `FixedVersion` bloqueia o comando. As vulnerabilidades sem correção ficam no relatório. O ignorefile é `/dev/null`. Antes do scan, o resultado vira `incomplete`; depois de todas as verificações, `passed`.

O scan de 2026-09-21 encontrou `msgpack 1.1.2` (GHSA-6v7p-g79w-8964) e `setuptools 70.3.0` (CVE-2025-47273) no pip da base/venv. Remover pip/ensurepip do runtime reduziu o SBOM de 140 para 121 pacotes, mantendo as 17 versões do lock. Com a mesma base Trivy, o reteste teve zero HIGH/CRITICAL corrigíveis. Resultado inicial: `docs/evidence/scan-initial-failed.json`.

O reteste manteve 247 vulnerabilidades sem correção, incluindo 55 HIGH e 5 CRITICAL. Remover pip em outra camada tira a ferramenta do filesystem final, mas mantém os bytes nas camadas da base.

## Cache

`cache` faz quatro builds de uma cópia temporária no runtime. Todos usam o mesmo comentário exclusivo no lock copiado para evitar reuso de uma série anterior. As imagens de teste não são carregadas.

1. `initial`: usa `--no-cache`; o cache de download do pip pode estar aquecido.
2. `unchanged`: mesmo contexto, deve reutilizar instalação de dependências.
3. `source`: acrescenta um marcador Python inofensivo em fonte copiada; COPY da fonte deve executar, instalação deve continuar em cache.
4. `dependency`: troca `idna` 3.20 por 3.19 no lock copiado. A instalação deve rodar e o SBOM deve mostrar 3.19. Ambas atendem `idna>=2.8` do AnyIO; [idna 3.19](https://pypi.org/project/idna/3.19/) suporta Python 3.13.

Logs, duração e cache por etapa ficam no runtime; resumo em `docs/evidence/cache-experiment.json`. Só dependency exporta OCI e SBOM, então os tempos totais incluem etapas diferentes. O script confere a invalidação esperada e remove o contexto temporário.

## Atualizar bases e dependências

Resolva a versão/digest com `docker buildx imagetools inspect <imagem:versão>`. Atualize lock e defaults do Dockerfile. Resolva dependências em ambiente descartável. Gere outra imagem e rode testes, audit, scan e HTTP. Guarde o ID anterior para rollback.

Referências oficiais: [armazenamento de attestations](https://docs.docker.com/build/metadata/attestations/attestation-storage/), [exportadores OCI/Docker](https://docs.docker.com/build/exporters/oci-docker/), [geração de SBOM](https://docs.docker.com/build/metadata/attestations/sbom/), [driver docker-container](https://docs.docker.com/build/builders/drivers/docker-container/), [Trivy em arquivos de imagem](https://trivy.dev/docs/latest/target/container_image/) e [bases do Trivy](https://trivy.dev/docs/latest/configuration/db/).
