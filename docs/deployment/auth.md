# Stock 独立账号与私有数据

当前入口：`https://stock.iteready.com`。每人独立账号；关闭自行注册。没有有效登录会话时，页面不挂载查询程序，后台拒绝行情数据请求。密码交给 Cognito 验证，不存进前端代码或浏览器本地存储。

## 管理账号

只使用账号所有者已登录的 AWS CLI（profile `dev`），不开放公网管理接口，也不自动发送邮件。

```sh
python3 deploy/auth/accounts.py create friend01
python3 deploy/auth/accounts.py create friend02 --password-length 8
python3 deploy/auth/accounts.py list
python3 deploy/auth/accounts.py disable friend01
python3 deploy/auth/accounts.py enable friend01
python3 deploy/auth/accounts.py reset friend01
python3 deploy/auth/accounts.py vault
```

账号密码统一保存在 `~/.config/stock-iteready/accounts.json` 的 `accounts` 中。创建和重置自动更新这份总表；`vault` 命令显示文件位置并迁移旧文件。目录权限 700、文件权限 600；这是本机明文凭据文件，不是加密密码库，不放入仓库或网站。账号名为 3–64 位英文、数字、下划线、点或短横线；只有用户明确要求发给指定人时，才通过指定渠道发送凭据。

迁移先原子写入、读回核对，再移除分散的旧文件；发现冲突停止并保留原件。旧 Amplify 临时访问凭据归入 `historical_hosting_access`，不作为网站用户账号使用。重置过程中候选密码先写入 `pending_password_changes`，云端成功后才替换账号现有密码；如果出现中断或失败，先核对云端结果再处理候选，不能把候选当作已生效密码。停用/启用状态以 `list` 的云端查询为准。

密码最低 8 位，须包含大小写字母、数字和符号。`--password-length 8` 会生成避免易混淆字符的 8 位密码，符号固定为一个 `!`；不指定长度时仍生成较长随机密码。重置也可使用这个参数。

停用账号会撤销已有令牌；重新启用后需要重新登录。重置先撤销已有会话，再设置新密码。退出登录撤销当前会话，后台会向 Cognito 检查撤销状态。会话有效期为一小时，到期重新输入密码。

## AWS 资源

- CloudFormation：`stock-private-access`，区域 `us-east-1`。
- Cognito：`us-east-1_jmyxYnjZU`，客户端 `17ug7t1rt7qophil83hsboj1ce`，只允许管理员创建用户。
- 私有数据桶：`stock-private-access-databucket-bmzud610gtex`，公开访问全部阻止，开启版本控制与静态加密。
- API：`https://api.stock.iteready.com`，Lambda `stock-auth-api`。API Gateway 默认执行域名已关闭。
- `POST /auth/login`、`POST /auth/logout`：必须来自 Stock 的准确 Origin。
- `GET /auth/me`、`GET /data/manifest.json`、`GET /data/stocks.json`、`GET /data/collection-status.json`、`GET /data/YYYY-MM-DD.json`：必须有此用户池及客户端签发、未过期、未撤销的访问令牌。
- 会话只放在 `Secure; HttpOnly; SameSite=Strict` 的主机 Cookie 中。身份和行情响应均不缓存。
- 普通登录账号没有 AWS 管理权限，不能创建用户或写入行情。后台角色只可在指定用户池登录和读取指定桶的 `data/` 路径。
- 原 Sites 网页已收为仅所有者可访问，不能作为匿名绕过入口。

## 构建与发布

```sh
NEXT_PUBLIC_AUTH_API=https://api.stock.iteready.com \
NEXT_PUBLIC_DATA_BASE=https://api.stock.iteready.com \
npm run build
```

`npm run build` 先检查私有接口配置，构建后移除 `dist/client/data/`。源目录 `public/data` 只保留可选的本地开发快照，已从当前 Git 索引移除并忽略；不能直接上传到公开存储；不要手动运行底层构建命令绕过打包检查。Amplify 的同名构建变量已配置，`NEXT_PUBLIC_COLLECTOR_API` 暂为空。

初次认证部署沿用上一版已验证的 32 只股票、60 日数据快照，单独放入私有桶。同期其他日历/采集代码修改没有并入本次发布。准确部署输入和权限读回见 `outputs/auth-build-input.json`、`outputs/auth-deployment.json`、`outputs/auth-live-verification.json`。

更新行情应先做数据验证，再通过拥有写权的采集器更新私有桶，最后更新 manifest；不可重新恢复公开数据文件。按需下载 Python 服务尚未部署，也尚未接入这套会话校验，不得把未认证的采集服务地址配置给公开网页。

`collection-status.json` 是采集器写入私有桶、展示给已登录用户的简要进度，不开放内部 `run-status.json`、断点、日志或写接口。只包含采集任务 ID、状态、已处理/目标股票数、目标日期、更新时间和可展示说明；不要写入主机、命令、异常堆栈或凭据。

超过 64 KiB 的行情 JSON 在客户端 Accept-Encoding 允许 gzip 时压缩，Lambda 返回 base64 标记与 Content-Encoding，由 API Gateway 解码二进制、浏览器解压。响应按完整 Lambda JSON 封装大小校验，超过 5,900,000 字节返回小型 413 错误，避免超过同步响应上限；生产发布采用更严格的 5.3 MiB 安全线，增加 OHLC 后必须以真实最大日期文件实测，超过安全线即停止发布并先设计分页或字段投影。未登录和其他小错误继续返回普通 JSON。5,556 行完整 OHLC 的合成测试必须验证压缩、Base64 封装和解压数据一致；合成结果只证明代码容量边界，不能代替线上最大对象读回。

认证 Lambda 的小范围代码更新：先重新生成 `template.json` 并通过认证测试；读取线上 `stock-auth-api` 的 `RevisionId`、Handler 和 CodeSha256，确认 Handler 为 `index.handler` 后，只将当前 `handler.py` 以 `index.py` 文件名打包。使用 `UpdateFunctionCode` 携带刚读取的 RevisionId，避免覆盖并发更新；不发送任何函数配置、环境变量或权限修改。等待 LastUpdateStatus 为 Successful，核对部署包 CodeSha256，再以匿名和合法会话分别检查进度接口。此方式只更新代码，会产生 CloudFormation 代码漂移，须在发布记录中保留并随后将既有栈模板的 Function.Code 同步为相同源码；不可直接拿本地全量模板覆盖未经核对的线上资源配置。

栈同步使用变更集的实际属性差异和动态依赖确认范围，以 `EXECUTE_COMPLETE`、匹配 `ClientRequestToken` 的栈完成事件及 `UPDATE_COMPLETE` 共同确认本次执行完成。最终下载函数部署包，校验包指纹和解包源码，并逐字段比较函数及 API 集成配置。2026-10-07 的中文模板读回限制与等价模板验证见 [认证发布验收](../verification/auth-format-release-20261007.md)。

## 读取容错与访问日志

查询、目录、股票名单、采集进度和初始登录状态的 GET 共用 `src/lib/read-request.ts`。瞬时网络故障及 HTTP 429、500、502、503、504 最多尝试 3 次，总等待有截止时间，退避时支持取消。POST 登录和退出仍只提交一次；401 和内容格式错误直接交给原有处理。日期数据每组最多并行读取 2 天，最终失败取消其他未完成读取，并保留成功日期的缓存。

页面结果绑定完整查询条件。切换股票或日期后，旧查询的结果及附属提示不参与新查询；同一查询的后台刷新失败则保留已有有效结果并显示恢复入口。目录与股票名单各自提供重新读取，不清空用户已选条件。

HTTP API 的访问日志组为 `/aws/apigateway/stock-auth-api-access`，保存 14 天。日志仅记录请求 ID、时间、方法、路径、状态、耗时和 Lambda 集成状态，不保存 Cookie、令牌、密码、请求体或用户身份。可用浏览器响应中的 `apigw-requestid` 对照日志，区分 API 限流、函数集成失败和业务响应。

新增日志使用 `deploy/auth/access_logging.py` 的最小模板差异检查。现有栈若有代码更新造成的漂移，须克隆当前线上模板后仅添加 AccessLogs 和 Stage.AccessLogSettings，检查变更集无替换，再执行。2026-10-03 日志发布保留了现有 Lambda CodeSha256；不得使用本地全量模板覆盖其他线上配置。

容量核对同时读取 Service Quotas 和 Lambda `get-account-settings`，以实际生效值判断。2026-10-03 修复后的区域并发为 1000，原值为 10；2026-10-07 再次读回仍为 1000，申请 1001 的工单已为 CASE_CLOSED，没有观察到 1001 生效。API 默认限速维持 20 请求/秒、突发 40，登录路由维持 1 请求/秒、突发 5。

本次计划与完整验证索引见 `outputs/read-reliability-20261003/plan.md` 和同目录 `result.json`。网页发布任务为 Amplify job 25；生产资源逐个哈希检查、真实 7 天查询和访问日志读回分别保存在 website/production-assets.json、production-browser-readback.json 和 access-log-readback.json。

## 验证与恢复

运行 `.venv/bin/python -m unittest discover -s tests/python -p 'test_auth*.py' -v` 检查身份边界和打包规则。线上验证脚本 `verify_live.py` 只操作专用测试账号 `stock-auth-check`，会停用该账号；测试凭据和账号用完后删除，不用于发放。

发布后必须验证：匿名/错误凭据 401、合法账号可读数据、停用和退出后的旧令牌 401、匿名 S3 403、网站 `/data/*.json` 不再返回原始行情。不要以隐藏页面或 CORS 代替身份验证。回滚网页时也必须使用已经移除公开数据的版本；原始公开快照不能直接回滚。

需要临时关站时，可通过 Amplify 的访问控制加一层随机密码；确认后台和私有资源正确后再恢复正式登录页。不要通过关闭 Cognito 检查或放开 S3 公开访问来排障。

官方依据：[Cognito 会话撤销](https://docs.aws.amazon.com/cognito/latest/developerguide/token-revocation.html)、[GetUser 在线验证](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html)。


## 筹码校准页面

新增页面 `/chips` 与认证GET `/data/chips/manifest.json`、`/data/chips/<16位版本>/<六位代码或screening>.json`，使用同一Cognito、私有桶、撤销检查和no-store响应，不增加写接口或权限。公开静态包不含这些JSON。当前仅三只股票四日校准样本，系数2、稳定阈值null、全市场每日任务未启用。使用与发布说明见 [筹码模块](../features/chips.md)。
