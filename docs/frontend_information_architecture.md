# AShareDragon 前端信息架构整理验收

定位：A 股历史指标与交易研究工作台。本轮只调整入口、品牌文案和质量区块锚点，未重写图表、API或统计；11个路由全部保留。

## 完整路由与实际功能清单

| URL | 页面组件与实际功能 | 决策 | 新入口 |
|---|---|---|---|
| `/` | Research.Dashboard → HistoryLimits overview → MarketSentiment：按历史日期展示广度、涨跌停、梯队、晋级、正负信号、D1与股票列表 | 保留；导航改名市场总览 | 一级市场总览 |
| `/indicator-lab` | IndicatorLab：13指标自由叠加、平滑曲线、Z-score、左右轴、日期范围、QQQ时序对齐、质量证据、本地组合保存 | 保留；统一自由指标曲线入口 | 一级指标研究室 |
| `/stock` | StockResearch：个股未复权K线、成交量/换手率/市值、涨停历史、周期证据；带history_date参数时用HistoricalCandles定位历史K线 | 保留 | 一级股票研究；二级个股与K线 |
| `/pool` | HistoryLimits：按日期/周期、证据、范围、连板高度筛选；梯队、晋级队列、分页股票列表与K线跳转 | 合并导航层级，保留完整页面 | 股票研究 → 涨停股票池 |
| `/dragons` | DragonCenter：启动规律、非IPO研究、历史连板排行、历史研究与证据；AnnualStudy/FeatureStudy/ObservedStreaks及后验样本 | 合并导航层级，保留 | 股票研究 → 连板与龙头 |
| `/seasonality` | DragonCenter seasonality：共用组件的季节性入口，NonIpoStudy、SeasonalityExploration、证据/敏感性与排行页签；不等同于自由曲线 | 合并导航层级，保留 | 股票研究 → 季节性研究 |
| `/data` | DataInventory：来源原始/标准化/派生资产、覆盖、来源明细、快照版本、父依赖与字段级质量 | 保留；降为二级 | 数据管理 → 数据 Inventory |
| `/data#field-quality`（锚点，非额外页面） | 既有“标准化与派生快照”质量区块，新增稳定id | 合并入口，不新建质量页面 | 数据管理 → 数据质量与版本 |
| `/data/operations` | Research.DataManagement + HistoryOperations：创建/重试任务、状态、缺失日、源错误/日志、历史试采与核验 | 保留；归入二级 | 数据管理 → 采集任务与日志 |
| `/data/lifecycle` | 既有服务端redirect('/data')，无独立页面功能 | 隐藏独立入口（原本即无）；保留兼容跳转 | 仍可直接访问并跳转Inventory |
| `/supplier` | SupplierOverview → LegacyDashboard：供应商当前事件池、数量趋势、炸板率、最高连板与梯队 | 保留；不把供应商池当作统一历史口径 | 数据管理 → 供应商行情 |
| `/supplier/pool` | SupplierPool → LegacyPool：按日期、代码/名称、行业、用途等筛选供应商股票池 | 保留 | 数据管理 → 供应商股票池 |

以上11个页面文件全部读取，并检查其实际挂载组件。`Research.Seasonality`、`HistoryOverview`等旧组件不是独立Next.js路由；本轮不清理未挂载或共享代码。

## 改造前后导航

| 原一级入口 | 新位置 |
|---|---|
| 历史涨停概览 | 市场总览 `/` |
| 涨停股票池 | 股票研究 → 涨停股票池 |
| 个股研究 | 股票研究 → 个股与K线（一级默认打开原 `/stock`） |
| 龙头研究中心 | 股票研究 → 连板与龙头 |
| 季节性研究 | 股票研究 → 季节性研究 |
| 指标研究室 | 指标研究室，顺序提升到第二项 |
| 数据资产 | 二级可展开数据管理 → Inventory / 质量与版本 / 采集任务与日志 / 供应商视图 |

一级固定顺序：**市场总览 → 指标研究室 → 股票研究**。股票路由显示股票研究子导航；数据管理始终可展开，在数据/供应商路由默认展开。手机一级三项直接可见，二级链接换行，无横向隐藏主入口；增加语义导航标签、当前页标记与键盘焦点样式。

## 覆盖判断与未来淘汰边界

Indicator Lab 已有工程验收报告与真实浏览器证据，但不能据此认定用户已批准删除旧功能。它覆盖自由历史指标与QQQ曲线，不覆盖市场总览的信号/D1/联动股票列表、供应商字段、季节性统计及周期分类。因此本轮**不隐藏这些功能入口，不删任何页面**。

代码检索中，QQQ仅出现在IndicatorLab组件，没有第二个QQQ页面可合并。历史涨停 overview 分支目前已挂载 MarketSentiment；旧实现里的部分统计绘图代码不能当作正在导航展示的独立页面。

未来可评估：`/dragons`与`/seasonality`共享研究页签的入口进一步统一；供应商概览中真正等价的趋势展示可指向Lab。但必须先证明字段、质量口径、筛选、交互和深链接均被覆盖并经用户验收。本轮没有将任何可用业务页面标为可立即淘汰；`/data/lifecycle`仅为既有兼容别名，仍保留。

## 实际修改

- `frontend/src/components/Nav.tsx`：三项主导航、股票子导航、数据管理折叠入口。
- `frontend/src/lib/navigation.ts`：集中定义导航与路由归属。
- `frontend/src/app/layout.tsx`：产品定位、标题与描述。
- `frontend/src/app/globals.css`：追加局部导航响应式/焦点样式。
- `frontend/src/components/DataInventory.tsx`：仅添加 `field-quality` 锚点。
- `frontend/tests/navigation.test.mjs`：4项导航回归。
- 本报告及 `data/frontend_ia/` 本地验收输出。

未改路由page文件、API、图表组件、数据管线或active指针。未删除/重定向任何新路径；生命周期跳转是原有行为。

## 验证

- `node --experimental-strip-types --test tests/*.test.mjs`：8项通过（4导航+4指标研究室）。
- `npm run typecheck`：通过。
- `npm run lint`：0错误，23条既有警告。
- `npm run build`：通过，全部既有路由仍生成。
- `git diff --check`：通过。
- 真实ego-browser验证市场总览加载4002只上涨等实际数据；一级链接进入股票研究，子导航高亮正确；逐一打开所有旧路由，`/data/lifecycle`仍跳转`/data`。
- 数据管理不触发采集、重试或刷新写入；仅GET查看。质量锚点复用已有区块。
- 桌面1440px与手机390px验证主导航、股票子导航及数据管理折叠；截图和页面观察记录见 `data/frontend_ia/`。
- 本轮是导航改造，未重复运行后端数据计算测试；所有原有组件/逻辑保持。

可访问：<http://127.0.0.1:3000/>。证据：`desktop-market.png`、`mobile-data.png`、`mobile-stock.png`、`browser-routes.json`、测试/构建日志与`protection.json`。

剩余限制：旧页面内部标题继续保留其具体功能名；季节性与龙头研究仍有共用页签，按要求未提前清理；日志仍为现有任务/源错误视图，没有新建日志系统。各页原有数据质量限制不因导航改名而改变。
