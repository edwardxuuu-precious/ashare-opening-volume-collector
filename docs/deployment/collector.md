# 旧 EC2 采集器部署参考

当前生产采集已迁至 GitHub Actions；日常维护以 [日更说明](daily-update.md) 为准。下文描述历史 EC2 架构，不能直接按下文重新启用旧写入者。

采集器是独立 EC2 进程。网站继续用原 Cognito 会话读取私有 S3；没有新增公网采集端口，也不允许浏览器直接触发无限全市场任务。

每日更新、四进程采集、自动补漏与费用验收以 [日更说明](daily-update.md) 为准。最新部署证据见 `outputs/daily-update/deployment-status.json`；下方旧发布记录只作历史证据。

## 本轮运行边界

- 一台 `t3.medium`（4 GB）、Amazon Linux 2023、30 GB 加密 gp3；CPU credit 使用 standard，避免 unlimited 突发费。
- 安全组没有任何入站规则，没有 SSH key。保留 HTTP/HTTPS 出站；BaoStock 另需其已验证服务 IP 的 TCP 10030 出站，网络变更见 `outputs/full-market/baostock-network.json`。没有新增入站规则；管理使用 SSM、IMDSv2。
- 实例角色仅指定数据桶的 `data/`、`collector/` 前缀读写和 SSM 基础权限，没有 Cognito 或其他项目权限。
- 每次开机通过 systemd OnBootSec=24h 安排硬停机，保留 EBS；worker 单轮最长 20 小时。异常暂停，不自动循环重试。停止实例后 EBS 仍计费，删除需先确认断点备份。
- EventBridge Scheduler 在工作日北京时间 17:10 启动这一台实例，启动角色只有该实例的 `ec2:StartInstances` 权限，不创建 Lambda。默认参数 `EnableDailySchedule=false`，真实来源与服务启动通过后才设为 true。
- 每次开机启动已启用的采集服务，正常完成或安全暂停并保存断点后自动停机。按实际开机小时计费，不要求每月 24 小时常驻；公网 IPv4、EBS 和 S3 分别计费。节假日由交易日历识别，不会伪造当日交易数据。

## 数据流程

首次一遍 `--full --months 6 --resume --resume-attempted --efficient-sina --baostock-fallback`，同一股票下载后计算整个六个月窗口。高效读取仅启用固定 AKShare 版本与源码指纹校验通过的适配。每处理 50 只保存快照，worker 每 30 秒检查新快照，先上传最新日、再上传 manifest。历史文件在本轮结束或暂停时统一上传，再更新 manifest，避免全量历史反复上传。

已有上传的历史条目在最新日更新过程中保留；未上传的新日期不会提前出现在 manifest。所有发布前验证比例算术和严格对账，待核验记录只保留参考值。全市场遍历完成只表示所有股票已尝试，不表示全部数据已核验通过。

断点逐只写在持久 EBS，收尾将 `checkpoint/`、`universe.json`、`calendar.json`、`run-status.json` 一起压缩备份到 `collector/checkpoint.tar.gz`。重启同实例恢复本地断点；实例重建必须由 SSM 恢复备份，不自动解压外部文件。`--resume-attempted` 保留已尝试的终态记录，避免少数未核验股票反复重试阻塞全市场进度。完成六个月首次遍历后，调用 daily_collector，优先最新交易日，再处理六个月窗口内持久队列的缺口；断点备份也包含 daily-state.json 和 daily-attempts.jsonl。

首次遍历暂停后，固定上轮交易日集合续跑，仅删除滚动窗口外的过期日期；不会因次日新增交易日而重新下载全部股票。首次遍历完成时若交易日历已有更新日期，会在本次开机剩余运行预算内继续补采最新日。`completedStocks` 按已尝试股票代码去重；同一旧断点补修不能重复增加它。本轮尝试次数独立记录，不与已完成股票数累加。`publishedStocks` 是最新已发布快照的已处理股票数，逐批发布时两者可能短暂不同。

初轮云采集额外启用 `--reconcile-latest-first`：每只完整初查六个月窗口，但仅最新目标日差异触发新浪重取与东方财富备用；历史差异保留初次观察、`reconciliationDeferred=true`、`ratio=null`。manifest 与日期文件声明 `reconciliationPolicy=latest_first`。新初查若与旧已核验记录明确冲突，隔离新冲突并保留 `previousVerifiedObservation`。

`latest_first` **只约束新浪/东方财富**。启用的 BaoStock 使用独立 SDK，对沪深整个请求窗口内仍有量差、延后核验或缺失的日期整套补洞；16 根分钟量、日线量、收盘和日期严格一致才接替，已有正常日保留原来源。北交所不请求 BaoStock，未尝试或熔断跳过不表示已核验。具体来源及尝试结果以逐行审计为准。

旧断点若已完整尝试但尚有符合条件且未做 BaoStock 核验的缺口，续跑进入 `repair_only`，沿用旧观察直接补 BaoStock，避免再次请求新浪/东财；已完成或已尝试的断点仍按续跑规则复用。普通续跑不会无限重试 BaoStock 的已记录失败。

日更使用独立的持久股票日期队列，自动补历史缺口。以下命令仅用于必要的人工定向修复：先启用维护标记并停止唯一采集服务，再按实际股票与日期运行（示例日期需仍在六个月窗口内）：

```sh
/opt/stock-collector/venv/bin/python /opt/stock-collector/scripts/collect.py \
  --symbols 600519,000001 --dates 2026-09-03,2026-09-04 \
  --out /var/lib/stock-collector/data --resume --efficient-sina --baostock-fallback --publish-every 1
```

此命令不带 `--reconcile-latest-first` 或 `--resume-attempted`；符合补洞条件的已尝试旧断点仍优先走 BaoStock `repair_only`，其余需采集日期走正常主源与备用流程。它更新本机数据，仍须经既有私有 S3 验证与发布流程后网页才生效，不能把本机重核验当作已上线。

公开给登录用户的状态对象：`data/collection-status.json`。字段 `state` 为 running / paused / completed，包含 `id`、`completedStocks`、`totalStocks`、`latestDate`、`dates`、`updatedAt`、`message`。认证 API 只需把该精确文件名加入白名单，不改变已有用户校验。状态不是可写任务 API。

滚动六个月过期日期先移出 manifest，再删除对应 `data/YYYY-MM-DD.json` 当前对象。桶已开启版本控制；行情和断点的非当前版本保留7天，当前数据不受7天规则影响。一次性导入包7天后清理，日志保留14天；范围及上线读回见日更说明。

## 部署（由 Coordinator 执行）

先核对 AWS `dev` 身份、region 和既有私有桶；选择有 Internet Gateway 出站路由的公开子网。实例有临时公网 IPv4 但没有入站端口。

```sh
# 只打包运行源码；不要打包 .env、账号、密钥、原始数据或整个工作区。
tar -czf /tmp/stock-collector.tar.gz requirements.txt deploy/collector -C backend scripts server
aws s3 cp /tmp/stock-collector.tar.gz s3://stock-private-access-databucket-bmzud610gtex/collector/releases/current.tar.gz --profile dev --region us-east-1 --sse AES256
aws cloudformation deploy --template-file deploy/collector/template.json --stack-name stock-private-collector --capabilities CAPABILITY_IAM --parameter-overrides VpcId=VERIFIED_VPC SubnetId=VERIFIED_SUBNET --profile dev --region us-east-1
```

模板 bootstrap 安装依赖及 systemd 文件，默认不启动采集。先通过 SSM `AWS-RunShellScript` 检查 `/var/lib/stock-collector/bootstrap-complete`，并使用同一 venv 运行 `scripts/probe_eastmoney_endpoint.py` 等真实来源探针。全部候选来源均无法通过真实探针时停止发布；单源失败按核验和熔断规则处理，不关闭鉴权或放开网络入站。

启动命令通过 SSM 运行：

```sh
systemctl enable --now stock-collector.service
systemctl status stock-collector.service --no-pager
journalctl -u stock-collector.service -n 30 --no-pager
cat /var/lib/stock-collector/worker-state.json
```

已存在的本地已验证 seed 可先放到私有 `collector/seed/`，经 SSM 复制到 `/var/lib/stock-collector/data/` 并归属 `stockcollector`。seed 必须包括 checkpoint、universe、calendar、manifest、日期文件；只复制 checkpoint 会失去保留旧聚合数据的路径。主机源码文件归 root，不允许 worker 修改自己。

`universe.json` 是带原始 `asOf` 的已验证名单：同日可直接复用，刷新失败最多回退 7 个自然日，同时保留原日期和 `refreshError` / `source`；名单代码、名称、数量及市场必须验证，损坏、未来或过期名单不能作为有效回退。恢复时保留它，避免云端名单接口短暂失败使已有行情断点无法启动；缓存使用不会把旧名单改称今日名单。

维护前先 `touch /etc/stock-collector.maintenance`，再 `systemctl stop stock-collector.service`。服务收到 SIGTERM 后停止子进程并尝试发布已保存结果及状态。维护标记会阻止收尾自动停机；完成维护后删除标记，再启动服务。标记不取消每次开机 24 小时硬上限。若已安排收尾的一分钟停机，维护者还需 `shutdown -c`。

服务只有在当前 systemd INVOCATION_ID 匹配、状态为 completed/paused 且数据与断点发布成功后才设置 shutdownReady；root 固定包装器读取这个数据标记，执行固定 `/usr/sbin/shutdown -h +1`。状态内容不能指定命令或路径。旧一轮状态、缺少标记、发布失败都不能触发提前停机；24 小时硬上限仍保留。

长期调度启用时，用更新后的 template.json 创建 ChangeSet，确认只新增 DailyScheduleGroup、DailyStartRole、DailyStartSchedule 后执行，并设 `EnableDailySchedule=true`。既有 CollectorInstance / UserData 保持原样，不因启用调度而替换实例。通过 SSM 上传新 worker、整个 deploy/collector 并执行 install-service.sh；其会安装每次开机预算 timer、取消旧 bootstrap 单次计时和旧机内每日 timer，但不会在探针阶段启用采集服务。确认后 `systemctl enable --now stock-collector.service`，即可每次 Scheduler 启机自动续跑。

只有来源探针、最新批次云端采集、私有 S3 读回、真实登录网页进度均通过后，才可报告云采集已接通。全市场数量、可计算数量、待核验数量应分别汇报。

## 本次发布证据（2026-09-06）

已有 EC2 私有采集运行环境。本轮 BaoStock 本机 `600519` 六个月集成及云端 `600519` / `000001` 9 月 4 日探针通过，175 余项自动测试通过。前端 job 8 已成功，40 个静态文件回读和匿名 401/403 边界通过；这些证据不能代替采集运行时切换和私有数据回读。

SSM `fe069bb8-a321-495d-ae04-2caef5b4fd70` 已返回 `Success`，运行时服务已恢复，126 日断点聚合已发布私有 S3，真实浏览器来源、查询、状态、gzip 和鉴权回读通过。源码/补采批次 hash 见 `outputs/full-market/baostock-production-release.json`，终态和浏览器证据入口见 `outputs/full-market/deployment-status.json`。2026-09-06 20:45 的终态记录仍为 `running` / `allMarketComplete=false`，全市场采集尚未完成；具体处理、发布及核验数量应读最新状态，不复用本次快照当实时进度。

## 仅从本地断点重建快照

`server/publish_cached.py` 不请求任何上游，也不直接上传 S3。必须先创建维护标记、停止采集服务；脚本同时取得 worker 和 collector 锁，验证冻结交易日、名单及全部候选断点后才写本地聚合。示例：

```sh
runuser -u stockcollector -- /opt/stock-collector/venv/bin/python \
  /opt/stock-collector/server/publish_cached.py --root /var/lib/stock-collector \
  --baostock-fallback --reconcile-latest-first
```

缺少维护标记、写入者仍持锁或存在无效候选均拒绝。输出 `upstreamRequests=0`、`publishedToS3=false`；仍需 Coordinator 经现有 Publisher 验证并同步日期、最后更新 manifest 及备份，再读回确认。完成后按维护流程恢复服务。

## 本地验证

```sh
.venv/bin/python -m unittest discover -s tests/python -p 'test_*.py' -v
python3 -m py_compile backend/server/cloud_worker.py
python3 -m json.tool deploy/collector/template.json >/dev/null
```

覆盖 manifest 最后发布、禁止泄漏未上传日期、旧历史保留、冲突比例拒绝、待核验参考值保留、进度协议、续跑命令等；这些测试不替代云端来源和实例验证。

官方依据：[Scheduler 通用 AWS SDK 目标](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-universal.html)、[Scheduler 信任策略必须限定 schedule-group](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html)。

## 合并一次性补采断点

`server/import_checkpoint_batch.py` 只导入新 checkpoint，不修改聚合日期、manifest 或 S3，不启动/停止服务。归档只允许普通 `checkpoint/六位股票代码.json`，不能含目录条目、软/硬链接、重复名称或其他路径。因此打包时逐个添加文件，不能直接把 checkpoint 目录加入 tar。

```python
from pathlib import Path
import tarfile, hashlib
source = Path('var/data/full-market-supplement/checkpoint')
archive = Path('/tmp/stock-supplement.tar.gz')
with tarfile.open(archive, 'w:gz') as bundle:
    for path in sorted(source.glob('*.json')):
        if path.is_file() and not path.is_symlink():
            bundle.add(path, arcname='checkpoint/' + path.name)
print(hashlib.sha256(archive.read_bytes()).hexdigest())
```

Coordinator 先通过 SSM 启用维护标记、停止云采集服务并确认 stopped，再上传导入脚本与归档。维护标记、worker 锁和 collector 锁三者均由导入器校验；不允许一边采集一边导入。

```sh
# 下列停止动作由维护者执行，导入器本身不会代执行。
touch /etc/stock-collector.maintenance
systemctl stop stock-collector.service
systemctl is-active stock-collector.service

/opt/stock-collector/venv/bin/python /opt/stock-collector/server/import_checkpoint_batch.py \
  --archive /tmp/stock-supplement.tar.gz \
  --root /var/lib/stock-collector \
  --expected-sha256 VERIFIED_ARCHIVE_SHA256
```

请求日期取云端 worker-state.json 冻结集合，股票身份取云端 universe.json。全批先验证后写，限制 10,000 文件、单文件 8 MiB、展开总量 2 GiB。未知股票、error/pending、日期覆盖不完整和没有有效首 15 分钟观测的记录跳过并计数；代码不符、错误版本、无效比例或不安全归档直接拒绝整批。未核验参考值按原状态保留，不能变成正式比例。

已有任何同名云 checkpoint 绝不覆盖。新文件以原子且禁止覆盖的方式插入，归属数据目录用户，并附 `importedAt`、`importedArchiveSha256`。报告写在 root/imports/，包括源 hash、目标日期、计划/成功代码和跳过计数。意外中断可能只完成部分文件；报告保留 planned/imported 状态，重跑仍不会覆盖已完成文件。完成导入后由 Coordinator 检查报告、移除维护标记、重新启动云采集；下一次正常快照才会发布导入结果，导入成功本身不代表网页已更新。
