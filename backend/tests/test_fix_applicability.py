"""B4 fix applicability: Doc-3's four LPT001 examples are the
acceptance tests, plus the hard-veto and level-ladder mechanics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.fix_applicability_service import assess_fix_applicability


def _entity(
    name,
    ci_class="cmdb_ci_computer",
    manufacturer=None,
    model=None,
    os_name=None,
    os_version=None,
    attributes=None,
):
    attrs = {"ci_class": ci_class}
    attrs.update(attributes or {})
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        manufacturer=manufacturer,
        model=model,
        os_name=os_name,
        os_version=os_version,
        attributes=attrs,
    )


def _rule(fix_id, required, excluded=None, target_class="endpoint", level="same_component_or_version"):
    return SimpleNamespace(
        fix_pattern_id=fix_id,
        target_class_key=target_class,
        required_traits=required,
        excluded_traits=excluded or {},
        applicability_level=level,
        minimum_evidence=1,
        confidence=0.5,
        approval_requirement="review",
    )


def _fix(fix_id, recommendation, source_entity_id=None):
    return SimpleNamespace(
        id=fix_id,
        workflow_entity_id=source_entity_id,
        recommended_fix=recommendation,
    )


def _db(rows, classes=None):
    classes = classes or {}

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "fix_applicability_rules" in text:
            result.all.return_value = rows
            return result
        if text.startswith("SELECT entity_classes."):
            result.scalar_one_or_none.return_value = classes.get("row")
            return result
        result.scalar_one_or_none.return_value = None
        result.all.return_value = []
        return result

    async def get(model, pk):
        return classes.get(pk)

    return SimpleNamespace(execute=execute, get=AsyncMock(side_effect=get))


@pytest.mark.asyncio
async def test_example_1_same_model_and_configuration_very_high():
    """Latitude 5420 BIOS/BitLocker fix -> LPT121, same model, same BIOS
    package, same TPM config: very high, no review."""
    tenant_id = uuid4()
    fix_id = uuid4()
    lpt121 = _entity(
        "LPT121",
        manufacturer="Dell",
        model="Latitude 5420",
        os_name="Windows",
        os_version="11 23H2",
        attributes={"bios_package": "1.21.0", "tpm_state": "enabled"},
    )
    rule = _rule(
        fix_id,
        required={
            "model": "Latitude 5420",
            "bios_package": "1.21.0",
            "tpm_state": "enabled",
            "error_signature": None,
        }
        | {"error_signature": "BITLOCKER_RECOVERY_PROMPT"},
        target_class="laptop",
    )
    lpt121.attributes["error_signature"] = "BITLOCKER_RECOVERY_PROMPT"
    db = _db([(rule, _fix(fix_id, "Suspend BitLocker, update BIOS, resume"))])

    out = await assess_fix_applicability(db, tenant_id, lpt121)

    assert len(out["applicable"]) == 1
    a = out["applicable"][0]
    assert a["applicability_level"] == "same_model_and_configuration"
    assert a["confidence"] >= 0.9
    assert a["requires_review"] is False
    assert any("model" in f for f in a["matching_factors"])


@pytest.mark.asyncio
async def test_example_2_chrome_crash_transfers_laptop_to_desktop():
    """Chrome 126 crash fix learned on a laptop applies to a desktop:
    the scope is managed endpoints + Chrome + policy, not 'laptops'."""
    tenant_id = uuid4()
    fix_id = uuid4()
    dtp055 = _entity(
        "DTP055",
        os_name="Windows",
        os_version="11 23H2",
        attributes={
            "software_version": "chrome_126",
            "policy_version": "endpoint_security_9.2",
            "error_signature": "CHROME_CRASH_ON_LAUNCH",
        },
    )
    rule = _rule(
        fix_id,
        required={
            "software_version": "chrome_126",
            "policy_version": "endpoint_security_9.2",
            "error_signature": "CHROME_CRASH_ON_LAUNCH",
            "os_version": "11 23H2",
        },
        target_class="endpoint",
    )
    db = _db([(rule, _fix(fix_id, "Exempt Chrome from policy scan module"))])

    out = await assess_fix_applicability(db, tenant_id, dtp055)

    assert len(out["applicable"]) == 1
    a = out["applicable"][0]
    assert a["applicability_level"] == "same_component_or_version"
    assert a["confidence"] >= 0.6
    # Being a desktop rather than a laptop did not block the transfer.
    assert not any("class" in d for d in a["differences"])


@pytest.mark.asyncio
async def test_example_3_no_applicable_precedent():
    """LPT001 battery fix vs DTP055 random power-off: different
    component, different failure mode -> honest empty list."""
    tenant_id = uuid4()
    fix_id = uuid4()
    dtp055 = _entity(
        "DTP055",
        attributes={"error_signature": "RANDOM_POWER_OFF"},
    )
    rule = _rule(
        fix_id,
        required={
            "component": "usb_c_power_adapter",
            "error_signature": "BATTERY_NOT_CHARGING",
        },
        target_class="laptop",
    )
    db = _db([(rule, _fix(fix_id, "Replace USB-C power adapter"))])

    out = await assess_fix_applicability(db, tenant_id, dtp055)

    assert out["applicable"] == []
    assert len(out["rejected"]) == 1
    assert out["rejected"][0]["reason"] == "required_trait_not_validated"


@pytest.mark.asyncio
async def test_example_4_partial_transfer_requires_review_realtek_rejected():
    """AX201 Code 10 fix: desktop with the same adapter + driver gets a
    reviewable partial transfer; a Realtek desktop is rejected outright."""
    tenant_id = uuid4()
    fix_id = uuid4()
    rule = _rule(
        fix_id,
        required={
            "wifi_adapter": "Intel AX201",
            "driver_version": "23.40.0",
            "error_signature": "NET_ADAPTER_CODE_10",
        },
        excluded={"physical_adapter_failure": "true"},
        target_class="laptop",
    )
    fix = _fix(fix_id, "Roll back the Wi-Fi driver")

    ax201_desktop = _entity(
        "DTP055",
        manufacturer="HP",
        attributes={
            "wifi_adapter": "Intel AX201",
            "driver_version": "23.40.0",
            "error_signature": "NET_ADAPTER_CODE_10",
        },
    )
    out = await assess_fix_applicability(db := _db([(rule, fix)]), tenant_id, ax201_desktop)
    assert len(out["applicable"]) == 1
    a = out["applicable"][0]
    assert a["applicability_level"] == "cross_class_capability"
    assert a["requires_review"] is True
    assert a["confidence"] < 0.9

    realtek_desktop = _entity(
        "DTP056",
        attributes={
            "wifi_adapter": "Realtek RTL8852",
            "driver_version": "6.1.0",
            "error_signature": "NET_ADAPTER_CODE_10",
        },
    )
    out = await assess_fix_applicability(_db([(rule, fix)]), tenant_id, realtek_desktop)
    assert out["applicable"] == []
    assert out["rejected"][0]["reason"] == "required_trait_not_validated"
    assert any("wifi_adapter" in d for d in out["rejected"][0]["differences"])


@pytest.mark.asyncio
async def test_excluded_trait_is_a_hard_veto():
    tenant_id = uuid4()
    fix_id = uuid4()
    rule = _rule(
        fix_id,
        required={"wifi_adapter": "Intel AX201"},
        excluded={"physical_adapter_failure": "true"},
    )
    broken = _entity(
        "LPT130",
        attributes={
            "wifi_adapter": "Intel AX201",
            "physical_adapter_failure": "true",
        },
    )
    out = await assess_fix_applicability(
        _db([(rule, _fix(fix_id, "Roll back driver"))]), tenant_id, broken
    )
    assert out["applicable"] == []
    assert out["rejected"][0]["reason"] == "excluded_trait"


@pytest.mark.asyncio
async def test_exact_ci_precedent_wins():
    tenant_id = uuid4()
    fix_id = uuid4()
    lpt001 = _entity("LPT001", attributes={"wifi_adapter": "Intel AX201"})
    rule = _rule(fix_id, required={"wifi_adapter": "Intel AX201"}, target_class=None)
    out = await assess_fix_applicability(
        _db([(rule, _fix(fix_id, "Roll back driver", source_entity_id=lpt001.id))]),
        tenant_id,
        lpt001,
    )
    a = out["applicable"][0]
    assert a["applicability_level"] == "exact_ci"
    assert a["confidence"] >= 0.95
