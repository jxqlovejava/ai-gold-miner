---
date: 2026-06-29
category: git
trigger: editing data/personal_rules.md
---

# 被 `.gitignore` 忽略但仍 tracked 的文件修改不会出现在 `git status`

## 现象

- `data/personal_rules.md` 在 `.gitignore` 第 38 行被显式忽略。
- 但 `git ls-files` 仍显示该文件被跟踪（因为历史 index 中仍有条目）。
- 编辑该文件后，`git status --short` 显示 clean，`git diff` 无输出。
- `git add data/personal_rules.md` 会报错：`ignored by one of your .gitignore files`。

## 影响

- 常规的 `gcp` / `git commit -a` 不会提交该文件变更。
- 容易误以为修改已经保存到版本控制，实际只在 working tree。
- 若用户在多设备工作，个人规则变更不会随仓库同步。

## 诊断命令

```bash
# 查看文件是否被忽略及规则来源
git check-ignore -v data/personal_rules.md

# 查看被忽略文件的变更状态
git status --short --ignored=matching data/personal_rules.md
```

## 处理选项

1. **留在本地**（推荐）：个人规则/持仓数据属于私密信息，不应进入远程仓库。确认 `.gitignore` 已覆盖即可。
2. **强制提交**：`git add -f data/personal_rules.md` 后提交。这会覆盖 `.gitignore` 规则，把个人文件纳入版本控制——仅当明确要求且了解后果时使用。

## 何时警惕

编辑以下文件前应先检查 `git check-ignore -v`：

- `data/personal_rules.md`
- `data/private/*`
- 任何从模板复制到本地的 `.example.*` 去掉了 `.example` 的文件

## 教训

不要只看 `git status` clean 就断言没有未提交变更。被忽略文件仍可能包含重要修改，需要显式检查。