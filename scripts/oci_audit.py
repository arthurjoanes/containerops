from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import BinaryIO


def stream_digest(source: BinaryIO, sentinel: bytes = b"", output: BinaryIO | None = None) -> str:
    digest = hashlib.sha256()
    tail = b""
    while chunk := source.read(1024 * 1024):
        if sentinel and sentinel in tail + chunk:
            raise RuntimeError("Sentinela encontrada no artefato")
        tail = chunk[-max(len(sentinel) - 1, 0) :] if sentinel else b""
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return "sha256:" + digest.hexdigest()


def file_digest(path: Path, sentinel: bytes = b"") -> str:
    with path.open("rb") as source:
        return stream_digest(source, sentinel)


def normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_packages(path: Path) -> dict[str, str]:
    packages = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s;\\]+)", line.strip())
        if match:
            packages[normalize_package(match[1])] = match[2]
    if not packages:
        raise RuntimeError(f"Nenhum pacote fixado em {path.name}")
    return packages


class OCIArchive:
    def __init__(self, path: Path):
        self.path = path
        self.tar = tarfile.open(path, "r:*")
        self.index_bytes = self.read_name("index.json")
        self.index = json.loads(self.index_bytes)
        self.manifests: list[tuple[dict, dict]] = []
        self._walk(self.index)
        candidates = [item for item in self.manifests if self._runnable(item)]
        if len(candidates) != 1:
            self.close()
            raise RuntimeError("OCI precisa conter uma imagem linux/amd64")
        self.descriptor, self.manifest = candidates[0]
        self.config_bytes = self.read_blob(self.manifest["config"])
        self.config = json.loads(self.config_bytes)

    def __enter__(self) -> OCIArchive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.tar.close()

    def read_name(self, name: str) -> bytes:
        member = self.tar.getmember(name)
        if not member.isfile():
            raise RuntimeError(f"Membro OCI não é arquivo regular: {name}")
        with self.tar.extractfile(member) as source:
            return source.read()

    @staticmethod
    def blob_name(descriptor: dict) -> str:
        digest = descriptor["digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise RuntimeError("Descriptor precisa usar SHA-256")
        return "blobs/sha256/" + digest.split(":", 1)[1]

    def read_blob(self, descriptor: dict) -> bytes:
        content = self.read_name(self.blob_name(descriptor))
        if len(content) != descriptor["size"]:
            raise RuntimeError("Tamanho de blob OCI divergente.")
        if "sha256:" + hashlib.sha256(content).hexdigest() != descriptor["digest"]:
            raise RuntimeError("Checksum de blob OCI divergente.")
        return content

    def _walk(self, index: dict) -> None:
        for descriptor in index["manifests"]:
            value = json.loads(self.read_blob(descriptor))
            if "manifests" in value:
                self._walk(value)
            elif "config" in value and "layers" in value:
                self.manifests.append((descriptor, value))

    @staticmethod
    def _runnable(item: tuple[dict, dict]) -> bool:
        descriptor, manifest = item
        if (
            manifest.get("artifactType")
            or descriptor.get("annotations", {}).get("vnd.docker.reference.type")
            == "attestation-manifest"
        ):
            return False
        platform = descriptor.get("platform", {})
        return platform.get("os") == "linux" and platform.get("architecture") == "amd64"

    def attestations(self, sentinel: bytes) -> list[dict]:
        statements = []
        for descriptor, manifest in self.manifests:
            target = manifest.get("subject", {}).get("digest") or descriptor.get(
                "annotations", {}
            ).get("vnd.docker.reference.digest")
            if target != self.descriptor["digest"]:
                continue
            self.read_blob(manifest["config"])
            for layer in manifest["layers"]:
                if layer["mediaType"] != "application/vnd.in-toto+json":
                    continue
                content = self.read_blob(layer)
                if sentinel in content:
                    raise RuntimeError("Sentinela encontrada na attestation.")
                statement = json.loads(content)
                subjects = {
                    "sha256:" + subject.get("digest", {}).get("sha256", "")
                    for subject in statement.get("subject", [])
                }
                if self.descriptor["digest"] not in subjects:
                    raise RuntimeError("Subject da attestation não corresponde ao manifesto.")
                statements.append(statement)
        return statements

    def copy_layer(self, descriptor: dict, output: BinaryIO, sentinel: bytes) -> str:
        member = self.tar.getmember(self.blob_name(descriptor))
        if member.size != descriptor["size"] or not member.isfile():
            raise RuntimeError("Tamanho/tipo de camada OCI divergente.")
        with self.tar.extractfile(member) as compressed:
            if stream_digest(compressed) != descriptor["digest"]:
                raise RuntimeError("Checksum da camada OCI divergente.")
            compressed.seek(0)
            media = descriptor["mediaType"]
            if media.endswith("+gzip") or media.endswith(".gzip"):
                with gzip.GzipFile(fileobj=compressed) as source:
                    return stream_digest(source, sentinel, output)
            if media.endswith(".tar"):
                return stream_digest(compressed, sentinel, output)
            raise RuntimeError(f"Compressão OCI não suportada: {media}")


def add_bytes(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(value)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(value))


def inspect_docker_archive(path: Path, expected: dict, daemon_id: str) -> dict:
    "Docker 29/containerd retorna o ID do manifesto; confira o config exportado."
    with tarfile.open(path, "r:*") as archive:

        def read(name: str) -> bytes:
            member = archive.getmember(name)
            if not member.isfile():
                raise RuntimeError("Export Docker contém referência não regular.")
            with archive.extractfile(member) as source:
                return source.read()

        manifests = json.loads(read("manifest.json"))
        if len(manifests) != 1:
            raise RuntimeError("Export do daemon deve conter uma imagem.")
        manifest = manifests[0]
        config_bytes = read(manifest["Config"])
        digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
        if digest != expected["config_digest"]:
            raise RuntimeError("Config exportado do daemon diverge do config OCI auditado.")
        config = json.loads(config_bytes)
        if config["rootfs"]["diff_ids"] != expected["diff_ids"]:
            raise RuntimeError("Config exportado lista camadas diferentes das auditadas.")
        actual_layers = []
        for name in manifest["Layers"]:
            with archive.extractfile(archive.getmember(name)) as source:
                actual_layers.append(stream_digest(source))
        if actual_layers != expected["diff_ids"]:
            raise RuntimeError("Conteúdo das camadas exportadas do daemon diverge do OCI.")
        if daemon_id != digest:
            index = json.loads(read("index.json"))
            descriptors = [item for item in index["manifests"] if item["digest"] == daemon_id]
            if len(descriptors) != 1:
                raise RuntimeError("Export não contém o descriptor correspondente ao ID do daemon.")
            descriptor = descriptors[0]
            payload = read(OCIArchive.blob_name(descriptor))
            if (
                len(payload) != descriptor["size"]
                or "sha256:" + hashlib.sha256(payload).hexdigest() != daemon_id
            ):
                raise RuntimeError("Digest do manifesto exportado diverge do ID do daemon.")
            if json.loads(payload)["config"]["digest"] != digest:
                raise RuntimeError("Manifesto do daemon não referencia o config OCI esperado.")
        return {
            "daemon_image_id": daemon_id,
            "config_digest": digest,
            "diff_ids": actual_layers,
            "export_archive_sha256": file_digest(path),
        }


def convert_to_docker(oci_path: Path, docker_path: Path, tag: str, sentinel: bytes) -> dict:
    "Mantém config e camadas idênticos ao OCI para o load no Docker."
    with OCIArchive(oci_path) as image, tarfile.open(docker_path, "w") as output:
        if sentinel in image.config_bytes:
            raise RuntimeError("Sentinela encontrada no config/histórico da imagem.")
        config_name = image.manifest["config"]["digest"].split(":", 1)[1] + ".json"
        add_bytes(output, config_name, image.config_bytes)
        diff_ids = image.config["rootfs"]["diff_ids"]
        if len(diff_ids) != len(image.manifest["layers"]):
            raise RuntimeError("Quantidade de camadas diverge dos diff_ids.")
        layers = []
        for index, descriptor in enumerate(image.manifest["layers"]):
            # A single scratch file bounds RAM use; no archive path is extracted.
            with tempfile.TemporaryFile(dir=docker_path.parent) as layer:
                digest = image.copy_layer(descriptor, layer, sentinel)
                if digest != diff_ids[index]:
                    raise RuntimeError("Camada descompactada diverge do diff_id no config.")
                name = digest.split(":", 1)[1] + "/layer.tar"
                info = tarfile.TarInfo(name)
                info.size = layer.tell()
                info.mode = 0o644
                layer.seek(0)
                output.addfile(info, layer)
                layers.append(name)
        add_bytes(
            output,
            "manifest.json",
            json.dumps([{"Config": config_name, "RepoTags": [tag], "Layers": layers}]).encode(),
        )
        return {
            "config_digest": image.manifest["config"]["digest"],
            "diff_ids": diff_ids,
            "platform_manifest_digest": image.descriptor["digest"],
        }


def inspect_oci(oci_path: Path, sentinel: bytes, requirements: Path, output_dir: Path) -> dict:
    if len(sentinel) < 24:
        raise RuntimeError("A sentinela deve conter pelo menos 24 bytes.")
    with OCIArchive(oci_path) as image:
        if sentinel in image.index_bytes or sentinel in image.config_bytes:
            raise RuntimeError("Sentinela encontrada no índice/config/histórico.")
        statements = image.attestations(sentinel)
        sboms = [s for s in statements if s.get("predicateType") == "https://spdx.dev/Document"]
        provenance = [s for s in statements if "slsa.dev/provenance" in s.get("predicateType", "")]
        if not sboms or not provenance:
            raise RuntimeError("OCI sem SBOM SPDX ou provenance vinculados à imagem")
        packages = {}
        for statement in sboms:
            for package in statement["predicate"].get("packages", []):
                name = normalize_package(package["name"])
                packages.setdefault(name, set()).add(package.get("versionInfo", ""))
        expected = locked_packages(requirements)
        absent = {
            name: version
            for name, version in expected.items()
            if version not in packages.get(name, set())
        }
        if absent:
            raise RuntimeError(f"Pacotes do lock ausentes no SBOM: {absent}")
        materials = []
        for statement in provenance:
            predicate = statement["predicate"]
            materials.extend(predicate.get("materials", []))
            materials.extend(predicate.get("buildDefinition", {}).get("resolvedDependencies", []))
        if not materials or not any(
            "python" in item.get("uri", "") and item.get("digest") for item in materials
        ):
            raise RuntimeError("Provenance sem base Python com digest")
        layer_checks = []
        diff_ids = image.config["rootfs"]["diff_ids"]
        if len(diff_ids) != len(image.manifest["layers"]):
            raise RuntimeError("Quantidade de camadas/config inconsistente.")
        for index, descriptor in enumerate(image.manifest["layers"]):
            with tempfile.TemporaryFile(dir=output_dir) as discarded:
                digest = image.copy_layer(descriptor, discarded, sentinel)
            if digest != diff_ids[index]:
                raise RuntimeError("diff_id da camada não corresponde ao config.")
            layer_checks.append({"blob_digest": descriptor["digest"], "diff_id": digest})
        for index, statement in enumerate(sboms):
            (output_dir / f"sbom-{index}.json").write_text(
                json.dumps(statement, indent=2), encoding="utf-8"
            )
        for index, statement in enumerate(provenance):
            (output_dir / f"provenance-{index}.json").write_text(
                json.dumps(statement, indent=2), encoding="utf-8"
            )
        return {
            "index_json_digest": "sha256:" + hashlib.sha256(image.index_bytes).hexdigest(),
            "platform_manifest_digest": image.descriptor["digest"],
            "config_digest": image.manifest["config"]["digest"],
            "diff_ids": diff_ids,
            "layers": layer_checks,
            "packages_total": len(packages),
            "locked_packages_verified": expected,
            "materials": materials,
            "attestation_subjects_verified": True,
            "sbom_documents": len(sboms),
            "provenance_documents": len(provenance),
            "sentinel_absent_layers_config_history_attestations": True,
            "image_user": image.config.get("config", {}).get("User"),
        }
