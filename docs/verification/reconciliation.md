# 成交量核验与备用方案

> 历史验证记录：正文保留当时结果与路径，不代表当前运行状态。旧路径对应关系见 [目录说明](../structure.md)。

当前指标：同一来源的 09:45 首根不复权 15 分钟 K 线成交量，除以同日日线成交量。成交量统一为股，首根开盘成交归属沿用来源，不自行补减集合竞价；不同来源的首根归属不保证完全等价。

## 当前来源策略

1. **新浪主源（AKShare）**：完整且唯一的 16 根 15 分钟线、非负整数成交量、正日线量、相同日期和收盘价（仅容许 1e-8 浮点噪声），分钟全天合计与日线量精确相等才通过。差异时清缓存重取一次。
2. **东方财富备用（AKShare）**：`stock_zh_a_hist_min_em(period='15', adjust='')` 与 `stock_zh_a_hist(period='daily', adjust='')` 整套核验。两接口成交量由手乘 100 转股，不能拼接新浪分子与东财分母。每轮最多核验 50 只，连续 3 次备用接口异常暂停该源。
3. **BaoStock 第三来源（独立 SDK）**：启用 `--baostock-fallback` 后，对沪深仍有量差、历史延后核验或缺失的请求日期，按股票一次读取整个请求窗口的不复权 15 分钟线和日线，再逐日严格核验。成交量单位为股；通过后整套替换，记录 `sourceProvider=baostock`，不称为 AKShare 返回的数据。北交所不请求 BaoStock；价格冲突等不满足补洞条件的记录不会自动变为有效。
4. **失败保留证据**：没有通过核验的来源时，保留缺失或待核验状态；差异记录 `ratio=null`，原比值保留为 `referenceRatio`，尝试过程、失败或跳过原因存入逐行审计。未调用、熔断跳过及不支持不等于已核验，也不能从日线推算缺失分钟量。

首次回填的 `--reconcile-latest-first` 仅限制新浪重取和东方财富备用：最新目标日优先，历史初查差异保留 `reconciliationDeferred=true`。**BaoStock 不受这个最新日限制，仍尝试整个请求窗口内的符合条件缺口**；它不会重写已核验正常日，也不等于已建立任意历史重核验队列。

旧 checkpoint 仍先验证身份、日期覆盖及状态。已尝试且存在未做 BaoStock 核验缺口的沪深断点走 `repair_only`：直接从旧观察补 BaoStock，不重复请求新浪和东财；已尝试的 BaoStock 失败不会因普通续跑无限重试。`completedStocks` 按股票代码去重，修复同一股票不重复增加完成数；本轮处理次数另计。

## 网页与存储

- 默认排名排除待核验记录；单股查询保留待核验行及原因。参考比例仅在详情显示，不进入中位数、趋势或最低占比筛选。
- 来源明确显示新浪、东方财富、BaoStock；未知来源不默认标作新浪。来源标签本身不能使参考值成为有效比例。
- 浏览器缓存仅保留展示字段，复用已规范行；完整尝试历史仍在私有 S3 和认证 API。前端继续隔离旧缓存中的差异数据。
- 保存数据迁移只是复核已有合计，`verificationAudit=saved_aggregate_recheck_v2` 不代表重新下载成功。仅从本地 checkpoint 重建聚合必须进入维护模式、停止唯一写入者并持有锁；本地聚合更新不代表已发布私有 S3。

## 2026-09-06 后续实测与发布证据

- 当前集成采集器对 `600519` 的 126 个请求交易日全部得到可核验结果：101 日保留新浪、25 日使用 BaoStock。9 月 4 日 BaoStock 首 15 分钟量 775,476 股、全天 4,541,564 股、占比 17.075087%，全天分钟合计与日线精确一致。此前新浪首根 775,576 股仅作为原来源证据保留，不混入新来源分子。
- 云端 BaoStock 对 `600519`、`000001` 的 9 月 4 日真实探针已通过；本轮集成累计 175 余项自动测试通过。真实来源验证、自动测试和运行时部署是不同证据层。
- 前端 Amplify **job 8 SUCCEED**，40 个静态文件回读及匿名 401/403 边界通过。记录见 `outputs/full-market/release.json`、`outputs/full-market/artifact-verification.json`。
- BaoStock 运行时经 SSM `fe069bb8-a321-495d-ae04-2caef5b4fd70` 维护发布成功，服务已恢复运行；本地断点聚合的 126 日数据已发布至私有 S3。真实浏览器确认茅台 BaoStock 数值与上表一致，单股、两股两日、日历、状态、gzip 和鉴权检查通过。源码/补采批次见 `outputs/full-market/baostock-production-release.json`；终态及浏览器证据入口见 `outputs/full-market/deployment-status.json`。
- **20:45:42 +08:00 状态快照**：5,556 只中已处理 1,238、已发布 1,233；最新日 973 条已核对、260 条待核验、4,323 条缺失。云端仍在继续历史采集，`allMarketComplete=false`。这些数字是此时快照，不能把 126 日文件齐全称为全市场行情采集完成。

来源实测证据：`outputs/baostock-six-month-probe-20260906/`（早期独立探针）、`outputs/full-market/baostock-integration.log`、`data/full-market-repair/checkpoint/600519.json`（本次集成结果）、`outputs/full-market/baostock-network.json`。已有独立 EC2 云采集流程；浏览器按需任务 API 是另一功能，不由网页发布自动开通。全市场股票数、已尝试数、已核对股票日数须分别报告。

## 2026-09-06 早期两来源验证（历史记录）

当时三股一天 CLI 用时 35.03 秒、47 HTTP 请求：`000001` 新浪精确一致；`600519` 新浪少 44,600 股、`920002` 多 1,762 股，东财均返回 ProxyError。独立直连东财也 ConnectionError，未修改系统代理，无法单独归因本机代理。此结论只描述当时新浪/东财情况，不覆盖后续 BaoStock 成功。

当时上线以私有 S3 的 60 日 / 32 股快照为基准：1,920 条中 872 条可纳入统计、1,038 条待核验、10 条缺失；1,036 条由可计算转为待核验。这是早期迁移快照，不能代替当前全市场发布状态。证据：`outputs/reconciliation-live-20260906/`、`outputs/reconciliation-collector-smoke/`、`outputs/reconciliation/release.json`、`outputs/reconciliation/live-verification.json`。

官方接口定义：https://akshare.akfamily.xyz/data/stock/stock.html
