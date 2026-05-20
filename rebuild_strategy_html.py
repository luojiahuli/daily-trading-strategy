#!/usr/bin/env python3
"""
重新生成完整的 strategy_compare.html
包含: ECharts图表 + 策略汇总 + 个股×策略交叉看板
所有数据通过JS变量传入，不依赖f-string花括号嵌套
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

# ============================================================
# 数据获取 + 回测
# ============================================================
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

# 回测所有策略
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

# ============================================================
# 构建JSON数据（统一转储，避免f-string花括号冲突）
# ============================================================
stock_json = json.dumps(stock_data, ensure_ascii=False, default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))

# 策略汇总统计
strat_summary = {}
for sk in STRATS:
    total_t = sum(s["s"][sk]["n"] for s in stock_data)
    total_p = sum(s["s"][sk]["p"] for s in stock_data)
    total_w = sum(s["s"][sk]["w"] for s in stock_data)
    strat_summary[sk] = {"t":total_t, "p":round(total_p,2), "w":total_w, "n":STRATS[sk]["n"], "d":STRATS[sk]["d"]}

strat_summary_json = json.dumps(strat_summary, ensure_ascii=False)

# 累计收益数据 + 分布数据
all_cum_data = {}
dist_data = {}
for sk in STRATS:
    cum = 0; pts = []; lbls = []
    dist = {"ge8":0,"ge5_lt8":0,"ge2_lt5":0,"ge0_lt2":0,"lt0_ge-3":0,"lt-3":0}
    for s in stock_data:
        for t in s["s"][sk]["t"]:
            cum += t["pct"]
            pts.append(round(cum,2))
            lbls.append(t["sd"])
            p = t["pct"]
            if p >= 8: dist["ge8"]+=1
            elif p >= 5: dist["ge5_lt8"]+=1
            elif p >= 2: dist["ge2_lt5"]+=1
            elif p >= 0: dist["ge0_lt2"]+=1
            elif p >= -3: dist["lt0_ge-3"]+=1
            else: dist["lt-3"]+=1
    all_cum_data[sk] = {"points":pts,"labels":lbls}
    dist_data[sk] = dist

all_cum_json = json.dumps(all_cum_data, ensure_ascii=False)
dist_json = json.dumps(dist_data, ensure_ascii=False)

# ============================================================
# 生成HTML（所有数据通过JS变量一次传入）
# ============================================================
HTML_CONTENT = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>多策略回测对比 + 个股操作看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:20px}
h1{color:#ffd700;text-align:center;font-size:24px}
.sub{color:#888;text-align:center;font-size:13px;margin-bottom:20px}
.dash{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:1400px;margin:0 auto}
.card{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e;margin-bottom:0}
.card h2{color:#ffd700;font-size:14px;margin-bottom:8px}
.full{grid-column:1/-1}
.chart{width:100%;height:320px}
.chart.tall{height:450px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#2a2a5e;color:#ffd700;padding:6px;text-align:left;position:sticky;top:0}
td{padding:5px 6px;border-bottom:1px solid #1a1a3e}
tr:hover{background:#1a1a4e}
.scroll{max-height:350px;overflow-y:auto}
.scroll-lg{max-height:600px;overflow-y:auto}
.tag{display:inline-block;padding:1px 5px;border-radius:3px;font-size:11px;font-weight:bold}
.win{background:#ff4d4f33;color:#ff4d4f;border:1px solid #ff4d4f66}
.lose{background:#52c41a33;color:#52c41a;border:1px solid #52c41a66}
.best{background:#ffd70022;color:#ffd700;border:1px solid #ffd70066}
.dc{background:#1a1a3e;border-radius:8px;margin-bottom:8px;padding:12px;border:1px solid #2a2a5e;cursor:pointer;transition:all 0.2s}
.dc:hover{border-color:#ffd70066;background:#1a1a4e}
.hdr{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px}
.sc{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}
.sc span{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px}
.sb{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:10px 0}
.sb-item{background:#1a1a4e;border-radius:6px;padding:10px;text-align:center}
</style>
</head>
<body>
<h1>多策略回测对比 + 个股操作看板</h1>
<p class="sub">4种策略 &times; 12只候选股 | 近1个月K线 | ''' + NOW + r'''</p>

<div class="dash">
  <div class="card full"><h2>策略总收益对比</h2><div id="barChart" class="chart"></div></div>
  <div class="card full"><h2>累计收益曲线对比</h2><div id="cumChart" class="chart tall"></div></div>
  <div class="card full"><h2>各策略收益分布</h2><div id="distChart" class="chart tall"></div></div>
  
  <div class="card full" style="margin-top:14px">
    <h2>个股 × 策略交叉明细看板</h2>
    <div id="stratSummaryRow" class="sb"></div>
    <p style="color:#888;font-size:12px;margin-bottom:8px">每只个股展示4种策略对比，黄色=该股最佳策略，点击展开买卖明细</p>
    <div id="stockCards" class="scroll-lg"></div>
  </div>
</div>

<script>
var S = ''' + stock_json + r''';
var SS = ''' + strat_summary_json + r''';
var CUMS = ''' + all_cum_json + r''';
var DIST = ''' + dist_json + r''';

var COLORS = {'A':'#ff4d4f','B':'#1890ff','C':'#52c41a','E':'#fa8c16'};
var SKEYS = Object.keys(SS);

// ========== 策略汇总 ==========
var sbHtml = '';
SKEYS.forEach(function(k){
  var d = SS[k];
  var c = COLORS[k];
  sbHtml += '<div class="sb-item" style="border:1px solid '+c+'66">';
  sbHtml += '<div style="font-size:11px;color:'+c+';font-weight:bold">'+d.n+'</div>';
  sbHtml += '<div style="font-size:20px;font-weight:bold;color:'+(d.p>0?'#ff4d4f':'#52c41a')+'">'+(d.p>0?'+':'')+d.p.toFixed(1)+'%</div>';
  sbHtml += '<div style="font-size:11px;color:#888">'+d.t+'笔 | 胜'+d.w+'</div>';
  sbHtml += '<div style="font-size:10px;color:#666">'+d.d+'</div></div>';
});
document.getElementById('stratSummaryRow').innerHTML = sbHtml;

// ========== ECharts: 策略收益对比 ==========
var barC = echarts.init(document.getElementById('barChart'));
var barNames = SKEYS.map(function(k){return SS[k].n});
var barVals = SKEYS.map(function(k){return SS[k].p});
barC.setOption({
  backgroundColor:'transparent', grid:{left:'8%',right:'5%',top:'10%',bottom:'15%'},
  xAxis:{type:'category',data:barNames,axisLabel:{fontSize:11,color:'#ccc',rotate:10},axisLine:{lineStyle:{color:'#333'}}},
  yAxis:{type:'value',name:'总收益%',nameTextStyle:{color:'#888'},splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888',formatter:function(v){return v+'%'}}},
  series:[{type:'bar',data:barVals.map(function(v){return{value:v,itemStyle:{color:v>5?'#ffd700':v>0?'#ff4d4f':'#52c41a'}}}),barWidth:'50%',
    label:{show:true,position:'top',fontSize:12,color:'#ffd700',fontWeight:'bold',formatter:function(p){return p.value.toFixed(1)+'%'+(p.value>5?' 🏆':'')}}}
  ]
});

// ========== ECharts: 累计收益曲线 ==========
var cumC = echarts.init(document.getElementById('cumChart'));
var allLabels = [];
SKEYS.forEach(function(k){ allLabels = allLabels.concat(CUMS[k].labels); });
allLabels = [...new Set(allLabels)].sort();
var cumSeries = SKEYS.map(function(k){
  var d = CUMS[k]; var cum=0; var lm={};
  for(var j=0;j<d.labels.length;j++){ cum=d.points[j]; lm[d.labels[j]]=cum; }
  var al = allLabels.map(function(l){ return lm[l]!==undefined?lm[l]:null; });
  var last=0;
  for(var j=0;j<al.length;j++){ if(al[j]===null)al[j]=last; else last=al[j]; }
  return {name:SS[k].n,type:'line',data:al,smooth:true,lineStyle:{width:2,color:COLORS[k]},symbol:'none',areaStyle:{opacity:0.05}};
});
cumC.setOption({
  backgroundColor:'transparent', tooltip:{trigger:'axis'},
  legend:{data:barNames,textStyle:{color:'#ccc'},top:0},
  grid:{left:'5%',right:'3%',top:'15%',bottom:'12%'},
  xAxis:{type:'category',data:allLabels,axisLabel:{rotate:45,fontSize:9,color:'#999',interval:2},axisLine:{lineStyle:{color:'#333'}}},
  yAxis:{type:'value',name:'累计%',nameTextStyle:{color:'#888'},splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888',formatter:function(v){return v+'%'}}},
  series:cumSeries,
});

// ========== ECharts: 收益分布 ==========
var distC = echarts.init(document.getElementById('distChart'));
var distCats = ['>=8%','5~8%','2~5%','0~2%','-3~0%','<-3%'];
var distKeys = ['ge8','ge5_lt8','ge2_lt5','ge0_lt2','lt0_ge-3','lt-3'];
var distSeries = SKEYS.map(function(k){
  var d = DIST[k];
  return {name:SS[k].n,type:'bar',stack:'total',data:distKeys.map(function(kk){return d[kk]||0}),itemStyle:{color:COLORS[k],opacity:0.7}};
});
distC.setOption({
  backgroundColor:'transparent', tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
  legend:{data:barNames,textStyle:{color:'#ccc'},top:0},
  grid:{left:'5%',right:'3%',top:'15%',bottom:'10%'},
  xAxis:{type:'category',data:distCats,axisLabel:{fontSize:11,color:'#ccc'},axisLine:{lineStyle:{color:'#333'}}},
  yAxis:{type:'value',splitLine:{lineStyle:{color:'#1a1a3e'}},axisLabel:{color:'#888'}},
  series:distSeries,
});

// ========== 个股×策略看板 ==========
var cardsHtml = '';
S.forEach(function(st, si){
  var strats = st.s || {};
  var sks = Object.keys(strats);
  var bestK = '', bestP = -9999;
  sks.forEach(function(k){ if(strats[k].p > bestP){ bestP=strats[k].p; bestK=k; } });
  
  cardsHtml += '<div class="dc" onclick="var e=document.getElementById(\'sd_'+si+'\');e.style.display=e.style.display===\\'none\\'?\\'block\\':\\'none\\'">';
  cardsHtml += '<div class="hdr"><div><b style="color:#ffd700;font-size:14px">#'+(si+1)+' '+st.name+'</b> <span style="color:#888;font-size:12px">'+st.code+'</span> <span style="color:#888;font-size:12px">| '+st.board+'</span></div>';
  cardsHtml += '<div><span style="color:#888">今日 </span><b style="color:'+(st.inc>0?'#ff4d4f':'#52c41a')+'">'+(st.inc>0?'+':'')+st.inc.toFixed(1)+'%</b> <span style="font-size:11px;color:#888">展开</span></div></div>';
  
  cardsHtml += '<div class="sc">';
  sks.forEach(function(k){
    var d = strats[k]; var ib = (k===bestK);
    cardsHtml += '<span style="background:'+(ib?'#ffd70022':'#111128')+';border:1px solid '+(ib?'#ffd70066':COLORS[k]+'44')+';color:'+(ib?'#ffd700':COLORS[k])+'">'+
      SS[k].n.substr(0,1)+': '+(d.p>0?'+':'')+d.p.toFixed(1)+'% ('+d.w+'/'+d.n+')'+(ib?' 🏆':'')+'</span>';
  });
  cardsHtml += '</div>';
  
  cardsHtml += '<div id="sd_'+si+'" style="display:none;margin-top:6px">';
  sks.forEach(function(k){
    var d = strats[k]; var trades = d.t || []; var ib = (k===bestK);
    cardsHtml += '<div style="background:'+(ib?'#ffd70008':'#111128')+';border-radius:6px;padding:10px;margin-bottom:6px;border:1px solid '+(ib?'#ffd70044':COLORS[k]+'33')+'">';
    cardsHtml += '<div style="font-size:13px;color:'+(ib?'#ffd700':COLORS[k])+';font-weight:bold;margin-bottom:4px">['+SS[k].n+'] '+d.desc+(ib?' 🏆':'')+' | 收益 <b style="color:'+(d.p>0?'#ff4d4f':'#52c41a')+'">'+(d.p>0?'+':'')+d.p.toFixed(1)+'%</b> 胜率 <b style="color:#1890ff">'+d.wr+'%</b> ('+d.w+'/'+d.n+')';
    cardsHtml += '</div>';
    if(trades.length > 0){
      cardsHtml += '<table><tr><th>#</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th><th>收益</th><th>持仓</th><th>原因</th></tr>';
      trades.forEach(function(t,ti){
        cardsHtml += '<tr><td>'+(ti+1)+'</td><td>'+t.bd+'</td><td>'+t.bp.toFixed(2)+'</td><td>'+t.sd+'</td><td>'+t.sp.toFixed(2)+'</td>';
        cardsHtml += '<td><span class="tag '+(t.pct>0?'win':'lose')+'">'+(t.pct>0?'+':'')+t.pct.toFixed(2)+'%</span></td>';
        cardsHtml += '<td>'+t.hd+'天</td><td style="color:#888;font-size:11px">'+t.r+'</td></tr>';
      });
      cardsHtml += '</table>';
    } else {
      cardsHtml += '<p style="color:#888;font-size:12px;padding:4px 0">无交易信号</p>';
    }
    cardsHtml += '</div>';
  });
  cardsHtml += '</div></div>';
});
document.getElementById('stockCards').innerHTML = cardsHtml;

window.onresize = function(){ barC.resize(); cumC.resize(); distC.resize(); };
</script>
</body>
</html>'''

# 写文件
PATH = os.path.join(OUTPUT_DIR, "strategy_compare.html")
with open(PATH, "w", encoding="utf-8") as f:
    f.write(HTML_CONTENT)

print(f"\n[+] 已生成: {PATH} ({os.path.getsize(PATH)} bytes)")

import webbrowser
webbrowser.open(f"file://{os.path.abspath(PATH)}")
print("[+] 已在浏览器中打开")
