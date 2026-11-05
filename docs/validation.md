# 第一轮验收记录

后续 v0.2 免费历史接入验收见 [validation_v02.md](validation_v02.md)。本文件保留 v0.1 基线。

执行日期：2026-11-04（America/Los_Angeles）；数据截止交易日：2026-11-04。验证均在本机实际执行。

## 环境

macOS Apple Silicon / Homebrew；原已有 Node.js 24.18.0、PostgreSQL 16.15_1；安装 Python 3.12.16。Docker 客户端存在但 daemon 未运行，本项目没有使用 Docker。已启动本机 PostgreSQL 16 并创建 `asharedragon`。Next.js 16.4.5、AKShare 1.20.27；详细依赖由 lock 文件固定。

## 数据与核心链路

- AKShare → 标准化 → PostgreSQL → FastAPI → Next.js，实际运行成功。
- 2026-10-26 至 2026-11-04 共 8 个交易日，涨停 544 条、炸板 271 条、跌停 211 条。
- 2026-11-04 当日：118 涨停、33 炸板、10 跌停；炸板率 33/151≈21.85%，最高连板 3，二板晋级 18.18%，三板晋级 50%。
- 2026-10-29 原始涨停池 39 行含一组事实相同的重复；保留原始载荷，标准化为 38 只。
- 日线 146 条：000001.SZ 在 2024-01 有 22 条；000001.SZ 与 600892.SH 在 2026-08-03 至 11-04 各 62 条。无估值字段伪填。
- 2024/2022 历史涨停池空响应已写入缺口和原始错误记录。探测证据见 data_sources.md。
- 浏览器创建任务 `cf7894ae-8de4-4603-9ba2-86ab00c75a18`，队列 worker 实际完成；已采集分区全部跳过，插入 0 条。
- 已确认实际数据库不存在测试夹具“测试样本”记录。

## 自动化检查

`python -m pytest backend/tests -q`：**29 passed**。测试通过随机 PostgreSQL schema 隔离；fixture 自动执行 Alembic upgrade，结束时 downgrade → upgrade，再删除专属 schema。

涵盖股票代码与交易所验证、ST/板块/改革日期/上市期、精确舍入与官方价格优先、交易日/节假日/未知范围、连板缺口、同队列晋级率、零分母、炸板率、未来完整窗口、单位转换、重复响应幂等、完全一致去重与冲突拒绝、失败刷新保留历史、整个池修订删除、分区失败隔离与计数、超时重试、API 错误结构、时间切片、跨年首板月份、90 日时间轴缺口。

`alembic check`：No new upgrade operations detected，模型和固定迁移无漂移。

`npm run build --prefix frontend`：生产构建、TypeScript 检查、五个页面生成成功。

测试有一条上游 Starlette 提示：当前 TestClient 的 httpx 路径未来将弃用，建议 httpx2；本次请求及断言正常。未通过忽略警告伪装无告警。

## 浏览器检查（ego-browser）

- 首页读取 PostgreSQL 的实际指标及 ECharts 图表；最终时间轴有 90 个源交易日，未采集数据为 NULL。
- 涨停池搜索“大晟文化”由 118 条过滤到 1 条，点击进入 600892.SH。
- 个股实际 K 线、成交量、换手率图；涨停表包含事件日期。
- 季节性图、历年热力表、覆盖说明和空标签展示。
- 数据管理任务列表、错误、缺口，以及提交采集任务后 worker 成功完成。
- 390×844 手机视口验证 documentWidth=390，无页面水平溢出；宽表在自己的容器内滚动。
- 截图在本机 `data/dashboard-final.png`、`data/stock.png`、`data/mobile.png`、`data/data-management.png`，未提交 Git。
- 最新代码下 `/api/health`、`dashboard`、`events`、`stocks/600892.SH`、`seasonality`、`data/status` 均实际 HTTP 200。
- `scripts/dev.sh` 已实际启动并验证，其 Ctrl+C 清理本次 API、worker、Next 进程。验收结束停止预览服务，PostgreSQL 和已采集数据保留。重新启动见 README。

## 未达到的完整研究能力

六年历史数据、Tushare 账户权限、历史概念/ST/退市状态、复权可投资收益、正式龙头标签、全市场广度尚未完成。现货与概念成分接口本轮超时。第一版可以进行已覆盖样本的事实查询和基础统计，不应据此声称得到 2020 年以来完整季节性规律。
