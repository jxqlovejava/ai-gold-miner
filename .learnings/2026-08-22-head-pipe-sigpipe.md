# 2026-08-22 计时命令 `| head` 截断运行中的 scan 进程

## 现象

`time bash scripts/quick_scan.sh | head -2` 计时采样 → head 收到 2 行后关闭管道 → SIGPIPE → scan 在 `tee.flush()` 抛 BrokenPipeError 中途崩溃。

## 根因

给**会写文件的运行中进程**用 `| head` 截输出 = 主动制造 SIGPIPE。采样只读命令（git log 等）无此问题。

## 未造成损坏的原因

scan.py `_run_with_report` 已做原子写入（先写 .tmp，成功后 os.replace，异常则不落正式文件）——半截内容只留在 .tmp，正式报告未被污染。干净重跑后确认 418 行完整。

## 规则

**给会写文件的命令计时/采样输出时，重定向到临时文件再读：`time cmd > /tmp/x.out 2>&1; head /tmp/x.out`。禁止 `cmd | head` 截断运行中的写进程。**
