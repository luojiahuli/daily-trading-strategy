#!/usr/bin/env python3
"""
重新生成strategy_compare.html - 在原有对比图表下方增加12只个股详细操作看板

直接在已有HTML基础上追加个股看板模块，保持所有原有ECharts图表不变。
"""

import os, json, re

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
SRC_FILE = os.path.join(OUTPUT_DIR, "strategy_compare.html")
os.makedirs(OUTPUT_DIR, exist_ok=True)

if not os.path.exists(SRC_FILE):
    print(f"[!] 找不到源文件: {SRC_FILE}")
    print("[*] 请先运行: python3 stock_strategy_compare.py")
    exit(1)

with open(SRC_FILE, "r", encoding="utf-8") as f:
    html = f.read()

# 从HTML中提取交易数据
# 找 window.onresize 之前的位置插入看板
insert_pos = html.rfind("window.onresize")
if insert_pos == -1:
    print("[!] 找不到插入位置")
    exit(1)

# 提取关键统计数据用于看板
# 解析现有HTML中的数据
import sys; sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float
import time, requests, re as re2

# 获取12只候选股并回测
boards = get_all_concept_boards(use_cache=True)
seen = {}
print("[*] 获取候选股...")
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
        if sname.startswith(("ST","*ST","退")): continue
        if 2.0 <= inc <= 15.0:
            if code not in seen or inc > seen[code]["inc"]:
                seen[code] = {"code":code,"name":sname,"board":name,"inc":inc,"price":safe_float(s.get("现价",0))}
    time.sleep(0.08)

candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)[:12]
print(f"[+] {len(candidates)} 只候选股")

# 获取K线数据并回测（策略A）
def get_kline_data(code, days=45):
    try:
        code = str(code).strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
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

stock_details = []
for c in candidates:
    print(f"  {c['code']} {c['name']}...", end=" ", flush=True)
    kline = get_kline_data(c["code"])
    if not kline or len(kline) < 30:
        print("数据不足")
        continue
    
    trades = []
    positions = []
    trade_dates = set()
    for i in range(1, len(kline)):
        today = kline[i]
        positions = [p for p in positions if i - p["buy_idx"] < 5]
        for p in positions[:]:
            hold = i - p["buy_idx"]
            hp = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
            lp = (today["low"] - p["buy_price"]) / p["buy_price"] * 100
            if hold >= 5:
                pct = round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":round(today["close"],2),"pct":pct,"reason":"到期卖出","hold_days":hold})
                positions.remove(p)
            elif hp >= 5.0:
                sp = round(p["buy_price"] * 1.05, 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":sp,"pct":5.0,"reason":"止盈+5%","hold_days":hold})
                positions.remove(p)
            elif lp <= -3.0:
                sp = round(p["buy_price"] * 0.97, 2)
                trades.append({"buy_date":p["buy_date"],"buy_price":round(p["buy_price"],2),
                    "sell_date":today["date"],"sell_price":sp,"pct":-3.0,"reason":"止损-3%","hold_days":hold})
                positions.remove(p)
        prev = kline[i-1]
        if 2.0 <= prev["pct"] <= 9.0 and len(positions) < 3 and today["date"] not in trade_dates:
            trade_dates.add(today["date"])
            positions.append({"buy_date":today["date"],"buy_price":kline[i]["open"],"buy_idx":i})
    
    if trades:
        total_pnl = sum(t["pct"] for t in trades)
        wins = sum(1 for t in trades if t["pct"] > 0)
        stock_details.append({**c, "trades":trades, "total_trades":len(trades),
            "total_pnl":round(total_pnl,2), "wins":wins,
            "win_rate":round(wins/len(trades)*100,1),
            "avg_pnl":round(total_pnl/len(trades),2)})
        print(f"{len(trades)}笔 总{total_pnl:+.2f}%")
    else:
        print("无信号")
    time.sleep(0.3)

stock_details.sort(key=lambda x: x["total_pnl"], reverse=True)

# 汇总统计
total_stocks = len(stock_details)
total_trades = sum(s["total_trades"] for s in stock_details)
total_wins = sum(s["wins"] for s in stock_details)
total_pnl = sum(s["total_pnl"] for s in stock_details)

stock_json = json.dumps(stock_details, ensure_ascii=False,
                         default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))

# 构建个股看板HTML
detail_html = f"""
  <!-- ===== 12只个股详细操作看板 ===== -->
  <div class="card full" style="margin-top:14px">
    <h2>📋 12只个股详细操作看板（策略A:基础窗口期，共{total_trades}笔交易）</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0">
      <div style="background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e">
        <div style="font-size:24px;font-weight:bold;color:#ffd700">{total_stocks}</div>
        <div style="font-size:11px;color:#888">推荐个股</div>
      </div>
      <div style="background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e">
        <div style="font-size:24px;font-weight:bold;color:#ff4d4f">{total_trades}</div>
        <div style="font-size:11px;color:#888">总交易笔数</div>
      </div>
      <div style="background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e">
        <div style="font-size:24px;font-weight:bold;color:#ff4d4f">{total_wins}/{total_trades-total_wins}</div>
        <div style="font-size:11px;color:#888">胜/负</div>
      </div>
      <div style="background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e">
        <div style="font-size:24px;font-weight:bold;color:#ff4d4f">{total_pnl:+.1f}%</div>
        <div style="font-size:11px;color:#888">累计收益</div>
      </div>
    </div>
    <div id="stockDetailContainer"></div>
  </div>
</div>

<script>
// ===== 个股详细操作数据 =====
var stockData = {stock_json};

var container = document.getElementById('stockDetailContainer');
var detailHtml = '';
stockData.forEach(function(s, si) {{
    var trades = s.trades || [];
    var pnl = s.total_pnl || 0;
    var winC = trades.filter(function(t){{return t.pct>0}}).length;
    detailHtml += '<div class="detail-card" onclick="var e=document.getElementById(\\'sd_'+si+'\\');e.style.display=e.style.display===\\'none\\'?\\'block\\':\\'none\\'">';
    detailHtml += '<div class="hdr"><div class="nm">#'+(si+1)+' '+s.name+' <span style="color:#888;font-size:12px">'+s.code+'</span> <span style="color:#888;font-size:12px">| '+s.board+'</span></div>';
    detailHtml += '<div class="st"><span style="color:#888">今日 </span><b style="color:'+(s.inc>0?'#ff4d4f':'#52c41a')+'">'+(s.inc>0?'+':'')+s.inc.toFixed(1)+'%</b>';
    detailHtml += ' <span style="color:#888">| 回测 </span><b style="color:'+(pnl>0?'#ff4d4f':'#52c41a')+'">'+(pnl>0?'+':'')+pnl.toFixed(1)+'%</b>';
    detailHtml += ' <span style="color:#888">| 胜率 </span><b style="color:#1890ff">'+s.win_rate+'%</b> <span style="color:#888">('+winC+'/'+trades.length+')</span>';
    detailHtml += ' <span style="font-size:11px;color:#888">▼ 展开</span></div></div>';
    detailHtml += '<div id="sd_'+si+'" style="display:none;margin-top:10px">';
    if(trades.length > 0) {{
        detailHtml += '<table><tr><th>#</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益</th><th>持仓</th><th>原因</th></tr>';
        trades.forEach(function(t,ti) {{
            detailHtml += '<tr><td>'+(ti+1)+'</td><td>'+t.buy_date+'</td><td>'+t.buy_price.toFixed(2)+'</td><td>'+t.sell_date+'</td><td>'+t.sell_price.toFixed(2)+'</td>';
            detailHtml += '<td><span class="tag '+(t.pct>0?'win':'lose')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
            detailHtml += '<td>'+(t.hold_days||0)+'天</td><td style="color:#888;font-size:11px">'+t.reason+'</td></tr>';
        }});
        detailHtml += '</table>';
    }} else {{
        detailHtml += '<p style="color:#888;font-size:13px">无交易记录</p>';
    }}
    detailHtml += '</div></div>';
}});
container.innerHTML = detailHtml;
</script>
"""

# 在window.onresize后面插入
insert_target = "window.onresize=function(){barChart.resize();cumChart.resize();distChart.resize()};"
new_target = insert_target + "\n" + detail_html
html = html.replace(insert_target, new_target)

# 写回文件
with open(SRC_FILE, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\n[+] 已更新: {SRC_FILE}")

# 打开浏览器
import webbrowser
webbrowser.open(f"file://{os.path.abspath(SRC_FILE)}")
print(f"[+] 已在浏览器中打开")
