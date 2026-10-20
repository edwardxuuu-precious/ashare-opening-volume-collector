# 云端运行

当前生产方案为 AWS Amplify + Cognito + 私有 S3，执行与账号管理见 [认证运行说明](auth.md)。下面的 GitHub Pages / Sites 内容是早期备选方案，尚未适配独立账号，不能直接用来发布当前受保护站点。

## GitHub Actions + Pages

1. 把本项目源码放入自己的 GitHub 仓库（不含 `.venv`、`node_modules`、原始响应、checkpoint或密钥）。
2. 仓库 Settings → Pages → Source 选择 GitHub Actions。
3. 首次到 Actions → Update A-share opening volume → Run workflow，days填186（覆盖最近6个月）。手动运行会采集全市场；需要数小时，具体取决于来源响应。
4. 完成后访问部署任务给出的 Pages 网址；这就是朋友可访问的网页。项目型网址的子路径由构建自动配置。
5. 确认首次数据覆盖和费用额度后，Settings → Secrets and variables → Actions → Variables，添加 `ENABLE_DAILY_UPDATE=true`。周一至周五北京时间18:17触发，日期以源数据中已收盘交易日为准，节假日可能重新校验上个交易日。

自动更新只有变量启用后才运行。任务最多330分钟；单次连续5股请求失败熔断。只在全名单遍历完成、最新日可计算覆盖至少90%时更新页面；未达到则保留旧站，任务显示失败。页面仍逐只标记缺失和源数据差异，90%不是“100%准确”的承诺。历史与断点保存在 Actions cache；缓存可能被清理，当前采用滚动6个自然月保留规则，窗口外数据自动清理。

GitHub定时任务可能延迟；公开仓库60日无活动可能自动禁用定时任务，需在Actions重新启用。账号可用额度、公开/私有仓库费用和Pages访问范围以账号当前设置为准。未自动新建仓库或购买服务。

官方依据：
- https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## 按需下载

`backend/server/service.py` 已实现AKShare任务队列、进度查询和持久缓存。部署步骤见 [backend/server/README.md](../development/service.md)。GitHub Actions适合定时批处理，本身不提供交互网页的常驻任务API。公网按需下载仍需独立Python云服务，当前未激活。

## 外部数据更新

Sites当前版本展示部署时的快照。若以后采用独立云采集器，可将计算结果发布到允许网页读取的地址，构建时设置 `NEXT_PUBLIC_DATA_BASE` 为其根网址（不要带 `/data`）。网站仅读取公开汇总文件，不暴露采集程序及其配置。
