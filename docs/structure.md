# 目录说明

网站、认证 API、采集器和每日派生计算统一在[公开源码仓库](https://github.com/edwardxuuu-precious/ashare-opening-volume-collector)维护。项目根目录由 `git rev-parse --show-toplevel` 确定；新克隆不依赖任何旧 Mac 或 Windows 绝对路径。

## 存放规则

```text
Stock/
├── README.md                 项目入口
├── .github/workflows/         正式工作流的唯一配置来源
├── src/
│   ├── app/                  页面与样式
│   ├── components/           页面组件及 UI 基础组件
│   ├── hooks/                前端 hooks
│   └── lib/                  查询、日期、图表与认证逻辑
├── backend/
│   ├── chips/                筹码计算、筛选与连续集中度
│   ├── golden/               固定样本与黄金分割研究
│   ├── golden_projection/    每日画线与分类
│   ├── trend_volume/         趋势量价日更
│   ├── triangles/            三角筛选日更与历史行情发布
│   ├── scripts/              行情采集、校验与来源探针
│   └── server/               查询服务、云端运行器、断点导入
├── tests/
│   ├── frontend/             Node 单元测试
│   ├── python/               Python 与部署逻辑的离线测试
│   └── browser/              隔离浏览器测试及显式线上验收
├── deploy/
│   ├── actions/              运行包导出、调度与 OIDC 基础设施
│   ├── auth/                 认证 API、模板与保护构建检查
│   └── collector/            旧采集部署兼容文件
├── tooling/                  跨平台构建、计算桥接与网站发布
├── docs/
│   ├── product.md            产品与指标说明
│   ├── features/             各功能当前规则与数据边界
│   ├── development/          本地开发与采集
│   ├── deployment/           认证、部署与日更运维
│   └── verification/         历史验证记录
├── public/                   品牌、字体及其许可证
├── var/
│   ├── data/                 采集数据、原始响应、断点与样本
│   └── work/                 临时构建副本、测试工作目录
└── outputs/                  按任务组织的验证证据与发布产物
```

新增前端代码进入 `src`，业务 Python 代码进入 `backend`，开发与发布工具进入 `tooling`，测试进入对应的 `tests` 子目录。运行说明进入 `docs`；不要在源码目录散放报告。工具要求的 `package.json`、锁文件、TypeScript/Vite/Amplify 配置继续留在根目录。上述树列出主要职责，不是完整文件清单；用 `git ls-files` 查看实际跟踪内容。

新采集输出显式指定 `--out var/data/<任务名>`，临时工作文件放 `var/work/<任务名>`，可供复核的结果放 `outputs/<任务名>`。`outputs` 里的日志、截图、发布包属于执行证据，不能代替当前代码或线上读回结果。此次保留既有证据内容和按任务组织的子目录，未清理旧数据。

`node_modules`、`.venv`、`dist` 和工具缓存由各自工具管理；不要手动挪动已安装的虚拟环境。`public/data` 是可选的本地旧快照，已解除 Git 跟踪并忽略；测试只使用已提交的合成 fixture。生产构建通过 `deploy/auth/strip_public_data.py` 移除发布包中的行情 JSON。

## 工作流与发布入口

`.github/workflows/` 中的7份 YAML 是正式工作流的唯一来源，维护时直接修改并测试这些文件。GitHub Actions 检出整个仓库；不经过额外工作流副本或运行包导出步骤。

`deploy/actions/package.py` 用明确文件清单生成独立运行包。包内保留 `backend`、必要的 `tooling` 和 TypeScript 计算依赖、配置文件、依赖声明及正式工作流。导出要求目标为空，检查敏感内容和符号链接；`--probe-only` 仅导出无 AWS 权限的来源探针。旧 `tooling/export_chip_runtime.py` 在统一 Git 仓库中只检查并提示，无需先导出再提交。

网站统一通过 `tooling/release_site.py` 制包和发布。它从 `src/lib/site-routes.ts` 读取页面清单，不另存发布路由表；操作顺序见[网站构建与发布](deployment/site-release.md)。认证 API 与行情日更分别遵循[认证说明](deployment/auth.md)和[日更说明](deployment/daily-update.md)。

## 旧路径对应关系

以下路径都相对于项目根目录。历史验证正文及 JSON 证据中的路径保留当时值，查找文件时使用此表。

| 旧路径 | 当前路径 |
| --- | --- |
| `app/`、`components/`、`hooks/`、`lib/` | `src/` 下同名目录 |
| `scripts/`、`server/` | `backend/` 下同名目录 |
| `checks/*.test.mjs` | `tests/frontend/` |
| `checks/*-live.cjs` | `tests/browser/` |
| `tests/test_*.py`、`deploy/collector/test_*.py` | `tests/python/` |
| `data/` | `var/data/` |
| `work/` | `var/work/` |
| `data/verification.md` | `docs/verification/data.md` |
| `data/reconciliation.md` | `docs/verification/reconciliation.md` |
| `checks/validation.md` | `docs/verification/initial-validation.md` |
| `server/README.md` | `docs/development/service.md` |
| `scripts/COLLECTOR.md` | `docs/development/collector.md` |
| `deploy/auth/README.md` | `docs/deployment/auth.md` |
| `deploy/collector/README.md` | `docs/deployment/collector.md` |
| `deploy/collector/DAILY_UPDATE.md` | `docs/deployment/daily-update.md` |
| `deploy/README.md`、`deploy/AMPLIFY.md` | `docs/deployment/legacy.md`、`amplify-legacy.md` |

## 历史迁移与兼容边界

2026-10-20 的 Windows 统一源码目录为 `D:\Codex\ashare-opening-volume-collector`，原开发副本 `D:\Codex\Stock` 保留当时依赖及私有产物。此前 Mac 工作目录为 `/Users/edward/Desktop/Stock`，`/Users/edward/Documents/ChatGPT/Stock` 为兼容软链接。这些都是历史定位信息，不是新克隆的必需路径。

当时目录迁移发现 PID 29007/29008 的本地补采任务仍使用旧绝对路径，因此保留 `scripts → backend/scripts`、`data → var/data` 两个兼容软链接。迁移时短暂暂停进程，移动和创建链接后恢复。该记录不表示这些进程或链接目前仍存在；清理某台主机前，先检查该主机实际调用方。

旧 EC2 运行时使用 `/opt/stock-collector/scripts` 和 `/opt/stock-collector/server`；旧部署打包器与 Docker 保留这套映射。当前 Actions 的运行数据放在运行器临时目录，并持久备份到私有 S3。

## 验证入口

```sh
npm test
npm run typecheck
```

Python 版本、测试依赖和 `STOCK_PYTHON` 选择见 [README](../README.md#本地开发)。Python 单独执行 `npm run test:python`，前端单独执行 `npm run test:frontend`。这些命令执行离线检查；浏览器脚本位于 `tests/browser/`，按各脚本要求选择隔离测试或有授权的线上验收。保护构建与离线制包命令见[网站构建与发布](deployment/site-release.md)。
