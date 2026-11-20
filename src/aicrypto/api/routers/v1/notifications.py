"""推送测试接口（PRD 15.7）。

- ``channel`` 必填，缺失返回 400 ``NOTIFY_CHANNEL_REQUIRED``；
- 当前仅实现 ``feishu``；其余渠道返回 400 ``NOTIFY_CHANNEL_UNSUPPORTED``，不静默降级。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aicrypto.api.auth import require_auth
from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.cache import get_redis
from aicrypto.infrastructure.database.session import get_db
from aicrypto.infrastructure.notifications import feishu
from aicrypto.infrastructure.notifications.service import deliver_test

router = APIRouter(prefix="/notifications", dependencies=[Depends(require_auth)])

SUPPORTED_CHANNELS = {feishu.CHANNEL}


@router.post("/test", summary="发送推送测试消息")
async def send_test_notification(
    db: Annotated[Session, Depends(get_db)],
    channel: Annotated[str | None, Query(description="渠道，当前支持 feishu")] = None,
) -> dict[str, Any]:
    if not channel:
        raise AICryptoError(ErrorCode.NOTIFY_CHANNEL_REQUIRED)
    if channel not in SUPPORTED_CHANNELS:
        raise AICryptoError(
            ErrorCode.NOTIFY_CHANNEL_UNKNOWN,
            message=f"渠道 {channel} 尚未实现",
            details={"channel": channel},
        )

    settings = get_settings()
    redis = get_redis(settings)
    outcome = await deliver_test(db, redis, settings=settings)
    db.commit()

    return {
        "status": outcome.status,
        "notification_event_id": outcome.notification_event_id,
        "error": outcome.error,
    }


__all__ = ["router"]
