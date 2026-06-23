---
date: 2026-06-22
type: workflow
---

# 本项目测试/检查命令

问题：直接运行 `pytest tests/...` 会报 `ModuleNotFoundError: No module named 'gold_miner'`，且系统 Python 为 3.9 不满足项目 `>=3.11` 要求。

解决：
1. 确保依赖已同步：`uv sync --extra dev`
2. 测试必须带 `PYTHONPATH=src`：`PYTHONPATH=src uv run pytest tests/...`
3. Lint 同样：`PYTHONPATH=src uv run ruff check --fix src/... tests/...`
4. uv 会使用 `.venv/bin/python3`（当前 3.13.11），符合项目要求。

注意：
- `.venv/bin/pytest` 默认不存在，需通过 `uv run pytest` 调用。
- 完整测试套件中存在一个 Polymarket 集成测试偶尔因网络/代理 flaky 失败，单独重跑可通过。
- loguru 在 pytest 结束时可能报 "I/O operation on closed file"，这是测试 stdout 关闭导致的良性错误，不影响测试结果。
