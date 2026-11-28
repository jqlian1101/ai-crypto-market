# K 线数据同步说明

本文说明 `market_candles` 表的数据从哪里来、如何同步、如何补数与校验。

## 1. 数据来源

`market_candles.source` 取值只有三种（见 `infrastructure/database/models/market.py`）：

| source | 含义 | 产生方式 |
|---|---|---|
| `WS` | 实时 WebSocket 推送的已收盘 K 线 | `scripts/run_market_collector.py` 常驻采集 |
| `REST` | 通过 REST 拉取的 K 线 | 采集断线后的恢复（`market_recovery.py`） |
| `REST_BACKFILL` | 历史补数写入 | `scripts/backfill_market.py` |

主键为 `pk_market_candles`，同一 K 线只保留一行。REST 数据优先：主键冲突时用 REST 数据覆盖 WS 数据（PRD 29.4）。

## 2. 同步范围（默认配置）

来自 `config/settings.py`，可用环境变量或 `.env` 覆盖：

| 配置项 | 默认值 |
|---|---|
| `MARKET_SYMBOLS` | `BTCUSDT,ETHUSDT` |
| `MARKET_TYPES` | `SPOT,USDM_FUTURES` |
| `MARKET_TIMEFRAMES` | `5m,15m,1h,4h` |
| `CLOCK_SYNC_INTERVAL_SECONDS` | `600`（最小 60） |
| `MARKET_BACKFILL_ENABLED` | `true` |
| `MARKET_BACKFILL_MAX_KLINES` | `1000` |

Binance 端点：现货为 `https://api.binance.com/api/v3/klines`（见 `infrastructure/binance_rest.py`）。

## 3. 三条同步链路

### 3.1 实时采集（WS）

- 入口：`python scripts/run_market_collector.py`
- 可用 `--market-types USDM_FUTURES` 只采永续。
- 采集器定期与交易所校时，间隔由 `CLOCK_SYNC_INTERVAL_SECONDS` 决定。
- 只落库已收盘 K 线。

### 3.2 断线恢复（自动）

- 实现：`services/market_recovery.py`，由 `services/market_collector.py` 调用。
- 检测到缺口后，按缺口的时间窗口通过 REST 补齐，单次最多 `MAX_RECOVERY_BARS = 5000` 根。
- 超过上限的更长断线，需要手动运行 `backfill_market.py`。
- 恢复动作记为 `BACKFILLED_FROM_REST`。
- 每次同步的状态写入 `market_data_sync_runs`，状态取值为 `RUNNING` / `SUCCESS` / `FAILED` / `PARTIAL`。

### 3.3 历史补数（手动）

- 入口：`python scripts/backfill_market.py`

参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--symbols` | 交易对，逗号分隔 | `MARKET_SYMBOLS` |
| `--market-types` | `SPOT` / `USDM_FUTURES` | `MARKET_TYPES` |
| `--timeframes` | 周期，逗号分隔 | `MARKET_TIMEFRAMES` |
| `--days` | 从当前时间往前补的天数，最小 1 | `7` |
| `--dry-run` | 只拉取与校验，不写库 | 关闭 |

执行逻辑（见 `scripts/backfill_market.py`）：

1. 时间窗口：`end` 对齐到当前周期起点，`start = now - days`，并对齐到周期边界。
2. 分页拉取：每页最多 1000 根，按 `openTime` 升序，请求间隔 0.25 秒。
3. 只保留已收盘 K 线，未收盘的丢弃。
4. OHLC 校验失败的行被拒绝并计入失败数。
5. 分批 upsert（每批 `UPSERT_BATCH_SIZE = 2000` 行），幂等，可重复执行。
6. 任一组合失败则退出码为 1，否则为 0。

注意：`--days` 只能指定"往前推多少天"，没有起始日期参数。要补指定起点的数据，需要多补一些，再删除起点之前的行。

## 4. 校验

- `python scripts/verify_market_candles.py`：K 线校验。
- 人工核对：按交易对和周期统计行数、首根、末根，并检查 `count(*)` 与 `count(distinct open_time)` 是否一致。

示例 SQL：

```sql
select symbol, timeframe, count(*) as rows,
       min(open_time) at time zone 'Asia/Shanghai' as first_local,
       max(open_time) at time zone 'Asia/Shanghai' as last_local,
       count(distinct open_time) as distinct_bars
from market_candles
where market_type = 'SPOT' and symbol in ('BTCUSDT', 'ETHUSDT')
group by 1, 2 order by 1, 2;
```

## 5. 常用命令

| 目的 | 命令 |
|---|---|
| 启动 K 线采集 | `python scripts/run_market_collector.py` |
| 只采永续 | `python scripts/run_market_collector.py --market-types USDM_FUTURES` |
| 补数（干跑） | `python scripts/backfill_market.py --market-types SPOT --symbols BTCUSDT --timeframes 1h --days 7 --dry-run` |
| 补数（写库） | `python scripts/backfill_market.py --market-types SPOT --symbols BTCUSDT --timeframes 1h --days 7` |
| K 线校验 | `python scripts/verify_market_candles.py` |

## 6. 实操注意事项

- **分周期运行**：大周期一次补数量不大，但 `5m` 每个交易对约 9.5 万行（约 332 天），建议按交易对、按周期分开跑，便于定位问题。
- **卡住的排查**：2026-11-28 的一次全量补数（4 个周期 × 2 个交易对一起跑）在写库阶段卡住超过 30 分钟，数据库中无写入。改为分批 upsert 后恢复正常。若再次出现，先检查 `pg_stat_activity` 与进程 CPU，再决定是否终止。
- **截断起点**：用 `--days` 补到指定起点后，需删除起点之前由本次补数写入的行（`source = 'REST_BACKFILL'`），删除前先确认该时段原本没有数据。
- **本地端口**：macOS 环境下 PostgreSQL 为 `15432`，Redis 为 `16379`，见 `.env`。

## 7. 关联文件

- `scripts/run_market_collector.py`
- `scripts/backfill_market.py`
- `scripts/verify_market_candles.py`
- `src/aicrypto/services/market_collector.py`
- `src/aicrypto/services/market_recovery.py`
- `src/aicrypto/services/market_ingest.py`
- `src/aicrypto/infrastructure/binance_rest.py`
- `src/aicrypto/infrastructure/database/models/market.py`
- `src/aicrypto/config/settings.py`
