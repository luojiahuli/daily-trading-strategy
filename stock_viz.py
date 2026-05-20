#!/usr/bin/env python3
"""
股票题材可视化系统 - 用ECharts展示板块-个股关系图谱、热度热力图、K线走势

生成一个独立的HTML文件，包含:
  1. 板块-个股力导向关系图 (基于NetworkX + ECharts)
  2. 板块热度排名柱状图
  3. 个股机会雷达图
  
使用方式:
  python3 stock_viz.py                    # 快速模式生成可视化
  python3 stock_viz.py --quick            # 快速模式
  python3 stock_viz.py --board "存储芯片"  # 指定板块展开
  python3 stock_viz.py --open             # 生成后自动打开浏览器
"""

import os, sys, time, json, argparse, webbrowser
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_history, get_board_constituents,
    get_stock_quote, calc_board_analysis, safe_float,
    CACHE_DIR
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def build_relationship_data(boards_with_analysis: list, top_n_boards: int = 30):
    """
    构建力导向图数据:
    - 节点: 板块 + 个股
    - 边: 板块包含个股
    - 节点大小根据热度/涨幅
    """
    nodes = []
    edges = []
    node_set = set()

    # 添加板块节点
    for b in boards_with_analysis[:top_n_boards]:
        name = b["name"]
        score = b["analysis"]["score"]
        ind = b["analysis"]["indicators"]
        pct_5d = ind.get("pct_5d", 0)
        pct_20d = ind.get("pct_20d", 0)

        if name not in node_set:
            node_set.add(name)
            # 板块用 category 0
            nodes.append({
                "id": name,
                "name": name,
                "category": 0,
                "value": round(score, 1),
                "symbolSize": max(30, min(80, 30 + score * 0.6)),
                "pct_5d": round(pct_5d, 2),
                "pct_20d": round(pct_20d, 2),
                "itemStyle": {"color": _board_color(score)},
            })

        # 获取成分股
        cons = get_board_constituents(name)
        if not cons:
            continue

        for s in cons[:6]:  # 每个板块最多6只股
            code = s.get("代码", "")
            stock_name = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))
            price = safe_float(s.get("现价", 0))

            # 过滤ST
            if stock_name.startswith(("ST", "*ST", "退")):
                continue

            stock_id = f"{code}|{stock_name}"
            if stock_id not in node_set:
                node_set.add(stock_id)
                # 个股用 category 1
                nodes.append({
                    "id": stock_id,
                    "name": f"{stock_name}({code})",
                    "shortName": stock_name,
                    "category": 1,
                    "value": abs(inc),
                    "symbolSize": max(15, min(50, 15 + abs(inc) * 3)),
                    "inc": round(inc, 2),
                    "price": price,
                    "itemStyle": {"color": _stock_color(inc)},
                })

            # 板块->个股边
            edges.append({
                "source": name,
                "target": stock_id,
                "value": round(abs(inc), 2),
                "lineStyle": {"width": max(1, min(5, abs(inc) * 0.5))},
            })

        time.sleep(0.15)  # 控制请求频率

    return {"nodes": nodes, "edges": edges, "categories": [
        {"name": "题材板块", "itemStyle": {"color": "#5470c6"}},
        {"name": "个股", "itemStyle": {"color": "#91cc75"}},
    ]}


def _board_color(score):
    """板块颜色：热度越高越红"""
    if score >= 70:
        return "#ff4d4f"
    elif score >= 55:
        return "#fa8c16"
    elif score >= 40:
        return "#fadb14"
    else:
        return "#91cc75"


def _stock_color(inc):
    """个股颜色：涨为红，跌为绿"""
    if inc > 5:
        return "#ff4d4f"
    elif inc > 2:
        return "#ff7a45"
    elif inc > 0:
        return "#ffa39e"
    elif inc > -2:
        return "#95de64"
    else:
        return "#52c41a"


def build_heatmap_data(boards_with_analysis: list, top_n: int = 30):
    """
    构建热度热力图数据
    横轴: 板块
    纵轴: 指标 (5日涨跌, 20日涨跌, 60日涨跌, 量比, 趋势, 窗口分)
    """
    boards = boards_with_analysis[:top_n]
    indicators = ["pct_5d", "pct_20d", "pct_60d", "量比", "趋势斜率", "窗口分"]
    indicator_labels = ["5日涨跌%", "20日涨跌%", "60日涨跌%", "量比", "趋势", "窗口分"]

    data = []
    max_vals = {}
    min_vals = {}

    for idx, b in enumerate(boards):
        ind = b["analysis"]["indicators"]
        for iname in indicators:
            val = ind.get(iname, 0)
            if iname not in max_vals:
                max_vals[iname] = val
                min_vals[iname] = val
            else:
                max_vals[iname] = max(max_vals[iname], val)
                min_vals[iname] = min(min_vals[iname], val)

    for idx, b in enumerate(boards):
        ind = b["analysis"]["indicators"]
        for j, iname in enumerate(indicators):
            val = ind.get(iname, 0)
            # 归一化到 0-1 用于颜色映射
            mx = max_vals.get(iname, 1)
            mn = min_vals.get(iname, -1)
            norm = (val - mn) / (mx - mn) if mx != mn else 0.5
            data.append([idx, j, round(val, 2), round(norm, 3)])

    return {
        "boardNames": [b["name"] for b in boards],
        "indicators": indicator_labels,
        "data": data,
    }


def build_chart_html(relation_data: dict, heatmap_data: dict, board_scores: list) -> str:
    """生成完整的HTML页面"""

    rel_json = json.dumps(relation_data, ensure_ascii=False)
    heat_json = json.dumps(heatmap_data, ensure_ascii=False)

    # 构建板块排名JSON
    rank_data = []
    for i, b in enumerate(board_scores[:30]):
        ind = b["analysis"]["indicators"]
        rank_data.append({
            "rank": i + 1,
            "name": b["name"],
            "score": b["analysis"]["score"],
            "pct_5d": ind.get("pct_5d", 0),
            "pct_20d": ind.get("pct_20d", 0),
        })
    rank_json = json.dumps(rank_data, ensure_ascii=False)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>题材板块可视化分析 - {now}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0f0f23; color: #e0e0e0; font-family: 'PingFang SC','Microsoft YaHei',sans-serif; padding: 20px; }}
h1 {{ color: #ffd700; text-align: center; font-size: 28px; margin-bottom: 5px; }}
.subtitle {{ text-align: center; color: #888; font-size: 14px; margin-bottom: 25px; }}
.dashboard {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-width: 1800px; margin: 0 auto; }}
.card {{ background: linear-gradient(135deg, #1a1a3e 0%, #16213e 100%); border-radius: 12px; padding: 16px; border: 1px solid #2a2a5e; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }}
.card h2 {{ color: #ffd700; font-size: 16px; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #2a2a5e; }}
.card.full {{ grid-column: 1 / -1; }}
.chart {{ width: 100%; height: 500px; }}
.chart.tall {{ height: 650px; }}
.chart.short {{ height: 350px; }}
.legend {{ display: flex; gap: 20px; justify-content: center; margin: 10px 0; font-size: 13px; }}
.legend-item {{ display: flex; align-items: center; gap: 6px; }}
.legend-dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
.legend-dot.board {{ background: #5470c6; }}
.legend-dot.stock {{ background: #91cc75; }}
.legend-dot.up {{ background: #ff4d4f; }}
.legend-dot.down {{ background: #52c41a; }}
.tooltip-box {{ font-size: 13px; line-height: 1.6; }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin: 2px; }}
.tag-hot {{ background: #ff4d4f33; color: #ff4d4f; border: 1px solid #ff4d4f44; }}
.tag-warm {{ background: #fa8c1633; color: #fa8c16; border: 1px solid #fa8c1644; }}
.tag-cool {{ background: #91cc7533; color: #91cc75; border: 1px solid #91cc7544; }}
.controls {{ text-align: center; margin: 15px 0; }}
.controls button {{
    background: #2a2a5e; color: #e0e0e0; border: 1px solid #3a3a7e; padding: 8px 18px;
    border-radius: 6px; cursor: pointer; font-size: 13px; margin: 0 5px; transition: all 0.2s;
}}
.controls button:hover {{ background: #3a3a7e; border-color: #5470c6; }}
.controls button.active {{ background: #5470c6; border-color: #6b8cff; }}
</style>
</head>
<body>

<h1>📊 题材板块可视化分析</h1>
<p class="subtitle">生成时间: {now} | 数据源: 同花顺 + 聚合数据</p>

<div class="controls">
    <button onclick="resetZoom()">重置视图</button>
    <button id="btnModerate" class="active" onclick="filterByScore(45, 80)">窗口期板块</button>
    <button id="btnHot" onclick="filterByScore(70, 100)">热门板块</button>
    <button id="btnAll" onclick="filterByScore(0, 100)">全部</button>
</div>

<div class="dashboard">

    <!-- 力导向关系图 -->
    <div class="card full">
        <h2>🔗 板块-个股关系图谱</h2>
        <p style="color:#888;font-size:12px;margin-bottom:8px;">
            节点: 方块=题材板块, 圆点=个股 | 颜色: 红=热/涨, 黄=温和, 绿=冷/跌 | 大小=热度/涨幅
            | 鼠标悬停查看详情, 拖拽移动节点
        </p>
        <div id="relationChart" class="chart tall"></div>
    </div>

    <!-- 板块热度排名 -->
    <div class="card">
        <h2>🔥 板块热度排名 Top 20</h2>
        <div id="rankChart" class="chart short"></div>
    </div>

    <!-- 热力图 -->
    <div class="card">
        <h2>🌡️ 板块指标热力图</h2>
        <p style="color:#888;font-size:12px;margin-bottom:8px;">颜色越红=指标越好, 越蓝=越差</p>
        <div id="heatChart" class="chart short"></div>
    </div>

    <!-- 5日涨跌分布 -->
    <div class="card">
        <h2>📈 板块5日涨跌分布</h2>
        <div id="barChart5d" class="chart short"></div>
    </div>

    <!-- 窗口分排名 -->
    <div class="card">
        <h2>🎯 窗口期板块排名</h2>
        <div id="windowChart" class="chart short"></div>
    </div>

</div>

<div class="legend">
    <span class="legend-item"><span class="legend-dot board"></span> 题材板块</span>
    <span class="legend-item"><span class="legend-dot stock"></span> 个股</span>
    <span class="legend-item"><span class="legend-dot up"></span> 上涨/热门</span>
    <span class="legend-item"><span class="legend-dot down"></span> 下跌/冷门</span>
</div>

<script>
var relationData = {rel_json};
var heatmapData = {heat_json};
var rankData = {rank_json};

// ============= 力导向关系图 =============
var relChart = echarts.init(document.getElementById('relationChart'));
var optionRelation = {{
    backgroundColor: 'transparent',
    tooltip: {{
        formatter: function(params) {{
            if (params.dataType === 'node') {{
                var d = params.data;
                if (d.category === 0) {{
                    return '<div class="tooltip-box">' +
                        '<b style="color:#5470c6">📊 板块: ' + d.name + '</b><br/>' +
                        '热度: ' + d.value + ' | 5日: ' + d.pct_5d + '% | 20日: ' + d.pct_20d + '%' +
                        '</div>';
                }} else {{
                    return '<div class="tooltip-box">' +
                        '<b style="color:#91cc75">📈 个股: ' + d.shortName + '</b><br/>' +
                        '代码: ' + d.name.split('|')[0] + '<br/>' +
                        '涨幅: ' + d.inc + '% | 现价: ' + d.price +
                        '</div>';
                }}
            }}
            return '';
        }}
    }},
    series: [{{
        type: 'graph',
        layout: 'force',
        force: {{
            repulsion: 800,
            edgeLength: [100, 300],
            layoutAnimation: true,
            gravity: 0.1,
        }},
        roam: true,
        draggable: true,
        data: relationData.nodes,
        edges: relationData.edges,
        categories: relationData.categories,
        label: {{
            show: true,
            position: 'right',
            fontSize: 11,
            color: '#ccc',
            formatter: function(params) {{
                if (params.data.category === 0) return params.data.name;
                return '';
            }}
        }},
        edgeLabel: {{
            show: false,
        }},
        lineStyle: {{
            color: '#2a2a5e',
            curveness: 0.2,
            opacity: 0.4,
        }},
        emphasis: {{
            focus: 'adjacency',
            lineStyle: {{
                width: 3,
                opacity: 0.8,
            }}
        }},
        zoom: 0.7,
    }}]
}};
relChart.setOption(optionRelation);

// ============= 板块排名柱状图 =============
var rankChart = echarts.init(document.getElementById('rankChart'));
var rankNames = rankData.slice(0, 20).map(function(d) {{ return d.name; }});
var rankScores = rankData.slice(0, 20).map(function(d) {{ return d.score; }});
var rankColors = rankScores.map(function(s) {{
    return s >= 70 ? '#ff4d4f' : s >= 55 ? '#fa8c16' : s >= 40 ? '#fadb14' : '#91cc75';
}});

var optionRank = {{
    backgroundColor: 'transparent',
    grid: {{ left: '3%', right: '3%', top: '5%', bottom: '15%', containLabel: true }},
    xAxis: {{
        type: 'category',
        data: rankNames,
        axisLabel: {{ rotate: 45, interval: 0, fontSize: 10, color: '#999' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
    }},
    yAxis: {{
        type: 'value',
        name: '热度评分',
        nameTextStyle: {{ color: '#888' }},
        splitLine: {{ lineStyle: {{ color: '#1a1a3e' }} }},
        axisLabel: {{ color: '#888' }},
    }},
    series: [{{
        type: 'bar',
        data: rankScores.map(function(v, i) {{
            return {{ value: v, itemStyle: {{ color: rankColors[i] }} }};
        }}),
        barWidth: '60%',
        label: {{
            show: true,
            position: 'top',
            fontSize: 10,
            color: '#ccc',
            formatter: function(p) {{ return p.value.toFixed(1); }}
        }},
    }}]
}};
rankChart.setOption(optionRank);

// ============= 热力图 =============
var heatChart = echarts.init(document.getElementById('heatChart'));
var heatData = [];
for (var i = 0; i < heatmapData.data.length; i++) {{
    var d = heatmapData.data[i];
    heatData.push([d[0], d[1], d[3]]);
}}

var optionHeat = {{
    backgroundColor: 'transparent',
    tooltip: {{
        formatter: function(params) {{
            var d = heatmapData.data[params.dataIndex];
            if (d) {{
                return '<div class="tooltip-box">' +
                    heatmapData.boardNames[d[0]] + '<br/>' +
                    heatmapData.indicators[d[1]] + ': <b>' + d[2] + '</b>' +
                    '</div>';
            }}
            return '';
        }}
    }},
    grid: {{ left: '10%', right: '3%', top: '5%', bottom: '20%' }},
    xAxis: {{
        type: 'category',
        data: heatmapData.boardNames,
        splitArea: {{ show: true }},
        axisLabel: {{ rotate: 45, interval: 0, fontSize: 9, color: '#999' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
    }},
    yAxis: {{
        type: 'category',
        data: heatmapData.indicators,
        splitArea: {{ show: true }},
        axisLabel: {{ color: '#ccc', fontSize: 11 }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
    }},
    visualMap: {{
        min: 0,
        max: 1,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: '2%',
        inRange: {{
            color: ['#313695', '#4575b4', '#74add1', '#abd9e9', '#fee090', '#fdae61', '#f46d43', '#d73027']
        }},
        textStyle: {{ color: '#888' }},
    }},
    series: [{{
        type: 'heatmap',
        data: heatData,
        label: {{
            show: false,
        }},
        emphasis: {{
            itemStyle: {{
                shadowBlur: 10,
                shadowColor: 'rgba(0, 0, 0, 0.5)',
            }}
        }}
    }}]
}};
heatChart.setOption(optionHeat);

// ============= 5日涨跌柱状图 =============
var barChart5d = echarts.init(document.getElementById('barChart5d'));
var bar5dNames = rankData.slice(0, 20).map(function(d) {{ return d.name; }});
var bar5dValues = rankData.slice(0, 20).map(function(d) {{ return d.pct_5d; }});

var optionBar5d = {{
    backgroundColor: 'transparent',
    grid: {{ left: '3%', right: '3%', top: '5%', bottom: '15%', containLabel: true }},
    xAxis: {{
        type: 'category',
        data: bar5dNames,
        axisLabel: {{ rotate: 45, interval: 0, fontSize: 10, color: '#999' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
    }},
    yAxis: {{
        type: 'value',
        name: '5日涨跌%',
        nameTextStyle: {{ color: '#888' }},
        splitLine: {{ lineStyle: {{ color: '#1a1a3e' }} }},
            axisLabel: {{ color: '#888', formatter: function(v) {{ return v + '%'; }} }},
    }},
    series: [{{
        type: 'bar',
        data: bar5dValues.map(function(v) {{
            return {{
                value: v,
                itemStyle: {{ color: v >= 0 ? '#ff4d4f' : '#52c41a' }}
            }};
        }}),
        barWidth: '50%',
        label: {{
            show: true,
            position: 'top',
            fontSize: 10,
            color: '#ccc',
            formatter: function(p) {{ return p.value.toFixed(1) + '%'; }}
        }},
    }}]
}};
barChart5d.setOption(optionBar5d);

// ============= 窗口分排名 =============
var windowChart = echarts.init(document.getElementById('windowChart'));
// 模拟窗口分 = 热度评分调整
var winData = rankData.slice(0, 20).map(function(d, i) {{
    var score = d.score;
    // 窗口分：热度适中加分，太高或太低减分
    var windowScore = score;
    if (score >= 45 && score <= 78) windowScore += 15;
    else if (score < 40) windowScore -= 10;
    else if (score > 80) windowScore -= 5;
    windowScore = Math.max(0, Math.min(100, windowScore));
    return {{
        name: d.name,
        value: Math.round(windowScore),
        original: score,
    }};
}}).sort(function(a, b) {{ return b.value - a.value; }});

var optionWindow = {{
    backgroundColor: 'transparent',
    grid: {{ left: '3%', right: '3%', top: '5%', bottom: '15%', containLabel: true }},
    xAxis: {{
        type: 'category',
        data: winData.map(function(d) {{ return d.name; }}),
        axisLabel: {{ rotate: 45, interval: 0, fontSize: 10, color: '#999' }},
        axisLine: {{ lineStyle: {{ color: '#333' }} }},
    }},
    yAxis: {{
        type: 'value',
        name: '窗口评分',
        nameTextStyle: {{ color: '#888' }},
        splitLine: {{ lineStyle: {{ color: '#1a1a3e' }} }},
        axisLabel: {{ color: '#888' }},
    }},
    series: [{{
        type: 'bar',
        data: winData.map(function(d) {{
            return {{
                value: d.value,
                itemStyle: {{
                    color: d.value >= 80 ? '#ff4d4f' : d.value >= 60 ? '#fa8c16' : d.value >= 40 ? '#fadb14' : '#91cc75'
                }}
            }};
        }}),
        barWidth: '50%',
        label: {{
            show: true,
            position: 'top',
            fontSize: 10,
            color: '#ccc',
            formatter: function(p) {{ return p.value; }}
        }},
    }}]
}};
windowChart.setOption(optionWindow);

// ============= 交互控制 =============
function resetZoom() {{
    relChart.setOption({{
        series: [{{
            zoom: 0.7,
            force: {{ layoutAnimation: true }},
        }}]
    }});
}}

function filterByScore(minScore, maxScore) {{
    var filteredNodes = relationData.nodes.filter(function(n) {{
        if (n.category === 0) {{
            return n.value >= minScore && n.value <= maxScore;
        }}
        return true; // keep all stocks
    }});

    var filteredNodeIds = new Set(filteredNodes.map(function(n) {{ return n.id; }}));
    var filteredEdges = relationData.edges.filter(function(e) {{
        return filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target);
    }});

    relChart.setOption({{
        series: [{{
            data: filteredNodes,
            edges: filteredEdges,
            force: {{
                repulsion: 600,
                edgeLength: [80, 200],
                layoutAnimation: true,
            }},
        }}]
    }});

    // 更新按钮样式
    document.querySelectorAll('.controls button').forEach(function(b) {{ b.classList.remove('active'); }});
    if (minScore === 45) document.getElementById('btnModerate').classList.add('active');
    else if (minScore === 70) document.getElementById('btnHot').classList.add('active');
    else document.getElementById('btnAll').classList.add('active');
}}

// 自适应
window.addEventListener('resize', function() {{
    relChart.resize();
    rankChart.resize();
    heatChart.resize();
    barChart5d.resize();
    windowChart.resize();
}});
</script>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="题材板块可视化分析")
    parser.add_argument("--quick", action="store_true", help="快速模式")
    parser.add_argument("--open", action="store_true", help="生成后自动打开浏览器")
    parser.add_argument("--top", type=int, default=30, help="分析前N个板块")
    parser.add_argument("--no-cache", action="store_true", help="不缓存")
    args = parser.parse_args()

    print("""
  ╔══════════════════════════════════════════╗
  ║    题材板块可视化分析系统                ║
  ║    ECharts + NetworkX 力导向图           ║
  ╚══════════════════════════════════════════╝
    """)

    boards = get_all_concept_boards(use_cache=not args.no_cache)
    if not boards:
        print("[!] 无法获取板块列表")
        return

    top_n = 50 if args.quick else min(args.top, len(boards))
    print(f"[*] 分析前 {top_n} 个板块...")

    all_results = []
    for idx, board in enumerate(boards[:top_n]):
        if (idx + 1) % 5 == 0:
            print(f"  [{idx+1}/{top_n}] 分析中... ({len(all_results)} 完成)", end="\r", flush=True)
        history = get_board_history(board["name"])
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            all_results.append({"name": board["name"], "code": board["code"], "analysis": analysis})
        time.sleep(0.2)

    all_results.sort(key=lambda x: x["analysis"]["score"], reverse=True)
    print(f"\n[+] 板块分析完成: {len(all_results)} 个")

    print(f"[*] 构建关系图谱 (获取成分股)...")
    rel_data = build_relationship_data(all_results, top_n_boards=20)
    print(f"[+] 关系图: {len(rel_data['nodes'])} 节点, {len(rel_data['edges'])} 条边")

    heat_data = build_heatmap_data(all_results, top_n=20)

    print(f"[*] 生成HTML页面...")
    html = build_chart_html(rel_data, heat_data, all_results)

    output_path = os.path.join(OUTPUT_DIR, "stock_viz.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 已生成: {output_path}")

    if args.open:
        webbrowser.open(f"file://{os.path.abspath(output_path)}")
        print(f"[+] 已在浏览器中打开")

    print(f"\n[*] 完成!")


if __name__ == "__main__":
    main()
