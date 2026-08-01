"""Tests for ZEIA fs/ embedding helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

from fluent_pipeline.checksum import compute_checksum, xml_root_name
from fluent_pipeline.checksums import entry_checksum_state
from fluent_pipeline.protocol_ir import build_media_path_map, sound_path_specs_from_ir
from fluent_pipeline.zeia_filesystem import (
    archive_fs_path_to_content_entry,
    audit_archive_filesystem,
    build_fs_mapping_xml,
    collect_archive_file_reference_paths,
    collect_file_reference_paths,
    copy_referenced_filesystem_from_archives,
    embed_filesystem_in_archive,
    ensure_script_file_references,
    parse_fs_mapping_directories,
    plan_fs_embed,
    repair_archive_content_filesystem,
    update_archive_content_filesystem,
)


def test_build_fs_mapping_xml_checksum_matches_native_algorithm() -> None:
    directories = [
        (1, r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media"),
        (2, r"C:\TubeEye\bin"),
    ]
    payload = build_fs_mapping_xml(directories)
    assert xml_root_name(payload) == "DirectoryMappings"
    assert entry_checksum_state(payload) == "valid"
    assert compute_checksum(payload) in payload.decode("utf-8-sig")


def test_archive_fs_path_to_content_entry_converts_native_shapes() -> None:
    assert archive_fs_path_to_content_entry("fs/mapping.xml") == "mapping.xml"
    assert archive_fs_path_to_content_entry(r"fs\mapping.xml") == "mapping.xml"
    assert archive_fs_path_to_content_entry("fs/1/step_010_video.gif") == r"1\step_010_video.gif"
    assert archive_fs_path_to_content_entry(r"fs\1\step_010_video.gif") == r"1\step_010_video.gif"


def test_update_archive_content_filesystem_inserts_manifest_and_checksum() -> None:
    content_xml = (
        "<?xml version='1.0' encoding='utf-8'?>\r\n"
        "<ArchiveContent>\r\n"
        "\t<Payload>\r\n"
        "\t\t<DatastoreEntries>\r\n"
        "\t\t\t<Entry>nodedescription.xml</Entry>\r\n"
        "\t\t</DatastoreEntries>\r\n"
        "\t\t<MetaEntries>\r\n"
        "\t\t\t<Entry>content.xml</Entry>\r\n"
        "\t\t</MetaEntries>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</ArchiveContent>\r\n"
    ).encode("utf-8")
    updated = update_archive_content_filesystem(
        content_xml,
        [r"1\step_010_video.gif", "mapping.xml"],
    )
    text = updated.decode("utf-8-sig")
    assert text.index("<FilesystemEntries>") < text.index("<DatastoreEntries>")
    assert "<Entry>1\\step_010_video.gif</Entry>" in text
    assert "<Entry>mapping.xml</Entry>" in text
    assert entry_checksum_state(updated) == "valid"
    assert compute_checksum(updated) in text


def test_update_archive_content_filesystem_replaces_backslash_entries_literally() -> None:
    content_xml = (
        "<?xml version='1.0' encoding='utf-8'?>\r\n"
        "<ArchiveContent>\r\n"
        "\t<Payload>\r\n"
        "\t\t<FilesystemEntries>\r\n"
        "\t\t\t<Entry>mapping.xml</Entry>\r\n"
        "\t\t</FilesystemEntries>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</ArchiveContent>\r\n"
    ).encode("utf-8")

    updated = update_archive_content_filesystem(
        content_xml,
        [r"7\step_004_image.png", "mapping.xml"],
    )

    text = updated.decode("utf-8-sig")
    assert "<Entry>7\\step_004_image.png</Entry>" in text
    assert entry_checksum_state(updated) == "valid"


def test_embed_updates_content_xml_filesystem_entries(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "step_001_video.gif").write_bytes(b"gif-bytes")

    archive = tmp_path / "generated_project.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("meta/content.xml", b"<ArchiveContent><Payload></Payload><Checksum></Checksum></ArchiveContent>")

    media_path_map = {
        "touchtools_dir": r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images",
        "subfolder": "Demo_media",
        "entries": [
            {
                "filename": "step_001_video.gif",
                "absolute_path": (
                    r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media\step_001_video.gif"
                ),
                "drives_selected_image_path": True,
            }
        ],
    }
    plan = plan_fs_embed(
        media_dir=media_dir,
        media_path_map=media_path_map,
        external_files_dir=None,
        external_entries=[],
    )
    summary = embed_filesystem_in_archive(archive, plan)

    assert summary["file_count"] == 1
    with zipfile.ZipFile(archive, "r") as zf:
        names = {name.replace("\\", "/") for name in zf.namelist()}
        assert "fs/1/step_001_video.gif" in names
        assert "fs/mapping.xml" in names
        mapping = zf.read("fs/mapping.xml")
        assert entry_checksum_state(mapping) == "valid"
        assert b"Demo_media" in mapping
        content = zf.read("meta/content.xml")
        assert entry_checksum_state(content) == "valid"
        assert b"<FilesystemEntries>" in content
        assert b"1\\step_001_video.gif" in content
        assert b"<Entry>mapping.xml</Entry>" in content
        assert audit_archive_filesystem({name: zf.read(name) for name in zf.namelist()}) == []


def test_copy_referenced_filesystem_from_archives_copies_exact_closure(tmp_path: Path) -> None:
    target_dir = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media"
    referenced = target_dir + r"\step001.gif"
    source = tmp_path / "source.zeia"
    destination = tmp_path / "generated.zeia"
    content = (
        b"<ArchiveContent><Payload><DatastoreEntries>"
        b"<Entry>UserSpecific\\script.xscr</Entry>"
        b"</DatastoreEntries></Payload><Checksum></Checksum></ArchiveContent>"
    )
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, target_dir)]))
        zf.writestr("fs/1/step001.gif", b"wanted")
        zf.writestr("fs/1/unreferenced.gif", b"not wanted")
    with zipfile.ZipFile(destination, "w") as zf:
        zf.writestr(
            "DataStore/UserSpecific/script.xscr",
            f"<VxData><Payload><FileReference><File>{referenced}</File></FileReference>"
            "<PayloadData /></Payload><Checksum></Checksum></VxData>",
        )
        zf.writestr("meta/content.xml", content)

    summary = copy_referenced_filesystem_from_archives(
        [source],
        destination,
    )

    assert summary["complete"] is True
    assert summary["file_count"] == 1
    with zipfile.ZipFile(destination) as zf:
        normalized = {name.replace("\\", "/") for name in zf.namelist()}
        assert "fs/1/step001.gif" in normalized
        assert "fs/1/unreferenced.gif" not in normalized
        archive_data = {name: zf.read(name) for name in zf.namelist()}
    assert audit_archive_filesystem(archive_data) == []


def test_copy_referenced_filesystem_searches_remaining_archives_and_remaps_keys(
    tmp_path: Path,
) -> None:
    primary_dir = r"C:\Tecan\Primary"
    secondary_dir = r"C:\Tecan\Secondary"
    primary_ref = primary_dir + r"\primary.dat"
    secondary_ref = secondary_dir + r"\secondary.dat"
    primary = tmp_path / "primary.zeia"
    secondary = tmp_path / "secondary.zeia"
    destination = tmp_path / "generated.zeia"
    content = (
        b"<ArchiveContent><Payload><DatastoreEntries>"
        b"<Entry>UserSpecific\\script.xscr</Entry>"
        b"</DatastoreEntries></Payload><Checksum></Checksum></ArchiveContent>"
    )
    with zipfile.ZipFile(primary, "w") as zf:
        zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, primary_dir)]))
        zf.writestr("fs/1/primary.dat", b"primary")
    with zipfile.ZipFile(secondary, "w") as zf:
        zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, secondary_dir)]))
        zf.writestr("fs/1/secondary.dat", b"secondary")
    with zipfile.ZipFile(destination, "w") as zf:
        zf.writestr(
            "DataStore/UserSpecific/script.xscr",
            f"""<VxData><Payload>
<FileReference><File>{primary_ref}</File></FileReference>
<FileReference><File>{secondary_ref}</File></FileReference>
</Payload></VxData>""",
        )
        zf.writestr("meta/content.xml", content)

    summary = copy_referenced_filesystem_from_archives(
        [primary, secondary],
        destination,
    )

    assert summary["complete"] is True
    assert [item["source_archive"] for item in summary["copied_files"]] == [
        str(primary),
        str(secondary),
    ]
    with zipfile.ZipFile(destination) as zf:
        assert zf.read("fs/1/primary.dat") == b"primary"
        assert zf.read("fs/2/secondary.dat") == b"secondary"
        mapping = dict(
            parse_fs_mapping_directories(zf.read("fs/mapping.xml"))
        )
    assert mapping == {1: primary_dir, 2: secondary_dir}


def test_copy_referenced_filesystem_prefers_identical_primary_payload(tmp_path: Path) -> None:
    target_dir = r"C:\Tecan\Shared"
    referenced = target_dir + r"\shared.dat"
    primary = tmp_path / "primary.zeia"
    secondary = tmp_path / "secondary.zeia"
    destination = tmp_path / "generated.zeia"
    for archive in (primary, secondary):
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, target_dir)]))
            zf.writestr("fs/1/shared.dat", b"identical")
    with zipfile.ZipFile(destination, "w") as zf:
        zf.writestr(
            "DataStore/UserSpecific/script.xscr",
            f"<VxData><Payload><FileReference><File>{referenced}</File>"
            "</FileReference></Payload></VxData>",
        )
        zf.writestr("meta/content.xml", "<ArchiveContent><Payload /></ArchiveContent>")

    summary = copy_referenced_filesystem_from_archives(
        [primary, secondary],
        destination,
    )

    assert summary["complete"] is True
    assert summary["copied_files"][0]["source_archive"] == str(primary)
    assert summary["copied_files"][0]["matching_source_count"] == 2
    assert summary["conflicting_paths"] == []


def test_copy_referenced_filesystem_rejects_conflicting_same_path_payloads(
    tmp_path: Path,
) -> None:
    target_dir = r"C:\Tecan\Shared"
    referenced = target_dir + r"\shared.dat"
    primary = tmp_path / "primary.zeia"
    secondary = tmp_path / "secondary.zeia"
    destination = tmp_path / "generated.zeia"
    for archive, payload in ((primary, b"primary"), (secondary, b"secondary")):
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, target_dir)]))
            zf.writestr("fs/1/shared.dat", payload)
    with zipfile.ZipFile(destination, "w") as zf:
        zf.writestr(
            "DataStore/UserSpecific/script.xscr",
            f"<VxData><Payload><FileReference><File>{referenced}</File>"
            "</FileReference></Payload></VxData>",
        )
        zf.writestr("meta/content.xml", "<ArchiveContent><Payload /></ArchiveContent>")

    summary = copy_referenced_filesystem_from_archives(
        [primary, secondary],
        destination,
    )

    assert summary["complete"] is False
    assert summary["copied_files"] == []
    assert summary["conflicting_paths"][0]["referenced_path"] == referenced
    assert {
        item["source_archive"]
        for item in summary["conflicting_paths"][0]["candidates"]
    } == {str(primary), str(secondary)}


def test_collect_archive_file_references_includes_packaged_subroutines(tmp_path: Path) -> None:
    archive = tmp_path / "generated.zeia"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "DataStore/UserSpecific/main.xscr",
            "<VxData><Payload><PayloadData /></Payload></VxData>",
        )
        zf.writestr(
            "DataStore/UserSpecific/helper.xscr",
            r"""<VxData><Payload>
<FileReference><File>C:\Tecan\Images\helper.gif</File></FileReference>
<PayloadData />
</Payload></VxData>""",
        )

    assert collect_archive_file_reference_paths(archive) == [
        r"C:\Tecan\Images\helper.gif"
    ]


def test_audit_archive_filesystem_rejects_unmapped_absolute_reference() -> None:
    archive_data = {
        "DataStore/UserSpecific/script.xscr": (
            b"<VxData><Payload><FileReference><File>C:\\Media\\step.gif</File>"
            b"</FileReference><PayloadData /></Payload></VxData>"
        ),
    }
    findings = audit_archive_filesystem(archive_data)
    assert [item["kind"] for item in findings] == ["unmapped_file_reference"]


def test_audit_archive_filesystem_flags_missing_manifest(tmp_path: Path) -> None:
    mapping = build_fs_mapping_xml(
        [(1, r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media")]
    )
    content_xml = (
        "<?xml version='1.0' encoding='utf-8'?>\r\n"
        "<ArchiveContent>\r\n"
        "\t<Payload>\r\n"
        "\t\t<DatastoreEntries></DatastoreEntries>\r\n"
        "\t\t<MetaEntries></MetaEntries>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</ArchiveContent>\r\n"
    ).encode("utf-8")
    archive_data = {
        "meta/content.xml": content_xml,
        "fs/mapping.xml": mapping,
        "fs/1/missing.gif": b"gif",
    }
    findings = audit_archive_filesystem(archive_data)
    kinds = {item["kind"] for item in findings}
    assert "missing_filesystem_entries_manifest" in kinds
    assert "invalid_content_checksum" in kinds


def test_repair_archive_content_filesystem_patches_existing_zip(tmp_path: Path) -> None:
    archive = tmp_path / "generated_project.zeia"
    content_xml = (
        "<?xml version='1.0' encoding='utf-8'?>\r\n"
        "<ArchiveContent>\r\n"
        "\t<Payload>\r\n"
        "\t\t<DatastoreEntries></DatastoreEntries>\r\n"
        "\t\t<MetaEntries></MetaEntries>\r\n"
        "\t</Payload>\r\n"
        "\t<Checksum></Checksum>\r\n"
        "</ArchiveContent>\r\n"
    ).encode("utf-8")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("meta/content.xml", content_xml)
        zf.writestr("fs/1/step_001_video.gif", b"gif")
        zf.writestr("fs/mapping.xml", build_fs_mapping_xml([(1, r"C:\Demo")]))

    summary = repair_archive_content_filesystem(archive)
    assert summary["patched"] is True
    assert summary["filesystem_entry_count"] == 2

    with zipfile.ZipFile(archive, "r") as zf:
        archive_data = {name: zf.read(name) for name in zf.namelist()}
    assert audit_archive_filesystem(archive_data) == []


def test_ensure_script_file_references_injects_blocks(tmp_path: Path) -> None:
    xscr = tmp_path / "demo.xscr"
    xscr.write_text(
        "\n".join(
            [
                "<?xml version='1.0' encoding='utf-8'?>",
                "<sd:VxData>",
                "  <Payload>",
                "    <Reference><Guid>abc</Guid></Reference>",
                "    <PayloadData>",
                "      <Script />",
                "    </PayloadData>",
                "  </Payload>",
                "</sd:VxData>",
            ]
        ),
        encoding="utf-8",
    )
    target = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media\step_001_video.gif"
    injected = ensure_script_file_references(xscr, [target, target])
    text = xscr.read_text(encoding="utf-8")

    assert injected == [target]
    assert text.index("</Reference>") < text.index("<FileReference>")
    assert text.index("<FileReference>") < text.index("<PayloadData>")
    assert target in text
    assert text.count("<FileReference>") == 1


def test_collect_file_reference_paths_includes_attachment_only_generated_media() -> None:
    display_path = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media\step_004_video.gif"
    attachment_path = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media\step_004_image.png"
    paths = collect_file_reference_paths(
        {
            "entries": [
                {
                    "absolute_path": display_path,
                    "drives_selected_image_path": True,
                    "kind": "video",
                },
                {
                    "absolute_path": attachment_path,
                    "attachment_only": True,
                    "kind": "image",
                },
            ]
        },
        None,
        None,
    )

    assert paths == [display_path, attachment_path]


def test_audio_path_in_media_path_map() -> None:
    ir = {
        "protocol": {"name": "AudioDemo"},
        "steps": [
            {
                "id": "step_001",
                "operation": "prompt_user",
                "parameters": {"sound_file": "media/step001Audio.mp3"},
            }
        ],
    }
    specs = sound_path_specs_from_ir(ir)
    assert len(specs) == 1
    assert specs[0]["kind"] == "audio"
    assert specs[0]["filename"] == "step001Audio.mp3"

    path_map = build_media_path_map(
        ir,
        r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images",
        subfolder="AudioDemo_media",
    )
    audio_entries = [entry for entry in path_map["entries"] if entry["kind"] == "audio"]
    assert len(audio_entries) == 1
    assert audio_entries[0]["drives_selected_sound_path"] is True
    assert audio_entries[0]["absolute_path"].endswith("step001Audio.mp3")


def test_audit_archive_filesystem_flags_missing_payload(tmp_path: Path) -> None:
    mapping = build_fs_mapping_xml(
        [(1, r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media")]
    )
    content_xml = update_archive_content_filesystem(
        (
            "<?xml version='1.0' encoding='utf-8'?>\r\n"
            "<ArchiveContent>\r\n"
            "\t<Payload>\r\n"
            "\t\t<DatastoreEntries></DatastoreEntries>\r\n"
            "\t\t<MetaEntries></MetaEntries>\r\n"
            "\t</Payload>\r\n"
            "\t<Checksum></Checksum>\r\n"
            "</ArchiveContent>\r\n"
        ).encode("utf-8"),
        ["mapping.xml"],
    )
    xscr = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<Root><SelectedImagePath>"
        r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\Demo_media\missing.gif"
        "</SelectedImagePath></Root>"
    ).encode("utf-8")
    archive_data = {
        "meta/content.xml": content_xml,
        "fs/mapping.xml": mapping,
        "DataStore/UserSpecific/demo.xscr": xscr,
    }
    findings = audit_archive_filesystem(archive_data)
    assert findings
    assert any(item["kind"] == "missing_fs_payload" for item in findings)
