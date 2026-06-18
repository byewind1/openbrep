from scripts.secret_precommit_check import blocked_path_reason, secret_line_reason


def test_blocks_personal_config_files():
    assert blocked_path_reason("config.toml")
    assert blocked_path_reason(".env")
    assert blocked_path_reason(".worktrees/react-workbench/config.toml")


def test_allows_example_config_files():
    assert blocked_path_reason("config.example.toml") is None
    assert blocked_path_reason(".env.example") is None


def test_blocks_realistic_secret_assignments():
    assert secret_line_reason("README.md", 'api_key = "' + "sk-live-secret-value" + '"')
    assert secret_line_reason("settings.toml", 'token = "' + "abcdefghijklmno" + '"')


def test_allows_placeholder_secret_assignments():
    assert secret_line_reason("README.md", 'api_key = "YOUR_API_KEY"') is None
    assert secret_line_reason("tests/test_config.py", 'api_key = "test-key"') is None
    assert secret_line_reason("config.example.toml", 'api_key = "' + "sk-live-secret-value" + '"') is None
