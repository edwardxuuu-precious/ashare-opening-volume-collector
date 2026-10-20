# 量序 · A股数据

[线上网站](https://stock.iteready.com) · [筹码工作台](https://stock.iteready.com/chips)

本仓库统一维护网站、认证 API、采集器和每日筹码筛选代码。网站与行情接口沿用账号登录；源码公开不改变行情访问权限。2026-10-20按用户明确要求，将原本独立的网站源码并入现有 main，原 `.github/workflows`、采集调度器和依赖清单保持原样。

## 当前筹码筛选

入选须同时满足：

1. 收盘价低于10元。
2. 最近10个交易日至少一次收盘涨停。
3. 收盘价高于当日主峰价或平均成本，任一成立即可；严格大于，等于不算。

系数固定2，回放窗口210根行情，150个动态节点。原峰占比变化继续展示，“没动”阈值未设置。支持历史日期、K线点击/悬停、方向键和有界预加载；详细计算与缺失处理见 [筹码模块](docs/features/chips.md)。

## 本地开发

需要 Node.js 22.13+ 与 Python 3.11+。在仓库根目录执行：

```sh
npm ci
python -m venv .venv
```

Windows 使用 `.venv\Scripts\python.exe -m pip install -r requirements.txt`；macOS/Linux 使用 `.venv/bin/python -m pip install -r requirements.txt`。

```sh
npm run dev
npm run typecheck
npm test
```

测试会优先使用 `STOCK_PYTHON`，否则选择本机 `.venv`。完整 Python 采集器测试需要 Linux/macOS 的 `fcntl` 和 requirements 中的依赖；Windows 可运行 `npm run test:frontend` 及 `npm run test:python -- -p "test_chip*.py"`，完整套件应在 Linux/macOS 执行。`npm test` 只执行离线前后端测试，线上浏览器验收单独执行。调度器 RSA 测试需要额外安装 `cryptography==50.0.1`；未安装时该项明确跳过。

Windows 保护构建示例：

```powershell
$env:NEXT_PUBLIC_AUTH_API='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_DATA_BASE='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_COLLECTOR_API=''
$env:STOCK_PYTHON=(Resolve-Path .venv\Scripts\python.exe).Path
npm run build
```

macOS/Linux 可在同一命令前设置这三个 `NEXT_PUBLIC_*` 变量及需要的 `STOCK_PYTHON`。构建前检查认证端点，构建后禁止公开行情目录。网站继续通过现有受保护构建包发布；本次源码合并不修改 AWS 资源，也不新增自动部署工作流。详见 [Windows 发布](docs/deployment/windows-stock.md)与[账号和数据权限](docs/deployment/auth.md)。

## 源码与本地文件

- `src/`：网站页面、图表、筛选读取器与浏览器筹码计算。
- `backend/`：采集、纯计算与运行器。
- `tests/`：离线测试及显式浏览器验收脚本。
- `deploy/`、`tooling/`：构建与部署工具。
- `docs/`：功能、计算口径与运维说明。
- `public/`：品牌和带许可证的字体等静态资源。

`var/`、`outputs/`、`public/data/`、`.env*`、账号文件、密钥、主机绑定、依赖与构建产物不提交。不要把本地完整工作区、账号密码或私有行情上传到公开仓库。现有 Actions 继续通过仓库限定的 OIDC 角色写私有 S3。

## 采集运行器

Minimal collection runtime for an opening-volume dashboard. New collection uses
only Sina through AKShare. The current trading day uses one bounded whole-market
snapshot after 15:30; historical gaps use Sina daily bars. Results keep their
original source and calculate opening volume divided by daily volume without
reconciliation. Only the recent six months are restored for active collection;
the bounded manual OHLC backfill may additionally restore its frozen historical
range. Older saved dates otherwise remain queryable without repeated downloads.

The source probe runs without AWS permissions and prints aggregate diagnostics.
The production workflow writes to private S3 through a repository-scoped OIDC
role. Accounts, credentials and private market snapshots are not included.

Scheduled collection is enabled by default after the previous writer is retired.
Set DISABLE_DAILY_UPDATE to true before a review or alternate writer is started.
Configure STOCK_BUCKET and AWS_ROLE_ARN separately.
Use only a public repository and a standard GitHub-hosted runner for free compute.
GitHub execution and scheduling limits still apply. This is not a real-time feed
or a guarantee that all source records are available by a fixed clock time.
