#!/usr/bin/env python3
"""
因子工程 + 多因子模型训练 + 因子有效性分析
融合传统技术因子 + 外部因子（舆情/政策/新闻联播）
"""
import os, sys, json, time, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from collections import defaultdict

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache", "factors")
os.makedirs(CACHE_DIR, exist_ok=True)


# ============================================================
# 1. 技术因子计算（从K线中提取全部技术因子）
# ============================================================

def compute_technical_factors(kline: list) -> dict:
    """
    计算高信噪比技术因子（精选20+个核心因子）
    kline: 历史K线列表 [{date, open, close, high, low, volume}]
    返回最后一日的因子 dict
    """
    if not kline or len(kline) < 20:
        return {}
    
    closes = np.array([k["close"] for k in kline])
    highs = np.array([k["high"] for k in kline])
    lows = np.array([k["low"] for k in kline])
    vols = np.array([k.get("volume", 0) for k in kline], dtype=float)
    n = len(closes)
    
    factors = {}
    
    # === 动量因子（精选3个）===
    if n >= 6:
        factors["momentum_5d"] = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) * 100
    else:
        factors["momentum_5d"] = 0
    if n >= 11:
        factors["momentum_10d"] = (closes[-1] - closes[-11]) / max(closes[-11], 0.01) * 100
    else:
        factors["momentum_10d"] = 0
    if n >= 21:
        factors["momentum_20d"] = (closes[-1] - closes[-21]) / max(closes[-21], 0.01) * 100
    else:
        factors["momentum_20d"] = 0
    
    # === 均线偏离（精选2个）===
    # MA5偏离
    if n >= 5:
        ma5 = np.mean(closes[-5:])
        factors["ma_5_dev"] = (closes[-1] - ma5) / max(ma5, 0.01) * 100
    else:
        factors["ma_5_dev"] = 0
    # MA20偏离
    if n >= 20:
        ma20 = np.mean(closes[-20:])
        factors["ma_20_dev"] = (closes[-1] - ma20) / max(ma20, 0.01) * 100
        # 均线排列方向
        ma10 = np.mean(closes[-10:]) if n >= 10 else ma20
        factors["ma_trend"] = 1.0 if ma5 > ma10 > ma20 else (-1.0 if ma5 < ma10 < ma20 else 0.0)
    else:
        factors["ma_20_dev"] = 0
        factors["ma_trend"] = 0
    
    # === RSI(14) ===
    if n >= 16:
        delta = np.diff(closes[-16:])
        gains = delta[delta > 0].mean() if len(delta[delta > 0]) > 0 else 0
        losses = abs(delta[delta < 0].mean()) if len(delta[delta < 0]) > 0 else 1e-6
        factors["rsi_14"] = 100 - 100 / (1 + gains / losses)
    elif n >= 2:
        delta = np.diff(closes)
        gains = delta[delta > 0].mean() if len(delta[delta > 0]) > 0 else 0
        losses = abs(delta[delta < 0].mean()) if len(delta[delta < 0]) > 0 else 1e-6
        factors["rsi_14"] = 100 - 100 / (1 + gains / losses)
    else:
        factors["rsi_14"] = 50
    
    # === MACD信号值 ===
    if n >= 27:
        ema12 = _ema_np(closes, 12)
        ema26 = _ema_np(closes, 26)
        macd_line = ema12[-1] - ema26[-1]
        signal_line = _ema_np(np.array([ema12[i] - ema26[i] for i in range(26, len(ema12))]), 9)
        factors["macd"] = macd_line
        factors["macd_signal"] = signal_line[-1] if len(signal_line) > 0 else 0
        factors["macd_diff"] = macd_line - factors["macd_signal"]
    else:
        factors["macd"] = 0
        factors["macd_signal"] = 0
        factors["macd_diff"] = 0
    
    # === 布林带位置 ===
    if n >= 20:
        ma20_boll = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        upper = ma20_boll + 2 * std20
        lower = ma20_boll - 2 * std20
        factors["boll_position"] = (closes[-1] - lower) / max(upper - lower, 0.01)
        factors["boll_width"] = (upper - lower) / max(ma20_boll, 0.01) * 100  # 带宽
    else:
        factors["boll_position"] = 0.5
        factors["boll_width"] = 0
    
    # === 波动率 ===
    if n >= 6:
        ret_5d = np.diff(closes[-6:]) / closes[-6:-1] * 100
        factors["volatility_5d"] = np.std(ret_5d)
    else:
        factors["volatility_5d"] = 0
    if n >= 21:
        ret_20d = np.diff(closes[-21:]) / closes[-21:-1] * 100
        factors["volatility_20d"] = np.std(ret_20d)
    else:
        factors["volatility_20d"] = 0
    
    # === ATR(14) 平均真实波幅 ===
    if n >= 15:
        tr_list = []
        for i in range(-14, 0):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr_list.append(max(hl, hc, lc))
        factors["atr_14"] = np.mean(tr_list) / max(closes[-1], 0.01) * 100
    else:
        factors["atr_14"] = 0
    
    # === 成交量因子 ===
    if n >= 11:
        avg_vol_10 = np.mean(vols[-11:-1])
        factors["volume_ratio"] = vols[-1] / max(avg_vol_10, 1)
        # 量价配合（同向=1，背离=-1）
        price_dir = 1 if closes[-1] > closes[-2] else (-1 if closes[-1] < closes[-2] else 0)
        vol_dir = 1 if factors["volume_ratio"] > 1.2 else (-1 if factors["volume_ratio"] < 0.8 else 0)
        factors["volume_price_corr"] = 1.0 if price_dir * vol_dir > 0 else (-1.0 if price_dir * vol_dir < 0 else 0.0)
    else:
        factors["volume_ratio"] = 1
        factors["volume_price_corr"] = 0
    
    # === 价格位置（20日百分位）===
    if n >= 20:
        ph = np.max(highs[-20:])
        pl = np.min(lows[-20:])
        factors["price_position"] = (closes[-1] - pl) / max(ph - pl, 0.01)
    else:
        factors["price_position"] = 0.5
    
    # === 滚动夏普比率(20日) ===
    if n >= 21:
        ret_20d_full = np.diff(closes[-21:]) / closes[-21:-1] * 100
        factors["sharpe_20d"] = np.mean(ret_20d_full) / max(np.std(ret_20d_full), 0.01)
    else:
        factors["sharpe_20d"] = 0
    
    # === 收益分布（偏度+胜率）===
    if n >= 21:
        ret_20d_full = np.diff(closes[-21:]) / closes[-21:-1] * 100
        factors["return_skew_20d"] = float(pd.Series(ret_20d_full).skew())
        factors["win_rate_20d"] = np.mean(ret_20d_full > 0) * 100
    else:
        factors["return_skew_20d"] = 0
        factors["win_rate_20d"] = 50
    
    # === 形态特征 ===
    if n >= 6:
        # 过去5日最大涨幅和最大跌幅
        ret_5d_full = np.diff(closes[-6:]) / closes[-6:-1] * 100
        factors["max_gain_5d"] = np.max(ret_5d_full) if len(ret_5d_full) > 0 else 0
        factors["max_loss_5d"] = np.min(ret_5d_full) if len(ret_5d_full) > 0 else 0
        factors["up_days_5d"] = np.mean(ret_5d_full > 0) * 100
    else:
        factors["max_gain_5d"] = 0
        factors["max_loss_5d"] = 0
        factors["up_days_5d"] = 50
    
    return factors


def _ema_np(data: np.ndarray, period: int) -> np.ndarray:
    """numpy向量化指数移动平均"""
    if len(data) < period:
        return data
    multiplier = 2 / (period + 1)
    result = np.zeros(len(data))
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


# ============================================================
# 2. 外部因子映射到每日
# ============================================================

def load_external_factors() -> pd.DataFrame:
    """
    读取历史外部因子数据（从 factor_data 缓存的CSV）
    返回: DataFrame with columns [date, sentiment, policy_score, cctv_impact, composite]
    """
    csv_path = os.path.join(CACHE_DIR, "history_factors.csv")
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path)
    
    # 如果不存在，尝试实时获取最近的外部因子
    from factor_data import get_all_factors
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        factors = get_all_factors(today)
        df = pd.DataFrame([{
            "date": today,
            "sentiment": factors["factor_vector"][0],
            "policy_score": factors["factor_vector"][1],
            "cctv_impact": factors["factor_vector"][2],
            "composite": factors["composite_score"],
        }])
        return df
    except:
        # 全降级：返回空DataFrame（全0因子）
        return pd.DataFrame(columns=["date", "sentiment", "policy_score", "cctv_impact", "composite"])


def get_external_factor_for_date(date_str: str, external_df: pd.DataFrame) -> dict:
    """
    获取某日的外因子（如果外部数据不存在，返回默认值）
    """
    if len(external_df) == 0:
        return {"sentiment": 0, "policy_score": 0, "cctv_impact": 0, "composite": 0}
    
    row = external_df[external_df["date"] == date_str]
    if len(row) > 0:
        return {
            "sentiment": float(row.iloc[0].get("sentiment", 0)),
            "policy_score": float(row.iloc[0].get("policy_score", 0)),
            "cctv_impact": float(row.iloc[0].get("cctv_impact", 0)),
            "composite": float(row.iloc[0].get("composite", 0)),
        }
    return {"sentiment": 0, "policy_score": 0, "cctv_impact": 0, "composite": 0}


# ============================================================
# 3. 多因子模型训练（优化版：特征选择+集成+标准化）
# ============================================================

def _select_features_by_ic(X_df: pd.DataFrame, y: np.ndarray, min_ic: float = 0.02) -> list:
    """用IC筛选有效特征"""
    selected = []
    for col in X_df.columns:
        if X_df[col].nunique() <= 1:
            continue
        corr = X_df[col].corr(pd.Series(y))
        if not np.isnan(corr) and abs(corr) >= min_ic:
            selected.append(col)
    return selected


def _search_gb_params(X_train, y_train, X_val, y_val):
    """简单网格搜索梯度提升参数"""
    best_acc = 0
    best_params = {"n_estimators": 80, "max_depth": 3, "lr": 0.1}
    
    configs = [
        (60, 3, 0.1), (80, 3, 0.08), (100, 3, 0.08),
        (80, 4, 0.08), (120, 3, 0.05), (100, 4, 0.05),
        (60, 5, 0.1), (80, 5, 0.05),
    ]
    
    for n_est, depth, lr in configs:
        try:
            m = GradientBoostingRegressor(
                n_estimators=n_est, max_depth=depth, learning_rate=lr,
                min_samples_leaf=5, subsample=0.8, random_state=42
            )
            m.fit(X_train, y_train)
            pred = m.predict(X_val)
            acc = np.mean((pred > 0) == (y_val > 0)) * 100
            if acc > best_acc:
                best_acc = acc
                best_params = {"n_estimators": n_est, "max_depth": depth, "lr": lr}
        except:
            continue
    
    return best_params, best_acc


def train_multi_factor_model(kline_data: list, external_df: pd.DataFrame = None) -> dict:
    """
    多因子模型训练（优化版）
    步骤: 计算全部因子 -> IC筛选 -> 标准化 -> 集成模型(GB+RF) -> 超参搜索
    返回: {model, accuracy, cv_r2, feature_names, feature_importance, effective_factors, pred_pct, ...}
    """
    if external_df is None:
        external_df = load_external_factors()
    
    if not kline_data or len(kline_data) < 40:
        return {"model": None, "accuracy": 0, "feature_names": [],
                "feature_importance": {}, "pred_pct": 0, "effective_factors": []}
    
    try:
        from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        
        # 对所有K线点计算因子，构建时间序列数据集
        records = []
        for i in range(20, len(kline_data) - 1):
            sub_kl = kline_data[:i+1]
            factors = compute_technical_factors(sub_kl)
            if not factors:
                continue
            date_str = kline_data[i]["date"]
            ext = get_external_factor_for_date(date_str, external_df)
            factors.update({"sentiment": ext["sentiment"], "policy_score": ext["policy_score"],
                           "cctv_impact": ext["cctv_impact"]})
            factors["future_ret"] = (kline_data[i+1]["close"] - kline_data[i]["close"]) / max(kline_data[i]["close"], 0.01) * 100
            records.append(factors)
        
        if len(records) < 30:
            return {"model": None, "accuracy": 0, "feature_names": [],
                    "feature_importance": {}, "pred_pct": 0, "effective_factors": []}
        
        df = pd.DataFrame(records)
        feature_cols = [c for c in df.columns if c not in ("future_ret", "date")]
        X_df = df[feature_cols]
        y = df["future_ret"].values
        
        # === 步骤1: IC筛选 ===
        selected = _select_features_by_ic(X_df, y, min_ic=0.02)
        # 如果筛选后太少，回退用全部特征（但排除冗余）
        if len(selected) < 5:
            # 只移除完全无信息的特征（零方差）
            selected = [c for c in feature_cols if X_df[c].nunique() > 1]
        
        X_selected = X_df[selected].values
        
        # === 步骤2: 时间序列交叉验证（按时间分割）===
        split_idx = int(len(X_selected) * 0.8)
        X_train, X_val = X_selected[:split_idx], X_selected[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]
        
        # === 步骤3: 标准化 ===
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_val_scaled = scaler.transform(X_val)
        
        # === 步骤4: 超参搜索(在验证集上) ===
        gb_params, gb_val_acc = _search_gb_params(X_train_scaled, y_train, X_val_scaled, y_val)
        
        # === 步骤5: 训练集成模型 ===
        # GB
        gb = GradientBoostingRegressor(
            n_estimators=gb_params["n_estimators"],
            max_depth=gb_params["max_depth"],
            learning_rate=gb_params["lr"],
            min_samples_leaf=5, subsample=0.8, random_state=42
        )
        gb.fit(X_train_scaled, y_train)
        
        # RF
        rf = RandomForestRegressor(
            n_estimators=100, max_depth=5, min_samples_leaf=5,
            max_features='sqrt', random_state=42
        )
        rf.fit(X_train_scaled, y_train)
        
        # 验证集性能
        gb_pred_val = gb.predict(X_val_scaled)
        rf_pred_val = rf.predict(X_val_scaled)
        gb_acc_val = np.mean((gb_pred_val > 0) == (y_val > 0)) * 100
        rf_acc_val = np.mean((rf_pred_val > 0) == (y_val > 0)) * 100
        
        # 集成权重（用验证集R2决定）
        from sklearn.metrics import r2_score
        gb_r2 = max(0, r2_score(y_val, gb_pred_val))
        rf_r2 = max(0, r2_score(y_val, rf_pred_val))
        total_r2 = gb_r2 + rf_r2
        w_gb = gb_r2 / total_r2 if total_r2 > 0 else 0.5
        w_rf = rf_r2 / total_r2 if total_r2 > 0 else 0.5
        
        # 整体（全量数据）
        X_all_scaled = scaler.transform(X_selected)
        gb.fit(X_all_scaled, y)
        rf.fit(X_all_scaled, y)
        
        gb_pred = gb.predict(X_all_scaled)
        rf_pred = rf.predict(X_all_scaled)
        ensemble_pred = gb_pred * w_gb + rf_pred * w_rf
        
        accuracy = np.mean((ensemble_pred > 0) == (y > 0)) * 100
        ensemble_acc_val = (gb_acc_val * w_gb + rf_acc_val * w_rf)
        
        # === 特征重要性（平均GB和RF）===
        gb_imp = dict(zip(selected, gb.feature_importances_))
        rf_imp = dict(zip(selected, rf.feature_importances_))
        avg_imp = {k: (gb_imp.get(k, 0) + rf_imp.get(k, 0)) / 2 for k in selected}
        sorted_imp = sorted(avg_imp.items(), key=lambda x: -x[1])
        
        # === 有效因子 ===
        _feature_ic = {c: abs(X_df[c].corr(pd.Series(y))) for c in selected}
        effective = [name for name, imp in sorted_imp if imp > 0.02]
        effective += [name for name, ic in _feature_ic.items() if ic > 0.03 and name not in effective]
        effective = list(dict.fromkeys(effective))[:12]  # 去重，最多12个
        
        # === 最新预测 ===
        last_factors = compute_technical_factors(kline_data)
        if last_factors:
            last_feats = [last_factors.get(k, 0) for k in selected]
            ext_latest = get_external_factor_for_date(datetime.now().strftime("%Y-%m-%d"), external_df)
            last_feats_extra = last_feats + [ext_latest["sentiment"], ext_latest["policy_score"], ext_latest["cctv_impact"]]
            # 如果 selected 中包含了外部因子
            feat_array = np.array([last_feats_extra[i] if i < len(last_feats_extra) else last_feats[i] for i in range(len(selected))]).reshape(1, -1)
            feat_scaled = scaler.transform(feat_array)
            pred_gb = float(gb.predict(feat_scaled)[0])
            pred_rf = float(rf.predict(feat_scaled)[0])
            pred_pct = pred_gb * w_gb + pred_rf * w_rf
        else:
            pred_pct = 0
        
        return {
            "model": (gb, rf, scaler, w_gb, w_rf),  # 元组，用于预测
            "gb_model": gb,
            "rf_model": rf,
            "scaler": scaler,
            "ensemble_weight_gb": w_gb,
            "ensemble_weight_rf": w_rf,
            "accuracy": round(accuracy, 1),
            "val_accuracy": round(ensemble_acc_val, 1),
            "cv_r2": round(gb_r2 + rf_r2, 4),
            "feature_names": selected,
            "feature_importance": sorted_imp,
            "effective_factors": effective,
            "gb_params": gb_params,
            "pred_pct": round(pred_pct, 2),
        }
        
    except Exception as e:
        print(f"  ⚠️ 多因子模型训练失败: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return {"model": None, "accuracy": 0, "feature_names": [],
                "feature_importance": {}, "pred_pct": 0, "effective_factors": []}


def _predict_ensemble(model_tuple, features_scaled):
    """集成模型预测"""
    gb, rf, scaler, w_gb, w_rf = model_tuple
    feat = features_scaled.reshape(1, -1) if features_scaled.ndim == 1 else features_scaled
    pred_gb = gb.predict(feat)
    pred_rf = rf.predict(feat)
    return pred_gb * w_gb + pred_rf * w_rf


def train_multi_factor_backtest_model(kline_data: list, external_df: pd.DataFrame = None) -> tuple:
    """
    回测版多因子模型训练 — 返回 (model, accuracy, feature_names, feature_importance, effective_factors)
    与 backtest_strategy.py 兼容
    """
    result = train_multi_factor_model(kline_data, external_df)
    model = result["model"]
    # 如果模型为None，降级到纯技术模型
    if model is None:
        # 用 backtest_strategy 的原始训练
        from backtest_strategy import train_model_for_backtest
        m, acc, *_ = train_model_for_backtest(kline_data, use_external_factors=False)
        return m, acc, [], {}, []
    return model, result["accuracy"], result["feature_names"], result["feature_importance"], result["effective_factors"]


def compute_enhanced_signal(kline: list, model_result: dict = None, external_factors: dict = None) -> tuple:
    """
    增强信号计算 — 融合多因子模型预测 + 外部因子调整 + 技术信号确认
    返回: (signal_score, reasons_list, pred_pct)
    
    Args:
        kline: 历史K线数据
        model_result: 可选，来自 train_multi_factor_model 的结果
        external_factors: 可选，来自 get_all_factors 的结果
    """
    if not kline or len(kline) < 10:
        return 0, [], 0
    
    closes = np.array([k["close"] for k in kline])
    highs = np.array([k["high"] for k in kline])
    lows = np.array([k["low"] for k in kline])
    vols = np.array([k.get("volume", 0) for k in kline], dtype=float)
    n = len(closes)
    
    score = 0.0
    reasons = []
    pred_pct = 0
    
    # === 1. 模型预测信号（如果有模型）===
    if model_result and model_result.get("model") is not None:
        model_tuple = model_result["model"]
        selected = model_result.get("feature_names", [])
        if selected:
            last_factors = compute_technical_factors(kline)
            if last_factors:
                feats = [last_factors.get(k, 0) for k in selected]
                # 标准化
                scaler = model_tuple[2]  # model_tuple = (gb, rf, scaler, w_gb, w_rf)
                feat_scaled = scaler.transform(np.array(feats).reshape(1, -1))
                pred = _predict_ensemble(model_tuple, feat_scaled.flatten())[0]
                pred_pct = float(pred)
                
                # 模型预测方向贡献
                if pred_pct > 0.5:
                    score += 0.25; reasons.append(f"模型预测涨{pred_pct:+.2f}%")
                elif pred_pct > 0.2:
                    score += 0.15; reasons.append(f"模型预测涨{pred_pct:+.2f}%")
                elif pred_pct < -0.5:
                    score -= 0.25; reasons.append(f"模型预测跌{pred_pct:+.2f}%")
                elif pred_pct < -0.2:
                    score -= 0.15; reasons.append(f"模型预测跌{pred_pct:+.2f}%")
                else:
                    reasons.append(f"模型预测平({pred_pct:+.2f}%)")
    
    # === 2. 外部因子调整 ===
    if external_factors:
        ext_vec = external_factors.get("factor_vector", [0, 0, 0])
        sentiment = ext_vec[0]
        policy = ext_vec[1]
        cctv = ext_vec[2]
        
        # 舆情贡献（±0.10）
        if sentiment > 0.2:
            score += 0.10; reasons.append(f"舆情偏正({sentiment:+.2f})")
        elif sentiment < -0.2:
            score -= 0.10; reasons.append(f"舆情偏负({sentiment:.2f})")
        
        # 政策贡献（±0.15）
        if policy > 0.2:
            score += 0.15; reasons.append(f"政策利好({policy:+.2f})")
        elif policy < -0.2:
            score -= 0.15; reasons.append(f"政策利空({policy:.2f})")
        
        # 新闻联播贡献（±0.05）
        if cctv > 0.1:
            score += 0.05; reasons.append("新闻联播经济向好")
        elif cctv < -0.1:
            score -= 0.05; reasons.append("新闻联播提及风险")
    
    # === 3. 技术信号确认 ===
    # 均线趋势
    if n >= 10:
        ma5 = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:])
        if closes[-1] > ma5 > ma10:
            score += 0.20; reasons.append("均线多头排列")
        elif closes[-1] < ma5 < ma10:
            score -= 0.20; reasons.append("均线空头排列")
        elif closes[-1] > ma5:
            score += 0.10; reasons.append("站上5日均线")
        else:
            score -= 0.10; reasons.append("破5日均线")
    
    # 价格位置
    if n >= 20:
        ph = np.max(highs[-20:])
        pl = np.min(lows[-20:])
        pos = (closes[-1] - pl) / max(ph - pl, 0.01)
        if pos < 0.2:
            score += 0.15; reasons.append(f"价格低位({pos*100:.0f}%分位)")
        elif pos > 0.8:
            score -= 0.15; reasons.append(f"价格高位({pos*100:.0f}%分位)")
    
    # RSI
    if n >= 16:
        delta = np.diff(closes[-16:])
        gains = delta[delta > 0].mean() if len(delta[delta > 0]) > 0 else 0
        losses = abs(delta[delta < 0].mean()) if len(delta[delta < 0]) > 0 else 1e-6
        rsi = 100 - 100 / (1 + gains / losses)
        if rsi < 30:
            score += 0.15; reasons.append(f"RSI超卖({rsi:.0f})")
        elif rsi > 70:
            score -= 0.15; reasons.append(f"RSI超买({rsi:.0f})")
        else:
            reasons.append(f"RSI中性({rsi:.0f})")
    
    # 成交量确认
    if n >= 6:
        vol_ratio = vols[-1] / max(np.mean(vols[-6:-1]), 1)
        if vol_ratio > 1.5 and score > 0:
            score += 0.10; reasons.append(f"放量上涨(量比{vol_ratio:.1f})")
        elif vol_ratio > 1.5 and score < 0:
            score -= 0.10; reasons.append(f"放量下跌(量比{vol_ratio:.1f})")
        elif vol_ratio < 0.5:
            reasons.append(f"缩量(量比{vol_ratio:.1f})")
        else:
            reasons.append(f"量能正常(量比{vol_ratio:.1f})")
    
    # 如果技术信号与模型方向相反，削弱信号强度
    if pred_pct != 0 and score != 0:
        tech_direction = 1 if score > 0 else -1
        model_direction = 1 if pred_pct > 0 else -1
        if tech_direction != model_direction:
            score *= 0.5  # 减半
            reasons.append("⚠️ 技术信号与模型方向相反")
    
    return max(min(score, 1.0), -1.0), reasons, round(pred_pct, 2)


# ============================================================
# 4. 因子有效性分析
# ============================================================

def analyze_factor_effectiveness(kline_data: list, external_df: pd.DataFrame = None) -> dict:
    """
    分析各因子的有效性
    返回: {
        'factor_name': {
            'ic': 信息系数,
            'rank_ic': 秩信息系数,
            'directional_accuracy': 方向准确率,
            'sharpe': 分层回测夏普,
            'is_effective': 是否有效
        }
    }
    """
    if external_df is None:
        external_df = load_external_factors()
    
    if not kline_data or len(kline_data) < 40:
        return {}
    
    # 对所有时间点计算所有因子
    records = []
    for i in range(20, len(kline_data) - 1):
        sub_kl = kline_data[:i+1]
        factors = compute_technical_factors(sub_kl)
        if not factors:
            continue
        date_str = kline_data[i]["date"]
        ext = get_external_factor_for_date(date_str, external_df)
        factors.update({
            "sentiment": ext["sentiment"],
            "policy_score": ext["policy_score"],
            "cctv_impact": ext["cctv_impact"],
            "composite": ext["composite"],
        })
        factors["future_ret"] = (kline_data[i+1]["close"] - kline_data[i]["close"]) / max(kline_data[i]["close"], 0.01) * 100
        records.append(factors)
    
    if not records:
        return {}
    
    df = pd.DataFrame(records)
    
    analysis = {}
    factor_cols = [c for c in df.columns if c not in ("future_ret", "date")]
    
    for col in factor_cols:
        valid = df[col].notna() & df["future_ret"].notna()
        if valid.sum() < 10:
            continue
        
        try:
            # IC (Information Coefficient) — 因子值与未来收益的相关系数
            ic = df.loc[valid, col].corr(df.loc[valid, "future_ret"])
            
            # Rank IC
            rank_ic = df.loc[valid, col].corr(df.loc[valid, "future_ret"], method="spearman")
            
            # 方向准确率
            factor_direction = (df[col] > df[col].median()).astype(int) if df[col].nunique() > 1 else (df[col] > 0).astype(int)
            direction_acc = np.mean((factor_direction == (df["future_ret"] > 0).astype(int)).astype(float)) * 100
            
            # 分层回测（简单版：高于中位数做多，低于中位数做空）
            if df[col].nunique() > 1:
                long = df.loc[df[col] > df[col].quantile(0.7), "future_ret"].mean()
                short = df.loc[df[col] < df[col].quantile(0.3), "future_ret"].mean()
                spread = long - short
            else:
                spread = 0
            
            is_effective = abs(ic) > 0.03 or direction_acc > 55 or abs(spread) > 0.3
            
            analysis[col] = {
                "ic": round(ic, 4),
                "rank_ic": round(rank_ic, 4),
                "directional_acc": round(direction_acc, 1),
                "long_short_spread": round(spread, 3),
                "is_effective": is_effective,
                "sample_count": int(valid.sum()),
            }
        except:
            pass
    
    return analysis


# ============================================================
# 5. 获取有效因子列表（用于回测和实盘）
# ============================================================

def get_effective_factor_names(kline_data: list = None, external_df: pd.DataFrame = None, min_ic: float = 0.02) -> list:
    """
    获取有效因子名列表（IC > min_ic 或方向准确率 > 55%）
    """
    if kline_data is None:
        # 返回所有默认因子名
        return _default_factor_names()
    
    analysis = analyze_factor_effectiveness(kline_data, external_df)
    
    effective = [name for name, info in analysis.items() if info.get("is_effective", False)]
    
    if not effective:
        return _default_factor_names()
    
    return effective


def _default_factor_names() -> list:
    """默认因子列表"""
    return [
        "momentum_1d", "momentum_5d", "ma_5_dev", "ma_10_dev", "ma_20_dev",
        "rsi_14", "macd", "macd_diff", "boll_position",
        "volume_ratio_5", "volume_price_corr", "price_position_20d",
        "volatility_5d", "max_drawdown_5d", "win_rate_20d",
        "sentiment", "policy_score", "cctv_impact"
    ]


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    # 测试技术因子计算
    kline = [
        {"date": f"2026-01-{i+1:02d}", "open": 10 + i*0.1, "close": 10 + i*0.1,
         "high": 10.5 + i*0.1, "low": 9.5 + i*0.1, "volume": 100 + i*5}
        for i in range(60)
    ]
    
    factors = compute_technical_factors(kline)
    print(f"技术因子数: {len(factors)}")
    print(f"因子列表: {sorted(factors.keys())[:10]}...")
    
    # 测试因子有效性分析
    ext_df = pd.DataFrame({
        "date": [k["date"] for k in kline],
        "sentiment": np.random.uniform(-0.5, 0.5, len(kline)),
        "policy_score": np.random.uniform(-0.3, 0.3, len(kline)),
        "cctv_impact": np.random.uniform(-0.2, 0.2, len(kline)),
        "composite": np.random.uniform(-0.4, 0.4, len(kline)),
    })
    
    analysis = analyze_factor_effectiveness(kline, ext_df)
    effective = [(k, v["ic"], v["directional_acc"]) for k, v in analysis.items() if v.get("is_effective", False)]
    print(f"有效因子数: {len(effective)}")
    for name, ic, acc in effective[:5]:
        print(f"  {name}: IC={ic:.4f}, 准确率={acc:.1f}%")
