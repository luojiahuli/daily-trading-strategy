#!/usr/bin/env python3
"""
完整可视化看板 - 板块关系图 + 每日推荐 + 回测明细 + 个股操作过程

生成一个独立HTML页面，包含:
  1. 板块-个股力导向关系图
  2. 今日推荐个股 TOP 8（含回测收益、胜率）
  3. 每只推荐股的交易明细（买入日、卖出日、每笔收益）
  4. 各策略累计收益曲线对比
  5. 操作过程时间线

使用方式:
  python3 stock_viz_dashboard.py --quick --open
"""

import os, sys, time, json, argparse, webbrowser, re
from datetime import datetime
from typing import Optional

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_history, get_board_constituents,
    calc_board_analysis, safe_float, CACHE_DIR
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 个股K线获取（新浪API）
# ============================================================
def get_kline_data(code: str, days: int = 45) -> Optional[list]:
    # 先试 Sina
    data = _sina_kline_direct(code, days)
    if data:
        return data
    # 降级到腾讯
    try:
        from stock_data_fallback import tencent_kline
        data = tencent_kline(code, days=days)
        if data:
            return _convert_tencent_kline(data)
    except ImportError:
        pass
    return None


def _sina_kline_direct(code: str, days: int = 45) -> Optional[list]:
    try:
        code = str(code).strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200: return None
        match = re.search(r'\[.*\]', resp.text)
        if not match: return None
        data = json.loads(match.group())
        records = []
        for d in data:
            records.append({"date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                          "high": float(d["high"]), "low": float(d["low"])})
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else: records[-1]["pct"] = 0.0
        return records
    except: return None


def _convert_tencent_kline(data: list) -> list:
    records = []
    for d in data:
        records.append({"date": d["day"], "open": d["open"], "close": d["close"],
                        "high": d["high"], "low": d["low"]})
        if len(records) >= 2:
            prev = records[-2]["close"]
            records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
        else:
            records[-1]["pct"] = 0.0
    return records


# ============================================================
# 获取候选股 + 回测
# ============================================================
STOP_LOSS = -3.0
TAKE_PROFIT = 5.0
MAX_HOLD = 5

def get_candidates_and_backtest() -> list:
    """获取候选股并运行回测，返回含回测明细的数据"""
    boards = get_all_concept_boards(use_cache=True)
    seen = {}

    print("[*] 扫描板块候选股...")
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
            price = safe_float(s.get("现价", 0))
            if sname.startswith(("ST","*ST","退")): continue
            if 0 <= inc <= 15:
                if code not in seen or inc > seen[code]["inc"]:
                    seen[code] = {"code": code, "name": sname, "board": name, "inc": inc, "price": price}
        time.sleep(0.08)

    candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)
    print(f"[+] {len(candidates)} 只候选股，开始回测...")

    results = []
    for idx, c in enumerate(candidates[:15]):
        print(f"  [{idx+1}/15] {c['code']} {c['name']}...", end=" ", flush=True)
        kline = get_kline_data(c["code"])
        if not kline or len(kline) < 30:
            print("数据不足")
            continue

        trades = []
        positions = []
        trade_dates = set()

        for i in range(1, len(kline)):
            today = kline[i]
            positions = [p for p in positions if i - p["buy_idx"] < MAX_HOLD]

            for p in positions[:]:
                hold = i - p["buy_idx"]
                hp = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
                lp = (today["low"] - p["buy_price"]) / p["buy_price"] * 100

                if hold >= MAX_HOLD:
                    pct = round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2)
                    trades.append({"buy_date": p["buy_date"], "buy_price": round(p["buy_price"],2),
                        "sell_date": today["date"], "sell_price": round(today["close"],2),
                        "pct": pct, "reason": "到期卖出", "hold_days": hold})
                    positions.remove(p)
                elif hp >= TAKE_PROFIT:
                    sp = round(p["buy_price"] * (1 + TAKE_PROFIT/100), 2)
                    trades.append({"buy_date": p["buy_date"], "buy_price": round(p["buy_price"],2),
                        "sell_date": today["date"], "sell_price": sp,
                        "pct": TAKE_PROFIT, "reason": f"止盈+{TAKE_PROFIT:.0f}%", "hold_days": hold})
                    positions.remove(p)
                elif lp <= STOP_LOSS:
                    sp = round(p["buy_price"] * (1 + STOP_LOSS/100), 2)
                    trades.append({"buy_date": p["buy_date"], "buy_price": round(p["buy_price"],2),
                        "sell_date": today["date"], "sell_price": sp,
                        "pct": STOP_LOSS, "reason": f"止损{STOP_LOSS:.0f}%", "hold_days": hold})
                    positions.remove(p)

            prev = kline[i-1]
            if 2.0 <= prev["pct"] <= 9.0 and len(positions) < 3 and today["date"] not in trade_dates:
                trade_dates.add(today["date"])
                positions.append({"buy_date": today["date"], "buy_price": kline[i]["open"], "buy_idx": i})

        if trades:
            total_pnl = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            results.append({**c, "trades": trades, "total_trades": len(trades),
                          "total_pnl": round(total_pnl, 2), "wins": wins,
                          "win_rate": round(wins/len(trades)*100, 1),
                          "avg_pnl": round(total_pnl/len(trades), 2)})
            print(f"{len(trades)}笔 总{total_pnl:+.2f}%")
        else:
            print("无信号")
        time.sleep(0.3)

    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    return results


# ============================================================
# 构建HTML
# ============================================================
def build_html(candidates_with_backtest: list) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 构建关系图数据
    boards = get_all_concept_boards(use_cache=True)
    board_scores = {}
    for b in boards[:30]:
        history = get_board_history(b["name"])
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            board_scores[b["name"]] = analysis["score"]
        time.sleep(0.15)

    # 关系图节点
    nodes = []
    edges = []
    node_set = set()

    # 板块名->候选股映射
    stock_in_boards = {}
    for c in candidates_with_backtest:
        b = c["board"]
        if b not in stock_in_boards:
            stock_in_boards[b] = []
        stock_in_boards[b].append(c)

    # 板块节点
    for bname, score in sorted(board_scores.items(), key=lambda x: x[1], reverse=True)[:20]:
        if bname not in node_set:
            node_set.add(bname)
            color = "#ff4d4f" if score >= 70 else "#fa8c16" if score >= 55 else "#fadb14" if score >= 40 else "#91cc75"
            nodes.append({"id": bname, "name": bname, "category": 0, "value": round(score, 1),
                         "symbolSize": max(30, min(70, 25 + score * 0.5)),
                         "itemStyle": {"color": color}})

    # 个股节点+边
    for c in candidates_with_backtest[:12]:
        sid = f"{c['code']}|{c['name']}"
        if sid not in node_set:
            node_set.add(sid)
            color = "#ff4d4f" if c["inc"] > 5 else "#ff7a45" if c["inc"] > 2 else "#91cc75"
            nodes.append({"id": sid, "name": f"{c['name']}({c['code']})", "shortName": c["name"],
                         "category": 1, "value": abs(c["inc"]),
                         "symbolSize": max(18, min(45, 15 + abs(c["inc"]) * 2)),
                         "inc": c["inc"], "total_pnl": c["total_pnl"],
                         "itemStyle": {"color": color}})
        if c["board"] in board_scores:
            edges.append({"source": c["board"], "target": sid,
                         "value": round(abs(c["inc"]), 1)})

    import json as _json
    rel_data = _json.dumps({"nodes": nodes, "edges": edges,
        "categories": [{"name": "题材板块","itemStyle":{"color":"#5470c6"}},
                       {"name": "推荐个股","itemStyle":{"color":"#ff4d4f"}}]}, ensure_ascii=False)

    # 候选股JSON
    reco_data = _json.dumps(candidates_with_backtest[:10], ensure_ascii=False,
                            default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日开盘推荐看板 - {now}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:16px}}
h1{{color:#ffd700;text-align:center;font-size:22px}}
.sub{{color:#888;text-align:center;font-size:12px;margin-bottom:12px}}
.dash{{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:1600px;margin:0 auto}}
.card{{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e}}
.card h2{{color:#ffd700;font-size:14px;margin-bottom:8px;display:flex;align-items:center;gap:6px}}
.full{{grid-column:1/-1}}
.half{{grid-column:span 1}}
.chart{{width:100%;height:380px}}
.chart-tall{{height:500px}}
.chart-short{{height:280px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#2a2a5e;color:#ffd700;padding:6px 5px;text-align:left;position:sticky;top:0;font-size:11px;white-space:nowrap}}
td{{padding:4px 5px;border-bottom:1px solid #1a1a3e;font-size:11px}}
tr:hover{{background:#1a1a4e}}
.scroll{{max-height:420px;overflow-y:auto}}
.tag{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:bold}}
.tag-win{{background:#ff4d4f22;color:#ff4d4f;border:1px solid #ff4d4f66}}
.tag-lose{{background:#52c41a22;color:#52c41a;border:1px solid #52c41a66}}
.tag-hold{{background:#1890ff22;color:#1890ff;border:1px solid #1890ff66}}
.tooltip-box{{font-size:12px;line-height:1.5}}
.summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}}
.summary-item{{background:#1a1a3e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e}}
.summary-item .v{{font-size:22px;font-weight:bold}}
.summary-item .l{{font-size:11px;color:#888;margin-top:2px}}
</style>
</head>
<body>

<h1>📊 每日开盘推荐看板</h1>
<p class="sub">策略: 前日涨2~9% → 止损-3%/止盈+5%/持仓5日 | {now}</p>

<div class="dash">
  <!-- 关系图 + 统计 -->
  <div class="card full">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h2>🔗 板块-推荐个股关系图</h2>
      <div>
        <button onclick="filterReco()" style="background:#ff4d4f33;color:#ff4d4f;border:1px solid #ff4d4f66;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">仅推荐股</button>
        <button onclick="resetGraph()" style="background:#2a2a5e;color:#ccc;border:1px solid #3a3a7e;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:11px">全部</button>
      </div>
    </div>
    <div id="relationChart" class="chart-tall"></div>
  </div>

  <!-- 推荐排行 -->
  <div class="card full">
    <h2>🏆 今日推荐个股 TOP {min(10, len(candidates_with_backtest))}</h2>
    <div class="summary-grid" id="summaryGrid"></div>
    <div id="rankChart" class="chart-short"></div>
  </div>

  <!-- 操作过程时间线 -->
  <div class="card full">
    <h2>⏱ 个股操作过程明细</h2>
    <p style="color:#888;font-size:11px;margin-bottom:6px">展开查看每只推荐股的完整交易记录（买入→卖出→每笔收益）</p>
    <div id="tradeDetail"></div>
  </div>
</div>

<script>
var recoData = {reco_data};
var relData = {rel_data};

// ======== 汇总统计 ========
var totalTrades = 0, totalWins = 0, totalPnl = 0;
recoData.forEach(function(r) {{
    totalTrades += r.total_trades || 0;
    totalWins += r.wins || 0;
    totalPnl += r.total_pnl || 0;
}});
var gridHtml = '';
gridHtml += '<div class="summary-item" style="border-color:#ffd70066"><div class="v" style="color:#ffd700">' + recoData.length + '</div><div class="l">推荐个股</div></div>';
gridHtml += '<div class="summary-item"><div class="v" style="color:#ff4d4f">' + totalTrades + '</div><div class="l">总交易笔数</div></div>';
gridHtml += '<div class="summary-item"><div class="v" style="color:#ff4d4f">' + totalWins + '/' + (totalTrades - totalWins) + '</div><div class="l">胜/负</div></div>';
gridHtml += '<div class="summary-item"><div class="v" style="color:' + (totalPnl > 0 ? '#ff4d4f' : '#52c41a') + '">' + (totalPnl > 0 ? '+' : '') + totalPnl.toFixed(1) + '%</div><div class="l">累计收益</div></div>';
document.getElementById('summaryGrid').innerHTML = gridHtml;

// ======== 关系图 ========
var relChart = echarts.init(document.getElementById('relationChart'));
var allNodeIds = relData.nodes.map(function(n){{return n.id}});
relChart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{formatter:function(p){{
        if(p.dataType==='node'){{
            var d=p.data;
            if(d.category===0) return '<div class="tooltip-box"><b>📊 '+d.name+'</b><br/>热度: '+d.value+'</div>';
            return '<div class="tooltip-box"><b>📈 '+d.shortName+'</b><br/>今日涨幅: '+d.inc+'%<br/>回测收益: '+(d.total_pnl>0?'+':'')+d.total_pnl+'%</div>';
        }}
        return '';
    }}}},
    series:[{{
        type:'graph',layout:'force',
        force:{{repulsion:600,edgeLength:[80,200],gravity:0.08}},
        roam:true,draggable:true,
        data:relData.nodes,edges:relData.edges,categories:relData.categories,
        label:{{show:true,position:'right',fontSize:10,color:'#ccc',
            formatter:function(p){{return p.data.category===0?p.data.name:''}}}},
        lineStyle:{{color:'#2a2a5e',curveness:0.2,opacity:0.3}},
        emphasis:{{focus:'adjacency',lineStyle:{{width:3,opacity:0.8}}}},
    }}]
}});

function filterReco() {{
    var sid = new Set();
    recoData.forEach(function(r){{sid.add(r.code+'|'+r.name)}});
    var fn = relData.nodes.filter(function(n){{return n.category===0||sid.has(n.id)}});
    var fid = new Set(fn.map(function(n){{return n.id}}));
    var fe = relData.edges.filter(function(e){{return fid.has(e.source)&&fid.has(e.target)}});
    relChart.setOption({{series:[{{data:fn,edges:fe}}]}});
}}
function resetGraph() {{
    relChart.setOption({{series:[{{data:relData.nodes,edges:relData.edges}}]}});
}}

// ======== 推荐排名柱状图 ========
var rankChart = echarts.init(document.getElementById('rankChart'));
var rnames = recoData.map(function(r){{return r.name}});
var rpnl = recoData.map(function(r){{return r.total_pnl}});
var rwin = recoData.map(function(r){{return r.win_rate}});
rankChart.setOption({{
    backgroundColor:'transparent',
    grid:{{left:'8%',right:'8%',top:'8%',bottom:'20%'}},
    tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}}}},
    legend:{{data:['回测收益%','胜率%'],textStyle:{{color:'#ccc'}},top:0}},
    xAxis:{{type:'category',data:rnames,axisLabel:{{rotate:30,fontSize:9,color:'#999'}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:[
        {{type:'value',name:'回测收益%',nameTextStyle:{{color:'#888',fontSize:10}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',fontSize:10,formatter:function(v){{return v+'%'}}}}}},
        {{type:'value',name:'胜率%',nameTextStyle:{{color:'#888',fontSize:10}},splitLine:{{show:false}},axisLabel:{{color:'#888',fontSize:10,formatter:function(v){{return v+'%'}}}}}},
    ],
    series:[
        {{name:'回测收益%',type:'bar',data:rpnl.map(function(v){{return{{value:v,itemStyle:{{color:v>0?'#ff4d4f':'#52c41a'}}}}}}),barWidth:'40%',label:{{show:true,position:'top',fontSize:9,color:'#ccc',formatter:function(p){{return p.value.toFixed(1)+'%'}}}}}},
        {{name:'胜率%',type:'line',yAxisIndex:1,data:rwin,lineStyle:{{width:2,color:'#1890ff'}},symbol:'circle',symbolSize:6,label:{{show:true,position:'bottom',fontSize:9,color:'#1890ff',formatter:function(p){{return p.value+'%'}}}}}},
    ]
}});

// ======== 个股操作明细 ========
var tradeHtml = '';
recoData.forEach(function(r, ri) {{
    var trades = r.trades || [];
    var totalP = r.total_pnl || 0;
    var winC = trades.filter(function(t){{return t.pct>0}}).length;
    tradeHtml += '<div style="background:#1a1a4e;border-radius:6px;margin-bottom:8px;padding:10px;border:1px solid #2a2a5e">';
    tradeHtml += '<div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer" onclick="var e=document.getElementById(\'td_'+ri+'\');e.style.display=e.style.display===\'none\'?\'block\':\'none\'">';
    tradeHtml += '<div><b style="color:#ffd700;font-size:13px">#'+(ri+1)+' '+r.name+'</b> <span style="color:#888;font-size:11px">'+r.code+'</span> <span style="color:#888;font-size:11px">| '+r.board+'</span></div>';
    tradeHtml += '<div><span style="color:#888;font-size:11px">今日涨幅 </span><span style="color:'+(r.inc>0?'#ff4d4f':'#52c41a')+';font-size:13px;font-weight:bold">'+(r.inc>0?'+':'')+r.inc.toFixed(1)+'%</span>';
    tradeHtml += ' <span style="color:#888;font-size:11px">| 回测 </span><span style="color:'+(totalP>0?'#ff4d4f':'#52c41a')+';font-size:13px;font-weight:bold">'+(totalP>0?'+':'')+totalP.toFixed(1)+'%</span>';
    tradeHtml += ' <span style="color:#888;font-size:11px">| 胜率 </span><span style="color:#1890ff;font-size:13px;font-weight:bold">'+r.win_rate+'%</span>';
    tradeHtml += ' <span style="color:#888;font-size:11px">('+winC+'/'+trades.length+')</span>';
    tradeHtml += ' <span style="font-size:11px;color:#888">▼</span></div></div>';
    tradeHtml += '<div id="td_'+ri+'" style="display:none;margin-top:8px">';
    if(trades.length > 0) {{
        tradeHtml += '<table><tr><th>#</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益%</th><th>持仓</th><th>原因</th></tr>';
        trades.forEach(function(t, ti) {{
            tradeHtml += '<tr><td>'+(ti+1)+'</td><td>'+t.buy_date+'</td><td>'+t.buy_price.toFixed(2)+'</td><td>'+t.sell_date+'</td><td>'+t.sell_price.toFixed(2)+'</td>';
            tradeHtml += '<td><span class="tag '+(t.pct>0?'tag-win':'tag-lose')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
            tradeHtml += '<td>'+(t.hold_days||0)+'天</td><td style="font-size:10px;color:#888">'+t.reason+'</td></tr>';
        }});
        tradeHtml += '</table>';
    }} else {{
        tradeHtml += '<p style="color:#888;font-size:11px">无交易记录</p>';
    }}
    tradeHtml += '</div></div>';
}});
document.getElementById('tradeDetail').innerHTML = tradeHtml;

window.onresize = function() {{ relChart.resize(); rankChart.resize(); }};
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="完整开盘推荐看板")
    parser.add_argument("--open", action="store_true", help="打开浏览器")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    print("""
  ╔══════════════════════════════════════════╗
  ║    每日开盘推荐看板                      ║
  ║    关系图 + 推荐排行 + 操作明细          ║
  ╚══════════════════════════════════════════╝
    """)

    # 获取回测数据
    candidates = get_candidates_and_backtest()
    if not candidates:
        print("[!] 无候选股")
        return

    # 生成HTML
    print("\n[*] 生成可视化看板...")
    html = build_html(candidates)
    path = os.path.join(OUTPUT_DIR, "daily_dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 看板: {path}")

    if args.open:
        webbrowser.open(f"file://{os.path.abspath(path)}")


if __name__ == "__main__":
    main()
