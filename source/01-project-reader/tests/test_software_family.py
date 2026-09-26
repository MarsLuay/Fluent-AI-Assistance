"""Software family is independent of the .zeia suffix and archive format."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

from tecan_reader.project_model import CanonicalProjectModel
from tecan_reader.zeia_adapters import ingest_zeia, probe_zeia


def _zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)


SCRIPT = "<Script><ObjectName>Demo</ObjectName></Script>"


def test_extension_and_format_do_not_imply_fluentcontrol(tmp_path: Path) -> None:
    archive = tmp_path / "plain.zeia"
    _zip(archive, {"Scripts/Demo.xscr": SCRIPT})
    detection = probe_zeia(archive).to_dict()
    assert detection["format_family"] == "structured-zeia"
    assert detection["software_family"]["software_family"] == "unknown"
    assert detection["software_family"]["status"] == "unknown"
    model = ingest_zeia(archive)
    assert model.source_metadata["software_family"]["software_family"] == "unknown"
    restored = json.loads(json.dumps(model.to_dict()))
    assert restored["detection"]["software_family"]["status"] == "unknown"


def test_explicit_names_classify_fluentcontrol_vcontrol_veya_and_conflict(tmp_path: Path) -> None:
    cases = {
        "fluent.zeia": ("<Script><ObjectName>FluentControl Demo</ObjectName></Script>", "fluentcontrol"),
        "vcontrol.zeia": ("<Script><ObjectName>vControl Demo</ObjectName></Script>", "vcontrol"),
        "veya.zeia": ("<Script><ObjectName>Veya Demo</ObjectName></Script>", "veya"),
    }
    for filename, (text, family) in cases.items():
        path = tmp_path / filename
        _zip(path, {"Scripts/Demo.xscr": text})
        family_record = probe_zeia(path).to_dict()["software_family"]
        assert family_record["status"] == "verified"
        assert family_record["software_family"] == family
        assert family_record["evidence"][0]["member"] == "Scripts/Demo.xscr"

    conflict = tmp_path / "both.zeia"
    _zip(conflict, {"Scripts/Demo.xscr": "<Script><ObjectName>FluentControl and vControl</ObjectName></Script>"})
    conflicted = probe_zeia(conflict).to_dict()["software_family"]
    assert conflicted["status"] == "conflicting"
    assert conflicted["software_family"] == "conflicting"
    assert {item["family"] for item in conflicted["evidence"]} == {"fluentcontrol", "vcontrol"}


def test_legacy_detection_without_family_stays_unknown(tmp_path: Path) -> None:
    model = CanonicalProjectModel.from_inspection(
        {"scripts": [], "objects": [], "gwls": []},
        source_archive=tmp_path / "legacy.zeia",
        detection={"status": "supported", "format_family": "structured-zeia"},
    )
    assert model.detection["software_family"]["software_family"] == "unknown"
    assert model.detection["software_family"]["compatibility"] == "missing-family-evidence-is-unknown"
