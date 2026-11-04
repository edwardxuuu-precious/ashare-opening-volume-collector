# 网站构建与发布

网站发布入口为 `tooling/release_site.py`。先执行保护构建并生成 ZIP，审阅同一文件的 SHA-256，再携带实际读回的 Amplify 任务号发布。制包和校验已有包都可离线执行。

本文用于静态网站发布。认证 API 的更新见[账号和数据权限](auth.md)，行情采集与每日派生计算见[日更说明](daily-update.md)。提交代码、发布网站、更新行情、登录后验收分别记录结果。

## 准备仓库与依赖

在 Git 仓库根目录执行命令。需要 Node.js 22.13+、Python 3.11+，依赖安装和离线测试见 [README](../../README.md#本地开发)。`STOCK_PYTHON` 可指定解释器；下文假设已创建本机 `.venv`。

发布前核对分支、远端、未提交范围和预期上线版本。在 Linux/macOS 上先运行 `npm test` 和 `npm run typecheck`。Windows 本机先运行 `npm run test:frontend` 和 `npm run typecheck`，并核对同一待发布版本在 Linux/macOS 上的完整 Python 套件已通过。检查通过后再执行保护构建。

## 执行保护构建

macOS/Linux：

```sh
STOCK_PYTHON=.venv/bin/python \
NEXT_PUBLIC_AUTH_API=https://api.stock.iteready.com \
NEXT_PUBLIC_DATA_BASE=https://api.stock.iteready.com \
NEXT_PUBLIC_COLLECTOR_API='' \
npm run build
```

Windows PowerShell：

```powershell
$env:STOCK_PYTHON=(Resolve-Path .venv\Scripts\python.exe).Path
$env:NEXT_PUBLIC_AUTH_API='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_DATA_BASE='https://api.stock.iteready.com'
$env:NEXT_PUBLIC_COLLECTOR_API=''
npm run build
```

`npm run build` 先执行认证配置检查，再构建网站，最后移除 `dist/client/data/`。保留这些检查，不直接调用底层构建命令替代入口。

## 生成并审阅发布包

macOS/Linux 使用以下命令。Windows 将 `.venv/bin/python` 换为 `.venv\Scripts\python.exe`，其余参数相同。

```sh
.venv/bin/python tooling/release_site.py --dry-run --output outputs/releases/stock-site.zip
```

每次选择新的 `.zip` 路径，工具拒绝覆盖已有文件。工具输出包的 SHA-256 和文件信息。核对包对应预期构建版本，记录实际哈希。包内必需页面由 `tooling/site_routes.mjs` 从 `src/lib/site-routes.ts` 读取，当前包含9个页面；缺少页面、存在敏感文件路径或危险 ZIP 路径时拒绝继续。检查按文件路径判断，不证明任意文件内容均无敏感信息；审阅时仍须核对实际构建输入。读取路由要求本机 Node 满足版本要求。

如需再次检查已有包，使用其实际哈希替换 `SHA256_FROM_REVIEW`：

```sh
.venv/bin/python tooling/release_site.py --dry-run --artifact outputs/releases/stock-site.zip --expected-sha256 SHA256_FROM_REVIEW
```

以上两条 `--dry-run` 命令不调用 AWS。保留已审阅 ZIP；实际发布使用 `--artifact`，不会重新从 `dist` 制包。

## 读回线上任务后发布

AWS CLI 使用本机获授权的 SSO 配置。工具默认 profile 为 `stock-dev`，region 为 `us-east-1`；使用其他配置时显式传入 `--profile` 和 `--region`，例如 macOS 现有 `dev` 配置。不要复制其他电脑的 SSO 缓存或将账号、令牌放入仓库。

先登录并读回身份，再读取 Amplify 最新任务。以下以 `stock-dev` 为例：

```sh
aws sso login --profile stock-dev
aws --profile stock-dev --region us-east-1 sts get-caller-identity
aws --profile stock-dev --region us-east-1 amplify list-jobs --app-id d1nb0daasi2snk --branch-name production --max-results 1 --query 'jobSummaries[0].{jobId:jobId,status:status}'
```

账户须为 `838775535952`，最新任务须为 `SUCCEED`。使用本次实际读回的任务号替换 `JOB_ID_FROM_READBACK`，并替换实际包哈希后执行：

```sh
.venv/bin/python tooling/release_site.py --artifact outputs/releases/stock-site.zip --expected-sha256 SHA256_FROM_REVIEW --expected-job JOB_ID_FROM_READBACK --profile stock-dev --region us-east-1
```

工具重新检查账户和最新任务，校验已审阅 ZIP，并上传同一份已校验字节。任务号变化、任务未成功或哈希不符时停止。不要把历史文档中的 job35、job40 或其他旧任务号作为当前值。

## 核对回执与实际页面

发布回执默认写在 ZIP 旁边，名称为 `<包文件名去掉.zip>-deployment.json`。回执保留发布意图、任务号和结果。请求中断或状态不明时，先读取回执和对应 Amplify 任务；确认旧任务状态后再决定恢复动作，不直接重复创建发布。

Amplify 任务 `SUCCEED` 表示托管发布完成。继续核对实际域名的静态文件与审阅包，并使用授权测试账号验证登录、页面导航、私有数据读取和退出后的访问边界。浏览器验收脚本位于 `tests/browser/`，按本次功能范围选择；离线测试和静态文件一致不能代替登录后的界面验收。

发布产物、回执、会话与私有数据留在本地忽略目录。Git 只提交源码和对应测试、文档。
