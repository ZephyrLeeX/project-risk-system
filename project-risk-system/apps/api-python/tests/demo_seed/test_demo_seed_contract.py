from risk_platform.demo_seed import (
    CANONICAL_PROJECTS,
    DEMO_MARKER,
    DemoRiskStage,
    _risk_fingerprint,
    normalized_alias,
    stable_id,
)


def test_demo_seed_contract_is_stable_and_canonical() -> None:
    assert DEMO_MARKER == "WSLDEMO"
    assert len(CANONICAL_PROJECTS) == 8
    assert len(set(CANONICAL_PROJECTS)) == 8
    assert all(name.startswith("WSLDEMO-") for name in CANONICAL_PROJECTS)
    assert normalized_alias("WSLDEMO-ERP 系统升级") == normalized_alias("wsldemo erp系统升级")
    assert stable_id("project", "01") == stable_id("project", "01")
    assert stable_id("project", "01") != stable_id("project", "02")
    assert len(_risk_fingerprint("01")) == 64


def test_demo_risk_stages_are_mapped_to_formal_statuses() -> None:
    assert tuple(stage.value for stage in DemoRiskStage) == (
        "open",
        "monitoring",
        "mitigated",
        "closed",
    )
