#!/usr/bin/env python3
"""
回测模型 — 基于历史数据回测新策略（趋势跟踪+时间序列模型）
生成H5回测报告，保持与实盘日报一致的可视化风格
"""
import os, sys, json, time, re, warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIZ_DIR = os.path.join(BASE_DIR, "viz_output")
os.makedirs(VIZ_DIR, exist_ok=True)

INITIAL_CAPITAL = 100000
MAX_POSITIONS = 5
STOP_LOSS = -5.0
MAX_HOLD = 20
COMMISSION_RATE = 0.0003
STAMP_TAX = 0.001

sys.path.insert(0, BASE_DIR)


# ── 数据获取 ──
def get_kline_data(code: str, days: int = 500) -> list:
    """获取个股日K线"""
    try:
        symbol = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        import requests
        resp = requests.get(url, params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)},
                           headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=15)
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
                "high": float(d["high"]), "low": float(d["low"]), "volume": int(float(d.get("volume", 0)))
            })
        return records
    except:
        return None


# ── 时间序列模型（多因子版）──
def train_model_for_backtest(kline_data: list, use_external_factors: bool = True) -> tuple:
    """训练模型并返回(model, accuracy)，整体训练一次
    
    如果 use_external_factors=True，会加载外部因子（舆情/政策/新闻联播）
    返回: (model, accuracy, feature_names, feature_importance, effective_factors)
    """
    if use_external_factors:
        try:
            from factor_models import train_multi_factor_backtest_model, load_external_factors
            from factor_data import get_all_factors
            
            # 加载外部因子（不做回填，直接用已有的缓存）
            ext_df = load_external_factors()
            if ext_df is None or len(ext_df) < 3:
                # 因子数据不足时快速降级，不做回填（避免Tavily逐日请求超时）
                return _train_technical_model(kline_data)
            
            model, acc, fnames, fimp, effective = train_multi_factor_backtest_model(kline_data, ext_df)
            if model is not None:
                # 打印有效因子
                if effective:
                    print(f"  🧬 有效因子({len(effective)}个): {', '.join(effective[:8])}...")
                return model, acc, fnames, fimp, effective
        except Exception as e:
            print(f"  ⚠️ 多因子模型降级: {e}")
    
    # 降级到纯技术因子
    return _train_technical_model(kline_data)


def _train_technical_model(kline_data: list) -> tuple:
    """纯技术因子模型训练（原版逻辑）"""
    if not kline_data or len(kline_data) < 40:
        return None, 0, [], {}, []
    try:
        from sklearn.ensemble import GradientBoostingRegressor
        closes = np.array([k["close"] for k in kline_data])
        highs = np.array([k["high"] for k in kline_data])
        lows = np.array([k["low"] for k in kline_data])
        vols = np.array([k.get("volume", 0) for k in kline_data])
        n = len(closes)
        X, y = [], []
        for i in range(20, n - 3):
            feats = []
            for lag in [1, 2, 3, 5, 10]:
                feats.append((closes[i] - closes[max(0,i-lag)]) / max(closes[max(0,i-lag)], 0.01) * 100 if i>=lag else 0)
            feats.append((max(highs[i-5:i]) - min(lows[i-5:i])) / max(closes[i], 0.01) * 100)
            ma5 = np.mean(closes[i-5:i]) if i>=5 else closes[i]
            ma10 = np.mean(closes[i-10:i]) if i>=10 else closes[i]
            ma20 = np.mean(closes[max(0,i-20):i]) if i>=20 else closes[i]
            feats.extend([(closes[i]-ma5)/max(ma5,0.01)*100, (closes[i]-ma10)/max(ma10,0.01)*100, (closes[i]-ma20)/max(ma20,0.01)*100])
            delta = np.diff(closes[max(0,i-14):i+1])
            gains = np.mean(delta[delta>0]) if len(delta[delta>0])>0 else 0
            losses = abs(np.mean(delta[delta<0])) if len(delta[delta<0])>0 else 1
            rsi = 100 - 100 / (1 + gains/max(losses,0.01))
            feats.append(rsi)
            X.append(feats)
            future = (closes[i+1] - closes[i]) / closes[i] * 100
            y.append(future)
        if len(X) < 20:
            return None, 0, [], {}, []
        X, y = np.array(X), np.array(y)
        model = GradientBoostingRegressor(n_estimators=80, max_depth=3, learning_rate=0.1, random_state=42)
        model.fit(X, y)
        y_pred = model.predict(X)
        acc = np.mean((y_pred>0) == (y>0)) * 100
        fnames = [f"feat_{i}" for i in range(len(X[0]))]
        fimp = dict(zip(fnames, model.feature_importances_))
        return model, round(acc, 1), fnames, fimp, []
    except:
        return None, 0, [], {}, []


# ── 综合信号（带可解释性）──
def calc_signal(kl: list, model_pred: dict) -> tuple:
    """计算综合信号 -1 到 +1，返回 (signal, reasons_list)"""
    if not kl or len(kl) < 10:
        return 0, []
    closes = [k["close"] for k in kl]
    highs = [k["high"] for k in kl]
    lows = [k["low"] for k in kl]
    cur = kl[-1]["close"]
    s = 0.0
    reasons = []
    ma5 = sum(closes[-5:])/5
    ma10 = sum(closes[-10:])/10
    ma20 = sum(closes[-min(20,len(closes)):])/min(20,len(closes))
    if cur > ma5 > ma10:
        s += 0.30; reasons.append("均线多头排列")
    elif cur < ma5 < ma10:
        s -= 0.30; reasons.append("均线空头排列")
    elif cur > ma5:
        s += 0.10; reasons.append("站上5日均线")
    else:
        s -= 0.10; reasons.append("破5日均线")
    ph = max(highs[-20:])
    pl = min(lows[-20:])
    pos = (cur-pl)/max(ph-pl,0.01)
    if pos < 0.2:
        s += 0.20; reasons.append(f"价格低位({pos*100:.0f}%分位)")
    elif pos > 0.8:
        s -= 0.20; reasons.append(f"价格高位({pos*100:.0f}%分位)")
    if len(closes) >= 15:
        delta = [closes[i]-closes[i-1] for i in range(-14,0)]
        g = sum(d for d in delta if d>0)/14
        ls = abs(sum(d for d in delta if d<0))/14
        rsi = 100 - 100/(1+g/max(ls,0.01))
        if rsi < 30:
            s += 0.20; reasons.append(f"RSI超卖({rsi:.0f})")
        elif rsi > 70:
            s -= 0.20; reasons.append(f"RSI超买({rsi:.0f})")
        else:
            reasons.append(f"RSI中性({rsi:.0f})")
    pred = model_pred.get("pred_pct", 0)
    if pred > 0.5:
        s += 0.15; reasons.append(f"模型预测涨{pred:+.1f}%")
    elif pred < -0.5:
        s -= 0.15; reasons.append(f"模型预测跌{pred:+.1f}%")
    else:
        reasons.append(f"模型预测平({pred:+.2f}%)")
    vols = [k.get("volume",0) for k in kl]
    if len(vols) >= 5 and max(vols[-5:]) > 0:
        vr = vols[-1]/max(np.mean(vols[-6:-1]) if len(vols)>=6 else np.mean(vols[:5]),1)
        if vr > 1.5 and s > 0:
            s += 0.15; reasons.append(f"放量上涨(量比{vr:.1f})")
        elif vr > 1.5 and s < 0:
            s -= 0.15; reasons.append(f"放量下跌(量比{vr:.1f})")
        elif vr < 0.5:
            reasons.append(f"缩量(量比{vr:.1f})")
        else:
            reasons.append(f"量能正常(量比{vr:.1f})")
    return max(min(s, 1.0), -1.0), reasons


# ── 回测 ──
def run_backtest(code: str, kline: list, model, model_acc: float, stock_info: dict = None) -> dict:
    """对单只个股运行完整回测"""
    if not kline or len(kline) < 60:
        return None
    
    capital = INITIAL_CAPITAL
    cash = capital
    shares = 0
    buy_price = 0
    buy_date = None
    trades = []
    equity_curve = []
    peak = capital
    max_dd = 0
    
    # 用训练好的模型做预测
    closes_arr = np.array([k["close"] for k in kline])
    highs_arr = np.array([k["high"] for k in kline])
    lows_arr = np.array([k["low"] for k in kline])
    vols_arr = np.array([k.get("volume", 0) for k in kline])
    
    def get_model_pred(i):
        """对第i个时间点做模型预测"""
        if model is None or i < 20:
            return 0
        try:
            feats = []
            for lag in [1, 2, 3, 5, 10]:
                feats.append((closes_arr[i] - closes_arr[max(0,i-lag)]) / max(closes_arr[max(0,i-lag)], 0.01) * 100 if i>=lag else 0)
            feats.append((max(highs_arr[i-5:i]) - min(lows_arr[i-5:i])) / max(closes_arr[i], 0.01) * 100)
            ma5 = np.mean(closes_arr[i-5:i]) if i>=5 else closes_arr[i]
            ma10 = np.mean(closes_arr[i-10:i]) if i>=10 else closes_arr[i]
            ma20 = np.mean(closes_arr[max(0,i-20):i]) if i>=20 else closes_arr[i]
            feats.extend([(closes_arr[i]-ma5)/max(ma5,0.01)*100, (closes_arr[i]-ma10)/max(ma10,0.01)*100, (closes_arr[i]-ma20)/max(ma20,0.01)*100])
            delta = np.diff(closes_arr[max(0,i-14):i+1])
            g = np.mean(delta[delta>0]) if len(delta[delta>0])>0 else 0
            ls = abs(np.mean(delta[delta<0])) if len(delta[delta<0])>0 else 1
            rsi = 100 - 100/(1+g/max(ls,0.01))
            feats.append(rsi)
            return float(model.predict(np.array(feats).reshape(1,-1))[0])
        except:
            return 0
    
    for i in range(60, len(kline)):
        date = kline[i]["date"]
        # 最后一日只做信号判断和清仓，不回调仓
        is_last = (i == len(kline) - 1)
        open_p = kline[i]["open"]
        close_p = kline[i]["close"]
        high = kline[i]["high"]
        low = kline[i]["low"]
        # 模型预测（已移除model_preds，直接使用get_model_pred）
        
        total_val = cash + shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX) if shares > 0 else cash
        
        # 信号
        sig = 0.0
        sig_reasons = []
        train_kl = kline[:i+1]
        if len(train_kl) >= 20:
            pred_pct = get_model_pred(i)
            sig, sig_reasons = calc_signal(train_kl, {"pred_pct": pred_pct, "accuracy": model_acc})
        
        reason_str = " | ".join(sig_reasons[:4]) if sig_reasons else ""
        
        if shares > 0:
            pnl = (close_p - buy_price) / buy_price * 100 if buy_price > 0 else 0
            hold_days = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(buy_date, "%Y-%m-%d")).days if buy_date else 0
            
            # 硬止损
            if pnl <= STOP_LOSS and hold_days >= 1 and not is_last:
                sp = shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX)
                trades.append({"date": date, "type": "止损", "price": round(close_p,2), "pnl_pct": round(pnl,2), "hold_days": hold_days, "capital": round(cash+sp,2), "reason": f"亏损{pnl:.1f}%触发{-STOP_LOSS:.0f}%硬止损"})
                cash += sp
                shares = 0; buy_price = 0; buy_date = None
                continue
            
            # 减仓/清仓
            if (sig < -0.5 and hold_days >= 2) or is_last:
                sp = shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX)
                trade_type = "最终清仓" if is_last else "趋势清仓"
                trades.append({"date": date, "type": trade_type, "price": round(close_p,2), "pnl_pct": round(pnl,2), "hold_days": hold_days, "capital": round(cash+sp,2), "reason": (f"回测期末平仓 | {reason_str}" if is_last else f"信号{sig:.2f}趋势转弱 | {reason_str}")})
                cash += sp
                shares = 0; buy_price = 0; buy_date = None
                continue
            elif sig < -0.2 and shares >= 200 and hold_days >= 3 and not is_last:
                sell_shares = int(shares * 0.5 / 100) * 100
                if sell_shares >= 100:
                    sp = sell_shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX)
                    cash += sp
                    shares -= sell_shares
                    trades.append({"date": date, "type": "减仓", "price": round(close_p,2), "shares": -sell_shares, "pnl_pct": round(pnl,2), "capital": round(cash+shares*close_p,2), "reason": f"信号偏弱{sig:.2f}减半仓 | {reason_str}"})
            
            # 到期
            if hold_days >= MAX_HOLD and not is_last:
                sp = shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX)
                trades.append({"date": date, "type": "到期", "price": round(close_p,2), "pnl_pct": round(pnl,2), "hold_days": hold_days, "capital": round(cash+sp,2), "reason": f"持仓{hold_days}天到期平仓 | 收益{pnl:+.1f}%"})
                cash += sp
                shares = 0; buy_price = 0; buy_date = None
        
        # 买入
        if shares == 0 and sig > 0.3 and not is_last:
            slot_pct = min(sig * 0.5, 0.25)
            buy_value = cash * slot_pct
            new_shares = int(buy_value / open_p / 100) * 100
            if new_shares >= 100:
                cost = new_shares * open_p * (1 + COMMISSION_RATE)
                if cost <= cash:
                    cash -= cost
                    shares = new_shares
                    buy_price = open_p
                    buy_date = date
                    trades.append({"date": date, "type": "买入", "price": round(open_p,2), "shares": new_shares, "capital": round(cash+shares*close_p,2), "reason": f"信号{sig:.2f}触发买入 | {reason_str}"})
        
        # 净值曲线
        total_val = cash + shares * close_p * (1 - COMMISSION_RATE - STAMP_TAX) if shares > 0 else cash
        if total_val > peak:
            peak = total_val
        dd = (peak - total_val) / peak * 100
        if dd > max_dd: max_dd = dd
        equity_curve.append({"date": date, "total_value": round(total_val,2), "holdings": shares, "drawdown": round(dd,2)})
    
    # 最终清仓（兜底：如果循环没清掉）
    if shares > 0:
        cash += shares * kline[-1]["close"] * (1 - COMMISSION_RATE - STAMP_TAX)
        shares = 0
    
    total_ret = (cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    buy_hold_ret = (kline[-1]["close"] - kline[60]["close"]) / kline[60]["close"] * 100
    
    stock_name = (stock_info or {}).get("name", code)
    stock_board = (stock_info or {}).get("board", "")
    
    # 给每笔交易带上名称、板块和代码
    for t in trades:
        t["name"] = stock_name
        t["board"] = stock_board
        t["code"] = code
    
    return {
        "code": code, "name": stock_name, "board": stock_board,
        "capital": round(cash,2), "total_return": round(total_ret,2),
        "buy_hold_return": round(buy_hold_ret,2), "beat_buy_hold": round(total_ret - buy_hold_ret,2),
        "max_drawdown": round(max_dd,2), "trades": trades, "equity_curve": equity_curve,
        "sharpe": round(total_ret / max(max_dd, 0.5), 2) if max_dd > 0 else 0
    }


# ── 生成H5报告（保持与实盘日报一致风格）──
def generate_html_report(results: list, all_trades: list, all_equity: list):
    """生成回测H5报告"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 统计汇总
    total_invested = INITIAL_CAPITAL * len(results)
    total_final = sum(r["capital"] for r in results if r)
    avg_return = np.mean([r["total_return"] for r in results if r]) * 100 / max(len([r for r in results if r]), 1)
    avg_dd = np.mean([r["max_drawdown"] for r in results if r])
    win_count = sum(1 for r in results if r and r["total_return"] > 0)
    total_count = len([r for r in results if r])
    win_rate = win_count / max(total_count, 1) * 100
    
    # 合并净值
    merged_equity = defaultdict(list)
    for eq in all_equity:
        for point in eq:
            merged_equity[point["date"]].append(point["total_value"])
    
    daily_values = []
    for date in sorted(merged_equity.keys()):
        vals = merged_equity[date]
        # 假设等权重
        avg_val = sum(vals) / len(vals) * (len(vals) / max(len([r for r in results if r]), 1)) + INITIAL_CAPITAL * (1 - len(vals) / max(len([r for r in results if r]), 1))
        daily_values.append({"date": date, "total_value": round(avg_val, 2)})
    
    # 累计净值
    portfolio_start = INITIAL_CAPITAL
    portfolio_final = np.mean([r["capital"] for r in results if r]) * total_count / max(total_count, 1) if results else INITIAL_CAPITAL
    portfolio_ret = (portfolio_final - portfolio_start) / portfolio_start * 100
    
    # 胜率
    profit_trades = [t for t in all_trades if t.get("type") == "趋势清仓" and t.get("pnl_pct", 0) > 0]
    loss_trades = [t for t in all_trades if t.get("type") in ("趋势清仓", "止损") and t.get("pnl_pct", 0) <= 0]
    trade_win_rate = len(profit_trades) / max(len(profit_trades) + len(loss_trades), 1) * 100
    
    snap_data = json.dumps(daily_values, ensure_ascii=False)
    
    # 个股回测表
    stock_rows = ""
    for i, r in enumerate(sorted([r for r in results if r], key=lambda x: -x["total_return"]), 1):
        color = "#34d399" if r["total_return"] >= 0 else "#f87171"
        dd_class = "good" if r["max_drawdown"] < 15 else ("mid" if r["max_drawdown"] < 25 else "bad")
        name_short = r.get("name", r["code"])[:8]
        board_short = r.get("board", "")[:10]
        stock_rows += f"<tr><td>{i}</td><td style='color:#ffd700'>{r['code']}</td>"\
                      f"<td style='color:#ccc;font-size:10px' title='{r.get('board','')}'>{name_short}</td>"\
                      f"<td style='color:#888;font-size:9px'>{board_short}</td>"\
                      f"<td style='color:{color};font-weight:bold'>{r['total_return']:+.2f}%</td>"\
                      f"<td style='color:#888'>{r['buy_hold_return']:+.2f}%</td>"\
                      f"<td style='color:#60a5fa'>{r['beat_buy_hold']:+.2f}%</td>"\
                      f"<td style='color:#f59e0b' class='{dd_class}'>{r['max_drawdown']:.1f}%</td>"\
                      f"<td>{r['sharpe']}</td>"\
                      f"<td>{len(r['trades'])}</td></tr>"
    
    # 交易流水
    trade_rows = ""
    for t in sorted(all_trades, key=lambda x: x.get("date",""), reverse=True)[:80]:
        if t.get("type") == "买入":
            color, tag = "#60a5fa", "买入"
        elif t.get("type") in ("止损",):
            color, tag = "#f87171", "止损"
        elif t.get("type") in ("趋势清仓",):
            color, tag = "#34d399" if t.get("pnl_pct",0) > 0 else "#f87171", "清仓"
        else:
            color, tag = "#888", t.get("type","")
        pnl_str = f"{t.get('pnl_pct',0):+.2f}%" if t.get("pnl_pct",0) != 0 else "-"
        reason = t.get("reason", "")
        t_name = t.get("name", "")
        t_board = t.get("board", "")
        tag_name = f"{t_name}"[:10]
        reason_short = reason[:30] + "..." if len(reason) > 33 else reason
        trade_rows += f"<tr><td style='color:#888'>{t['date'][5:]}</td>"\
                      f"<td><span class='tag tag-{tag}' style='background:{color}22;color:{color};border:1px solid {color}44'>{tag}</span></td>"\
                      f"<td style='color:#ccc;font-size:10px' title='{t_board}'>{tag_name}</td>"\
                      f"<td>¥{t['price']:.2f}</td>"\
                      f"<td>{t.get('shares','-')}</td>"\
                      f"<td style='color:{color};font-weight:bold'>{pnl_str}</td>"\
                      f"<td style='color:#555;font-size:10px'>{t.get('capital','')}</td>"\
                      f"<td style='color:#999;font-size:9px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title=\"{reason}\">{reason_short}</td></tr>"
    
    total_trades = len(all_trades)
    
    HT = """<!DOCTYPE html>
<html lang='zh-CN'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>策略回测报告 | """ + today + """</title>
<script src='echarts.min.js'></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e1a;color:#e0e0e0;font-family:'PingFang SC','Microsoft YaHei',sans-serif;padding:12px;max-width:1100px;margin:0 auto}
h1{color:#ffd700;font-size:20px;text-align:center;margin-bottom:2px;letter-spacing:1px}
.sub{color:#666;text-align:center;font-size:11px;margin-bottom:12px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
.card{background:linear-gradient(135deg,#111827,#1a1a3e);border-radius:8px;padding:12px;border:1px solid #1e3a5f}
.card h2{color:#ffd700;font-size:13px;margin-bottom:8px}
.stat{text-align:center;padding:10px}
.stat .v{font-size:20px;font-weight:bold;margin-bottom:1px}
.stat .l{font-size:10px;color:#888}
.chart{width:100%;height:260px}
table{width:100%;border-collapse:collapse;font-size:11px}
th{background:#1e3a5f;color:#ffd700;padding:4px 5px;text-align:left;position:sticky;top:0;white-space:nowrap}
td{padding:3px 5px;border-bottom:1px solid #111827;font-size:11px}
.tag{display:inline-block;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:bold}
.good{color:#34d399!important}
.mid{color:#f59e0b!important}
.bad{color:#f87171!important}
.flow-box{max-height:400px;overflow-y:auto}
@media(max-width:640px){.grid4,.grid2{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<h1>📊 策略回测报告 · """ + today + """</h1>
<p class="sub">趋势跟踪+时间序列模型 | 动态调仓 | 硬止损-5% | 最大持有20日 | 回测期: 约2年</p>

<div class="grid4">
  <div class="card stat"><div class="v" style="color:#ffd700">""" + str(total_count) + """</div><div class="l">回测股票数</div></div>
  <div class="card stat"><div class="v" style="color:#34d399">""" + f"{portfolio_ret:+.2f}%" + """</div><div class="l">平均收益</div></div>
  <div class="card stat"><div class="v" style="color:#60a5fa">""" + f"{win_rate:.0f}%" + """</div><div class="l">个股胜率</div></div>
  <div class="card stat"><div class="v" style="color:#f59e0b">""" + f"{avg_dd:.1f}%" + """</div><div class="l">平均最大回撤</div></div>
</div>

<div class="grid2">
  <div class="card"><h2>📈 净值曲线（等权组合）</h2><div id="eqChart" class="chart"></div></div>
  <div class="card"><h2>📋 回测统计</h2><div style="font-size:12px;line-height:2">
    <div class="summary-row"><span class="summary-label">回测股票数</span><span class="summary-val">""" + str(total_count) + """</span></div>
    <div class="summary-row"><span class="summary-label">个股胜率</span><span class="summary-val" style="color:""" + ("#34d399" if win_rate>=50 else "#f87171") + """">""" + f"{win_rate:.1f}%" + """</span></div>
    <div class="summary-row"><span class="summary-label">交易胜率</span><span class="summary-val">""" + f"{trade_win_rate:.1f}%" + """</span></div>
    <div class="summary-row"><span class="summary-label">总交易次数</span><span class="summary-val">""" + str(total_trades) + """</span></div>
    <div class="summary-row"><span class="summary-label">平均收益</span><span class="summary-val" style="color:""" + ("#34d399" if portfolio_ret>=0 else "#f87171") + """">""" + f"{portfolio_ret:+.2f}%" + """</span></div>
    <div class="summary-row"><span class="summary-label">平均最大回撤</span><span class="summary-val" style="color:#f59e0b">""" + f"{avg_dd:.1f}%" + """</span></div>
    <div class="summary-row"><span class="summary-label">平均夏普</span><span class="summary-val">""" + f"{np.mean([r['sharpe'] for r in results if r]):.2f}" + """</span></div>
    <div class="summary-row"><span class="summary-label">跑赢基准(持有)</span><span class="summary-val">""" + f"{np.mean([r['beat_buy_hold'] for r in results if r]):+.2f}%" + """</span></div>
  </div></div>
</div>

<div class="card"><h2>📊 个股回测明细（按收益排序）</h2>
<table style="font-size:10px"><tr><th>#</th><th>代码</th><th>名称</th><th>板块</th><th>总收益</th><th>持有收益</th><th>跑赢</th><th>最大回撤</th><th>夏普</th><th>交易</th></tr>""" + stock_rows + """</table></div>

<div class="card"><h2>📋 交易流水</h2><div class="flow-box">
<table style="font-size:10px"><tr><th>日期</th><th>操作</th><th>股票</th><th>价格</th><th>数量</th><th>盈亏</th><th>总资产</th><th>原因</th></tr>""" + trade_rows + """</table></div></div>

<p class="sub" style="margin-top:14px;color:#555;text-align:center">⚠️ 回测数据仅供参考 | 数据源: 新浪财经 | 策略: 趋势跟踪+时间序列模型 | """ + datetime.now().strftime("%Y-%m-%d %H:%M") + """</p>

<script>
var eqData = """ + snap_data + """;
var dates = eqData.map(function(s){return s.date.slice(5);});
var vals = eqData.map(function(s){return s.total_value;});

var ec1 = echarts.init(document.getElementById('eqChart'));
ec1.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',formatter:function(p){
    var i=p[0].dataIndex;
    return '<b>'+eqData[i].date+'</b><br/>总资产: ¥'+eqData[i].total_value.toLocaleString()+'<br/>累计: '+((eqData[i].total_value/100000-1)*100).toFixed(2)+'%';
  }},
  grid:{left:'8%',right:'5%',top:'12%',bottom:'15%'},
  xAxis:{type:'category',data:dates,axisLabel:{fontSize:9,color:'#888',rotate:30},axisLine:{lineStyle:{color:'#1e3a5f'}}},
  yAxis:{type:'value',name:'总资产',nameTextStyle:{color:'#555'},splitLine:{lineStyle:{color:'#111827'}},axisLabel:{color:'#888',formatter:'¥{value}'}},
  series:[{
    type:'line',data:vals,smooth:true,
    lineStyle:{width:2,color:'#34d399'},
    areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'#34d39933'},{offset:1,color:'#34d39900'}]}},
    markLine:{data:[{yAxis:100000,lineStyle:{color:'#555',type:'dashed'},label:{formatter:'本金10万',color:'#888',fontSize:10}}]},
    label:{show:true,position:'top',fontSize:9,color:'#ffd700',formatter:function(p){return ((p.value/100000-1)*100).toFixed(1)+'%';}}
  }]
});
</script>
</body>
</html>"""
    
    path = os.path.join(VIZ_DIR, f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(HT)
    print(f"✅ 回测报告已生成: {path} ({os.path.getsize(path)//1024}KB)")
    return path


# ── 主函数 ──
def main():
    print("📊 策略回测系统")
    print("=" * 50)
    print("策略: 趋势跟踪 + 时间序列模型 + 动态调仓")
    print(f"本金: ¥{INITIAL_CAPITAL:,} | 最大持仓: {MAX_POSITIONS} | 硬止损: {STOP_LOSS}%\n")
    
    from stock_theme_analyzer import get_all_concept_boards, get_board_constituents
    
    # 获取热门板块的个股
    print("[1/3] 获取热门板块个股...")
    boards = get_all_concept_boards(use_cache=True)
    target_codes = []
    seen = set()
    stock_info_map = {}  # code -> {name, board}
    
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
        time.sleep(0.1)
    
    print(f"    → 筛选 {len(target_codes)} 只候选股")
    
    # 获取K线数据
    print("\n[2/3] 获取K线数据...")
    kline_cache = {}
    for code in target_codes:
        kl = get_kline_data(code, days=500)
        if kl and len(kl) >= 60:
            kline_cache[code] = kl
        print(f"    {'✅' if code in kline_cache else '❌'} {code}")
        time.sleep(0.3)
    
    print(f"    → 成功获取 {len(kline_cache)} 只K线数据")
    
    # 整体训练一个时间序列模型
    print("\n[3/4] 训练时间序列模型...")
    all_kline_for_training = []
    for kl in kline_cache.values():
        if len(kl) >= 100:
            all_kline_for_training.extend(kl)
    # 用合并数据训练全局模型
    model, model_acc, fnames, fimp, effective = train_model_for_backtest(all_kline_for_training)
    print(f"    → 模型方向准确率: {model_acc}%" if model else "    → 模型训练失败，使用纯技术信号")
    if effective:
        print(f"    → 有效因子({len(effective)}个): {', '.join(effective[:6])}")
    
    # 运行回测
    print("\n[4/4] 运行回测...")
    results = []
    all_trades = []
    all_equity = []
    
    for code, kl in kline_cache.items():
        result = run_backtest(code, kl, model, model_acc, stock_info_map.get(code))
        if result:
            results.append(result)
            all_trades.extend(result["trades"])
            all_equity.append(result["equity_curve"])
            ret = result["total_return"]
            name_display = result.get("name", code)
            print(f"    {code} {name_display}: 收益{ret:+.2f}% 最大回撤{result['max_drawdown']:.1f}% 交易{len(result['trades'])}笔")
    
    if not results:
        print("❌ 无有效回测结果")
        return
    
    # 汇总
    print(f"\n{'='*50}")
    wins = sum(1 for r in results if r["total_return"] > 0)
    print(f"回测 {len(results)} 只股票: 胜{wins}只 负{len(results)-wins}只")
    print(f"平均收益: {np.mean([r['total_return'] for r in results]):+.2f}%")
    print(f"平均最大回撤: {np.mean([r['max_drawdown'] for r in results]):.1f}%")
    print(f"平均跑赢持有: {np.mean([r['beat_buy_hold'] for r in results]):+.2f}%")
    
    # 生成H5
    print(f"\n生成回测报告H5...")
    path = generate_html_report(results, all_trades, all_equity)
    
    # 打开浏览器
    import subprocess
    subprocess.run(["open", path])
    print(f"\n✅ 已完成！浏览器已打开回测报告")


if __name__ == "__main__":
    main()
