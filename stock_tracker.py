#!/usr/bin/env python3
"""
跟踪脚本：定期运行，扫描热门轮动板块，生成推荐报告+看板
每次运行生成带时间戳的快照，便于对比历史
"""

import os, sys, json, time, re as re2, shutil
from datetime import datetime

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests as req

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float, get_board_history, calc_board_analysis
from stock_viz_dashboard import get_kline_data as get_kline

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "tracking_history")
VIZ_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NOW = datetime.now()
TS = NOW.strftime("%Y%m%d_%H%M")
DATE_STR = NOW.strftime("%Y-%m-%d")
WEEKDAY = NOW.weekday()

# ============================================================
# 配置
# ============================================================
WATCH_BOARDS = 60       # 监控前N个板块
MAX_CANDIDATES = 12     # 候选股数量
STRAT = {
    "name": "A:基础窗口期",
    "check": lambda kl, i: kl[i]["open"] if i>0 and 2.0 <= kl[i-1]["pct"] <= 9.0 else None,
    "sl": -3.0, "tp": 5.0, "mh": 5,
}

# ============================================================
# 核心：板块热度排名（用于判断轮动）
# ============================================================
def get_board_heat_ranking(top_n=30):
    """获取板块热度排名，用于判断热点轮动"""
    boards = get_all_concept_boards(use_cache=True)
    results = []
    for board in boards[:WATCH_BOARDS]:
        history = get_board_history(board["name"])
        if history and len(history) >= 10:
            analysis = calc_board_analysis(history)
            results.append({"name": board["name"], "code": board["code"], "analysis": analysis})
        time.sleep(0.15)
    results.sort(key=lambda x: x["analysis"]["score"], reverse=True)
    return results[:top_n]


# ============================================================
# 核心：回测推荐
# ============================================================
def get_recommendations():
    """获取推荐个股+回测数据"""
    print("[*] 扫描板块候选股...")
    boards = get_all_concept_boards(use_cache=True)
    seen = {}
    for board in boards[:WATCH_BOARDS]:
        cons = get_board_constituents(board["name"])
        if not cons:
            time.sleep(0.08)
            continue
        for s in cons[:8]:
            code = s.get("代码",""); sname = s.get("名称","")
            inc = safe_float(s.get("涨幅",0))
            if sname.startswith(("ST","*ST","退")): continue
            if 2.0 <= inc <= 15.0:
                if code not in seen or inc > seen[code]["inc"]:
                    seen[code] = {"code":code,"name":sname,"board":board["name"],
                                  "inc":inc,"price":safe_float(s.get("现价",0))}
        time.sleep(0.08)
    
    candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)[:MAX_CANDIDATES]
    print(f"[+] {len(candidates)} 只候选股")
    
    stock_data = []
    for c in candidates:
        print(f"  {c['code']} {c['name']} inc={c['inc']:+.1f}%...", end=" ", flush=True)
        kl = get_kline(c["code"])
        if not kl or len(kl) < 30:
            print("数据不足")
            continue
        trades = []
        positions = []
        tdates = set()
        for i in range(1, len(kl)):
            today = kl[i]
            positions = [p for p in positions if i - p["bi"] < STRAT["mh"]]
            for p in positions[:]:
                hd = i - p["bi"]
                hp = (today["high"] - p["bp"]) / p["bp"] * 100
                lp = (today["low"] - p["bp"]) / p["bp"] * 100
                if hd >= STRAT["mh"]:
                    pct = round((today["close"] - p["bp"]) / p["bp"] * 100, 2)
                    trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],
                                   "sp":round(today["close"],2),"pct":pct,"r":"到期卖出","hd":hd})
                    positions.remove(p)
                elif hp >= STRAT["tp"]:
                    sp = round(p["bp"] * (1 + STRAT["tp"]/100), 2)
                    trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],
                                   "sp":sp,"pct":STRAT["tp"],"r":"止盈+5%","hd":hd})
                    positions.remove(p)
                elif lp <= STRAT["sl"]:
                    sp = round(p["bp"] * (1 + STRAT["sl"]/100), 2)
                    trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],
                                   "sp":sp,"pct":STRAT["sl"],"r":"止损-3%","hd":hd})
                    positions.remove(p)
            prev = kl[i-1]
            bp = STRAT["check"](kl, i)
            if bp and len(positions) < 3 and today["date"] not in tdates:
                tdates.add(today["date"])
                positions.append({"bd":today["date"],"bp":bp,"bi":i})
        
        if trades:
            total_pnl = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            stock_data.append({**c, "trades":trades, "total_trades":len(trades),
                "total_pnl":round(total_pnl,2), "wins":wins,
                "win_rate":round(wins/len(trades)*100,1),
                "avg_pnl":round(total_pnl/len(trades),2)})
            print(f"{len(trades)}笔 总{total_pnl:+.2f}%")
        else:
            print("无信号")
        time.sleep(0.3)
    
    stock_data.sort(key=lambda x: x["total_pnl"], reverse=True)
    return stock_data


# ============================================================
# 输出：终端报告
# ============================================================
def print_report(heat_ranking, recommendations):
    print(f"""
  {'='*70}
   股票题材跟踪报告 - {DATE_STR}
  {'='*70}
    """)
    
    # 热门板块 TOP10
    print(f"  [热点板块 TOP 10]")
    print(f"  {'排名':>3} {'板块':<20} {'热度':>6} {'5日涨跌':>10} {'20日涨跌':>10}")
    print(f"  {'-'*50}")
    for i, b in enumerate(heat_ranking[:10]):
        ind = b["analysis"]["indicators"]
        print(f"  {i+1:>3} {b['name']:<20} {b['analysis']['score']:>5.1f} "
              f"{ind.get('pct_5d',0):>+8.2f}% {ind.get('pct_20d',0):>+8.2f}%")
    
    # 推荐个股 TOP5
    print(f"\n  [推荐个股 TOP {min(5,len(recommendations))}]")
    if recommendations:
        print(f"  {'代码':<10} {'名称':<10} {'板块':<14} {'今日涨幅':>8} {'回测收益':>8} {'胜率':>6}")
        print(f"  {'-'*58}")
        for r in recommendations[:5]:
            print(f"  {r['code']:<10} {r['name']:<10} {r['board']:<14} "
                  f"{r['inc']:>+6.1f}% {r['total_pnl']:>+7.2f}% {r['win_rate']:>5.1f}%")
    else:
        print(f"  今日无推荐")
    
    print(f"\n  {'='*70}")


# ============================================================
# 主流程
# ============================================================
def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    股票题材跟踪系统                      ║
  ║    板块轮动监测 + 每日推荐              ║
  ╚══════════════════════════════════════════╝
    """)
    
    if WEEKDAY >= 5:
        print(f"[i] {DATE_STR} 是非交易日，跳过回测，仅更新板块热度")
    
    # 1. 板块热度排名
    print("[*] 扫描板块热度...")
    heat = get_board_heat_ranking(top_n=30)
    print(f"[+] 完成")
    
    # 2. 个股推荐 (非交易日跳过)
    recommendations = []
    if WEEKDAY < 5:
        recommendations = get_recommendations()
    
    # 3. 终端输出
    print_report(heat, recommendations)
    
    # 4. 保存快照
    snapshot = {
        "timestamp": TS,
        "date": DATE_STR,
        "is_trading_day": WEEKDAY < 5,
        "board_ranking": [{
            "name": b["name"],
            "score": b["analysis"]["score"],
            "pct_5d": b["analysis"]["indicators"].get("pct_5d",0),
            "pct_20d": b["analysis"]["indicators"].get("pct_20d",0),
        } for b in heat],
        "recommendations": [{
            "code": r["code"], "name": r["name"], "board": r["board"],
            "inc": r["inc"], "total_pnl": r["total_pnl"],
            "win_rate": r["win_rate"], "total_trades": r["total_trades"],
        } for r in recommendations],
        "recommendation_count": len(recommendations),
    }
    
    snap_path = os.path.join(OUTPUT_DIR, f"snapshot_{TS}.json")
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    print(f"\n[+] 快照已保存: {snap_path}")
    
    # 5. 更新最新快照（方便后续读取）
    latest_path = os.path.join(OUTPUT_DIR, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)
    
    # 6. 输出推荐摘要（供cron/launchd通知用）
    if recommendations:
        print(f"\n  >>> 今日推荐: {recommendations[0]['name']}({recommendations[0]['code']}) "
              f"回测收益{recommendations[0]['total_pnl']:+.1f}% "
              f"板块:{recommendations[0]['board']}")
    
    print(f"\n[*] 完成！")


if __name__ == "__main__":
    main()
