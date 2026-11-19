"""离线导出 API 契约，无需运行服务器或连接数据库。"""

import json
from pathlib import Path

from aicrypto.api.app import create_app
from aicrypto.config.settings import Settings


def main() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://schema:schema@localhost/schema",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
    )
    target = Path(__file__).resolve().parents[1] / "apps/web/src/api/generated/openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
