from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
RECIPE_SCHEMA_VERSION = "tecan.compatibility_recipe.v1"
SOURCE_RECIPE_SCHEMA_VERSION = "tecan.synthetic_zeia_recipe.v1"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
VISIONX_NAMESPACE = "http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData"


def load_recipe(path: str | Path) -> dict[str, Any]:
    recipe_path = Path(path).resolve()
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    if recipe.get("schema_version") not in {
        RECIPE_SCHEMA_VERSION,
        SOURCE_RECIPE_SCHEMA_VERSION,
    }:
        raise ValueError(f"unsupported compatibility recipe schema: {recipe.get('schema_version')!r}")
    return recipe


def recipe_sha256(path: str | Path) -> str:
    """Hash the logical UTF-8 recipe so Windows checkout EOL conversion is harmless."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def materialize_recipe(path: str | Path, output_dir: str | Path) -> Path:
    recipe_path = Path(path).resolve()
    recipe = load_recipe(recipe_path)
    archive_name = str(recipe.get("archive_name") or "")
    if not archive_name.endswith(".zeia") or Path(archive_name).name != archive_name:
        raise ValueError(f"unsafe compatibility archive name: {archive_name!r}")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name

    entries = _recipe_entries(recipe, recipe_path)
    transform = recipe.get("xml_transform") or {}
    with zipfile.ZipFile(archive, "w") as zf:
        for entry in sorted(entries, key=lambda item: str(item["archive_path"])):
            archive_path = PurePosixPath(str(entry["archive_path"]))
            if archive_path.is_absolute() or ".." in archive_path.parts or not archive_path.name:
                raise ValueError(f"unsafe compatibility archive path: {archive_path}")
            payload = _entry_bytes(entry)
            if Path(archive_path).suffix.casefold() in {
                ".xscr", ".xcmp", ".xwsp", ".xlqc", ".xlcp", ".xsit", ".xcon", ".xml"
            }:
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text:
                    payload = _transform_xml(text, transform).encode("utf-8")
            info = zipfile.ZipInfo(archive_path.as_posix(), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, payload)
    return archive


def _recipe_entries(recipe: dict[str, Any], recipe_path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    source_recipe = recipe.get("source_recipe")
    if source_recipe:
        source_path = REPO_ROOT / str(source_recipe)
        entries.extend(_recipe_entries(load_recipe(source_path), source_path))
    entries.extend(_with_source_base(entry, recipe_path) for entry in recipe.get("entries") or [])
    entries.extend(_with_source_base(entry, recipe_path) for entry in recipe.get("extra_entries") or [])
    if not entries:
        raise ValueError(f"compatibility recipe has no entries: {recipe_path}")
    return entries


def _entry_bytes(entry: dict[str, Any]) -> bytes:
    if "content" in entry:
        return str(entry["content"]).encode("utf-8")
    if "content_base64" in entry:
        return base64.b64decode(str(entry["content_base64"]), validate=True)
    source = entry.get("source")
    if source:
        source_path = Path(str(source))
        candidates = [
            entry.get("_source_base", REPO_ROOT) / source_path,
            REPO_ROOT / source_path,
            REPO_ROOT / "source/03-protocol-builder" / source_path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_bytes()
        raise FileNotFoundError(f"compatibility source entry not found: {source!r}")
    raise ValueError(f"compatibility entry has no content or source: {entry.get('archive_path')!r}")


def _with_source_base(entry: dict[str, Any], recipe_path: Path) -> dict[str, Any]:
    item = dict(entry)
    item["_source_base"] = recipe_path.parent
    return item


def _transform_xml(text: str, transform: dict[str, Any]) -> str:
    if not transform:
        return text
    match = re.search(r"<VxData(?P<attributes>[^>]*)>", text, flags=re.IGNORECASE)
    if match is None:
        return text
    attributes = match.group("attributes")
    additions: list[str] = []
    version = str(transform.get("data_store_version") or "")
    if version and not re.search(r"\bdataStoreVersion\s*=", attributes, flags=re.IGNORECASE):
        additions.append(f' dataStoreVersion="{version}"')
    if transform.get("namespace") and not re.search(r"\bxmlns\s*=", attributes, flags=re.IGNORECASE):
        additions.append(f' xmlns="{VISIONX_NAMESPACE}"')
    root_name = str(transform.get("root_name") or "VxData")
    if not additions and root_name == "VxData":
        return text
    replacement = f"<{root_name}{attributes}{''.join(additions)}>"
    return text[: match.start()] + replacement + text[match.end() :].replace(
        "</VxData>", f"</{root_name}>"
    )
