from fluent_pipeline.source_family_gate import source_family_gate


def _detection(family: str, status: str, evidence=None):
    return {"software_family": {"software_family": family, "status": status, "evidence": evidence or []}}


def test_verified_fluentcontrol_is_accepted_and_install_is_not_an_input():
    decision = source_family_gate(_detection("fluentcontrol", "verified", [{"member": "Scripts/Demo.xscr"}]))
    assert decision["accepted"] is True
    assert decision["code"] == "source_family_fluentcontrol"
    assert decision["evidence"][0]["member"] == "Scripts/Demo.xscr"
    assert "install" not in source_family_gate.__code__.co_varnames


def test_vcontrol_veya_unknown_and_conflict_block_before_generation():
    blocked = {
        "vcontrol": "source_family_vcontrol_blocked",
        "veya": "source_family_veya_blocked",
        "unknown": "source_family_unknown",
        "conflicting": "source_family_conflicting",
    }
    for family, code in blocked.items():
        status = "verified" if family in {"vcontrol", "veya"} else family
        decision = source_family_gate(_detection(family, status, [{"family": family}]))
        assert decision["accepted"] is False
        assert decision["code"] == code
        assert decision["blocked_before"] == "fluent_generation"
        assert decision["evidence"] == [{"family": family}]


def test_missing_family_record_is_unknown_not_fluent():
    decision = source_family_gate({"format_family": "visionx-datastore"})
    assert decision["accepted"] is False
    assert decision["code"] == "source_family_unknown"
    assert decision["software_family"] == "unknown"
