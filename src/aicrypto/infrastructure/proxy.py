"""行情访问的网络代理配置（PRD 29 章）。

**为什么需要这一层**：项目里币安访问分两条腿——

- **REST** 走 ``httpx``。``httpx`` 默认 ``trust_env=True``，会自动读取
  macOS / Windows 的系统代理，所以在本机上「看起来是走了代理」的。
  但这带来两个问题：行为**隐式**（代码里看不出走了代理），且一旦
  ``trust_env=False`` 或环境里出现 ``NO_PROXY`` 就会静默直连。
- **WS** 走 ``websockets``。它的 ``connect(proxy=True)`` 也会读系统代理，
  但 macOS 把 SOCKS 代理暴露为 ``socks://127.0.0.1:7892``，Python 的
  ``urllib.request.getproxies()`` 会把它规范成 ``socks5h://``，
  而 websockets 走 SOCKS 需要额外安装 ``python-socks``——**未安装时会直接
  ``ImportError``**，WS 采集根本起不来。

因此这里把代理**显式化**：由配置项 ``MARKET_PROXY`` 统一决定，
REST 与 WS 共用同一份地址，且**只允许 http/https 代理**（走 CONNECT 隧道），
从根上避开 SOCKS 依赖问题。

配置格式：``scheme://host:port``，例如 ``http://127.0.0.1:7892``。
留空表示直连（此时 REST 关闭 ``trust_env``，WS 显式禁用代理）。

本模块**不依赖** :mod:`aicrypto.config.settings`——代理地址的解析是纯函数，
与配置加载解耦，避免 ``settings`` 与 ``infrastructure`` 形成循环导入。
调用方传字符串即可。
"""

from __future__ import annotations

from typing import Any, Final

#: 允许的代理协议。``socks5``/``socks5h`` 被排除：
#: websockets 走 SOCKS 需要 ``python-socks`` 依赖，而 httpx 走 SOCKS
#: 需要 ``socksio``，两者都不在依赖清单里，且与本项目的 HTTP 代理用法冲突。
ALLOWED_PROXY_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

#: 默认代理地址：与本机 MGNB（Clash/Mihomo 内核）的混合端口一致。
DEFAULT_PROXY_URL: Final[str] = "http://127.0.0.1:7892"


def normalize_proxy_url(raw: str | None) -> str | None:
    """归一化代理地址；无效或留空时返回 ``None``（表示直连）。

    允许的写法：``http://host:port`` / ``https://host:port``。
    ``host:port`` 这种省略协议的写法**不接受**——默认协议猜测容易踩坑，
    显式写全更安全。
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if "://" not in value:
        raise ValueError(
            f"MARKET_PROXY 格式非法: {raw!r}，应为 'http://host:port' 或 'https://host:port'"
        )
    scheme, _, rest = value.partition("://")
    scheme = scheme.lower()
    if scheme not in ALLOWED_PROXY_SCHEMES:
        raise ValueError(
            f"MARKET_PROXY 协议不支持: {scheme!r}，仅支持 {sorted(ALLOWED_PROXY_SCHEMES)}"
        )
    if not rest.strip():
        raise ValueError(f"MARKET_PROXY 缺少主机地址: {raw!r}")
    return f"{scheme}://{rest.strip()}"


def httpx_client_kwargs(proxy: str | None) -> dict[str, Any]:
    """构造 ``httpx`` 客户端的关键字参数，使请求走配置的代理。

    - 配置了代理：显式传 ``proxy=``，并关闭 ``trust_env``——
      避免系统代理 / ``NO_PROXY`` 规则把币安请求绕开代理；
    - 未配置代理：``trust_env=False``，保证**确定性地直连**，
      不受本机系统代理设置影响。

    ``httpx`` 的 ``trust_env`` 还会读取 ``SSL_CERT_FILE`` 等环境变量，
    这里关闭它可能会影响自定义 CA 的场景——但本项目访问的是公共行情接口，
    使用系统默认信任链即可。
    """
    url = normalize_proxy_url(proxy)
    if url is None:
        return {"trust_env": False}
    return {"proxy": url, "trust_env": False}


def websockets_proxy(proxy: str | None) -> str | None:
    """返回传给 ``websockets.connect(proxy=...)`` 的值。

    - 配置了代理：返回代理地址，走 HTTP CONNECT 隧道；
    - 未配置代理：返回 ``None``，**显式禁用**代理自动探测。
      ``websockets`` 的 ``proxy`` 参数默认值是 ``True``（自动读系统代理），
      必须显式传 ``None`` 才能真正直连——否则会踩上 macOS SOCKS 的坑。
    """
    return normalize_proxy_url(proxy)


__all__ = [
    "ALLOWED_PROXY_SCHEMES",
    "DEFAULT_PROXY_URL",
    "httpx_client_kwargs",
    "normalize_proxy_url",
    "websockets_proxy",
]
