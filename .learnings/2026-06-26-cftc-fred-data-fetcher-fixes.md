# Learning: CFTC / FRED 数据源修复要点

Date: 2026-06-26
Trigger: 用户问 CFTC 数据为何不可用、财政信用为何 fallback，要求修复。

## 学到的规则

1. **CFTC HTML 解析不可靠，应使用官方 comma-delimited 文件**
   - 旧实现用 BeautifulSoup 解析 `deacmesf.htm`，页面结构一变就失败
   - 正确数据源：`https://www.cftc.gov/dea/newcot/deafut.txt`（futures-only legacy report）
   - 该文件**无表头**，必须按列位置取值，不能用列名

2. **CFTC 文件里同时存在 GOLD 和 MICRO GOLD，必须区分**
   - 标准 GOLD 合约代码是 `088691`，市场代码 `CMX`
   - MICRO GOLD 合约代码是 `088695`，会干扰解析
   - 优先按合约代码 `088691` 筛选，次按名称排除 `MICRO`

3. **FRED 数据单位要注意**
   - `GFDEBTN`（联邦债务总额）单位是**百万美元**，需除以 1000 转换为十亿美元
   - `GFDEGDQ188S` 是债务/GDP 百分比，直接使用
   - `REAINTRATREARAT10Y` / `T10YIE` 是日度数据，财政信用按季度末重采样

4. **美元储备份额没有权威 FRED series**
   - IMF COFER 数据不在 FRED 标准 series 中
   - 保留内置历史回填，按季度末日期匹配

5. **复用项目内已有模式**
   - FRED HTTP 调用复用 `MacroDataFetcher` 模式：`settings.fred_api_key` + `get_proxied_client`
   - 失败时回退到内置数据，保持 pipeline 不中断

## 如何应用

- 配置 `FRED_API_KEY` 后，`FiscalDataFetcher` 会优先从 FRED 拉取真实季度数据
- 每周五 CFTC 发布新报告后，`CotReportFetcher` 自动从 `deafut.txt` 解析标准 GOLD 持仓
- 测试时 mock `get_proxied_client` 返回文本/JSON，验证解析路径和 fallback 路径

## 相关文件

- src/gold_miner/data/cot_report.py
- src/gold_miner/data/fiscal.py
- src/gold_miner/data/macro.py
- tests/test_new_modules.py
