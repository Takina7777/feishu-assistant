"""生命周期业务测试（使用 FakeClient，不访问网络）。"""
from __future__ import annotations

import pytest

from app import lifecycle
from app.lifecycle import LifecycleError, Person

from conftest import make_cfg, zh_user


class TestPerson:
    def test_from_user(self):
        u = zh_user("ou_1", frozen=True)
        p = Person.from_user(u)
        assert p.open_id == "ou_1"
        assert p.is_frozen is True
        assert p.state_label == "已停用（冻结中，无法登录）"

    def test_state_labels(self):
        assert Person.from_user(zh_user("a")).state_label == "正常（在职可用）"
        assert Person.from_user(zh_user("a", resigned=True)).state_label.startswith("已离职")
        assert Person.from_user(zh_user("a", unjoin=True)).state_label.startswith("待加入")
        assert Person.from_user(zh_user("a", exited=True)).state_label.startswith("已主动退出")


class TestProvision:
    def test_create_ok(self, fake_client, monkeypatch):
        monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
        cfg = make_cfg()
        params = {
            "name": "张三",
            "mobile": "13800138000",
            "email": "zs@corp.com",
            "dept_open_id": "od-dept-1",
            "employee_no": "1001",
            "client_token": "tok-1",
        }
        person = lifecycle.provision(fake_client, params, cfg)
        assert person.open_id.startswith("ou_new_")
        assert fake_client.created[0]["name"] == "张三"
        assert fake_client.created[0]["department_ids"] == ["od-dept-1"]

    def test_default_dept_used(self, fake_client, monkeypatch):
        monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
        cfg = make_cfg(default_department_open_id="od-default-dept")
        lifecycle.provision(fake_client, {"name": "张三", "mobile": "13800138000", "client_token": "t"}, cfg)
        assert fake_client.created[0]["department_ids"] == ["od-default-dept"]

    def test_no_dept_fails(self, fake_client):
        cfg = make_cfg(default_department_open_id="")
        with pytest.raises(LifecycleError, match="默认部门"):
            lifecycle.provision(fake_client, {"name": "张三", "mobile": "13800138000"}, cfg)

    def test_conflict_active(self, fake_client, monkeypatch):
        monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
        fake_client.add(zh_user("ou_existing", mobile="13800138000"))
        cfg = make_cfg()
        with pytest.raises(LifecycleError, match="在职成员"):
            lifecycle.provision(fake_client, {"name": "李四", "mobile": "13800138000", "client_token": "t"}, cfg)
        assert fake_client.created == []

    def test_conflict_resigned(self, fake_client):
        fake_client.add(zh_user("ou_gone", mobile="13800138000", resigned=True))
        cfg = make_cfg()
        with pytest.raises(LifecycleError, match="离职"):
            lifecycle.provision(fake_client, {"name": "李四", "mobile": "13800138000"}, cfg)

    def test_conflict_resigned_advises_restore(self, fake_client):
        fake_client.add(zh_user("ou_gone", mobile="13800138000", resigned=True, name="泷奈"))
        cfg = make_cfg()
        with pytest.raises(LifecycleError) as ei:
            lifecycle.provision(fake_client, {"name": "Takina", "mobile": "13800138000"}, cfg)
        text = str(ei.value)
        assert "恢复" in text
        assert "占用" in text

    def test_conflict_unjoin(self, fake_client):
        fake_client.add(zh_user("ou_pending", mobile="13800138000", unjoin=True))
        cfg = make_cfg()
        with pytest.raises(LifecycleError, match="待加入"):
            lifecycle.provision(fake_client, {"name": "李四", "mobile": "13800138000"}, cfg)

    def test_conflict_status_unknown_not_claimed_active(self, fake_client):
        # 未开通“获取用户受雇信息”字段权限时，get_user 不返回 status，不得误报“在职”
        user = {"open_id": "ou_no_status", "user_id": "u_no_status",
                "name": "泷奈", "mobile": "13800138000"}
        fake_client.add(user)
        cfg = make_cfg()
        with pytest.raises(LifecycleError) as ei:
            lifecycle.provision(fake_client, {"name": "Takina", "mobile": "13800138000"}, cfg)
        text = str(ei.value)
        assert "无法读取其账号状态" in text
        assert "在职成员" not in text
        assert fake_client.created == []

    def test_conflict_frozen(self, fake_client):
        fake_client.add(zh_user("ou_frozen", mobile="13800138000", frozen=True))
        cfg = make_cfg()
        with pytest.raises(LifecycleError, match="停用"):
            lifecycle.provision(fake_client, {"name": "李四", "mobile": "13800138000"}, cfg)


class TestFreezeFlow:
    def test_freeze_then_unfreeze(self, fake_client, monkeypatch):
        monkeypatch.setattr(lifecycle, "_pace_freeze", lambda: None)
        fake_client.add(zh_user("ou_target", mobile="13800138000"))
        cfg = make_cfg()

        p = lifecycle.freeze(fake_client, "ou_target")
        assert p.is_frozen is True
        assert fake_client.patch_calls[-1] == ("ou_target", {"is_frozen": True})

        p2 = lifecycle.unfreeze(fake_client, "ou_target")
        assert p2.is_frozen is False
        assert fake_client.patch_calls[-1] == ("ou_target", {"is_frozen": False})

    def test_resolve_by_mobile(self, fake_client):
        fake_client.add(zh_user("ou_target", mobile="13900000000", name="王五"))
        p = lifecycle.resolve_person(fake_client, "13900000000", "mobile")
        assert p.open_id == "ou_target"
        assert p.name == "王五"

    def test_resolve_not_found(self, fake_client):
        with pytest.raises(LifecycleError, match="未找到"):
            lifecycle.resolve_person(fake_client, "13700000000", "mobile")

    def test_deprovision(self, fake_client):
        fake_client.add(zh_user("ou_target"))
        cfg = make_cfg()
        lifecycle.deprovision(fake_client, "ou_target", cfg)
        assert fake_client.delete_calls == ["ou_target"]
        assert fake_client.users["ou_target"]["status"]["is_resigned"] is True
