"""Private, bounded transport of logical backups; importing never restores or loads images."""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 1
MAX_JSON_BYTES = 8 * 1024**2
MAX_DUMP_BYTES = 1024**3
MAX_IMAGE_BYTES = 2 * 1024**3
MAX_LAYER_BYTES = 4 * 1024**3
MAX_ARCHIVE_FILES = 10000
CHUNK_BYTES = 1024**2
FILES = {"metadata.json", "containerops.dump", "manifest.json", "application-image.tar"}
LIMITS = {
    "metadata.json": MAX_JSON_BYTES,
    "manifest.json": MAX_JSON_BYTES,
    "containerops.dump": MAX_DUMP_BYTES,
    "application-image.tar": MAX_IMAGE_BYTES,
}
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
PROJECT = re.compile(
    r"pf-containerops(?:-(?:test|restore)-[a-f0-9]{8}|-session-[a-z0-9][a-z0-9-]{0,31})?\Z"
)
RELEASES = ("1.0.0", "2.0.0")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "JSON contém chave duplicada")
        value[key] = item
    return value


def decode_json(content):
    require(len(content) <= MAX_JSON_BYTES, "JSON acima do limite de 8 MiB")
    try:
        return json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_object,
            parse_constant=lambda _: require(False, "JSON contém número não finito"),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise RuntimeError("JSON malformado") from error


def _keys(value, required, optional=()):
    require(type(value) is dict, "Objeto JSON obrigatório")
    require(set(required) <= set(value), "JSON incompleto")
    require(
        set(value) <= set(required) | set(optional), "Campo JSON desconhecido ou caminho externo"
    )


def _text(value, description):
    require(type(value) is str and 0 < len(value) <= 512, f"{description} inválido")
    require(not any(ord(character) < 32 for character in value), f"{description} inválido")
    return value


def _date(value):
    _text(value, "Data")
    require(
        bool(
            re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
            )
        ),
        "Data ISO 8601 com timezone obrigatória",
    )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("Data inválida") from error
    require(parsed.utcoffset().total_seconds() == 0, "Data deve usar UTC")
    require(parsed <= datetime.now(UTC), "Data do backup está no futuro")
    return parsed


def _cutoff(value, created_at):
    _keys(value, ("started_at", "completed_at", "method"))
    start, end = _date(value["started_at"]), _date(value["completed_at"])
    require(start <= end <= _date(created_at), "Janela de corte inválida")
    _text(value["method"], "Método de corte")


def _source(value):
    _keys(value, ("revision", "sha256"), ("files", "source_diff_sha256", "source_status_sha256"))
    require(
        type(value["revision"]) is str
        and bool(re.fullmatch(r"(?:[a-f0-9]{40}(?:-dirty)?|uncommitted)", value["revision"])),
        "Revisão de fontes inválida",
    )
    require(
        type(value["sha256"]) is str and bool(DIGEST.fullmatch(value["sha256"])),
        "Hash de fontes inválido",
    )
    for key in ("source_diff_sha256", "source_status_sha256"):
        if key in value:
            require(
                type(value[key]) is str and bool(DIGEST.fullmatch(value[key])),
                "Hash de fontes inválido",
            )
    if "files" in value:
        require(type(value["files"]) is dict, "Referência de fontes inválida")
        for name, digest in value["files"].items():
            require(type(name) is str, "Caminho de fonte inválido")
            path = PurePosixPath(name)
            require(
                type(name) is str
                and not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in name
                and ":" not in name,
                "Caminho de fonte inválido",
            )
            require(
                type(digest) is str and bool(DIGEST.fullmatch(digest)), "Hash de fonte inválido"
            )
        actual = hashlib.sha256(json.dumps(value["files"], sort_keys=True).encode()).hexdigest()
        require(actual == value["sha256"], "Referência de fontes diverge dos hashes")


def validate_metadata(metadata, *, portable=False):
    required = (
        "created_at",
        "sha256",
        "server",
        "schema_version",
        "snapshot",
        "source_project",
        "image",
    )
    optional = (
        "cutoff_interval",
        "elapsed_seconds",
        "tool",
        "source_context",
        "source_ref",
        "image_release",
        "config_digest",
        "oci_manifest_digest",
    )
    _keys(metadata, required, optional)
    _date(metadata["created_at"])
    require(
        type(metadata["sha256"]) is str and bool(DIGEST.fullmatch(metadata["sha256"])),
        "Checksum de backup inválido",
    )
    require(
        type(metadata["schema_version"]) is int and metadata["schema_version"] in (1, 2),
        "Schema futuro ou incompatível",
    )
    require(
        type(metadata["source_project"]) is str
        and bool(PROJECT.fullmatch(metadata["source_project"])),
        "Origem do backup inválida",
    )
    require(
        type(metadata["image"]) is str and bool(IMAGE_ID.fullmatch(metadata["image"])),
        "Backup precisa de ImageID imutável",
    )
    require(
        type(metadata["server"]) is str
        and bool(re.fullmatch(r"postgres \(PostgreSQL\) 17(?:\.\d+){0,2}", metadata["server"])),
        "PostgreSQL incompatível; exige versão 17",
    )
    if "tool" in metadata:
        require(
            type(metadata["tool"]) is str
            and bool(re.fullmatch(r"pg_dump \(PostgreSQL\) 17(?:\.\d+){0,2}", metadata["tool"])),
            "Ferramenta pg_dump incompatível; exige versão 17",
        )
    if portable:
        require("cutoff_interval" in metadata, "Pacote precisa de janela de corte")
    if "cutoff_interval" in metadata:
        _cutoff(metadata["cutoff_interval"], metadata["created_at"])
    snapshot = metadata["snapshot"]
    require(
        type(snapshot) is dict
        and snapshot.get("schema_version") == metadata["schema_version"]
        and type(snapshot.get("schema_version")) is int,
        "Schema do snapshot divergente",
    )
    _keys(snapshot, ("schema_version", "jobs"), ("counts", "admission_paused"))
    require(type(snapshot.get("jobs")) is list, "Snapshot de jobs inválido")
    ids = set()
    for job in snapshot["jobs"]:
        _keys(job, ("id", "owner", "state", "attempts", "word_count", "checksum", "error_category"))
        require(
            type(job["id"]) is str
            and bool(re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", job["id"]))
            and job["id"] not in ids,
            "ID de job inválido ou duplicado",
        )
        ids.add(job["id"])
        _text(job["owner"], "Proprietário do job")
        require(
            type(job["state"]) is str
            and job["state"] in ("succeeded", "failed")
            and type(job["attempts"]) is int
            and 0 <= job["attempts"] <= 3,
            "Estado/tentativas do job inválidos",
        )
        require(
            job["word_count"] is None or type(job["word_count"]) is int and job["word_count"] >= 0,
            "Contagem de palavras inválida",
        )
        require(
            job["checksum"] is None
            or type(job["checksum"]) is str
            and bool(DIGEST.fullmatch(job["checksum"])),
            "Checksum de job inválido",
        )
        require(
            job["error_category"] is None
            or type(job["error_category"]) is str
            and bool(re.fullmatch(r"[a-z0-9_]{1,64}", job["error_category"])),
            "Categoria de erro de job inválida",
        )
        if job["state"] == "succeeded":
            require(
                job["word_count"] is not None
                and job["checksum"] is not None
                and job["error_category"] is None,
                "Resultado do job incompleto",
            )
    if "admission_paused" in snapshot:
        require(type(snapshot["admission_paused"]) is bool, "Pausa do snapshot inválida")
    if "counts" in snapshot:
        _keys(snapshot["counts"], ("queued", "running", "succeeded", "failed"))
        require(
            all(type(count) is int and count >= 0 for count in snapshot["counts"].values()),
            "Contagens do snapshot inválidas",
        )
        require(
            snapshot["counts"]["queued"] == snapshot["counts"]["running"] == 0
            and sum(snapshot["counts"].values()) == len(snapshot["jobs"]),
            "Snapshot não está drenado",
        )
    if "elapsed_seconds" in metadata:
        elapsed = metadata["elapsed_seconds"]
        require(
            type(elapsed) in (float, int) and math.isfinite(elapsed) and elapsed >= 0,
            "Duração inválida",
        )
    if "source_context" in metadata:
        require(
            type(metadata["source_context"]) is str
            and bool(SLUG.fullmatch(metadata["source_context"])),
            "Contexto de origem inválido",
        )
    if "source_ref" in metadata:
        _source(metadata["source_ref"])
    if "image_release" in metadata:
        require(
            type(metadata["image_release"]) is str and metadata["image_release"] in RELEASES,
            "Release de imagem incompatível",
        )
    for key in ("config_digest", "oci_manifest_digest"):
        if key in metadata:
            require(
                type(metadata[key]) is str and bool(IMAGE_ID.fullmatch(metadata[key])),
                "Identidade de imagem inválida",
            )
    return metadata


def _fingerprint(item):
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


def safe_path(path, *, directory=False):
    """Reject every existing ancestor before resolve can hide a junction or symlink."""
    path = Path(os.path.abspath(path))
    for ancestor in (*reversed(path.parents), path):
        if not ancestor.exists() and not ancestor.is_symlink():
            continue
        item = ancestor.lstat()
        require(
            not stat.S_ISLNK(item.st_mode)
            and not getattr(item, "st_file_attributes", 0) & 0x400
            and not getattr(item, "st_reparse_tag", 0),
            "Links/reparse points não são aceitos",
        )
    if directory:
        require(path.is_dir(), "Diretório de backup ausente")
    return path


def _regular(path):
    safe_path(path)
    item = path.lstat()
    require(
        stat.S_ISREG(item.st_mode) and item.st_nlink == 1,
        "Backup precisa de arquivos regulares sem links",
    )
    return item


def file_record(path, *, output=None):
    path = Path(path)
    before = _regular(path)
    limit = LIMITS.get(path.name, MAX_IMAGE_BYTES)
    require(0 < before.st_size <= limit, "Arquivo vazio ou acima do limite de bytes")
    digest, size = hashlib.sha256(), 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        require(
            _fingerprint(os.fstat(stream.fileno())) == _fingerprint(before),
            "Arquivo mudou antes da leitura",
        )
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            require(size <= limit, "Arquivo acima do limite de bytes")
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        require(
            _fingerprint(os.fstat(stream.fileno())) == _fingerprint(before),
            "Arquivo mudou durante a cópia",
        )
    require(
        _fingerprint(_regular(path)) == _fingerprint(before) and size == before.st_size,
        "Arquivo mudou durante a cópia",
    )
    return {"size": size, "sha256": digest.hexdigest()}


def _read_json(path):
    require(_regular(path).st_size <= MAX_JSON_BYTES, "JSON acima do limite de 8 MiB")
    buffer = io.BytesIO()
    file_record(path, output=buffer)
    return decode_json(buffer.getvalue())


def read_json(path):
    return _read_json(Path(path))


def file_hash(path):
    return file_record(Path(path))["sha256"]


def _file_set(directory, *, portable):
    safe_path(directory, directory=True)
    names = {path.name for path in directory.iterdir()}
    require(
        names <= FILES and {"metadata.json", "containerops.dump"} <= names,
        "Backup incompleto ou contém itens extras",
    )
    if portable:
        require("manifest.json" in names, "Pacote incompleto: manifesto ausente")
    for name in names:
        _regular(directory / name)
    return names


def validate_manifest(manifest, metadata, files):
    _keys(
        manifest,
        ("format", "format_version", "exported_at", "files", "backup", "application_image"),
    )
    require(
        manifest["format"] == "containerops-backup"
        and type(manifest["format_version"]) is int
        and manifest["format_version"] == FORMAT_VERSION,
        "Formato de pacote futuro ou incompatível",
    )
    _date(manifest["exported_at"])
    require(
        type(manifest["files"]) is dict
        and set(manifest["files"]) == set(files) - {"manifest.json"},
        "Conjunto de arquivos do manifesto divergente",
    )
    for name, record in manifest["files"].items():
        _keys(record, ("size", "sha256"))
        require(
            type(record["size"]) is int
            and 0 < record["size"] <= LIMITS[name]
            and type(record["sha256"]) is str
            and bool(DIGEST.fullmatch(record["sha256"])),
            "Tamanho/hash de arquivo inválido",
        )
        require(record == files[name], "Checksum/tamanho do pacote divergente")
    backup = manifest["backup"]
    _keys(
        backup,
        ("created_at", "cutoff_interval", "schema_version", "postgresql", "origin", "source"),
    )
    require(
        backup["created_at"] == metadata["created_at"]
        and backup["cutoff_interval"] == metadata["cutoff_interval"]
        and type(backup["schema_version"]) is int
        and backup["schema_version"] == metadata["schema_version"],
        "Manifesto diverge dos metadados",
    )
    _keys(backup["postgresql"], ("server", "tool"))
    require(
        backup["postgresql"]["server"] == metadata["server"]
        and backup["postgresql"]["tool"] == metadata.get("tool"),
        "Ferramenta PostgreSQL não conferida",
    )
    require(backup["postgresql"]["tool"] is not None, "Registre a versão pg_dump antes de exportar")
    _keys(backup["origin"], ("source_project", "context"))
    require(
        backup["origin"]["source_project"] == metadata["source_project"]
        and type(backup["origin"]["context"]) is str
        and bool(SLUG.fullmatch(backup["origin"]["context"])),
        "Origem do manifesto divergente",
    )
    if "source_context" in metadata:
        require(
            backup["origin"]["context"] == metadata["source_context"],
            "Contexto de origem divergente",
        )
    _source(backup["source"])
    if "source_ref" in metadata:
        require(
            backup["source"]
            == {key: metadata["source_ref"][key] for key in ("revision", "sha256")},
            "Fonte do manifesto divergente",
        )
    image = manifest["application_image"]
    _keys(image, ("image_id", "config_digest", "oci_manifest_digest", "release", "archive"))
    require(
        image["image_id"] == metadata["image"]
        and type(image["config_digest"]) is str
        and bool(IMAGE_ID.fullmatch(image["config_digest"]))
        and image["release"] in RELEASES,
        "Imagem do manifesto inválida",
    )
    require(
        image["oci_manifest_digest"] is None
        or type(image["oci_manifest_digest"]) is str
        and bool(IMAGE_ID.fullmatch(image["oci_manifest_digest"])),
        "Manifesto OCI inválido",
    )
    for key, field in (
        ("image_release", "release"),
        ("config_digest", "config_digest"),
        ("oci_manifest_digest", "oci_manifest_digest"),
    ):
        if key in metadata:
            require(metadata[key] == image[field], "Identidade OCI/config/release divergente")
    require(
        image["archive"] == ("application-image.tar" if "application-image.tar" in files else None),
        "Arquivo da imagem divergente",
    )


def validate_backup(directory, *, portable=False):
    directory = safe_path(directory, directory=True)
    names = _file_set(directory, portable=portable)
    files = {name: file_record(directory / name) for name in sorted(names)}
    metadata = _read_json(directory / "metadata.json")
    require(
        type(metadata) is dict and metadata.get("sha256") == files["containerops.dump"]["sha256"],
        "Checksum de backup inválido",
    )
    validate_metadata(metadata, portable=portable or "manifest.json" in names)
    manifest = _read_json(directory / "manifest.json") if "manifest.json" in names else None
    if manifest is not None:
        validate_manifest(manifest, metadata, files)
        if "application-image.tar" in names:
            inspect_application_archive(
                directory / "application-image.tar", manifest["application_image"]
            )
    require(
        names == _file_set(directory, portable=portable),
        "Conjunto do backup mudou durante a leitura",
    )
    require(
        files == {name: file_record(directory / name) for name in sorted(names)},
        "Backup mudou durante a validação",
    )
    return {"metadata": metadata, "files": files, "manifest": manifest}


def _protect(directory, protect):
    if protect is None:
        from proof import protect_copy_directory

        protect = protect_copy_directory
    if os.name != "nt":
        directory.chmod(0o700)
        for path in directory.iterdir():
            path.chmod(0o600)
    return protect(directory)


def _copy(source, target, expected):
    with target.open("xb") as destination:
        if os.name != "nt":
            target.chmod(0o600)
        copied = file_record(source, output=destination)
        destination.flush()
        os.fsync(destination.fileno())
    require(
        copied == expected and file_record(target) == expected and file_record(source) == expected,
        "Cópia privada diverge do backup",
    )


def _write_json(path, value):
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    require(len(content) <= MAX_JSON_BYTES, "JSON acima do limite")
    with path.open("xb") as output:
        if os.name != "nt":
            path.chmod(0o600)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _finalize(stage, destination):
    safe_path(destination.parent, directory=True)
    require(not destination.exists() and not destination.is_symlink(), "Destino já existe")
    if os.name == "nt":
        os.rename(stage, destination)
    else:
        # os.rename may replace an empty directory on POSIX. Linux provides the
        # exclusive same-filesystem finalization needed by the fixture/CI path.
        library = ctypes.CDLL(None, use_errno=True)
        require(
            hasattr(library, "renameat2"), "Finalização exclusiva indisponível nesta plataforma"
        )
        result = library.renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
        if result:
            raise OSError(ctypes.get_errno(), "Finalização exclusiva do backup falhou")


def export_backup(
    directory,
    destination,
    *,
    source_ref=None,
    source_context="default",
    image_release=None,
    include_image=False,
    run=None,
    protect=None,
    config_digest=None,
    oci_manifest_digest=None,
):
    checked = validate_backup(directory)
    metadata, original = checked["metadata"], checked["files"]
    validate_metadata(metadata, portable=True)
    previous = checked["manifest"]
    source_ref = (
        source_ref
        or metadata.get("source_ref")
        or (previous["backup"]["source"] if previous else None)
    )
    _source(source_ref)
    require(
        type(source_context) is str and bool(SLUG.fullmatch(source_context)), "Contexto inválido"
    )
    image_release = (
        image_release
        or metadata.get("image_release")
        or (previous["application_image"]["release"] if previous else None)
    )
    require(image_release in RELEASES, "Registre a release da imagem antes de exportar")
    config_digest = (
        config_digest
        or metadata.get("config_digest")
        or (previous["application_image"]["config_digest"] if previous else None)
    )
    require(
        type(config_digest) is str and bool(IMAGE_ID.fullmatch(config_digest)),
        "Config digest da imagem não conferido",
    )
    destination = safe_path(destination)
    safe_path(destination.parent, directory=True)
    require(not destination.exists(), "Destino já existe")
    stage = destination.parent / (".containerops-export-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        _protect(stage, protect)
        for name in ("containerops.dump", "metadata.json"):
            _copy(Path(directory) / name, stage / name, original[name])
        image = {
            "image_id": metadata["image"],
            "config_digest": config_digest,
            "oci_manifest_digest": oci_manifest_digest or metadata.get("oci_manifest_digest"),
            "release": image_release,
            "archive": "application-image.tar" if include_image else None,
        }
        if include_image:
            require(run is not None, "Exportação de imagem exige comando Docker explícito")
            preflight_image(metadata, run)
            archive = stage / "application-image.tar"
            run(["docker", "image", "save", "--output", str(archive), metadata["image"]])
            if os.name != "nt":
                archive.chmod(0o600)
            file_record(archive)
            inspect_application_archive(archive, image)
        files = {path.name: file_record(path) for path in stage.iterdir()}
        manifest = {
            "format": "containerops-backup",
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "files": files,
            "backup": {
                "created_at": metadata["created_at"],
                "cutoff_interval": metadata["cutoff_interval"],
                "schema_version": metadata["schema_version"],
                "postgresql": {"server": metadata["server"], "tool": metadata.get("tool")},
                "origin": {"source_project": metadata["source_project"], "context": source_context},
                "source": {key: source_ref[key] for key in ("revision", "sha256")},
            },
            "application_image": image,
        }
        _write_json(stage / "manifest.json", manifest)
        protection = _protect(stage, protect)
        validated = validate_backup(stage, portable=True)
        require(
            validate_backup(directory)["files"] == original,
            "Backup original mudou durante a exportação",
        )
        _finalize(stage, destination)
        return {
            "directory": destination,
            "manifest": manifest,
            "protection": protection,
            "files": validated["files"],
        }
    finally:
        if stage.exists():
            safe_path(stage, directory=True)
            shutil.rmtree(stage)


def import_backup(source, backups_root, backup_id, *, protect=None):
    require(type(backup_id) is str and bool(SLUG.fullmatch(backup_id)), "ID de backup inválido")
    checked = validate_backup(source, portable=True)
    source = safe_path(source, directory=True)
    backups_root = safe_path(backups_root, directory=True)
    destination = backups_root / backup_id
    require(not destination.exists(), "ID de backup já existe")
    stage = backups_root / (".containerops-import-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        _protect(stage, protect)
        for name, expected in checked["files"].items():
            _copy(source / name, stage / name, expected)
        protection = _protect(stage, protect)
        copied = validate_backup(stage, portable=True)
        require(
            copied["files"] == checked["files"]
            and validate_backup(source, portable=True)["files"] == checked["files"],
            "Pacote mudou durante a importação",
        )
        _finalize(stage, destination)
        return destination, {
            "id": backup_id,
            "imported": True,
            "protected": True,
            "protection": protection,
            "origin": copied["manifest"]["backup"]["origin"],
            "files": copied["files"],
            "manifest_sha256": copied["files"]["manifest.json"]["sha256"],
        }
    finally:
        if stage.exists():
            safe_path(stage, directory=True)
            shutil.rmtree(stage)


def preflight_image(metadata, run, *, identity=None):
    """Inspect the immutable identity only; missing images are never rebuilt or loaded."""
    validate_metadata(metadata)
    expected = identity or {}
    config_digest = expected.get("config_digest", metadata.get("config_digest"))
    result = run(["docker", "image", "inspect", metadata["image"]], capture=True, check=False)
    if result.returncode != 0 and config_digest and config_digest != metadata["image"]:
        result = run(["docker", "image", "inspect", config_digest], capture=True, check=False)
    require(
        result.returncode == 0,
        "Imagem ausente: obtenha a aplicação por ImageID; se o pacote confiável inclui imagem, execute backup-image-load --trusted-image-source antes de restore-test",
    )
    items = decode_json(result.stdout.encode())
    require(type(items) is list and len(items) == 1, "Inspeção da imagem inválida")
    image = items[0]
    require(
        type(image) is dict and type(image.get("Config")) is dict, "Inspeção da imagem inválida"
    )
    labels = image["Config"].get("Labels") or {}
    require(type(labels) is dict, "Labels da imagem inválidos")
    release = labels.get("org.opencontainers.image.version")
    require(
        image.get("Id") in (metadata["image"], config_digest)
        and image.get("Os") == "linux"
        and image.get("Architecture") == "amd64"
        and labels.get("org.opencontainers.image.title") == "ContainerOps"
        and labels.get("org.opencontainers.image.source")
        == "https://github.com/arthurjoanes/containerops"
        and release in RELEASES
        and release == expected.get("release", metadata.get("image_release", release)),
        "Imagem/release/plataforma incompatível com o backup",
    )
    return image["Id"]


def inspect_application_archive(path, identity):
    """Never extract untrusted tar members or transport a daemon's shared tags."""
    file_record(path)
    with tarfile.open(path, "r:") as archive:
        members = {}
        total = 0
        for member in archive:
            name = member.name.rstrip("/")
            parts = PurePosixPath(name).parts
            require(
                name not in members
                and not name.startswith("/")
                and ".." not in parts
                and "\\" not in name
                and ":" not in name,
                "Archive contém caminho ou membro duplicado",
            )
            require(member.isfile() or member.isdir(), "Archive contém link ou arquivo não regular")
            members[name] = member
            total += member.size
            require(
                len(members) <= MAX_ARCHIVE_FILES and total <= MAX_IMAGE_BYTES,
                "Archive acima dos limites",
            )

        def read(name):
            require(
                type(name) is str
                and name in members
                and members[name].isfile()
                and members[name].size <= MAX_JSON_BYTES,
                "Config/manifesto do archive inválido",
            )
            with archive.extractfile(members[name]) as source:
                return source.read(MAX_JSON_BYTES + 1)

        manifests = decode_json(read("manifest.json"))
        require(type(manifests) is list and len(manifests) == 1, "Archive exige uma única imagem")
        manifest = manifests[0]
        require(
            type(manifest) is dict and not manifest.get("RepoTags"),
            "Archive transporta tags compartilhadas; exporte pelo ImageID",
        )

        def check_annotations(value):
            annotations = value.get("annotations", {})
            require(type(annotations) is dict, "Annotations OCI inválidas")
            for key in ("org.opencontainers.image.ref.name", "io.containerd.image.name"):
                tag = annotations.get(key)
                require(
                    tag is None or tag == identity["image_id"],
                    "Archive OCI transporta tag compartilhada",
                )

        check_annotations(manifest)
        if "repositories" in members:
            require(
                decode_json(read("repositories")) == {}, "Archive transporta tags de repositórios"
            )
        content = read(manifest.get("Config"))
        config_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        require(config_digest == identity["config_digest"], "Config digest do archive divergente")
        config = decode_json(content)
        require(
            type(config) is dict
            and type(config.get("config")) is dict
            and type(config.get("rootfs")) is dict,
            "Config da imagem inválido",
        )
        labels = config["config"].get("Labels") or {}
        require(type(labels) is dict, "Labels do archive inválidos")
        require(
            config.get("os") == "linux"
            and config.get("architecture") == "amd64"
            and labels.get("org.opencontainers.image.title") == "ContainerOps"
            and labels.get("org.opencontainers.image.source")
            == "https://github.com/arthurjoanes/containerops"
            and labels.get("org.opencontainers.image.version") == identity["release"],
            "Release/config do archive incompatível",
        )
        layers, diff_ids = manifest.get("Layers"), config.get("rootfs", {}).get("diff_ids")
        require(
            type(layers) is list and type(diff_ids) is list and len(layers) == len(diff_ids),
            "Camadas do archive inválidas",
        )
        expanded = 0
        for name, expected in zip(layers, diff_ids, strict=True):
            require(
                type(name) is str
                and name in members
                and members[name].isfile()
                and type(expected) is str
                and bool(IMAGE_ID.fullmatch(expected)),
                "Camada do archive inválida",
            )
            digest = hashlib.sha256()
            with archive.extractfile(members[name]) as stream:
                compressed = stream.read(2) == b"\x1f\x8b"
                stream.seek(0)
                reader = gzip.GzipFile(fileobj=stream) if compressed else stream
                try:
                    while chunk := reader.read(CHUNK_BYTES):
                        expanded += len(chunk)
                        require(expanded <= MAX_LAYER_BYTES, "Camadas expandidas acima do limite")
                        digest.update(chunk)
                finally:
                    if compressed:
                        reader.close()
            require("sha256:" + digest.hexdigest() == expected, "Conteúdo da camada divergente")
        if "index.json" in members:
            index = decode_json(read("index.json"))
            require(type(index) is dict, "Índice OCI inválido")
            check_annotations(index)
            descriptors = index.get("manifests", [])
            require(
                type(descriptors) is list and len(descriptors) == 1,
                "Índice OCI precisa de uma única imagem",
            )
            for descriptor in descriptors:
                require(
                    type(descriptor) is dict and type(descriptor.get("annotations", {})) is dict,
                    "Descriptor OCI inválido",
                )
                check_annotations(descriptor)
            descriptor = descriptors[0]
            require(
                descriptor.get("digest") == identity["image_id"], "ImageID OCI ausente do archive"
            )
            payload = read("blobs/sha256/" + identity["image_id"].split(":")[1])
            require(
                type(descriptor.get("size")) is int
                and len(payload) == descriptor["size"]
                and "sha256:" + hashlib.sha256(payload).hexdigest() == identity["image_id"],
                "Identidade do manifesto OCI divergente",
            )
            oci = decode_json(payload)
            require(type(oci) is dict and type(oci.get("config")) is dict, "Manifesto OCI inválido")
            check_annotations(oci)
            require(
                oci.get("config", {}).get("digest") == config_digest,
                "Manifesto OCI referencia outro config",
            )
        else:
            require(
                identity["image_id"] == config_digest,
                "ImageID/config precisam de manifesto OCI para distinção",
            )
    return {
        "image_id": identity["image_id"],
        "config_digest": config_digest,
        "release": identity["release"],
        "shared_tags": False,
    }


def load_application_image(directory, *, run, tag, trusted=False, protect=None):
    require(
        trusted is True, "Carga exige --trusted-image-source: aceite somente artefatos confiáveis"
    )
    require(
        type(tag) is str
        and bool(
            re.fullmatch(r"containerops-[a-z0-9][a-z0-9-]{0,79}-app:(?:1\.0\.0|2\.0\.0)", tag)
        ),
        "Carga exige tag exclusiva da sessão",
    )
    checked = validate_backup(directory, portable=True)
    identity = checked["manifest"]["application_image"]
    require(
        identity["archive"] == "application-image.tar",
        "Pacote sem imagem: exporte com --include-image",
    )
    require(tag.endswith(":" + identity["release"]), "Tag não corresponde à release")
    existing = run(["docker", "image", "inspect", tag], capture=True, check=False)
    if existing.returncode == 0:
        items = decode_json(existing.stdout.encode())
        require(
            type(items) is list
            and len(items) == 1
            and type(items[0]) is dict
            and items[0].get("Id") in (identity["image_id"], identity["config_digest"]),
            "Tag da sessão já aponta para outro conteúdo",
        )
    directory = safe_path(directory, directory=True)
    source = directory / "application-image.tar"
    expected = checked["files"]["application-image.tar"]
    stage = directory.parent / (".containerops-load-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        _protect(stage, protect)
        archive = stage / "application-image.tar"
        _copy(source, archive, expected)
        _protect(stage, protect)
        inspect_application_archive(archive, identity)
        run(["docker", "load", "--input", str(archive)])
        require(
            file_record(archive) == expected and file_record(source) == expected,
            "Imagem mudou durante a carga",
        )
        selected = preflight_image(checked["metadata"], run, identity=identity)
        current = run(["docker", "image", "inspect", tag], capture=True, check=False)
        if current.returncode == 0:
            items = decode_json(current.stdout.encode())
            require(
                type(items) is list
                and len(items) == 1
                and type(items[0]) is dict
                and items[0].get("Id") == selected,
                "Tag mudou durante a carga",
            )
        run(["docker", "tag", selected, tag])
        return {
            "image_id": selected,
            "source_image_id": identity["image_id"],
            "config_digest": identity["config_digest"],
            "release": identity["release"],
            "tag": tag,
            "trusted_source_acknowledged": True,
        }
    finally:
        if stage.exists():
            safe_path(stage, directory=True)
            shutil.rmtree(stage)
