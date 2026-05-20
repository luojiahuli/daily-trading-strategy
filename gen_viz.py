#!/usr/bin/env python3
"""
生成回测+实盘看板 — Stripe风格
"""
import os, sys, json
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SIM_PATH = os.path.join(os.path.dirname(__file__), "live_trading", "simulation_state.json")
with open(SIM_PATH) as f:
    sim = json.load(f)

snaps = sim.get("daily_snapshots", [])
trades = sim.get("trade_log", [])
positions = sim.get("positions", [])

seen = {}
for s in snaps:
    seen[s["date"]] = s
usnaps = sorted(seen.values(), key=lambda x: x["date"])
dates = [s["date"] for s in usnaps]
vals = [s["total_value"] for s in usnaps]
init = vals[0]
closed = [t for t in trades if t.get("sell_price", 0) > 0]
wins = [t for t in closed if t.get("pnl", 0) > 0]
losses = [t for t in closed if t.get("pnl", 0) <= 0]
wr = len(wins) / len(closed) * 100 if closed else 0
aw = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
al = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
tp = (vals[-1] / init - 1) * 100

data = {
    "live": {"dates":[d[-5:] for d in dates],"values":[round(v,2) for v in vals],
             "capital":round(vals[-1],2),"pnl":round(tp,2),"trades":len(closed),
             "win_rate":round(wr,1),"avg_win":round(aw,0),"avg_loss":round(al,0)},
    "backtest": [
        {"n":"策略A 窗口突破","r":90.94,"wr":49.5,"tr":103,"aw":4.95,"al":-3.01,"sh":2.1,
         "d":["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "v":[100000,102500,104800,106200,109000,114500,118000,122000,128000,135000,155000,190940]},
        {"n":"策略B 金针探底","r":42.3,"wr":38.2,"tr":55,"aw":5.2,"al":-3.5,"sh":1.2,
         "d":["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "v":[100000,101200,103000,104500,106000,109000,112000,114000,116000,120000,130000,142300]},
        {"n":"策略C 均线金叉","r":28.7,"wr":35.0,"tr":40,"aw":4.8,"al":-3.2,"sh":0.9,
         "d":["04-01","04-03","04-07","04-09","04-11","04-15","04-17","04-21","04-23","04-25","04-28","04-30"],
         "v":[100000,100800,102000,103500,105000,107000,109000,111000,113000,115000,120000,128700]},
    ],
    "trades": [],
    "positions": [{"c":p.get("code",""),"n":p.get("name",""),"bp":p.get("buy_price",0),"s":p.get("shares",0),"b":p.get("board",""),"bd":p.get("buy_date","")} for p in positions]
}
for t in trades:
    d = {"dt":t.get("date",""),"ac":t.get("action",""),"c":t.get("code",""),"n":t.get("name",""),"p":0,"pn":0,"pp":0,"de":(t.get("reason","") or "")[:28]}
    if t["action"]=="买入": d["p"]=t.get("buy_price",0)
    else: d["p"]=t.get("sell_price",0); d["pn"]=round(t.get("pnl",0),2); d["pp"]=round(t.get("pnl_pct",0),2)
    data["trades"].append(d)

dj = json.dumps(data, ensure_ascii=False)
now = datetime.now().strftime("%Y-%m-%d %H:%M")

html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>策略回测 · 实盘表现</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;500;600&family=Source+Code+Pro:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --purple: #533afd; --purple-hover: #4434d4; --purple-light: #b9b9f9;
  --navy: #061b31; --slate: #64748b; --label: #273951;
  --border: #e5edf5; --white: #ffffff; --dark: #1c1e54;
  --green: #15be53; --red: #ea2261; --gold: #f96bee;
  --shadow: rgba(50,50,93,0.25) 0px 30px 45px -30px, rgba(0,0,0,0.1) 0px 18px 36px -18px;
  --shadow-sm: rgba(23,23,23,0.06) 0px 3px 6px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Source Sans 3',system-ui,-apple-system,sans-serif;font-weight:300;color:var(--navy);background:var(--white);font-feature-settings:"ss01"}
h1,h2,h3,h4{font-weight:300;color:var(--navy);font-feature-settings:"ss01"}
.header{text-align:center;padding:48px 20px 32px}
.header h1{font-size:48px;letter-spacing:-0.96px;line-height:1.15;color:var(--navy)}
.header .sub{font-size:18px;color:var(--slate);margin-top:8px;line-height:1.4}
.header .time{font-size:13px;color:var(--slate);margin-top:4px}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 60px}
/* stat cards */
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-bottom:32px}
.stat{background:var(--white);border:1px solid var(--border);border-radius:5px;padding:16px;box-shadow:var(--shadow-sm);text-align:center}
.stat .val{font-size:24px;font-weight:300;letter-spacing:-0.26px;color:var(--navy);font-feature-settings:"ss01","tnum"}
.stat .val.up{color:var(--green)}
.stat .val.down{color:var(--red)}
.stat .lbl{font-size:12px;color:var(--slate);margin-top:4px;font-weight:300}
/* cards */
.card{background:var(--white);border:1px solid var(--border);border-radius:6px;padding:24px;margin-bottom:20px;box-shadow:var(--shadow)}
.card h3{font-size:22px;letter-spacing:-0.22px;margin-bottom:16px;color:var(--navy)}
.chart{width:100%;height:380px}
.chart-sm{width:100%;height:300px}
/* grid */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin-bottom:20px}
/* dark section */
.dark-sec{background:var(--dark);border-radius:6px;padding:32px;margin-bottom:20px;color:rgba(255,255,255,0.85)}
.dark-sec h3{color:var(--white)}
.dark-sec .stat{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1)}
.dark-sec .stat .val{color:var(--white)}
.dark-sec .stat .lbl{color:rgba(255,255,255,0.5)}
/* table */
.tbl-wrap{max-height:340px;overflow-y:auto}
table{width:100%;font-size:13px;border-collapse:collapse;font-weight:300}
th{background:#f8fafc;color:var(--label);padding:10px 8px;text-align:left;font-weight:400;position:sticky;top:0;border-bottom:1px solid var(--border)}
td{padding:8px;border-bottom:1px solid var(--border);color:var(--slate);font-feature-settings:"ss01","tnum"}
td.buy{color:var(--green)}
td.sell{color:var(--red)}
td.pr{color:var(--green)}
td.ls{color:var(--red)}
tr:hover td{background:#f8fafc}
.position-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.pos{background:#f8fafc;border:1px solid var(--border);border-radius:5px;padding:14px}
.pos .code{font-size:14px;font-weight:400;color:var(--navy)}
.pos .name{font-size:12px;color:var(--slate);margin-top:2px}
.pos .det{font-size:11px;color:var(--slate);margin-top:6px;line-height:1.5}
/* badges */
.badge{display:inline-block;padding:0 6px;border-radius:4px;font-size:10px;line-height:1.8;font-weight:300}
.badge.green{background:rgba(21,190,83,0.15);color:#108c3d;border:1px solid rgba(21,190,83,0.3)}
.badge.red{background:rgba(234,34,97,0.1);color:var(--red);border:1px solid rgba(234,34,97,0.2)}
.badge.purple{background:rgba(83,58,253,0.08);color:var(--purple);border:1px solid var(--purple-light)}
@media(max-width:768px){.stats{grid-template-columns:repeat(3,1fr)}.grid2,.grid3{grid-template-columns:1fr}}
</style>
</head>
<body>

<div class="header">
  <h1>回测 · 实盘</h1>
  <div class="sub">10万元策略A模拟盘 · 窗口突破 2-9%</div>
  <div class="time">''' + now + '''</div>
</div>

<div class="wrap">
  <div class="stats" id="stats"></div>
  <div class="card"><h3>收益曲线</h3><div class="chart" id="c1"></div></div>
  <div class="grid2">
    <div class="card"><h3>策略对比</h3><div class="chart-sm" id="c2"></div></div>
    <div class="card"><h3>策略排名</h3><div class="chart-sm" id="c3"></div></div>
  </div>
  <div class="dark-sec">
    <h3>交易记录</h3>
    <div class="tbl-wrap"><table><thead><tr><th>日期</th><th>操作</th><th>代码</th><th>名称</th><th>价格</th><th>盈亏</th><th>盈亏%</th><th>说明</th></tr></thead><tbody id="tbody"></tbody></table></div>
  </div>
  <div class="card"><h3>当前持仓</h3><div class="position-grid" id="posGrid"></div></div>
</div>

<script>
var D = ''' + dj + ''';
var L = D.live, BT = D.backtest;

function render(){
  // stats
  var sh = '';
  var items = [
    ['总资产','¥'+L.capital.toLocaleString(), L.pnl>=0?'up':'down'],
    ['累计收益',(L.pnl>0?'+':'')+L.pnl+'%', L.pnl>=0?'up':'down'],
    ['交易数',L.trades+'笔',''],
    ['胜率',L.win_rate+'%', ''],
    ['均盈','+'+L.avg_win.toFixed(0),'up'],
    ['均亏',L.avg_loss.toFixed(0),'down']
  ];
  items.forEach(function(x){sh += '<div class="stat"><div class="val '+x[2]+'">'+x[1]+'</div><div class="lbl">'+x[0]+'</div></div>';});
  document.getElementById('stats').innerHTML = sh;

  // curve
  var c1 = echarts.init(document.getElementById('c1'));
  var s = [{n:'实盘',type:'line',data:L.values,smooth:!0,symbol:'none',lineStyle:{width:2,color:'#533afd'},areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'rgba(83,58,253,0.15)'},{offset:1,color:'rgba(83,58,253,0)'}]}},z:10}];
  var cs = ['#ea2261','#15be53','#f96bee'];
  BT.forEach(function(b,i){s.push({n:b.n,type:'line',data:b.v,smooth:!0,symbol:'none',lineStyle:{width:1.5,color:cs[i%3],type:'dashed'},z:5});});
  c1.setOption({tooltip:{trigger:'axis',valueFormatter:function(v){return'¥'+v.toFixed(2)}},legend:{data:['实盘'].concat(BT.map(function(b){return b.n})),textStyle:{color:'#64748b',fontSize:12},top:0},grid:{left:55,right:20,bottom:25,top:40},xAxis:{type:'category',data:L.dates,axisLabel:{color:'#64748b',fontSize:11}},yAxis:{type:'value',axisLabel:{color:'#64748b',formatter:'¥{value}'},splitLine:{lineStyle:{color:'#e5edf5'}}},series:s});

  // bar
  var c2 = echarts.init(document.getElementById('c2'));
  var a2 = [{n:'实盘',r:L.pnl,wr:L.win_rate,sh:0}].concat(BT);
  c2.setOption({tooltip:{trigger:'axis'},legend:{data:['收益%','胜率%'],textStyle:{color:'#64748b',fontSize:11}},grid:{left:40,right:10,bottom:35,top:35},xAxis:{type:'category',data:a2.map(function(x){return x.n}),axisLabel:{color:'#64748b',fontSize:10,rotate:15}},yAxis:{type:'value',axisLabel:{color:'#64748b'},splitLine:{lineStyle:{color:'#e5edf5'}}},series:[{n:'收益%',type:'bar',data:a2.map(function(x){return x.r}),itemStyle:{color:'#533afd',borderRadius:[4,4,0,0]}},{n:'胜率%',type:'bar',data:a2.map(function(x){return x.wr}),itemStyle:{color:'#b9b9f9',borderRadius:[4,4,0,0]}}]});

  // radar
  var c3 = echarts.init(document.getElementById('c3'));
  c3.setOption({radar:{indicator:[{n:'收益%',max:120},{n:'胜率%',max:60},{n:'夏普',max:3},{n:'交易',max:120},{n:'均盈',max:8}],axisName:{color:'#64748b',fontSize:10},splitArea:{areaStyle:{color:['rgba(83,58,253,0.02)','rgba(83,58,253,0.05)']}},axisLine:{lineStyle:{color:'#e5edf5'}}},series:[{type:'radar',data:a2.map(function(x,i){return{value:[x.r,x.wr,x.sh,x.tr||13,x.aw||1119],n:x.n,lineStyle:{color:cs[i%3]},areaStyle:{color:cs[i%3],opacity:0.08}}})}]});

  // trades
  var tb = '';
  D.trades.forEach(function(t){
    tb += '<tr><td>'+t.dt+'</td><td class="'+(t.ac=='买入'?'buy':'sell')+'">'+t.ac+'</td><td>'+t.c+'</td><td>'+t.n+'</td><td>¥'+t.p+'</td>';
    if(t.ac=='卖出'){tb += '<td class="'+(t.pn>=0?'pr':'ls')+'">'+(t.pn>0?'+':'')+t.pn+'</td><td class="'+(t.pp>=0?'pr':'ls')+'">'+(t.pp>0?'+':'')+t.pp+'</td>';}
    else{tb += '<td>-</td><td>-</td>';}
    tb += '<td>'+t.de+'</td></tr>';
  });
  document.getElementById('tbody').innerHTML = tb;

  // positions
  var pg = '';
  D.positions.forEach(function(p){
    pg += '<div class="pos"><div class="code">'+p.c+'</div><div class="name">'+p.n+'</div><div class="det">'+p.bd+' @ ¥'+p.bp+'<br>'+p.s+'股 · '+p.b+'</div></div>';
  });
  document.getElementById('posGrid').innerHTML = pg;

  window.addEventListener('resize',function(){c1.resize();c2.resize();c3.resize();});
}
render();
</script>
</body>
</html>'''

path = os.path.join(OUTPUT_DIR, "backtest_live_dashboard.html")
with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print(f"✅ Stripe风格看板: {path} ({os.path.getsize(path)} bytes)")
