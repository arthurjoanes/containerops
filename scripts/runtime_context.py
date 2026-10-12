"""Define os caminhos e os nomes dos recursos de cada sessão."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"{kind}{n}" for kind in ("com", "lpt") for n in range(10)),
}


def valid_session_slug(value: object) -> bool:
    return isinstance(value, str) and bool(_SLUG.fullmatch(value)) and value not in _RESERVED


def ensure_no_links(path: Path) -> Path:
    """Recusa links e reparse points no caminho e nos diretórios acima dele."""
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise RuntimeError("Caminho de runtime não aceita traversal")
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_reparse_tag", 0)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RuntimeError("Runtime não aceita links ou junções")
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Runtime precisa de diretórios regulares")
    resolved = path.resolve()
    if resolved != path:
        raise RuntimeError("Caminho resolvido fora do runtime solicitado")
    return resolved


def ensure_file_path(path: Path) -> Path:
    """Valida o arquivo e seus diretórios; recusa hardlinks existentes."""
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise RuntimeError("Caminho de arquivo não aceita traversal")
    ensure_no_links(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_reparse_tag", 0)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise RuntimeError("Arquivo precisa ser regular, sem links, junções ou hardlinks")
    if path.resolve() != path:
        raise RuntimeError("Arquivo resolvido fora do caminho solicitado")
    return path


def ensure_tree(path: Path) -> Path:
    """Valida os arquivos e diretórios existentes antes de montar o caminho."""
    directory = ensure_no_links(path)
    pending = [directory] if directory.exists() else []
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            info = child.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(ensure_no_links(child))
            else:
                ensure_file_path(child)
    return directory


@dataclass(frozen=True)
class RuntimeContext:
    root: Path
    base: Path
    runtime: Path
    session: str | None = None

    @property
    def main(self) -> str:
        return "pf-containerops-session-" + self.session if self.session else "pf-containerops"

    @property
    def builder(self) -> str:
        return (
            "pf-containerops-builder-" + self.session if self.session else "pf-containerops-builder"
        )

    @property
    def evidence(self) -> Path:
        return (self.runtime if self.session else self.root) / "docs" / "evidence"

    @property
    def report(self) -> Path:
        return (self.runtime if self.session else self.root) / "docs" / "report.html"

    def image(self, kind: str, version: str | None = None) -> str:
        if kind not in {"app", "test", "database", "proxy", "cache-experiment"}:
            raise ValueError("Componente de imagem ContainerOps desconhecido")
        version = version or ("1.0.0" if kind == "app" else "local")
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", version):
            raise ValueError("Tag de imagem inválida")
        namespace = f"containerops-{self.session}-" if self.session else "containerops-"
        return namespace + kind + ":" + version


def resolve_context(
    root: Path = ROOT,
    *,
    environment: Mapping[str, str] | None = None,
    windows: bool | None = None,
) -> RuntimeContext:
    """No Windows, as sessões ficam dentro do runtime reservado."""
    environment = os.environ if environment is None else environment
    windows = os.name == "nt" if windows is None else windows
    root = Path(root).resolve()
    base = (
        Path(environment["USERPROFILE"]) / "AppData" / "Local" / "ContainerOps-runtime"
        if windows
        else root / ".runtime-containerops"
    )
    requested = Path(environment.get("CONTAINEROPS_RUNTIME", str(base)))
    session = environment.get("CONTAINEROPS_SESSION")
    if session is not None and not valid_session_slug(session):
        raise RuntimeError(
            "CONTAINEROPS_SESSION deve ser basename de 1 a 32 letras minúsculas/dígitos/hífens"
        )
    if not windows:
        # A sessão deve ficar dentro do runtime informado pelo CI.
        if session and requested.name == session and requested.parent.name == "sessions":
            base = requested.parent.parent
        else:
            base = requested
        if session and "containerops" not in base.name.lower():
            raise RuntimeError("Runtime CI precisa de nome exclusivo com containerops")
    base = ensure_no_links(base)
    expected = base / "sessions" / session if session else base
    requested = ensure_no_links(requested)
    if windows and requested not in (base, expected):
        raise RuntimeError(f"Runtime Windows deve ser o caminho reservado: {expected}")
    if session and requested not in (base, expected):
        raise RuntimeError("Runtime da sessão fora do subdiretório reservado")
    runtime = ensure_no_links(expected)
    return RuntimeContext(root, base, runtime, session)


def active_context(root: Path = ROOT) -> RuntimeContext:
    return resolve_context(root)


def validate_runtime(root: Path, runtime: Path) -> Path:
    """Confere se o runtime de build pertence à sessão ativa."""
    context = active_context(root)
    runtime = ensure_no_links(runtime)
    if context.session or os.name == "nt":
        if runtime != context.runtime:
            raise RuntimeError(f"Runtime deve corresponder ao contexto ativo: {context.runtime}")
    elif "containerops" not in runtime.name.lower():
        raise RuntimeError("Runtime CI precisa de nome exclusivo com containerops")
    return runtime
