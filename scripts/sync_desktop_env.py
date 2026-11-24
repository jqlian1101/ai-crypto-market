"""把桌面的 env 配置合并进项目 .env / .env.local。

规则：

1. 以项目 ``.env.example`` 为**基底**（PRD 32.2 要求包含全部配置项）；
2. 叠加桌面 ``.env`` 的值；
3. 桌面 ``.env.local`` 单独写入项目 ``.env.local``——
   pydantic-settings 的 ``env_file=(".env", ".env.local")`` 顺序加载，
   后者优先级更高，因此 LLM 云端配置能覆盖 ``.env`` 里的 ``LLM_PROVIDER_DEFAULT``；
4. 连接串端口改写为本项目 Docker 容器的实际端口（15432 / 16379），
   桌面的 5432 / 6379 是 Homebrew 的实例，指向的是别的数据库。
"""

from __future__ import annotations

import pathlib
import re
import sys

PROJECT = pathlib.Path("/Users/lian/Documents/workspace/ai-crypto-market")
DESKTOP = pathlib.Path("/Users/lian/Desktop/env")


def parse(path: pathlib.Path) -> dict[str, str]:
    """解析 env 文件，返回 key -> value（保留注释行由调用方另行处理）。"""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value.strip()
    return out


def render(base_text: str, values: dict[str, str]) -> str:
    """按基底文件的顺序渲染，空值保持留空（PRD 32.2）。"""
    lines = base_text.splitlines()
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    # 基底里没有但桌面有的键，补在末尾
    extra = {k: v for k, v in values.items() if k not in seen}
    if extra:
        out.append("")
        out.append("# ---------- 来自桌面 env 的补充项 ----------")
        for key, value in extra.items():
            out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def main() -> int:
    base_text = (PROJECT / ".env.example").read_text(encoding="utf-8")
    base = parse(PROJECT / ".env.example")
    desktop_env = parse(DESKTOP / ".env")
    desktop_local = parse(DESKTOP / ".env.local")

    merged = dict(base)
    merged.update(desktop_env)

    # 端口改写：桌面配置指向 Homebrew 实例，本项目用 Docker 容器
    merged["DATABASE_URL"] = re.sub(r"@[^:/]+:5432/", "@127.0.0.1:15432/", merged["DATABASE_URL"])
    # Redis URL 形如 redis://host:port/db（无 @ 段），单独处理
    merged["REDIS_URL"] = re.sub(r"(redis://[^:/]+):6379/", r"\1:16379/", merged["REDIS_URL"])
    # BACKUP_DIR 用相对路径，避免指向不存在的绝对路径
    merged["BACKUP_DIR"] = merged.get("BACKUP_DIR", "./data/backups")

    (PROJECT / ".env").write_text(render(base_text, merged), encoding="utf-8")

    # .env.local：只放覆盖项
    local_lines = [
        "# 由 /Users/lian/Desktop/env/.env.local 同步而来（PRD 32.1：优先级高于 .env）",
        "",
    ]
    for key, value in desktop_local.items():
        local_lines.append(f"{key}={value}")
    (PROJECT / ".env.local").write_text("\n".join(local_lines) + "\n", encoding="utf-8")

    print(f".env       写入完成（{len(merged)} 个键）")
    print(f".env.local 写入完成（{len(desktop_local)} 个键）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
