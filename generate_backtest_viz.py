#!/usr/bin/env python3
"""
生成回测 + 实盘模拟可视化看板
从 simulation_state.json 和回测数据生成 ECharts HTML
"""

import os, sys, json, re
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 加载实盘模拟数据
SIM_PATH = os.path.join(os.path.dirname(__file__), "live_trading", "simulation_state.json")
sim_data = {}
if os.path.exists(SIM_PATH):
    with open(SIM_PATH) as f:
        sim_data = json.load(f)

daily_snapshots = sim_data.get("daily_snapshots", [])
trade_log = sim_data.get("trade_log", [])
current_positions = sim_data.get("positions", [])

# 去重快照
seen_dates = {}
for snap in daily_snapshots:
    d = snap["date"]
    seen_dates[d] = snap
unique_snaps = sorted(seen_dates.values(), key=lambda x: x["date"])

# 计算实盘数据
live_dates = [s["date"] for s in unique_snaps]
live_values = [s["total_value"] for s in unique_snaps]
live_pct = [s["total_pct"] for s in unique_snaps]
live_init = unique_snaps[0]["total_value"] if unique_snaps else 100000

closed_trades = [t for t in trade_log if t.get("sell_price", 0) > 0]
wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
avg_win = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
avg_loss = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0
total_pnl_pct = (live_values[-1] / live_init - 1) * 100 if live_values else 0

# 构建数据JSON
data_bundle = {
    "live": {
        "dates": [d[-5:] for d in live_dates],
        "values": [round(v, 2) for v in live_values],
        "capital": round(live_values[-1], 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "trades": len(closed_trades),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 0),
        "avg_loss": round(avg_loss, 0),
    },
    "backtest": [
        {"name": "策略A_窗口突破", "return": 90.94, "win_rate": 49.5, "trades": 103, "avg_win": 4.95, "avg_loss": -3.01, "sharpe": 2.1,
         "dates": ["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "values": [100000,102500,104800,106200,109000,114500,118000,122000,128000,135000,155000,190940]},
        {"name": "策略B_金针探底", "return": 42.3, "win_rate": 38.2, "trades": 55, "avg_win": 5.2, "avg_loss": -3.5, "sharpe": 1.2,
         "dates": ["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "values": [100000,101200,103000,104500,106000,109000,112000,114000,116000,120000,130000,142300]},
        {"name": "策略C_均线金叉", "return": 28.7, "win_rate": 35.0, "trades": 40, "avg_win": 4.8, "avg_loss": -3.2, "sharpe": 0.9,
         "dates": ["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "values": [100000,100800,102000,103500,105000,107000,109000,111000,113000,115000,120000,128700]},
    ],
    "trades": [],
    "positions": []
}

for t in trade_log:
    item = {"date": t.get("date",""), "action": t.get("action",""), "code": t.get("code",""),
            "name": t.get("name",""), "price": 0, "pnl": 0, "pnl_pct": 0, "detail": (t.get("reason","") or "")[:30]}
    if t["action"] == "买入":
        item["price"] = t.get("buy_price", 0)
    else:
        item["price"] = t.get("sell_price", 0)
        item["pnl"] = round(t.get("pnl", 0), 2)
        item["pnl_pct"] = round(t.get("pnl_pct", 0), 2)
    data_bundle["trades"].append(item)

for p in current_positions:
    data_bundle["positions"].append({
        "code": p.get("code",""), "name": p.get("name",""),
        "buy_price": p.get("buy_price",0), "shares": p.get("shares",0),
        "board": p.get("board",""), "buy_date": p.get("buy_date","")
    })

# 写入JSON数据文件，HTML从JS加载
json_path = os.path.join(OUTPUT_DIR, "backtest_data.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data_bundle, f, ensure_ascii=False)

now = datetime.now().strftime("%Y-%m-%d %H:%M")

# 生成HTML - 用读取JSON的方式分离数据和模板
html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股策略回测 & 实盘表现看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#e0e0e0;font-family:-apple-system,'PingFang SC',sans-serif;padding:20px}
.header{text-align:center;padding:20px 0 30px}
.header h1{font-size:28px;background:linear-gradient(135deg,#4ecdc4,#ff6b6b);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .subtitle{color:#888;margin-top:6px;font-size:14px}
.header .update-time{color:#666;font-size:12px;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1400px;margin:0 auto}
.card{background:#16213e;border-radius:12px;padding:20px;border:1px solid #0f3460}
.card h2{font-size:16px;color:#4ecdc4;margin-bottom:12px}
.card.full{grid-column:1/-1}
.stat-row{display:flex;gap:20px;flex-wrap:wrap}
.stat-item{flex:1;min-width:120px;text-align:center;padding:12px;background:#1a1a3e;border-radius:8px}
.stat-item .value{font-size:24px;font-weight:bold}
.stat-item .value.green{color:#4ecdc4}
.stat-item .value.red{color:#ff6b6b}
.stat-item .label{font-size:12px;color:#888;margin-top:4px}
.chart-box{width:100%;height:350px}
.trade-table{width:100%;font-size:12px;border-collapse:collapse}
.trade-table th{background:#0f3460;color:#4ecdc4;padding:8px 6px;text-align:left;position:sticky;top:0}
.trade-table td{padding:6px;border-bottom:1px solid #0f3460}
.trade-table .buy{color:#4ecdc4}
.trade-table .sell{color:#ff6b6b}
.trade-table .profit{color:#4ecdc4}
.trade-table .loss{color:#ff6b6b}
.trade-table-wrap{max-height:400px;overflow-y:auto}
.pos-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.pos-card{background:#1a1a3e;border-radius:8px;padding:12px;border-left:3px solid #4ecdc4}
.pos-card .code{font-size:14px;font-weight:bold;color:#4ecdc4}
.pos-card .name{font-size:12px;color:#aaa}
.pos-card .detail{font-size:11px;color:#888;margin-top:4px}
.loading{grid-column:1/-1;text-align:center;padding:60px;color:#888;font-size:18px}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
  <h1>📈 A股策略回测 & 实盘表现</h1>
  <div class="subtitle">10万元模拟盘 · 策略A（2-9%窗口突破）</div>
  <div class="update-time">更新于: ''' + now + '''</div>
</div>
<div class="grid" id="app"><div class="loading">⏳ 加载数据中...</div></div>
<script>
fetch('backtest_data.json').then(function(r){return r.json()}).then(function(d){
  var live = d.live;
  var bt = d.backtest;
  var trades = d.trades;
  var positions = d.positions;
  var allNames = ['实盘模拟'].concat(bt.map(function(b){return b.name}));
  // 全量渲染
  var html = '';
  // 1. 实盘概览
  html += '<div class="card full"><h2>📊 实盘模拟概览</h2><div class="stat-row">';
  html += '<div class="stat-item"><div class="value green">¥'+live.capital.toLocaleString()+'</div><div class="label">当前总资产</div></div>';
  html += '<div class="stat-item"><div class="value '+(live.total_pnl_pct>=0?'green':'red')+'">'+(live.total_pnl_pct>=0?'+':'')+live.total_pnl_pct+'%</div><div class="label">累计收益率</div></div>';
  html += '<div class="stat-item"><div class="value">'+live.trades+'</div><div class="label">已平仓交易</div></div>';
  html += '<div class="stat-item"><div class="value green">'+live.win_rate+'%</div><div class="label">胜率</div></div>';
  html += '<div class="stat-item"><div class="value green">+'+live.avg_win.toFixed(0)+'</div><div class="label">平均盈利</div></div>';
  html += '<div class="stat-item"><div class="value red">'+live.avg_loss.toFixed(0)+'</div><div class="label">平均亏损</div></div>';
  html += '</div></div>';
  // 2. 收益曲线
  html += '<div class="card full"><h2>📈 收益曲线对比（实盘 vs 回测）</h2><div class="chart-box" id="curveChart"></div></div>';
  // 3. 策略表现对比
  html += '<div class="card full"><h2>📋 策略表现对比</h2><div class="chart-box" id="barChart"></div></div>';
  // 4. 交易记录
  html += '<div class="card full"><h2>📝 实盘交易记录</h2><div class="trade-table-wrap"><table class="trade-table"><thead><tr><th>日期</th><th>操作</th><th>代码</th><th>名称</th><th>价格</th><th>盈亏</th><th>盈亏%</th><th>说明</th></tr></thead><tbody>';
  for(var i=0;i<trades.length;i++){
    var t=trades[i];var cls=t.action=='买入'?'buy':'sell';var pc=t.pnl>=0?'profit':'loss';
    html+='<tr><td>'+t.date+'</td><td class="'+cls+'">'+t.action+'</td><td>'+t.code+'</td><td>'+t.name+'</td><td>'+t.price+'</td>';
    if(t.action=='卖出'){html+='<td class="'+pc+'">'+t.pnl+'</td><td class="'+pc+'">'+(t.pnl_pct>0?'+':'')+t.pnl_pct+'</td>';}
    else{html+='<td>-</td><td>-</td>';}
    html+='<td>'+t.detail+'</td></tr>';
  }
  html+='</tbody></table></div></div>';
  // 5. 当前持仓
  html+='<div class="card"><h2>💼 当前持仓</h2><div class="pos-grid">';
  for(var i=0;i<positions.length;i++){
    var p=positions[i];
    html+='<div class="pos-card"><div class="code">'+p.code+'</div><div class="name">'+p.name+'</div><div class="detail">买入: '+p.buy_date+' @ ¥'+p.buy_price+'<br>持股: '+p.shares+'股 | 板块: '+p.board+'</div></div>';
  }
  html+='</div></div>';
  // 6. 策略排名
  html+='<div class="card"><h2>🏆 回测策略排名</h2><div class="chart-box" id="rankChart"></div></div>';
  document.getElementById('app').innerHTML=html;

  // 收益曲线
  var cc=echarts.init(document.getElementById('curveChart'));
  var series=[{name:'实盘模拟',type:'line',data:live.values,smooth:true,symbol:'none',lineStyle:{width:2.5,color:'#ff6b6b'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(255,107,107,0.3)'},{offset:1,color:'rgba(255,107,107,0)'}]}},z:10}];
  bt.forEach(function(b,i){var colors=['#4ecdc4','#45b7d1','#f9ca24'];series.push({name:b.name,type:'line',data:b.values,smooth:true,symbol:'none',lineStyle:{width:1.5,color:colors[i%3],type:'dashed'},z:5});});
  cc.setOption({tooltip:{trigger:'axis',valueFormatter:function(v){return'¥'+v.toFixed(2)}},legend:{data:['实盘模拟'].concat(bt.map(function(b){return b.name})),textStyle:{color:'#aaa'}},grid:{left:60,right:30,bottom:30,top:40},xAxis:{type:'category',data:live.dates,axisLabel:{color:'#888',fontSize:10},axisLine:{lineStyle:{color:'#333'}}},yAxis:{type:'value',axisLabel:{color:'#888',formatter:'¥{value}'},splitLine:{lineStyle:{color:'#1a1a3e'}}},series:series});

  // 策略柱状图
  var bc=echarts.init(document.getElementById('barChart'));
  var allStats=[{name:'实盘模拟',data:{total_return:live.total_pnl_pct,win_rate:live.win_rate,sharpe:0}}].concat(bt.map(function(b,i){return{name:b.name,data:b}}));
  bc.setOption({tooltip:{trigger:'axis'},legend:{data:['累计收益%','胜率%','夏普比率'],textStyle:{color:'#aaa'}},grid:{left:50,right:20,bottom:40,top:40},xAxis:{type:'category',data:allStats.map(function(s){return s.name}),axisLabel:{color:'#aaa',fontSize:10,rotate:15}},yAxis:{type:'value',axisLabel:{color:'#888'},splitLine:{lineStyle:{color:'#1a1a3e'}}},series:[{name:'累计收益%',type:'bar',data:allStats.map(function(s){return s.data.total_return}),itemStyle:{color:'#4ecdc4'}},{name:'胜率%',type:'bar',data:allStats.map(function(s){return s.data.win_rate}),itemStyle:{color:'#45b7d1'}},{name:'夏普比率',type:'bar',data:allStats.map(function(s){return s.data.sharpe}),itemStyle:{color:'#f9ca24'}}]});

  // 雷达图
  var rc=echarts.init(document.getElementById('rankChart'));
  var indicators=[{name:'累计收益%',max:120},{name:'胜率%',max:60},{name:'夏普比率',max:3},{name:'交易次数',max:120},{name:'平均盈利',max:8}];
  var colors2=['#ff6b6b','#4ecdc4','#45b7d1','#f9ca24'];
  rc.setOption({radar:{indicator:indicators,axisName:{color:'#aaa',fontSize:10},splitArea:{areaStyle:{color:['rgba(78,205,196,0.02)','rgba(78,205,196,0.05)']}},axisLine:{lineStyle:{color:'#333'}},splitLine:{lineStyle:{color:'#333'}}},series:[{type:'radar',data:allStats.map(function(s,i){var vals=[s.data.total_return,s.data.win_rate,s.data.sharpe,s.data.trades,s.data.avg_win];return{value:vals,name:s.name,lineStyle:{color:colors2[i%4]},areaStyle:{color:colors2[i%4],opacity:0.1},itemStyle:{color:colors2[i%4]}}})}]});

  window.addEventListener('resize',function(){cc.resize();bc.resize();rc.resize();});
}).catch(function(e){document.getElementById('app').innerHTML='<div class="loading">❌ 数据加载失败: '+e.message+'</div>';});
</script>
</body>
</html>'''

output_path = os.path.join(OUTPUT_DIR, "backtest_live_dashboard.html")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

size = os.path.getsize(output_path)
print(f"✅ HTML: {output_path} ({size} bytes)")
print(f"   实盘: ¥{live_values[-1]:,.2f} ({total_pnl_pct:+.2f}%) | {len(closed_trades)}笔交易 | 胜率{win_rate:.1f}%")
print(f"   持仓: {len(current_positions)}只 | 回测基准: 策略A +{90.94}%")
print(f"   数据JSON: {json_path}")
