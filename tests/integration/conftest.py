"""集成测试的数据库连接串解析（真库测试的统一入口）。

**为什么需要这个文件**

集成测试的连接串一律通过 ``os.getenv("ANALYSIS_TEST_DATABASE_URL")`` 读取。
但项目本地的 ``.env`` 是由 **pydantic-settings** 读取的——
它把文件内容灌进 ``Settings``，**不会**写回 ``os.environ``。
于是「在 ``.env`` 里配好测试库」这件事对 ``os.getenv()`` 是**无效**的：
开发者明明配了，测试却仍然全部 skip。

CI 之所以没这个问题，是因为 CI 的 ``env:`` 段直接注入**进程环境变量**。
本地没有这一层，缺口就在这里。

**解析优先级**

1. 进程环境变量 ``ANALYSIS_TEST_DATABASE_URL`` / ``ANOMALY_TEST_DATABASE_URL``
   （CI 走这条；也允许本地临时覆盖）；
2. 项目根目录 ``.env`` 的 ``DATABASE_URL``；
3. 内置默认值 ``postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:5432/aicrypto``
   （与 ``docker-compose.yml`` 的容器一致）。

**为什么不额外建一个 ``aicrypto_test`` 库**

CI 的 ``ANALYSIS_TEST_DATABASE_URL`` 与应用的 ``DATABASE_URL`` **指向同一个库**，
这是本仓库既有的既定口径。集成测试本身用「事务级隔离 + 测试结束回滚」保证不污染数据，
个别直接 ``DELETE`` 的语句也都按 ``model_id`` / ``agent_name`` 精确限定范围。

**注意**

本文件只设置这两个键，**不动**其它环境变量——
``tests/conftest.py`` 的 ``_isolated_env`` 负责屏蔽 ``Settings`` 的同名环境变量，
两者不冲突（这两个键不是 ``Settings`` 的字段）。
"""

from __future__ import annotations

import os
import pathlib

TEST_DB_URL_KEYS = (
    "ANALYSIS_TEST_DATABASE_URL",
    "ANOMALY_TEST_DATABASE_URL",
)

DEFAULT_TEST_DB_URL = "postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:5432/aicrypto"

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read_dotenv_database_url() -> str | None:
    """从项目根目录的 ``.env`` 里读 ``DATABASE_URL``。

    只做最朴素的解析——``.env`` 是本项目自己生成的扁平 ``KEY=VALUE`` 文件，
    不需要引入 ``python-dotenv``（``pydantic-settings`` 已经在依赖里，
    但它不会把值导出到 ``os.environ``，这里只能自己读）。
    """
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return None
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "DATABASE_URL":
            url = value.strip()
            return url or None
    return None


def _resolve_test_database_url() -> str:
    """按优先级解析测试库连接串。"""
    for key in TEST_DB_URL_KEYS:
        url = os.environ.get(key)
        if url:
            return url

    from_dotenv = _read_dotenv_database_url()
    if from_dotenv:
        return from_dotenv

    return DEFAULT_TEST_DB_URL


def _is_reachable(url: str) -> bool:
    """检查数据库是否真的连得上。

    连不上时**不设置**环境变量——让集成测试按既有约定「跳过」，
    而不是变成一堆连接错误（ERROR）。这与「未配置测试库就跳过」
    是同一条纪律：**测试不该因为环境缺失而失败**。

    **连接超时必须设得很短**：探测端口不通时 psycopg 会一直等
    操作系统的 TCP 连接超时（本机实测约 140 秒），
    整个测试会话会被这一件事拖住。``connect_timeout`` 把它压到 2 秒。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError

    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except SQLAlchemyError:
        return False
    except Exception:  # 任何连接层异常都视为「不可达」
        return False
    finally:
        if engine is not None:
            engine.dispose()


# 在**导入期**写入进程环境变量：
# 集成测试的各个 fixture 在**收集完成后**才读取这些键，
# 而 pytest 的导入阶段早于任何 fixture 执行，因此这里设置得足够早。
_resolved_url = _resolve_test_database_url()
if _is_reachable(_resolved_url):
    for _key in TEST_DB_URL_KEYS:
        os.environ.setdefault(_key, _resolved_url)
else:
    # 连不上就不设——测试按「未配置测试库」跳过，而不是报错。
    for _key in TEST_DB_URL_KEYS:
        os.environ.pop(_key, None)
