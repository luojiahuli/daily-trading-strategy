#!/usr/bin/env python3
"""
股票题材"窗口期"分析系统

核心逻辑: 找出还处于"上涨窗口期"的板块和个股
- 板块还没过热、趋势向上、有资金介入
- 个股刚开始启动、还有上涨空间

窗口期定义:
  板块:
    - 热度 50-80 (有一定的关注度但没到最高)
    - 近5日跌幅不超过5% (没有大跌)
    - 近20日涨幅 > 3% (中期趋势向上)
    - 量比 > 1.0 (成交量放大)
    - 距60日高点 < -5% (还有上涨空间)
    - 趋势斜率 > 0 (趋势向上)
  
  个股:
    - 当日涨幅 2%~9% (开始活跃但没涨停过火)
    - 处于窗口期板块内
"""

import os, sys, time, json, argparse
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_history, get_board_constituents,
    get_stock_quote, calc_board_analysis, safe_float,
    CACHE_DIR, BOARD_LIST_CACHE
)


def analyze_window_boards(
    boards: list,
    min_score: float = 45,
    max_score: float = 80,
    max_5d_pct: float = -3.0,      # 近5日不能跌超X%
    min_20d_pct: float = 3.0,      # 近20日至少涨X%
    min_vol_ratio: float = 1.0,    # 量比至少X
    max_gap_from_high: float = -3.0,  # 距高点至少还有X%空间
    min_trend: float = 0.1,        # 趋势斜率大于X
    top_n: int = 50
) -> list:
    """
    分析哪些板块处于"窗口期"

    返回: [{"name": ..., "analysis": {...}, "window_score": ...}, ...]
    """
    if top_n > 0:
        boards = boards[:top_n]

    results = []
    total = len(boards)

    for idx, board in enumerate(boards):
        name = board["name"]
        if (idx + 1) % 5 == 0 or idx == 0:
            print(f"  [{idx+1}/{total}] 分析中... ({len(results)} 窗口板块)", end="\r", flush=True)

        history = get_board_history(name)
        if not history or len(history) < 20:
            time.sleep(0.2)
            continue

        analysis = calc_board_analysis(history)
        ind = analysis["indicators"]
        score = analysis["score"]

        pct_5d = ind.get("pct_5d", 0)
        pct_20d = ind.get("pct_20d", 0)
        vol_ratio = ind.get("量比", 1)
        gap_from_high = ind.get("近60日高点差%", 0)
        trend = ind.get("趋势斜率", 0)
        near_high = ind.get("近高点", False)

        # 窗口期条件检查
        conditions = {
            "热度适中": min_score <= score <= max_score,
            "无大跌": pct_5d >= max_5d_pct,
            "趋势向上": pct_20d >= min_20d_pct and trend > min_trend,
            "量能配合": vol_ratio >= min_vol_ratio,
            "空间充足": gap_from_high <= max_gap_from_high,
        }

        passed = sum(1 for v in conditions.values() if v)
        total_conds = len(conditions)
        window_score = round(passed / total_conds * 100, 1)

        if passed >= 3:  # 至少满足3个条件
            results.append({
                "name": name,
                "code": board["code"],
                "analysis": analysis,
                "window_score": window_score,
                "conditions": conditions,
                "conditions_passed": passed,
                "conditions_total": total_conds,
            })

        time.sleep(0.2)

    print(f"\n[+] 完成! 共找到 {len(results)} 个窗口期板块")
    results.sort(key=lambda x: (x["window_score"], x["analysis"]["score"]), reverse=True)
    return results


def find_window_stocks_from_board(board_name: str, max_stocks: int = 10) -> list:
    """
    从板块中找出处于"窗口期"的个股

    窗口期个股条件:
    - 涨幅 2%~9% (开始活跃但没涨停)
    - 排除ST/退市股
    - 有成交量
    """
    cons = get_board_constituents(board_name)
    if not cons:
        return []

    candidates = []
    for s in cons:
        code = s.get("代码", "")
        name = s.get("名称", "")
        inc_pct = safe_float(s.get("涨幅", 0))
        price = safe_float(s.get("现价", 0))

        # 排除ST股、退市股
        if name.startswith(("ST", "*ST", "退")):
            continue

        # 窗口期个股: 涨幅2%~9%
        if 2.0 <= inc_pct <= 9.0:
            candidates.append({
                "代码": code,
                "名称": name,
                "涨幅": inc_pct,
                "现价": price,
                "涨跌": s.get("涨跌", "0"),
                "换手": s.get("换手", "0"),
                "量比": s.get("量比", "0"),
            })

    candidates.sort(key=lambda x: x["涨幅"], reverse=True)
    return candidates[:max_stocks]


def print_window_report(boards: list, recommend_stocks: bool = True):
    """打印窗口期板块报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"""
╔══════════════════════════════════════════════════════════╗
║        题材板块"窗口期"分析报告                        ║
║        找还没涨完、刚开始启动的板块和个股               ║
╚══════════════════════════════════════════════════════════╝
  生成时间: {now}
  分析逻辑: 热度适中 + 趋势向上 + 量能配合 + 空间充足
""")

    print(f"{'=' * 120}")
    print(f"  {'排名':>3} {'板块名称':<22} {'窗口分':>6} {'热度':>6} {'5日涨跌':>10} {'20日涨跌':>10} {'量比':>7} {'趋势':>7} {'距高点':>8} {'条件达标'}")
    print(f"{'=' * 120}")

    for i, b in enumerate(boards):
        ind = b["analysis"]["indicators"]
        cond_str = f"{b['conditions_passed']}/{b['conditions_total']}"
        cond_detail = ""
        for cname, cval in b["conditions"].items():
            cond_detail += "Y" if cval else "N"
        print(
            f"  {i+1:>3} {b['name']:<22} "
            f"{b['window_score']:>5.1f} "
            f"{b['analysis']['score']:>5.1f} "
            f"{ind.get('pct_5d', 0):>+9.2f}% "
            f"{ind.get('pct_20d', 0):>+9.2f}% "
            f"{ind.get('量比', 1):>6.2f} "
            f"{ind.get('趋势斜率', 0):>+6.2f} "
            f"{ind.get('近60日高点差%', 0):>+7.2f}% "
            f"{cond_str}({cond_detail})"
        )

    print(f"{'=' * 120}")
    print(f"  条件说明: 热度适中|无大跌|趋势向上|量能配合|空间充足")
    print(f"  窗口分 = 达标条件占比, 越高说明该板块越符合'窗口期'特征")
    print()

    # 推荐个股
    if recommend_stocks and boards:
        print(f"{'=' * 120}")
        print(f"  窗口期板块 -> 推荐个股 (涨幅2%~9%, 刚开始活跃)")
        print(f"{'=' * 120}")

        for i, b in enumerate(boards[:10]):  # 只看前10个板块
            print(f"\n  [{i+1}] {b['name']} (窗口分: {b['window_score']})")
            stocks = find_window_stocks_from_board(b["name"], max_stocks=8)
            if stocks:
                print(f"  {'代码':<10} {'名称':<12} {'涨幅%':>7} {'现价':>9} {'换手%':>7} {'量比':>7}")
                print(f"  {'-' * 52}")
                for s in stocks:
                    print(f"  {s['代码']:<10} {s['名称']:<12} {s['涨幅']:>+6.2f}% {s['现价']:>8.2f} {safe_float(s.get('换手',0)):>6.2f}% {safe_float(s.get('量比',0)):>6.2f}")
            else:
                print(f"  [i] 该板块今日无符合条件的窗口期个股")
            time.sleep(0.3)  # 避免请求过快


def main():
    parser = argparse.ArgumentParser(
        description="找题材板块的'窗口期'–还没涨完但趋势向上的机会",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 stock_window_analysis.py                    # 默认窗口期分析
  python3 stock_window_analysis.py --quick             # 快速模式(前50板块)
  python3 stock_window_analysis.py --top 100           # 分析前100个板块
  python3 stock_window_analysis.py --strict            # 严格模式(更苛刻条件)
  python3 stock_window_analysis.py --loose             # 宽松模式(更多候选)
  python3 stock_window_analysis.py --board "存储芯片"   # 只看某个板块的窗口股
        """
    )
    parser.add_argument("--quick", action="store_true",
                        help="快速模式: 只分析前50个板块")
    parser.add_argument("--top", type=int, default=100,
                        help="分析前N个板块 (默认: 100)")
    parser.add_argument("--board", type=str, default=None,
                        help="只看指定板块的窗口期个股")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式: 更苛刻的窗口条件")
    parser.add_argument("--loose", action="store_true",
                        help="宽松模式: 更多候选")
    parser.add_argument("--no-stock", action="store_true",
                        help="不推荐个股，只看板块")
    parser.add_argument("--no-cache", action="store_true",
                        help="不缓存板块列表")

    args = parser.parse_args()

    print("""
  ╔══════════════════════════════════════════╗
  ║    题材板块"窗口期"分析 v1.0              ║
  ║    找还没涨完、刚开始启动的机会           ║
  ╚══════════════════════════════════════════╝
    """)

    boards = get_all_concept_boards(use_cache=not args.no_cache)
    if not boards:
        print("[!] 无法获取板块列表")
        return

    # 如果只看单个板块的窗口期个股
    if args.board:
        target = None
        for b in boards:
            if args.board.lower() in b["name"].lower():
                target = b
                break
        if not target:
            print(f"[!] 未找到板块: {args.board}")
            return

        print(f"[*] 查找 [{target['name']}] 板块的窗口期个股...")
        stocks = find_window_stocks_from_board(target["name"], max_stocks=20)
        if stocks:
            print(f"\n  {'代码':<10} {'名称':<12} {'涨幅%':>7} {'现价':>9} {'换手%':>7} {'量比':>7}")
            print(f"  {'-' * 52}")
            for s in stocks:
                print(f"  {s['代码']:<10} {s['名称']:<12} {s['涨幅']:>+6.2f}% {s['现价']:>8.2f} {safe_float(s.get('换手',0)):>6.2f}% {safe_float(s.get('量比',0)):>6.2f}")
        else:
            print(f"[i] 该板块今日无符合条件的窗口期个股")
        return

    # 设置窗口期参数
    if args.strict:
        params = dict(min_score=50, max_score=75, max_5d_pct=-2.0,
                      min_20d_pct=5.0, min_vol_ratio=1.2,
                      max_gap_from_high=-5.0, min_trend=0.2)
        print("[*] 严格模式: 条件更苛刻，筛选更精准\n")
    elif args.loose:
        params = dict(min_score=35, max_score=85, max_5d_pct=-8.0,
                      min_20d_pct=0.0, min_vol_ratio=0.8,
                      max_gap_from_high=0.0, min_trend=0.0)
        print("[*] 宽松模式: 放宽条件，更多候选\n")
    else:
        params = dict(min_score=45, max_score=78, max_5d_pct=-3.0,
                      min_20d_pct=3.0, min_vol_ratio=1.0,
                      max_gap_from_high=-3.0, min_trend=0.1)
        print("[*] 标准模式\n")

    # 确定分析数量
    if args.quick:
        analyze_count = 50
        print(f"[*] 快速模式: 分析前50个板块\n")
    else:
        analyze_count = min(args.top, len(boards))
        print(f"[*] 全量模式: 分析前{analyze_count}个板块\n")

    # 窗口期分析
    window_boards = analyze_window_boards(
        boards, top_n=analyze_count, **params
    )

    if not window_boards:
        print("[!] 当前没有符合条件的窗口期板块")
        return

    # 输出报告
    print_window_report(window_boards, recommend_stocks=not args.no_stock)

    print(f"""
  ─── 使用建议 ───
  - 以上板块符合"窗口期"特征：趋势向上 + 量能配合 + 还有空间
  - 个股条件：涨幅2%~9%（刚开始活跃，还没涨停封板）
  - 建议根据自己的风险偏好进一步筛选
  - 使用 --board "板块名" 查看指定板块详情
  - 使用 --strict 更严格筛选，--loose 更多候选
    """)


if __name__ == "__main__":
    main()
