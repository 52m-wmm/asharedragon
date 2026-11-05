# AShareDragon v0.2

在 v0.1 上增量接入 **BaoStock 免费历史日线与涨停重建**。已完成 2022、2024、2025 各一个月的真实分层试采；每月 24 只样本，不能当作三个月全市场已恢复。公开进度摘要见 [项目进度](docs/progress.md)。逐股核验报告 `docs/validation_v02.md` 仅保存在本机，不随公开仓库分发。

- 适配器支持历史证券目录、退市日期、未复权日线、成交量额、换手率、PE/PB、当日 ST/停牌状态。
- 重建结果单独存储，按规则版本、输入来源与批次审计；与 AKShare 实际观测分开。未知封板时间、炸板次数和封单金额不推断。
- 原五个页面增量加入历史覆盖率、月度涨停/最高连板、首板日期查询和核验明细；原有功能与数据保留。
- 支持按股或按日全市场采集、断点续传、失败重试、去重及原始响应缓存。未执行六年全量回填。

## 当前进度（2026-11-05）

AKShare → PostgreSQL → FastAPI → Next.js 已跑通，五个前端页面已完成。v0.1 已采集 8 个交易日的真实涨停相关数据；v0.2 新增 **51,401 条 BaoStock 历史日线**，包含预热窗口。2022、2024、2025 年各 1 月完成每月 24 只股票的分层试采，尚非全市场覆盖。

近期三天交叉核验在共同可判定样本内，准确率约 **99.87%–99.92%**，精确率约 **92.31%–95.87%**；**16 条误报、3 条无法核验**仍待分析，不以高准确率代替差异核查。2020 年至今的全市场历史回填尚未执行。

本次收尾实际复测：`41 passed, 1 warning`（Starlette/httpx 弃用提示）。本轮仅更新进度文档并提交，不开发功能或执行回填。下一阶段首先排查核验差异，完整待办见 [v0.3 计划](docs/progress.md#v03-待办仅记录尚未执行)。数据发布范围见 [发布说明](docs/publishing.md)。

当前运行方式不变：`./scripts/dev.sh`，访问 <http://127.0.0.1:3000>。新依赖和迁移安装方式、试采/分年回填命令详见 [免费历史数据说明](docs/history_v02.md)。

```bash
# 升级现有环境
.venv/bin/pip install -r backend/requirements.lock.txt
.venv/bin/pip install --no-deps -e backend
.venv/bin/alembic -c backend/alembic.ini upgrade head
npm ci --prefix frontend

# 小样本（worker 已运行时追加 --enqueue）
.venv/bin/python -m app.history_cli collect --start 2024-01-01 --end 2024-01-31 --sample-size 24
# 仅生成分月计划；没有 --execute 就不会采集
.venv/bin/python -m app.history_cli backfill --start 2020-01-01 --end 2026-11-05 --all-stocks --market
```

下一步应根据核验差异完善特殊交易状态、补齐历史月份供应商范围，再考虑龙头标签。基础目录的已退市记录与全市场接口仍受供应商实际覆盖约束，不保证覆盖整个 A 股历史全集。

## v0.1 基线说明（保留）

A 股涨停与龙头研究系统。本地 PostgreSQL → FastAPI → Next.js 真实数据链路；不包含模拟行情、自动交易或预测模型。

第一版已实测：2026-10-26 至 2026-11-04 共 8 个交易日三类股票池；2026-11-04 涨停 118、炸板 33、跌停 10。AKShare 的 2024/2022 涨停池请求为空，不能用于六年完整历史研究。详细证据见 [数据源报告](docs/data_sources.md)。

## 本机启动

本次环境：macOS Apple Silicon、Homebrew、Python 3.12.16、Node 24.18.0、PostgreSQL 16.15。无需 Docker。命令均在项目根目录执行；Intel Mac 使用 Homebrew 自己的路径，不硬编码 `/opt/homebrew`。

已安装依赖与迁移后：

```bash
cd /Users/a00/Projects/asharedragon
brew services start postgresql@16
./scripts/dev.sh
```

- 看板：<http://127.0.0.1:3000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>
- `Ctrl+C` 停止前端、API 和采集 worker；数据库为本机 Homebrew 服务。
- 端口已有服务时脚本会提示，请复用或停止原服务，不要重复启动。

### 在另一台 Mac 首次初始化

先执行 `command -v brew python3.12 node npm psql`、`brew list --versions`、`pg_isready`，仅安装缺失项。示例：

```bash
# 仅缺失时安装；PostgreSQL 16 或已存在的兼容稳定版本均可
brew install python@3.12 node postgresql@16
brew services start postgresql@16
createdb asharedragon
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock.txt
.venv/bin/pip install --no-deps -e backend
npm ci --prefix frontend
cp .env.example .env
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python -m app.cli calendar
./scripts/dev.sh
```

本机已创建 `asharedragon` 数据库，不要重复执行 `createdb`。默认使用当前系统用户连接 localhost，无硬编码密码；若数据库需要认证，在 `.env` 的 `DATABASE_URL` 配置用户名/密码。`.env` 已被 Git 忽略。Tushare Token 可留空，AKShare 路径不依赖它。

前端默认访问 `http://127.0.0.1:8000`。变更地址时在 `frontend/.env.local` 配置 `NEXT_PUBLIC_API_URL` 并重新启动/构建；根目录 `.env.example` 中此项是说明模板，Next.js 不自动读取父目录 `.env`。后端跨域地址通过根目录 `.env` 的 `CORS_ORIGINS` 配置。

### 分别运行 / 生产构建

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 另一个终端：执行持久化采集队列，必须运行才能处理网页任务
.venv/bin/python -m app.worker
# 第三个终端
npm run dev --prefix frontend
# 或先 npm run build --prefix frontend，再 npm run start --prefix frontend
```

绑定 loopback，仅供本机研究；未实现互联网部署所需的认证。Web 任务写入 PostgreSQL，worker 退出后排队任务仍保留。中断任务在 worker 重启时标记 interrupted，可在页面重试。PostgreSQL advisory lock 限制一个 worker。

## 采集与查询

先验证小样本，不自动启动 2020 年以来全量抓取。所有日期按 Asia/Shanghai，当前日需 16:15 后才允许收盘后采集；非交易日根据源日历跳过，未知日历范围报错。

```bash
# 同步交易日历
.venv/bin/python -m app.cli calendar
# 同步执行一天 / 范围，已有成功分区跳过
.venv/bin/python -m app.cli collect --start 2026-11-02 --end 2026-11-02
.venv/bin/python -m app.cli collect --start 2026-10-26 --end 2026-11-04
# 个股未复权日线：成交量转为股，金额为人民币元
.venv/bin/python -m app.cli daily --code 000001.SZ --start 2024-01-02 --end 2024-01-31
# 最近完成交易日增量
.venv/bin/python -m app.cli incremental
# 月份拆批回填：仅在确认源能覆盖之后运行；--enqueue 交给 worker
.venv/bin/python -m app.cli backfill --start 2024-01-01 --end 2024-01-31 --enqueue
# 强制重新请求并保留修订版本
.venv/bin/python -m app.cli collect --start 2026-11-02 --end 2026-11-02 --force
curl 'http://127.0.0.1:8000/api/events?trade_date=2026-11-04&page=1&page_size=30'
```

`pools` 采集三类池及其当日市值快照、情绪；**个股 OHLC 日线需单独选择 daily 数据集**，不会隐式请求全市场几千只股票。纯同步 CLI 适合单人维护，不应与 worker 同时提交同一分区的强制刷新；并发写入有唯一约束保护，但没有多 worker 调度。

每天收盘后可通过 macOS `launchd` 调用 `.venv/bin/python -m app.cli incremental --enqueue`；第一版提供命令，不自动修改你的系统定时任务。

## 已交付功能

- 五个真实 API 驱动页面：市场总览、涨停股票池（日期/搜索/行业/题材/数值筛选、排序分页）、个股 K 线/成交量/换手率/市值快照/事件、月份统计与热力表、采集任务和缺口管理。
- 模块化供应商子进程、超时、指数外的线性退避重试与限速、断点续采；所有响应（包括失败/空表）可审计。
- PostgreSQL 核心表、Alembic 迁移、原始响应哈希、按供应商和版本隔离；同样内容重复请求不重复插入，字段变化保留新版本。
- 同日同代码事实完全一致（忽略源展示序号）才去重；冲突重复拒绝整个分区。
- 同队列晋级率、炸板率、板数分布、完整首板可追溯的高连板启动月统计；高连板不等于后验龙头。
- 保守的历史价格规则与 Decimal 舍入、独立收益/回撤函数、严格公开时间切片接口。

## 未完成 / 真实限制

- **2020 年至今完整涨停历史未获得**。AKShare 旧日期可能空表或报错；没有伪造历史。
- Tushare 提供可选通用读取适配器，Token 未配置，权限/字段和实际范围未验证。尚未接通其标准化入库；不能把它当成已经可用的历史补全渠道。
- 概念、龙虎榜、现货接口的探测结果保存在源报告；尚未实现有效期概念回填、龙虎榜研究页面。当前概念不会用于过去特征。
- 后验标签表与版本机制已建，但**未定义或自动生成龙头标签**；对应图表明确为空。
- 历史 PE/PB、自由流通市值、全市场成交/涨跌家数、精确可公开时间、复权收益与公司行动尚缺。当前股价不用于生成可投资收益标签。
- 一字/T字板、成交可达性、停复牌/退市的完整历史状态还需可靠源，缺少时为 NULL。未核实的特殊制度拒绝推算。
- 未来收益分布、题材归因与完整个股特征流水线仍需复权行情及可靠历史状态；独立计算函数不代表该研究数据集已完备。

## 验证

```bash
./scripts/check.sh
# 单独测试：需本机 PostgreSQL，使用随机隔离 schema，结束时删除该测试 schema
.venv/bin/python -m pytest backend/tests -q
# 重跑真实源探测；原始返回写入被 Git 忽略的 data/probes
.venv/bin/python scripts/probe_sources.py --recent 20261104
.venv/bin/python scripts/probe_extended.py
```

测试仅在隔离 schema 使用合成夹具，永不灌入实际研究数据库。覆盖代码转换、制度边界、舍入、日历、连板、晋级、缺失、重复与修订、错误重试、部分失败、API、时间切片以及迁移 upgrade/downgrade/upgrade。实际验收记录见 [validation.md](docs/validation.md)。

## 目录与下一步

```text
backend/app/{providers,collectors,analytics,services,models,schemas,api,core,db}
backend/alembic/versions/   # 固定迁移，不在启动时 create_all
backend/tests/             # PostgreSQL 隔离测试
frontend/src/              # Next.js + TypeScript + Tailwind + ECharts
scripts/                   # 启动、检查与真实数据探测
docs/                      # 架构、字典、来源、方法和验收
data/                      # 本地探测、日志、截图（Git 忽略）
```

优先补充具有真实历史权限的数据源，以 2022、2024 样本交叉核验；随后建立历史股票状态/题材有效期及复权序列，再设计版本化龙头标签与季节性研究。不要先用稀疏样本训练预测模型。
