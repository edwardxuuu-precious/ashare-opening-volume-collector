# 行情采集运行说明

安装 Python 依赖：`python3 -m venv .venv`，然后 `.venv/bin/pip install -r requirements.txt`。

验证一批股票：

```bash
.venv/bin/python backend/scripts/collect.py --limit 30 --days 60 --out var/data/initial-batch
```

全市场分批运行，每次新采集或重试最多 250 只；同一天继续运行相同命令可接着做：

```bash
.venv/bin/python backend/scripts/collect.py --full --max-stocks 250 --days 60 --resume --out var/data/full-market
```

一次遍历全市场：

```bash
.venv/bin/python backend/scripts/collect.py --full --days 60 --resume --out var/data/full-market
```

## 涨跌幅与振幅回填

新采集的每条记录直接带 `pctChange`（当日收盘涨跌幅）与 `amplitude`（当日振幅），由同一日线按前收计算。已保存的历史记录用回填脚本补齐，缓存断点可续跑：

```bash
# 沪深用腾讯日线（窗口请求、速度快），北交所用新浪日线；数值与新浪不复权一致
.venv/bin/python backend/scripts/backfill_quotes.py --out var/data --cache var/work/quote-backfill-cache.json
```

回填只增补两列并把可计算的记录升级到当前 `verificationVersion`，不改动成交量、比例和状态；缺失前收或最高最低时两列留空。脚本占用输出目录的采集锁，与采集器互斥。

## 输出含义

- `manifest.json`：可查询日期、每日期有效/缺失数量。
- `YYYY-MM-DD.json`：按当次目标名单列出的全部股票；未采集项成交量和比例均为 `null`、状态 `missing`。
- `universe.json`：采集当天的当前 A 股名单；不是历史成分股档案。
- `checkpoint/<code>.json`：逐只原子保存的结果和采集成功/失败状态，不发往公开网页。
- `run-status.json`：实际请求次数、缓存命中、耗时、批次进度和停止原因。

`scope=sample` 代表选定样本；`scope=partial` 代表全市场目标尚未遍历完成；`scope=full` 仅表示整份当前名单已遍历，不表示每只股票都有有效数据。某日期是否全部数据可用，另看日期条目的 `status` 和 `valid/missing/unverified`。

分批模式下，`attemptedCount` 包括已尝试但接口失败的股票；`pendingCount` 是尚未尝试数量。同日所有请求日期均可计算的 checkpoint 会跳过；同日失败、缺日线或存在冲突的 checkpoint 会重新请求。缺失日不称停牌。

## 运行保护

每次 HTTP 请求最少相隔 0.75 秒（`--interval` 最低允许 0.5 秒）；20 秒超时，每只股票最多重试一次。连续五只采集失败会停止。默认每 50 只发布网页数据，最后不足 50 只也发布；逐只 checkpoint 不等待批次。SIGINT/SIGTERM 会发布已完成部分并退出；强制杀进程也不影响此前已完成的原子 checkpoint。

同一个输出目录同时只能有一个采集进程。若刷新缺失，按股票保留既有有效结果并标注保留时间，同时接纳其他股票新补齐的数据；新出现的明确冲突仍展示，不用旧成功掩盖。旧日期索引仍可查。公开目录只复制 manifest 和日期 JSON，不能整体复制采集目录。

## 已完成实测

2026-09-05 真实运行 30 只股票 × 60 个交易日，共 108.83 秒、144 HTTP 请求、58 次重复响应缓存命中，0 次股票请求失败。1,800 个股票日中：1,788 可计算、10 日线缺失、2 日分钟收盘不一致。全量尚未跑完；以此批次的约 3 秒/只推算，5,556 只可能需要约 4.6 小时，不能视为已测得全量耗时。
