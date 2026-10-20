# N1–N3 诊断与实现证据

任务与范围见 [阶段计划](n123-stage-plan.md)。本记录只描述已执行的诊断与本地验证；生产版本、自然周期和最终收口由 `outputs/n123-convergence/` 的发布与验收证据确认。

## 诊断结论

- 实际日志显示 opening 运行保存断点后以 `source_retry_not_due / incomplete_checkpointed` 结束，没有 worker 异常退出。断点含 5,557 份开盘量，缺少的 15 只均取得精确目标日的交易所全日停牌证据。
- 开盘路径只统计数值缓存，未承认正式无成交证据，是重复补采的已确认原因。全天 `unprocessedCount` 被混同为开盘未处理数会放大问题，但不意味着数值缓存丢失。
- 原发布状态只有工作目标/兼容日期，未显式提供目录已发布日期。页面对退避、失败、暂停和等待收盘的表达也需要分别处理。
- 认证接口读取未保存日期时返回 503，API access log 确认是函数响应；私有对象与目录均无该日。读角色没有 ListBucket 权限，S3 将不存在的对象返回为 AccessDenied，原接口仅将 NoSuchKey 映射为未保存。行为依据：[AWS GetObject 权限说明](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)。
- 已缓存值保留、SHA 绑定恢复检查及恢复回归通过，当前证据不支持断点丢失导致本次问题。

来源：`initial-run-evidence.json`、`opening-cache-evidence.json`、`opening-exchange-evidence.json`、`historical-baseline.json`，均位于 `outputs/n123-convergence/`。正式无成交不能由空响应、名称、失败次数或旧日期状态推断。

## 失败反馈与修复

| 命令/边界 | 修复前 | 修复后 |
| --- | --- | --- |
| `python -m unittest discover -s tests/python -p test_refresh_worker.py -k opening_resume_reports_cache -v` | 开盘缺口指标为空，不能正确表达缓存和退避 | 独立表达 2 份数值缓存、1 份待重试与全天未完成 |
| `python -m unittest discover -s tests/python -p test_refresh_worker.py -k opening_exact_day_official -v` | 正式全日停牌仍请求分钟量 | 原 worker/fetch 边界不再请求分钟量，正式证据单独保存；不写 0、不发布全天 |
| `python -m unittest discover -s tests/python -p test_cloud_worker.py -k opening_status_separates -v` | 已发布日期字段为空 | 真实 Publisher 从已提交目录读出前一已发布日，与本次目标分开 |
| 前端 collection-status 回归 | 退避、失败/暂停及开盘未完成文案反例失败 | 保持最后成功时间，正确区分等待补采、数据源退避、失败、暂停、等待收盘及待发布 |
| `python -m unittest discover -s tests/python -p test_auth_gateway.py -k unpublished_date -v` | 真实 gateway/get_object 边界返回 503 | 先查已提交目录；未保存日期返回 404，不向不存在的对象请求读取；已保存对象的 AccessDenied 仍返回 503 |

## 实现与保护

`refresh_worker.py` 增加经既有官方证据校验的 `openingNoTrade`，拒绝错日、错股票、无效或与数值缓存冲突的证明。开盘计数与全天计数独立；旧断点兼容，正常有限退避保留。

`cloud_worker.py` 从 Publisher 的已提交目录派生已发布日期。前端解析可选新字段并检查计数守恒；缺失不补零，传输错误保留实际错误，原方案 B 布局与交互基线保留。

公开采集发布清单仅包含两个运行文件及两个对应测试文件。未修改工作流、权限、预算、来源策略或采集目标选择。私有成交量正文、凭据和签名 URL 不进入公开仓库或证据记录。

`deploy/auth/handler.py` 的日期读取以已提交目录为准，保留认证、压缩及真实存储错误。API 发布使用原栈的 Function.Code 唯一变更，无替换，无 IAM 或函数配置变更。部署源码使用 ASCII Unicode 转义，与本地源码 AST 一致；基线绑定实际下载的函数 ZIP，部署后读回实际源码和模板。部署准备的瞬态 CREATE_PENDING 继续核对同一 change set，没有重复创建或执行。

## 本地验证

- 独立公开采集 checkout：283 项通过，失败/错误/跳过均 0；外部传输被拦截，真实外网请求 0。详见 `collector-release-tests.json`。
- 根仓库 worker 40 项、runner 41 项、Publisher 33 项通过，覆盖正式证据、正常退避、无证据缺失、恢复与发布边界。
- 前端 123 项通过；TypeScript、lint、保护构建通过。构建不含公开 `data/` 目录。
- 认证、日志配置和保护打包 28 项通过；压缩测试 fixture 补入实际读取所需的已提交目录，完整行情解压一致性断言保留。失败及通过证据见 `n123-auth-red.log`、`n123-auth-tests.log`。

本地验证不能替代自然交易日运行、私有落盘、认证接口及真实页面验收。外部来源缺失或生产计数矛盾时，对应任务保持未完成。

## 自然收盘发现的单代码遗漏

自然收盘运行 `37743810526` 首轮保存检查点后仍有 11 项缺口；自然补采 `37745616075` 补齐 10 条停牌说明，剩余 689009 的日成交量和价格帧。新浪批量快照遗漏该代码，新浪和腾讯历史接口均成功返回但没有目标日记录；新浪按代码报价已返回精确目标日、收盘时间、成交量及完整价格帧。证据见 `rare-symbol-source-readback.json`、`rare-symbol-realtime-readback.json` 和 `post-first-pass-readback.json`。

`sina_spot.py` 在既有最多 10 个遗漏代码的路径中，优先读取同源新浪报价。必须匹配股票标识、目标日、15:30 或更晚的报价时间、正整数成交量及完整合理的 OHLC；空、错日、盘中、异常或部分价格均拒绝。失败时保留原有备用与有限重试。成交量直接采用来源记录，不用分钟合计生成日成交量，也不修改已经保存的数值。

实际 loader/fetch 场景修复前 `availableCount=99`，修复后为 100，保持已缓存开盘量并使用真实同源日成交量。独立公开采集 checkout 的 293 项回归通过，外网真实请求为 0；新增报价校验、真实 fetch 及 10 个代码上限反例均通过。证据见 `rare-symbol-red.log` 和 `rare-collector-release-tests.json`。

验收脚本也分别核对原始数值类型和正确状态源：分钟成交量字符串先转数值；带交易日历的观察以 `collector/refresh-status.json` 为准，公开小状态对象单独做接口一致性比较。脚本缺少日历或字符串求和错误不记为产品故障。最终自然接续和完整发布仍须真实验证。

来源读回还保留 689009 的实际分钟合计 6,978,387 股与收盘报价 6,981,392 股相差 3,005 股。当前 `download_only` 口径直接保存开盘和全日来源记录，不要求二者合计相等；没有用分钟合计覆盖日成交量，也不称其为全天分钟一致性核验通过。上交所现行规则第 3.7.10 条将盘后固定价格交易计入当日总成交量；此为可能的口径解释，尚未取得该代码的盘后明细，差额成因保持未知。来源：[上海证券交易所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml)。

## 正式无成交记录的页面读取边界

真实查询 `000016 / 2026-10-08` 返回有效停牌记录，但省略开盘量字段；前端将该字段保持为 `undefined`，表格 tooltip 的数字格式化触发 React 错误。浏览器 URL、具体异常与截图见 `browser-no-trade-error.json`。

共享的 `normalizeDownloadedRow` 将省略的开盘量和日成交量明确归一为 `null`，供表格、明细和图表使用；已有数值 0 与其他源字段保持。实际 query-loader 读取原场景的回归先失败（`undefined != null`），修复后通过，原始来源对象未改写。前端 124 项、类型检查和 lint 已通过；保护构建、发布与同一线上链接复验由对应证据确认。
