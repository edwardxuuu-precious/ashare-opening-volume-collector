# AKShare 按需下载服务

> 本文记录早期按需服务实现及本地开发方式。生产访问与认证以 [认证说明](../deployment/auth.md) 为准。

网页保持 Sites 托管，Python 服务运行在独立云主机，个人电脑可以关闭。没有云主机地址时，网页可查询部署快照；未保存的交易日明确提示服务未接通，不模拟下载进度。

本地启动：`.venv/bin/python backend/server/service.py --out var/work/service-data`。开发网页启动前设置 `NEXT_PUBLIC_COLLECTOR_API=http://localhost:8787`。这只用于本地验证。

云主机构建：`docker build -f backend/server/Dockerfile -t stock-query .`。挂载持久目录到容器 `/data`；反向代理提供 HTTPS；设置 `ALLOWED_ORIGINS=https://ashare-opening-volume.bene-team.chatgpt.site`。站点构建时将 `NEXT_PUBLIC_COLLECTOR_API` 设置成服务的 HTTPS 根地址，重新发布即可接通。未创建付费资源，未把本地服务暴露到公网。

接口：
- `GET /api/health`：健康检查。
- `GET /data/manifest.json`、`stocks.json`、`YYYY-MM-DD.json`：允许公开的计算结果，不能读取原始日志和 checkpoint。
- `POST /api/query-jobs`：`{"dates":["2026-09-04"],"scope":"single","symbols":["000001"]}`。全市场用 `scope=all` 和空 `symbols`。
- `GET /api/query-jobs/{id}`：真实队列状态、已处理股票数、总数、完成/失败。失败不会伪装成100%成功。命中数据直接返回completed，无需轮询。

同一查询复用排队/进行中任务，完成的来源缺失记录正常显示为不可计算，避免无限重试。股票采集失败或未采集记录不会命中成功缓存。全市场任务需要数小时，只有一个工作进程写数据；最多8个等待任务，每IP每分钟最多5次新采集请求。反向代理应加连接/请求速率限制；CORS只控制浏览器来源，不充当身份认证。当前仅适用于少量朋友共用，不是大规模公开行情服务。

任务状态原子保存，重启将未完成任务标为可重试并保留股票断点。单任务5.5小时上限；浏览器停止等待不删除已保存数据。只按真实股票完成数量展示进度，进度完成不代表所有股票都有有效占比。

已保存的聚合文件与查询目录持续保留。最近6个自然月仅限制新采集、补采和工作 checkpoint；旧文件仍可通过 GET 查询。服务空闲时每日及收盘后刷新交易日历，精简工作断点。维护失败保留最后验证日历并稍后重试，不伪造休市/交易日。

新浪分钟接口默认返回1970根15分钟线，本次实测最早仅到2026-03-10下午。请求更长长度未获得有效数据，所以初次回填最近6个月的最早几日可能缺失，逐日保存后可累积完整窗口。所有缺失保留原因；不把“初始6个月回填窗口”表述成“上游保证6个月每一天都有数据”。
