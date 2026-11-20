"""异动扫描 + 自动投递的单次入口，供计划任务（Windows 任务计划程序 / cron）周期调用。

用法（从项目根目录运行）::

    .venv\\Scripts\\python.exe scripts\\run_anomaly_scan.py

退出码：

- 0：扫描完成（含投递）
- 2：未获取到调度锁（上一轮仍在运行），属正常跳过，不视为故障
- 1：扫描或投递异常，需要排查
"""

from __future__ import annotations

import sys

from aicrypto.services.anomaly_runner import run_anomaly_scan_once

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_SKIPPED_LOCKED = 2


def main() -> int:
    try:
        result = run_anomaly_scan_once()
    # 入口层兜底：异常必须打印并以非零码退出，不得吞掉（36.7）。
    except Exception as exc:
        print(f"anomaly_scan_failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return EXIT_FAILED

    if result.status == "SKIPPED_LOCKED":
        print("anomaly_scan_skipped: 上一轮仍在运行", flush=True)
        return EXIT_SKIPPED_LOCKED

    for outcome in result.outcomes:
        print(
            f"scan {outcome.symbol}: detected={outcome.detected} "
            f"persisted={outcome.persisted} suppressed={outcome.suppressed} "
            f"errors={len(outcome.errors)}",
            flush=True,
        )
    print(f"anomaly_scan_complete: duration_ms={round(result.duration_ms)}", flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
