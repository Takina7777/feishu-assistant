"""配置层回归测试（含 is_approval 方法形态，防 property 误用）。"""
from __future__ import annotations

from conftest import make_cfg


def test_is_approval_maps_modes():
    # 全 direct
    cfg = make_cfg(provision_mode="direct", freeze_mode="direct",
                   unfreeze_mode="direct", delete_mode="direct")
    assert cfg.is_approval("provision") is False
    assert cfg.is_approval("freeze") is False
    assert cfg.is_approval("delete") is False

    # 混合
    cfg2 = make_cfg(provision_mode="approval", freeze_mode="direct", delete_mode="approval")
    assert cfg2.is_approval("provision") is True
    assert cfg2.is_approval("freeze") is False
    assert cfg2.is_approval("delete") is True

    # 未知类型按 direct 处理
    assert cfg2.is_approval("whatever") is False


def test_is_approval_case_insensitive():
    cfg = make_cfg(delete_mode="APPROVAL")
    assert cfg.is_approval("delete") is True


def test_validate_flags_missing():
    cfg = make_cfg(operator_open_ids=[], approver_open_ids=[])
    problems = cfg.validate()
    assert any("OPERATOR_OPEN_IDS" in p for p in problems)
