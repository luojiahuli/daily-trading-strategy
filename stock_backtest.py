#!/usr/bin/env python3
"""
窗口期策略回测系统 v2 - 更高效的实现

核心思路:
  1. 先找出当前窗口期板块中的代表个股
  2. 获取这些个股近一个月的日K线
  3. 模拟每个交易日买入->止盈止损->卖出的过程
  4. 统计整体收益

策略:
  - 买入: 当日收盘价
  - 卖出: 盘中达止盈+5%或止损-3%时触发，否则持仓5日收盘卖出
  - 仓位: 等权重，最多同时3只
"""

import os, sys, time, json, argparse, math, re
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_constituents,
    safe_float, CACHE_DIR
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

STOP_LOSS = -3.0
TAKE_PROFIT = 5.0
MAX_HOLD = 5
MAX_POSITIONS = 3
INITIAL_CAPITAL = 100000


def get_kline_data(code: str, days: int = 45) -> Optional[list]:
    """获取个股日K线 (使用新浪财经API)"""
    try:
        # 判断代码前缀
        code = str(code).strip()
        if code.startswith(("6", "9")):
            symbol = f"sh{code}"
        elif code.startswith(("0", "3")):
            symbol = f"sz{code}"
        elif code.startswith(("4", "8")):
            symbol = f"bj{code}"
        else:
            symbol = f"sh{code}"

        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        import re, json
        match = re.search(r'\[.*\]', resp.text)
        if not match:
            return None

        data = json.loads(match.group())
        records = []
        for d in data:
            records.append({
                "date": d["day"],
                "open": float(d["open"]),
                "close": float(d["close"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
            })
            # 计算涨跌幅
            if len(records) >= 2:
                prev = records[-2]["close"]
                curr = records[-1]["close"]
                records[-1]["pct"] = round((curr - prev) / prev * 100, 2)
            else:
                records[-1]["pct"] = 0.0

        return records
    except Exception as e:
        return None


def get_today_stock_picks() -> list:
    """获取今日窗口期板块的个股推荐"""
    boards = get_all_concept_boards(use_cache=True)
    picks = []

    for board in boards[:60]:
        name = board["name"]
        cons = get_board_constituents(name)
        if not cons:
            time.sleep(0.1)
            continue

        for s in cons[:10]:
            code = s.get("代码", "")
            sname = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))

            if sname.startswith(("ST", "*ST", "退")):
                continue
            if 2.0 <= inc <= 9.0:
                picks.append({
                    "code": code,
                    "name": sname,
                    "board": name,
                    "inc": inc,
                    "price": safe_float(s.get("现价", 0)),
                })

        time.sleep(0.1)

    # 去重（同一只股票可能出现在多个板块）
    seen = set()
    unique_picks = []
    for p in picks:
        if p["code"] not in seen:
            seen.add(p["code"])
            unique_picks.append(p)

    unique_picks.sort(key=lambda x: x["inc"], reverse=True)
    return unique_picks[:MAX_POSITIONS * 3]


def simulate_trades(kline: list, code: str, name: str, board: str) -> list:
    """
    在整段K线数据上模拟窗口期策略交易

    规则:
      - 每个交易日检查是否符合买入条件（前一日符合窗口期特征，通过板块判断）
      - 持有中逐日检查止盈止损
    """
    trades = []
    positions = []  # [{"buy_date", "buy_price", "buy_idx", "hold_days"}, ...]

    for i in range(5, len(kline) - 1):
        today = kline[i]
        today_date = today["date"]

        # 清理已到期的持仓
        positions = [p for p in positions if p["hold_days"] < MAX_HOLD]

        # 检查是否需要卖出
        for p in positions[:]:
            entry_idx = p["buy_idx"]
            hold_days = i - entry_idx

            if hold_days >= MAX_HOLD:
                # 到期卖出
                trades.append({
                    "code": code, "name": name, "board": board,
                    "buy_date": p["buy_date"],
                    "buy_price": p["buy_price"],
                    "sell_date": today_date,
                    "sell_price": today["close"],
                    "pct": round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2),
                    "reason": "到期卖出",
                })
                positions.remove(p)
                continue

            # 检查盘中止盈止损
            high_pct = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
            low_pct = (today["low"] - p["buy_price"]) / p["buy_price"] * 100

            if high_pct >= TAKE_PROFIT:
                sell_price = p["buy_price"] * (1 + TAKE_PROFIT / 100)
                trades.append({
                    "code": code, "name": name, "board": board,
                    "buy_date": p["buy_date"],
                    "buy_price": p["buy_price"],
                    "sell_date": today_date,
                    "sell_price": round(sell_price, 2),
                    "pct": TAKE_PROFIT,
                    "reason": f"止盈+{TAKE_PROFIT}%",
                })
                positions.remove(p)
            elif low_pct <= STOP_LOSS:
                sell_price = p["buy_price"] * (1 + STOP_LOSS / 100)
                trades.append({
                    "code": code, "name": name, "board": board,
                    "buy_date": p["buy_date"],
                    "buy_price": p["buy_price"],
                    "sell_date": today_date,
                    "sell_price": round(sell_price, 2),
                    "pct": STOP_LOSS,
                    "reason": f"止损{STOP_LOSS}%",
                })
                positions.remove(p)

        # 买入信号: 前一日涨幅在2%~9%之间（窗口期特征）
        prev_day = kline[i - 1]
        prev_pct = prev_day["pct"]
        if 1.5 <= prev_pct <= 9.0 and len(positions) < MAX_POSITIONS:
            # 检查是否已买过这只股且未卖出
            if not any(p["buy_date"] == today_date for p in positions):
                # 以开盘价买入（模拟次日开盘买入）
                buy_price = today["open"]
                positions.append({
                    "buy_date": today_date,
                    "buy_price": buy_price,
                    "buy_idx": i,
                    "hold_days": 0,
                })

    return trades


def run_backtest(picks: list) -> dict:
    """对选出的个股逐只回测"""
    all_trades = []

    print(f"[*] 回测 {len(picks)} 只候选个股...\n")

    for idx, pick in enumerate(picks):
        code = pick["code"]
        name = pick["name"]
        board = pick["board"]
        print(f"  [{idx+1}/{len(picks)}] {code} {name} ({board})...", end=" ", flush=True)

        kline = get_kline_data(code)
        if not kline or len(kline) < 10:
            print("数据不足")
            continue

        trades = simulate_trades(kline, code, name, board)
        if trades:
            all_trades.extend(trades)
            print(f"{len(trades)} 笔交易")
        else:
            print("无信号")
        time.sleep(0.3)

    return analyze_results(all_trades)


def analyze_results(trades: list) -> dict:
    """分析交易结果"""
    if not trades:
        return {"trades": [], "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_pnl": 0, "total_pnl": 0, "max_drawdown": 0}

    wins = sum(1 for t in trades if t["pct"] > 0)
    losses = len(trades) - wins
    total_pnl = sum(t["pct"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    win_rate = wins / len(trades) * 100

    # 最大回撤
    max_dd = 0
    peak = 0
    cum = 0
    for t in trades:
        cum += t["pct"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    trades.sort(key=lambda x: x["sell_date"])

    return {
        "trades": trades,
        "total": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 2),
    }


def generate_html(result: dict) -> str:
    """生成可视化HTML"""
    trades = result["trades"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 收益分布
    dist = {"ge5": 0, "ge2_lt5": 0, "ge0_lt2": 0, "lt0_ge-2": 0, "lt-2": 0}
    for t in trades:
        p = t["pct"]
        if p >= 5: dist["ge5"] += 1
        elif p >= 2: dist["ge2_lt5"] += 1
        elif p >= 0: dist["ge0_lt2"] += 1
        elif p >= -2: dist["lt0_ge-2"] += 1
        else: dist["lt-2"] += 1

    # 累计收益曲线
    cum_data = []
    cum_labels = []
    running = 0
    for t in trades:
        running += t["pct"]
        cum_data.append(round(running, 2))
        cum_labels.append(t["sell_date"])

    # 每日收益
    daily = {}
    for t in trades:
        d = t["sell_date"]
        daily[d] = daily.get(d, 0) + t["pct"]
    daily_items = sorted(daily.items())

    import json
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>窗口期策略回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:20px}}
h1{{color:#ffd700;text-align:center;font-size:24px;margin-bottom:5px}}
.subtitle{{text-align:center;color:#888;font-size:13px;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;max-width:900px;margin:15px auto}}
.stat{{background:#1a1a3e;border-radius:8px;padding:12px;text-align:center;border:1px solid #2a2a5e}}
.stat .v{{font-size:26px;font-weight:bold}}
.stat .l{{font-size:12px;color:#888;margin-top:3px}}
.green .v{{color:#ff4d4f}}
.red .v{{color:#52c41a}}
.blue .v{{color:#1890ff}}
.dash{{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:1200px;margin:0 auto}}
.card{{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e}}
.card h2{{color:#ffd700;font-size:14px;margin-bottom:8px}}
.full{{grid-column:1/-1}}
.chart{{width:100%;height:350px}}
.chart.tall{{height:450px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#2a2a5e;color:#ffd700;padding:6px;text-align:left;position:sticky;top:0}}
td{{padding:5px 6px;border-bottom:1px solid #1a1a3e;font-size:12px}}
.tag{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:11px}}
.win{{background:#ff4d4f33;color:#ff4d4f;border:1px solid #ff4d4f66}}
.lose{{background:#52c41a33;color:#52c41a;border:1px solid #52c41a66}}
.scroll{{max-height:400px;overflow-y:auto}}
</style>
</head>
<body>
<h1>📊 窗口期策略回测报告</h1>
<p class="subtitle">策略: 涨幅2%~9%买入 | 止损-3% | 止盈+5% | 持仓≤5日 | 回测周期: 近一个月 | {now}</p>

<div class="grid">
  <div class="stat green"><div class="v">{result['total']}</div><div class="l">总交易</div></div>
  <div class="stat green"><div class="v">{result['win_rate']}%</div><div class="l">胜率</div></div>
  <div class="stat green"><div class="v">{result['total_pnl']:+.2f}%</div><div class="l">总收益率</div></div>
  <div class="stat blue"><div class="v">{result['avg_pnl']:+.2f}%</div><div class="l">平均单笔</div></div>
  <div class="stat green"><div class="v">{result['wins']}</div><div class="l">盈利</div></div>
  <div class="stat red"><div class="v">{result['losses']}</div><div class="l">亏损</div></div>
</div>

<div class="dash">
  <div class="card"><h2>📊 收益分布</h2><div id="dist" class="chart"></div></div>
  <div class="card"><h2>📈 累计收益</h2><div id="cum" class="chart"></div></div>
  <div class="card full"><h2>📉 每日收益明细</h2><div id="daily" class="chart"></div></div>
  <div class="card full">
    <h2>📋 交易明细 ({result['total']}笔)</h2>
    <div class="scroll">
    <table><tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>买入</th><th>买入价</th><th>卖出</th><th>卖出价</th><th>收益</th><th>原因</th></tr>
    {''.join(f'<tr><td>{i+1}</td><td>{t["code"]}</td><td>{t["name"]}</td><td style="font-size:11px">{t["board"]}</td><td>{t["buy_date"]}</td><td>{t["buy_price"]:.2f}</td><td>{t["sell_date"]}</td><td>{t["sell_price"]:.2f}</td><td><span class="tag {"win" if t["pct"]>=0 else "lose"}">{t["pct"]:+.2f}%</span></td><td style="font-size:11px">{t["reason"]}</td></tr>' for i,t in enumerate(trades))}
    </table>
    </div>
  </div>
</div>

<script>
var distData = {json.dumps(dist)};
var cumData = {json.dumps(cum_data)};
var cumLabels = {json.dumps(cum_labels)};
var dailyLabels = {json.dumps([d for d,_ in daily_items])};
var dailyValues = {json.dumps([v for _,v in daily_items])};

var d1=echarts.init(document.getElementById('dist'));
d1.setOption({{
    backgroundColor:'transparent',
    series:[{{type:'pie',radius:['40%','70%'],center:['50%','55%'],
        data:[
            {{value:distData.ge5,name:'≥5%',itemStyle:{{color:'#ff4d4f'}}}},
            {{value:distData.ge2_lt5,name:'2~5%',itemStyle:{{color:'#ff7a45'}}}},
            {{value:distData.ge0_lt2,name:'0~2%',itemStyle:{{color:'#ffa39e'}}}},
            {{value:distData['lt0_ge-2'],name:'-2~0%',itemStyle:{{color:'#b7eb8f'}}}},
            {{value:distData['lt-2'],name:'<-2%',itemStyle:{{color:'#52c41a'}}}},
        ],
        label:{{color:'#ccc',fontSize:11}},
    }}]
}});

var d2=echarts.init(document.getElementById('cum'));
d2.setOption({{
    backgroundColor:'transparent',
    grid:{{left:'8%',right:'5%',top:'10%',bottom:'15%'}},
    xAxis:{{type:'category',data:cumLabels,axisLabel:{{rotate:45,fontSize:10,color:'#999'}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:{{type:'value',name:'累计%',nameTextStyle:{{color:'#888'}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
    series:[{{type:'line',data:cumData,smooth:true,lineStyle:{{width:2,color:'#ff4d4f'}},
        areaStyle:{{color:new echarts.graphic.LinearGradient(0,0,0,1,[{{offset:0,color:'#ff4d4f66'}},{{offset:1,color:'#ff4d4f00'}}])}},
        markLine:{{data:[{{yAxis:0}}],lineStyle:{{color:'#555',type:'dashed'}},label:{{show:false}}}},
        label:{{show:true,position:'top',fontSize:10,color:'#ccc',formatter:function(p){{return p.value+'%'}}}},
    }}]
}});

var d3=echarts.init(document.getElementById('daily'));
d3.setOption({{
    backgroundColor:'transparent',
    grid:{{left:'5%',right:'3%',top:'8%',bottom:'12%'}},
    xAxis:{{type:'category',data:dailyLabels,axisLabel:{{rotate:30,fontSize:10,color:'#999'}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:{{type:'value',name:'收益%',nameTextStyle:{{color:'#888'}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
    series:[{{type:'bar',data:dailyValues.map(function(v){{return{{value:v,itemStyle:{{color:v>=0?'#ff4d4f':'#52c41a'}}}}}}),barWidth:'50%'}}]
}});

window.onresize=function(){{d1.resize();d2.resize();d3.resize()}};
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="窗口期策略回测v2")
    parser.add_argument("--open", action="store_true", help="打开浏览器")
    args = parser.parse_args()

    print("""
  ╔══════════════════════════════════════════╗
  ║    窗口期策略回测系统 v2                 ║
  ║    止损-3% / 止盈+5% / 最长5日           ║
  ╚══════════════════════════════════════════╝
    """)

    # 1. 获取今日窗口期候选股
    print("[*] 扫描今日窗口期候选股...")
    picks = get_today_stock_picks()
    print(f"[+] 找到 {len(picks)} 只候选股")

    for p in picks[:10]:
        print(f"    {p['code']} {p['name']:>8} {p['board']:<12} 涨幅{p['inc']:+.2f}%")

    if not picks:
        print("[!] 今日无窗口期候选股")
        return

    # 2. 回测
    result = run_backtest(picks)

    if result["total"] == 0:
        print("[!] 回测无交易")
        return

    # 3. 输出统计
    print(f"""
  ═══════════════ 回测结果 ═══════════════
  总交易: {result['total']}
  盈利:   {result['wins']}
  亏损:   {result['losses']}
  胜率:   {result['win_rate']}%
  总收益: {result['total_pnl']:+.2f}%
  平均:   {result['avg_pnl']:+.2f}%
  最大回撤: {result['max_drawdown']:.2f}%
  ══════════════════════════════════════════
    """)

    # 4. 生成HTML
    print("[*] 生成回测报告HTML...")
    html = generate_html(result)
    path = os.path.join(OUTPUT_DIR, "backtest_report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 报告: {path}")

    if args.open:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(path)}")

    # 打印交易明细
    print("\n  交易明细:")
    print(f"  {'代码':<10} {'名称':<10} {'买入日':<12} {'卖出日':<12} {'收益%':>8} {'原因'}")
    print(f"  {'-'*60}")
    for t in result["trades"]:
        tag = "✓" if t["pct"] > 0 else "✗"
        print(f"  {t['code']:<10} {t['name']:<10} {t['buy_date']:<12} {t['sell_date']:<12} {t['pct']:>+7.2f}% {tag} {t['reason']}")


if __name__ == "__main__":
    main()
