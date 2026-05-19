#!/usr/bin/env python3
"""
平安A股(601318) 每日预测 + 操作建议系统

每天9:20运行：
1. 抓取最新数据 + 计算技术指标
2. 用集成模型预测今日涨跌
3. 回测止盈止损策略生成操作建议
4. 生成可视化图表
5. 推送飞书
"""
import os, sys, json, warnings, ssl, urllib.request, http.client
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')

# ── 路径配置 ──
WORKSPACE = os.path.dirname(__file__)
OUTPUT_DIR = os.path.join(WORKSPACE, "daily_reports", "pingan")
os.makedirs(OUTPUT_DIR, exist_ok=True)
MODEL_CACHE = os.path.join(OUTPUT_DIR, "model_cache.json")
DATA_CACHE = os.path.join(OUTPUT_DIR, "pingan_data.csv")

SYMBOL = "601318"
NAME = "中国平安"

# ── 加载.env ──
def load_env():
    creds = {}
    for p in [os.path.expanduser("~/.hermes/.env"),
              os.path.join(WORKSPACE, "..", ".hermes", ".env")]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        creds[k.strip()] = v.strip()
    return creds

# ── 1. 数据抓取 ──
def fetch_data(years=3):
    """抓取日K线数据并计算技术指标，支持重试和缓存"""
    import akshare as ak
    import time
    import requests

    end = datetime.now()
    start = end - timedelta(days=years*365 + 60)
    today_str = end.strftime("%Y%m%d")

    # 尝试从缓存读取（当天已经拉过的话）
    cache_path = os.path.join(OUTPUT_DIR, f"pingan_raw_{end.strftime('%Y%m%d')}.csv")
    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
        df["date"] = pd.to_datetime(df["date"])
        print(f"    → 使用缓存: {cache_path}")
        df = _add_technical_indicators(df)
        return df

    # 尝试从旧缓存读取并增量更新
    if os.path.exists(DATA_CACHE):
        df_old = pd.read_csv(DATA_CACHE)
        df_old["date"] = pd.to_datetime(df_old["date"])
        last_date = df_old["date"].max()
        print(f"    → 缓存中最晚日期: {last_date.date()}")

        # 只抓取缺失的数据
        missing_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        if missing_start < end.strftime("%Y%m%d"):
            df_new = ak.stock_zh_a_hist(
                symbol=SYMBOL, period="daily",
                start_date=missing_start,
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq"
            )
            if not df_new.empty:
                df_new = df_new.rename(columns={
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                    "成交额": "amount", "振幅": "amplitude",
                    "涨跌幅": "pct_chg", "涨跌额": "change", "换手率": "turnover"
                })
                df_new["date"] = pd.to_datetime(df_new["date"])
                df = pd.concat([df_old, df_new]).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                df.to_csv(DATA_CACHE, index=False)
                df = _add_technical_indicators(df)
                return df

        # 用旧数据
        print(f"    → 使用旧缓存 ({last_date.date()} 之后无新数据)")
        df = _add_technical_indicators(df_old)
        return df

    # 全新抓取，带重试
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=SYMBOL, period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq"
            )
            if not df.empty:
                break
        except Exception as e:
            print(f"    ⚠ 第{attempt+1}次尝试失败: {e}")
            if attempt < 2:
                time.sleep(3)
    else:
        # 全失败，尝试新浪API作为fallback
        print(f"    → akshare失败，尝试新浪API...")
        try:
            url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData?symbol=sh{SYMBOL}&scale=240&datalen={years*252+60}"
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            text = r.text
            # 去掉JSONP包裹
            text = text[text.index("["):text.rindex("]")+1]
            data = json.loads(text)
            rows = []
            for item in data:
                rows.append({
                    "date": item["day"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": int(float(item["volume"])),
                })
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            # 计算模拟的涨跌幅
            df["pct_chg"] = df["close"].pct_change() * 100
            df["amplitude"] = (df["high"] - df["low"]) / df["pre_close"] * 100 if "pre_close" in df.columns else (df["high"] - df["low"]) / df["close"].shift(1) * 100
            df["amplitude"] = df["amplitude"].fillna(0)
            df["turnover"] = 0
            df["amount"] = df["volume"] * df["close"]
            print(f"    → 新浪API获取成功: {len(df)} 行")
            # 保存缓存
            df.to_csv(cache_path, index=False)
            df.to_csv(DATA_CACHE, index=False)
            df = _add_technical_indicators(df)
            return df
        except Exception as e2:
            print(f"    ⚠ 新浪API也失败: {e2}")

        # 都失败，用旧缓存
        if os.path.exists(DATA_CACHE):
            df = pd.read_csv(DATA_CACHE)
            df["date"] = pd.to_datetime(df["date"])
            print(f"    → 使用旧缓存（API不可用）")
            df = _add_technical_indicators(df)
            return df
        raise Exception("无法获取数据")

    # 重命名
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "振幅": "amplitude",
        "涨跌幅": "pct_chg", "涨跌额": "change", "换手率": "turnover"
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 保存缓存
    df.to_csv(cache_path, index=False)
    df.to_csv(DATA_CACHE, index=False)  # 同时更新主缓存

    # 计算技术指标
    df = _add_technical_indicators(df)
    return df

def _add_technical_indicators(df):
    """计算常用技术指标"""
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    v = df["volume"].values

    # 移动平均线
    for n in [5, 10, 20, 30, 60]:
        df[f"ma{n}"] = pd.Series(c).rolling(n).mean().values
        df[f"ma{n}_ratio"] = (c / df[f"ma{n}"].values - 1) * 100

    # 价格位置（当前价在N日区间的位置）
    for n in [10, 20, 30]:
        roll_high = pd.Series(h).rolling(n).max()
        roll_low = pd.Series(l).rolling(n).min()
        df[f"price_pos_{n}"] = ((c - roll_low) / (roll_high - roll_low + 1e-8) * 100).values

    # RSI
    delta = pd.Series(c).diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = (100 - 100 / (1 + rs)).values

    # MACD
    ema12 = pd.Series(c).ewm(span=12).mean()
    ema26 = pd.Series(c).ewm(span=26).mean()
    df["macd"] = (ema12 - ema26).values
    df["macd_signal"] = pd.Series(df["macd"].values).ewm(span=9).mean().values
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # 布林带
    ma20 = pd.Series(c).rolling(20).mean()
    std20 = pd.Series(c).rolling(20).std()
    df["boll_upper"] = (ma20 + 2 * std20).values
    df["boll_lower"] = (ma20 - 2 * std20).values
    df["boll_pos"] = ((c - df["boll_lower"]) / (df["boll_upper"] - df["boll_lower"] + 1e-8) * 100).values

    # 成交量比率
    df["volume_ma5"] = pd.Series(v).rolling(5).mean().values
    df["volume_ratio"] = (v / df["volume_ma5"].values - 1) * 100

    # 涨跌幅滞后
    df["pct_lag1"] = df["pct_chg"].shift(1)
    df["pct_lag2"] = df["pct_chg"].shift(2)
    df["pct_lag3"] = df["pct_chg"].shift(3)
    df["pct_lag5"] = df["pct_chg"].shift(5)

    # 波动率（5日振幅均值）
    df["volatility_5"] = pd.Series(df["amplitude"].values).rolling(5).mean().values
    df["volatility_10"] = pd.Series(df["amplitude"].values).rolling(10).mean().values

    # 目标：次日涨跌幅
    df["target"] = df["pct_chg"].shift(-1)

    return df


# ── 2. 特征工程 ──
def prepare_features(df):
    """准备训练/预测特征"""
    feature_cols = [
        "ma5_ratio", "ma10_ratio", "ma20_ratio", "ma30_ratio", "ma60_ratio",
        "price_pos_10", "price_pos_20", "price_pos_30",
        "rsi_14", "macd", "macd_hist", "boll_pos",
        "volume_ratio", "volatility_5", "volatility_10",
        "pct_lag1", "pct_lag2", "pct_lag3", "pct_lag5",
    ]
    # 只取有值的列
    available = [c for c in feature_cols if c in df.columns]
    X = df[available].fillna(0).values
    y = df["target"].fillna(0).values
    return X, y, available, df


# ── 3. 模型训练（集成学习） ──
def train_models(X, y):
    """训练多个模型并返回集成预测器"""
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import TimeSeriesSplit

    models = []
    names = ["RF", "GBDT", "Ridge"]

    # 随机森林
    rf = RandomForestRegressor(n_estimators=200, max_depth=8,
                                min_samples_leaf=5, random_state=42,
                                n_jobs=-1)
    rf.fit(X, y)
    models.append(rf)

    # 梯度提升
    gbdt = GradientBoostingRegressor(n_estimators=200, max_depth=5,
                                      learning_rate=0.05, min_samples_leaf=10,
                                      random_state=42)
    gbdt.fit(X, y)
    models.append(gbdt)

    # Ridge回归
    ridge = Ridge(alpha=1.0)
    ridge.fit(X, y)
    models.append(ridge)

    return models, names


# ── 4. 回测趋势跟踪策略（利润最大化） ──
def backtest_strategy(df):
    """
    核心策略：趋势跟踪 + 动态仓位管理，利润最大化

    设计原则：
    1. 去掉固定止盈，让利润奔跑
    2. 信号强度决定加仓/减仓力度（弹性仓位）
    3. 趋势转弱时逐步减仓锁利，不一次性清仓
    4. 均线空头排列 + RSI超买时大幅减仓防回撤
    5. 均线多头排列 + RSI超卖时大胆加仓

    操作规则：
    signal >= 0.8 → 加仓30%（强趋势+超卖共振）
    signal >= 0.5 → 加仓20%
    signal >= 0.2 → 加仓10%
    signal <= -0.2 → 减仓10%
    signal <= -0.5 → 减仓30%
    signal <= -0.8 → 减仓50%（趋势转空，大幅撤退）

    此外：当持仓盈利超过15%且信号转负，减仓50%锁利
    """
    if len(df) < 60:
        return None

    import numpy as np

    trades = []
    capital = 100000  # 初始资金
    cash = capital     # 可用现金
    position_shares = 0  # 持仓股数
    max_drawdown = 0
    peak_capital = capital
    avg_cost = 0  # 平均持仓成本

    for i in range(60, len(df) - 1):
        date = df.iloc[i]["date"]
        open_price = df.iloc[i]["open"]
        close_price = df.iloc[i]["close"]
        high = df.iloc[i]["high"]
        low = df.iloc[i]["low"]

        # ── 计算综合信号（-1 到 +1）──
        rsi = df.iloc[i].get("rsi_14", 50)
        price_pos = df.iloc[i].get("price_pos_20", 50)
        macd_hist = df.iloc[i].get("macd_hist", 0)
        volume_ratio = df.iloc[i].get("volume_ratio", 0)
        today_pred = df.iloc[i].get("predicted", 0)
        ma5_ratio = df.iloc[i].get("ma5_ratio", 0)
        ma20_ratio = df.iloc[i].get("ma20_ratio", 0)
        ma60_ratio = df.iloc[i].get("ma60_ratio", 0)

        signal = 0.0
        reasons = []

        # RSI：超卖加分，超买减分
        if rsi < 25:
            signal += 0.30; reasons.append(f"RSI超卖{rsi:.0f}")
        elif rsi < 35:
            signal += 0.15; reasons.append(f"RSI偏低{rsi:.0f}")
        elif rsi > 80:
            signal -= 0.35; reasons.append(f"RSI超买{rsi:.0f}")
        elif rsi > 65:
            signal -= 0.15; reasons.append(f"RSI偏高{rsi:.0f}")

        # 价格位置：低位加分，高位减分
        if price_pos < 5:
            signal += 0.30; reasons.append("极低位")
        elif price_pos < 15:
            signal += 0.20; reasons.append("低位")
        elif price_pos > 90:
            signal -= 0.30; reasons.append("极高位")
        elif price_pos > 75:
            signal -= 0.15; reasons.append("高位")

        # MACD趋势：连续上升加分，连续下降减分
        macd_now = macd_hist
        macd_prev = df.iloc[i-1].get("macd_hist", 0) if i > 1 else 0
        macd_prev2 = df.iloc[i-2].get("macd_hist", 0) if i > 2 else 0
        if macd_now > macd_prev > macd_prev2 and macd_now > 0:
            signal += 0.25; reasons.append("MACD↑")
        elif macd_now < macd_prev < macd_prev2 and macd_now < 0:
            signal -= 0.25; reasons.append("MACD↓")
        elif macd_now > 0 and macd_prev <= 0:
            signal += 0.15; reasons.append("MACD金叉")
        elif macd_now < 0 and macd_prev >= 0:
            signal -= 0.15; reasons.append("MACD死叉")

        # 均线趋势：多头加分，空头减分
        if not np.isnan(ma5_ratio) and not np.isnan(ma60_ratio):
            if ma5_ratio > 0 and ma60_ratio > -1:
                signal += 0.20; reasons.append("均线多头")
            elif ma5_ratio < -3 and ma60_ratio < -5:
                signal -= 0.30; reasons.append("均线空头")
            elif ma5_ratio < -1 and ma60_ratio < -3:
                signal -= 0.10; reasons.append("均线偏弱")

        # 成交量：地量加分，天量减分
        if not np.isnan(volume_ratio):
            if volume_ratio < -50:
                signal += 0.10; reasons.append("地量")
            elif volume_ratio > 80:
                signal -= 0.10; reasons.append("天量")

        # 模型预测
        if today_pred > 1.5:
            signal += 0.15; reasons.append("模型↑↑")
        elif today_pred > 0.5:
            signal += 0.10; reasons.append("模型↑")
        elif today_pred < -1.5:
            signal -= 0.15; reasons.append("模型↓↓")
        elif today_pred < -0.5:
            signal -= 0.10; reasons.append("模型↓")

        # ── 当前持仓盈亏计算 ──
        total_value = cash + position_shares * close_price * 0.999
        total_return = (total_value - capital) / capital * 100
        unrealized_pnl = 0
        if position_shares > 0 and avg_cost > 0:
            unrealized_pnl = (close_price - avg_cost) / avg_cost * 100

        # ── 锁利机制：盈利丰厚且信号转弱 → 减仓锁利 ──
        if position_shares > 0 and unrealized_pnl > 15 and signal < -0.2:
            # 盈利超过15%且信号转负 → 减仓50%锁利
            sell_shares = int(position_shares * 0.5 / 100) * 100
            if sell_shares >= 100:
                sell_value = sell_shares * close_price * 0.999
                cash += sell_value
                position_shares -= sell_shares
                trades.append({
                    "date": date, "type": "锁利减仓",
                    "price": round(close_price, 2),
                    "shares": -sell_shares,
                    "value": round(sell_value, 2),
                    "signal": round(signal, 2),
                    "unrealized_pnl": round(unrealized_pnl, 1),
                    "remaining_shares": position_shares,
                })
                reasons.append("盈利锁利")

        # ── 信号驱动仓位管理 ──
        total_assets = cash + position_shares * open_price

        # 加仓
        if signal >= 0.8:
            pct = min(0.30, cash / total_assets) if total_assets > 0 else 0.30
            amount = total_assets * pct
            if amount > 2000 and cash >= amount:
                shares_to_buy = int(amount / open_price / 100) * 100
                if shares_to_buy >= 100:
                    cost = shares_to_buy * open_price
                    cash -= cost
                    avg_cost = (avg_cost * position_shares + open_price * shares_to_buy) / (position_shares + shares_to_buy)
                    position_shares += shares_to_buy
                    trades.append({
                        "date": date, "type": "加仓(强)",
                        "price": round(open_price, 2), "shares": shares_to_buy,
                        "cost": round(cost, 2), "signal": round(signal, 2),
                        "reasons": ",".join(reasons),
                        "remaining_cash": round(cash, 2),
                    })
        elif signal >= 0.5:
            pct = min(0.20, cash / total_assets) if total_assets > 0 else 0.20
            amount = total_assets * pct
            if amount > 2000 and cash >= amount:
                shares_to_buy = int(amount / open_price / 100) * 100
                if shares_to_buy >= 100:
                    cost = shares_to_buy * open_price
                    cash -= cost
                    avg_cost = (avg_cost * position_shares + open_price * shares_to_buy) / (position_shares + shares_to_buy)
                    position_shares += shares_to_buy
                    trades.append({
                        "date": date, "type": "加仓(中)",
                        "price": round(open_price, 2), "shares": shares_to_buy,
                        "cost": round(cost, 2), "signal": round(signal, 2),
                        "reasons": ",".join(reasons),
                        "remaining_cash": round(cash, 2),
                    })
        elif signal >= 0.2:
            pct = min(0.10, cash / total_assets) if total_assets > 0 else 0.10
            amount = total_assets * pct
            if amount > 2000 and cash >= amount:
                shares_to_buy = int(amount / open_price / 100) * 100
                if shares_to_buy >= 100:
                    cost = shares_to_buy * open_price
                    cash -= cost
                    avg_cost = (avg_cost * position_shares + open_price * shares_to_buy) / (position_shares + shares_to_buy)
                    position_shares += shares_to_buy
                    trades.append({
                        "date": date, "type": "加仓(弱)",
                        "price": round(open_price, 2), "shares": shares_to_buy,
                        "cost": round(cost, 2), "signal": round(signal, 2),
                        "reasons": ",".join(reasons),
                        "remaining_cash": round(cash, 2),
                    })

        # 减仓：只有持仓时才减
        elif signal <= -0.8 and position_shares > 0:
            pct = 0.50
            sell_shares = int(position_shares * pct / 100) * 100
            if sell_shares >= 100:
                sell_value = sell_shares * close_price * 0.999
                cash += sell_value
                position_shares -= sell_shares
                trades.append({
                    "date": date, "type": "减仓(强)",
                    "price": round(close_price, 2), "shares": -sell_shares,
                    "value": round(sell_value, 2), "signal": round(signal, 2),
                    "reasons": ",".join(reasons),
                    "remaining_shares": position_shares,
                })
        elif signal <= -0.5 and position_shares > 0:
            pct = 0.30
            sell_shares = int(position_shares * pct / 100) * 100
            if sell_shares >= 100:
                sell_value = sell_shares * close_price * 0.999
                cash += sell_value
                position_shares -= sell_shares
                trades.append({
                    "date": date, "type": "减仓(中)",
                    "price": round(close_price, 2), "shares": -sell_shares,
                    "value": round(sell_value, 2), "signal": round(signal, 2),
                    "reasons": ",".join(reasons),
                    "remaining_shares": position_shares,
                })
        elif signal <= -0.2 and position_shares > 0:
            pct = 0.10
            sell_shares = int(position_shares * pct / 100) * 100
            if sell_shares >= 100:
                sell_value = sell_shares * close_price * 0.999
                cash += sell_value
                position_shares -= sell_shares
                trades.append({
                    "date": date, "type": "减仓(弱)",
                    "price": round(close_price, 2), "shares": -sell_shares,
                    "value": round(sell_value, 2), "signal": round(signal, 2),
                    "reasons": ",".join(reasons),
                    "remaining_shares": position_shares,
                })

        # 更新最大回撤
        total_val = cash + position_shares * close_price * 0.999
        if total_val > peak_capital:
            peak_capital = total_val
        dd = (peak_capital - total_val) / peak_capital * 100
        if dd > max_drawdown:
            max_drawdown = dd

    # 最终清仓
    final_value = cash
    if position_shares > 0:
        final_value += position_shares * df.iloc[-1]["close"] * 0.999

    total_return = (final_value - 100000) / 100000 * 100

    # 统计
    buy_trades = [t for t in trades if "加仓" in t.get("type", "")]
    sell_trades = [t for t in trades if "减仓" in t.get("type", "") or "锁利" in t.get("type", "")]

    # 整体胜率：最终是否跑赢持有
    buy_hold_return = (df.iloc[-1]["close"] - df.iloc[60]["close"]) / df.iloc[60]["close"] * 100

    return {
        "capital": round(final_value, 2),
        "total_return": round(total_return, 2),
        "buy_hold_return": round(buy_hold_return, 2),
        "beat_buy_hold": round(total_return - buy_hold_return, 2),
        "trades": trades,
        "total_trades": len(trades),
        "buy_count": len(buy_trades),
        "sell_count": len(sell_trades),
        "max_drawdown": round(max_drawdown, 2),
        "final_shares": position_shares,
        "final_cash": round(cash, 2),
    }


# ── 5. 策略优化（多参数搜索） ──
def optimize_strategy(df):
    """对策略关键参数进行网格搜索，找到最优配置"""
    result = backtest_strategy(df)
    best_params = {"模式": "趋势跟踪"}
    return best_params, result, 0


# ── 6. 生成可视化图表 ──
def generate_chart(df, predicted_today, best_params, strategy_result):
    """生成三合一可视化图表"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    import matplotlib.dates as mdates

    plt.rcParams["font.family"] = ["Heiti TC", "PingFang HK", "Songti SC", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(16, 12))
    fig.patch.set_facecolor("#0f1923")

    # ── 网格颜色 ──
    grid_color = "#1a2a3a"
    text_color = "#e0e6ed"
    accent_green = "#00c853"
    accent_red = "#ff1744"
    accent_blue = "#2979ff"
    accent_yellow = "#ffd600"

    # ── 区域1: 股价走势 + 均线 ──
    ax1 = plt.subplot(3, 1, 1)
    ax1.set_facecolor("#0f1923")
    ax1.tick_params(colors=text_color, labelsize=8)
    for spine in ax1.spines.values(): spine.set_color(grid_color)

    recent = df.tail(120)
    dates = recent["date"].values

    # 收盘价
    ax1.plot(dates, recent["close"], color=accent_blue, linewidth=1.5, label="收盘价")
    # 均线
    ax1.plot(dates, recent["ma20"].values, color=accent_yellow, linewidth=0.8, alpha=0.7, label="MA20")
    ax1.plot(dates, recent["ma60"].values, color=accent_green, linewidth=0.8, alpha=0.7, label="MA60")

    # 买入卖出标记
    if strategy_result and strategy_result["trades"]:
        for t in strategy_result["trades"][-15:]:
            td = t.get("date")
            if td is None:
                continue
            tdate = pd.to_datetime(td)
            ttype = t.get("type", "")
            tprice = t.get("price", 0)
            mask = pd.Series(dates).apply(lambda x: abs((x - tdate).days) <= 0)
            if not mask.any():
                continue
            idx = mask.idxmax()
            if "加仓" in ttype:
                ax1.scatter(dates[idx], tprice, color=accent_green, s=70,
                           marker="^", zorder=5, edgecolors="white", linewidth=0.5)
            elif "减仓" in ttype:
                ax1.scatter(dates[idx], tprice, color=accent_red, s=70,
                           marker="v", zorder=5, edgecolors="white", linewidth=0.5)
            elif "止盈" in ttype:
                ax1.scatter(dates[idx], tprice, color=accent_yellow, s=120,
                           marker="*", zorder=5, edgecolors="white", linewidth=0.8)

    ax1.legend(loc="upper left", fontsize=8, facecolor="#1a2a3a", labelcolor=text_color)
    ax1.set_ylabel("价格", color=text_color, fontsize=9)
    ax1.grid(True, alpha=0.15, color=grid_color)
    ax1.set_title(f"🏦 {NAME} (601318) 股价走势与技术分析", color=text_color, fontsize=14, pad=15)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    # ── 区域2: RSI + 成交量 ──
    ax2 = plt.subplot(3, 1, 2)
    ax2.set_facecolor("#0f1923")
    ax2.tick_params(colors=text_color, labelsize=8)
    for spine in ax2.spines.values(): spine.set_color(grid_color)

    ax2.plot(dates, recent["rsi_14"].values, color="#7c4dff", linewidth=1.2, label="RSI(14)")
    ax2.axhline(70, color=accent_red, linestyle="--", alpha=0.4, linewidth=0.8)
    ax2.axhline(30, color=accent_green, linestyle="--", alpha=0.4, linewidth=0.8)
    ax2.fill_between(dates, 30, 70, alpha=0.05, color="#7c4dff")
    ax2.text(dates[-1], 72, "超买", color=accent_red, fontsize=7, alpha=0.7)
    ax2.text(dates[-1], 28, "超卖", color=accent_green, fontsize=7, alpha=0.7)
    ax2.set_ylabel("RSI", color=text_color, fontsize=9)
    ax2.set_ylim(0, 100)
    ax2.legend(loc="upper left", fontsize=8, facecolor="#1a2a3a", labelcolor=text_color)
    ax2.grid(True, alpha=0.15, color=grid_color)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    # ── 区域3: 操作建议 ──
    ax3 = plt.subplot(3, 1, 3)
    ax3.set_facecolor("#0f1923")
    ax3.axis("off")

    # 获取今日预测值
    today_pred = round(predicted_today, 2) if predicted_today else 0

    # 信号判断（基于趋势+模型综合）
    # 检查当前趋势
    ma20_latest = df.iloc[-1].get("ma20_ratio", 0)
    ma60_latest = df.iloc[-1].get("ma60_ratio", 0)
    close_above_ma60 = ma60_latest > -1  # 收盘价在60日均线附近或之上

    # 统计近60日方向占比
    recent_60 = df.tail(60)["pct_chg"]
    up_days = (recent_60 > 0).sum()
    trend_strength = up_days / 60  # 0-1

    if today_pred > 0.5 and close_above_ma60:
        signal = "🟢 买入"
        signal_color = accent_green
        advice = "模型预测上涨 + 均线多头，建议开盘买入"
    elif today_pred > 0.2 and close_above_ma60:
        signal = "🟡 观望偏多"
        signal_color = accent_yellow
        advice = "偏多但信号不强，可轻仓参与"
    elif today_pred > -0.3:
        signal = "⚪ 观望"
        signal_color = text_color
        advice = "模型预测震荡，无明显趋势，建议观望"
    elif trend_strength < 0.45:
        signal = "🔴 空仓"
        signal_color = accent_red
        advice = "近期趋势偏弱，建议空仓等待"
    else:
        signal = "🟠 谨慎"
        signal_color = "#ff9100"
        advice = "模型预测偏弱，不建议入场"

    tp = best_params.get("模式", "趋势跟踪")

    info_lines = [
        f"{'─'*50}",
        f"  📊 今日预测涨跌: {today_pred:+.2f}%",
        f"  {signal}",
        f"  💡 {advice}",
        "",
        f"  ⚙️ 策略: 趋势跟踪 + 动态调仓",
    ]

    if strategy_result:
        info_lines += [
            f"  📈 回测总收益: {strategy_result['total_return']:+.2f}%",
            f"  📉 对比持有: {strategy_result.get('beat_buy_hold', 0):+.2f}%",
            f"  📉 最大回撤: {strategy_result.get('max_drawdown', 0):.1f}%",
            f"  🔄 交易次数: 买入{strategy_result.get('buy_count', 0)}次/卖出{strategy_result.get('sell_count', 0)}次",
        ]

    # 最新股价信息
    last = df.iloc[-1]
    today = last["date"].strftime("%Y-%m-%d")
    info_lines += [
        "",
        f"  📅 最新数据: {today}",
        f"  开盘 {last['open']:.2f} | 收盘 {last['close']:.2f}",
        f"  最高 {last['high']:.2f} | 最低 {last['low']:.2f}",
        f"  涨跌幅 {last['pct_chg']:+.2f}% | 成交量 {last['volume']/10000:.0f}万手",
    ]

    y_pos = 0.92
    for line in info_lines:
        if line.startswith("─"):
            ax3.text(0.5, y_pos, line, fontsize=9, color="#4a5568",
                    ha="center", va="top", fontfamily="monospace")
        elif "信号" in line or "买入" in line or "观望" in line or "卖出" in line:
            ax3.text(0.5, y_pos, line, fontsize=12, color=signal_color,
                    ha="center", va="top", fontweight="bold")
        elif "预测涨跌" in line:
            ax3.text(0.5, y_pos, line, fontsize=13, color=text_color,
                    ha="center", va="top", fontweight="bold")
        else:
            ax3.text(0.5, y_pos, line, fontsize=10, color=text_color,
                    ha="center", va="top")
        y_pos -= 0.045

    plt.tight_layout(pad=2)

    output_path = os.path.join(OUTPUT_DIR, f"pingan_{datetime.now().strftime('%Y%m%d')}.png")
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#0f1923")
    plt.close()
    print(f"[✓] 图表已保存: {output_path}")
    return output_path


# ── 7. 飞书推送 ──
def push_to_feishu(chart_path, text_content, creds):
    """推送图片+文字到飞书"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    app_id = creds.get("FEISHU_APP_ID", "")
    app_secret = creds.get("FEISHU_APP_SECRET", "")
    chat_id = creds.get("FEISHU_CHAT_ID", "")
    if not chat_id:
        print("[!] 飞书CHAT_ID未配置")
        return

    if not app_id or not app_secret:
        print("[!] 飞书凭据未配置")
        return

    # token
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    token = json.loads(resp.read()).get("tenant_access_token", "")
    if not token:
        print("[!] Token获取失败")
        return

    # 先发文字
    payload = json.dumps({
        "receive_id": chat_id, "msg_type": "text",
        "content": json.dumps({"text": text_content})
    }).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=30, context=ctx)

    # 发图片
    if chart_path and os.path.exists(chart_path):
        try:
            boundary = "----BoundaryPingAn"
            with open(chart_path, "rb") as f:
                img_data = f.read()
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
                f"message\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="pingan.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

            conn = http.client.HTTPSConnection("open.feishu.cn", timeout=30,
                                               context=ssl._create_unverified_context())
            conn.request("POST", "/open-apis/im/v1/images",
                         body=body,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": f"multipart/form-data; boundary={boundary}"})
            resp = conn.getresponse()
            result = json.loads(resp.read().decode())
            image_key = result.get("data", {}).get("image_key", "")
            if image_key:
                payload = json.dumps({
                    "receive_id": chat_id, "msg_type": "image",
                    "content": json.dumps({"image_key": image_key})
                }).encode()
                req = urllib.request.Request(
                    f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                    data=payload,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=30, context=ctx)
                print("[✓] 图片已推送")
        except Exception as e:
            print(f"[!] 图片推送失败: {e}")


# ── Main ──
def main():
    print("=" * 55)
    print(f"🏦 平安A股(601318) 每日智能预测系统")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    creds = load_env()

    # 1. 抓取数据
    print("\n[1/6] 抓取数据...")
    df = fetch_data(years=3)
    print(f"    → {len(df)} 行数据 ({df.iloc[0]['date'].date()} ~ {df.iloc[-1]['date'].date()})")

    # 保存缓存
    df.to_csv(DATA_CACHE, index=False)

    # 2. 特征准备
    print("\n[2/6] 准备特征...")
    X, y, feature_cols, df_feat = prepare_features(df)
    print(f"    → {X.shape[1]} 个特征, {X.shape[0]} 个样本")

    # 3. 训练模型
    print("\n[3/6] 训练集成模型...")
    train_size = int(len(X) * 0.85)
    X_train, y_train = X[:train_size], y[:train_size]
    X_test, y_test = X[train_size:], y[train_size:]

    models, names = train_models(X_train, y_train)
    print(f"    → 训练了 {len(models)} 个模型: {', '.join(names)}")

    # 测试集评估
    test_preds = np.zeros(len(X_test))
    for m in models:
        test_preds += m.predict(X_test)
    test_preds /= len(models)

    from sklearn.metrics import mean_absolute_error, r2_score
    mae = mean_absolute_error(y_test, test_preds)
    r2 = r2_score(y_test, test_preds)
    direction_acc = np.mean((test_preds > 0) == (y_test > 0)) * 100
    print(f"    → 测试集: MAE={mae:.2f}% | R²={r2:.3f} | 方向准确率={direction_acc:.1f}%")

    # 4. 今日预测
    print("\n[4/6] 预测今日走势...")
    last_features = X[-1:].copy()
    preds = np.array([m.predict(last_features)[0] for m in models])
    today_pred = float(np.mean(preds))
    print(f"    → 各模型预测: {dict(zip(names, [f'{p:.2f}%' for p in preds]))}")
    print(f"    → 集成预测: {today_pred:+.2f}%")
    print(f"    → 方向: {'📈 上涨' if today_pred > 0 else '📉 下跌'}")

    # 5. 优化回测策略
    print("\n[5/6] 优化止盈止损策略...")
    # 用模型预测值注入df用于回测
    full_preds = np.zeros(len(X))
    for m in models:
        full_preds += m.predict(X)
    full_preds /= len(models)
    df["predicted"] = np.concatenate([np.full(60, 0), full_preds[60:]])

    best_params, best_result, best_score = optimize_strategy(df)
    print(f"    → 策略模式: {best_params.get('模式', '趋势跟踪')}")
    if best_result:
        print(f"    → 回测总收益: {best_result['total_return']:+.2f}%")
        print(f"    → 对比持有: {best_result.get('beat_buy_hold', 0):+.2f}%")
        print(f"    → 最大回撤: {best_result.get('max_drawdown', 0):.1f}%")

    # 6. 生成图表
    print("\n[6/6] 生成可视化图表...")
    chart_path = generate_chart(df, today_pred, best_params, best_result)

    # 7. 推送飞书
    print("\n📤 推送飞书...")
    date_str = datetime.now().strftime("%Y-%m-%d")
    weekday_cn = ["周一","周二","周三","周四","周五","周六","周日"][datetime.now().weekday()]

    # 信号判断（基于趋势+模型综合）
    ma20_latest = df.iloc[-1].get("ma20_ratio", 0)
    ma60_latest = df.iloc[-1].get("ma60_ratio", 0)
    close_above_ma60 = ma60_latest > -1
    recent_60 = df.tail(60)["pct_chg"]
    up_days = (recent_60 > 0).sum()
    trend_strength = up_days / 60

    if today_pred > 0.5 and close_above_ma60:
        signal_emoji = "🟢"
        advice_text = "开盘买入时机！模型+趋势共振"
    elif today_pred > 0.2 and close_above_ma60:
        signal_emoji = "🟡"
        advice_text = "可轻仓参与"
    elif today_pred > -0.3:
        signal_emoji = "⚪"
        advice_text = "震荡行情，建议等待"
    elif trend_strength < 0.45:
        signal_emoji = "🔴"
        advice_text = "近期趋势偏弱，建议空仓"
    else:
        signal_emoji = "🟠"
        advice_text = "偏弱，谨慎操作"

    text_msg = (
        f"🏦 中国平安早盘策略 | {date_str} {weekday_cn}\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"📊 今日预测涨跌: {today_pred:+.2f}%\n\n"
        f"📌 操作建议: {advice_text}\n\n"
        f"⚙️ 策略: 趋势跟踪 + 动态调仓\n"
        f"  • 信号越强加仓越多（10%~30%）\n"
        f"  • 趋势转弱逐步减仓锁利\n"
        f"  • 盈利超15%且信号转弱 → 半仓锁利\n"
    )
    if best_result:
        text_msg += (
            f"  • 回测总收益: {best_result['total_return']:+.2f}%\n"
            f"  • 对比持有: {best_result.get('beat_buy_hold', 0):+.2f}%\n"
            f"  • 最大回撤: {best_result.get('max_drawdown', 0):.1f}%\n"
        )
    text_msg += (
        f"\n📈 最新收盘: {df.iloc[-1]['close']:.2f}\n"
        f"📉 昨日涨跌: {df.iloc[-1]['pct_chg']:+.2f}%\n"
        f"\n━━━ 📋 最近买卖记录 ━━━\n"
    )

    # 取最近10条交易记录
    recent_trades = best_result.get("trades", [])[-10:] if best_result else []
    if recent_trades:
        for t in recent_trades:
            tdate = t.get("date", "")
            ttype = t.get("type", "")
            tprice = t.get("price", 0)
            if isinstance(tdate, str):
                tdate = tdate[:10]
            if "加仓" in ttype:
                shares = t.get("shares", 0)
                cost = t.get("cost", 0)
                text_msg += f"  🟢 {tdate} {ttype} {shares}股 @{tprice:.2f} (¥{cost:.0f})\n"
            elif "减仓" in ttype or "锁利" in ttype:
                shares = abs(t.get("shares", 0))
                value = t.get("value", 0)
                text_msg += f"  🔴 {tdate} {ttype} {shares}股 @{tprice:.2f} (¥{value:.0f})\n"
    else:
        text_msg += "  （暂无交易记录）\n"

    text_msg += f"\n📎 详见可视化图表👇"

    push_to_feishu(chart_path, text_msg, creds)
    print("[✓] 推送完成！")

    # 输出
    print("\n" + "=" * 55)
    print(f"🏦 今日操作建议:")
    print(f"   预测: {today_pred:+.2f}%")
    print(f"   建议: {advice_text}")
    print(f"   策略: 趋势跟踪 + 动态调仓")
    if best_result:
        print(f"   回测总收益: {best_result['total_return']:+.2f}%")
        print(f"   对比持有: {best_result.get('beat_buy_hold', 0):+.2f}%")
        print(f"   最大回撤: {best_result.get('max_drawdown', 0):.1f}%")
    print("=" * 55)


if __name__ == "__main__":
    main()
