# Indicator Lab v1 实际交付与验收

页面：<http://127.0.0.1:3000/indicator-lab>。API：<http://127.0.0.1:8000/docs>。

已交付独立研究页面、导航入口、可搜索分类库、13 项真实指标、任意增删、左右轴、原始值 / Z-score、30/90 日及1/3年与自定义区间、轴联动悬停、缩放平移、隐藏曲线、浏览器本地组合保存和恢复。按用户最新要求，曲线采用 ECharts `smooth:0.25, smoothMonotone:'x'`；不修改采样值，不连接缺失值，不把视觉插值当作真实日值。

## 数据口径与复用

| 指标 | 现有来源 | 证据状态 |
|---|---|---|
| 上涨、下跌、上涨比例 | active E1 月度 Parquet；筛选复用现有 scoped_month | confirmed 是参考价合格的可观察样本，不是全市场覆盖认证 |
| 涨停、首板、二板、三板以上、最高观察连板 | active H 与既有 historical_month / stock_scope | H 中已有事件证据和规则候选合计；含候选日标 candidate，附原始分类计数 |
| 跌停、炸板率、一进二、二进三 | active E2 frozen_limits / scoped_month | supplier_diagnostic，保留固定队列分子分母及 strict_value；不提升为正式非 ST 指标 |
| QQQ 收盘价 | 现有 Nasdaq public historical QQQ Parquet | 严格时点可得性检查 / 显式回顾候选；保留美国交易日、收盘时间、截止时间和获取时间 |

只投影现有计算结果。无新数据库，不重新构建独立候选快照；2024—2026 年690日独立候选数据未混入 active 指标。A股指标按收盘后观察解释，不是开盘前特征。共同股票范围沿用现有实现；未认证身份不当作普通股票。QQQ 不受 A 股范围筛选影响。

QQQ 使用 XNAS 真实交易日历，包含夏令时、节假日及半日市。北京时间当日09:25之前，选择最近结束且满足收盘+60分钟假定发布间隔的美股交易日。实际历史发布日期仍未经认证；严格模式另要求来源观测时间早于截止时间，否则值为 null。回顾模式只能标 candidate。延展的是预期交易日历，不采集或补造价格；预期日缺价时不向前回填。

实测：2026-11-09 对齐美股2026-11-06，QQQ 使用该日既有美元收盘价（回顾 candidate；原始价保留本地）；2026-11-10 预期美股2026-11-09，缺数据，null/unknown。价格未复权、非总收益。QQQ 当前可得历史版本大多晚于历史决策时点，因此严格模式的历史空值属预期。

Z-score 使用所选窗口内非空值的总体标准差（ddof=0）；少于两点、常数序列保持 null；两轴使用相同标准化刻度。完整窗口标准化仅用于回顾图形比较，不能用作无泄漏交易特征。原始模式若同轴混合单位，页面会提示调整轴或使用 Z-score。

## API

- `GET /api/indicator-lab/catalog`：指标定义、单位、分组、范围适用性、active 版本、A 股日历范围；NQ/A股指数预留但 unavailable。
- `GET /api/indicator-lab/series?start=2026-11-09&end=2026-11-10&indicators=breadth_up,limit_up,qqq_close&exclude_st=false&exclude_ipo=false&qqq_mode=retrospective`
- `qqq_mode` 默认 strict；日期限制在已发布 A 股日历。缺失记录依然输出 null，不填0。

每条记录结构示例：

```json
{"indicator_id":"limit_up","trade_date":"2026-11-10","value":95,"unit":"家","source":"H：已有事件证据与规则候选；非官方全量认证","quality":"candidate","snapshot_version":"95191cee081053e69364","reason":null,"evidence":{"confirmed":93,"candidate":2}}
```

`meta` 含 `dates/coverage/snapshots/stock_scope/qqq_mode/version/qqq_manifest_sha256`。coverage 提供每项实际首末可用日、非空数与各质量计数。完整真实响应见 `data/indicator_lab/response-example.json`。

## 验收结果

- 后端全量：`PYTHONPATH=.:backend .venv/bin/python -m pytest backend/tests -q` → **492 passed, 5 skipped**，140.43秒。跳过的是现有依赖显式隔离fixture的测试，不是 Lab 功能跳过。
- 新增 Lab 后端6项：四种范围与现有E4逐值、质量、版本比较；QQQ跨周末、夏令时和半日市；严格历史版本门禁；缺失预期美股日；参数拒绝与null保留。
- 前端纯逻辑测试：`node --experimental-strip-types --test tests/indicator-lab.test.mjs` → **4 passed**；标准化、常数/缺失、范围、保存恢复和HTML转义。
- `npm run typecheck`、`npm run build`：通过。
- `npm run lint`：0 errors，23条既有文件警告；新页面无lint错误。
- 真API13指标：30日22交易日/1.64秒；90日58交易日/1.63秒；1年243交易日/7.76秒；3年726交易日/22.23秒；2017年自定义区间4日。见 `api-checks.json`。
- ego-browser真实页面：六条默认曲线，增加QQQ后七条同时显示；切换原始/Z-score、左右轴混合单位提示、显示开关、自定义2026-11-09至10的2交易日、ST+IPO筛选、保存组合并刷新恢复均已检查。悬停显示2026-11-09所有指标原始值及QQQ美股2026-11-06。真实数据无mock。
- 桌面1440px、手机390px无页面横向溢出；手机图表356px宽，图例滚动，证据表独立横向滚动。截图：`desktop.png`、`mobile.png`、`mobile-chart.png`、`tooltip.png`、`zoom.png`。

## 修改文件

新增：

- `backend/app/statistics/indicator_lab.py`：只读数据投影与QQQ对齐。
- `backend/app/api/indicator_lab.py`：API。
- `backend/tests/test_indicator_lab.py`：回归与时序测试。
- `frontend/src/app/indicator-lab/page.tsx`：路由。
- `frontend/src/components/IndicatorLab.tsx`：研究室。
- `frontend/src/lib/indicator-lab.ts`：标准化/日期/本地组合校验。
- `frontend/tests/indicator-lab.test.mjs`：前端逻辑测试。
- 本报告与 `data/indicator_lab/` 验收输出（本地忽略目录）。

修改已有文件仅：`backend/app/main.py` 注册路由、`frontend/src/components/Nav.tsx` 添加导航、`frontend/src/app/globals.css` 追加研究室专用样式。未覆盖其他未提交改动，未改已有看板或 Chart。

## 保护与剩余限制

保护核验：48个manifest未变；旧快照952文件、原始证据6276文件、R1正式安装903文件哈希一致；6张业务表只读指纹一致。见 `protection.json`。未切换active，未采集/购买数据，未提交Git、推送或部署公网。

长区间带身份筛选仍使用现有按月计算，首次加载会较慢；不伪造缓存结果。E2历史供应商事件覆盖稀疏，未知日断开；NQ/A股指数仍未接入。浏览器组合存储仅限当前浏览器与origin，无跨设备同步；保存的是指标、轴、显示开关、模式、区间、QQQ模式与股票范围，临时缩放和图例点击状态不保存（可通过表格显示开关保存隐藏状态）。暂无滚动相关性、领先滞后统计或预测模型。
