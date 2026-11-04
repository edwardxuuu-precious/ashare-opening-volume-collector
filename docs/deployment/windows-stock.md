# Windows Stock 开发与发布

Windows 与 macOS/Linux 使用同一 Git 仓库、保护构建和发布入口。当前操作步骤统一见[网站构建与发布](site-release.md)，本页补充 Windows 本机环境及历史验证依据。

## 使用本机 Git 工作目录

在实际克隆的仓库根目录工作，以 `git rev-parse --show-toplevel` 为准。按 [README](../../README.md#本地开发)安装 Node.js 22.13+、Python 3.11+ 和本机 `.venv`；`STOCK_PYTHON` 可指定已有解释器。完整 Python 套件含 `fcntl`，在 Linux/macOS 执行；Windows 的可运行范围见 README。

统一 Git 工作目录内无需导出运行器。`tooling/export_chip_runtime.py` 只检查并提示已统一纳管；从旧独立开发副本导出时仍保留明确文件清单和未提交修改保护。行情、账号、凭据、主机绑定、依赖和构建输出不提交。

## 登录本机 AWS SSO

使用 Windows 自己的 AWS CLI 配置，不复制其他电脑的缓存或创建长期密钥。发布工具默认使用 `stock-dev`、`us-east-1`；按本机实际配置显式传参。

```powershell
aws sso login --profile stock-dev
aws sts get-caller-identity --profile stock-dev --region us-east-1
```

身份读回须返回账户 `838775535952`。如果需要设备码流程，使用 `aws sso login --profile stock-dev --no-browser --use-device-code`，并在命令本次输出的官方链接完成确认。验证码过期时重新登录，不向聊天发送密码或验证码。

按[网站构建与发布](site-release.md)中的 PowerShell 命令构建，再通过 `tooling/release_site.py` 离线制包、审阅哈希和发布。每次发布前重新读取最新任务号，不沿用下文历史值。

每日采集与派生计算由 GitHub Actions 和云端调度承载，使用仓库限定的 OIDC 角色，不要求 Windows 登录或个人电脑常开。后备触发、断点与租约边界见[日更说明](daily-update.md)。

## 历史验证记录

以下内容记录当时操作与证据，不表示当前部署版本或本机配置。历史命令中的默认包路径和发布参数已由当前[共享发布入口](site-release.md)替代。

2026-10-20按用户明确要求，将网站源码并入现有[公开仓库](https://github.com/edwardxuuu-precious/ashare-opening-volume-collector)的 main 分支。Windows Git 工作目录为 `D:\Codex\ashare-opening-volume-collector`；`D:\Codex\Stock` 保留原开发副本、依赖和私有验证产物，不移动或删除。网站、纯计算模块、测试和部署工具统一审阅，原采集工作流和调度器保留。

2026-10-18已验证 Windows 独立发布成功。Amplify 应用 `d1nb0daasi2snk` 的 production 分支 job35 为 `SUCCEED`。构建包由 Windows 生成，上传、启动发布及结果查询均在 Windows 执行，当次未连接 Mac 或使用 SSH。AWS 身份、私有 S3 读取、认证 Lambda 及 GitHub 推送权限均已核实，未新增长期 AWS 密钥。

job35 复用了已核对的 Windows 构建包，SHA-256 为 `d50748051b8eff8269cd243b9cd5511dba08cb2938710062e144b3d23ee77861`。发布记录位于 `outputs/chips-daily-20261018/windows-site-release.jsonl`，发布后验证位于 `outputs/windows-aws-20261018/publish-verification.json`。`access-check.json` 是发布前检查，当时 job34 成功、Windows 发布尚待执行，不能把其待执行字段当作最终结果。

2026-10-19补证发布再次验证 Windows 独立操作。GitHub 云任务 `39201993431` 成功提交筹码批次 `3b84a7c7866d761f`，5577 READY、144 PASS、5433 FAIL、0 UNKNOWN。个股详情旧筛选提示的修正由 Windows 保护构建并发布为 production job40，状态 `SUCCEED`。

job40 独立构建包位于 `outputs/chips-coverage-20261018/stock-limit-evidence-site-windows.zip`，SHA-256 为 `33818c71e1e83a9d1277a083b92c02d176bf65e820671fa6430568df8e4446a7`，旧包保留。`stock.iteready.com` 及 production 域名的三条 HTML 路由与两份实际提示逻辑 JS 均逐字节匹配保护构建；私有 manifest 匿名访问仍401。回执为 `outputs/chips-coverage-20261018/site-publication-validation-3b84a7c7866d761f.json`，不包含登录后的浏览器画面验收。

当时 AWS CLI 安装在 `C:\Program Files\Amazon\AWSCLIV2\aws.exe`，`stock-dev` 使用原账户和 SSO 角色，没有复制 Mac 缓存。历史目录和主机安装位置保留作追溯，新克隆无需使用这些绝对路径。
