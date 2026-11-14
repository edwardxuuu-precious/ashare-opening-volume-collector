# 独立调度器部署与验收

本目录的 Dispatcher 只读取私有健康状态和 writer lease，并只向
`edwardxuuu-precious/ashare-opening-volume-collector` 的 `main` 分支派发
`collector.yml`、`chips.yml` 和独立开关的 `cycle-bottom.yml`。它不下载、不修改市场数据，也不创建或接管计算写锁。

## 调度合约

- 09:50（上海时间）：开盘预采。
- 15:30：当天收盘全量更新。
- 07:00：已知历史缺口接续。
- 07时至23时及02时：每小时第2、7、12……57分钟调用现有 watchdog。采集任务仍只在原07:00–23:55窗口内运行，检查缺口、退避、活跃任务和 writer lease。
- 筹码按官方交易日历，在15:40、21:20和次日02:20到期后，由最近的 watchdog 调用唤醒。现有 GitHub 三个定时事件及采集完成事件继续保留。新增02时覆盖每天增加12次现有 Lambda 调用，没有增加云资源或 IAM 权限。
- Scheduler 固定 `Asia/Shanghai`，关闭弹性时间窗。09:40–09:50 和 15:20–15:30 是让位窗口，watchdog 不派发。

采集任务输入始终包含 `mode=collect`、`phase`、`target_date` 和
`max_minutes=12..285`。任务未完成时会保存 checkpoint 并以失败状态结束；
watchdog 依状态与退避继续接力，不以 GitHub 绿灯替代数据完成。

筹码输入为 `mode=collect`、`automatic_origin=aws-watchdog`、`auto_slot`、`as_of` 和 `max_minutes=285`。调度器按日期和时段先认领再派发，每档最多一次 POST。未知网络结果也保留已消耗额度。调度账本的全部更新使用 S3 条件写，避免采集与筹码互相覆盖。

运行侧用同一并发组、S3 写锁和不可重置的时段认领防止重复计算。暂停的 checkpoint 接续不要求新行情；worker 超时后只接续已确认清理完进程的批次。每日一次的新输入修复额度独立保留。每次计算仍为4个 worker、最多285分钟，job上限310分钟。

`operational_complete` 表示当前模型和输入已得到终态结果，可以包含有具体原因的UNKNOWN。它不会认证证券清单或把原 `market_complete`、`calculation_complete` 改为通过。没有新输入或实现版本变化时，已完成批次直接跳过。`DISABLE_CHIPS_UPDATE=true`同时阻止 GitHub 定时、采集完成和 AWS 自动筹码计算。

## 账号持有人操作

以下步骤需要账号持有人在退出本地验证阶段后完成；不要把私钥、JWT、token 或
密码提交到仓库、终端历史或对话。

1. 完成 AWS SSO，并核对目标账号和 `us-east-1`。
2. 创建只安装到该仓库的 GitHub App：仅 `Actions: Read and write` 和平台必需的 Metadata 只读；禁用 Webhook，不申请 Contents、Secrets、Administration 或用户权限。
3. 将 App 私钥以 Standard SecureString 保存为
   `/stock/scheduler/github-app-key`。CloudFormation 只接收该参数名，不接收私钥值。
4. 以新的、哈希命名的 Lambda ZIP 上传到
   `collector/dispatcher-code/`；先创建所有 schedule 为 `DISABLED` 的 stack
   `stock-actions-scheduler`，并以 `CAPABILITY_IAM` 审阅最小权限模板。
5. 用本地虚构状态测试时段、认领竞争和未知 POST 结果。生产 watchdog 在02时也可能合法唤醒筹码，不用手工调用作为只读探针。
6. 只读核对 `collector/refresh-status.json` 的交易日历、目标缺口和 writer lease 后，启用 schedules。
7. 确认 Dispatcher 已成功派发一次带明确目标日期的 run 后，再将仓库变量
   `DISABLE_DAILY_UPDATE=true`，关闭旧 GitHub cron 的数据写入。手工和 Dispatcher 的
   `workflow_dispatch` 不受该变量限制。

回滚顺序：先禁用四个 Scheduler schedule，再将
`DISABLE_DAILY_UPDATE` 恢复为非 `true`。不要删除 checkpoint、状态对象或私有行情数据。

## 生产验收

一个完整交易日应保留：15:30 附近的 Scheduler 调用日志、GitHub run ID 及启动时间、
首条实际采集时间、`dataComplete=true` 的完整发布时间，以及
`observe_refresh.py` 的私有 manifest/payload/status 回读。17:00 后仍不完整时，
GitHub 必须为失败，Summary 应显示未完成数量、原因和下一次重试时间。

AWS Scheduler 只保证在所选分钟内触发 Lambda；GitHub runner 的排队延迟需要以实际 run
时间记录和验收，不能假设固定启动时延。

## 长周期每日任务迁移（独立开关，默认未切换）

新增 `CycleSchedule`：每天北京时间22:40，仅派发 `cycle-bottom.yml`，
不进入 collector/chips watchdog，不补跑、不自动重试。事件携带 Scheduler 原始时间，
只接受同一天22:40起五分钟内的事件；私有账本 `cycle:YYYY-MM-DD` 在 POST 前条件认领，
并发冲突不发送，未知响应或进程中断也不会释放当日额度。周末仍每天一次，沿用原任务频率。
GitHub runner 仍可能排队，数据源日期仍以真实返回为准。

`--enabled` 只控制原四个 schedule；新增 `--cycle-enabled` 独立控制 CycleSchedule，默认关闭。
现有角色、SSM 参数、S3 账本权限不扩大；复用 Lambda 增加一个每日 Scheduler 事件。
`automatic_origin=aws-scheduler` 标记自动调用；`DISABLE_CYCLE_BOTTOM_UPDATE=true` 同时暂停两种自动入口，
不影响未标记的手工调用。仓库变量 `CYCLE_SCHEDULER=aws` 是互斥选择开关：
设置前仅 GitHub cron 可运行，设置后仅 AWS 自动调用可运行，迟到的旧 cron 会跳过。

安全切换步骤（需要可用 AWS 会话和部署授权）：

1. 核对 AWS 身份、us-east-1、现有 stack/函数/四个 schedule 的实时状态和配置。
   上传经测试的哈希命名 ZIP，更新原 stack，保留原四个 schedule 的现有状态，
   新 CycleSchedule 必须保持 DISABLED。不要用默认模板意外关闭现有生产 schedules。
2. 合并含互斥开关的 workflow；只读回查远端 main、Lambda CodeSha256、
   CycleSchedule 时区/22:40表达式/输入/零重试及 IAM diff。不得通过生产调用来做只读测试。
3. 在当天22:40之前的安全窗口切换；若当天已有周期任务执行、旧 cron 已排队或正在运行，
   等次日切换，避免同一天重复。先核对无活跃周期任务，再将 CYCLE_SCHEDULER 设为 aws，
   然后启用 CycleSchedule；回查两端配置。若启用失败，恢复原变量，保持 AWS 禁用。
4. 验证下一次22:40的 Lambda 日志、每日账本、唯一 workflow_dispatch run、commit、
   真实运行起止时间、发布回读与 sourceDates。partial 是数据滞后，不是发布失败。
   完整验收前不要宣称迁移完成。GitHub cron 保留为受互斥开关禁用的回滚入口。

回滚仅针对此任务：先禁用 CycleSchedule 并回查，确认没有已派发/排队/运行中的周期任务，
再恢复 CYCLE_SCHEDULER 为非 aws；已消耗日期不手动补跑，不清理每日账本。
不要关闭原四个 schedule，也不要改变其他任务频率。
