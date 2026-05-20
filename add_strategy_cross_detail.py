#!/usr/bin/env python3
"""
重新生成strategy_compare.html - 增加策略×个股交叉明细看板

每一笔交易标注所属策略，展示:
  - 每只个股在不同策略下的表现对比
  - 每个策略在每只个股上的操作明细
  - 策略组合的交叉收益分析
"""

import os, sys, json, re, time, requests as req

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
SRC_FILE = os.path.join(OUTPUT_DIR, "strategy_compare.html")
os.makedirs(OUTPUT_DIR, exist_ok=True)

if not os.path.exists(SRC_FILE):
    print(f"[!] 找不到源文件: {SRC_FILE}，请先运行 stock_strategy_compare.py")
    exit(1)

with open(SRC_FILE, "r", encoding="utf-8") as f:
    html = f.read()

# ============================================================
# 获取12只候选股并对每个策略分别回测
# ============================================================
import re as re2
sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

def get_kline(code, days=50):
    try:
        code = str(code).strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = req.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200: return None
        match = re2.search(r'\[.*\]', resp.text)
        if not match: return None
        data = json.loads(match.group())
        records = []
        for d in data:
            records.append({"date":d["day"],"open":float(d["open"]),"close":float(d["close"]),
                          "high":float(d["high"]),"low":float(d["low"])})
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else: records[-1]["pct"] = 0.0
        return records
    except: return None

def run_strategy(kline, check_buy_fn, stop_loss, take_profit, max_hold=5):
    """通用策略运行器"""
    trades = []
    positions = []
    trade_dates = set()
    for i in range(1, len(kline)):
        today = kline[i]
        positions = [p for p in positions if i - p["buy_idx"] < max_hold]
        for p in positions[:]:
            hold = i - p["buy_idx"]
            hp = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
            lp = (today["low"] - p["buy_price"]) / p["buy_price"] * 100
            if hold >= max_hold:
                pct = round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":round(today["close"],2),
                    "pct":pct,"reason":"到期卖出","hold_days":hold})
                positions.remove(p)
            elif hp >= take_profit:
                sp = round(p["buy_price"] * (1 + take_profit/100), 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":sp,
                    "pct":take_profit,"reason":f"止盈+{take_profit:.0f}%","hold_days":hold})
                positions.remove(p)
            elif lp <= stop_loss:
                sp = round(p["buy_price"] * (1 + stop_loss/100), 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":sp,
                    "pct":stop_loss,"reason":f"止损{stop_loss:.0f}%","hold_days":hold})
                positions.remove(p)
        prev = kline[i-1]
        buy_price = check_buy_fn(kline, i)
        if buy_price is not None and len(positions) < 3 and today["date"] not in trade_dates:
            trade_dates.add(today["date"])
            positions.append({"buy_date":today["date"],"buy_price":buy_price,"buy_idx":i})
    return trades

# 策略定义
STRATEGIES = {
    "A:基础窗口期": {
        "check": lambda kl, i: kl[i]["open"] if 2.0 <= kl[i-1]["pct"] <= 9.0 else None,
        "sl": -3.0, "tp": 5.0, "desc": "前日涨2~9%买入,止损-3%/止盈+5%"
    },
    "B:强势过滤": {
        "check": lambda kl, i: kl[i]["open"] if 4.0 <= kl[i-1]["pct"] <= 9.0 else None,
        "sl": -5.0, "tp": 8.0, "desc": "前日涨4~9%买入,止损-5%/止盈+8%"
    },
    "C:趋势确认": {
        "check": lambda kl, i: kl[i]["open"] if i >= 2 and kl[i-2]["pct"] > 2.0 and kl[i-1]["pct"] > 2.0 else None,
        "sl": -4.0, "tp": 6.0, "desc": "连续2日涨>2%买入,止损-4%/止盈+6%"
    },
    "E:最优组合": {
        "check": lambda kl, i: kl[i]["open"] if 4.0 <= kl[i-1]["pct"] <= 9.0 else None,
        "sl": -4.0, "tp": 8.0, "desc": "前日涨4~9%买入,止损-4%/止盈+8%"
    },
}

# 获取候选股
print("[*] 获取候选股...")
boards = get_all_concept_boards(use_cache=True)
seen = {}
for board in boards[:60]:
    name = board["name"]
    cons = get_board_constituents(name)
    if not cons:
        time.sleep(0.08)
        continue
    for s in cons[:8]:
        code = s.get("代码","")
        sname = s.get("名称","")
        inc = safe_float(s.get("涨幅",0))
        if sname.startswith(("ST","*ST","退")): continue
        if 2.0 <= inc <= 15.0:
            if code not in seen or inc > seen[code]["inc"]:
                seen[code] = {"code":code,"name":sname,"board":name,"inc":inc,"price":safe_float(s.get("现价",0))}
    time.sleep(0.08)

candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)[:12]
print(f"[+] {len(candidates)} 只候选股")

# 对每只股运行所有策略
# 数据结构: stock_results[code] = {name, board, inc, strategies: {strat_name: {trades, total_pnl, wins, ...}}}
all_stock_data = []

for c in candidates:
    print(f"\n  {c['code']} {c['name']} ({c['board']}) inc={c['inc']:+.1f}%")
    kline = get_kline(c["code"])
    if not kline or len(kline) < 30:
        print("    数据不足")
        continue

    strat_results = {}
    for sname, sconf in STRATEGIES.items():
        trades = run_strategy(kline, sconf["check"], sconf["sl"], sconf["tp"])
        if trades:
            total_pnl = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            strat_results[sname] = {
                "trades": trades,
                "total_trades": len(trades),
                "total_pnl": round(total_pnl, 2),
                "wins": wins,
                "win_rate": round(wins/len(trades)*100, 1),
                "avg_pnl": round(total_pnl/len(trades), 2),
                "desc": sconf["desc"],
            }
            print(f"    {sname}: {len(trades)}笔 总{total_pnl:+.2f}% 胜{wins}/{len(trades)}")
        else:
            strat_results[sname] = {"trades":[],"total_trades":0,"total_pnl":0,"wins":0,"win_rate":0,"avg_pnl":0,"desc":sconf["desc"]}
            print(f"    {sname}: 无信号")
    
    all_stock_data.append({**c, "strategies": strat_results})
    time.sleep(0.3)

# 统计汇总
total_by_strat = {}
for sname in STRATEGIES:
    total_trades = sum(s["strategies"][sname]["total_trades"] for s in all_stock_data)
    total_pnl = sum(s["strategies"][sname]["total_pnl"] for s in all_stock_data)
    total_wins = sum(s["strategies"][sname]["wins"] for s in all_stock_data)
    total_by_strat[sname] = {"trades": total_trades, "pnl": round(total_pnl, 2), "wins": total_wins}

print(f"\n{'='*70}")
print(f"  策略组合汇总")
print(f"{'='*70}")
for sname, st in total_by_strat.items():
    desc = STRATEGIES[sname]["desc"]
    print(f"  {sname:<18} {desc:<35} 交易{st['trades']:>3} 总{st['pnl']:>+7.2f}%")

# ============================================================
# 生成看板HTML并注入
# ============================================================
stock_json = json.dumps(all_stock_data, ensure_ascii=False,
                         default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))

strat_total_json = json.dumps(total_by_strat, ensure_ascii=False)

detail_html = f"""
  <!-- ===== 策略×个股交叉明细看板 ===== -->
  <div class="card full" style="margin-top:14px">
    <h2>📋 策略×个股交叉明细看板（{len(all_stock_data)}只个股 × 4种策略）</h2>

    <!-- 策略汇总 -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0">
      <script id="stratSummaryData" type="application/json">{strat_total_json}</script>
      <div id="stratSummaryContainer"></div>
    </div>

    <p style="color:#888;font-size:12px;margin-bottom:10px">
      每只个股展示4种策略的对比，点击展开查看该策略的完整买卖记录。
      黄色高亮 = 该股的最佳策略
    </p>

    <!-- 按个股分组的策略对比卡片 -->
    <div style="max-height:700px;overflow-y:auto" id="stockDetailContainer"></div>
  </div>
</div>

<script>
var stockCrossData = {stock_json};
var stratTotalData = {strat_total_json};

// ===== 策略汇总统计 =====
var stratSummaryHtml = '';
var sNames = Object.keys(stratTotalData);
var sColors = {{'A:基础窗口期':'#ff4d4f','B:强势过滤':'#1890ff','C:趋势确认':'#52c41a','E:最优组合':'#fa8c16'}};
sNames.forEach(function(sn) {{
    var d = stratTotalData[sn];
    stratSummaryHtml += '<div style="background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid '+(sColors[sn]||'#2a2a5e')+'66">';
    stratSummaryHtml += '<div style="font-size:11px;color:'+(sColors[sn]||'#888')+';font-weight:bold">'+sn+'</div>';
    stratSummaryHtml += '<div style="font-size:20px;font-weight:bold;color:'+(d.pnl>0?'#ff4d4f':'#52c41a')+'">'+(d.pnl>0?'+':'')+d.pnl.toFixed(1)+'%</div>';
    stratSummaryHtml += '<div style="font-size:11px;color:#888">'+d.trades+'笔 | 胜'+d.wins+'</div>';
    stratSummaryHtml += '</div>';
}});
document.getElementById('stratSummaryContainer').innerHTML = stratSummaryHtml;

// ===== 个股×策略明细 =====
var detailHtml = '';
stockCrossData.forEach(function(stock, si) {{
    var strats = stock.strategies || {{}};
    var sList = Object.keys(strats);

    // 找到该股的最佳策略
    var bestStrat = '';
    var bestPnl = -9999;
    sList.forEach(function(sn) {{
        if(strats[sn].total_pnl > bestPnl) {{
            bestPnl = strats[sn].total_pnl;
            bestStrat = sn;
        }}
    }});

    detailHtml += '<div class="detail-card" onclick="var e=document.getElementById(\\'sd_'+si+'\\');e.style.display=e.style.display===\\'none\\'?\\'block\\':\\'none\\'">';
    detailHtml += '<div class="hdr"><div class="nm">#'+(si+1)+' '+stock.name+' <span style="color:#888;font-size:12px">'+stock.code+'</span> <span style="color:#888;font-size:12px">| '+stock.board+'</span></div>';
    detailHtml += '<div class="st"><span style="color:#888">今日 </span><b style="color:'+(stock.inc>0?'#ff4d4f':'#52c41a')+'">'+(stock.inc>0?'+':'')+stock.inc.toFixed(1)+'%</b>';
    detailHtml += ' <span style="font-size:11px;color:#888">▼ 展开策略对比</span></div></div>';

    // 策略对比小标签
    detailHtml += '<div style="display:flex;gap:6px;margin:6px 0">';
    sList.forEach(function(sn) {{
        var d = strats[sn];
        var isBest = (sn === bestStrat);
        detailHtml += '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;' +
            'background:'+(isBest?'#ffd70022':'#1a1a4e')+';' +
            'border:1px solid '+(isBest?'#ffd70066':sColors[sn]+'44')+';' +
            'color:'+(isBest?'#ffd700':sColors[sn])+'">' +
            sn.substr(0,1)+': '+(d.total_pnl>0?'+':'')+d.total_pnl.toFixed(1)+'% ('+d.wins+'/'+d.total_trades+')' +
            (isBest?' 🏆':'') + '</span>';
    }});
    detailHtml += '</div>';

    detailHtml += '<div id="sd_'+si+'" style="display:none;margin-top:6px">';

    // 每个策略的明细
    sList.forEach(function(sn) {{
        var d = strats[sn];
        var trades = d.trades || [];
        var isBest = (sn === bestStrat);
        detailHtml += '<div style="background:'+(isBest?'#ffd70008':'#111128')+';border-radius:6px;padding:10px;margin-bottom:8px;border:1px solid '+(isBest?'#ffd70044':sColors[sn]+'33')+'">';
        detailHtml += '<div style="font-size:13px;color:'+(isBest?'#ffd700':sColors[sn])+';font-weight:bold;margin-bottom:6px">';
        detailHtml += '【'+sn+'】'+d.desc+(isBest?' 🏆':'')+' | 总收益<b style="color:'+(d.total_pnl>0?'#ff4d4f':'#52c41a')+'">'+(d.total_pnl>0?'+':'')+d.total_pnl.toFixed(1)+'%</b> 胜率<b style="color:#1890ff">'+d.win_rate+'%</b> ('+d.wins+'/'+d.total_trades+')';
        detailHtml += '</div>';
        if(trades.length > 0) {{
            detailHtml += '<table><tr><th>#</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益</th><th>持仓</th><th>原因</th></tr>';
            trades.forEach(function(t,ti) {{
                detailHtml += '<tr><td>'+(ti+1)+'</td><td>'+t.buy_date+'</td><td>'+t.buy_price.toFixed(2)+'</td><td>'+t.sell_date+'</td><td>'+t.sell_price.toFixed(2)+'</td>';
                detailHtml += '<td><span class="tag '+(t.pct>0?'win':'lose')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
                detailHtml += '<td>'+(t.hold_days||0)+'天</td><td style="color:#888;font-size:11px">'+t.reason+'</td></tr>';
            }});
            detailHtml += '</table>';
        }} else {{
            detailHtml += '<p style="color:#888;font-size:12px;padding:4px 0">该策略在本股上无交易信号</p>';
        }}
        detailHtml += '</div>';
    }});

    detailHtml += '</div></div>';
}});
document.getElementById('stockDetailContainer').innerHTML = detailHtml;
</script>
"""

# 注入到现有HTML
insert_target = "window.onresize=function(){barChart.resize();cumChart.resize();distChart.resize()};"
if insert_target in html:
    html = html.replace(insert_target, insert_target + "\n" + detail_html)
    with open(SRC_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[+] 已更新: {SRC_FILE} ({os.path.getsize(SRC_FILE)} bytes)")
else:
    print("[!] 找不到注入点，尝试备用方案...")
    # 在</body>前插入
    html = html.replace("</body>", detail_html + "\n</body>")
    with open(SRC_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 已更新(备用方案): {SRC_FILE}")

import webbrowser
webbrowser.open(f"file://{os.path.abspath(SRC_FILE)}")
print("[+] 已在浏览器中打开")
