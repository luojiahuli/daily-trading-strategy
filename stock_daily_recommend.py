#!/usr/bin/env python3
"""
每日开盘前自动回测+推荐

1. 获取当天窗口期候选股
2. 回测验证（用近一个月数据验证策略表现）
3. 输出推荐报告到文件 + 通知
"""

import os, sys, time, json, re, math
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "daily_reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_POSITIONS = 3
STOP_LOSS = -3.0
TAKE_PROFIT = 5.0
MAX_HOLD = 5


def get_kline(code, days=50):
    """新浪API获取K线"""
    try:
        code = str(code).strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        match = re.search(r'\[.*\]', resp.text)
        if not match:
            return None
        data = json.loads(match.group())
        records = []
        for d in data:
            records.append({
                "date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                "high": float(d["high"]), "low": float(d["low"]),
            })
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else:
                records[-1]["pct"] = 0.0
        return records
    except:
        return None


def run_backtest_on_pick(code, name, board):
    """对单只个股运行策略A回测"""
    kline = get_kline(code)
    if not kline or len(kline) < 30:
        return None

    trades = []
    positions = []
    trade_dates = set()

    for i in range(1, len(kline)):
        today = kline[i]
        positions = [p for p in positions if i - p["buy_idx"] < MAX_HOLD]

        for p in positions[:]:
            hold = i - p["buy_idx"]
            if hold >= MAX_HOLD:
                pct = round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": round(today["close"],2),
                    "pct": pct, "reason": "到期"})
                positions.remove(p)
                continue

            hp = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
            lp = (today["low"] - p["buy_price"]) / p["buy_price"] * 100
            if hp >= TAKE_PROFIT:
                sp = round(p["buy_price"] * (1 + TAKE_PROFIT/100), 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": sp,
                    "pct": TAKE_PROFIT, "reason": f"止盈+{TAKE_PROFIT:.0f}%"})
                positions.remove(p)
            elif lp <= STOP_LOSS:
                sp = round(p["buy_price"] * (1 + STOP_LOSS/100), 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": sp,
                    "pct": STOP_LOSS, "reason": f"止损{STOP_LOSS:.0f}%"})
                positions.remove(p)

        if len(positions) < MAX_POSITIONS:
            prev = kline[i-1]
            if 2.0 <= prev["pct"] <= 9.0 and today["date"] not in trade_dates:
                trade_dates.add(today["date"])
                positions.append({"buy_date": today["date"], "buy_price": kline[i]["open"], "buy_idx": i})

    return trades if trades else None


def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    每日开盘推荐系统                      ║
  ║    回测验证 → 个股推荐                  ║
  ╚══════════════════════════════════════════╝
    """)

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.weekday()

    if weekday >= 5:
        print("[i] 今天是非交易日（周末），跳过")
        return

    # 1. 获取板块候选股
    print(f"[*] {today} 扫描板块候选股...")
    boards = get_all_concept_boards(use_cache=True)
    seen = {}

    for board in boards[:60]:
        name = board["name"]
        cons = get_board_constituents(name)
        if not cons:
            time.sleep(0.08)
            continue
        for s in cons[:8]:
            code = s.get("代码", "")
            sname = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))
            if sname.startswith(("ST","*ST","退")): 
                continue
            if 0 <= inc <= 15:
                if code not in seen or inc > seen[code]["inc"]:
                    seen[code] = {"code": code, "name": sname, "board": name, "inc": inc}
        time.sleep(0.08)

    candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)
    print(f"[+] 获取到 {len(candidates)} 只候选股")

    # 2. 逐只回测
    print(f"\n[*] 回测验证候选股（策略A: 止损-3%/止盈+5%）...\n")
    all_results = []

    for idx, c in enumerate(candidates[:15]):
        print(f"  [{idx+1}/15] {c['code']} {c['name']} ({c['board']}) inc={c['inc']:+.1f}%...", end=" ", flush=True)
        trades = run_backtest_on_pick(c["code"], c["name"], c["board"])
        if trades:
            total_pnl = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            all_results.append({
                **c,
                "trades": trades,
                "total_trades": len(trades),
                "total_pnl": round(total_pnl, 2),
                "wins": wins,
                "win_rate": round(wins/len(trades)*100, 1),
                "avg_pnl": round(total_pnl/len(trades), 2) if trades else 0,
            })
            print(f"{len(trades)}笔 总{total_pnl:+.2f}% 胜{wins}/{len(trades)}")
        else:
            print("无信号/数据不足")
        time.sleep(0.3)

    # 3. 排序输出推荐
    all_results.sort(key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n{'='*70}")
    print(f"  📋 今日推荐（按回测收益排序）")
    print(f"{'='*70}")
    print(f"  {'排名':>3} {'代码':<8} {'名称':<10} {'板块':<14} {'今日涨幅':>8} {'回测收益':>8} {'回测胜率':>8}")
    print(f"  {'-'*65}")

    top_recos = all_results[:8]
    for i, r in enumerate(top_recos):
        print(f"  {i+1:>3} {r['code']:<8} {r['name']:<10} {r['board']:<14} "
              f"{r['inc']:>+6.1f}% {r['total_pnl']:>+7.2f}% {r['win_rate']:>6.1f}%")

    # 4. 生成推荐报告
    lines = []
    lines.append(f"")
    lines.append(f"  ╔══════════════════════════════════════════╗")
    lines.append(f"  ║    每日开盘推荐报告                       ║")
    lines.append(f"  ║    {today}                       ║")
    lines.append(f"  ╚══════════════════════════════════════════╝")
    lines.append(f"")
    lines.append(f"  [策略] 前日涨2~9%买入 → 止损-3%/止盈+5%/持仓5日")
    lines.append(f"")

    if top_recos:
        lines.append(f"  🏆 今日推荐个股 Top {len(top_recos)}:")
        lines.append(f"  {'':4}{'代码':<8} {'名称':<10} {'板块':<14} {'今日涨幅':>8} {'回测收益':>8} {'回测胜率':>8}")
        lines.append(f"  {'':4}{'-'*56}")
        for i, r in enumerate(top_recos):
            tag = "⭐" if i == 0 else ""
            lines.append(f"  {tag}{i+1:>2} {r['code']:<8} {r['name']:<10} {r['board']:<14} "
                        f"{r['inc']:>+6.1f}% {r['total_pnl']:>+7.2f}% {r['win_rate']:>6.1f}%")

        lines.append(f"")
        lines.append(f"  [操作建议]")
        lines.append(f"  - 开盘关注以上 {len(top_recos)} 只，优先选择排前列的")
        lines.append(f"  - 买入条件: 开盘后观察，若走势平稳可入场")
        lines.append(f"  - 止损位: 买入价下方 -3%")
        lines.append(f"  - 止盈位: 买入价上方 +5%")
        lines.append(f"  - 最迟持仓: 5个交易日")
        lines.append(f"")
        lines.append(f"  [回测说明]")
        lines.append(f"  - 回测周期: 近一个月")
        lines.append(f"  - 回测收益: 单只个股在回测期间按策略交易的总收益%")
        lines.append(f"  - 回测胜率: 盈利交易占比")
        lines.append(f"  - 实际交易需考虑交易成本(佣金+印花税)")
    else:
        lines.append(f"  [i] 今日无符合条件的推荐")
        lines.append(f"")

    # 输出到终端
    report_text = "\n".join(lines)
    print(report_text)

    # 保存到文件
    report_path = os.path.join(OUTPUT_DIR, f"daily_{today}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\n[+] 报告已保存: {report_path}")

    # 生成离线HTML看板
    print(f"\n[*] 生成离线HTML看板...")
    try:
        html_path = os.path.join(os.path.dirname(__file__), "daily_reports", f"daily_{today.replace('-','')}.html")
        os.makedirs(os.path.dirname(html_path), exist_ok=True)
        
        import json as _json
        recos_json = _json.dumps(top_recos, ensure_ascii=False)
        
        # 尝试加载模拟交易数据，用于大盘对比曲线
        sim_eq = []
        sh_data = []
        eq_dates = []
        try:
            sim_path = os.path.join(os.path.dirname(__file__), "live_trading", "simulation_result.json")
            if os.path.exists(sim_path):
                with open(sim_path) as f:
                    sim = _json.load(f)
                trade_log = sim.get("trade_log", [])
                # 从trade_log提取每日净值
                eq_pts = {}
                for t in trade_log:
                    dt = t.get("date", "")[:10].replace("-", "")
                    if "总资产" in t.get("说明", ""):
                        eq_pts[dt] = t
                if eq_pts:
                    sorted_dt = sorted(eq_pts.keys())
                    base_sh = None
                    for dt in sorted_dt:
                        try:
                            import requests, re
                            url = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData'
                            resp = requests.get(url, params={'symbol': 'sh000001', 'scale': '240', 'ma': 'no', 'datalen': '60'}, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                            m = re.search(r'\[.*\]', resp.text)
                            if m:
                                all_sh = _json.loads(m.group())
                                break
                        except:
                            pass
                    if all_sh:
                        for dt in sorted_dt:
                            eq_val = eq_pts[dt].get("总资产", 100000)
                            d = dt[:4] + "-" + dt[4:6] + "-" + dt[6:8]
                            for s in all_sh:
                                if s["day"] == d:
                                    sim_eq.append(eq_val)
                                    sh_data.append(float(s["close"]))
                                    eq_dates.append(d[5:])
                                    if base_sh is None: base_sh = float(s["close"])
                                    break
        except:
            pass
        eq_json = _json.dumps(sim_eq)
        sh_json = _json.dumps(sh_data)
        dates_json = _json.dumps(eq_dates)
        
        html_content = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>开盘推荐 ''' + today + '''</title>
<script src="../viz_output/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:14px}
h1{color:#ffd700;text-align:center;font-size:18px;margin-bottom:2px}
.sub{color:#888;text-align:center;font-size:11px;margin-bottom:10px}
.card{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:8px;padding:10px;border:1px solid #2a2a5e;margin-bottom:8px}
.card h2{color:#ffd700;font-size:13px;margin-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th{background:#2a2a5e;color:#ffd700;padding:5px;text-align:left}
td{padding:4px 5px;border-bottom:1px solid #1a1a3e}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px}
.stat{background:#1a1a4e;border-radius:6px;padding:8px;text-align:center}
.stat .v{font-size:18px;font-weight:bold}
.stat .l{font-size:10px;color:#888}
.small{font-size:10px;color:#888}
</style>
</head>
<body>
<h1>开盘推荐 ''' + today + '''</h1>
<p class="sub">策略A:基础窗口期 | 止损-3%/止盈+5%/持仓5日</p>
<div class="grid4">
  <div class="stat"><div class="v" style="color:#ffd700">''' + str(len(top_recos)) + '''</div><div class="l">推荐个股</div></div>
  <div class="stat"><div class="v" style="color:#ff4d4f">''' + today + '''</div><div class="l">更新日期</div></div>
  <div class="stat"><div class="v" style="color:#1890ff">-3%/+5%</div><div class="l">止损/止盈</div></div>
  <div class="stat"><div class="v" style="color:#52c41a">5日</div><div class="l">最长持仓</div></div>
</div>
<div class="card">
  <h2>推荐个股 TOP ''' + str(len(top_recos)) + '''</h2>
  <table><tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>今日涨幅</th><th>回测收益</th><th>胜率</th></tr>
  </table>
  <div id="recoList" style="margin-top:6px"></div>
</div>
<div class="card">
  <h2>操作策略</h2>
  <div style="font-size:11px;line-height:1.7;color:#ccc">
    <b style="color:#ffd700">买入:</b> 前一日涨幅2~9%的个股，次日开盘买入<br>
    <b style="color:#ffd700">卖出:</b> 盘中止盈+5% / 止损-3% / 最长持仓5日<br>
    <b style="color:#ffd700">仓位:</b> 等权重，最多3只，单只≤30%<br>
    <b style="color:#ff4d4f">提醒:</b> 严格执行止损，不要扛单
  </div>
</div>
<script>
var recos = ''' + recos_json + ''' || [];
var tb = document.querySelector('table');
recos.forEach(function(r,i){
  var tr = tb.insertRow();
  tr.innerHTML = '<td>'+(i+1)+'</td><td>'+r.code+'</td><td>'+r.name+'</td><td>'+r.board+'</td><td style="color:'+(r.inc>0?'#ff4d4f':'#52c41a')+';font-weight:bold">'+(r.inc>0?'+':'')+r.inc.toFixed(1)+'%</td><td style="color:'+(r.total_pnl>0?'#ff4d4f':'#52c41a')+';font-weight:bold">'+(r.total_pnl>0?'+':'')+r.total_pnl.toFixed(1)+'%</td><td>'+r.win_rate+'%</td>';
});
</script>
</body>
</html>'''
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"[+] 看板: {html_path}")
    except Exception as e:
        print(f"  [!] 生成看板失败: {e}")

    print(f"\n[*] 完成！")


if __name__ == "__main__":
    main()
