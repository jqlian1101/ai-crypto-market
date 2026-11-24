"""币安访问代理配置的单元测试（PRD 29 章 / 32.3）。

**背景**：本机的 ``websockets`` 在自动读取 macOS 系统代理时会把 SOCKS 代理
解析成 ``socks5h://``，而 websockets 走 SOCKS 需要 ``python-socks`` 依赖
（未安装）——直接 ``ImportError``，WS 采集起不来。

因此代理必须**显式配置**，且只允许 http/https（走 CONNECT 隧道）。

本测试锁死：

- 代理地址的归一化与非法值的拒绝；
- httpx 客户端参数（配置代理 -> 显式 proxy；未配置 -> 确定性直连）；
- websockets 的 proxy 参数（未配置时必须显式传 ``None``，
  否则会退回系统代理自动探测，重新踩上 SOCKS 的坑）；
- ``MARKET_PROXY`` 在 Settings 层就被校验（启动期暴露，而非首次请求）。
"""

from __future__ import annotations

import pytest

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.proxy import (
    ALLOWED_PROXY_SCHEMES,
    httpx_client_kwargs,
    normalize_proxy_url,
    websockets_proxy,
)


def _settings(**overrides: object) -> Settings:
    """构造一个最小可用的 Settings（只填必填项）。"""
    kwargs: dict[str, object] = {
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "AUTH_ENABLED": False,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestNormalizeProxyUrl:
    """代理地址归一化。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://127.0.0.1:7892", "http://127.0.0.1:7892"),
            ("HTTP://127.0.0.1:7892", "http://127.0.0.1:7892"),
            ("  http://127.0.0.1:7892  ", "http://127.0.0.1:7892"),
            ("https://proxy.example.com:8443", "https://proxy.example.com:8443"),
        ],
    )
    def test_valid_urls_are_normalized(self, raw: str, expected: str) -> None:
        assert normalize_proxy_url(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_blank_means_direct_connection(self, raw: str | None) -> None:
        """留空 = 未配置 = 直连，而不是报错。"""
        assert normalize_proxy_url(raw) is None

    @pytest.mark.parametrize(
        "raw",
        [
            "127.0.0.1:7892",  # 省略协议
            "socks5://127.0.0.1:7892",  # SOCKS 未安装依赖
            "socks5h://127.0.0.1:7892",
            "socks4://127.0.0.1:7892",
            "ftp://127.0.0.1:7892",
            "http://",  # 缺主机
        ],
    )
    def test_invalid_urls_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError):
            normalize_proxy_url(raw)

    def test_allowed_schemes_are_http_only(self) -> None:
        """只允许 http/https——SOCKS 依赖不在清单内。"""
        assert frozenset({"http", "https"}) == ALLOWED_PROXY_SCHEMES


class TestHttpxClientKwargs:
    """httpx 客户端的代理参数。"""

    def test_configured_proxy_is_passed_explicitly(self) -> None:
        kwargs = httpx_client_kwargs("http://127.0.0.1:7892")
        assert kwargs["proxy"] == "http://127.0.0.1:7892"

    def test_trust_env_is_disabled_when_proxy_configured(self) -> None:
        """关掉 trust_env，避免系统代理 / NO_PROXY 绕开代理。"""
        kwargs = httpx_client_kwargs("http://127.0.0.1:7892")
        assert kwargs["trust_env"] is False

    def test_no_proxy_means_deterministic_direct_connection(self) -> None:
        """未配置代理时必须是确定性直连，不能留 proxy 键。"""
        kwargs = httpx_client_kwargs("")
        assert "proxy" not in kwargs
        assert kwargs["trust_env"] is False


class TestWebsocketsProxy:
    """websockets.connect 的 proxy 参数。"""

    def test_configured_proxy_is_returned(self) -> None:
        assert websockets_proxy("http://127.0.0.1:7892") == "http://127.0.0.1:7892"

    def test_unconfigured_returns_none_not_true(self) -> None:
        """**关键**：必须返回 None 而不是 True。

        websockets 的 ``proxy`` 默认值是 ``True``（自动读系统代理）。
        macOS 的 SOCKS 系统代理会被解析成 ``socks5h://``，
        而 websockets 走 SOCKS 需要未安装的 ``python-socks``——
        于是 WS 采集直接 ImportError。显式传 None 才能真正直连。
        """
        assert websockets_proxy("") is None
        assert websockets_proxy(None) is None


class TestSettingsMarketProxy:
    """``MARKET_PROXY`` 在 Settings 层校验（PRD 32.3：启动期暴露问题）。"""

    def test_default_is_blank(self) -> None:
        assert _settings().MARKET_PROXY == ""

    def test_valid_value_is_accepted(self) -> None:
        assert _settings(MARKET_PROXY="http://127.0.0.1:7892").MARKET_PROXY == (
            "http://127.0.0.1:7892"
        )

    @pytest.mark.parametrize("raw", ["127.0.0.1:7892", "socks5://127.0.0.1:7892"])
    def test_invalid_value_fails_fast(self, raw: str) -> None:
        """非法值必须阻止进程启动，而不是等到第一次请求。"""
        with pytest.raises(ValueError, match="MARKET_PROXY"):
            _settings(MARKET_PROXY=raw)
