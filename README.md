# 量序 · A股数据

[线上网站](https://stock.iteready.com) · [筹码工作台](https://stock.iteready.com/chips) · [连续集中度](https://stock.iteready.com/concentration) · [趋势量价筛选](https://stock.iteready.com/trend) · [每日画线](https://stock.iteready.com/golden/daily) · [趋势三角](https://stock.iteready.com/triangles)

本仓库维护网站、认证 API、行情采集器和每日派生计算。网站与行情接口沿用账号登录；源码公开不改变行情访问权限。新克隆以 Git 顶层目录为根，不依赖旧电脑上的绝对路径。

页面字段、布局顺序、名单导航、图表查看及错误恢复遵循[界面与交互约束](docs/ui-guidelines.md)。相似功能使用共用组件与行为测试，业务日期、研究口径和画线规则分别保留。

价格图与黄金派生几何统一采用[对数价格口径](docs/features/price-coordinates.md)。原始价格、筹码质量、均线数值、成交量和百分比指标按原定义保留；历史算术画法明确标记，不继承为对数画法的人工认可。

## 当前筹码筛选

入选须同时满足：

1. 最近10个官方交易日至少一次确认收盘涨停。
2. 收盘价严格高于完整精度平均成本，等于不通过。
3. 自动顶格稳定：相对最早涨停前固定基准，逐日顶格区域位移不超过1%，各固定原峰区域占比不低于基准90%；顶格区域按峰高90%定义。

不设10元上下价格门。系数固定2，回放窗口210根行情，150个动态节点；位移精度不足只限制位移判断，原峰区域占比明确低于基准90%仍可排除，不能由缺证推成入选。支持历史日期、K线点击/悬停、方向键和有界预加载；详细计算与缺失处理见 [筹码模块](docs/features/chips.md)。

## 全市场黄金分割统计

新增[每日画线与分类看板](https://stock.iteready.com/golden/daily)：按波段、中轴及支撑阻力转换依据生成保守自动候选，使用2%收盘距离及盘中越线标签分类；有依据才画，缺证或歧义不强画。原[人工核对工作台](https://stock.iteready.com/golden)继续保留。当前规则版本、计算与时间边界见[每日画线说明](docs/features/golden-projection.md)。

原研究版格子统计与固定样本人工工作台继续保留，旧60/5选点及分层不作为当前每日画线算法。原功能口径见[黄金工作台说明](docs/features/golden.md)。

## 其他每日结果与历史研究

| 页面 | 当前入口与规则说明 |
| --- | --- |
| 连续集中度 | `/concentration` 读取每日筹码批次的连续20天及大于20天结果，见[连续集中度说明](docs/features/concentration-streaks.md)。 |
| 趋势量价 | `/trend` 展示每日筛选，`/trend/research` 保留历史研究，见[趋势量价说明](docs/features/trend-volume.md)。 |
| 金三角与死三角 | `/triangles` 默认展示后台已计算的全市场结果，也支持个股全部已保存日线的形态与关键点位标记、自定义参数研究，见[趋势三角说明](docs/features/triangles.md)。 |

云端日更不依赖网页打开或个人电脑常开。各页面以实际发布的行情日期和完整批次为准；未完成时保留上一完整结果。触发关系、后备时段和恢复边界见[日更说明](docs/deployment/daily-update.md)。

## 本地开发

需要 Node.js 22.13+ 与 Python 3.11+。在仓库根目录安装锁定的 Node 依赖：

```sh
npm ci
```

macOS/Linux 使用 `python3 -m venv .venv`，再执行 `.venv/bin/python -m pip install -r requirements.txt PyYAML`。Windows 使用 `python -m venv .venv`，再执行 `.venv\Scripts\python.exe -m pip install -r requirements.txt PyYAML`。创建前确认所选 Python 满足版本要求；`PyYAML` 用于离线工作流测试，不加入生产采集依赖。

```sh
npm run dev
npm run typecheck
npm test
```

测试会优先使用 `STOCK_PYTHON`，否则选择本机 `.venv`。完整 Python 采集器测试需要 Linux/macOS 的 `fcntl` 和 requirements 中的依赖；Windows 可运行 `npm run test:frontend` 及 `npm run test:python -- -p "test_chip*.py"`，完整套件应在 Linux/macOS 执行。`npm test` 只执行离线前后端测试，线上浏览器验收单独执行。调度器 RSA 测试需要额外安装 `cryptography==50.0.1`；未安装时该项明确跳过。

网站使用 `npm run build` 的保护构建，再由 `tooling/release_site.py` 制包和发布。构建变量、Windows 与 macOS/Linux 命令、制包审阅及发布回读统一见[网站构建与发布](docs/deployment/site-release.md)。Windows 的本机登录说明与历史验证见[Windows 开发与发布](docs/deployment/windows-stock.md)。认证 API 单独遵循[账号和数据权限](docs/deployment/auth.md)。

## 源码与本地文件

- `src/`：网站页面、图表、筛选读取器与浏览器筹码计算。
- `backend/`：采集、纯计算与运行器。
- `tests/`：离线测试及显式浏览器验收脚本。
- `.github/workflows/`：7份正式工作流的唯一配置来源。
- `deploy/`：认证、采集与调度基础设施，以及显式运行包导出器。
- `tooling/`：本地构建、计算桥接与发布入口。
- `docs/`：功能、计算口径与运维说明。
- `public/`：品牌和带许可证的字体等静态资源。

`var/`、`outputs/`、`public/data/`、`.env*`、账号文件、密钥、主机绑定、依赖与构建产物不提交。不要把本地完整工作区、账号密码或私有行情上传到公开仓库。现有 Actions 继续通过仓库限定的 OIDC 角色写私有 S3。

GitHub Actions 直接检出本仓库。需要独立运行包时，使用 `deploy/actions/package.py` 的明确文件清单导出代码、依赖声明和正式工作流，不复制整个工作区。目录职责与历史路径对应关系见[目录说明](docs/structure.md)。

## 采集运行器

新增开盘量采集固定使用 AKShare 新浪。当前交易日在15:30后执行有界全市场快照，历史缺口使用新浪日线；按原始来源保存开盘量和全天量并直接计算比例。日常恢复最近六个月工作集，已保存的更早日期仍可查询；手动 OHLC 回填只使用其冻结历史范围。

来源探针不需要 AWS 权限，只输出汇总诊断。生产任务通过仓库限定的 OIDC 角色写私有 S3，使用标准 GitHub 托管运行器。`STOCK_BUCKET`、`AWS_ROLE_ARN` 与停用变量单独配置。维护时须按[调度手册](deploy/actions/SCHEDULER.md)和[日更说明](docs/deployment/daily-update.md)关闭对应触发器、等待原写入者释放租约；单独设置 `DISABLE_DAILY_UPDATE` 不能替代全局停机。定时投递与免费源都可能延迟，不保证固定时刻行情齐全。
