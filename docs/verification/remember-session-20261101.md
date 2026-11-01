# 15天登录会话发布核验

2026-11-01，源码 `8814dcc8bd2425fb48a415ca7e630e1a8efe3cca` 已同步主分支并在正式认证服务生效。成功密码登录后，同一浏览器会话自登录起最多保持15天；关闭浏览器后可继续使用，短期访问令牌在后台自动续期。用户需重新登录一次，将此前按一天签发的旧续期凭证换成新期限。

## 实现与验证范围

原因由当前源码和实时配置共同确认。原访问令牌一小时过期，续期Cookie及Cognito期限均为一天，网关未使用续期凭证。改动在统一认证入口完成，各页面读取及已有批注保存沿用原请求，不增加前端刷新接口或重放POST。

请求内 `Session` 结果与单一Cookie出口落实 Model the Domain。Cookie保留 `Secure; HttpOnly; SameSite=Strict` 和主机限定；续期只更新短期访问Cookie，不延长初次登录的15天期限。来源、客户端、用途、时间及Cognito在线验证均先于数据访问。`auth_time` 限制最终期限，临近到期的访问Cookie按剩余时间截断。临时服务或网络故障返回503并保留会话，主动退出、到期、撤销及停用返回失效状态。

独立复核发现超大整数声明会令 `math.isfinite` 抛出OverflowError。修正依照 Boundary Discipline 限于外部数字输入，非法声明返回401，提供者非法响应返回503；加入实际Cookie回归并经独立复核确认。没有新增规则抑制。

`test_auth_gateway.py` 的37项以及相关认证消费者共238项检查通过，`git diff --check`通过。包含15天边界、末尾120秒截断、不滑动延长、撤销、异常声明、临时故障、原POST保存一次、Origin拒绝、gzip及错误响应Cookie合并和请求间隔离。本轮未修改前端，未重复构建或部署静态UI，也未运行无关后端全套测试。

## 实际发布

克隆实时CloudFormation模板，仅修改 `Function.Code` 和 `UserClient` 的续期有效期、允许认证流。实际运行的模型引导与模型包原样保留，认证正文与提交一致。模板生成器补回的批注PutObject条目已存在于原JSON及线上权限，本次未扩大IAM。

变更集没有资源替换，实际直接差异为认证代码及客户端两项属性。`IntegrationUri` 仅是 `Function.Arn` 的动态依赖，定义未变。变更集为 `EXECUTE_COMPLETE`，匹配执行令牌的栈事件及最终栈状态为 `UPDATE_COMPLETE`。

下载最终Lambda包，解包源码与已审阅候选完全一致，源码SHA-256为 `7df43d53114f11b25f49f93dd4fb60616511ed525d8a5b827814c11e76a33835`。运行时、Handler、角色、环境、内存、超时等14项配置保持。Cognito实时读回续期15天，访问及ID令牌仍为一小时，撤销启用，其余客户端字段保持。

`GetTemplate` 仍会将源码非ASCII字符投影成问号，与[既有模板读回限制](auth-format-release-20261007.md)一致。直接UTF-8源码模板指纹比较未通过；除已确认的字符投影外，整个模板逐字段一致。核验依据包含独立的部署包源码精确匹配、实际客户端配置、变更集执行事件及下述真实接口结果，不将模板原文误报为逐字节匹配。

## 真实浏览器与接口

使用一个随机命名、禁止发送欢迎消息的专用测试账号，密码及Cookie仅在内存中使用。没有读取、重置或退出用户的实际账号。页面行情读取在浏览器内隔离，仅直接读取一次真实受保护manifest；行情和批注写入均为0。

| 场景 | 结果 |
| --- | --- |
| 真实密码登录及Cookie属性 | 通过，续期Cookie为1,296,000秒 |
| 关闭浏览器进程后，以原持久Cookie重开 | 无需再次输入密码，真实身份接口200 |
| 移除访问Cookie，保留续期Cookie | 自动续期，原身份请求200 |
| 续期期限与Cognito `auth_time` | 原始认证时间、续期令牌及原截止时间均保持 |
| 原受保护manifest请求内续期 | 200并收到新的访问Cookie |
| 访问Cookie缺失时主动退出 | 成功撤销，旧Cookie重放401 |
| 停用专用账号后的access及refresh-only请求 | 均401 |
| 匿名私有行情访问 | 401 |

以上8项均通过。测试账号已删除，独立查询确认该专用测试前缀下剩余账号为0。15天日历流逝未现场等待，期限依据真实Cookie、提供者配置以及冻结时钟边界测试核验。浏览器重开、续期与撤销为真实Cognito及生产接口结果。

证据位于 `outputs/remember-session-20261101/verification.json`、`backend-release.json`、`production-configuration.json`及`production-session-browser.json`。Prove It Works 的核验同时覆盖实际部署包、实时配置和真实浏览器行为。没有新增备份或过程日志。

续期协议及认证时间字段对照2026-11-01读取的官方[Cognito续期文档](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-refresh-token.html)和[访问令牌说明](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-access-token.html)。这些资料只提供协议依据，实际生效结论以本次线上读回为准。
