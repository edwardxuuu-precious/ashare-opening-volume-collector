# 日更与费用控制

> **当前规则**：独立 AWS Scheduler 分发 GitHub Actions，唯一采集器写入私有 S3；上午预采、收盘日更、有限接续，仍遵守目标日证据、退避和写租约。以 [调度手册](../../deploy/actions/SCHEDULER.md)、当前部署配置及 [阶段验收摘要](../verification/phase-closeout-20261006.md) 为准。[2026-09-08 部署记录](fast-refresh.md) 与本文明确标为历史的内容只作追溯。

当前采集入口已迁到 [GitHub Actions](https://github.com/edwardxuuu-precious/ashare-opening-volume-collector/actions)。网站仍为 https://stock.iteready.com，Cognito 登录、私有 S3 和查询 API 保持原架构。迁移证据见 `outputs/actions-migration/migration-status.json`；旧 EC2、30 GB 磁盘及旧调度已删除，旧 EC2 部署记录只作历史参考。

## 历史每日运行机制（已被分阶段刷新取代）

- 工作日北京时间 17:10 由 GitHub 定时启动一次；交易日历判断新增日期和持久队列中的缺口。当天优先，历史缺口随后处理，不重新全量采集六个月。
- 2026-10-20起公开仓库同时包含网站和采集源码；行情、断点、账号和密钥不进仓库或公开运行日志。使用标准 Ubuntu 运行器，不使用付费大型运行器，也不上传数据 artifacts/cache。
- 新采集固定 AKShare 新浪：分钟线与日线各取一次，不因量差重取，不调用东方财富或 BaoStock。保留 4 个隔离进程和共享 0.75 秒 HTTP 限速；不再等待 20:05 备用源发布。这里是同一次运行内部并发，不是每天检查四次。
- 正常预算 285 分钟，含恢复和收尾，预留最后 10 分钟保存；21:55 停止派发。目标 22 点前发布可用结果，未完成的股票日期留到下次自动补采。
- GitHub 定时任务可能排队或漏触发，免费源也可能失败，因此不保证 22 点硬截止或每只股票当天全部核验通过。公开仓库连续 60 天没有仓库活动时 GitHub 可自动停用定时任务，需在 Actions 中重新启用；不制造空提交维持活跃。
- 用户取消的额外基准及连续三天 Codex 自动检查继续暂停。GitHub 的一次日更和采集所需的数据一致性校验正常保留。

## 断点与安全发布

每个新运行器从私有 S3 恢复断点、名单、日历、最近六个月的工作快照和待补队列；更早的日期只保留在私有 S3 和完整查询目录，不在每次运行时重复下载。归档校验 SHA、文件类型、路径、股票身份和成交量；损坏备份拒绝导入。`collector/actions-state.json` 与备份 SHA 绑定，保存 worker 状态及发布回执，避免把首次历史遍历误判为未开始。

GitHub concurrency 和 S3 条件租约共同限制唯一写入者。旧 EC2 不能和 Actions 同时启用。OIDC 仅允许指定公开仓库 main 分支取得临时 AWS 身份，对象权限仅限 Stock 数据桶的必要路径，不存 AWS 长期密钥。

日期数据只在内容变化后上传，最后更新目录；成功记录不会被网络缺失覆盖。用户已取消行情交叉核对：首15分钟量与日线量可用即计算比例，不再因分钟合计差异排除排名。已保存的每日行情持续累积，不因超过六个月而删除；采集器 IAM 已撤去行情及备份删除权限，仅租约允许删除。最近六个月是自动补采和工作断点窗口，不是历史查询保存期限。旧行情/断点的重复版本保留 7 天；一次性导入包 7 天清理，API 日志 14 天。收尾先保存断点再保存绑定状态，异常运行保留已发布数据。

### 开盘预采与全天发布的独立口径

开盘预采以“有效开盘量缓存 + 精确股票/目标日的正式无成交证据”判断完成。无成交证据单独保存在 `openingNoTrade`，不写入开盘量 0，不在上午发布全天行情。空响应、反复失败、股票名称或旧日期停牌均不能替代目标日正式证据。

`openingCachedCount / openingNoTradeCount / openingMissingCount` 的和等于目标股票数；缺口进一步分为 `openingUnprocessedCount / openingRetryableCount`。原 `unprocessedCount / pendingCount / dataComplete` 保留全天口径，不能用于判断开盘缓存全部未处理。断点恢复验证正式证据身份、日期及与数值缓存的冲突；旧断点缺少新字段仍可恢复。

页面根据 `outcome / exitReason` 区分等待补采、数据源退避、真实失败与暂停；成功时间仍来自最后成功发布。开盘完成表示等待收盘数据，收盘完成还要求原子价格帧齐全。

`targetDate` 表示任务目标；`latestPublishedDate / latestPublishedAt` 仅从已提交的完整目录派生。目录尚无条目时显式为 `null`，不能把目标日写成已发布日。心跳、目录内日文件生成时间和最后成功同步时间分别保留，页面不把心跳作为同步成功。

认证日文件读取先核对已提交目录。目标日未保存时返回 404，不把私有存储隐藏不存在对象的 AccessDenied 当作行情服务故障；目录内对象的真实权限错误保持 503。无需扩展 S3 列目录权限。

## 日行情 OHLC（price schema v1）

每条新记录同时保存不复权的 `open`、`high`、`low`、`close`、`priceStatus` 与行情来源。四个价格必须来自同一行情帧并通过正数和高低关系校验；任何部分字段、非有限数字、零价格或非法关系都会使整组价格留空。正式停牌、退市程序或其他确认无成交事项写入四个 `null` 和 `priceStatus=not_traded`；没有正式证据的源缺失只能标记为 `missing`，不能根据零量或空响应猜测停牌。

`verificationVersion=3` 继续表示原有开盘量、涨跌幅和振幅契约，不因约 70 万条旧记录缺少 OHLC 而回退。日期文件与目录另设 `priceSchemaVersion=1`、价格字段声明、三类计数及 `priceDataComplete`。2026-09-21 起的新交易日只有核心 `dataComplete` 和日行情 `priceDataComplete` 同时为真才完成；更早日期仍保持原核心完成状态，由独立回填补价。

固定历史回填范围为 2026-03-06 至 2026-09-18，ID 为 `ohlc-v1-20260920`。每次从新到旧选择最多 20 个尚未完成的交易日；每只股票按整个所需窗口请求一次日线，沪深使用腾讯日线、北交所使用新浪日线，缓存 v2 保存完整 OHLC、涨跌幅、振幅、来源和适配器。旧版只含涨跌幅和振幅的缓存不能推导价格，必须重新请求。回填只更新行情字段和行情元数据，不得改变首 15 分钟量、全天量、占比、普通状态或已有特殊状态。

```sh
gh workflow run collector.yml \
  --repo edwardxuuu-precious/ashare-opening-volume-collector \
  -f mode=price_backfill \
  -f backfill_id=ohlc-v1-20260920 \
  -f backfill_from=2026-03-06 \
  -f backfill_to=2026-09-18 \
  -f backfill_batch_size=20 \
  -f max_minutes=285
```

同一 `backfill_id` 复用受保护的 v2 缓存与状态；成功缓存的股票不重复请求，旧 v1 缓存不算完成。日期对象逐个条件写入并做哈希回读，manifest 始终最后发布。手动回填与正常日更共用单写入者租约；调度器不会自动启动历史回填，租约冲突时本批安全退出，正常每日采集保持优先。恢复固定历史范围时允许下载这批旧日期，但不会扩大日常六个月工作窗口。

## 手动恢复与发布

维护入口为 `deploy/actions/`，运行器为 `backend/server/actions_runner.py`。`deploy/actions/package.py` 的明确文件清单继续用于采集器运行包；按用户2026-10-20要求，网站源码也在同一 Git 仓库维护。提交前检查源码 diff 和忽略规则，不提交工作区中的数据、凭据、依赖、构建或验证产物。修改后运行相应检查并提交到现有仓库 main。

```sh
# 只读恢复验证，不请求数据源、不写线上数据。
gh workflow run collector.yml --repo edwardxuuu-precious/ashare-opening-volume-collector -f mode=restore_probe -f max_minutes=12
# 完整单次日更；正常状态 DISABLE_DAILY_UPDATE=false，先确认无活动写入者。
gh workflow run collector.yml --repo edwardxuuu-precious/ashare-opening-volume-collector -f mode=collect -f max_minutes=285
# 阻止 GitHub 自带 cron；维护时还必须禁用四个独立 Scheduler。
gh variable set DISABLE_DAILY_UPDATE --repo edwardxuuu-precious/ashare-opening-volume-collector --body true
```

正常运行需变量 `AWS_ROLE_ARN`、`STOCK_BUCKET`、`DISABLE_DAILY_UPDATE=false`。维护停机先禁用四个独立 Scheduler，同时关闭 GitHub cron；该变量不阻止明确的手动分发，不能单独视为全局写门。核对没有 active/queued 任务并等待持锁任务安全收尾、唯一 writer lease 释放后，才能接入其他写入者。恢复时按维护前记录精确恢复四个 Scheduler 和变量值，不能擅自删除活动租约。AWS 栈为 `stock-actions-collector`。OIDC subject 包含当前 GitHub owner/repository 的不可变 ID，不能改成宽泛通配。

恢复旧 EC2 会重新产生费用，不能自动执行。必要时先停用新写入者、等租约释放，使用 `outputs/actions-migration/retirement-backup.json` 的旧模板/参数重建，再从已验证 S3 备份恢复；不得运行两套生产采集器。

## 费用估算

按每月 22 个交易日、十人以内偶尔查询、美元兑人民币 7 估算。目标为正常运行约 **10–20 元/月**，不是账单上限；迁移前已产生的 EC2 费用、首次回填、税和域名续费另计。

| 项目 | 正常月估算与依据 |
| --- | --- |
| GitHub 标准运行器 | 公开仓库计算免费；不用付费大型运行器 |
| 旧 EC2、IPv4、30 GB EBS | 已释放，后续不再产生这些运行费用 |
| Route 53 托管区 | 按完整一个区 $0.50，约 3.5 元 |
| S3 存储与读写 | 约 1–3 元，取决于变化日期和旧版本数量 |
| 每日恢复流量 | 当前数据及断点约 0.5 GB，每月约 11 GB；即使按 $0.09/GB 计费约 7 元，账号可用免费出站额度会降低此项 |
| 网页、API 与少量查询 | 预算约 1–6 元，按实际访问和免费额度占用浮动 |

S3 标准存储 us-east-1 $0.023/GB-month；PUT/LIST $0.005/千次、GET $0.0004/千次。永久历史会逐渐增加存储量，但日更仍只恢复近期工作集。大量跨日期查询、历史补采导致反复更新多个日期、访问增长会提高费用；不自动加机器或购买数据源。短时迁移测试不是完整日更速度或整月账单证明。

依据：[GitHub 计费](https://docs.github.com/en/billing/concepts/product-billing/github-actions)、[定时任务限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[S3 价格](https://aws.amazon.com/s3/pricing/)、[Route 53 价格](https://aws.amazon.com/route53/pricing/)、[Amplify 价格](https://aws.amazon.com/amplify/pricing/)。

## 历史累积变更（2026-09-07）

此前按滚动窗口删除的 `2026-03-06` 曾从尚存S3版本恢复，当时保留原有缺失状态，该次文件恢复证据见 `outputs/history-retention/status.json`。2026-10-06已完成该日及其余指定历史成交量缺口的真实补齐与线上验收，当前结果见 [恢复与阶段验收摘要](../verification/phase-closeout-20261006.md)。网页日历可选最早已保存日期，单次最多查询186个交易日是请求规模限制，不是存储期限。

## 历史一次性复核（2026-09-07，后续用户已恢复新增采集）

用户要求暂不录入9月7日，先重新复核已有问题数据。`ENABLE_DAILY_UPDATE=false`，今日自动新增采集已停用；不要在复核尚未结束时自行恢复普通日更。

一次性复核ID为`historical-20260907-01`，冻结范围及基线见`outputs/history-review/scope.json`：2026-03-06至2026-09-04共126个已保存日期，初始66971条缺失或差异记录。模式`review`只接受已保存且早于`review_as_of`的明确日期，不刷新交易日历或自动转入新增日采集。逐条已验证结果受发布前原始快照比对保护。

新浪按同一review ID持久记录尝试，旧版已完成的新浪尝试继续复用；中断未提交的请求允许续跑。单一来源模式的`reviewCompleted=true`只要求本轮新浪尝试已完成，不再要求备用源尝试，不代表缺失或差异均已解决。若达到285分钟预算且仍有未尝试请求，保存结果和断点，并保持false；使用同一ID及同一日期列表续跑，不能更换ID后声称无重复。

当前运行与进度证据见`outputs/history-review/status.json`，不能把工作流开始或绿色预算暂停当作复核全部完成。

## 单一来源切换（2026-09-07）

用户要求固定一个来源，选择沿用 AKShare 新浪。生产日更、历史采集、历史复核命令均固定`--source-policy sina`；CLI仅允许该来源。旧备用代码仅保留用于兼容测试和历史审计，不由生产采集入口调用。已有其他来源的历史记录不删除或改名，`collectionSourcePolicy`表示新采集政策，`sourcePolicy`及逐行`sourceProvider`保留历史来源。

切换时旧工作流使用启动时的代码，需先安全收尾；新版本复核以同一ID和日期列表排队接续，不取消旧任务导致断点丢失，不新增9月7日。上线与接续状态记录见`outputs/history-review/single-source-release.json`。

## 仅下载与直接计算（2026-09-07，覆盖此前核验规则）

用户明确取消核对和量差标注。`download_only`按同一股票、日期的09:45分钟成交量与日线成交量直接相除；不要求全天16根齐全，不比较合计成交量或收盘价，不进行跨来源核对。仍保留文件/股票/日期身份、有限数值和除数可用性保护，真实缺失保持空值。网页不显示质量状态列、核验提示或参考比例，已有两个量可用的历史记录立即按原公式展示及参与排名。

后端`download_only.prepare_saved_downloads`先整理保存的日期和断点，原始成交量及已经成功的历史行不变；仅对缺少必要量的记录继续新浪下载。已取消尚未开始的旧单源复核34090093672；新下载任务使用`historical-download-20260907-01`及原126日期窗口，以新的计算规则识别缺失，不重新采集已可计算记录。9月7日新增仍停用。旧多源任务需先保存断点再由唯一写入队列交接，新任务开始前不能声称旧任务已切换。

网页、采集器版本、测试和实时部署状态记录在`outputs/download-only/`。本节的“直接计算”取代上文旧版本“必须核验通过”要求，`status=ok`在新数据中表示可计算，不表示与别的行情序列核对过。

## 每日筹码筛选的独立日更

`backend/chips/runner.py` 与 `.github/workflows/chips.yml` 使用独立写锁、断点及私有 `collector/chips/`、`data/chips/daily/` 命名空间，不改变开盘量采集规则。定时为北京时间16:20、21:20、次日02:20；GitHub托管执行器和OIDC访问私有S3，不依赖Mac或Windows常开。Windows可用本机 `stock-dev` 登录独立发布网页，认证会话到期按AWS正常流程续登。

正式模型使用共享210根窗口的昨日/今日前缀、零初始化、OHLC4顶点、150动态节点、直接高度注入和系数2。已有真实历史原件可与带日期的有效最终报价拼接后重新回放；未交易必须有明确证据，接口零量占位或盘中缓存不能当作收盘行情。VWAP超出OHLC只审计，不沿用旧VWAP模型的顶点限制，其他基础数值校验继续保留。

历史涨停量额差异需要独立原始证据时，可重新校验已归档的较早请求，并仅使用当前10个交易日窗口内的重合记录。旧请求的日期、URL及原文哈希保留，不改标为今日证据，不以旧响应缺少今日记录判定今日未涨停；重合记录不能解决的差异仍须新证据或保持待核实。

确切日期的官方当前名单核对件优先；无该核对件时使用行情商清单及明确标注覆盖的目录回退。当前名单不冒充历史时点成员。只有整批完成后才切换manifest，旧批次可继续读取；覆盖不足或行情/涨停证据缺失时显示实际计数及待核实状态，不把任务成功退出当作数据齐全。

页面 `/chips` 仅筛选收盘价严格低于10元、最近10个交易日至少一次收盘涨停，展示峰价、昨日原峰固定价格区间的筹码占比及百分点变化。“没动”阈值保持null。`/chips/calibration` 保留明确标记的旧模型历史样本，不作为每日新模型输出。
