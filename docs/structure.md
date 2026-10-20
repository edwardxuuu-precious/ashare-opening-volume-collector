# 目录说明

2026-10-20起，网站和采集器源码统一在 `D:\Codex\ashare-opening-volume-collector` 的 Git main 分支维护；`D:\Codex\Stock` 的原开发副本、依赖及私有产物保留。新克隆以 Git 顶层目录为根，不依赖任何旧 Mac 或 Windows 绝对路径。

此前 Mac 实际位置为 `/Users/edward/Desktop/Stock`，原 `/Users/edward/Documents/ChatGPT/Stock` 为兼容软链接；这两个路径仅保留为历史定位信息。

## 存放规则

```text
Stock/
├── README.md                 项目入口
├── src/
│   ├── app/                  页面与样式
│   ├── components/           页面组件及 UI 基础组件
│   ├── hooks/                前端 hooks
│   └── lib/                  查询、日期、图表与认证逻辑
├── backend/
│   ├── scripts/              行情采集、校验与来源探针
│   └── server/               查询服务、云端 worker、断点导入
├── tests/
│   ├── frontend/             Node 单元测试
│   ├── python/               Python 与部署逻辑的离线测试
│   └── browser/              显式执行的线上浏览器验收
├── deploy/                   部署代码和模板
├── docs/
│   ├── product.md            产品与指标说明
│   ├── development/          本地开发与采集
│   ├── deployment/           认证、部署与日更运维
│   └── verification/         历史验证记录
├── public/                   静态资源与本地网页验证数据
├── var/
│   ├── data/                 采集数据、原始响应、断点与样本
│   └── work/                 临时构建副本、测试工作目录
└── outputs/                  按任务组织的验证证据与发布产物
```

新增前端代码进入 `src`，Python 代码进入 `backend`，测试进入对应的 `tests` 子目录。运行说明进入 `docs`；不要再在源码目录散放报告。工具要求的 `package.json`、锁文件、TypeScript/Vite/Amplify 配置继续留在根目录。

新采集输出显式指定 `--out var/data/<任务名>`，临时工作文件放 `var/work/<任务名>`，可供复核的结果放 `outputs/<任务名>`。`outputs` 里的日志、截图、发布包属于执行证据，不能代替当前代码或线上读回结果。此次保留既有证据内容和按任务组织的子目录，未清理旧数据。

`node_modules`、`.venv`、`dist` 和工具缓存由各自工具管理；不要手动挪动已安装的虚拟环境。`public/data` 是可选的本地旧快照，已解除 Git 跟踪并忽略；测试只使用已提交的合成 fixture。生产构建通过 `deploy/auth/strip_public_data.py` 移除发布包中的行情 JSON。

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

## 运行兼容边界

整理时发现 PID 29007/29008 的本地补采任务仍使用旧绝对路径，因此保留 `scripts → backend/scripts`、`data → var/data` 两个软链接。迁移时短暂暂停这两个进程，完成移动和软链接创建后已立即恢复。只有确认旧进程退出、没有旧路径调用方时，才能移除兼容入口。

旧 EC2 运行时使用 `/opt/stock-collector/scripts` 和 `/opt/stock-collector/server`；旧部署打包器与 Docker 保留这套映射。GitHub Actions 运行部分保留 `backend/scripts` 和 `backend/server`，由 `deploy/actions/package.py` 明确列出可公开文件，运行数据放在运行器临时目录并持久备份到私有 S3。

## 验证入口

```sh
npm test
npm run typecheck
NEXT_PUBLIC_AUTH_API=https://api.stock.iteready.com NEXT_PUBLIC_DATA_BASE=https://api.stock.iteready.com npm run build
```

Python 单独执行：`.venv/bin/python -m unittest discover -s tests/python -v`。前端单独执行：`npm run test:frontend`。线上脚本位于 `tests/browser/`，需要各脚本指定的验收账号和授权。
