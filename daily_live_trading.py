#!/usr/bin/env python3
"""
每日实盘模拟交易系统 - 持续运行版

策略: 策略A 基础窗口期
  - 买入: 前一日涨幅2~9%的个股，当日开盘买入
  - 卖出: 盘中达+5%止盈 / -3%止损 / 持仓5日到期
  - 仓位: 等权重，最多同时3只，单只≤33%

每日流程 (15:30 cron):
  1. 获取今日所有持仓股实时行情
  2. 检查止盈止损 -> 卖出触发条件
  3. 检查持仓到期 -> 第5日自动卖出
  4. 扫描板块 -> 筛选候选股
  5. 买入新候选股（最多3只）
  6. 更新资金曲线
  7. 生成HTML日报 -> viz_output/stock_daily_YYMMDD.html
"""

import os, sys, json, time, re
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "live_trading")
VIZ_DIR = os.path.join(BASE_DIR, "viz_output")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(VIZ_DIR, exist_ok=True)

# ============================================================
# 配置
# ============================================================
INITIAL_CAPITAL = 100000
MAX_POSITIONS = 3
STOP_LOSS = -3.0
TAKE_PROFIT = 5.0
MAX_HOLD = 5
COMMISSION_RATE = 0.0003
STAMP_TAX = 0.001

STATE_FILE = os.path.join(DATA_DIR, "simulation_state.json")
LOG_PATH = os.path.join(DATA_DIR, "daily_log.txt")


# ============================================================
# 工具函数
# ============================================================
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")


def get_kline(code: str, days: int = 30) -> Optional[list]:
    """获取日K线"""
    try:
        symbol = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
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
            records.append({"date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                           "high": float(d["high"]), "low": float(d["low"])})
            if len(records) >= 2:
                records[-1]["pct"] = round((records[-1]["close"] - records[-2]["close"]) / records[-2]["close"] * 100, 2)
            else:
                records[-1]["pct"] = 0.0
        return records
    except:
        return None


def get_today_quotes(codes: list) -> dict:
    """获取多只个股实时行情"""
    quotes = {}
    api_key = "a10cb6c3ecac5077facb835d88cdf017"
    for code in codes:
        try:
            symbol = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
            params = {"key": api_key, "gid": symbol}
            resp = requests.get("https://web.juhe.cn/finance/stock/hs", params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("error_code") == 0 and data.get("result"):
                    items = data["result"]
                    if isinstance(items, list) and len(items) > 0:
                        item = items[0]
                        if isinstance(item, dict) and "data" in item:
                            d = item["data"]
                            quotes[code] = {
                                "price": safe_float(d.get("nowPri", 0)),
                                "open": safe_float(d.get("todayStartPri", 0)),
                                "high": safe_float(d.get("todayMax", 0)),
                                "low": safe_float(d.get("todayMin", 0)),
                                "pct": safe_float(d.get("increPer", 0)),
                            }
        except:
            pass
        time.sleep(0.3)
    return quotes


# ============================================================
# 交易状态管理
# ============================================================
def load_state():
    """加载交易状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "capital": INITIAL_CAPITAL,
        "positions": [],
        "trade_log": [],
        "daily_snapshots": [],
        "last_date": None,
    }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, default=str, indent=2)


# ============================================================
# 选股 & 买入分析
# ============================================================
def get_prev_day_pct(code: str) -> Optional[float]:
    """获取个股前一日涨幅"""
    kl = get_kline(code, days=5)
    if kl and len(kl) >= 2:
        return kl[-2].get("pct")
    return None


def analyze_candidates(candidates: list, quotes: dict) -> list:
    """
    对候选股进行买入分析：热门题材、K线形态、量价表现、技术指标、资金流向
    返回带分析评级的列表
    """
    from asharehub import AShareHub as _ASH
    
    # 初始化asharehub客户端
    ah = None
    try:
        ah = _ASH(api_key="ash_db6d84fb324fa03b5281f878a73eaa53afb467a6698c8294")
    except:
        pass

    board_heat = {}
    try:
        boards = get_all_concept_boards(use_cache=True)
        for b in boards:
            board_heat[b["name"]] = b.get("热度", 60)
    except:
        pass

    analyzed = []
    for c in candidates:
        code = c["code"]
        q = quotes.get(code, {})
        price = q.get("price", c.get("price", 0))

        # 1. 热门题材分析
        board_name = c.get("board", "")
        board_score = 0
        try:
            with open(os.path.join(BASE_DIR, "tracking_history", "latest.json")) as f:
                bd = json.load(f)
            for b in bd.get("board_ranking", [])[:30]:
                if board_name in b["name"] or b["name"] in board_name:
                    board_score = b["score"]
                    break
        except:
            pass

        # 2. 技术指标 (asharehub: MACD/KDJ/RSI/CCI/BOLL)
        tech = {}
        if ah:
            try:
                tf = ah.technical_factors(ts_code=code, start_date="2026-05-01")
                if tf is not None and len(tf) > 0:
                    last = tf.iloc[-1]
                    tech = {
                        'macd': last.get('macd', 0),
                        'kdj_k': last.get('kdj_k', 50),
                        'kdj_j': last.get('kdj_j', 50),
                        'rsi_6': last.get('rsi_6', 50),
                        'cci': last.get('cci', 0),
                        'boll_pos': (last.get('close_qfq', price) - last.get('boll_lower', 0)) / max(last.get('boll_upper', 1) - last.get('boll_lower', 1), 0.01),
                    }
            except:
                pass

        # 3. 资金流向 (asharehub: 大单净流入)
        moneyflow = {}
        if ah:
            try:
                mf = ah.moneyflow(limit=50)
                if mf is not None and len(mf) > 0:
                    row = mf[mf['ts_code'] == code]
                    if len(row) > 0:
                        r = row.iloc[0]
                        moneyflow = {
                            'net_mf': r.get('net_mf_amount', 0),
                            'buy_lg': r.get('buy_lg_amount', 0),
                            'sell_lg': r.get('sell_lg_amount', 0),
                        }
            except:
                pass

        # 4. K线形态分析 (原有逻辑)
        kl = get_kline(code, days=15)
        kline_analysis = {}
        if kl and len(kl) >= 5:
            closes = [k["close"] for k in kl[-10:]]
            highs = [k["high"] for k in kl[-10:]]
            lows = [k["low"] for k in kl[-10:]]
            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes) / len(closes)
            trend = "上升" if ma5 > ma10 and closes[-1] > ma5 else ("下降" if ma5 < ma10 and closes[-1] < ma5 else "震荡")
            volatility = (max(highs[-5:]) - min(lows[-5:])) / ma5 * 100
            recent_high = max(highs[-10:-5]) if len(highs) >= 10 else max(highs)
            breaking = closes[-1] > recent_high * 0.98
            kline_analysis = {"trend": trend, "ma5": round(ma5, 2), "ma10": round(ma10, 2),
                              "volatility": round(volatility, 2), "breaking": breaking}

        # 5. 综合评分 (融合技术指标和资金流)
        score = 0
        reasons = []

        inc = c.get("inc", 0)
        if 2.0 <= inc <= 4.0: score += 15; reasons.append("温和启动")
        elif 4.0 < inc <= 7.0: score += 25; reasons.append("强势启动")
        elif 7.0 < inc <= 9.0: score += 20; reasons.append("高位窗口")

        if board_score >= 70: score += 20; reasons.append(f"热门题材({board_score:.0f})")
        elif board_score >= 60: score += 10; reasons.append(f"关注题材({board_score:.0f})")

        # 技术因子评分
        if tech:
            if tech.get('macd', 0) > 0: score += 15; reasons.append("MACD金叉")
            if tech.get('kdj_j', 50) > 80: score += 10; reasons.append("KDJ强势")
            elif tech.get('kdj_j', 50) < 20: score -= 10; reasons.append("KDJ超卖")
            if tech.get('rsi_6', 50) > 60: score += 10; reasons.append("RSI强势")
            if tech.get('cci', 0) > 100: score += 10; reasons.append("CCI突破")
            elif tech.get('cci', 0) < -100: score -= 10; reasons.append("CCI超卖")
            pos = tech.get('boll_pos', 0.5)
            if pos > 0.8: score += 10; reasons.append("布林上轨")
            elif pos < 0.2: score += 5; reasons.append("布林下轨(反弹)")

        # 资金流向评分
        if moneyflow:
            net = moneyflow.get('net_mf', 0)
            if net > 0: score += 15; reasons.append("主力净流入")
            elif net < 0: score -= 10; reasons.append("主力净流出")
            buy_lg = moneyflow.get('buy_lg', 0)
            sell_lg = moneyflow.get('sell_lg', 0)
            if buy_lg > sell_lg * 1.5: score += 10; reasons.append("大单大幅买入")

        if kline_analysis:
            if kline_analysis["trend"] == "上升": score += 15; reasons.append("上升趋势")
            if kline_analysis.get("breaking"): score += 10; reasons.append("突破形态")
            if kline_analysis["volatility"] < 5: score += 5; reasons.append("低波动稳健")

        if score >= 75: rating = "⭐ 强烈推荐"
        elif score >= 50: rating = "✅ 推荐"
        elif score >= 25: rating = "👀 关注"
        else: rating = "⬇ 一般"

        analyzed.append({**c, "score": score, "rating": rating,
            "reasons": " | ".join(reasons),
            "trend": kline_analysis.get("trend", "未知"),
            "board_score": board_score,
            "ma5": kline_analysis.get("ma5", 0),
            "ma10": kline_analysis.get("ma10", 0),
            "tech": tech, "moneyflow": moneyflow})

    if ah: ah.close()
    analyzed.sort(key=lambda x: -x["score"])
    return analyzed


def screen_candidates() -> list:
    """扫描板块，筛选当日可买入候选股"""
    log("扫描候选股...")
    boards = get_all_concept_boards(use_cache=True)
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
            price = safe_float(s.get("现价", 0))
            if sname.startswith(("ST", "*ST", "退")):
                continue
            if 0 <= inc <= 15:
                if code not in seen or inc > seen[code]["inc"]:
                    seen[code] = {"code": code, "name": sname, "board": name, "inc": inc, "price": price}
        time.sleep(0.08)

    candidates = sorted(seen.values(), key=lambda x: x["inc"], reverse=True)
    # 只保留满足窗口期条件的
    window_candidates = [c for c in candidates if 2.0 <= c["inc"] <= 9.0]
    log(f"  候选: {len(window_candidates)}只窗口期 / 共{len(candidates)}只")
    return window_candidates[:MAX_POSITIONS * 2]


# ============================================================
# 交易引擎
# ============================================================
def run_daily():
    today = get_today_str()
    log(f"\n{'='*60}")
    log(f"📊 每日实盘模拟 | {today}")
    log(f"{'='*60}")

    state = load_state()
    capital = state["capital"]
    positions = state["positions"]
    trade_log = state["trade_log"]
    daily_snapshots = state["daily_snapshots"]

    # 如果是同一天已跑过，跳过
    if state.get("last_date") == today:
        log("⚠️ 今日已运行，跳过")
        return

    # 获取持仓和候选股的实时行情
    all_codes = list(set(
        [p["code"] for p in positions] +
        [c["code"] for c in screen_candidates()[:6]]
    ))

    if not all_codes:
        log("⚠️ 无候选股，可能非交易日")
        return

    log(f"获取 {len(all_codes)} 只个股实时行情...")
    quotes = get_today_quotes(all_codes)

    # ---- 1. 检查止盈止损 ----
    sold = []
    for p in positions[:]:
        code = p["code"]
        q = quotes.get(code)
        if not q:
            continue

        buy_price = p["buy_price"]
        high = q["high"]
        low = q["low"]
        hold_days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(p["buy_date"], "%Y-%m-%d")).days

        # 交易成本
        buy_cost = buy_price * p["shares"] * (1 + COMMISSION_RATE)
        def sell_proceed(price):
            return price * p["shares"] * (1 - COMMISSION_RATE - STAMP_TAX)

        # 止盈
        if high > 0 and (high - buy_price) / buy_price * 100 >= TAKE_PROFIT:
            sell_price = buy_price * (1 + TAKE_PROFIT / 100)
            sp = sell_proceed(sell_price)
            pnl = sp - buy_cost
            pnl_pct = (sp / buy_cost - 1) * 100
            trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(buy_price, 2), "sell_price": round(sell_price, 2),
                             "shares": p["shares"], "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                             "reason": f"止盈+{TAKE_PROFIT:.0f}%"})
            capital += sp
            log(f"✅ 止盈 {p['name']}({code}) 持仓{hold_days}天 +{pnl:.2f}")
            positions.remove(p)
            sold.append(p)
            continue

        # 止损
        if low > 0 and (low - buy_price) / buy_price * 100 <= STOP_LOSS:
            sell_price = buy_price * (1 + STOP_LOSS / 100)
            sp = sell_proceed(sell_price)
            pnl = sp - buy_cost
            pnl_pct = (sp / buy_cost - 1) * 100
            trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(buy_price, 2), "sell_price": round(sell_price, 2),
                             "shares": p["shares"], "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                             "reason": f"止损{STOP_LOSS:.0f}%"})
            capital += sp
            log(f"❌ 止损 {p['name']}({code}) 持仓{hold_days}天 {pnl:.2f}")
            positions.remove(p)
            sold.append(p)

    # ---- 2. 检查到期 ----
    for p in positions[:]:
        hold_days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(p["buy_date"], "%Y-%m-%d")).days
        if hold_days >= MAX_HOLD:
            q = quotes.get(p["code"], {})
            sell_price = q.get("price", p["buy_price"])
            pnl = (sell_price - p["buy_price"]) / p["buy_price"] * 100
            buy_cost = p["buy_price"] * p["shares"] * (1 + COMMISSION_RATE)
            sp = sell_price * p["shares"] * (1 - COMMISSION_RATE - STAMP_TAX)
            pnl_amt = sp - buy_cost
            trade_log.append({"date": today, "action": "卖出", "code": p["code"], "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(p["buy_price"], 2), "sell_price": round(sell_price, 2),
                             "shares": p["shares"], "pnl": round(pnl_amt, 2), "pnl_pct": round(pnl, 2),
                             "reason": f"持仓到期{MAX_HOLD}日"})
            capital += sp
            log(f"⏰ 到期卖出 {p['name']}({p['code']}) 持仓{hold_days}天 PnL{pnl:.2f}%")
            positions.remove(p)

    # ---- 3. 买入分析 & 买入新候选股 ----
    buy_decisions = []  # 记录本次买入决策的原因
    analysis = []  # 分析结果（给买入记录用）
    if len(positions) < MAX_POSITIONS:
        candidates = screen_candidates()
        # 过滤已持仓的
        held_codes = {p["code"] for p in positions}
        candidates = [c for c in candidates if c["code"] not in held_codes]

        # 买入分析
        if candidates:
            analysis = analyze_candidates(candidates[:10], quotes)
            log(f"\n  📊 买入分析 TOP {min(len(analysis), 5)}:")
            for a in analysis[:5]:
                log(f"    {a['rating']} {a['name']}({a['code']}) 涨幅{a['inc']:.1f}% "
                    f"题材评分{a['board_score']:.0f} K线{a['trend']} | {a['reasons']}")

        for c in candidates[:MAX_POSITIONS - len(positions)]:
            code = c["code"]
            q = quotes.get(code)
            if not q:
                continue
            buy_price = q.get("price", q.get("open", 0))
            if buy_price <= 0:
                continue

            # 获取该候选股的分析结果
            buy_reason = "策略A买入"
            for a in analysis:
                if a["code"] == code:
                    buy_reason = f"策略A {a['rating']} {a['trend']} {a['reasons'][:20]}"
                    buy_decisions.append(a)
                    break

            # 等权重
            slot_value = capital / (MAX_POSITIONS - len(positions))
            shares = int(slot_value / buy_price / 100) * 100  # 整百
            if shares < 100:
                continue
            cost = buy_price * shares * (1 + COMMISSION_RATE)
            if cost > capital * 0.33:
                shares = int(capital * 0.33 / buy_price / 100) * 100
                cost = buy_price * shares * (1 + COMMISSION_RATE)
            if shares < 100 or cost > capital:
                continue

            capital -= cost
            positions.append({
                "code": code, "name": c["name"], "board": c["board"],
                "buy_date": today, "buy_price": round(buy_price, 2),
                "shares": shares,
            })
            trade_log.append({"date": today, "action": "买入", "code": code, "name": c["name"],
                             "buy_date": today, "hold_days": 0,
                             "buy_price": round(buy_price, 2), "sell_price": 0,
                             "shares": shares, "pnl": 0, "pnl_pct": 0, "reason": buy_reason})
            log(f"🟢 买入 {c['name']}({code}) @ {buy_price:.2f} × {shares}股 = {cost:.2f} | {buy_reason}")

    # ---- 4. 更新快照 ----
    pos_value = sum(p["shares"] * quotes.get(p["code"], {}).get("price", p["buy_price"]) for p in positions)
    total_value = capital + pos_value
    prev_total = daily_snapshots[-1]["total_value"] if daily_snapshots else INITIAL_CAPITAL
    daily_pct = (total_value / prev_total - 1) * 100 if prev_total > 0 else 0

    daily_snapshots.append({
        "date": today, "capital": round(capital, 2),
        "position_value": round(pos_value, 2),
        "total_value": round(total_value, 2),
        "daily_pct": round(daily_pct, 2),
        "total_pct": round((total_value / INITIAL_CAPITAL - 1) * 100, 2),
        "position_count": len(positions),
    })

    # ---- 5. 保存状态 ----
    state["capital"] = capital
    state["positions"] = positions
    state["trade_log"] = trade_log
    state["daily_snapshots"] = daily_snapshots
    state["last_date"] = today
    save_state(state)

    # ---- 6. 输出摘要 ----
    total_pct = (total_value / INITIAL_CAPITAL - 1) * 100
    log(f"\n{'='*50}")
    log(f"📋 {today} 收盘总结")
    log(f"{'='*50}")
    log(f"  现金: {capital:>10.2f}")
    log(f"  持仓: {pos_value:>10.2f} ({len(positions)}只)")
    log(f"  总资产: {total_value:>10.2f}")
    log(f"  累计收益: {total_pct:>+7.2f}%")

    if positions:
        log(f"\n  持仓明细:")
        for p in positions:
            cur = quotes.get(p["code"], {}).get("price", p["buy_price"])
            pct = (cur - p["buy_price"]) / p["buy_price"] * 100
            log(f"    {p['code']} {p['name']:10s} {p['buy_date']} 买入¥{p['buy_price']:.2f} "
                f"现¥{cur:.2f} ({pct:+.2f}%) {p['shares']}股")

    log(f"\n✅ 今日模拟完成")

    # ---- 7. 生成HTML日报 ----
    # 获取买入分析用于日报
    buy_analysis = []
    if len(positions) < MAX_POSITIONS:
        held_codes = {p["code"] for p in positions}
        all_candidates = screen_candidates()
        candidates = [c for c in all_candidates if c["code"] not in held_codes]
        if candidates:
            buy_analysis = analyze_candidates(candidates[:8], quotes)
    generate_html_report(state, today, total_value, positions, quotes, buy_analysis)


# ============================================================
# HTML日报生成
# ============================================================
def generate_html_report(state, today, total_value, positions, quotes, buy_analysis=None):
    if buy_analysis is None:
        buy_analysis = []
    total_pct = (total_value / INITIAL_CAPITAL - 1) * 100
    trade_log = state["trade_log"]
    daily_snapshots = state["daily_snapshots"]

    # 交易流水
    trade_rows = ""
    sell_done = [t for t in trade_log if t["action"] == "卖出"]
    win = sum(1 for t in sell_done if t.get("pnl", 0) > 0)
    lose = sum(1 for t in sell_done if t.get("pnl", 0) <= 0)

    for t in trade_log[-50:]:
        color = "#60a5fa" if t["action"] == "买入" else ("#34d399" if t.get("pnl", 0) > 0 else "#f87171")
        tag = "买入" if t["action"] == "买入" else ("盈利" if t.get("pnl", 0) > 0 else "亏损")
        pnl_str = "-" if t["action"] == "买入" else f"{t.get('pnl', 0):+.0f}"
        trade_rows += f"<tr><td style='color:#888'>{t['date'][5:]}</td>" \
                      f"<td><span class='tag tag-{tag}'>{t['action']}</span></td>" \
                      f"<td>{t['code']}</td><td>{t['name']}</td>" \
                      f"<td>¥{t.get('buy_price', 0):.2f}</td><td>{t['shares']}</td>" \
                      f"<td style='color:{color};font-weight:bold'>{pnl_str}</td>" \
                      f"<td style='color:#555;font-size:10px'>{t.get('reason','')}</td></tr>"

    snap_data = json.dumps(daily_snapshots, ensure_ascii=False)

    # 板块热度
    board_html = ""
    try:
        with open(os.path.join(BASE_DIR, "tracking_history", "latest.json")) as f:
            bd = json.load(f)
        for b in bd.get("board_ranking", [])[:6]:
            bar_color = "#34d399" if b.get("pct_5d", 0) >= 0 else "#f87171"
            bw = max(min(b['score'], 100), 5)
            board_html += f"<div><span style='width:90px;display:inline-block;color:#ffd700'>{b['name'][:8]}</span>" \
                          f"<span style='width:30px;display:inline-block;text-align:right;color:#60a5fa'>{b['score']}</span>" \
                          f"<div class='bar-bg' style='display:inline-block;width:100px;margin-left:4px'>" \
                          f"<div class='bar' style='width:{bw}%;background:{bar_color}'></div></div>" \
                          f"<span style='margin-left:4px;color:{bar_color};font-size:11px'>{b.get('pct_5d',0):+.1f}%</span></div>"
    except:
        board_html = "<div style='color:#555'>暂无板块数据</div>"

    # 持仓
    pos_html = "<div style='color:#555;font-size:12px'>空仓</div>"
    if positions:
        pos_html = "<table><tr><th>代码</th><th>名称</th><th>买入日</th><th>买入价</th><th>现价</th><th>涨幅</th><th>股数</th><th>市值</th><th>技术指标</th></tr>"
        for p in positions:
            cur = quotes.get(p["code"], {}).get("price", p["buy_price"])
            pct = (cur - p["buy_price"]) / p["buy_price"] * 100
            mkt_val = cur * p["shares"]
            # 找买入时的技术指标
            tech_icons = ""
            for a in buy_analysis:
                if a['code'] == p['code']:
                    tech = a.get('tech', {})
                    mf = a.get('moneyflow', {})
                    parts = []
                    if tech.get('macd', 0) > 0: parts.append("MACD+")
                    if tech.get('kdj_j', 50) > 80: parts.append("KDJ↑")
                    if tech.get('rsi_6', 50) > 60: parts.append("RSI↑")
                    if mf.get('net_mf', 0) > 0: parts.append("主力+")
                    tech_icons = "  ".join(parts) if parts else ""
                    break
            pos_html += f"<tr><td>{p['code']}</td><td>{p['name']}</td><td>{p['buy_date'][5:]}</td>" \
                        f"<td>¥{p['buy_price']:.2f}</td><td>¥{cur:.2f}</td>" \
                        f"<td style='color:{'#34d399' if pct>=0 else '#f87171'};font-weight:bold'>{pct:+.2f}%</td>" \
                        f"<td>{p['shares']}</td><td>¥{mkt_val:,.0f}</td>" \
                        f"<td style='color:#888;font-size:9px'>{tech_icons}</td></tr>"
        pos_html += "</table>"

    # 买入分析HTML - 带评分条
    analysis_html = "<div style='color:#555'>今日无候选股（可能已满仓或非交易日）</div>"
    if buy_analysis:
        analysis_html = "<table><tr><th>评级</th><th>名称</th><th>涨幅</th><th>题材</th><th>趋势</th><th>评分</th><th>理由</th><th>技术/资金</th></tr>"
        for a in buy_analysis[:15]:
            score_color = "#ff4d4f" if a['score'] >= 75 else ("#34d399" if a['score'] >= 50 else "#f59e0b")
            rating_color = "#ff4d4f" if a['rating'].startswith("⭐") else ("#34d399" if a['rating'].startswith("✅") else "#f59e0b")
            trend_color = "#34d399" if a['trend'] == "上升" else ("#f87171" if a['trend'] == "下降" else "#f59e0b")
            inc_color = "#ff4d4f" if a['inc'] >= 5 else "#f59e0b"

            tech = a.get('tech', {})
            mf = a.get('moneyflow', {})
            micro_parts = []
            if tech.get('macd', 0) > 0: micro_parts.append("MACD+")
            if tech.get('rsi_6', 50) > 60: micro_parts.append("RSI↑")
            if tech.get('kdj_j', 50) > 80: micro_parts.append("KDJ↑")
            if tech.get('cci', 0) > 100: micro_parts.append("CCI突破")
            if mf.get('net_mf', 0) > 0: micro_parts.append("主力净流入")
            micro_str = " | ".join(micro_parts[:3]) if micro_parts else ""

            bar_w = max(min(a['score'], 100), 5)

            analysis_html += f"<tr><td style='color:{rating_color};font-weight:bold'>{a['rating'][:4]}</td>" \
                            f"<td style='color:#ffd700'>{a['name']}<br><span style='color:#888;font-size:9px'>{a['code']}</span></td>" \
                            f"<td style='color:{inc_color}'>{a['inc']:+.1f}%</td>" \
                            f"<td style='color:#60a5fa'>{a['board_score']:.0f}</td>" \
                            f"<td style='color:{trend_color}'>{a['trend']}</td>" \
                            f"<td><div style='display:flex;align-items:center;gap:4px'><span style='color:{score_color};font-weight:bold;min-width:24px'>{a['score']}</span>" \
                            f"<div class='bar-bg' style='width:50px'><div class='bar' style='width:{bar_w}%;background:{score_color}'></div></div></div></td>" \
                            f"<td style='color:#888;font-size:10px;max-width:120px'>{a['reasons'][:30]}</td>" \
                            f"<td style='color:#888;font-size:9px;max-width:120px'>{micro_str}</td></tr>"
        analysis_html += "</table>"

    profit_trades = [t for t in trade_log if t["action"] == "卖出" and t.get("pnl", 0) > 0]
    loss_trades = [t for t in trade_log if t["action"] == "卖出" and t.get("pnl", 0) <= 0]
    profit_sum = sum(t.get("pnl", 0) for t in profit_trades)
    loss_sum = abs(sum(t.get("pnl", 0) for t in loss_trades))
    win_rate = win / max(win + lose, 1) * 100

    # ===== 用字符串拼接构建HTML，避免f-string花括号冲突 =====
    HT = ""

    # Head
    HT += "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n"
    HT += "<meta charset='UTF-8'>\n<meta name='viewport' content='width=device-width,initial-scale=1.0'>\n"
    HT += f"<title>实盘模拟日报 | {today}</title>\n"
    HT += "<script src='echarts.min.js'></script>\n"
    HT += """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e1a;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:12px;max-width:1100px;margin:0 auto}
h1{color:#ffd700;font-size:20px;text-align:center;margin-bottom:2px;letter-spacing:1px}
.sub{color:#666;text-align:center;font-size:11px;margin-bottom:12px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
.card{background:linear-gradient(135deg,#111827,#1a1a3e);border-radius:8px;padding:12px;border:1px solid #1e3a5f}
.card h2{color:#ffd700;font-size:13px;margin-bottom:8px}
.stat{text-align:center;padding:10px}
.stat .v{font-size:20px;font-weight:bold;margin-bottom:1px}
.stat .l{font-size:10px;color:#888}
.chart{width:100%;height:220px}
.chart-md{height:180px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{background:#1e3a5f;color:#ffd700;padding:4px 5px;text-align:left;position:sticky;top:0;white-space:nowrap}
td{padding:3px 5px;border-bottom:1px solid #111827;vertical-align:top}
.tag{display:inline-block;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:bold}
.tag-买入{background:#1e3a5f33;color:#60a5fa;border:1px solid #1e3a5f66}
.tag-盈利{background:#065f4633;color:#34d399;border:1px solid #065f4666}
.tag-亏损{background:#7f1d1d33;color:#f87171;border:1px solid #7f1d1d66}
.bar-bg{height:5px;background:#1e3a5f;border-radius:3px;margin:3px 0;overflow:hidden}
.bar{height:100%;border-radius:3px}
.flow-box{max-height:400px;overflow-y:auto}
.summary-row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #111827;font-size:12px}
.summary-row:last-child{border:none}
.summary-label{color:#888}
.summary-val{font-weight:bold}
@media(max-width:640px){.grid4,.grid3,.grid2{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>

<h1>📊 实盘模拟 · TODAY_PLACEHOLDER</h1>
<p class="sub">策略A: 前日涨幅2~9%买入 | 止损-3%/止盈+5%/持仓5日 | asharehub技术因子增强</p>

"""

    # 顶部指标卡
    HT += '<div class="grid4">\n'
    HT += f'  <div class="card stat"><div class="v" style="color:#ffd700">{total_value:,.0f}</div><div class="l">总资产 (¥)</div></div>\n'
    HT += f'  <div class="card stat"><div class="v" style="color:#34d399">{total_pct:+.2f}%</div><div class="l">累计收益</div></div>\n'
    HT += f'  <div class="card stat"><div class="v" style="color:#60a5fa">{len(positions)}只</div><div class="l">当前持仓</div></div>\n'
    HT += f'  <div class="card stat"><div class="v" style="color:#f59e0b">{win}胜/{lose}负</div><div class="l">交易成绩</div></div>\n'
    HT += '</div>\n'

    # 第二行：净值曲线 + 日收益柱状图
    HT += '<div class="grid2">\n'
    HT += '  <div class="card"><h2>📈 净值曲线</h2><div id="eqChart" class="chart"></div></div>\n'
    HT += '  <div class="card"><h2>📊 日收益</h2><div id="pnlChart" class="chart-md"></div></div>\n'
    HT += '</div>\n'

    # 第三行：板块热度 + 交易统计
    HT += '<div class="grid2">\n'
    HT += f'  <div class="card"><h2>🔥 板块热度 TOP6</h2><div style="font-size:12px;line-height:2.2">{board_html}</div></div>\n'

    max_drawdown = 0
    peak = INITIAL_CAPITAL
    for s in daily_snapshots:
        tv = s["total_value"]
        if tv > peak:
            peak = tv
        dd = (peak - tv) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    HT += f'  <div class="card"><h2>📋 交易统计</h2><div style="font-size:12px;line-height:2">'
    HT += f'<div class="summary-row"><span class="summary-label">总交易次数</span><span class="summary-val">{len(trade_log)}</span></div>'
    HT += f'<div class="summary-row"><span class="summary-label">胜率</span><span class="summary-val" style="color:{"#34d399" if win_rate>=50 else "#f87171"}">{win_rate:.1f}%</span></div>'
    HT += f'<div class="summary-row"><span class="summary-label">总盈利</span><span class="summary-val" style="color:#34d399">+¥{profit_sum:,.0f}</span></div>'
    HT += f'<div class="summary-row"><span class="summary-label">总亏损</span><span class="summary-val" style="color:#f87171">-¥{loss_sum:,.0f}</span></div>'
    HT += f'<div class="summary-row"><span class="summary-label">最大回撤</span><span class="summary-val" style="color:#f59e0b">{max_drawdown:.2f}%</span></div>'
    HT += f'<div class="summary-row"><span class="summary-label">累计收益</span><span class="summary-val" style="color:#34d399">+{total_value - INITIAL_CAPITAL:,.0f} ({total_pct:+.2f}%)</span></div>'
    HT += '</div></div>\n'
    HT += '</div>\n'

    # 持仓表
    HT += f'<div class="card"><h2>💰 当前持仓</h2><div style="font-size:12px">{pos_html}</div></div>\n'

    # 买入分析
    HT += '<div class="card"><h2>🔍 今日买入分析 · TOP候选</h2>'
    HT += '<div style="font-size:11px;color:#888;margin-bottom:4px">评分: ⭐≥75 强烈推荐 | ✅≥50 推荐 | 👀≥25 关注 | ⬇<25 一般</div>'
    HT += f'<div style="font-size:12px;line-height:2">{analysis_html}</div></div>\n'

    # 交易流水
    HT += f'<div class="card"><h2>📋 交易流水</h2><div class="flow-box"><table><tr><th>日期</th><th>操作</th><th>代码</th><th>名称</th><th>价格</th><th>数量</th><th>盈亏</th><th>说明</th></tr>{trade_rows}</table></div></div>\n'

    # 页脚
    HT += f'<p class="sub" style="margin-top:14px">⚠️ 模拟交易，仅供参考 | 数据源: asharehub/聚合数据/新浪财经 | 更新时间: {datetime.now().strftime("%H:%M")}</p>\n'

    # ===== ECharts JavaScript =====
    HT += """<script>
var snapshots = SNAP_DATA_PLACEHOLDER;
var dates = snapshots.map(function(s){return s.date.slice(5);});
var vals = snapshots.map(function(s){return s.total_value;});
var dailyPcts = snapshots.map(function(s){return s.daily_pct;});

// 净值曲线
var ec1 = echarts.init(document.getElementById('eqChart'));
ec1.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',formatter:function(p){
    var i=p[0].dataIndex,s=snapshots[i];
    return '<b>'+s.date+'</b><br/>总资产: ¥'+s.total_value.toLocaleString()+'<br/>日收益: '+(s.daily_pct>=0?'+':'')+s.daily_pct.toFixed(2)+'%<br/>累计: '+(s.total_pct>=0?'+':'')+s.total_pct.toFixed(2)+'%<br/>持仓: '+s.position_count+'只';
  }}},
  grid:{left:'8%',right:'5%',top:'12%',bottom:'15%'},
  xAxis:{type:'category',data:dates,axisLabel:{fontSize:10,color:'#888',rotate:30},axisLine:{lineStyle:{color:'#1e3a5f'}}},
  yAxis:{type:'value',min:95000,name:'总资产',nameTextStyle:{color:'#555'},splitLine:{lineStyle:{color:'#111827'}},axisLabel:{color:'#888',formatter:'¥{value}'}},
  series:[{
    type:'line',data:vals,smooth:true,
    lineStyle:{width:2.5,color:'#34d399'},
    areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'#34d39933'},{offset:1,color:'#34d39900'}]}},
    markLine:{data:[{yAxis:100000,lineStyle:{color:'#555',type:'dashed'},label:{formatter:'本金10万',color:'#888',fontSize:10}}]},
    label:{show:true,position:'top',fontSize:9,color:'#ffd700',formatter:function(p){return ((p.value/100000-1)*100).toFixed(1)+'%';}}
  }]
});

// 日收益柱状图
var ec2 = echarts.init(document.getElementById('pnlChart'));
var barColors = dailyPcts.map(function(v){return v>=0?'#34d399':'#f87171';});
ec2.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',formatter:function(p){
    var i=p[0].dataIndex,s=snapshots[i];
    return '<b>'+s.date+'</b><br/>日收益: '+(s.daily_pct>=0?'+':'')+s.daily_pct.toFixed(2)+'%<br/>总资产: ¥'+s.total_value.toLocaleString();
  }},
  grid:{left:'6%',right:'5%',top:'10%',bottom:'15%'},
  xAxis:{type:'category',data:dates,axisLabel:{fontSize:9,color:'#888',rotate:30},axisLine:{lineStyle:{color:'#1e3a5f'}}},
  yAxis:{type:'value',name:'日收益%',nameTextStyle:{color:'#555'},splitLine:{lineStyle:{color:'#111827'}},axisLabel:{color:'#888'}},
  series:[{
    type:'bar',data:vals.map(function(v,i){return {value:dailyPcts[i],itemStyle:{color:barColors[i]}};}),
    barWidth:'60%',
    label:{show:true,position:'top',fontSize:9,formatter:function(p){return (p.value>=0?'+':'')+p.value.toFixed(2)+'%';},color:'#888'}
  }]
});
</script>
</body>
</html>"""

    # 替换占位符
    html = HT
    html = html.replace('TODAY_PLACEHOLDER', today)
    html = html.replace('SNAP_DATA_PLACEHOLDER', snap_data)

    path = os.path.join(VIZ_DIR, f"stock_daily_{today.replace('-','')}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"📄 日报已生成: {path}")

# ============================================================
# 初始化（首次运行）
# ============================================================
def init_simulation():
    """首次运行初始化"""
    log("首次运行，初始化模拟交易系统...")
    state = {
        "capital": INITIAL_CAPITAL,
        "positions": [],
        "trade_log": [],
        "daily_snapshots": [{
            "date": get_today_str(),
            "capital": INITIAL_CAPITAL,
            "position_value": 0,
            "total_value": INITIAL_CAPITAL,
            "daily_pct": 0,
            "total_pct": 0,
            "position_count": 0,
        }],
        "last_date": None,
    }
    save_state(state)
    log(f"✅ 初始化完成，初始资金 ¥{INITIAL_CAPITAL:,}")


# ============================================================
# 主入口
# ============================================================
def main():
    # 首次运行初始化
    if not os.path.exists(STATE_FILE):
        init_simulation()

    try:
        run_daily()
    except Exception as e:
        log(f"❌ 运行异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
