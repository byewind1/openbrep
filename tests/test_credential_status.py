"""卡01：无副作用凭据状态纯函数测试。

覆盖：五种 location 各一例；池条目 form=pool 且 select 零调用；
明文掩码 / ${ENV} 原样 / 无 key 空串；env_ref 不可解析时 resolvable=False。
"""

import pytest

from openbrep.config import GDLAgentConfig
from openbrep.credential_pool import CredentialPool
from openbrep.workbench.credential_status import (
    credential_status,
    is_env_ref,
    mask_secret,
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for name in (
        "ZAI_API_KEY", "ZHIPU_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY", "RELAY_KEY_VAR",
    ):
        monkeypatch.delenv(name, raising=False)


def _config(**llm_overrides) -> GDLAgentConfig:
    config = GDLAgentConfig()
    for key, value in llm_overrides.items():
        setattr(config.llm, key, value)
    return config


def test_location_entry_direct_key():
    entry = {"name": "relay", "api_key": "sk-live-1234567890"}
    status = credential_status(entry, _config(api_key="sk-top"))

    assert status == {"location": "entry", "form": "direct", "resolvable": True}


def test_location_entry_env_ref():
    entry = {"name": "relay", "api_key": "${RELAY_KEY_VAR}"}
    status = credential_status(entry, _config(api_key="sk-top"))

    assert status["location"] == "entry"
    assert status["form"] == "env_ref"
    assert status["resolvable"] is False  # 环境变量未设置

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RELAY_KEY_VAR", "sk-from-env")
        assert credential_status(entry, _config())["resolvable"] is True


def test_location_provider_keys_for_official_named_entry():
    entry = {"name": "zhipu", "api": "https://open.bigmodel.cn/api/paas/v4"}
    config = _config(provider_keys={"zhipu": "sk-official"})
    status = credential_status(entry, config)

    assert status == {"location": "provider_keys", "form": "direct", "resolvable": True}


def test_location_top_level():
    entry = {"name": "relay"}
    status = credential_status(entry, _config(api_key="test-top-level"))

    assert status == {"location": "top_level", "form": "direct", "resolvable": True}


def test_location_env_for_official_named_entry():
    entry = {"name": "deepseek"}
    status = credential_status(entry, _config())

    assert status["location"] == "none"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEEPSEEK_API_KEY", "sk-env")
        status = credential_status(entry, _config())

    assert status["location"] == "env"
    assert status["resolvable"] is True


def test_location_none_without_any_credential():
    entry = {"name": "relay", "api_key": ""}
    status = credential_status(entry, _config())

    assert status == {"location": "none", "form": "direct", "resolvable": False}


def test_pool_entry_reports_pool_form_without_selecting(monkeypatch):
    entry = {
        "name": "relay",
        "credentials": [
            {"id": "c1", "value": "sk-pool-1"},
            {"id": "c2", "api_key": "sk-pool-2"},
        ],
    }
    select_calls: list = []
    monkeypatch.setattr(
        CredentialPool,
        "select",
        lambda self, scope="default", **kwargs: select_calls.append(scope),
    )

    status = credential_status(entry, _config())

    assert status["location"] == "entry"
    assert status["form"] == "pool"
    assert status["resolvable"] is True
    assert select_calls == []  # 绝不 select：不创建租约、不改池状态


def test_pool_entry_all_empty_refs_not_resolvable():
    entry = {
        "name": "relay",
        "credentials": [{"id": "c1", "env": "MISSING_VAR_XYZ"}],
    }
    status = credential_status(entry, _config())

    assert status["form"] == "pool"
    assert status["resolvable"] is False


def test_mask_secret_rules():
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("sk-1234567890abcdef") == "sk-…cdef"
    assert mask_secret("${RELAY_KEY_VAR}") == "${RELAY_KEY_VAR}"  # env 名不是秘密
    assert mask_secret("short") == "•••"  # 过短明文整体掩码


def test_is_env_ref():
    assert is_env_ref("${VAR_1}") is True
    assert is_env_ref("sk-plain") is False
    assert is_env_ref("prefix ${VAR}") is False
    assert is_env_ref("") is False


def test_status_never_mutates_entry_or_config():
    entry = {"name": "relay", "api_key": "${RELAY_KEY_VAR}"}
    config = _config(api_key="sk-top")
    entry_before = dict(entry)

    credential_status(entry, config)

    assert entry == entry_before
    assert config.llm.api_key == "sk-top"


if __name__ == "__main__":
    pytest.main([__file__])
