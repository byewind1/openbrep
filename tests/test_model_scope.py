from openbrep.model_scope import model_enabled


def test_path_scoped_allow_and_provider_deny(tmp_path):
    enabled = [{"path": str(tmp_path), "models": ["openai/*"]}]
    assert model_enabled("openai/gpt", provider="openai", enabled_models=enabled, cwd=tmp_path)
    assert not model_enabled(
        "deepseek-chat", provider="deepseek", enabled_models=enabled, cwd=tmp_path
    )
    denied = [{"path": str(tmp_path), "providers": ["openai"]}]
    assert not model_enabled(
        "openai/gpt", provider="openai", disabled_providers=denied, cwd=tmp_path
    )


def test_global_rules_remain_global():
    assert model_enabled("anything", provider="x", enabled_models=["*"])
    assert not model_enabled("anything", provider="x", disabled_providers=["x"])
