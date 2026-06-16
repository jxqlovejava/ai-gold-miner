# Learning: 项目瘦身与可用性改造实践

Date: 2026-06-16
Trigger: 用户对项目架构/可用性提出批评后，执行了一次完整的瘦身 + 可用性改造。

## 学到的规则

1. **大文件必须先拆分，再谈优雅**
   - `cli.py` 1,810 行是项目最明显的坏味道。
   - 拆分为 `cli/` 包后，虽然总行数没大降，但模块边界清晰，新增命令成本大幅降低。

2. **Demo 模式是降低新用户门槛的最有效手段**
   - 0 API key 即可运行的 `--demo` 比写 10 页文档更有用。
   - 需要在 pipeline 入口明确关闭会失败的外部调用。

3. **可用性改造要落在一键命令上**
   - `docker compose up --build`
   - `gold-miner --demo scan`
   - `gold-miner web`
   - 每个入口都要能在 5 分钟内跑通。

4. **CI 要从新增代码开始保护**
   - 已有测试套件存在历史失败时，至少为新增代码建立独立的测试目录（`tests/test_cli/`）。
   - 新增代码必须 ruff clean，已有 mypy 问题可以逐步收敛。

5. **README 不是功能清单，是上手路径**
   - 旧的 README 是模块说明书，新的 README 是 "5 分钟上手" 指南。
   - 重写 README 时宁可删减细节，也要突出 Docker/Demo/Web/CI 四大入口。

## 如何应用

- 遇到超过 800 行的文件，优先拆分。
- 新功能默认提供 Demo/沙盒路径。
- 环境变量按 required/optional/advanced 分层。
- 改造完成后立即补充测试 + CI，防止回退。
- README 重写时以新用户视角走一遍 onboarding。

## 相关文件

- src/gold_miner/cli/
- src/gold_miner/config.py
- .env.demo / .env.example
- Dockerfile / docker-compose.yml
- src/gold_miner/web/
- .github/workflows/ci.yml
- tests/test_cli/
- README.md
