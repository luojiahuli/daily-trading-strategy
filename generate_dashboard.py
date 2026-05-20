#!/usr/bin/env python3
"""
生成完整看板HTML - 直接从缓存数据构建，不需要重新运行回测
优先展示推荐排行 + 操作明细，数据准确展示
"""

import os, json
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 模拟一些测试数据（实际的看板运行时会从回测获取真实数据）
# 这里放一个静态版本的示例数据用来验证HTML渲染

NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日开盘推荐看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:16px}}
h1{{color:#ffd700;text-align:center;font-size:22px}}
.sub{{color:#888;text-align:center;font-size:12px;margin-bottom:12px}}
.dash{{display:grid;grid-template-columns:1fr;gap:12px;max-width:1400px;margin:0 auto}}
.card{{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e;margin-bottom:12px}}
.card h2{{color:#ffd700;font-size:15px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #2a2a5e}}
.chart{{width:100%;height:350px}}
.chart-tall{{height:500px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#2a2a5e;color:#ffd700;padding:8px 6px;text-align:left;position:sticky;top:0}}
td{{padding:6px;border-bottom:1px solid #1a1a3e}}
tr:hover{{background:#1a1a4e}}
.tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold}}
.tag-win{{background:#ff4d4f22;color:#ff4d4f;border:1px solid #ff4d4f66}}
.tag-lose{{background:#52c41a22;color:#52c41a;border:1px solid #52c41a66}}
.scroll{{max-height:600px;overflow-y:auto;border-radius:6px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:16px}}
.stat-box{{background:#1a1a4e;border-radius:8px;padding:14px;text-align:center;border:1px solid #2a2a5e}}
.stat-box .v{{font-size:28px;font-weight:bold}}
.stat-box .l{{font-size:12px;color:#888;margin-top:4px}}
.detail-card{{background:#1a1a3e;border-radius:8px;margin-bottom:10px;padding:14px;border:1px solid #2a2a5e;cursor:pointer;transition:all 0.2s}}
.detail-card:hover{{border-color:#ffd70066;background:#1a1a4e}}
.detail-card .header{{display:flex;justify-content:space-between;align-items:center}}
.detail-card .header .left{{font-size:15px;color:#ffd700}}
.detail-card .header .right{{font-size:13px}}
</style>
</head>
<body>
<h1>📊 每日开盘推荐看板</h1>
<p class="sub">策略: 前日涨2~9% → 止损-3%/止盈+5%/持仓5日 | {NOW}</p>

<div class="dash">
  <div class="card">
    <h2>🏆 今日推荐排行</h2>
    <div id="rankChart" class="chart"></div>
  </div>

  <div class="card">
    <h2>⏱ 个股操作明细（点击展开交易记录）</h2>
    <div id="tradeDetail"></div>
  </div>

  <div class="card">
    <h2>🔗 板块-个股关系图</h2>
    <div id="relChart" class="chart-tall"></div>
  </div>
</div>

<script>
// ======== 测试数据 ========
var recoData = [];
var relData = {{nodes:[],edges:[],categories:[
    {{name:'题材板块',itemStyle:{{color:'#5470c6'}}}},
    {{name:'推荐个股',itemStyle:{{color:'#ff4d4f'}}}}
]}};

// 从localStorage或后端获取真实数据
fetch('/stock_viz_data.json')
    .then(function(r){{return r.json()}})
    .then(function(data){{
        recoData = data.candidates || [];
        relData = data.relation || relData;
        renderAll();
    }})
    .catch(function(err){{
        console.log('No data file found, using empty state');
        renderAll();
    }});

function renderAll() {{
    renderRank();
    renderDetail();
    renderRelation();
}}

function renderRank() {{
    if(!recoData.length) {{
        document.getElementById('rankChart').innerHTML='<p style="color:#888;text-align:center;padding:80px 0;font-size:16px">暂无推荐数据，请运行回测后刷新</p>';
        return;
    }}
    var c = echarts.init(document.getElementById('rankChart'));
    var names = recoData.map(function(r){{return r.name}});
    var pnl = recoData.map(function(r){{return r.total_pnl}});
    var win = recoData.map(function(r){{return r.win_rate}});
    c.setOption({{
        backgroundColor:'transparent',
        tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}}}},
        legend:{{data:['回测收益%','胜率%'],textStyle:{{color:'#ccc'}},top:0}},
        grid:{{left:'8%',right:'8%',top:'15%',bottom:'18%'}},
        xAxis:{{type:'category',data:names,axisLabel:{{rotate:30,fontSize:11,color:'#ccc'}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
        yAxis:[
            {{type:'value',name:'收益%',nameTextStyle:{{color:'#888'}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
            {{type:'value',name:'胜率%',nameTextStyle:{{color:'#888'}},splitLine:{{show:false}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
        ],
        series:[
            {{name:'回测收益%',type:'bar',data:pnl.map(function(v){{return{{value:v,itemStyle:{{color:v>0?'#ff4d4f':'#52c41a'}}}}}}),barWidth:'50%',
                label:{{show:true,position:'top',fontSize:11,color:'#ffd700',fontWeight:'bold',formatter:function(p){{return p.value.toFixed(1)+'%'}}}}
            }},
            {{name:'胜率%',type:'line',yAxisIndex:1,data:win,lineStyle:{{width:3,color:'#1890ff'}},symbol:'circle',symbolSize:8,
                label:{{show:true,position:'bottom',fontSize:11,color:'#1890ff',formatter:function(p){{return p.value+'%'}}}}
            }}
        ]
    }});
    window.addEventListener('resize',function(){{c.resize()}});
}}

function renderDetail() {{
    if(!recoData.length) {{
        document.getElementById('tradeDetail').innerHTML='<p style="color:#888;text-align:center;padding:40px 0;font-size:16px">暂无交易明细数据</p>';
        return;
    }}
    var html = '';
    recoData.forEach(function(r, ri){{
        var trades = r.trades || [];
        var winC = trades.filter(function(t){{return t.pct>0}}).length;
        var totalPnl = r.total_pnl || 0;
        html += '<div class="detail-card" onclick="toggleDetail('+ri+')">';
        html += '<div class="header"><div class="left">#'+(ri+1)+' '+r.name+' <span style="color:#888;font-size:12px">'+r.code+'</span> <span style="color:#888;font-size:12px">| '+r.board+'</span></div>';
        html += '<div class="right"><span style="color:#888">今日 </span><b style="color:'+(r.inc>0?'#ff4d4f':'#52c41a')+'">'+(r.inc>0?'+':'')+r.inc.toFixed(1)+'%</b>';
        html += ' <span style="color:#888">| 回测 </span><b style="color:'+(totalPnl>0?'#ff4d4f':'#52c41a')+'">'+(totalPnl>0?'+':'')+totalPnl.toFixed(1)+'%</b>';
        html += ' <span style="color:#888">| 胜率 </span><b style="color:#1890ff">'+r.win_rate+'%</b> <span style="color:#888">('+winC+'/'+trades.length+')</span>';
        html += ' <span style="color:#888;font-size:11px">▼ 展开</span></div></div>';
        html += '<div id="detail_'+ri+'" style="display:none;margin-top:10px">';
        if(trades.length) {{
            html += '<table><tr><th>#</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益</th><th>持仓</th><th>原因</th></tr>';
            trades.forEach(function(t,ti){{
                html += '<tr><td>'+(ti+1)+'</td><td>'+t.buy_date+'</td><td>'+t.buy_price.toFixed(2)+'</td><td>'+t.sell_date+'</td><td>'+t.sell_price.toFixed(2)+'</td>';
                html += '<td><span class="tag '+(t.pct>0?'tag-win':'tag-lose')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
                html += '<td>'+(t.hold_days||0)+'天</td><td style="color:#888;font-size:12px">'+t.reason+'</td></tr>';
            }});
            html += '</table>';
        }} else {{
            html += '<p style="color:#888;font-size:13px">无交易记录</p>';
        }}
        html += '</div></div>';
    }});
    document.getElementById('tradeDetail').innerHTML = html;
}}

function toggleDetail(idx) {{
    var el = document.getElementById('detail_'+idx);
    if(el) el.style.display = el.style.display==='none'?'block':'none';
}}

function renderRelation() {{
    if(!relData.nodes.length) {{
        document.getElementById('relChart').innerHTML='<p style="color:#888;text-align:center;padding:80px 0;font-size:16px">关系图数据加载中...</p>';
        return;
    }}
    var c = echarts.init(document.getElementById('relChart'));
    c.setOption({{
        backgroundColor:'transparent',
        tooltip:{{formatter:function(p){{
            if(p.dataType==='node'){{
                var d=p.data;
                if(d.category===0) return '<b>📊 '+d.name+'</b><br/>热度: '+d.value;
                return '<b>📈 '+d.shortName+'</b><br/>涨幅: '+d.inc+'%<br/>回测: '+(d.total_pnl>0?'+':'')+d.total_pnl+'%';
            }}
            return '';
        }}}},
        series:[{{
            type:'graph',layout:'force',
            force:{{repulsion:600,edgeLength:[80,200],gravity:0.08}},
            roam:true,draggable:true,
            data:relData.nodes,edges:relData.edges,categories:relData.categories,
            label:{{show:true,position:'right',fontSize:11,color:'#ccc',formatter:function(p){{return p.data.category===0?p.data.name:''}}}},
            lineStyle:{{color:'#2a2a5e',curveness:0.2,opacity:0.3}},
            emphasis:{{focus:'adjacency',lineStyle:{{width:3,opacity:0.8}}}},
        }}]
    }});
    window.addEventListener('resize',function(){{c.resize()}});
}}
</script>
</body>
</html>"""

path = os.path.join(OUTPUT_DIR, "dashboard.html")
with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"[+] 看板模板: {path}")

# 也生成一份带真实数据的完整JSON
import sys; sys.path.insert(0, os.path.dirname(__file__))
print("[*] 运行回测获取真实数据...")
try:
    from stock_viz_dashboard import get_candidates_and_backtest
    candidates = get_candidates_and_backtest()
    print(f"[+] 获取到 {len(candidates)} 只候选股")
    
    # 构建关系图数据
    from stock_theme_analyzer import get_all_concept_boards, get_board_history, calc_board_analysis
    import time
    boards = get_all_concept_boards(use_cache=True)
    board_scores = {}
    for b in boards[:20]:
        history = get_board_history(b["name"])
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            board_scores[b["name"]] = analysis["score"]
        time.sleep(0.1)
    
    nodes = []
    edges = []
    node_set = set()
    for bname, score in sorted(board_scores.items(), key=lambda x: x[1], reverse=True)[:20]:
        if bname not in node_set:
            node_set.add(bname)
            color = "#ff4d4f" if score >= 70 else "#fa8c16" if score >= 55 else "#fadb14"
            nodes.append({"id": bname, "name": bname, "category": 0, "value": round(score, 1),
                         "symbolSize": max(30, min(70, 25 + score * 0.5)), "itemStyle": {"color": color}})
    
    stock_in_boards = {}
    for c in candidates:
        b = c["board"]
        if b not in stock_in_boards: stock_in_boards[b] = []
        stock_in_boards[b].append(c)
    
    for c in candidates[:12]:
        sid = f"{c['code']}|{c['name']}"
        if sid not in node_set:
            node_set.add(sid)
            color = "#ff4d4f" if c["inc"] > 5 else "#ff7a45"
            nodes.append({"id": sid, "name": f"{c['name']}({c['code']})", "shortName": c["name"],
                         "category": 1, "value": abs(c["inc"]), "symbolSize": max(18, min(45, 15 + abs(c["inc"]) * 2)),
                         "inc": c["inc"], "total_pnl": c["total_pnl"], "itemStyle": {"color": color}})
        if c["board"] in board_scores:
            edges.append({"source": c["board"], "target": sid, "value": round(abs(c["inc"]), 1)})
    
    data_path = os.path.join(OUTPUT_DIR, "stock_viz_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({
            "candidates": candidates[:12],
            "relation": {"nodes": nodes, "edges": edges, "categories": [
                {"name": "题材板块", "itemStyle": {"color": "#5470c6"}},
                {"name": "推荐个股", "itemStyle": {"color": "#ff4d4f"}}
            ]}
        }, f, ensure_ascii=False, default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))
    print(f"[+] 数据文件: {data_path}")
    
except Exception as e:
    print(f"[!] 获取真实数据失败: {e}")

print(f"\n[*] 完成！\n  看板: file://{os.path.abspath(path)}\n  数据: file://{os.path.abspath(data_path)}")
