#!/usr/bin/env python3
"""APScheduler 守护进程启动脚本。

用法：
    python scripts/run_scheduler_daemon.py

或通过 supervisor / systemd 保证常驻运行。

**配置示例**（supervisor）：

    [program:aicrypto_scheduler]
    command=python scripts/run_scheduler_daemon.py
    directory=/path/to/aicrypto
    autostart=true
    autorestart=true
    redirect_stderr=true
    stdout_logfile=/var/log/aicrypto_scheduler.log
    environment=PATH=/path/to/venv/bin,HOME=/root
"""

import sys

from aicrypto.services.scheduler_daemon import main

if __name__ == "__main__":
    sys.exit(main())
