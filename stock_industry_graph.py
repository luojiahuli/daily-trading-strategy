#!/usr/bin/env python3
"""
NetworkX 产业链关系图谱生成器

三层架构:
  1. 行业链 (上游/中游/下游) - 通过板块间的个股重叠推断
  2. 题材板块 (同花顺概念板块)
  3. 个股 (板块成分股)

关系类型:
  - 个股 → 板块: "属于该板块"
  - 板块 → 板块: "共享X只个股" (产业链上下游关联)
  - 个股 → 个股: "同一板块" 或 "跨板块关联"

输出: viz_output/industry_graph.html (独立可交互HTML)
"""

import os, sys, json, time, re as re2
from datetime import datetime
from collections import defaultdict

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests as req

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import (
    get_all_concept_boards, get_board_constituents, get_board_history,
    calc_board_analysis, safe_float, CACHE_DIR
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "viz_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# 1. 数据采集
# ============================================================
def collect_graph_data(top_boards=40, stocks_per_board=10):
    """
    采集图谱数据:
    - stock_to_boards: {股票代码: [板块名称, ...]}
    - board_to_stocks: {板块名称: [{code, name, inc, price}, ...]}
    - board_scores: {板块名称: 热度评分}
    """
    print("[*] 获取板块列表...")
    boards = get_all_concept_boards(use_cache=True)
    print(f"[+] {len(boards)} 个板块")

    board_to_stocks = {}
    stock_to_boards = defaultdict(list)
    board_scores = {}

    for b in boards[:top_boards]:
        name = b["name"]
        cons = get_board_constituents(name)
        if not cons:
            time.sleep(0.08)
            continue

        # 板块热度
        history = get_board_history(name)
        score = 50
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            score = analysis["score"]
            board_scores[name] = score
        else:
            board_scores[name] = 50

        stocks = []
        for s in cons[:stocks_per_board]:
            code = s.get("代码", "")
            sname = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))
            price = safe_float(s.get("现价", 0))
            if sname.startswith(("ST", "*ST", "退")):
                continue
            stock_to_boards[code].append(name)
            stocks.append({"code": code, "name": sname, "inc": inc, "price": price})
            stock_to_boards[code]  # ensure key exists

        if stocks:
            board_to_stocks[name] = stocks

        time.sleep(0.08)

    print(f"[+] 采集完成: {len(board_to_stocks)} 个板块, {len(stock_to_boards)} 只个股")
    return board_to_stocks, stock_to_boards, board_scores


# ============================================================
# 2. 构建产业链关系
# ============================================================
def build_industry_relations(board_to_stocks, stock_to_boards, board_scores):
    """
    构建三层图谱:

    节点类型:
    - industry (行业链/产业链簇): 通过板块聚类生成
    - board (题材板块)
    - stock (个股)

    边类型:
    - board_in_industry: 板块属于某个产业链簇
    - stock_in_board: 个股属于板块
    - board_related: 两个板块共享N只个股 (N>=2 视为强关联)
    - stock_related: 两只个股属于同一板块
    """

    # ----- 2a. 板块聚类：基于共享个股数 -----
    board_names = list(board_to_stocks.keys())
    print(f"[*] 计算板块间关联... ({len(board_names)} 个板块)")

    # 计算板块间共享个股数
    board_pairs = defaultdict(int)
    for code, bnames in stock_to_boards.items():
        if len(bnames) >= 2:
            for i in range(len(bnames)):
                for j in range(i+1, len(bnames)):
                    a, b = bnames[i], bnames[j]
                    if a in board_names and b in board_names:
                        key = tuple(sorted([a, b]))
                        board_pairs[key] += 1

    print(f"    {len(board_pairs)} 对板块有关联")

    # 聚类：按共享个股数 >= 2 的强关联构建产业链簇
    industry_clusters = defaultdict(list)
    assigned = set()
    cluster_id = 0

    # 用并查集简单聚类
    parent = {}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px

    for b in board_names:
        parent[b] = b

    for (b1, b2), count in board_pairs.items():
        if count >= 2:  # 共享2只以上个股视为强关联
            union(b1, b2)

    cluster_map = defaultdict(list)
    for b in board_names:
        root = find(b)
        cluster_map[root].append(b)

    # 过滤出有2个以上板块的聚类作为产业链簇
    industry_groups = {k: v for k, v in cluster_map.items() if len(v) >= 2}
    print(f"    {len(industry_groups)} 个产业链簇 (>=2个板块)")

    # 无关联的板块各自成簇
    standalone = [b for b in board_names if b not in [bb for g in industry_groups.values() for bb in g]]
    if standalone:
        industry_groups[f"other_{id(standalone)}"] = standalone

    # 给产业链簇命名
    industry_nodes = {}
    for root, members in industry_groups.items():
        # 用热度最高的板块作为产业链名称参考
        sorted_members = sorted(members, key=lambda x: board_scores.get(x, 50), reverse=True)
        if len(members) == 1:
            name = members[0]
        else:
            # 取名：取热度最高的板块名 + "产业链"
            top = sorted_members[0]
            # 去掉"概念"后缀
            clean = top.replace("概念", "").replace("板块", "")
            if len(clean) > 8:
                clean = clean[:6] + ".."
            name = f"{clean}产业链"
        industry_nodes[root] = {"name": name, "members": members, "size": len(members)}

    print(f"    {len(industry_nodes)} 个产业链节点")

    # ----- 2b. 构建图谱数据 -----
    nodes = []
    edges = []
    node_set = set()
    edge_set = set()

    def add_node(nid, name, cat, **kw):
        if nid not in node_set:
            node_set.add(nid)
            nodes.append({"id": nid, "name": name, "category": cat, **kw})

    def add_edge(src, dst, val=1, label=""):
        key = (src, dst) if src < dst else (dst, src)
        if key not in edge_set:
            edge_set.add(key)
            edges.append({"source": src, "target": dst, "value": val, "label": label})

    # ----- 添加产业链节点 (category=0) -----
    industry_colors = [
        "#ff6b6b", "#f06595", "#cc5de8", "#845ef7", "#5c7cfa",
        "#339af0", "#22b8cf", "#20c997", "#51cf66", "#94d82d",
        "#fcc419", "#ff922b", "#ff6b6b", "#e599f7", "#74c0fc",
    ]
    for i, (root, info) in enumerate(industry_nodes.items()):
        ci = i % len(industry_colors)
        add_node(f"ind_{root}", info["name"], 0,
                 symbolSize=30 + info["size"] * 3,
                 itemStyle={"color": industry_colors[ci]},
                 cluster_size=info["size"])

    # ----- 添加板块节点 (category=1) -----
    for bname in board_names:
        score = board_scores.get(bname, 50)
        # 找所属产业链
        parent_root = None
        for root, info in industry_nodes.items():
            if bname in info["members"]:
                parent_root = root
                break
        # 板块颜色按热度
        color = "#ff4d4f" if score >= 70 else "#fa8c16" if score >= 55 else "#fadb14" if score >= 40 else "#91cc75"
        add_node(f"b_{bname}", bname, 1,
                 symbolSize=max(20, min(60, 18 + score * 0.5)),
                 itemStyle={"color": color},
                 score=round(score, 1))
        # 板块 → 产业链
        if parent_root:
            add_edge(f"ind_{parent_root}", f"b_{bname}", val=round(score/10, 1))

    # ----- 添加个股节点 (category=2) + 个股→板块边 -----
    stock_color_map = {}
    all_stocks = {}
    for bname, stocks in board_to_stocks.items():
        for s in stocks:
            code = s["code"]
            if code not in all_stocks:
                all_stocks[code] = s
                color = "#ff4d4f" if s["inc"] > 5 else "#ff7a45" if s["inc"] > 2 else "#91cc75"
                stock_color_map[code] = color

    print(f"    {len(all_stocks)} 只个股")

    stock_inc = {}
    for code, s in all_stocks.items():
        stock_inc[code] = s["inc"]
        sid = f"s_{code}"
        add_node(sid, f"{s['name']}\\n({code})", 2,
                 symbolSize=max(10, min(40, 8 + abs(s["inc"]) * 2)),
                 itemStyle={"color": stock_color_map[code]},
                 inc=s["inc"],
                 price=s["price"],
                 shortName=s["name"])

    # 个股→板块边
    for bname, stocks in board_to_stocks.items():
        bid = f"b_{bname}"
        for s in stocks:
            sid = f"s_{s['code']}"
            add_edge(bid, sid, val=round(abs(s["inc"]), 1))

    # ----- 个股间关联边 (同板块) -----
    print(f"    [*] 构建个股间关联...")
    stock_edge_count = 0
    for bname, stocks in board_to_stocks.items():
        codes = [s["code"] for s in stocks]
        for i in range(len(codes)):
            for j in range(i+1, len(codes)):
                ci, cj = codes[i], codes[j]
                if ci in all_stocks and cj in all_stocks:
                    avg_inc = (abs(all_stocks[ci]["inc"]) + abs(all_stocks[cj]["inc"])) / 2
                    # 强度 = 共享板块数的影响
                    add_edge(f"s_{ci}", f"s_{cj}", val=round(avg_inc, 1))
                    stock_edge_count += 1

    print(f"    {stock_edge_count} 条个股间边")

    categories = [
        {"name": "产业链", "itemStyle": {"color": "#ff6b6b"}},
        {"name": "题材板块", "itemStyle": {"color": "#fa8c16"}},
        {"name": "个股", "itemStyle": {"color": "#91cc75"}},
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "categories": categories,
        "stats": {
            "industries": len(industry_nodes),
            "boards": len(board_names),
            "stocks": len(all_stocks),
            "edges": len(edges),
        }
    }


# ============================================================
# 3. 生成HTML
# ============================================================
def generate_html(graph_data, stock_candidates=None):
    data_json = json.dumps(graph_data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>产业链关系图谱 - {NOW}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f23;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:16px;overflow:hidden;height:100vh}}
h1{{color:#ffd700;text-align:center;font-size:20px;position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:10;text-shadow:0 2px 10px rgba(0,0,0,0.8)}}
#chart{{width:100vw;height:100vh}}
.legend{{position:absolute;bottom:20px;left:50%;transform:translateX(-50%);display:flex;gap:20px;z-index:10;background:#1a1a3edd;padding:8px 20px;border-radius:8px;border:1px solid #2a2a5e;font-size:12px}}
.legend-item{{display:flex;align-items:center;gap:6px}}
.dot{{width:12px;height:12px;border-radius:50%;display:inline-block}}
.stats{{position:absolute;top:40px;right:16px;z-index:10;background:#1a1a3edd;padding:10px 14px;border-radius:8px;border:1px solid #2a2a5e;font-size:11px;color:#888;line-height:1.6}}
.stats b{{color:#ffd700}}
.tip{{font-size:12px;line-height:1.5}}
</style>
</head>
<body>
<h1>产业链关系图谱 &#x1F50D;</h1>
<div class="stats">
  产业链 <b>{graph_data['stats']['industries']}</b> 个<br>
  板块 <b>{graph_data['stats']['boards']}</b> 个<br>
  个股 <b>{graph_data['stats']['stocks']}</b> 只<br>
  关联 <b>{graph_data['stats']['edges']}</b> 条
</div>
<div class="legend">
  <span class="legend-item"><span class="dot" style="background:#ff6b6b"></span>产业链</span>
  <span class="legend-item"><span class="dot" style="background:#fa8c16"></span>题材板块</span>
  <span class="legend-item"><span class="dot" style="background:#91cc75"></span>个股</span>
  <span style="color:#888">|</span>
  <span style="color:#888">拖拽移动 · 滚轮缩放 · 悬停查看详情</span>
</div>
<div id="chart"></div>

<script>
var graphData = {data_json};

var chart = echarts.init(document.getElementById('chart'));

// 节点大小归一化
var minSize = Infinity, maxSize = -Infinity;
graphData.nodes.forEach(function(n) {{
    if(n.symbolSize < minSize) minSize = n.symbolSize;
    if(n.symbolSize > maxSize) maxSize = n.symbolSize;
}});

chart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{
        formatter: function(params) {{
            if(params.dataType === 'node') {{
                var d = params.data;
                if(d.category === 0) {{
                    return '<div class="tip"><b style="color:#ff6b6b">&#x1F3ED; 产业链: ' + d.name + '</b><br/>' +
                        '关联板块数: ' + d.cluster_size + '</div>';
                }} else if(d.category === 1) {{
                    return '<div class="tip"><b style="color:#fa8c16">&#x1F4CA; 板块: ' + d.name + '</b><br/>' +
                        '热度: ' + d.score + '</div>';
                }} else {{
                    return '<div class="tip"><b style="color:#91cc75">&#x1F4C8; ' + d.shortName + '</b><br/>' +
                        '代码: ' + d.name.split('\\\\n')[1].replace(/[()]/g,'') + '<br/>' +
                        '涨幅: ' + (d.inc > 0 ? '+' : '') + d.inc.toFixed(2) + '%' +
                        (d.price ? ' | 现价: ' + d.price.toFixed(2) : '') + '</div>';
                }}
            }}
            return '';
        }}
    }},
    series:[{{
        type:'graph',
        layout:'force',
        force:{{
            repulsion: 1200,
            edgeLength: [80, 350],
            gravity: 0.05,
            friction: 0.1,
        }},
        roam:true,
        draggable:true,
        data: graphData.nodes.map(function(n) {{
            // 产业链节点用特殊形状
            if(n.category === 0) {{
                return {{
                    ...n,
                    symbol: 'diamond',
                    symbolSize: Math.max(40, n.symbolSize),
                    label: {{show:true,position:'bottom',fontSize:10,color:'#ffd700',fontWeight:'bold'}}
                }};
            }} else if(n.category === 1) {{
                return {{
                    ...n,
                    symbol: 'roundRect',
                    symbolSize: Math.max(25, n.symbolSize),
                    label: {{show:true,position:'right',fontSize:9,color:'#ccc'}}
                }};
            }} else {{
                return {{
                    ...n,
                    symbol: 'circle',
                    label: {{show:false}}
                }};
            }}
        }}),
        edges: graphData.edges.map(function(e) {{
            return {{
                ...e,
                lineStyle:{{
                    width: Math.max(0.5, Math.min(4, e.value || 1)),
                    opacity: 0.3,
                    curveness: 0.2,
                }}
            }};
        }}),
        categories: graphData.categories,
        lineStyle:{{
            color:'#2a2a5e',
            opacity:0.3,
        }},
        emphasis:{{
            focus:'adjacency',
            lineStyle:{{width:3,opacity:0.8}},
            itemStyle:{{shadowBlur:10,shadowColor:'rgba(255,215,0,0.3)'}},
        }},
        zoom: 0.6,
    }}]
}});

window.addEventListener('resize', function(){{chart.resize()}});
</script>
</body>
</html>"""
    return html


# ============================================================
# 主流程
# ============================================================
def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    产业链关系图谱生成器                  ║
  ║    NetworkX + ECharts 力导向图           ║
  ╚══════════════════════════════════════════╝
    """)

    # 1. 采集数据
    board_to_stocks, stock_to_boards, board_scores = collect_graph_data(
        top_boards=40, stocks_per_board=10
    )

    # 2. 构建关系图
    graph_data = build_industry_relations(board_to_stocks, stock_to_boards, board_scores)

    print(f"\n[+] 图谱统计:")
    print(f"    产业链: {graph_data['stats']['industries']}")
    print(f"    板块: {graph_data['stats']['boards']}")
    print(f"    个股: {graph_data['stats']['stocks']}")
    print(f"    关系边: {graph_data['stats']['edges']}")

    # 3. 生成HTML
    print("\n[*] 生成HTML...")
    html = generate_html(graph_data)
    path = os.path.join(OUTPUT_DIR, "industry_graph.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[+] 已生成: {path} ({len(html)} bytes)")

    import webbrowser
    webbrowser.open(f"file://{os.path.abspath(path)}")


if __name__ == "__main__":
    main()
