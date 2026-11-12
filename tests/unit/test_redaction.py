"""日志脱敏测试（PRD 第 32.6 / 36.3 必测清单：日志不含密钥明文）。"""

from __future__ import annotations

from aicrypto.common.redaction import mask_config_value, mask_url, mask_value, redact


class TestMasking:
    def test_mask_value_keeps_edges(self) -> None:
        assert mask_value("abcdefghij") == "abcd****ghij"

    def test_mask_value_short(self) -> None:
        assert mask_value("abc") == "****"

    def test_mask_empty(self) -> None:
        assert mask_value("") == ""

    def test_mask_url_hides_webhook_token(self) -> None:
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123def456"
        masked = mask_url(url)
        assert "abc123def456" not in masked
        assert masked.startswith("https://open.feishu.cn")

    def test_mask_config_value_by_key(self) -> None:
        assert mask_config_value("AUTH_TOKEN", "secret-value") == "***"
        assert mask_config_value("FEISHU_WEBHOOK_URL", "https://x.com/hook/abc") != "***"

    def test_mask_config_value_marks_token_key(self) -> None:
        assert mask_config_value("WECOM_CORP_SECRET", "corp") == "***"
        assert mask_config_value("LLM_CLOUD_API_KEY", "sk-xxx") == "***"


class TestRedact:
    def test_key_value_pattern(self) -> None:
        text = "calling api with api_key=sk-supersecret and url https://x.com/a"
        result = redact(text)
        assert "sk-supersecret" not in result

    def test_corpsecret_masked(self) -> None:
        result = redact("corpsecret=abc123def")
        assert "abc123def" not in result

    def test_plain_text_unchanged(self) -> None:
        assert redact("hello world") == "hello world"

    def test_empty_string(self) -> None:
        assert redact("") == ""
