# 首批加载优化验证

本轮完成 API 预连接、系统字体和登录后保留当前文档三项前端优化。受控浏览器对照的 20 个样本全部通过：已有会话的首条有效结果中位耗时由 5,787.95 ms 降为 2,908.97 ms，密码提交后由 6,761.20 ms 降为 1,374.38 ms。此结论限定于下述本地限速与合成数据，尚未发布，也未完成国内原机原网络验收。

## 代码与范围

基线与候选均包含主分支 `edf37da5437dfef9bd06be76fb799aa99f570b80`，保留新加入的三只股票自动平台锚点比较。独立目录完成本轮改动，未修改原有脏工作区。

- 初始 HTML 的 head 中加入 API origin 的 preconnect 与 dns-prefetch，预连接使用 `crossorigin="use-credentials"`。11 个 HTML 页面均已核对。框架的部分异步模块声明仍排在自定义 hint 前面；没有声称 hint 先于所有脚本，也未测量连接复用收益。
- 页面改用系统字体，移除全局网络字体引用。自然登录与首查均为零字体请求。原有静态字体资源仍保留；不同设备的实际字形可能不同。
- 登录成功直接采用服务端返回的有效用户名，更新会话并显示当前路由。SiteRouter 持续保留，私有页面在退出或过期时卸载，重新登录后重新挂载。URL 与查询条件保留，旧账号组件状态不复用。
- 登录、退出仍各只提交一次 POST，没有自动重放。旧会话请求迟到的 401 不能撤销新会话；旧登录状态检查使用 ticket 与 AbortController 废弃。被丢弃的登录检查响应和退出响应会释放 body；清理错误不覆盖已收到的 HTTP 认证结果。

服务器地区、后端接口、Cookie 有效期、Cognito、IAM 和行情存储均未更改。本轮没有生产写入。

## 测量方法与结果

两侧均使用正式构建，设置相同的 `NEXT_PUBLIC_AUTH_API` 和 `NEXT_PUBLIC_DATA_BASE`，值为 `https://api.stock.iteready.com`。Chrome / Playwright 1.56.1 使用 1440×960 视口，每个样本新建 context，禁用缓存和 Service Worker。本地静态文件经 Brotli/gzip 传输；CDP 设置 150 ms 延迟、200,000 B/s 下载和 100,000 B/s 上传。

API 由浏览器路由隔离，固定延迟 250 ms，只返回一只带罕见汉字名称的合成股票。断言实际日期行情请求发生、正确股票名称和有效数值进入表格，不将骨架或错误页面当作结果。没有真实账号或生产数据请求。

每个版本、每种场景各测五次，共 20 次；按轮次交替版本顺序。会话场景从开始导航计时；密码场景从点击提交前计时，包含提交、页面切换及首条有效结果，排除此前打开登录页与人工输入时间。诊断用的 body-drain wrapper 在正式测量中关闭，measure 模式也禁止启用它。

| 场景 | 基线中位数，范围 | 候选中位数，范围 | 中位耗时减少 |
| --- | --- | --- | --- |
| 已有会话，冷打开至首条有效结果 | 5,787.95 ms，5,751.32–7,233.66 ms | 2,908.97 ms，2,845.32–3,742.59 ms | 49.74% |
| 密码提交至首条有效结果 | 6,761.20 ms，6,700.98–6,949.33 ms | 1,374.38 ms，1,342.55–1,426.85 ms | 79.67% |

每个场景的五个同轮配对均为候选更快，两侧范围不重叠。正式测量没有失败、页面异常、意外外部请求或最终未决请求。首查及第一轮退出重登后的 networkidle 断言均通过。

| 可直接观察的变化 | 基线 | 候选 |
| --- | --- | --- |
| 已有会话首查字体请求 | 15 个，860,427 B | 0 个，0 B |
| 密码登录字体请求 | 25 个，已完成部分 1,126,356 B | 0 个，0 B |
| 密码登录主文档请求 | 2 次 | 1 次 |
| 密码登录 auth/me 请求 | 2 次 | 1 次 |
| 已有会话已完成静态传输 | 1,156,630 B | 276,042 B |

字节统计在首查网络空闲后采样，不是严格在首条结果出现时采样。基线密码登录因重载取消了 15 个旧静态请求，其未知部分不计为零成本：1,126,356 B 字体及 1,651,306 B 静态总量仅为已完成传输的下界。候选两场景均无静态取消、失败或未知传输大小。

测量开始时主机为 10 核，load average 为 18.92 / 23.45 / 20.70。只读进程快照确认有其他高负载进程，未停止用户进程；采用交替配对并报告全部范围。删除网络字体减少带宽竞争、去掉重载消除第二次文档与资源加载，是本测试可解释的主要收益。API 为固定延迟 mock，因此数字不包含真实跨境链路或全市场解析成本，也不能单独归因给 preconnect。

## 回归与实际产物

- 最终源码前端测试 371/371 通过，未跳过；包括旧请求迟到 401 与当前会话 401 的区别。类型检查通过，相关应用代码与新增测试的定向 lint、浏览器脚本语法检查、diff 检查通过。没有声称全仓 lint 或后端全套通过；既有 workbench 测试的未修改行仍有 no-useless-spread lint 错误。
- 最终正式构建成功，10 个受保护路由及 404 共 11 个 HTML 完整，三只股票画格路由存在。活动 CSS bundle 没有网络字体声明，静态字体源文件仍存在。构建和 ZIP 均通过私有数据、敏感路径及必需路由检查。
- login-card 浏览器的 17 项检查通过：1440/375/320 像素布局、品牌与图标、键盘顺序、自动填充、错误密码、重复点击保护、同文档首查、显式刷新后恢复会话、退出清空密码、A/B 账号子路由重登、退出 500 后显式重试、旧会话检查废弃。已查看桌面与手机截图，表单完整、没有横向溢出；未代替用户人工验收。
- 子路由测试通过真实 SiteLink 从首页进入 triangles，保留路径及查询，退出时旧私有内容消失，B 账号触发新的读取；没有匿名行情读取。旧检查场景实际观察到 `net::ERR_ABORTED`，只证明取消分支，未声称迟到响应已消费后被 ticket 拦截。
- 完整 ui-workbench-v2 需要未提供的行情快照，本轮没有运行；只更新其登录后文档/身份请求断言。黄金画格账号草稿存储的完整动态回归仍未执行，源码保留原有按用户划分的存储 key。

早期候选的密码场景曾在有效结果显示后超时。保留的请求诊断定位为未消费的初始 auth/me 401 body；仅在诊断 wrapper 中消费这一响应就使原 networkidle 断言通过。退出 200 同样存在丢弃 body。产品修复后，用关闭诊断 wrapper 的正式构建重新跑完上述 20 个样本；原失败与诊断报告未覆盖。另一次 UI 运行失败来自构建删除了临时 HTTP server 的工作目录，重启固定目录的 server 后通过，未修改产品或放宽断言。

## 复现与交付

测量入口为 `tests/browser/initial-load-performance.cjs`。先在干净的基线 checkout 构建主分支，再在候选 checkout 构建；两侧使用上述相同环境变量。通过 `BASELINE_DIR` 指定基线 dist/client，`CANDIDATE_DIR` 默认指向当前 dist/client。Playwright 安装在仓库外，通过 NODE_PATH 提供，不修改依赖清单。

```sh
NODE_PATH=/tmp/stock-load-browser-tools/node_modules BASELINE_DIR=/tmp/stock-load-baseline-edf37da/dist/client OUTPUT_DIR=outputs/domestic-load-20261106/performance-final node tests/browser/initial-load-performance.cjs --measure
NODE_PATH=/tmp/stock-load-browser-tools/node_modules LOGIN_CARD_URL=http://127.0.0.1:3137 LOGIN_CARD_OUTPUT_DIR=outputs/domestic-load-20261106/candidate-final/login-ui node tests/browser/login-card.cjs
```

唯一供发布审阅的包为 `outputs/domestic-load-20261106/website-reviewed.zip`，5,419,970 B，187 个文件。SHA-256 为 `16087decc979b3b930ef27d1ccbf1ac3517afedb9d103fab1b33aae7e23110bd`。打包后已通过 release_site.py 的 reviewed-artifact dry-run 回读；没有调用 publish。此前的 website.zip 是旧候选，不能用于发布。

本地完整证据位于 `outputs/domestic-load-20261106/`，不提交生成文件。主要指针：`performance-final/initial-load-performance.json`、`candidate-final/login-ui/login-browser-checks.json`、`build-checks-final.json`、`body-cleanup-verification/initial-load-performance.json` 及 `final-checks/`。基线源码/旧生产模块的对照在 `baseline-source-check.json`，只说明原始 60988dc 与 job 110 相关模块经依赖文件名归一后相同，不证明最终包与整个生产包字节相同。

发布仍需最后确认，并在当前其他发布完成后核对最新主分支和线上成功 job。发现更新、账号隔离回退、认证异常或路由缺失即停止发布并重新审阅；不覆盖排队中的其他版本。国内原机原网络的五次同条件复测，以及预连接是否实际复用，留待发布后验证。受控改善超过 30% 不等于原定国内实网验收已通过。
