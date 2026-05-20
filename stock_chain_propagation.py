#!/usr/bin/env python3
"""
产业链传导分析引擎 + 产业链推荐策略

核心逻辑：
  1. 构建产业链图谱（同 stock_industry_graph.py）
  2. 对产业链内每只股票，计算它对同链其他股票的"传导影响系数"
  3. 传导因子：
     a) 同板块占比：两只股票共享的板块数 / 总板块数
     b) 涨跌相关性（proxy）：同板块内历史涨幅差越小 = 相关性越高
     c) 热点传导：龙头股涨幅越大，跟风股预期传导越强
     d) 市值比（proxy）：用价格*换手作为活跃度权重
  4. 输出每只股票的"产业链传导评分" = 同链其他股票受其影响的加权总和
  5. 推荐策略：选择传导评分高 + 自身涨幅适中的个股（即"带动效应强但还没大涨的"）

输出：viz_output/industry_chain_analysis.json + 嵌入到看板 HTML
"""

import os, sys, json, time, re as re2
from datetime import datetime
from collections import defaultdict
import math

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

# 权重因子
W_SAME_BOARD = 0.35   # 同板块占比权重
W_CORRELATION = 0.25  # 相关性权重
W_HOT_SPREAD = 0.25   # 热点传导权重
W_ACTIVITY = 0.15     # 活跃度权重


# ============================================================
# 1. 数据采集（同产业链图谱）
# ============================================================
def collect_data(top_boards=50, stocks_per_board=10):
    print("[*] 采集板块与成分股数据...")
    boards = get_all_concept_boards(use_cache=True)

    board_to_stocks = {}
    stock_to_boards = defaultdict(list)
    board_scores = {}
    stock_info = {}  # code -> {name, price, inc, board_count}

    for b in boards[:top_boards]:
        name = b["name"]
        cons = get_board_constituents(name)
        if not cons:
            time.sleep(0.08)
            continue

        history = get_board_history(name)
        score = 50
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            score = analysis["score"]
        board_scores[name] = score

        stocks = []
        for s in cons[:stocks_per_board]:
            code = s.get("代码", "")
            sname = s.get("名称", "")
            inc = safe_float(s.get("涨幅", 0))
            price = safe_float(s.get("现价", 0))
            vol = safe_float(s.get("换手", 0))
            if sname.startswith(("ST", "*ST", "退")):
                continue
            stock_to_boards[code].append(name)
            stocks.append({"code": code, "name": sname, "inc": inc, "price": price, "vol": vol})
            if code not in stock_info:
                stock_info[code] = {"name": sname, "price": price, "inc": inc, "vol": vol, "board_count": 0}
            stock_info[code]["board_count"] += 1

        if stocks:
            board_to_stocks[name] = stocks

        time.sleep(0.08)

    # 补全 board_count
    for code, bnames in stock_to_boards.items():
        if code in stock_info:
            stock_info[code]["board_count"] = len(bnames)

    # 聚类产业链
    board_names = list(board_to_stocks.keys())
    board_pairs = defaultdict(int)
    for code, bnames in stock_to_boards.items():
        if len(bnames) >= 2:
            for i in range(len(bnames)):
                for j in range(i+1, len(bnames)):
                    a, b = bnames[i], bnames[j]
                    if a in board_names and b in board_names:
                        key = tuple(sorted([a, b]))
                        board_pairs[key] += 1

    parent = {b: b for b in board_names}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px

    for (b1, b2), count in board_pairs.items():
        if count >= 2:
            union(b1, b2)

    cluster_map = defaultdict(list)
    for b in board_names:
        cluster_map[find(b)].append(b)

    industry_clusters = {k: v for k, v in cluster_map.items() if len(v) >= 2}
    standalone = [b for b in board_names if b not in [bb for g in industry_clusters.values() for bb in g]]
    if standalone:
        industry_clusters[f"other_{id(standalone)}"] = standalone

    # 产业链命名
    industry_names = {}
    for root, members in industry_clusters.items():
        top = sorted(members, key=lambda x: board_scores.get(x, 50), reverse=True)[0]
        clean = top.replace("概念", "").replace("板块", "")
        if len(clean) > 8:
            clean = clean[:6] + ".."
        industry_names[root] = f"{clean}产业链"

    print(f"[+] {len(board_to_stocks)} 板块, {len(stock_info)} 个股, {len(industry_clusters)} 产业链簇")
    return board_to_stocks, stock_to_boards, board_scores, stock_info, industry_clusters, industry_names


# ============================================================
# 2. 传导分析引擎
# ============================================================
def analyze_chain_propagation(board_to_stocks, stock_to_boards, board_scores, stock_info,
                               industry_clusters, industry_names):
    """
    对每条产业链，计算个股间的传导影响系数

    返回:
    chain_data = {
      chain_name: {
        "boards": [板块名],
        "stocks": [{code, name, inc, price, vol, ...}],
        "propagation": {
          code: {
            "influence_score": 总传导影响分,
            "affects": [{target_code, target_name, score, reasons}],
            "affected_by": [{source_code, source_name, score, reasons}],
          }
        },
        "recommendations": [{code, name, chain_score, reason}]
      }
    }
    """
    chain_data = {}
    all_stock_codes = set(stock_info.keys())

    for root, members in industry_clusters.items():
        cname = industry_names.get(root, "其他")
        print(f"\n  [*] 分析: {cname} ({len(members)} 板块)")

        # 收集该产业链的所有个股
        chain_stocks = {}  # code -> info
        for bname in members:
            for s in board_to_stocks.get(bname, []):
                code = s["code"]
                if code not in chain_stocks:
                    chain_stocks[code] = {**s, "board_count": stock_info.get(code, {}).get("board_count", 1)}

        chain_codes = list(chain_stocks.keys())
        print(f"      {len(chain_codes)} 只个股")

        if len(chain_codes) < 2:
            continue

        # ---- 计算两两只股票之间的传导影响 ----
        propagation = {}

        for i, ci in enumerate(chain_codes):
            si = chain_stocks[ci]
            si_boards = set(stock_to_boards.get(ci, [])) & set(members)

            affects = []
            total_influence = 0

            for j, cj in enumerate(chain_codes):
                if i == j:
                    continue
                sj = chain_stocks[cj]
                sj_boards = set(stock_to_boards.get(cj, [])) & set(members)

                # --- 因子1: 同板块占比 ---
                shared_boards = si_boards & sj_boards
                all_boards = si_boards | sj_boards
                same_board_ratio = len(shared_boards) / max(len(all_boards), 1)

                # --- 因子2: 涨跌相关性（涨幅差的倒数）---
                inc_diff = abs(si["inc"] - sj["inc"])
                correlation = max(0, 1 - inc_diff / 30)  # 涨幅差超过30%则认为不相关

                # --- 因子3: 热点传导（龙头->跟风）---
                # 如果i涨幅大，它对j的传导强；反之弱
                hot_spread = 0
                if si["inc"] > 3 and sj["inc"] <= si["inc"]:
                    # i是龙头/领涨，j是跟风
                    hot_spread = min(1, (si["inc"] - max(0, sj["inc"])) / 20)
                elif sj["inc"] > 3 and si["inc"] <= sj["inc"]:
                    # j是龙头，i受j影响
                    hot_spread = min(1, (sj["inc"] - max(0, si["inc"])) / 20) * 0.5  # 反向影响减半

                # --- 因子4: 活跃度 ---
                avg_vol = (abs(si.get("vol", 0)) + abs(sj.get("vol", 0))) / 2
                activity = min(1, avg_vol / 20)

                # --- 综合传导系数 ---
                score = (
                    W_SAME_BOARD * same_board_ratio +
                    W_CORRELATION * correlation +
                    W_HOT_SPREAD * hot_spread +
                    W_ACTIVITY * activity
                )
                score = max(0, min(1, score))

                if score > 0.05:  # 阈值过滤
                    affects.append({
                        "target": cj,
                        "target_name": sj["name"],
                        "score": round(score, 3),
                        "same_board_ratio": round(same_board_ratio, 2),
                        "correlation": round(correlation, 2),
                        "hot_spread": round(hot_spread, 2),
                        "activity": round(activity, 2),
                        "target_inc": sj["inc"],
                    })
                    total_influence += score

            propagation[ci] = {
                "influence_score": round(total_influence, 3),
                "affects": sorted(affects, key=lambda x: x["score"], reverse=True),
                "name": si["name"],
                "inc": si["inc"],
                "price": si.get("price", 0),
                "vol": si.get("vol", 0),
            }

        # ---- 产业链推荐策略 ----
        # 选传导影响大 + 涨幅还不大的 → 带动效应强但还有空间
        recommendations = []
        for code, prop in propagation.items():
            if prop["influence_score"] > 0 and 0 <= prop["inc"] <= 6:
                chain_score = prop["influence_score"] * (1 - prop["inc"] / 10)
                reason_parts = []
                if prop["affects"]:
                    top_affects = prop["affects"][:3]
                    reason_parts.append(f"影响: {', '.join([a['target_name'] for a in top_affects])}")
                if prop["inc"] > 2:
                    reason_parts.append("自身已启动")
                elif prop["inc"] <= 0:
                    reason_parts.append("低吸窗口")
                recommendations.append({
                    "code": code,
                    "name": prop["name"],
                    "inc": prop["inc"],
                    "price": prop["price"],
                    "vol": prop["vol"],
                    "influence_score": prop["influence_score"],
                    "chain_score": round(chain_score, 3),
                    "affects_count": len(prop["affects"]),
                    "reason": "; ".join(reason_parts),
                })

        recommendations.sort(key=lambda x: x["chain_score"], reverse=True)

        chain_data[cname] = {
            "boards": members,
            "stock_count": len(chain_codes),
            "propagation": propagation,
            "recommendations": recommendations,
            "hot_board": max(members, key=lambda b: board_scores.get(b, 50)),
            "hot_board_score": max(board_scores.get(b, 50) for b in members),
        }

        top_recs = recommendations[:3]
        if top_recs:
            names = [f'{r["name"]}(分{r["chain_score"]:.2f})' for r in top_recs]
            print(f"      TOP推荐: {', '.join(names)}")

    return chain_data


# ============================================================
# 3. 生成图谱数据 + 传导数据JSON
# ============================================================
def build_output(chain_data, stock_info, board_to_stocks, stock_to_boards, board_scores):
    """构建完整输出数据"""

    # 汇总所有推荐（跨产业链）
    all_recs = []
    for cname, cd in chain_data.items():
        for r in cd.get("recommendations", []):
            all_recs.append({**r, "chain": cname})

    all_recs.sort(key=lambda x: x["chain_score"], reverse=True)

    # 统计
    total_influence = sum(len(cd.get("propagation", {})) for cd in chain_data.values())
    total_edges = sum(
        len(prop["affects"]) for cd in chain_data.values()
        for prop in cd.get("propagation", {}).values()
    )

    output = {
        "generated_at": NOW,
        "chain_count": len(chain_data),
        "total_stocks_analyzed": total_influence,
        "total_propagation_edges": total_edges,
        "chains": chain_data,
        "all_recommendations": all_recs[:30],  # 取前30
        "summary": {
            "chains": len(chain_data),
            "stocks": total_influence,
            "edges": total_edges,
            "top_recommendations": all_recs[:10],
        }
    }

    return output


# ============================================================
# 4. 生成HTML片段（嵌入看板用）
# ============================================================
def generate_chain_module_html(output):
    """生成产业链推荐策略的HTML卡片"""
    recs = output.get("all_recommendations", [])[:15]

    cards = ""
    for i, r in enumerate(recs):
        inc_c = "#ff4d4f" if r["inc"] > 0 else "#52c41a"
        inc_s = "+" if r["inc"] > 0 else ""
        cards += f'''
        <div class="dc" style="margin-bottom:6px">
          <div class="hd">
            <div><b style="color:#ffd700;font-size:13px">#{i+1} {r["name"]}</b> <span style="color:#888;font-size:11px">{r["code"]}</span> <span style="color:#666;font-size:11px">| {r.get("chain","")}</span></div>
            <div>
              <span style="color:#888">涨幅 </span><b style="color:{inc_c}">{inc_s}{r["inc"]:.1f}%</b>
              <span style="color:#888;margin-left:8px">传导 </span><b style="color:#1890ff">{r["influence_score"]:.2f}</b>
              <span style="color:#888;margin-left:8px">评分 </span><b style="color:#ffd700">{r["chain_score"]:.2f}</b>
            </div>
          </div>
          <div style="font-size:11px;color:#888;margin-top:4px">{r.get("reason","")} | 影响{r.get("affects_count",0)}只个股</div>
        </div>'''

    # 统计摘要
    stats = output.get("summary", {})
    stat_cards = f'''
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px">
      <div class="si"><div style="font-size:20px;font-weight:bold;color:#ffd700">{stats.get("chains",0)}</div><div style="font-size:11px;color:#888">产业链</div></div>
      <div class="si"><div style="font-size:20px;font-weight:bold;color:#1890ff">{stats.get("stocks",0)}</div><div style="font-size:11px;color:#888">个股分析</div></div>
      <div class="si"><div style="font-size:20px;font-weight:bold;color:#91cc75">{stats.get("edges",0)}</div><div style="font-size:11px;color:#888">传导关系</div></div>
      <div class="si"><div style="font-size:20px;font-weight:bold;color:#ff4d4f">{len(recs)}</div><div style="font-size:11px;color:#888">推荐标的</div></div>
    </div>'''

    return {
        "cards_html": cards,
        "stat_cards_html": stat_cards,
    }


# ============================================================
# 5. 主流程
# ============================================================
def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    产业链传导分析 + 推荐策略             ║
  ║    个股涨跌对同链股票的量化影响         ║
  ╚══════════════════════════════════════════╝
    """)

    # 1. 采集数据
    board_to_stocks, stock_to_boards, board_scores, stock_info, clusters, cnames = collect_data()

    # 2. 传导分析
    print("\n[*] 产业链传导分析...")
    chain_data = analyze_chain_propagation(
        board_to_stocks, stock_to_boards, board_scores, stock_info, clusters, cnames
    )

    # 3. 构建输出
    output = build_output(chain_data, stock_info, board_to_stocks, stock_to_boards, board_scores)

    # 4. 保存JSON
    json_path = os.path.join(OUTPUT_DIR, "industry_chain_analysis.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, default=lambda o: float(round(o,2)) if isinstance(o,(int,float)) else str(o))
    print(f"\n[+] 分析数据: {json_path}")

    # 5. 打印结果
    print(f"\n{'='*60}")
    print(f"  产业链推荐策略结果")
    print(f"{'='*60}")
    recs = output.get("all_recommendations", [])[:10]
    if recs:
        print(f"  {'排名':>3} {'个股':<12} {'产业链':<18} {'涨幅':>6} {'传导分':>8} {'综合分':>8} {'影响数':>6}")
        print(f"  {'-'*65}")
        for i, r in enumerate(recs):
            print(f"  {i+1:>3} {r['name']:<12} {r.get('chain',''):<18} {r['inc']:>+5.1f}% {r['influence_score']:>7.3f} {r['chain_score']:>7.3f} {r['affects_count']:>5}")
    else:
        print("  无推荐")

    print(f"\n[*] 完成！")


if __name__ == "__main__":
    main()
