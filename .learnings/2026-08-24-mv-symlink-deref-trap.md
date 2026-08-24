# 2026-08-24 归档 skill 时 mv 解引用符号链接搬空外部目录

## 事故

上下文瘦身第三轮：`cd ~/.claude/skills && for d in */; do mv "$d" backup/` 批量归档技能。循环变量 `$d` 带 trailing slash（`a-evolve/`），**BSD mv 遇到带尾斜杠的符号链接源会解引用**——把 symlink 指向的外部实体目录（`~/.orchestra/skills/`、`~/.claude/semantica/plugins/skills/`、gstack 目录、ego 应用包 `Resources/ego-skills/ego-browser`）整体搬走，留下悬空 symlink。89 个符号链接受影响，其中 ego 应用包内目录被搬出（应用 09:32 重建 symlink 但目标已空）。

## 根因

`mv symlink/`（带尾斜杠）≠ `mv symlink`（不带）。尾斜杠强制把源当目录解析，跟随符号链接到真实目录并移动真实目录本体。`2>/dev/null` 又吞掉了部分失败信息，第一轮未发现。

## 修复

1. 遍历 skills/ 找出 `[ -L ]` 符号链接且目标不存在（被搬空）的，把 backup 里同名实体 mv 回 readlink 目标（75 个恢复成功）
2. 相对目标 symlink（gstack 系 13 个）实体并回 round2/gstack 单一单元
3. ego-browser mv 回 `/Applications/ego lite.app/.../Resources/ego-skills/ego-browser`
4. 符号链接本身单独归档到 `round2-2026-08-24/_symlinks/`

## 规则（后续批量移动目录时必须执行）

- 批量 `mv` 前先 `[ -L "$x" ] && 单独处理`：符号链接只移动链接本身（不带尾斜杠），绝不跟随
- 循环变量去掉尾斜杠：`for d in *; do [ -d "$d" ] || continue; ...` 替代 `for d in */`
- 批量移动**禁开 `2>/dev/null`**，失败必须可见
- 涉及 `/Applications` 应用包内路径的移动操作前先确认来源（应用会重建链接，但包内目录被搬会破坏签名/功能）
