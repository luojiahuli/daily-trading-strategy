#!/usr/bin/env python3
"""
每日实盘模拟交易系统 + 回测系统

策略: 趋势跟踪 + 时间序列模型 + 动态调仓
  - 买入: 综合信号(均线趋势30%+价格位置20%+RSI 20%+模型预测15%+量能15%)>0.2 触发
  - 卖出: 信号<-0.3减半仓, <-0.5清仓, 硬止损-5%, 最大持有20日
  - 仓位: 动态仓位(信号强度*0.5), 最多同时5只, 单只≤25%

每日流程 (15:30 cron):
  1. 获取今日所有持仓股行情 + 历史K线
  2. 计算综合信号 -> 减仓/清仓/到期处理
  3. 扫描板块 -> 候选股买入分析
  4. 信号驱动的买入（动态仓位）
  5. 更新资金曲线 + 写入数据库
  6. 自动回测热门板块个股（趋势跟踪策略）
  7. 回测结果写入数据库
  8. 生成H5日报 -> viz_output/
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
MAX_POSITIONS = 5
STOP_LOSS = -5.0       # 硬止损（作为最后防线，不再作为主策略）
MAX_HOLD = 20           # 最长持有20日
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
    api_key = os.environ.get("JUHE_API_KEY", "")
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
        ah = _ASH(api_key=os.environ.get("ASHAREHUB_API_KEY", ""))
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
# 时间序列模型（趋势预测）
# ============================================================
def train_trend_model(stock_data: list) -> dict:
    """
    基于历史K线训练简单趋势预测模型
    返回一个包含模型预测能力的字典
    """
    if not stock_data or len(stock_data) < 30:
        return {"direction": 0, "confidence": 0, "pred_pct": 0}
    
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        import numpy as np
        
        # 构造特征：过去N日的价格变化率、波动率、相对位置
        closes = np.array([k["close"] for k in stock_data])
        highs = np.array([k["high"] for k in stock_data])
        lows = np.array([k["low"] for k in stock_data])
        volumes = np.array([k.get("volume", 0) for k in stock_data])
        
        n = len(closes)
        X, y = [], []
        for i in range(20, n - 1):
            features = []
            # 过去5日收益率
            for lag in [1, 2, 3, 5, 10]:
                if i >= lag:
                    features.append((closes[i] - closes[i-lag]) / closes[i-lag] * 100)
                else:
                    features.append(0)
            # 过去5日波动率
            features.append((max(highs[i-5:i]) - min(lows[i-5:i])) / closes[i] * 100)
            # 相对位置 (MA偏离)
            ma5 = np.mean(closes[i-5:i]) if i >= 5 else closes[i]
            ma10 = np.mean(closes[i-10:i]) if i >= 10 else closes[i]
            ma20 = np.mean(closes[i-20:i]) if i >= 20 else closes[i]
            features.append((closes[i] - ma5) / ma5 * 100)
            features.append((closes[i] - ma10) / ma10 * 100)
            features.append((closes[i] - ma20) / ma20 * 100)
            # RSI
            delta = np.diff(closes[i-14:i+1]) if i >= 14 else np.diff(closes[:i+1])
            gains = np.mean(delta[delta > 0]) if len(delta[delta > 0]) > 0 else 0
            losses = abs(np.mean(delta[delta < 0])) if len(delta[delta < 0]) > 0 else 1
            rsi = 100 - 100 / (1 + gains / max(losses, 0.01))
            features.append(rsi)
            # 成交量变化
            if i >= 5:
                vol_ratio = volumes[i] / max(np.mean(volumes[i-5:i]), 1)
                features.append(vol_ratio)
            else:
                features.append(1)
            
            X.append(features)
            # 目标：未来3日平均收益
            if i + 3 < n:
                future_ret = (closes[i+3] - closes[i]) / closes[i] * 100
            elif i + 1 < n:
                future_ret = (closes[i+1] - closes[i]) / closes[i] * 100
            else:
                future_ret = 0
            y.append(future_ret)
        
        if len(X) < 10:
            return {"direction": 0, "confidence": 0, "pred_pct": 0}
        
        X, y = np.array(X), np.array(y)
        split = int(len(X) * 0.8)
        X_train, y_train = X[:split], y[:split]
        X_test, y_test = X[split:], y[split:]
        
        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=3, learning_rate=0.1,
            min_samples_leaf=5, random_state=42
        )
        model.fit(X_train, y_train)
        
        # 预测次日
        last_features = X[-1:].reshape(1, -1)
        pred = float(model.predict(last_features)[0])
        
        # 评估准确率
        y_pred = model.predict(X_test)
        direction_acc = np.mean((y_pred > 0) == (y_test > 0)) * 100
        
        return {
            "direction": 1 if pred > 0 else -1,
            "confidence": min(abs(pred) * 5, 80) if direction_acc > 45 else 20,
            "pred_pct": round(pred, 2),
            "accuracy": round(direction_acc, 1),
            "model": model
        }
    except Exception as e:
        log(f"  ⚠ 模型训练失败: {e}")
        return {"direction": 0, "confidence": 0, "pred_pct": 0}


# ============================================================
# 综合信号评分 (替代止盈止损)
# ============================================================
def calculate_signal(code: str, kline: list, model_pred: dict) -> float:
    """
    增强信号计算 — 使用多因子模型（如果可用）+ 外部因子 + 技术信号
    兼容原有接口，返回 -1 ~ 1 的信号
    """
    if not kline or len(kline) < 10:
        return 0
    
    # 尝试使用增强信号（如果有外部因子和多因子模型）
    try:
        from factor_models import compute_enhanced_signal, load_external_factors, get_external_factor_for_date
        
        # 获取外部因子
        ext_df = load_external_factors()
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 构造伪 external_factors 结构
        ext = get_external_factor_for_date(today_str, ext_df)
        external_factors = {"factor_vector": [ext["sentiment"], ext["policy_score"], ext["cctv_impact"]]}
        
        # 如果有模型结果，传入；否则只传外部因子+技术信号
        mf_result = model_pred.get("_multi_factor_result")
        
        signal, reasons, pred_pct = compute_enhanced_signal(kline, mf_result, external_factors)
        return signal
    except Exception:
        pass
    
    # 降级到原始信号计算
    closes = [k["close"] for k in kline]
    highs = [k["high"] for k in kline]
    lows = [k["low"] for k in kline]
    last = kline[-1]
    cur_close = last["close"]
    
    signal = 0.0
    
    # 1. 均线趋势
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    if cur_close > ma5 > ma10:
        signal += 0.30
    elif cur_close < ma5 < ma10:
        signal -= 0.30
    elif cur_close > ma5:
        signal += 0.10
    else:
        signal -= 0.10
    
    # 2. 价格位置
    period_high = max(highs[-20:])
    period_low = min(lows[-20:])
    pos = (cur_close - period_low) / max(period_high - period_low, 0.01)
    if pos < 0.2:
        signal += 0.20
    elif pos > 0.8:
        signal -= 0.20
    
    # 3. RSI
    if len(closes) >= 15:
        delta = [closes[i] - closes[i-1] for i in range(-14, 0)]
        gains = sum(d for d in delta if d > 0) / 14
        losses = abs(sum(d for d in delta if d < 0)) / 14
        rsi = 100 - 100 / (1 + gains / max(losses, 0.01))
        if rsi < 30:
            signal += 0.20
        elif rsi > 70:
            signal -= 0.20
    
    # 4. 模型预测
    pred = model_pred.get("pred_pct", 0)
    conf = model_pred.get("confidence", 0)
    if pred > 0.5:
        signal += 0.15 * (conf / 50)
    elif pred < -0.5:
        signal -= 0.15 * (conf / 50)
    
    # 5. 成交量
    if len(kline) >= 5:
        volumes = [k.get("volume", 0) for k in kline]
        vol_ratio = volumes[-1] / max(sum(volumes[-6:-1]) / 5, 1)
        if vol_ratio > 1.5 and signal > 0:
            signal += 0.15
        elif vol_ratio > 1.5 and signal < 0:
            signal -= 0.15
        elif vol_ratio < 0.5 and signal > 0:
            signal += 0.05
    
    return max(min(signal, 1.0), -1.0)


# ============================================================
# 交易引擎（重写版 — 趋势跟踪 + 动态调仓）
# ============================================================
def run_daily():
    today = get_today_str()
    log(f"\n{'='*60}")
    log(f"📊 每日实盘模拟(趋势跟踪) | {today}")
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

    # 获取候选股
    all_candidates = screen_candidates()[:8]
    all_codes = list(set(
        [p["code"] for p in positions] +
        [c["code"] for c in all_candidates]
    ))

    if not all_codes:
        log("⚠️ 无候选股，可能非交易日")
        return

    log(f"获取 {len(all_codes)} 只个股行情...")
    kline_cache = {}  # 缓存K线数据
    model_cache = {}  # 缓存模型预测

    for code in all_codes:
        kline_cache[code] = get_kline(code, days=60)
        if kline_cache[code] and len(kline_cache[code]) >= 30:
            model_cache[code] = train_trend_model(kline_cache[code])
        else:
            model_cache[code] = {"direction": 0, "confidence": 0, "pred_pct": 0}
    
    log(f"模型预测完成: {len(model_cache)} 只")
    for code, pred in model_cache.items():
        if pred.get("pred_pct", 0) != 0:
            acc = pred.get("accuracy", 0)
            log(f"  {code}: 预测{pred['pred_pct']:+.2f}% (准确率{acc:.0f}%)")

    # ---- 1. 计算各持仓信号，决定加减仓 ----
    total_risk = 0  # 整体组合风险
    for p in positions[:]:
        code = p["code"]
        kl = kline_cache.get(code)
        model_pred = model_cache.get(code, {"direction": 0, "confidence": 0, "pred_pct": 0})
        
        if not kl or len(kl) < 10:
            continue
        
        signal = calculate_signal(code, kl, model_pred)
        cur_price = kl[-1]["close"]
        hold_days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(p["buy_date"], "%Y-%m-%d")).days
        unrealized_pnl = (cur_price - p["buy_price"]) / p["buy_price"] * 100
        
        buy_cost = p["buy_price"] * p["shares"] * (1 + COMMISSION_RATE)
        
        def sell_proceed(price):
            return price * p["shares"] * (1 - COMMISSION_RATE - STAMP_TAX)
        
        # 硬止损（最后防线，-5%）
        if unrealized_pnl <= STOP_LOSS:
            sp = sell_proceed(cur_price)
            pnl = sp - buy_cost
            trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(p["buy_price"], 2), "sell_price": round(cur_price, 2),
                             "shares": p["shares"], "pnl": round(pnl, 2), "pnl_pct": round(unrealized_pnl, 2),
                             "reason": f"硬止损{unrealized_pnl:.1f}%"})
            capital += sp
            log(f"🔴 止损 {p['name']}({code}) 持仓{hold_days}d {unrealized_pnl:.1f}%")
            positions.remove(p)
            continue
        
        # 信号驱动的减仓（趋势转弱）
        if signal < -0.3 and hold_days >= 3:
            # 减半仓
            sell_shares = int(p["shares"] * 0.5 / 100) * 100
            if sell_shares >= 100:
                sp = sell_proceed(cur_price)
                pnl_ratio = (cur_price - p["buy_price"]) / p["buy_price"] * 100
                trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                                 "buy_date": p["buy_date"], "hold_days": hold_days,
                                 "buy_price": round(p["buy_price"], 2), "sell_price": round(cur_price, 2),
                                 "shares": -sell_shares, "pnl": round(sp - buy_cost * 0.5, 2), 
                                 "pnl_pct": round(pnl_ratio, 2),
                                 "reason": f"趋势转弱减仓(signal={signal:.2f})"})
                capital += sp
                p["shares"] -= sell_shares
                p["buy_price"] = p["buy_price"]  # 成本不变
                log(f"🟡 减仓 {p['name']}({code}) {sell_shares}股 signal={signal:.2f}")
        
        # 信号驱动的清仓
        if signal < -0.6:
            sp = sell_proceed(cur_price)
            pnl = sp - buy_cost
            trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(p["buy_price"], 2), "sell_price": round(cur_price, 2),
                             "shares": p["shares"], "pnl": round(pnl, 2), "pnl_pct": round(unrealized_pnl, 2),
                             "reason": f"强卖出信号清仓(signal={signal:.2f})"})
            capital += sp
            log(f"🔴 清仓 {p['name']}({code}) signal={signal:.2f}")
            positions.remove(p)
            continue
        
        # 到期卖出（最长持有20日）
        if hold_days >= MAX_HOLD:
            sp = sell_proceed(cur_price)
            pnl = sp - buy_cost
            trade_log.append({"date": today, "action": "卖出", "code": code, "name": p["name"],
                             "buy_date": p["buy_date"], "hold_days": hold_days,
                             "buy_price": round(p["buy_price"], 2), "sell_price": round(cur_price, 2),
                             "shares": p["shares"], "pnl": round(pnl, 2), "pnl_pct": round(unrealized_pnl, 2),
                             "reason": f"持仓到期{MAX_HOLD}d"})
            capital += sp
            log(f"⏰ 到期卖出 {p['name']}({code}) {unrealized_pnl:+.1f}%")
            positions.remove(p)
        
        # 累积组合风险
        total_risk += abs(signal)
    
    # ---- 2. 买入分析 & 买入 ----
    buy_analysis = []
    if len(positions) < MAX_POSITIONS:
        held_codes = {p["code"] for p in positions}
        new_candidates = [c for c in all_candidates if c["code"] not in held_codes]
        
        # 计算买入信号
        for c in new_candidates:
            code = c["code"]
            kl = kline_cache.get(code)
            model_pred = model_cache.get(code, {"direction": 0, "confidence": 0, "pred_pct": 0})
            
            if not kl or len(kl) < 10:
                continue
            
            signal = calculate_signal(code, kl, model_pred)
            pred_pct = model_pred.get("pred_pct", 0)
            
            # 综合评分 (0-100)
            score = max(0, min(100, (signal + 1) * 50 + max(0, pred_pct) * 2))
            
            reasons = []
            if signal > 0.3: reasons.append(f"信号强度{signal:.2f}")
            if pred_pct > 0.5: reasons.append(f"模型预测{pred_pct:+.2f}%")
            
            buy_analysis.append({
                **c,
                "score": int(score),
                "signal": round(signal, 2),
                "pred_pct": pred_pct,
                "reasons": " | ".join(reasons) if reasons else "趋势跟踪",
                "rating": "⭐ 强烈推荐" if score >= 70 else ("✅ 推荐" if score >= 50 else ("👀 关注" if score >= 30 else "⬇ 一般")),
            })
        
        buy_analysis.sort(key=lambda x: -x["score"])
        
        log(f"\n  📊 买入分析 TOP {min(len(buy_analysis), 5)}:")
        for a in buy_analysis[:5]:
            log(f"    {a['rating']} {a['name']}({a['code']}) "
                f"信号{a['signal']:+.2f} 预测{a['pred_pct']:+.2f}% 评分{a['score']} | {a['reasons']}")
        
        # 买入
        for a in buy_analysis:
            if len(positions) >= MAX_POSITIONS:
                break
            if a["signal"] < 0.2:
                continue  # 信号不足不买入
            
            code = a["code"]
            kl = kline_cache.get(code)
            if not kl:
                continue
            buy_price = kl[-1]["close"]
            if buy_price <= 0:
                continue
            
            # 动态仓位：信号越强买越多
            slot_pct = min(a["signal"] * 0.5, 0.25)
            slot_value = capital * slot_pct
            shares = int(slot_value / buy_price / 100) * 100
            if shares < 100:
                shares = 100
            cost = buy_price * shares * (1 + COMMISSION_RATE)
            if cost > capital:
                continue
            
            capital -= cost
            positions.append({
                "code": code, "name": a["name"], "board": a.get("board", ""),
                "buy_date": today, "buy_price": round(buy_price, 2),
                "shares": shares,
            })
            trade_log.append({"date": today, "action": "买入", "code": code, "name": a["name"],
                             "buy_date": today, "hold_days": 0,
                             "buy_price": round(buy_price, 2), "sell_price": 0,
                             "shares": shares, "pnl": 0, "pnl_pct": 0, 
                             "reason": f"趋势买入(signal={a['signal']:.2f},pred={a['pred_pct']:.1f}%)"})
            log(f"🟢 买入 {a['name']}({code}) @ {buy_price:.2f} × {shares}股 = {cost:.2f} | 信号{a['signal']:.2f}")

    # ---- 4. 更新快照 ----
    pos_value = 0
    for p in positions:
        kl = kline_cache.get(p["code"])
        cur_price = kl[-1]["close"] if kl and len(kl) > 0 else p["buy_price"]
        pos_value += p["shares"] * cur_price
    
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
            cur = p["buy_price"]
            kl = kline_cache.get(p["code"])
            if kl and len(kl) > 0:
                cur = kl[-1]["close"]
            pct = (cur - p["buy_price"]) / p["buy_price"] * 100
            log(f"    {p['code']} {p['name']:10s} {p['buy_date']} 买入¥{p['buy_price']:.2f} "
                f"现¥{cur:.2f} ({pct:+.2f}%) {p['shares']}股")

    log(f"\n✅ 今日模拟完成")

    # ---- 7. 生成HTML日报 ----
    generate_html_report(state, today, total_value, positions, kline_cache, buy_analysis)

    # ---- 8. 写入数据库 ----
    try:
        from trading_db import save_daily_snapshot, save_positions, save_trade
        
        # 计算最大回撤
        peak = INITIAL_CAPITAL
        max_dd = 0
        for s in daily_snapshots:
            if s["total_value"] > peak:
                peak = s["total_value"]
            dd = (peak - s["total_value"]) / peak * 100
            if dd > max_dd:
                max_dd = dd
        
        save_daily_snapshot(
            date=today, capital=capital, pos_value=pos_value,
            total_value=total_value, daily_pct=daily_pct,
            total_pct=total_pct, position_count=len(positions),
            max_drawdown=round(max_dd, 2)
        )
        
        # 持仓明细
        pos_for_db = []
        for p in positions:
            cur = p["buy_price"]
            kl = kline_cache.get(p["code"])
            if kl and len(kl) > 0:
                cur = kl[-1]["close"]
            pct = (cur - p["buy_price"]) / p["buy_price"] * 100
            pos_for_db.append({
                "code": p["code"], "name": p["name"], "board": p.get("board",""),
                "buy_date": p["buy_date"], "buy_price": p["buy_price"],
                "current_price": cur, "shares": p["shares"],
                "pnl_pct": round(pct, 2)
            })
        save_positions(today, pos_for_db)
        
        # 今日交易
        today_trades = [t for t in trade_log if t.get("date") == today]
        for t in today_trades:
            save_trade(t)
        
        log(f"💾 每日数据已写入数据库")
    except Exception as e:
        log(f"⚠️ 数据库写入失败: {e}")

    # ---- 9. 自动回测 ----
    try:
        run_daily_backtest()
    except Exception as e:
        log(f"⚠️ 自动回测失败: {e}")
    
    # ---- 10. 获取并保存每日因子数据 ----
    try:
        from factor_data import get_all_factors
        from trading_db import save_daily_factors, save_factor_effectiveness
        
        log("\n📡 获取外部因子数据...")
        factors = get_all_factors(today)
        
        save_daily_factors(
            date=today,
            sentiment=factors["factor_vector"][0],
            policy_score=factors["factor_vector"][1],
            cctv_impact=factors["factor_vector"][2],
            composite_score=factors["composite_score"],
            sentiment_source=factors.get("sentiment_source", ""),
            policy_source=factors.get("policy_source", ""),
            cctv_source=factors.get("cctv_source", ""),
            hot_topics=factors.get("sentiment", {}).get("hot_topics", []),
            policy_titles=[p.get("title","") for p in factors.get("policy", {}).get("policies", [])],
            cctv_headlines=[h.get("title","") for h in factors.get("cctv", {}).get("headlines", [])],
        )
        log(f"  ✅ 外部因子已保存: 舆情{factors['factor_vector'][0]:+.3f} 政策{factors['factor_vector'][1]:+.3f} 新闻联播{factors['factor_vector'][2]:+.3f}")
        
        # 保存因子有效性分析（如果有K线数据）
        try:
            from factor_models import analyze_factor_effectiveness, load_external_factors
            
            ext_df = load_external_factors()
            # 检查是否有足够的数据做分析
            factor_log = get_kline("603439", days=200)
            if factor_log and len(factor_log) >= 50:
                analysis = analyze_factor_effectiveness(factor_log, ext_df)
                if analysis:
                    save_factor_effectiveness(today, analysis)
                    effective = [(k, v["ic"]) for k, v in analysis.items() if v.get("is_effective")]
                    if effective:
                        log(f"  🧬 有效因子({len(effective)}个): {', '.join(k for k,_ in effective[:5])}")
        except Exception as e2:
            log(f"  ⚠️ 因子分析失败: {e2}")
        
        # 保存模型版本
        try:
            from factor_models import train_multi_factor_model
            from trading_db import save_model_version
            
            # 用所有K线数据训练多因子模型
            all_data_for_model = []
            for code, kl in kline_cache.items():
                if len(kl) >= 60:
                    all_data_for_model.extend(kl)
            
            if all_data_for_model:
                mf_result = train_multi_factor_model(all_data_for_model, ext_df if 'ext_df' in dir() else None)
                if mf_result.get("model") is not None:
                    version = f"v{today.replace('-','')}"
                    save_model_version(
                        version=version,
                        train_date=today,
                        accuracy=mf_result["accuracy"],
                        cv_r2=mf_result.get("cv_r2", 0),
                        feature_count=len(mf_result.get("feature_names", [])),
                        effective_factor_count=len(mf_result.get("effective_factors", [])),
                        effective_factors=mf_result.get("effective_factors", []),
                        feature_importance=mf_result.get("feature_importance", {}),
                        pred_pct=mf_result.get("pred_pct", 0),
                        data_span="latest",
                        stock_count=len(kline_cache),
                    )
                    log(f"  🧠 模型版本 {version} 已保存, 准确率{mf_result['accuracy']}%")
        except Exception as e3:
            log(f"  ⚠️ 模型版本保存失败: {e3}")
        
    except Exception as e:
        log(f"⚠️ 因子数据获取失败: {e}")
    
    log(f"\n{'='*50}\n")


# ============================================================
# HTML日报生成
# ============================================================
def generate_html_report(state, today, total_value, positions, kline_cache, buy_analysis=None):
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
            cur = p["buy_price"]
            kl = kline_cache.get(p["code"])
            if kl and len(kl) > 0:
                cur = kl[-1]["close"]
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
            # 兼容新旧两种格式的字段
            score_color = "#ff4d4f" if a.get('score', 50) >= 75 else ("#34d399" if a.get('score', 50) >= 50 else "#f59e0b")
            rating_color = "#ff4d4f" if str(a.get('rating', '')).startswith("⭐") else ("#34d399" if str(a.get('rating', '')).startswith("✅") else "#f59e0b")
            trend = a.get('trend', a.get('signal', 0) > 0.2 and "上升" or (a.get('signal', 0) < -0.2 and "下降" or "震荡"))
            trend_color = "#34d399" if trend in ("上升",) else ("#f87171" if trend == "下降" else "#f59e0b")
            inc = a.get('inc', a.get('pred_pct', 0))
            inc_color = "#ff4d4f" if inc >= 5 else "#f59e0b"

            tech = a.get('tech', {})
            mf = a.get('moneyflow', {})
            micro_parts = []
            if tech.get('macd', 0) > 0: micro_parts.append("MACD+")
            if tech.get('rsi_6', 50) > 60: micro_parts.append("RSI↑")
            if tech.get('kdj_j', 50) > 80: micro_parts.append("KDJ↑")
            if tech.get('cci', 0) > 100: micro_parts.append("CCI突破")
            if mf.get('net_mf', 0) > 0: micro_parts.append("主力净流入")
            micro_str = " | ".join(micro_parts[:3]) if micro_parts else ""

            bar_w = max(min(a.get('score', 50), 100), 5)

            analysis_html += f"<tr><td style='color:{rating_color};font-weight:bold'>{a['rating'][:4] if 'rating' in a else '-'}</td>" \
                            f"<td style='color:#ffd700'>{a.get('name','')}<br><span style='color:#888;font-size:9px'>{a.get('code','')}</span></td>" \
                            f"<td style='color:{inc_color}'>{inc:+.1f}%</td>" \
                            f"<td style='color:#60a5fa'>{a.get('board_score', a.get('signal', 0)*50+50):.0f}</td>" \
                            f"<td style='color:{trend_color}'>{trend}</td>" \
                            f"<td><div style='display:flex;align-items:center;gap:4px'><span style='color:{score_color};font-weight:bold;min-width:24px'>{a.get('score', 50)}</span>" \
                            f"<div class='bar-bg' style='width:50px'><div class='bar' style='width:{bar_w}%;background:{score_color}'></div></div></div></td>" \
                            f"<td style='color:#888;font-size:10px;max-width:120px'>{str(a.get('reasons', ''))[:30]}</td>" \
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
<p class=\"sub\">趋势跟踪+时间序列模型 | 行业/板块轮动选股 | 动态仓位管理 | 最大回撤可控</p>

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
# 回测整合（每日自动跑热门板块回测）
# ============================================================

def run_daily_backtest():
    """每日自动回测：热门板块个股的趋势跟踪策略回测，结果写入数据库"""
    import numpy as np
    from backtest_strategy import get_kline_data, train_model_for_backtest, run_backtest, calc_signal
    from stock_theme_analyzer import get_all_concept_boards, get_board_constituents
    
    today = get_today_str()
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M')}"
    
    log(f"\n📊 自动回测 — 热门板块个股（趋势跟踪策略）")
    log("=" * 50)
    
    # 获取热门板块
    boards = get_all_concept_boards(use_cache=True)
    target_codes = []
    seen = set()
    stock_info_map = {}
    
    for board in boards[:8]:
        bname = board["name"]
        cons = get_board_constituents(bname)
        if cons:
            for s in cons[:4]:
                code = s.get("代码", "")
                sname = s.get("名称", "")
                if code and code not in seen and not sname.startswith(("ST","*ST","退")):
                    seen.add(code)
                    target_codes.append(code)
                    stock_info_map[code] = {"name": sname, "board": bname}
        time.sleep(0.05)
    
    log(f"  候选股: {len(target_codes)} 只")
    
    # 获取K线
    kline_cache = {}
    for code in target_codes:
        kl = get_kline_data(code, days=500)
        if kl and len(kl) >= 60:
            kline_cache[code] = kl
        time.sleep(0.3)
    
    log(f"  K线获取: {len(kline_cache)} 只")
    
    if len(kline_cache) < 3:
        log("  ⚠ 数据不足，跳过回测")
        return
    
    # 训练全局模型
    all_data = []
    for kl in kline_cache.values():
        if len(kl) >= 100:
            all_data.extend(kl)
    model, model_acc, *_ = train_model_for_backtest(all_data)
    log(f"  模型准确率: {model_acc}%")
    
    # 跑回测
    results = []
    all_trades = []
    all_equity = []
    
    for code, kl in kline_cache.items():
        result = run_backtest(code, kl, model, model_acc, stock_info_map.get(code))
        if result:
            results.append(result)
            all_trades.extend(result["trades"])
            all_equity.append(result["equity_curve"])
    
    if not results:
        log("  ⚠ 无有效回测结果")
        return
    
    # 汇总
    wins = sum(1 for r in results if r["total_return"] > 0)
    avg_ret = np.mean([r["total_return"] for r in results])
    avg_dd = np.mean([r["max_drawdown"] for r in results])
    log(f"  回测 {len(results)} 只: 胜{wins}只 平均{avg_ret:+.2f}% 回撤{avg_dd:.1f}%")
    
    # 写入数据库
    from trading_db import (
        save_backtest_summary, save_backtest_stocks,
        save_backtest_trades, save_backtest_equity, save_daily_backtest_summary
    )
    save_backtest_summary(run_id, today, results, all_trades, model_acc)
    save_backtest_stocks(run_id, results)
    save_backtest_trades(run_id, all_trades)
    save_backtest_equity(run_id, all_equity)
    save_daily_backtest_summary(today, run_id, results)
    
    log(f"  ✅ 回测数据已写入数据库")
    return results


# ============================================================
# 主入口（增强版）
# ============================================================
def main():
    # 首次运行初始化
    if not os.path.exists(STATE_FILE):
        init_simulation()
    
    # 初始化数据库
    from trading_db import init_db as init_trading_db
    init_trading_db()
    
    try:
        run_daily()
    except Exception as e:
        log(f"❌ 运行异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
