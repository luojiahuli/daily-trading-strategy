#!/usr/bin/env python3
"""
多策略回测对比系统 - 5种窗口期策略对比

策略:
  A: 基础窗口期 - 前日涨2~9%买入, 止损-3%/止盈+5%/持仓5日 (原策略)
  B: 强势过滤  - 前日涨4~9%买入, 止损-5%/止盈+8%/持仓5日
  C: 趋势确认  - 连续2日涨>2%才买入, 止损-4%/止盈+6%/持仓5日
  D: 板块精选  - 只买窗口期板块内的个股(涨2~9%), 止损-3%/止盈+5%/持仓5日
  E: 最优组合  - 前日涨4~9% + 板块窗口期, 止损-4%/止盈+8%/持仓5日
"""

import os, sys, time, json, re, math
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_constituents, safe_float, CACHE_DIR
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

MAX_POSITIONS = 3
MIN_KLINES = 30


# ============================================================
# 数据获取
# ============================================================

def get_kline(code: str, days: int = 50) -> Optional[list]:
    """新浪API获取K线"""
    try:
        code = str(code).strip()
        if code.startswith(("6", "9")):
            symbol = f"sh{code}"
        elif code.startswith(("0", "3")):
            symbol = f"sz{code}"
        else:
            symbol = f"sh{code}"

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
                "date": d["day"],
                "open": float(d["open"]),
                "close": float(d["close"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
            })
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else:
                records[-1]["pct"] = 0.0
        return records
    except:
        return None


# ============================================================
# 策略引擎
# ============================================================

class Strategy:
    """策略基类"""
    def __init__(self, name: str, desc: str):
        self.name = name
        self.desc = desc
        self.trades = []

    def check_buy(self, kline: list, idx: int) -> Optional[float]:
        return None

    def get_stop_loss(self) -> float:
        return -3.0

    def get_take_profit(self) -> float:
        return 5.0

    def get_max_hold(self) -> int:
        return 5

    def run(self, kline: list, code: str, name: str, board: str = "") -> list:
        self.trades = []
        positions = []
        code_trade_dates = set()

        for i in range(1, len(kline)):
            today = kline[i]
            # 清理过期
            positions = [p for p in positions if i - p["buy_idx"] < self.get_max_hold()]

            # 卖出检查
            for p in positions[:]:
                hold = i - p["buy_idx"]
                if hold >= self.get_max_hold():
                    self.trades.append(self._make_trade(code, name, board,
                        p["buy_date"], p["buy_price"], today["date"], today["close"],
                        "到期"))
                    positions.remove(p)
                    continue

                high_pct = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
                low_pct = (today["low"] - p["buy_price"]) / p["buy_price"] * 100

                if high_pct >= self.get_take_profit():
                    sp = round(p["buy_price"] * (1 + self.get_take_profit() / 100), 2)
                    self.trades.append(self._make_trade(code, name, board,
                        p["buy_date"], p["buy_price"], today["date"], sp,
                        f"止盈+{self.get_take_profit():.0f}%"))
                    positions.remove(p)
                elif low_pct <= self.get_stop_loss():
                    sp = round(p["buy_price"] * (1 + self.get_stop_loss() / 100), 2)
                    self.trades.append(self._make_trade(code, name, board,
                        p["buy_date"], p["buy_price"], today["date"], sp,
                        f"止损{self.get_stop_loss():.0f}%"))
                    positions.remove(p)

            # 买入检查
            if len(positions) < MAX_POSITIONS:
                buy_price = self.check_buy(kline, i)
                if buy_price is not None and today["date"] not in code_trade_dates:
                    code_trade_dates.add(today["date"])
                    positions.append({
                        "buy_date": today["date"],
                        "buy_price": buy_price,
                        "buy_idx": i,
                    })

        return self.trades

    def _make_trade(self, code, name, board, bd, bp, sd, sp, reason):
        pct = round((sp - bp) / bp * 100, 2)
        return {
            "code": code, "name": name, "board": board,
            "buy_date": bd, "buy_price": round(bp, 2),
            "sell_date": sd, "sell_price": round(sp, 2),
            "pct": pct, "reason": reason,
        }


# 策略A: 原策略 - 前日涨2~9%, 止损-3%/止盈+5%
class StrategyA(Strategy):
    def __init__(self):
        super().__init__("A: 基础窗口期", "前日涨2~9%, 止损-3%/止盈+5%")
    def check_buy(self, kline, i):
        prev = kline[i-1]
        if 2.0 <= prev["pct"] <= 9.0:
            return kline[i]["open"]
        return None


# 策略B: 强势过滤 - 门槛提高, 扩大止盈止损比
class StrategyB(Strategy):
    def __init__(self):
        super().__init__("B: 强势过滤", "前日涨4~9%, 止损-5%/止盈+8%")
    def check_buy(self, kline, i):
        prev = kline[i-1]
        if 4.0 <= prev["pct"] <= 9.0:
            return kline[i]["open"]
        return None
    def get_stop_loss(self): return -5.0
    def get_take_profit(self): return 8.0


# 策略C: 趋势确认 - 连续2日上涨才买入
class StrategyC(Strategy):
    def __init__(self):
        super().__init__("C: 趋势确认", "连续2日涨>2%, 止损-4%/止盈+6%")
    def check_buy(self, kline, i):
        if i < 2: return None
        d1, d2 = kline[i-2], kline[i-1]
        if d1["pct"] > 2.0 and d2["pct"] > 2.0:
            return kline[i]["open"]
        return None
    def get_stop_loss(self): return -4.0
    def get_take_profit(self): return 6.0


# 策略D: 板块精选 - 只买窗口期板块内的个股
class StrategyD(Strategy):
    def __init__(self, board_picks: set):
        board_pairs = {}
        for item in board_picks:
            parts = item.split("|")
            if len(parts) >= 2:
                board_pairs[parts[0]] = parts[1]  # code -> board_name
        
        self.board_map = board_pairs
        super().__init__("D: 板块精选", f"窗口期板块个股, 止损-3%/止盈+5%")
        
    def check_buy(self, kline, i):
        prev = kline[i-1]
        if 2.0 <= prev["pct"] <= 9.0:
            return kline[i]["open"]
        return None


# 策略E: 最优组合 - 强势+板块+大止盈止损比
class StrategyE(Strategy):
    def __init__(self, board_picks: set = None):
        super().__init__("E: 最优组合", "前日涨4~9%+板块确认, 止损-4%/止盈+8%")
        self.board_codes = set()
        if board_picks:
            for item in board_picks:
                self.board_codes.add(item.split("|")[0])
                
    def check_buy(self, kline, i):
        prev = kline[i-1]
        if 4.0 <= prev["pct"] <= 9.0:
            return kline[i]["open"]
        return None
    def get_stop_loss(self): return -4.0
    def get_take_profit(self): return 8.0


# ============================================================
# 获取候选股（带板块信息）
# ============================================================

def get_candidates() -> list:
    """获取候选股列表（含板块信息）"""
    boards = get_all_concept_boards(use_cache=True)
    seen = {}
    
    for board in boards[:60]:
        name = board["name"]
        cons = get_board_constituents(name)
        if not cons:
            time.sleep(0.1)
            continue
        for s in cons[:8]:
            code = s.get("代码", "")
            sname = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))
            if sname.startswith(("ST", "*ST", "退")): 
                continue
            if 0 <= inc <= 15:
                if code not in seen:
                    seen[code] = {"code": code, "name": sname, "board": name, "inc": inc}
                elif inc > seen[code]["inc"]:
                    seen[code]["board"] = name
                    seen[code]["inc"] = inc
        time.sleep(0.1)
    
    candidates = list(seen.values())
    candidates.sort(key=lambda x: x["inc"], reverse=True)
    return candidates


# ============================================================
# 统计
# ============================================================

def analyze(trades: list) -> dict:
    if not trades:
        return {"trades": [], "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_pnl": 0, "total_pnl": 0, "max_drawdown": 0, "profit_factor": 0}
    wins = sum(1 for t in trades if t["pct"] > 0)
    losses = len(trades) - wins
    total_pnl = sum(t["pct"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    win_rate = wins / len(trades) * 100
    
    gross_profit = sum(t["pct"] for t in trades if t["pct"] > 0)
    gross_loss = abs(sum(t["pct"] for t in trades if t["pct"] <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    max_dd = 0; peak = 0; cum = 0
    for t in trades:
        cum += t["pct"]
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    
    trades.sort(key=lambda x: x["sell_date"])
    return {
        "trades": trades, "total": len(trades),
        "wins": wins, "losses": losses,
        "win_rate": round(win_rate, 1), "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(total_pnl, 2), "max_drawdown": round(max_dd, 2),
        "profit_factor": round(profit_factor, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


# ============================================================
# 主流程
# ============================================================

def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    多策略窗口期回测对比系统              ║
  ║    5种策略同时回测，选最优               ║
  ╚══════════════════════════════════════════╝
    """)

    strategies = [
        StrategyA(),
        StrategyB(),
        StrategyC(),
        StrategyE(),  # E不依赖板块数据，先跑
    ]

    # 获取候选股
    print("[*] 获取候选股...")
    candidates = get_candidates()
    print(f"[+] 共 {len(candidates)} 只候选股")

    # 运行所有策略
    all_results = {}
    for strat in strategies:
        print(f"\n{'='*60}")
        print(f"[*] 策略 {strat.name}: {strat.desc}")
        print(f"{'='*60}")
        
        all_trades = []
        for idx, c in enumerate(candidates[:12]):  # 取前12只
            print(f"  [{idx+1}/12] {c['code']} {c['name']} ({c['board']})...", end=" ", flush=True)
            kline = get_kline(c["code"])
            if not kline or len(kline) < MIN_KLINES:
                print("数据不足")
                continue
            trades = strat.run(kline, c["code"], c["name"], c["board"])
            if trades:
                all_trades.extend(trades)
                print(f"{len(trades)}笔")
            else:
                print("无信号")
            time.sleep(0.3)
        
        result = analyze(all_trades)
        all_results[strat.name] = result
        
        if result["total"] > 0:
            print(f"\n  >>> 总交易:{result['total']} 胜率:{result['win_rate']}% "
                  f"总收益:{result['total_pnl']:+.2f}% 平均:{result['avg_pnl']:+.2f}% "
                  f"盈亏比:{result['profit_factor']} 最大回撤:{result['max_drawdown']:.2f}%")

    # ========== 输出对比 ==========
    print(f"\n\n{'='*70}")
    print(f"  策略对比总表")
    print(f"{'='*70}")
    print(f"  {'策略':<22} {'交易':>5} {'胜率':>7} {'总收益':>9} {'平均':>8} {'盈亏比':>8} {'回撤':>8}")
    print(f"  {'-'*68}")
    best_strat = None
    best_pnl = -999
    for name, r in all_results.items():
        pnl = r["total_pnl"]
        if pnl > best_pnl:
            best_pnl = pnl
            best_strat = name
        tag = " 🏆" if pnl > 5 else ""
        print(f"  {name:<22} {r['total']:>5} {r['win_rate']:>6.1f}% "
              f"{r['total_pnl']:>+8.2f}% {r['avg_pnl']:>+7.2f}% "
              f"{r['profit_factor']:>7.2f} {r['max_drawdown']:>7.2f}%{tag}")
    print(f"{'='*70}")
    
    if best_strat:
        print(f"\n  🏆 最优策略: {best_strat} (总收益 {all_results[best_strat]['total_pnl']:+.2f}%)")

    # ========== 生成可视化对比HTML ==========
    gen_html(all_results)
    
    # 打印最优策略的明细
    if best_strat and all_results[best_strat]["total"] > 0:
        print(f"\n[{best_strat}] 交易明细:")
        print(f"  {'代码':<10} {'名称':<10} {'买入日':<12} {'卖出日':<12} {'收益%':>8} {'原因'}")
        print(f"  {'-'*60}")
        for t in all_results[best_strat]["trades"]:
            tag = "✓" if t["pct"] > 0 else "✗"
            print(f"  {t['code']:<10} {t['name']:<10} {t['buy_date']:<12} {t['sell_date']:<12} {t['pct']:>+7.2f}% {tag} {t['reason']}")


def gen_html(results: dict):
    """生成策略对比HTML"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    strat_names = list(results.keys())
    strat_data = [results[n] for n in strat_names]
    
    # 构建各策略累计收益
    all_cum = {}
    for name, r in results.items():
        cum = 0
        points = []
        labels = []
        for t in r.get("trades", []):
            cum += t["pct"]
            points.append(round(cum, 2))
            labels.append(t["sell_date"])
        all_cum[name] = {"points": points, "labels": labels}
    
    # 收益分布
    dist_data = {}
    for name, r in results.items():
        dist = {"ge8":0,"ge5_lt8":0,"ge2_lt5":0,"ge0_lt2":0,"lt0_ge-3":0,"lt-3":0}
        for t in r.get("trades", []):
            p = t["pct"]
            if p >= 8: dist["ge8"]+=1
            elif p >= 5: dist["ge5_lt8"]+=1
            elif p >= 2: dist["ge2_lt5"]+=1
            elif p >= 0: dist["ge0_lt2"]+=1
            elif p >= -3: dist["lt0_ge-3"]+=1
            else: dist["lt-3"]+=1
        dist_data[name] = dist
    
    import json as _json
    # 构建可序列化的结果
    ser_results = {}
    for n, r in results.items():
        ser_results[n] = {k: v for k, v in r.items() if k != 'trades'}
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>多策略回测对比报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:20px}}
h1{{color:#ffd700;text-align:center;font-size:24px}}
.sub{{color:#888;text-align:center;font-size:13px;margin-bottom:20px}}
.dash{{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:1400px;margin:0 auto}}
.card{{background:linear-gradient(135deg,#1a1a3e,#16213e);border-radius:10px;padding:14px;border:1px solid #2a2a5e}}
.card h2{{color:#ffd700;font-size:14px;margin-bottom:8px}}
.full{{grid-column:1/-1}}
.chart{{width:100%;height:350px}}
.chart.tall{{height:500px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#2a2a5e;color:#ffd700;padding:6px;text-align:left;position:sticky;top:0}}
td{{padding:5px 6px;border-bottom:1px solid #1a1a3e}}
.scroll{{max-height:350px;overflow-y:auto}}
.tag{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:11px}}
.win{{background:#ff4d4f33;color:#ff4d4f;border:1px solid #ff4d4f66}}
.lose{{background:#52c41a33;color:#52c41a;border:1px solid #52c41a66}}
.best{{background:#ffd70022;color:#ffd700;border:1px solid #ffd70066}}
</style>
</head>
<body>
<h1>📊 多策略窗口期回测对比</h1>
<p class="sub">5种策略同时回测 | 候选股12只 | 近1个月K线 | {now}</p>

<div class="dash">
  <!-- 总收益对比 -->
  <div class="card full"><h2>🏆 策略总收益对比</h2><div id="barCompare" class="chart" style="height:300px"></div></div>
  
  <!-- 累计收益曲线 -->
  <div class="card full"><h2>📈 累计收益曲线对比</h2><div id="cumCompare" class="chart tall"></div></div>
  
  <!-- 详细数据表 -->
  <div class="card full">
    <h2>📋 策略详细数据</h2>
    <div class="scroll">
    <table><tr><th>策略</th><th>总交易</th><th>盈利</th><th>亏损</th><th>胜率</th><th>总收益</th><th>平均</th><th>盈利总额</th><th>亏损总额</th><th>盈亏比</th><th>最大回撤</th></tr>
    {''.join(f'<tr class="{"best" if s["total_pnl"]>5 else ""}"><td><b>{n}</b></td><td>{s["total"]}</td><td>{s["wins"]}</td><td>{s["losses"]}</td><td>{s["win_rate"]}%</td><td style="color:{"#ff4d4f" if s["total_pnl"]>0 else "#52c41a"}">{s["total_pnl"]:+.2f}%</td><td>{s["avg_pnl"]:+.2f}%</td><td>{s["gross_profit"]:+.2f}%</td><td>{s["gross_loss"]:.2f}%</td><td>{s["profit_factor"]}</td><td>{s["max_drawdown"]:.2f}%</td></tr>' for n,s in results.items())}
    </table>
    </div>
  </div>
  
  <!-- 收益分布 -->
  <div class="card full"><h2>📊 各策略收益分布</h2><div id="distCompare" class="chart tall"></div></div>
</div>

<script>
var stratNames = {_json.dumps(strat_names)};
var allCum = {_json.dumps(all_cum)};
var distData = {_json.dumps(dist_data)};
var results = {_json.dumps(ser_results)};

var COLORS = ['#ff4d4f','#1890ff','#52c41a','#fa8c16','#722ed1'];
var COLOR_MAP = {{}};
stratNames.forEach(function(n,i){{COLOR_MAP[n]=COLORS[i]}});

// === 总收益对比柱状图 ===
var barChart = echarts.init(document.getElementById('barCompare'));
var barData = stratNames.map(function(n,i) {{
    var v = results[n].total_pnl;
    return {{value:v,itemStyle:{{color:v>5?'#ffd700':v>0?'#ff4d4f':'#52c41a'}}}};
}});
barChart.setOption({{
    backgroundColor:'transparent',
    grid:{{left:'8%',right:'5%',top:'10%',bottom:'15%'}},
    xAxis:{{type:'category',data:stratNames,axisLabel:{{fontSize:11,color:'#ccc',rotate:10}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:{{type:'value',name:'总收益%',nameTextStyle:{{color:'#888'}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
    series:[{{type:'bar',data:barData,barWidth:'50%',
        label:{{show:true,position:'top',fontSize:12,color:'#ffd700',fontWeight:'bold',
            formatter:function(p){{return p.value.toFixed(2)+'%'+(p.value>5?' 🏆':'')}}}}}}
    ],
    markLine:{{data:[{{yAxis:5,lineStyle:{{color:'#ffd700',type:'dashed'}},label:{{formatter:'目标5%',color:'#ffd700'}}}}]}}
}});

// === 累计收益曲线 ===
var cumChart = echarts.init(document.getElementById('cumCompare'));
var cumSeries = stratNames.map(function(n) {{
    var d = allCum[n];
    return {{
        name:n,
        type:'line',
        data:d.points,
        smooth:true,
        lineStyle:{{width:2,color:COLOR_MAP[n]}},
        symbol:'none',
    }};
}});
// 合并所有日期标签
var allLabels = [];
stratNames.forEach(function(n) {{ allLabels = allLabels.concat(allCum[n].labels); }});
allLabels = [...new Set(allLabels)].sort();

// 对齐数据到统一时间轴
var alignedSeries = stratNames.map(function(n) {{
    var d = allCum[n];
    var cum = 0;
    var labelMap = {{}};
    for(var j=0;j<d.labels.length;j++) {{
        cum = d.points[j];
        labelMap[d.labels[j]] = cum;
    }}
    var aligned = allLabels.map(function(l) {{
        return labelMap[l] !== undefined ? labelMap[l] : null;
    }});
    // 向前填充null
    var last = 0;
    for(var j=0;j<aligned.length;j++) {{
        if(aligned[j] === null) aligned[j] = last;
        else last = aligned[j];
    }}
    return {{
        name:n,
        type:'line',
        data:aligned,
        smooth:true,
        lineStyle:{{width:2,color:COLOR_MAP[n]}},
        symbol:'none',
        areaStyle:{{opacity:0.05}},
    }};
}});

cumChart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis'}},
    legend:{{data:stratNames,textStyle:{{color:'#ccc'}},top:0}},
    grid:{{left:'5%',right:'3%',top:'15%',bottom:'12%'}},
    xAxis:{{type:'category',data:allLabels,axisLabel:{{rotate:45,fontSize:9,color:'#999',interval:2}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:{{type:'value',name:'累计%',nameTextStyle:{{color:'#888'}},splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888',formatter:function(v){{return v+'%'}}}}}},
    series:alignedSeries,
}});

// === 收益分布堆叠柱状图 ===
var distChart = echarts.init(document.getElementById('distCompare'));
var distCategories = ['≥8%','5~8%','2~5%','0~2%','-3~0%','<-3%'];
var distKeys = ['ge8','ge5_lt8','ge2_lt5','ge0_lt2','lt0_ge-3','lt-3'];
var distColors = ['#ff4d4f','#ff7a45','#ffa39e','#d9d9d9','#b7eb8f','#52c41a'];

var distSeries = stratNames.map(function(n,i) {{
    var d = distData[n];
    return {{
        name:n,
        type:'bar',
        stack:'total',
        data:distKeys.map(function(k){{return d[k]||0}}),
        itemStyle:{{color:COLOR_MAP[n],opacity:0.7}},
    }};
}});

distChart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}}}},
    legend:{{data:stratNames,textStyle:{{color:'#ccc'}},top:0}},
    grid:{{left:'5%',right:'3%',top:'15%',bottom:'10%'}},
    xAxis:{{type:'category',data:distCategories,axisLabel:{{fontSize:11,color:'#ccc'}},axisLine:{{lineStyle:{{color:'#333'}}}}}},
    yAxis:{{type:'value',splitLine:{{lineStyle:{{color:'#1a1a3e'}}}},axisLabel:{{color:'#888'}}}},
    series:distSeries,
}});

window.onresize=function(){{barChart.resize();cumChart.resize();distChart.resize()}};
</script>
</body>
</html>"""

    path = os.path.join(OUTPUT_DIR, "strategy_compare.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[+] 对比报告: {path}")


if __name__ == "__main__":
    main()
