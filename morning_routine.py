#!/usr/bin/env python3
"""
每日晨间总控脚本 — 08:55 自动执行
1. 跑 stock_tracker.py (板块热度)
2. 跑 stock_daily_recommend.py (推荐)
3. 跑 arxiv_daily.py (AI论文分析)
4. 输出组合报告到stdout，供cronjob发飞书
"""
import os, sys, subprocess, json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime.now()
DATE_STR = NOW.strftime("%Y-%m-%d")

def run_script(name, script_path, timeout=300):
    print(f"\n{'='*60}")
    print(f"[{name}] 开始执行... ({datetime.now().strftime('%H:%M:%S')})")
    print(f"{'='*60}")
    sys.stdout.flush()
    try:
        result = subprocess.run(
            ["python3", script_path],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.stdout:
            print(result.stdout[-2000:])
        if result.stderr:
            print(f"[stderr] {result.stderr[-1000:]}")
        print(f"[{name}] 退出码: {result.returncode}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[{name}] 超时!")
        return False
    except Exception as e:
        print(f"[{name}] 错误: {e}")
        return False

def read_report(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def main():
    print(f"🔥 每日晨间总控 — {DATE_STR}")
    print(f"工作目录: {BASE}")
    print()

    # Step 1: 板块热度
    run_script("板块热度", os.path.join(BASE, "stock_tracker.py"))

    # Step 2: 每日推荐
    run_script("每日推荐", os.path.join(BASE, "stock_daily_recommend.py"))

    # Step 3: AI论文分析
    run_script("AI论文分析", os.path.join(BASE, "arxiv_daily.py"))

    # 汇总报告
    print(f"\n{'='*60}")
    print("✅ 晨间任务全部完成!")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
