# Git 历史清理指南

本项目包含个人敏感数据（持仓信息、交易记录、投资者画像等）。在开源前，必须清理 Git 历史中的这些文件。

## 推荐工具

### 1. git-filter-repo（推荐）

安装：
```bash
pip install git-filter-repo
```

使用：
```bash
# 移除特定文件的历史记录
git filter-repo --path data/portfolio.yaml --invert-paths
git filter-repo --path data/trade_log.md --invert-paths
git filter-repo --path data/prediction_journal.jsonl --invert-paths
git filter-repo --path data/event_store.jsonl --invert-paths
git filter-repo --path data/personal_rules.md --invert-paths
git filter-repo --path investor_profile.md --invert-paths
git filter-repo --path data/jd_ms_gold_history.csv --invert-paths
git filter-repo --path data/doctrine_state.json --invert-paths
git filter-repo --path data/scenarios.jsonl --invert-paths
git filter-repo --path data/reports/ --invert-paths

# 或者一次性移除
git filter-repo \
  --path data/portfolio.yaml \
  --path data/trade_log.md \
  --path data/prediction_journal.jsonl \
  --path data/event_store.jsonl \
  --path data/personal_rules.md \
  --path investor_profile.md \
  --path data/jd_ms_gold_history.csv \
  --path data/doctrine_state.json \
  --path data/scenarios.jsonl \
  --path data/reports/ \
  --invert-paths
```

### 2. BFG Repo-Cleaner

```bash
# 下载 BFG
wget https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar

# 创建需要删除的文件列表
cat > delete-files.txt << 'EOF'
data/portfolio.yaml
data/trade_log.md
data/prediction_journal.jsonl
data/event_store.jsonl
data/personal_rules.md
investor_profile.md
data/jd_ms_gold_history.csv
data/doctrine_state.json
data/scenarios.jsonl
EOF

# 运行清理
java -jar bfg-1.14.0.jar --delete-files delete-files.txt my-repo.git
```

## 清理后检查

```bash
# 确认文件不再存在于历史中
git log --all --full-history -- data/portfolio.yaml
# 应该无输出

# 检查仓库大小
git count-objects -vH

# 运行秘密扫描
python scripts/secret-scan.py
```

## 注意事项

1. **备份**：清理前备份完整仓库（包括所有分支）
2. **强制推送**：清理后需要 force push 到远程
   ```bash
   git push --force --all
   git push --force --tags
   ```
3. **协作影响**：所有协作者需要重新克隆仓库
4. **已克隆的副本**：任何已克隆的本地副本仍包含完整历史，需重新克隆

## 替代方案：新建仓库

如果历史较复杂，最简单的方法是：
1. 导出当前代码（不含 .git）
2. 新建仓库
3. 只提交清理后的代码
4. 将旧仓库归档为私有
