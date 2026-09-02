"""命令解析器单元测试。"""
from __future__ import annotations

import pytest

from app.parser import CommandError, classify_identity, normalize_identity, parse_command


class TestClassify:
    def test_mobile(self):
        assert classify_identity("13800138000") == "mobile"
        assert classify_identity("+8613800138000") == "mobile"
        assert classify_identity("+86 138 0013 8000") == "mobile"
        assert classify_identity("13912345678") == "mobile"

    def test_intl_mobile(self):
        assert classify_identity("+14145551234") == "mobile"

    def test_email(self):
        assert classify_identity("zhangsan@corp.com") == "email"
        assert classify_identity("a.b+c@sub.corp.com.cn") == "email"

    def test_open_id(self):
        assert classify_identity("ou_7dab8a3d3cdcc9da") == "open_id"
        assert classify_identity("on_123456") == "union_id"

    def test_unknown(self):
        assert classify_identity("张三") == "unknown"
        assert classify_identity("abc123") == "unknown"

    def test_normalize(self):
        assert normalize_identity("+86 138-0013-8000") == "+8613800138000"


class TestProvision:
    def test_basic(self):
        cmd = parse_command("开通 张三 13800138000")
        assert cmd.action == "provision"
        assert cmd.params["name"] == "张三"
        assert cmd.params["mobile"] == "13800138000"

    def test_full_fields(self):
        raw = "请帮我开通：李四 13900000000 lisi@corp.com 部门=od-abc123 工号=10086 职务=工程师 谢谢"
        cmd = parse_command(raw)
        assert cmd.action == "provision"
        assert cmd.params["name"] == "李四"
        assert cmd.params["mobile"] == "13900000000"
        assert cmd.params["email"] == "lisi@corp.com"
        assert cmd.params["dept_open_id"] == "od-abc123"
        assert cmd.params["employee_no"] == "10086"
        assert cmd.params["job_title"] == "工程师"

    def test_email_only_allowed(self):
        cmd = parse_command("开通 王五 wangwu@corp.com")
        assert cmd.action == "provision"
        assert cmd.params["name"] == "王五"
        assert cmd.params["email"] == "wangwu@corp.com"

    def test_intl_mobile_normalized(self):
        cmd = parse_command("开通 张三 +86 138 0013 8000")
        assert cmd.params["mobile"] == "+8613800138000"

    def test_missing_name(self):
        with pytest.raises(CommandError, match="姓名"):
            parse_command("开通 13800138000")

    def test_missing_contact(self):
        with pytest.raises(CommandError, match="手机号"):
            parse_command("开通 张三")

    def test_bad_department(self):
        with pytest.raises(CommandError, match="od-"):
            parse_command("开通 张三 13800138000 部门=研发部")

    def test_employee_type_zh(self):
        cmd = parse_command("开通 张三 13800138000 类型=实习")
        assert cmd.params["employee_type"] == "2"

    def test_unknown_keyword(self):
        with pytest.raises(CommandError):
            parse_command("开通 张三 13800138000 职位等级=9")


class TestIdentityOps:
    @pytest.mark.parametrize("verb", ["停用", "冻结", "回收", "回收账号", "停用账号"])
    def test_freeze_aliases(self, verb):
        cmd = parse_command(f"{verb} 13800138000")
        assert cmd.action == "freeze"
        assert cmd.target == "13800138000"
        assert cmd.identity == "mobile"

    @pytest.mark.parametrize("verb", ["启用", "解冻", "恢复账号"])
    def test_unfreeze_aliases(self, verb):
        cmd = parse_command(f"{verb} zhangsan@corp.com")
        assert cmd.action == "unfreeze"
        assert cmd.identity == "email"

    @pytest.mark.parametrize("verb", ["删除", "彻底删除", "注销", "离职", "回收-彻底删除"])
    def test_delete_aliases(self, verb):
        cmd = parse_command(f"{verb} ou_abcdef123456")
        assert cmd.action == "delete"
        assert cmd.identity == "open_id"

    def test_delete_with_note(self):
        cmd = parse_command("删除 zhangsan@corp.com 员工离职交接")
        assert cmd.action == "delete"
        assert cmd.params["note"] == "员工离职交接"

    def test_query(self):
        cmd = parse_command("查询 13800138000")
        assert cmd.action == "query"

    def test_group_mention_stripped(self):
        cmd = parse_command("@_user_1 停用 13800138000")
        assert cmd.action == "freeze"

    def test_missing_target(self):
        with pytest.raises(CommandError, match="目标账号"):
            parse_command("停用")

    def test_unknown_target(self):
        with pytest.raises(CommandError, match="无法识别账号"):
            parse_command("停用 马冬梅")

    def test_unknown_command(self):
        with pytest.raises(CommandError):
            parse_command("今天晚上吃什么")

    def test_help_and_whoami(self):
        assert parse_command("帮助").action == "help"
        assert parse_command("whoami").action == "whoami"
        assert parse_command("日志 20").action == "logs"
        assert parse_command("日志 20").params.get("count") == "20"
        assert parse_command("日志").params.get("count") is None
