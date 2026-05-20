#!/usr/bin/env python3
"""
生成 strategy_compare.html + data.json
HTML页面独立加载数据，彻底避免花括号嵌套问题
"""

import os, sys, json, time, re as re2
from datetime import datetime

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests as req

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

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

def run_strategy(kl, check_fn, sl, tp, mh=5):
    trades, positions, tdates = [], [], set()
    for i in range(1, len(kl)):
        today = kl[i]
        positions = [p for p in positions if i - p["bi"] < mh]
        for p in positions[:]:
            hd = i - p["bi"]
            hp = (today["high"] - p["bp"]) / p["bp"] * 100
            lp = (today["low"] - p["bp"]) / p["bp"] * 100
            if hd >= mh:
                pct = round((today["close"] - p["bp"]) / p["bp"] * 100, 2)
                trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],"sp":round(today["close"],2),"pct":pct,"r":"到期卖出","hd":hd})
                positions.remove(p)
            elif hp >= tp:
                sp = round(p["bp"] * (1 + tp/100), 2)
                trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],"sp":sp,"pct":tp,"r":f"止盈+{tp:.0f}%","hd":hd})
                positions.remove(p)
            elif lp <= sl:
                sp = round(p["bp"] * (1 + sl/100), 2)
                trades.append({"bd":p["bd"],"bp":round(p["bp"],2),"sd":today["date"],"sp":sp,"pct":sl,"r":f"止损{sl:.0f}%","hd":hd})
                positions.remove(p)
        prev = kl[i-1]
        bp = check_fn(kl, i)
        if bp is not None and len(positions) < 3 and today["date"] not in tdates:
            tdates.add(today["date"])
            positions.append({"bd":today["date"],"bp":bp,"bi":i})
    return trades

STRATS = {
    "A": {"n":"A:基础窗口期","d":"前日涨2~9%买入,止损-3%/止盈+5%","sl":-3.0,"tp":5.0,
          "fn":lambda kl,i: kl[i]["open"] if 2.0 <= kl[i-1]["pct"] <= 9.0 else None},
    "B": {"n":"B:强势过滤","d":"前日涨4~9%买入,止损-5%/止盈+8%","sl":-5.0,"tp":8.0,
          "fn":lambda kl,i: kl[i]["open"] if 4.0 <= kl[i-1]["pct"] <= 9.0 else None},
    "C": {"n":"C:趋势确认","d":"连续2日涨>2%买入,止损-4%/止盈+6%","sl":-4.0,"tp":6.0,
          "fn":lambda kl,i: kl[i]["open"] if i>=2 and kl[i-2]["pct"]>2.0 and kl[i-1]["pct"]>2.0 else None},
    "E": {"n":"E:最优组合","d":"前日涨4~9%买入,止损-4%/止盈+8%","sl":-4.0,"tp":8.0,
          "fn":lambda kl,i: kl[i]["open"] if 4.0 <= kl[i-1]["pct"] <= 9.0 else None},
}

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
        code = s.get("代码",""); sname = s.get("名称","")
        inc = safe_float(s.get("涨幅",0))
        if sname.startswith(("ST","*ST","退")): continue
        if 2.0 <= inc <= 15.0:
            if code not in seen or inc > seen[code]["inc"]:
                seen[code] = {"code":code,"name":sname,"board":name,"inc":inc,"price":safe_float(s.get("现价",0))}
    time.sleep(0.08)

candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)[:12]
print(f"[+] {len(candidates)} 只候选股")

stock_data = []
for c in candidates:
    print(f"\n  {c['code']} {c['name']} inc={c['inc']:+.1f}%")
    kl = get_kline(c["code"])
    if not kl or len(kl) < 30:
        print("    ❌ 数据不足")
        continue
    strats_out = {}
    for sk, sv in STRATS.items():
        trades = run_strategy(kl, sv["fn"], sv["sl"], sv["tp"])
        if trades:
            tp = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            strats_out[sk] = {"t":trades,"n":len(trades),"p":round(tp,2),"w":wins,
                              "wr":round(wins/len(trades)*100,1),"ap":round(tp/len(trades),2),"desc":sv["d"]}
            print(f"    {sv['n']}: {len(trades)}笔 总{tp:+.2f}% 胜{wins}/{len(trades)}")
        else:
            strats_out[sk] = {"t":[],"n":0,"p":0,"w":0,"wr":0,"ap":0,"desc":sv["d"]}
            print(f"    {sv['n']}: 无信号")
    stock_data.append({**c, "s":strats_out})
    time.sleep(0.3)

# 策略汇总
strat_summary = {}
all_cum_data = {}
dist_data = {}
for sk in STRATS:
    total_t = sum(s["s"][sk]["n"] for s in stock_data)
    total_p = sum(s["s"][sk]["p"] for s in stock_data)
    total_w = sum(s["s"][sk]["w"] for s in stock_data)
    strat_summary[sk] = {"t":total_t, "p":round(total_p,2), "w":total_w, "n":STRATS[sk]["n"], "d":STRATS[sk]["d"]}
    
    cum = 0; pts = []; lbls = []
    dist = {"ge8":0,"ge5_lt8":0,"ge2_lt5":0,"ge0_lt2":0,"lt0_ge-3":0,"lt-3":0}
    for s in stock_data:
        for t in s["s"][sk]["t"]:
            cum += t["pct"]; pts.append(round(cum,2)); lbls.append(t["sd"])
            p = t["pct"]
            if p >= 8: dist["ge8"]+=1
            elif p >= 5: dist["ge5_lt8"]+=1
            elif p >= 2: dist["ge2_lt5"]+=1
            elif p >= 0: dist["ge0_lt2"]+=1
            elif p >= -3: dist["lt0_ge-3"]+=1
            else: dist["lt-3"]+=1
    all_cum_data[sk] = {"points":pts,"labels":lbls}
    dist_data[sk] = dist

# ========== 写入JSON数据文件 ==========
data = {
    "stockData": stock_data,
    "stratSummary": strat_summary,
    "allCum": all_cum_data,
    "dist": dist_data,
    "now": NOW,
    "stratKeys": list(STRATS.keys()),
    "stratNames": {k: STRATS[k]["n"] for k in STRATS},
    "stratColors": {"A":"#ff4d4f","B":"#1890ff","C":"#52c41a","E":"#fa8c16"},
}
data_path = os.path.join(OUTPUT_DIR, "strategy_data.json")
with open(data_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))
print(f"\n[+] 数据文件: {data_path}")

# ========== 生成纯HTML（无任何Python变量插值，纯字符串拼接） ==========
html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>多策略回测 + 产业链图谱 + 个股看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>window.onEChartsLoad = function(){};
if(typeof echarts === 'undefined') {
  var s = document.createElement('script');
  s.src = 'https://unpkg.com/echarts@5/dist/echarts.min.js';
  s.onload = function(){ window.onEChartsLoad(); };
  document.head.appendChild(s);
} else { window.onEChartsLoad(); }
</script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:20px}
h1{color:#ffd700;text-align:center;font-size:22px;margin-bottom:4px}
.sub{color:#888;text-align:center;font-size:12px;margin-bottom:16px}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:1400px;margin:0 auto}
.sec{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e}
.sec h2{color:#ffd700;font-size:14px;margin-bottom:8px}
.fw{grid-column:1/-1}
.chart{width:100%;height:320px}
.chart-m{height:400px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#2a2a5e;color:#ffd700;padding:6px;text-align:left;position:sticky;top:0}
td{padding:5px 6px;border-bottom:1px solid #1a1a3e}
tr:hover{background:#1a1a4e}
.tg{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:bold}
.tw{background:#ff4d4f33;color:#ff4d4f;border:1px solid #ff4d4f66}
.tl{background:#52c41a33;color:#52c41a;border:1px solid #52c41a66}
.scr{max-height:600px;overflow-y:auto}
.sb{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}
.si{background:#1a1a4e;border-radius:6px;padding:10px;text-align:center;border:1px solid #2a2a5e}
.dc{background:#1a1a3e;border-radius:8px;margin-bottom:8px;padding:12px;border:1px solid #2a2a5e;cursor:pointer;transition:all 0.15s}
.dc:hover{border-color:#ffd70055;background:#1a1a4e}
.hd{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px}
.sc{display:flex;gap:5px;flex-wrap:wrap;margin:5px 0}
.stb{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:bold}
</style>
</head>
<body>
<h1>&#x1F4CA; 多策略回测对比 + 个股操作看板</h1>
<p class="sub" id="subtitle">加载中...</p>

<div class="wrap">

  <div class="sec fw"><h2>&#x1F3C6; 策略总收益对比</h2><div id="barChart" class="chart"></div></div>
  <div class="sec fw"><h2>&#x1F4C8; 累计收益曲线对比</h2><div id="cumChart" class="chart chart-m"></div></div>
  <div class="sec fw"><h2>&#x1F4CA; 各策略收益分布</h2><div id="distChart" class="chart chart-m"></div></div>

  <div class="sec fw" style="margin-top:6px">
    <h2>&#x1F4CB; 个股 &#x00D7; 策略交叉明细</h2>
    <div id="sbRow" class="sb"></div>
    <p style="color:#888;font-size:12px;margin-bottom:6px">每只个股展示4种策略对比，黄色=最佳策略，点击展开买卖记录</p>
    <div id="stockCards" class="scr"></div>
  </div>

  <!-- 产业链关系图谱 -->
  <div class="sec fw" style="margin-top:6px">
    <h2>产业链关系图谱</h2>
    <p style="color:#888;font-size:12px;margin-bottom:6px">
      三层结构：产业链簇(菱形) → 题材板块(圆角矩形) → 个股(圆点)
      <a href="industry_graph.html" target="_blank" style="color:#1890ff;margin-left:12px;font-weight:bold">全屏打开 ↗</a>
    </p>
    <div id="industryGraphContainer" style="width:100%;height:500px;background:#111128;border-radius:8px"></div>
  </div>

</div>

<script>
fetch('strategy_data.json?_t='+Date.now())
  .then(function(r){ return r.json(); })
  .then(function(D){
    document.getElementById('subtitle').textContent = D.now + ' | 4种策略 × ' + D.stockData.length + '只候选股';

    // ===== 加载产业链图谱 (内嵌) =====
    (function(){
      var xhr = new XMLHttpRequest();
      xhr.open('GET', 'industry_graph.html?_t='+Date.now(), true);
      xhr.onload = function(){
        var m = xhr.responseText.match(/var graphData = ({.*?});/);
        if(!m) return;
        try {
          var gd = JSON.parse(m[1]);
          var gc = echarts.init(document.getElementById('industryGraphContainer'));
          gc.setOption({
            backgroundColor:'transparent',
            tooltip:{formatter:function(p){
              if(p.dataType==='node'){
                var d=p.data;
                if(d.category===0) return '<b style=\"color:#ff6b6b\">产业链: '+d.name+'</b><br/>板块数: '+d.cluster_size;
                if(d.category===1) return '<b style=\"color:#fa8c16\">板块: '+d.name+'</b><br/>热度: '+d.score;
                return '<b style=\"color:#91cc75\">'+d.shortName+'</b><br/>涨幅: '+(d.inc>0?'+':'')+d.inc.toFixed(2)+'%'+(d.price?' | 现价: '+d.price.toFixed(2):'');
              } return '';
            }},
            series:[{type:'graph',layout:'force',
              force:{repulsion:800,edgeLength:[60,250],gravity:0.04,friction:0.1},
              roam:true,draggable:true,
              data:gd.nodes.map(function(n){
                if(n.category===0) return Object.assign({},n,{symbol:'diamond',symbolSize:Math.max(35,n.symbolSize),label:{show:true,position:'bottom',fontSize:9,color:'#ffd700',fontWeight:'bold'}});
                if(n.category===1) return Object.assign({},n,{symbol:'roundRect',symbolSize:Math.max(20,n.symbolSize),label:{show:true,position:'right',fontSize:8,color:'#ccc'}});
                return Object.assign({},n,{symbol:'circle',label:{show:false}});
              }),
              edges:gd.edges.map(function(e){return Object.assign({},e,{lineStyle:{width:Math.max(0.5,Math.min(3,e.value||1)),opacity:0.25,curveness:0.2}});}),
              categories:gd.categories,
              lineStyle:{color:'#2a2a5e',opacity:0.3},
              emphasis:{focus:'adjacency',lineStyle:{width:3,opacity:0.8}},
              zoom:0.4,
            }]
          });
          window.addEventListener('resize',function(){gc.resize()});
        } catch(e){}
      };
      xhr.send();
    })();

    var SKS = D.stratKeys;
    var COL = D.stratColors;
    var COL_A = [];

    // ===== 策略汇总 =====
    var sbh = '';
    SKS.forEach(function(k){
      var d = D.stratSummary[k];
      sbh += '<div class="si" style="border-color:'+COL[k]+'88">';
      sbh += '<div style="font-size:11px;color:'+COL[k]+';font-weight:bold">'+d.n+'</div>';
      sbh += '<div style="font-size:20px;font-weight:bold;color:'+(d.p>0?'#ff4d4f':'#52c41a')+'">'+(d.p>0?'+':'')+d.p.toFixed(1)+'%</div>';
      sbh += '<div style="font-size:11px;color:#888">'+d.t+'笔 | 胜'+d.w+'</div>';
      sbh += '<div style="font-size:10px;color:#666">'+d.d+'</div></div>';
    });
    document.getElementById('sbRow').innerHTML = sbh;

    // ===== ECharts: 策略收益对比 =====
    var bc = echarts.init(document.getElementById('barChart'));
    var bNames = SKS.map(function(k){return D.stratSummary[k].n});
    var bVals = SKS.map(function(k){return D.stratSummary[k].p});
    COL_A = SKS.map(function(k){return COL[k]});
    bc.setOption({
      backgroundColor:'transparent',
      grid:{left:'8%',right:'5%',top:'10%',bottom:'15%'},
      xAxis:{type:'category',data:bNames,axisLabel:{fontSize:11,color:'#ccc',rotate:10},axisLine:{lineStyle:{color:'#333'}}},
      yAxis:{type:'value',name:'总收益%',nameTextStyle:{color:'#888'},splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888',formatter:function(v){return v+'%'}}},
      series:[{type:'bar',data:bVals.map(function(v,i){return{value:v,itemStyle:{color:v>5?'#ffd700':v>0?'#ff4d4f':'#52c41a'}}}),barWidth:'50%',
        label:{show:true,position:'top',fontSize:12,color:'#ffd700',fontWeight:'bold',formatter:function(p){return p.value.toFixed(1)+'%'+(p.value>5?' \uD83C\uDFC6':'')}}
      }]
    });

    // ===== ECharts: 累计收益曲线 =====
    var cc = echarts.init(document.getElementById('cumChart'));
    var allLabels = [];
    SKS.forEach(function(k){ allLabels = allLabels.concat(D.allCum[k].labels); });
    allLabels = [...new Set(allLabels)].sort();
    var cumSeries = SKS.map(function(k){
      var d = D.allCum[k]; var cum=0; var lm={};
      for(var j=0;j<d.labels.length;j++){ cum=d.points[j]; lm[d.labels[j]]=cum; }
      var al = allLabels.map(function(l){ return lm[l]!==undefined?lm[l]:null; });
      var last=0;
      for(var j=0;j<al.length;j++){ if(al[j]===null)al[j]=last; else last=al[j]; }
      return {name:D.stratSummary[k].n,type:'line',data:al,smooth:true,lineStyle:{width:2,color:COL[k]},symbol:'none',areaStyle:{opacity:0.05}};
    });
    cc.setOption({
      backgroundColor:'transparent',tooltip:{trigger:'axis'},
      legend:{data:bNames,textStyle:{color:'#ccc'},top:0},
      grid:{left:'5%',right:'3%',top:'15%',bottom:'12%'},
      xAxis:{type:'category',data:allLabels,axisLabel:{rotate:45,fontSize:9,color:'#999',interval:2},axisLine:{lineStyle:{color:'#333'}}},
      yAxis:{type:'value',name:'累计%',nameTextStyle:{color:'#888'},splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888',formatter:function(v){return v+'%'}}},
      series:cumSeries,
    });

    // ===== ECharts: 收益分布 =====
    var dc = echarts.init(document.getElementById('distChart'));
    var dCats = ['\u22658%','5~8%','2~5%','0~2%','-3~0%','<-3%'];
    var dKeys = ['ge8','ge5_lt8','ge2_lt5','ge0_lt2','lt0_ge-3','lt-3'];
    var dSeries = SKS.map(function(k){
      var d = D.dist[k];
      return {name:D.stratSummary[k].n,type:'bar',stack:'total',data:dKeys.map(function(kk){return d[kk]||0}),itemStyle:{color:COL[k],opacity:0.7}};
    });
    dc.setOption({
      backgroundColor:'transparent',tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
      legend:{data:bNames,textStyle:{color:'#ccc'},top:0},
      grid:{left:'5%',right:'3%',top:'15%',bottom:'10%'},
      xAxis:{type:'category',data:dCats,axisLabel:{fontSize:11,color:'#ccc'},axisLine:{lineStyle:{color:'#333'}}},
      yAxis:{type:'value',splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888'}},
      series:dSeries,
    });

    // ===== 个股×策略看板 =====
    var ch = '';
    D.stockData.forEach(function(st, si){
      var sks = Object.keys(st.s);
      var bestK = '', bestP = -9999;
      sks.forEach(function(k){ if(st.s[k].p > bestP){ bestP=st.s[k].p; bestK=k; } });
      ch += '<div class="dc" onclick="var e=document.getElementById(\'sd_'+si+'\');e.style.display=e.style.display==\'none\'?\'block\':\'none\'">';
      ch += '<div class="hd"><div><b style="color:#ffd700;font-size:14px">#'+(si+1)+' '+st.name+'</b> <span style="color:#888;font-size:12px">'+st.code+'</span> <span style="color:#888;font-size:12px">| '+st.board+'</span></div>';
      ch += '<div><span style="color:#888">今日 </span><b style="color:'+(st.inc>0?'#ff4d4f':'#52c41a')+'">'+(st.inc>0?'+':'')+st.inc.toFixed(1)+'%</b> <span style="font-size:11px;color:#888;margin-left:4px">\u25BC</span></div></div>';
      ch += '<div class="sc">';
      sks.forEach(function(k){
        var d = st.s[k]; var ib = (k===bestK);
        ch += '<span class="stb" style="background:'+(ib?'#ffd70022':'#111128')+';border:1px solid '+(ib?'#ffd70066':COL[k]+'44')+';color:'+(ib?'#ffd700':COL[k])+'">'+
          D.stratNames[k].charAt(0)+': '+(d.p>0?'+':'')+d.p.toFixed(1)+'% ('+d.w+'/'+d.n+')'+(ib?' \uD83C\uDFC6':'')+'</span>';
      });
      ch += '</div>';
      ch += '<div id="sd_'+si+'" style="display:none;margin-top:6px">';
      sks.forEach(function(k){
        var d = st.s[k]; var trades = d.t||[]; var ib = (k===bestK);
        ch += '<div style="background:'+(ib?'#ffd70008':'#111128')+';border-radius:6px;padding:10px;margin-bottom:6px;border:1px solid '+(ib?'#ffd70044':COL[k]+'33')+'">';
        ch += '<div style="font-size:13px;color:'+(ib?'#ffd700':COL[k])+';font-weight:bold;margin-bottom:4px">['+D.stratNames[k]+'] '+d.desc+(ib?' \uD83C\uDFC6':'')+' | 收益 <b style="color:'+(d.p>0?'#ff4d4f':'#52c41a')+'">'+(d.p>0?'+':'')+d.p.toFixed(1)+'%</b> 胜率 <b style="color:#1890ff">'+d.wr+'%</b> ('+d.w+'/'+d.n+')</div>';
        if(trades.length>0){
          ch += '<table><tr><th>#</th><th>买入</th><th>买入价</th><th>卖出</th><th>卖出价</th><th>收益</th><th>持仓</th><th>原因</th></tr>';
          trades.forEach(function(t,ti){
            ch += '<tr><td>'+(ti+1)+'</td><td>'+t.bd+'</td><td>'+t.bp.toFixed(2)+'</td><td>'+t.sd+'</td><td>'+t.sp.toFixed(2)+'</td>';
            ch += '<td><span class="tg '+(t.pct>0?'tw':'tl')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
            ch += '<td>'+t.hd+'天</td><td style="color:#888;font-size:11px">'+t.r+'</td></tr>';
          });
          ch += '</table>';
        }else{
          ch += '<p style="color:#888;font-size:12px;padding:4px 0">无交易信号</p>';
        }
        ch += '</div>';
      });
      ch += '</div></div>';
    });
    document.getElementById('stockCards').innerHTML = ch;

    window.onresize = function(){ bc.resize(); cc.resize(); dc.resize(); };
  })
  .catch(function(e){
    document.getElementById('subtitle').textContent = '数据加载失败: '+e.message;
  });
</script>
</body>
</html>"""

html_path = os.path.join(OUTPUT_DIR, "strategy_compare.html")
with open(html_path, "w", encoding="utf-8", errors="surrogateescape") as f:
    f.write(html)
print(f"[+] HTML: {html_path}")

import webbrowser
webbrowser.open(f"file://{os.path.abspath(html_path)}")
print("[+] 已在浏览器中打开")
