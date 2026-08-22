#!/usr/bin/env python3
"""同步黄金 crontab 条目到实际 crontab (追加方式, 保留其他项目任务)。

背景/根因: scripts/hermes_crontab.txt 是黄金定时任务的本地源文件(真相源)。
部署脚本 deploy_gold_miner_to_hermes.sh 此前仅将该文件 scp 到服务器 scripts/ 目录,
从不安装到实际 crontab, 导致本地新增条目 (如 11:35 watchdog) 后服务器 crontab 漂移
(事故: 2026-08-22 服务器缺 35 11 * * 1-5 watchdog 条目, 本地有服务器无)。

此脚本在服务器端运行:
  1. 从 <REMOTE_ROOT>/scripts/hermes_crontab.txt 提取黄金条目
     (特征: 含 'cd /home/ubuntu/ai-gold-miner', 排除注释行/空行)
  2. 与 `crontab -l` 逐行对比, 缺失则追加
  3. 只增不删 — 移除条目须手动, 避免误删白泽等其他项目任务
     (服务器 crontab 是黄金 + 白泽等混合, 全量覆盖会破坏其他任务)

用法:
  python3 scripts/sync_gold_crontab.py [--dry-run]
"""
import argparse
import subprocess
import sys

# 部署脚本以 scp 覆盖此处文件后运行本脚本; 路径与部署脚本 REMOTE_ROOT 保持一致
CRON_FILE = "/home/ubuntu/ai-gold-miner/scripts/hermes_crontab.txt"
MARKER = "cd /home/ubuntu/ai-gold-miner"


def get_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def extract_gold_lines() -> list:
    gold = []
    try:
        with open(CRON_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.strip().startswith("#"):
                    continue
                if MARKER in line:
                    gold.append(line)
    except FileNotFoundError:
        print(f"❌ 未找到 {CRON_FILE} (先运行部署脚本同步该文件)")
        sys.exit(1)
    return gold


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅对比, 不写入 crontab")
    args = parser.parse_args()

    current = get_crontab()
    current_lines = set(current.splitlines())
    missing = [ln for ln in extract_gold_lines() if ln not in current_lines]

    if not missing:
        print(f"✅ 黄金 crontab 条目已全部同步 (0 条新增, 共 {len(current_lines)} 行)")
        return 0

    if args.dry_run:
        print(f"⏸ dry-run: 以下 {len(missing)} 条将同步 (未写入):")
        for m in missing:
            print(f"    + {' '.join(m.split()[0:5])}")
        return 0

    new_crontab = current.rstrip("\n") + "\n" + "\n".join(missing) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print(f"✅ 同步 {len(missing)} 条黄金 crontab 条目:")
    for m in missing:
        print(f"    + {' '.join(m.split()[0:5])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
