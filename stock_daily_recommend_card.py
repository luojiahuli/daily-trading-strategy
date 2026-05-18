#!/usr/bin/env python3
"""
每日开盘推荐卡片生成器 v2
1. 运行回测获取推荐数据
2. 生成精美H5卡片（深色主题）
3. 用浏览器截图生成图片
4. 推送飞书（文本+图片）
"""

import os, sys, json, time, re, base64
from datetime import datetime

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "daily_reports")
IMG_DIR = os.path.join(os.path.dirname(__file__), "daily_reports", "images")
os.makedirs(IMG_DIR, exist_ok=True)

MAX_POSITIONS = 3
STOP_LOSS = -3.0
TAKE_PROFIT = 5.0
MAX_HOLD = 5


def get_kline(code, days=50):
    try:
        code = str(code).strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
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
                "date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                "high": float(d["high"]), "low": float(d["low"]),
            })
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else:
                records[-1]["pct"] = 0.0
        return records
    except:
        return None


def run_backtest_on_pick(code, name, board):
    kline = get_kline(code)
    if not kline or len(kline) < 30:
        return None
    trades = []
    positions = []
    trade_dates = set()
    for i in range(1, len(kline)):
        today = kline[i]
        positions = [p for p in positions if i - p["buy_idx"] < MAX_HOLD]
        for p in positions[:]:
            hold = i - p["buy_idx"]
            if hold >= MAX_HOLD:
                pct = round((today["close"] - p["buy_price"]) / p["buy_price"] * 100, 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": round(today["close"],2),
                    "pct": pct, "reason": "到期"})
                positions.remove(p)
                continue
            hp = (today["high"] - p["buy_price"]) / p["buy_price"] * 100
            lp = (today["low"] - p["buy_price"]) / p["buy_price"] * 100
            if hp >= TAKE_PROFIT:
                sp = round(p["buy_price"] * (1 + TAKE_PROFIT/100), 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": sp,
                    "pct": TAKE_PROFIT, "reason": f"止盈+{TAKE_PROFIT:.0f}%"})
                positions.remove(p)
            elif lp <= STOP_LOSS:
                sp = round(p["buy_price"] * (1 + STOP_LOSS/100), 2)
                trades.append({"buy": p["buy_date"], "sell": today["date"],
                    "buy_price": round(p["buy_price"],2), "sell_price": sp,
                    "pct": STOP_LOSS, "reason": f"止损{STOP_LOSS:.0f}%"})
                positions.remove(p)
        if len(positions) < MAX_POSITIONS:
            prev = kline[i-1]
            if 2.0 <= prev["pct"] <= 9.0 and today["date"] not in trade_dates:
                trade_dates.add(today["date"])
                positions.append({"buy_date": today["date"], "buy_price": kline[i]["open"], "buy_idx": i})
    return trades if trades else None


def generate_html_card(top_recos, today_str):
    """生成精美H5卡片（深色股市主题，可直接截图）"""
    recos_json = json.dumps(top_recos, ensure_ascii=False)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 构建每行的HTML（避开f-string花括号冲突）
    rows_html = ""
    for i, r in enumerate(top_recos[:5]):
        inc_color = "#ff4d4f" if r["inc"] > 0 else "#52c41a"
        pnl_color = "#ff4d4f" if r["total_pnl"] > 0 else "#52c41a"
        row = f'''<tr{" class=alt" if i%2==0 else ""}>
          <td class="num">{i+1}</td>
          <td class="code">{r["code"]}</td>
          <td>{r["name"][:8]}</td>
          <td class="muted">{r["board"][:8]}</td>
          <td class="num" style="color:{inc_color}">{r["inc"]:+.1f}%</td>
          <td class="num" style="color:{pnl_color}">{r["total_pnl"]:+.2f}%</td>
          <td class="num" style="color:#52c41a">{r["win_rate"]:.0f}%</td>
        </tr>'''
        rows_html += row
    
    # 首选推荐
    pick_html = ""
    if top_recos:
        r0 = top_recos[0]
        pick_html = f'''<div class="pick-box">
          <div class="pick-header"><span class="badge">⭐ 首选推荐</span></div>
          <div class="pick-body">
            <div class="pick-name">{r0["name"]} <span class="pick-code">{r0["code"]}</span></div>
            <div class="pick-info">板块: {r0["board"]}</div>
            <div class="pick-info">回测收益: <span class="up">{r0["total_pnl"]:+.2f}%</span> | 胜率: {r0["wins"]}/{r0["total_trades"]} ({r0["win_rate"]:.0f}%)</div>
            <div class="pick-detail">推荐理由: {r0.get("reason_detail", "窗口期+技术面强势")}</div>
            <div class="pick-action">预期操作: {r0.get("expected_action", "开盘关注，择机入场")}</div>
          </div>
        </div>'''
    
    # 其他推荐详情
    detail_rows = ""
    for i, r in enumerate(top_recos[:3]):
        if i == 0:
            continue
        detail_rows += f'''<div class="detail-item">
          <div class="detail-header">推荐{i+1}: {r["name"]}({r["code"]})</div>
          <div class="detail-body">
            <span>板块: {r["board"]} | 涨幅: <span class="up">{r["inc"]:+.1f}%</span></span>
            <span>回测: <span class="up">{r["total_pnl"]:+.2f}%</span> | 胜率: {r["wins"]}/{r["total_trades"]} ({r["win_rate"]:.0f}%)</span>
          </div>
        </div>'''
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日开盘推荐 {today_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a1a;color:#e0e0e0;font-family:'Inter','PingFang SC','Microsoft YaHei',sans-serif;padding:0;width:780px;min-height:1150px}}
.container{{padding:20px}}

/* Header */
.header{{text-align:center;padding:24px 20px 16px;background:linear-gradient(180deg,#12123a 0%,#0a0a1a 100%);border-bottom:1px solid #1a1a3e}}
.header h1{{font-size:24px;font-weight:700;color:#ffd700;letter-spacing:-0.5px;margin-bottom:4px}}
.header .sub{{font-size:13px;color:#666;font-weight:400}}
.header .badge-row{{display:flex;justify-content:center;gap:10px;margin-top:8px}}
.header .badge-row span{{background:#1a1a3e;color:#888;padding:2px 10px;border-radius:4px;font-size:11px;border:1px solid #2a2a4e}}

/* Stats Grid */
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:16px 20px}}
.stat-card{{background:linear-gradient(135deg,#12123a,#1a1a3e);border-radius:10px;padding:12px 8px;text-align:center;border:1px solid #1e1e44}}
.stat-card .val{{font-size:20px;font-weight:700;margin-bottom:2px}}
.stat-card .lbl{{font-size:11px;color:#666}}

/* Section Title */
.section-title{{font-size:15px;font-weight:600;color:#ffd700;margin:16px 20px 10px;display:flex;align-items:center;gap:6px}}

/* Table */
.table-wrap{{margin:0 20px 12px;background:linear-gradient(135deg,#0f0f2e,#12123a);border-radius:10px;padding:4px 0;border:1px solid #1a1a3e;overflow:hidden}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#1a1a3e;color:#ffd700;font-weight:500;padding:8px 6px;text-align:left;font-size:11px;letter-spacing:0.3px;white-space:nowrap;border-bottom:1px solid #2a2a4e}}
th:first-child{{padding-left:12px;width:28px;text-align:center}}
th:last-child{{padding-right:12px;text-align:center}}
td{{padding:7px 6px;border-bottom:1px solid #12122a;font-size:12px}}
td:first-child{{padding-left:12px;text-align:center;color:#666}}
td:last-child{{padding-right:12px;text-align:center}}
tr.alt td{{background:rgba(255,255,255,0.02)}}
.code{{font-weight:600;font-family:'SF Mono','Menlo',monospace;letter-spacing:0.3px}}
.num{{font-weight:600;font-family:'SF Mono','Menlo',monospace;text-align:right}}
.muted{{color:#666;font-size:11px}}

/* Pick Box */
.pick-box{{margin:0 20px 12px;background:linear-gradient(135deg,#0d0d25,#15153a);border-radius:10px;border:1.5px solid #ffd700;overflow:hidden}}
.pick-header{{background:linear-gradient(90deg,#1a1a3e,#0f0f2e);padding:10px 14px;border-bottom:1px solid #2a2a4e}}
.badge{{color:#ffd700;font-size:13px;font-weight:600}}
.pick-body{{padding:12px 14px}}
.pick-name{{font-size:16px;font-weight:700;color:#fff;margin-bottom:4px}}
.pick-code{{font-size:13px;color:#888;font-weight:400;font-family:'SF Mono','Menlo',monospace}}
.pick-info{{font-size:12px;color:#ccc;margin-top:4px;line-height:1.6}}
.pick-detail{{font-size:11px;color:#999;margin-top:6px;padding-top:6px;border-top:1px solid #1a1a3e;line-height:1.5}}
.pick-action{{font-size:12px;color:#52c41a;margin-top:6px;font-weight:500}}

/* Detail Items */
.detail-item{{margin:0 20px 6px;background:#0f0f2e;border-radius:8px;padding:10px 14px;border:1px solid #1a1a3e}}
.detail-header{{font-size:12px;font-weight:600;color:#ddd;margin-bottom:4px}}
.detail-body{{font-size:11px;color:#888;line-height:1.6;display:flex;flex-direction:column}}
.up{{color:#ff4d4f;font-weight:600}}

/* Footer */
.footer{{margin:14px 20px 0;background:linear-gradient(135deg,#0d0d25,#12123a);border-radius:10px;padding:14px;border:1px solid #1a1a3e}}
.footer-title{{font-size:13px;font-weight:600;color:#ffd700;margin-bottom:6px}}
.footer-line{{font-size:11px;color:#999;line-height:1.8}}
.footer .warn{{color:#ff4d4f;margin-top:6px;padding-top:6px;border-top:1px solid #1a1a3e;font-size:11px}}
.footer .gen-time{{color:#555;font-size:10px;margin-top:4px}}
</style>
</head>
<body>
<div class="header">
  <h1>📈 每日开盘推荐</h1>
  <div class="sub">{today_str}</div>
  <div class="badge-row">
    <span>策略A: 基础窗口期</span>
    <span>止损-3%/止盈+5%</span>
    <span>持仓≤5日</span>
  </div>
</div>

<div class="stats">
  <div class="stat-card"><div class="val" style="color:#ffd700">{len(top_recos)}</div><div class="lbl">推荐个股</div></div>
  <div class="stat-card"><div class="val" style="color:#1890ff">{today_str}</div><div class="lbl">更新日期</div></div>
  <div class="stat-card"><div class="val" style="color:#ff4d4f">-3%/+5%</div><div class="lbl">止损/止盈</div></div>
  <div class="stat-card"><div class="val" style="color:#52c41a">5日</div><div class="lbl">最长持仓</div></div>
</div>

<div class="section-title">🏆 今日推荐 TOP {len(top_recos)}</div>
<div class="table-wrap">
  <table>
    <tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>涨幅</th><th>回测收益</th><th>胜率</th></tr>
    {rows_html}
  </table>
</div>

{pick_html}

<div class="section-title">📋 个股详情</div>
{detail_rows}

<div class="footer">
  <div class="footer-title">⚙️ 操作策略</div>
  <div class="footer-line"><b style="color:#ffd700">买入:</b> 前一日涨幅2~9%的个股，次日开盘等权重买入</div>
  <div class="footer-line"><b style="color:#ffd700">卖出:</b> 盘中+5%止盈 | -3%止损 | 持仓5日到期</div>
  <div class="footer-line"><b style="color:#ffd700">仓位:</b> 最多3只，单只≤30%</div>
  <div class="warn">⚠️ 股市有风险，投资需谨慎。以上仅供参考。</div>
  <div class="gen-time">生成时间: {now} | 数据: 新浪财经+同花顺</div>
</div>
</body>
</html>"""
    
    html_path = os.path.join(IMG_DIR, f"reco_{today_str.replace('-','')}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_path


def capture_screenshot(html_path, output_png):
    """用playwright截图（更稳定）"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 780, "height": 1150})
            page.goto(f"file://{html_path}")
            page.wait_for_timeout(1000)
            page.screenshot(path=output_png, full_page=True)
            browser.close()
        return True
    except ImportError:
        pass
    
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--window-size=780,1500")
        driver = webdriver.Chrome(options=options)
        driver.get(f"file://{html_path}")
        time.sleep(1)
        driver.save_screenshot(output_png)
        driver.quit()
        return True
    except:
        pass
    
    return False


def main():
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.weekday()
    
    if weekday >= 5:
        print("[i] 今天是非交易日（周末），跳过")
        return
    
    print(f"[*] {today} 扫描板块候选股...")
    boards = get_all_concept_boards(use_cache=True)
    if not boards:
        boards = get_all_concept_boards(use_cache=False)
    
    seen = {}
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
            if sname.startswith(("ST","*ST","退")):
                continue
            if 0 <= inc <= 15:
                if code not in seen or inc > seen[code]["inc"]:
                    seen[code] = {"code": code, "name": sname, "board": name, "inc": inc}
        time.sleep(0.08)
    
    candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)
    print(f"[+] 获取到 {len(candidates)} 只候选股")
    
    print(f"\n[*] 回测验证候选股...\n")
    all_results = []
    for idx, c in enumerate(candidates[:15]):
        print(f"  [{idx+1}/15] {c['code']} {c['name']} ({c['board']}) inc={c['inc']:+.1f}%...", end=" ", flush=True)
        trades = run_backtest_on_pick(c["code"], c["name"], c["board"])
        if trades:
            total_pnl = sum(t["pct"] for t in trades)
            wins = sum(1 for t in trades if t["pct"] > 0)
            reason = f"所属{c['board']}板块 | 涨幅{c['inc']:+.1f}% | 回测{len(trades)}笔总{total_pnl:+.2f}% | 胜率{wins}/{len(trades)}"
            if total_pnl > 25:
                action = "⭐ 强势推荐，开盘优先买入，设好止损-3%"
            elif total_pnl > 15:
                action = "✅ 推荐关注，开盘观察走势平稳可入场"
            elif total_pnl > 8:
                action = "👀 适当关注，等待更好介入时机"
            else:
                action = "📊 列入观察，暂不建议追高"
            all_results.append({
                **c,
                "trades": trades,
                "total_trades": len(trades),
                "total_pnl": round(total_pnl, 2),
                "wins": wins,
                "win_rate": round(wins/len(trades)*100, 1),
                "reason_detail": reason,
                "expected_action": action,
            })
            print(f"{len(trades)}笔 总{total_pnl:+.2f}% 胜{wins}/{len(trades)}")
        else:
            print("无信号")
        time.sleep(0.3)
    
    all_results.sort(key=lambda x: x["total_pnl"], reverse=True)
    top_recos = all_results[:8]
    
    # 文本报告
    lines = [f"\n  ╔══════════════════════════════════════════╗",
             f"  ║    每日开盘推荐报告                       ║",
             f"  ║    {today}                       ║",
             f"  ╚══════════════════════════════════════════╝",
             f"",
             f"  策略: 前日涨2~9%买入 → 止损-3%/止盈+5%/持仓5日",
             f"  {now.strftime('%H:%M')} 更新"]
    
    if top_recos:
        lines.append(f"")
        lines.append(f"  🏆 今日推荐个股 Top {len(top_recos)}:")
        lines.append(f"  {'':4}{'代码':<8} {'名称':<10} {'板块':<14} {'涨幅':>6} {'回测收益':>8} {'胜率':>6}")
        lines.append(f"  {'':4}{'-'*50}")
        for i, r in enumerate(top_recos):
            tag = "⭐" if i == 0 else ""
            lines.append(f"  {tag}{i+1:>2} {r['code']:<8} {r['name']:<10} {r['board']:<14} "
                        f"{r['inc']:>+5.1f}% {r['total_pnl']:>+7.2f}% {r['win_rate']:>5.1f}%")
        
        lines.append(f"")
        lines.append(f"  ── 个股详情 ──")
        for i, r in enumerate(top_recos[:3]):
            lines.append(f"")
            lines.append(f"  [推荐{i+1}] {r['name']}({r['code']})")
            lines.append(f"  板块: {r['board']} | 今日涨幅: {r['inc']:+.1f}%")
            lines.append(f"  回测总收益: {r['total_pnl']:+.2f}% ({r['total_trades']}笔交易)")
            lines.append(f"  历史胜率: {r['wins']}/{r['total_trades']} ({r['win_rate']:.0f}%)")
            lines.append(f"  推荐理由: {r.get('reason_detail', '窗口期+技术面强势')}")
            lines.append(f"  预期操作: {r.get('expected_action', '开盘关注')}")
    else:
        lines.append(f"  [i] 今日无符合条件的推荐")
    
    report_text = "\n".join(lines)
    print(report_text)
    
    # 保存文本
    report_path = os.path.join(OUTPUT_DIR, f"daily_{today}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    
    # 生成H5 + 截图
    html_path = generate_html_card(top_recos, today)
    print(f"\n[+] H5卡片: {html_path}")
    
    png_path = os.path.join(IMG_DIR, f"reco_{today.replace('-','')}.png")
    success = capture_screenshot(html_path, png_path)
    
    if success and os.path.exists(png_path):
        print(f"[+] H5→图片: {png_path} ({os.path.getsize(png_path)/1024:.1f} KB)")
    else:
        print(f"[!] 截图失败，尝试浏览器打开...")
        # 尝试用系统浏览器截图
        import subprocess
        subprocess.run(["open", html_path])
    
    print(f"\n[*] 完成！")
    
    # 输出结果JSON供cron读取
    result = {
        "report_text": report_text,
        "html_path": html_path,
        "png_path": png_path if success else None,
        "recos": [(r["code"], r["name"], r["board"], r["inc"], r["total_pnl"], r["win_rate"]) for r in top_recos[:5]],
    }
    with open(os.path.join(OUTPUT_DIR, "last_result.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False)
    
    return result


if __name__ == "__main__":
    main()
