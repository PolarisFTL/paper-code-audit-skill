#!/usr/bin/env python3
"""Prepare read-only paper/code evidence; never install or execute project code.

Python 3.10+ and the standard library suffice. An already-installed pdftotext
or pypdf may optionally provide PDF page text. Archive limits apply before any
member is written, including members in normally excluded directories.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from typing import Any, BinaryIO


EXCLUDED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})
MIB = 1024 * 1024
CHUNK = MIB
SCHEMA_VERSION = "1.0"


class InputError(Exception):
    """An input cannot be prepared safely."""


def within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def reparse_or_link(path: Path, info: os.stat_result | None = None) -> bool:
    info = info if info is not None else path.lstat()
    # Includes Windows junctions and other reparse points on Python 3.10.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def open_regular(path: Path) -> BinaryIO:
    before = path.lstat()
    if reparse_or_link(path, before) or not stat.S_ISREG(before.st_mode):
        raise InputError(f"Not a regular, non-linked file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise InputError(f"File changed while opening: {path}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        while block := stream.read(CHUNK):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InputError(f"File changed while hashing: {path}")
    return digest.hexdigest()


def archive_kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        return "tar"
    raise InputError("Unsupported code archive. Use a directory, .zip, .tar, .tar.gz, .tgz, .tar.bz2, or .tar.xz.")


def member_path(name: str, is_directory: bool) -> str:
    if not name or "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise InputError(f"Unsafe archive path: {name!r}")
    parts = name.split("/")
    if ".." in parts:
        raise InputError(f"Archive path traversal: {name!r}")
    parts = [part for part in parts if part not in ("", ".")]
    if not parts:
        if is_directory:
            return ""  # A conventional './' tar root entry has no destination.
        raise InputError(f"Empty archive file path: {name!r}")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}
    for part in parts:
        if ":" in part or part.endswith((".", " ")) or part.split(".")[0].upper() in reserved:
            raise InputError(f"Nonportable or unsafe archive path: {name!r}")
    return "/".join(parts)


class PathRegistry:
    """Reject duplicates, case collisions, and file/directory ancestor overlap."""

    def __init__(self) -> None:
        self.explicit: set[str] = set()
        self.nodes: dict[str, tuple[str, bool]] = {}

    def add(self, path: str, is_directory: bool) -> None:
        key = path.casefold()
        if key in self.explicit:
            raise InputError(f"Duplicate or case-colliding archive path: {path!r}")
        self.explicit.add(key)
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            prefix_key = prefix.casefold()
            directory = index < len(parts) or is_directory
            if prefix_key in self.nodes:
                old_path, old_directory = self.nodes[prefix_key]
                if old_path != prefix:
                    raise InputError(f"Case-colliding archive paths: {old_path!r}, {prefix!r}")
                if not old_directory or not directory:
                    raise InputError(f"Archive file/directory path conflict: {prefix!r}")
            else:
                self.nodes[prefix_key] = (prefix, directory)


@dataclass
class Member:
    path: str
    directory: bool
    size: int
    info: Any


def preflight(archive: Any, kind: str, args: argparse.Namespace) -> list[Member]:
    registry = PathRegistry()
    members: list[Member] = []
    total = 0
    raw_members = archive.infolist() if kind == "zip" else archive
    for count, info in enumerate(raw_members, 1):
        if count > args.max_files:
            raise InputError(f"Archive exceeds --max-files ({args.max_files}) entries, including directories.")
        if kind == "zip":
            directory = info.is_dir()
            mode = stat.S_IFMT(info.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise InputError(f"Archive links and special files are forbidden: {info.filename!r}")
            if mode == stat.S_IFDIR and not directory:
                raise InputError(f"Inconsistent ZIP directory metadata: {info.filename!r}")
            if info.flag_bits & 1:
                raise InputError(f"Encrypted ZIP entries are unsupported: {info.filename!r}")
            # On Windows ZipInfo.filename normalizes backslashes before callers
            # see it; orig_filename preserves the actual archive path to audit.
            size, raw_name = info.file_size, info.orig_filename
        else:
            directory = info.isdir()
            if not directory and not info.isreg():
                raise InputError(f"Archive links and special files are forbidden: {info.name!r}")
            size, raw_name = info.size, info.name
        path = member_path(raw_name, directory)
        if size < 0 or (directory and size != 0):
            raise InputError(f"Invalid archive member size: {raw_name!r}")
        if size > args.max_file_bytes:
            raise InputError(f"Archive member exceeds --max-file-bytes ({args.max_file_bytes}): {raw_name!r}")
        total += size
        if total > args.max_expanded_bytes:
            raise InputError(f"Archive exceeds --max-expanded-bytes ({args.max_expanded_bytes}).")
        if not path:
            continue
        registry.add(path, directory)
        members.append(Member(path, directory, size, info))
    return members


def archive_excluded(member: Member) -> bool:
    parts = member.path.split("/")
    directories = parts if member.directory else parts[:-1]
    return any(part in EXCLUDED_DIRS for part in directories)


def extract_archive(code: Path, destination: Path, kind: str, args: argparse.Namespace) -> tuple[Path, list[dict[str, str]]]:
    excluded: list[dict[str, str]] = []
    opener = zipfile.ZipFile if kind == "zip" else tarfile.open
    with opener(code, "r") as archive:
        members = preflight(archive, kind, args)
        # No member is written until the entire archive has passed validation.
        destination.mkdir()
        for member in members:
            if archive_excluded(member):
                excluded.append({"path": member.path, "reason": "excluded_directory"})
                continue
            target = destination.joinpath(*member.path.split("/"))
            if not within(target.resolve(), destination.resolve()):
                raise InputError(f"Archive destination escapes source directory: {member.path!r}")
            if member.directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            incoming = archive.open(member.info, "r") if kind == "zip" else archive.extractfile(member.info)
            if incoming is None:
                raise InputError(f"Archive member cannot be read: {member.path!r}")
            copied = 0
            with incoming, target.open("xb") as outgoing:
                while block := incoming.read(min(CHUNK, member.size - copied + 1)):
                    copied += len(block)
                    if copied > member.size or copied > args.max_file_bytes:
                        raise InputError(f"Archive member expanded beyond its declared size: {member.path!r}")
                    outgoing.write(block)
            if copied != member.size:
                raise InputError(f"Archive member size mismatch: {member.path!r}")
    children = list(destination.iterdir())
    root = children[0] if len(children) == 1 and children[0].is_dir() else destination
    if root != destination:
        prefix = root.name + "/"
        excluded = [{**item, "path": item["path"][len(prefix):] if item["path"].startswith(prefix) else item["path"]} for item in excluded]
    return root, excluded


def make_manifest(root: Path, hash_max_bytes: int, excluded: list[dict[str, str]]) -> dict[str, Any]:
    files = []

    def walk_error(error: OSError) -> None:
        raise InputError(f"Cannot inspect source directory: {error}")

    for directory, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in sorted(directories):
            path = base / name
            reason = "excluded_directory" if name in EXCLUDED_DIRS else "symlink_or_reparse_point" if reparse_or_link(path) else None
            if reason:
                directories.remove(name)
                excluded.append({"path": path.relative_to(root).as_posix(), "reason": reason})
        directories.sort()
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if reparse_or_link(path, info) or not stat.S_ISREG(info.st_mode):
                excluded.append({"path": relative, "reason": "symlink_or_reparse_point" if reparse_or_link(path, info) else "non_regular_file"})
                continue
            item: dict[str, Any] = {"path": relative, "bytes": info.st_size, "sha256": None}
            if info.st_size > hash_max_bytes:
                item.update(hash_status="skipped", hash_reason=f"file_exceeds_hash_max_bytes:{hash_max_bytes}")
            else:
                item.update(sha256=sha256_file(path), hash_status="hashed")
            files.append(item)
    return {"schema_version": SCHEMA_VERSION, "root": str(root), "hash_max_bytes": hash_max_bytes,
            "files": sorted(files, key=lambda item: item["path"]),
            "excluded": sorted(excluded, key=lambda item: (item["path"], item["reason"]))}


def pdf_text(paper: Path, out: Path, code_roots: tuple[Path, ...], disabled: bool) -> dict[str, Any]:
    fallback: dict[str, Any] = {"status": "needs_pdf_tool", "method": None, "directory": None, "pages": None,
                                "reason": "Use the Codex PDF tool to inspect the supplied paper; no dependency was installed."}
    if disabled:
        return {**fallback, "reason": "Optional text extraction disabled; use the Codex PDF tool."}
    errors = []
    executable = shutil.which("pdftotext")
    # Never run an executable supplied as part of the project being audited.
    if executable and not any(within(Path(executable).resolve(), root) for root in code_roots):
        text_directory = out / "paper_text"
        text_directory.mkdir(exist_ok=True)
        combined = text_directory / ".pdftotext.txt"
        try:
            subprocess.run([executable, "-enc", "UTF-8", str(paper), str(combined)], cwd=out,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=60)
            if combined.stat().st_size > 64 * MIB:
                raise InputError("Optional PDF text exceeds 64 MiB; use the PDF tool.")
            pages = combined.read_text(encoding="utf-8").split("\f")
            if pages and not pages[-1].strip():
                pages.pop()
            for index, text in enumerate(pages, 1):
                (text_directory / f"page_{index:02d}.txt").write_text(text, encoding="utf-8")
            return {"status": "extracted", "method": "pdftotext", "directory": str(text_directory), "pages": len(pages),
                    "note": "Text extraction is an aid; inspect PDF figures/equations with the PDF tool."}
        except (OSError, ValueError, subprocess.SubprocessError, InputError) as error:
            errors.append(f"pdftotext: {type(error).__name__}")
        finally:
            if combined.exists():
                combined.unlink()
    # Exclude the input tree from Python imports before looking for an installed pypdf.
    previous_path = sys.path[:]
    try:
        sys.path[:] = [entry for entry in previous_path
                       if not any(within(Path(entry or os.getcwd()).resolve(), root) for root in code_roots)]
        try:
            pypdf = importlib.import_module("pypdf")
        except ImportError:
            pypdf = None
        except Exception as error:
            errors.append(f"pypdf import: {type(error).__name__}")
            pypdf = None
        if pypdf is not None:
            try:
                reader = pypdf.PdfReader(str(paper))
                texts = []
                size = 0
                for page in reader.pages:
                    text = page.extract_text() or ""
                    size += len(text.encode("utf-8"))
                    if size > 64 * MIB:
                        raise InputError("Optional PDF text exceeds 64 MiB; use the PDF tool.")
                    texts.append(text)
                text_directory = out / "paper_text"
                text_directory.mkdir(exist_ok=True)
                for index, text in enumerate(texts, 1):
                    (text_directory / f"page_{index:02d}.txt").write_text(text, encoding="utf-8")
                return {"status": "extracted", "method": "pypdf", "directory": str(text_directory), "pages": len(texts),
                        "note": "Text extraction is an aid; inspect PDF figures/equations with the PDF tool."}
            except Exception as error:
                errors.append(f"pypdf: {type(error).__name__}")
    finally:
        sys.path[:] = previous_path
    if errors:
        fallback["attempts"] = errors
    return fallback


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--paper", type=Path, required=True, help="Local PDF; read only.")
    result.add_argument("--code", type=Path, required=True, help="Local code directory or supported archive; read only.")
    result.add_argument("--out", type=Path, required=True, help="New output directory; its parent must exist.")
    result.add_argument("--max-expanded-bytes", type=positive_int, default=1024 * MIB)
    result.add_argument("--max-file-bytes", type=positive_int, default=100 * MIB)
    result.add_argument("--max-files", type=positive_int, default=100000, help="Maximum archive entries, including directories.")
    result.add_argument("--hash-max-bytes", type=positive_int, default=16 * MIB)
    result.add_argument("--no-pdf-text", action="store_true", help="Skip optional installed PDF text extractors.")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    out: Path | None = None
    try:
        candidate = args.out.expanduser().absolute()
        if candidate.exists() or candidate.is_symlink():
            raise InputError(f"Output directory must be new: {candidate}")
        code = args.code.expanduser().resolve()
        candidate = candidate.resolve()
        if code.is_dir() and within(candidate, code):
            raise InputError("Output directory must not be inside the input code directory.")
        candidate.mkdir()  # Never modify an existing output directory or create outside parents.
        out = candidate
        paper = args.paper.expanduser().resolve(strict=True)
        if paper.suffix.lower() != ".pdf":
            raise InputError("--paper must be a PDF file.")
        with open_regular(paper) as stream:
            if b"%PDF-" not in stream.read(1024):
                raise InputError("--paper does not have a PDF header.")
        paper_hash = sha256_file(paper)
        excluded: list[dict[str, str]] = []
        if code.is_dir():
            root, kind, archive_hash = code, "directory", None
        elif code.is_file():
            archive_format = archive_kind(code)
            archive_hash = sha256_file(code)
            root, excluded = extract_archive(code, out / "source", archive_format, args)
            kind = "archive"
        else:
            raise InputError(f"--code is not a directory or regular archive: {code}")
        manifest = make_manifest(root, args.hash_max_bytes, excluded)
        write_json(out / "source_manifest.json", manifest)
        extraction = pdf_text(paper, out, (code, root, out / "source"), args.no_pdf_text)
        files = manifest["files"]
        inputs = {"schema_version": SCHEMA_VERSION,
                  "paper": {"path": str(paper), "sha256": paper_hash, "pages": extraction["pages"]},
                  "source": {"kind": kind, "path": str(code), "root": str(root), "sha256": archive_hash},
                  "inventory": {"files": len(files), "bytes": sum(item["bytes"] for item in files),
                                "hashed_files": sum(item["sha256"] is not None for item in files),
                                "excluded": manifest["excluded"], "excluded_count": len(manifest["excluded"])},
                  "pdf_text": extraction,
                  "method": "Static input preparation only. Inputs are read only; project code is never executed and no packages are downloaded or installed.",
                  "notes": ["Unhashed or unverified weights are evidence gaps, not method mismatches.",
                            "Large files remain in the inventory; hash omissions include an explicit reason.",
                            "Directory exclusions are recorded; absence from this inventory does not establish absence from the original input."],
                  "limits": {"max_expanded_bytes": args.max_expanded_bytes, "max_file_bytes": args.max_file_bytes,
                             "max_files": args.max_files, "hash_max_bytes": args.hash_max_bytes}}
        write_json(out / "inputs.json", inputs)
        print(json.dumps({"inputs": str(out / "inputs.json"), "manifest": str(out / "source_manifest.json"),
                          "source_root": str(root), "files": len(files), "excluded": len(manifest["excluded"])}, ensure_ascii=True, separators=(",", ":")))
        return 0
    except Exception as error:
        failure = {"schema_version": SCHEMA_VERSION, "status": "failed", "error_type": type(error).__name__, "message": str(error),
                   "note": "Inputs were not modified. Partial output is retained for inspection and must not be used as a completed preparation."}
        if out is not None:
            try:
                write_json(out / "FAILURE.json", failure)
            except OSError:
                pass
        print(f"prepare_inputs: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
