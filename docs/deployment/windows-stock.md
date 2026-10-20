# Windows Stock 开发与发布

2026-10-20按用户明确要求，将网站源码并入现有公开仓库 https://github.com/edwardxuuu-precious/ashare-opening-volume-collector 的 main 分支。Windows Git 工作目录为 D:\Codex\ashare-opening-volume-collector；D:\Codex\Stock 保留原开发副本、依赖和私有验证产物，不移动或删除。后续版本提交以 Git 工作目录为准，网站、纯计算模块、测试和部署工具一起审阅；行情、账号、凭据、主机绑定、依赖和构建输出均不提交。原采集工作流和已跟踪调度器保持原样。

Git 工作目录内不再需要运行导出器，tooling/export_chip_runtime.py仅检查并提示已统一纳管；从旧 Stock 副本导出运行器仍保留精确清单和未提交修改保护。Python可通过 STOCK_PYTHON 指定现有解释器；新克隆也可按 README 创建自己的 .venv。

2026-10-18已验证Windows独立发布成功：Amplify应用d1nb0daasi2snk的production分支job35为SUCCEED。构建包由Windows生成，上传、启动发布及结果查询均在Windows执行，本次未连接Mac或使用SSH。AWS身份、私有S3读取、认证Lambda及GitHub仓库推送权限均已核实；未新增长期AWS密钥。

2026-10-19补证发布再次验证Windows独立操作：GitHub云任务39201993431成功提交筹码批次3b84a7c7866d761f（5577 READY、144 PASS、5433 FAIL、0 UNKNOWN）。个股详情的旧筛选提示修正由Windows保护构建并发布为Amplify production job40，状态SUCCEED；独立构建包位于outputs/chips-coverage-20261018/stock-limit-evidence-site-windows.zip，SHA-256为33818c71e1e83a9d1277a083b92c02d176bf65e820671fa6430568df8e4446a7，旧包保留。stock.iteready.com及production域名的三条HTML路由与两份实际提示逻辑JS均逐字节匹配保护构建；私有manifest匿名访问仍401。实际回执为outputs/chips-coverage-20261018/site-publication-validation-3b84a7c7866d761f.json，不包含登录后的浏览器画面验收。

AWS CLI已安装，stock-dev配置指向原账户838775535952、us-east-1和原SSO角色，没有复制Mac缓存。首次身份登录已完成；短期会话过期时，在Windows重新登录：

```powershell
& 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' sso login --profile stock-dev --no-browser --use-device-code
& 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' sts get-caller-identity --profile stock-dev
```

STS须实际返回预期账户。设备验证码有效期由AWS控制，须使用命令本次输出的新码；在已登录AWS的外部浏览器打开命令输出的官方验证链接，完成确认及Allow access。无需向聊天发送密码或验证码；认证后本机工具自行使用短期会话。会话过期在Windows重新登录即可，后续构建、推送、发布和GitHub云任务均无需Mac。

保护构建：

```powershell
Set-Location D:\Codex\Stock
$env:PATH='C:\Program Files\nodejs;'+$env:PATH
$env:STOCK_PYTHON='D:\Codex\Stock\.venv\Scripts\python.exe'
$env:NEXT_PUBLIC_AUTH_API='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_DATA_BASE='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_COLLECTOR_API=''
npm.cmd run typecheck
npm.cmd run build
.\.venv\Scripts\python.exe tooling/release_site.py --dry-run
```

实际发布前读Amplify最新成功job并传入--expected-job，防止覆盖其他发布。例如当前网站仍是job35时使用：

```powershell
.\.venv\Scripts\python.exe tooling/release_site.py --expected-job 35
```

工具会再次检查AWS账户、旧job状态、三个静态入口和公开数据禁令，上传受保护构建，启动Amplify发布并核对最终结果。job编号是并发守卫，不能把文档示例当成未来固定值。

job35复用了已核对的Windows构建包，SHA-256为d50748051b8eff8269cd243b9cd5511dba08cb2938710062e144b3d23ee77861。发布记录位于outputs/chips-daily-20261018/windows-site-release.jsonl；发布后验证位于outputs/windows-aws-20261018/publish-verification.json。access-check.json是发布前检查，当时job34成功、实际Windows发布尚待执行；不能把它的待执行字段当成最终结果。

每日筹码任务已由仓库GitHub Actions承载，OIDC使用原仓库限定角色，无需本地AWS登录或Mac保持在线。首次大规模历史补齐与日常增量是不同耗时，页面明确显示当前状态、已处理数与筹码可用数。GitHub定时器可能延迟，不能承诺精确秒级触发。
