# 独立调度器部署与验收

本目录的 Dispatcher 只读取私有健康状态和 writer lease，并只向
`edwardxuuu-precious/ashare-opening-volume-collector` 的 `main` 分支派发
`collector.yml`。它不下载、不修改市场数据，也不创建或接管采集写锁。

## 调度合约

- 09:50（上海时间）：开盘预采。
- 15:30：当天收盘全量更新。
- 07:00：已知历史缺口接续。
- 07:02–23:55：每五分钟 watchdog；只在数据不完整、退避已到期、无 GitHub 活跃任务且无有效 writer lease 时派发。
- Scheduler 固定 `Asia/Shanghai`，关闭弹性时间窗。09:40–09:50 和 15:20–15:30 是让位窗口，watchdog 不派发。

任务输入始终包含 `mode=collect`、`phase`、`target_date` 和
`max_minutes=12..285`。任务未完成时会保存 checkpoint 并以失败状态结束；
watchdog 依状态与退避继续接力，不以 GitHub 绿灯替代数据完成。

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
5. 在采集窗口外调用 watchdog。预期结果为
   `outside_collection_window`，且不得产生 GitHub run 或市场数据写入。
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
