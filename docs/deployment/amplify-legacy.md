# AWS Amplify 部署

认证已切换为 Cognito 独立账号及私有 S3 数据。当前账号管理、构建和访问边界以 [认证运行说明](auth.md) 为准；下方初次部署方案保留为背景。S3 已创建，行情采集器仍未部署。

目标域名：`stock.iteready.com`。网页使用 Amplify Hosting 静态托管，构建目录是 `dist/client`，不是 `dist/server`。根目录 `amplify.yml` 可供后续连接 Git 时使用；手动部署应将 `dist/client` 内的文件放在 ZIP 根目录。

## 资源与边界

- AWS CLI profile：`dev`；部署前必须用 STS 核对当前身份。
- 区域：`us-east-1`；Stock 应用：`d1nb0daasi2snk`；发布分支：`production`。
- 域名位于 Route 53 公有区域 `Z0072410BLK5SPP65UQW`。只配置 Stock 子域名及对应证书验证记录。
- 已有 IRai 应用独立管理根域名和 www；Stock 的域名关联使用 `stock.iteready.com` 和空前缀。
- 初次为手动上传。`amplify.yml` 存在不代表 Git 自动发布或行情自动采集已经启用。
- 详细部署结果以 `outputs/amplify-deployment.json` 和云端当前状态为准。

## 数据与采集方案

建议网页放 Amplify，行情汇总、采集断点和历史归档放独立 S3 路径；Python 采集器放独立运行环境。S3 和采集运行环境尚未创建，部署网页不会自动开通这些资源。

按 32 只股票、60 个交易日现有日期 JSON 的实际体积推算，全市场 5,556 只约为：60 日 116 MB、125 日 243 MB、250 日 485 MB。均为十进制单位的未压缩估算，不包括原始分钟行情、断点、备份、日志。当前代码采用最近六个月窗口，实际交易日数需按交易日历计算；容量可容纳不代表来源能回补所有日期。

网页配置 `NEXT_PUBLIC_COLLECTOR_API` 才能调用按需下载接口；`NEXT_PUBLIC_DATA_BASE` 可指定数据根地址，二者均为构建时配置。后台应独立实现任务排队、状态查询、限速、重复任务合并、存储及来源验证。不得把公网按钮直接连接为无限制全市场采集任务。

现有整批采集耗时预计数小时。Lambda 单次最长 15 分钟，不能原样承载整批任务。可在确认预算和访问控制后选择分批任务，或独立容器/主机运行采集器；暂不启用新增持续计费计算资源。

## 验证标准

1. 本地类型检查、构建通过；部署包只包含静态网页和公开数据。
2. Amplify 部署任务成功；默认域名首页和数据文件 HTTP 200，文件内容与部署包一致。
3. 自定义域名关联 AVAILABLE，DNS 指向对应托管地址，HTTPS 和数据读回通过。
4. 自动采集另行验证真实任务、数据覆盖和错误恢复。网页上线不计作自动采集成功。

## 官方依据

- [Amplify 手动部署](https://docs.aws.amazon.com/amplify/latest/userguide/manual-deploys.html)
- [Amplify 自定义域名](https://docs.aws.amazon.com/amplify/latest/userguide/custom-domains.html)
- [S3 存储概览](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html)
- [Lambda 超时上限](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html)
