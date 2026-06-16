# Learning: 投资者画像等私密数据应通过占位符引用，不硬编码在项目指令中

Date: 2026-06-16
Trigger: 用户发现 `CLAUDE.md` 与 `AGENTS.md` 标题不一致、内容大量重复，且 `CLAUDE.md` 硬编码了个人投资者画像。

## 学到的规则

1. **Claude Code 的项目指令文件必须是静态文件**
   - `CLAUDE.md` / `AGENTS.md` 会被 Claude Code 自动加载，不能依赖渲染脚本生成。
   - 若把 `CLAUDE.md` 做成模板渲染产物，新 clone 仓库的人在运行渲染前将没有任何项目指令。

2. **私密数据用文件引用占位，而非模板占位符**
   - 推荐写法：在 `CLAUDE.md` 中明确说明「投资者画像存放于 `data/private/investor_profile.md`，分析前必须先读取」。
   - 这比 `{{INVESTOR_PROFILE}}` 模板注入更实用，同时满足「占位符引入、可替换」的需求。
   - 公开约束（如 20 万上限、-30% 硬止损规则）可以保留在 `CLAUDE.md`，具体数值留在私密文件。

3. **同一项目的多个指令文件要职责互补、避免重复**
   - `CLAUDE.md` 聚焦：项目上下文、领域规则（军规/Munger）、验证协议、强制流程。
   - `AGENTS.md` 聚焦：执行入口、输出格式、失败回退、禁止行为、检查清单。
   - 重复内容只保留一份，另一份用链接引用，避免上下文冗余。

4. **通盘检查要覆盖代码和测试中的个人数值**
   - 成本价、持仓克数、止损价位等个人数据可能从 `CLAUDE.md` 泄漏到测试或策略默认值中。
   - 修复时应把默认值改为从配置读取，并把测试数据替换为通用示例值。

5. **同一类私密数字只在一个文件维护**
   - 持仓量、成本价、止损价、额度等数字必须只维护在 `data/private/portfolio.yaml`。
   - `data/private/investor_profile.md` 只保留定性画像（风险偏好、交易风格、信源偏好、笔记），数字部分用「见 portfolio.yaml」引用。
   - 避免 `investor_profile.md` 与 `portfolio.yaml` 数字不一致导致分析错误。

## 如何应用

- 任何包含用户画像/持仓/成本的项目，优先采用 `data/private/<file>.md` + `data/<file>.example.md` 模式。
- `CLAUDE.md` 中只保留「引用说明 + 公开约束」，不保留具体持仓数字。
- **持仓数字统一放在 `portfolio.yaml`，`investor_profile.md` 只写定性画像；分析前两个文件都读取。**
- 修改项目指令文件后，用 `grep` 检查追踪文件中是否仍有个人数值残留。
- 多个 `.md` 指令文件保持标题风格一致，内容互补不重复。
- 定期检查 `investor_profile.md` 与 `portfolio.yaml` 是否数字冲突。

## 相关文件

- `CLAUDE.md`
- `AGENTS.md`
- `README.md`
- `src/gold_miner/strategy/position_risk_manager.py`
- `tests/test_position_risk_manager.py`
- `tests/test_trailing_stop.py`
- `data/private/investor_profile.md`
- `data/private/portfolio.yaml`
- `data/investor_profile.example.md`
- `data/portfolio.example.yaml`
