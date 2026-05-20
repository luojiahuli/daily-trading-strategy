#!/usr/bin/env python3
"""
交易数据库模块 — SQLite存储历史回测数据和每日实盘数据
"""
import os, json, sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_data", "trading.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_conn():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化所有表"""
    conn = get_conn()
    cur = conn.cursor()
    
    # 每日实盘快照
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            capital REAL NOT NULL,
            position_value REAL NOT NULL DEFAULT 0,
            total_value REAL NOT NULL,
            daily_pct REAL NOT NULL DEFAULT 0,
            total_pct REAL NOT NULL DEFAULT 0,
            position_count INTEGER NOT NULL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 持仓明细（每个快照的持仓）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            board TEXT DEFAULT '',
            buy_date TEXT,
            buy_price REAL,
            current_price REAL,
            shares INTEGER,
            pnl_pct REAL DEFAULT 0,
            signal_score REAL DEFAULT 0,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 交易流水
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            action TEXT NOT NULL,  -- 买入/卖出
            code TEXT NOT NULL,
            name TEXT DEFAULT '',
            board TEXT DEFAULT '',
            buy_date TEXT,
            hold_days INTEGER DEFAULT 0,
            buy_price REAL,
            sell_price REAL DEFAULT 0,
            shares INTEGER,
            pnl REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            capital REAL DEFAULT 0,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 回测结果汇总
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,         -- 回测批次标识, e.g. "20260520_2203"
            run_date TEXT NOT NULL,        -- 运行日期
            stock_count INTEGER,
            win_count INTEGER,
            lose_count INTEGER,
            avg_return REAL,
            avg_drawdown REAL,
            win_rate REAL,
            total_trades INTEGER,
            trade_win_rate REAL,
            portfolio_return REAL,
            model_accuracy REAL,
            stock_codes TEXT,             -- JSON数组
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 回测个股明细
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT DEFAULT '',
            board TEXT DEFAULT '',
            total_return REAL,
            buy_hold_return REAL,
            beat_buy_hold REAL,
            max_drawdown REAL,
            sharpe REAL,
            trade_count INTEGER,
            capital_end REAL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 回测交易流水
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT DEFAULT '',
            board TEXT DEFAULT '',
            action TEXT NOT NULL,  -- 买入/止损/趋势清仓/最终清仓/到期/减仓
            price REAL,
            shares INTEGER,
            pnl_pct REAL DEFAULT 0,
            capital REAL DEFAULT 0,
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 回测净值曲线
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_equity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            date TEXT NOT NULL,
            total_value REAL,
            holdings INTEGER DEFAULT 0,
            drawdown REAL DEFAULT 0,
            UNIQUE(run_id, date)
        )
    """)
    
    # 每日回测（每天自动跑的回测）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_backtest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            stock_count INTEGER,
            avg_return REAL,
            avg_drawdown REAL,
            win_rate REAL,
            portfolio_return REAL,
            best_stock TEXT,
            best_return REAL,
            worst_stock TEXT,
            worst_return REAL,
            top3_codes TEXT,     -- JSON
            top3_returns TEXT,   -- JSON
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # ==== 因子相关表 ====
    
    # 每日外部因子（舆情/政策/新闻联播）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            sentiment REAL DEFAULT 0,
            policy_score REAL DEFAULT 0,
            cctv_impact REAL DEFAULT 0,
            composite_score REAL DEFAULT 0,
            sentiment_source TEXT DEFAULT 'fallback',
            policy_source TEXT DEFAULT 'fallback',
            cctv_source TEXT DEFAULT 'fallback',
            hot_topics TEXT DEFAULT '',       -- JSON array
            policy_titles TEXT DEFAULT '',     -- JSON array
            cctv_headlines TEXT DEFAULT '',    -- JSON array
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 单股日度因子（技术因子 + 外部因子）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT DEFAULT '',
            board TEXT DEFAULT '',
            tech_factors TEXT DEFAULT '{}',
            sentiment_factor REAL DEFAULT 0,
            policy_factor REAL DEFAULT 0,
            cctv_factor REAL DEFAULT 0,
            future_return_1d REAL,
            future_return_3d REAL,
            future_return_5d REAL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, code)
        )
    """)
    
    # 因子有效性分析
    cur.execute("""
        CREATE TABLE IF NOT EXISTS factor_effectiveness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            factor_name TEXT NOT NULL,
            ic REAL,
            rank_ic REAL,
            directional_acc REAL,
            long_short_spread REAL,
            is_effective INTEGER DEFAULT 0,
            sample_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    # 模型版本（每次训练记录）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS model_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            train_date TEXT NOT NULL,
            model_type TEXT DEFAULT 'GradientBoosting',
            accuracy REAL,
            cv_r2 REAL,
            feature_count INTEGER,
            effective_factor_count INTEGER,
            effective_factors TEXT DEFAULT '[]',
            feature_importance TEXT DEFAULT '{}',
            pred_pct REAL DEFAULT 0,
            data_span TEXT DEFAULT '',   -- e.g. "2024-06 ~ 2026-05"
            stock_count INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成: {DB_PATH}")


# ============================================================
# 实盘数据写入
# ============================================================

def save_daily_snapshot(date, capital, pos_value, total_value, 
                        daily_pct, total_pct, position_count, max_drawdown=0):
    """保存每日快照"""
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO daily_snapshots 
        (date, capital, position_value, total_value, daily_pct, total_pct, position_count, max_drawdown)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, capital, pos_value, total_value, daily_pct, total_pct, position_count, max_drawdown))
    conn.commit()
    conn.close()


def save_positions(date, positions):
    """保存持仓明细"""
    conn = get_conn()
    conn.execute("DELETE FROM positions WHERE date=?", (date,))
    for p in positions:
        conn.execute("""
            INSERT INTO positions (date, code, name, board, buy_date, buy_price, current_price, shares, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, p.get("code",""), p.get("name",""), p.get("board",""),
              p.get("buy_date",""), p.get("buy_price",0), p.get("current_price",0),
              p.get("shares",0), p.get("pnl_pct",0)))
    conn.commit()
    conn.close()


def save_trade(trade):
    """保存一笔交易"""
    conn = get_conn()
    conn.execute("""
        INSERT INTO trades (date, action, code, name, board, buy_date, hold_days,
                           buy_price, sell_price, shares, pnl, pnl_pct, capital, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.get("date",""), trade.get("action",""), trade.get("code",""),
        trade.get("name",""), trade.get("board",""), trade.get("buy_date",""),
        trade.get("hold_days",0), trade.get("buy_price",0), trade.get("sell_price",0),
        trade.get("shares",0), trade.get("pnl",0), trade.get("pnl_pct",0),
        trade.get("capital",0), trade.get("reason","")
    ))
    conn.commit()
    conn.close()


# ============================================================
# 回测数据写入
# ============================================================

def save_backtest_summary(run_id, run_date, results, trades, model_acc):
    """保存回测汇总"""
    if not results:
        return
    wins = sum(1 for r in results if r.get("total_return", 0) > 0)
    loses = len(results) - wins
    avg_ret = sum(r.get("total_return", 0) for r in results) / len(results)
    avg_dd = sum(r.get("max_drawdown", 0) for r in results) / len(results)
    win_rate = wins / len(results) * 100
    
    # 交易胜率
    close_trades = [t for t in trades if t.get("type") in ("趋势清仓", "止损", "最终清仓")]
    trade_wins = sum(1 for t in close_trades if t.get("pnl_pct", 0) > 0)
    trade_win_rate = trade_wins / max(len(close_trades), 1) * 100
    
    portfolio_ret = sum(r.get("total_return", 0) for r in results) / len(results)
    stock_codes = json.dumps([r.get("code") for r in results], ensure_ascii=False)
    
    conn = get_conn()
    conn.execute("""
        INSERT INTO backtest_summary 
        (run_id, run_date, stock_count, win_count, lose_count, avg_return, avg_drawdown,
         win_rate, total_trades, trade_win_rate, portfolio_return, model_accuracy, stock_codes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, run_date, len(results), wins, loses, round(avg_ret,2), round(avg_dd,2),
          round(win_rate,1), len(trades), round(trade_win_rate,1), round(portfolio_ret,2),
          model_acc, stock_codes))
    conn.commit()
    conn.close()


def save_backtest_stocks(run_id, results):
    """保存回测个股明细"""
    if not results:
        return
    conn = get_conn()
    for r in results:
        conn.execute("""
            INSERT INTO backtest_stocks 
            (run_id, code, name, board, total_return, buy_hold_return, beat_buy_hold,
             max_drawdown, sharpe, trade_count, capital_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, r.get("code",""), r.get("name",""), r.get("board",""),
              r.get("total_return",0), r.get("buy_hold_return",0), r.get("beat_buy_hold",0),
              r.get("max_drawdown",0), r.get("sharpe",0), len(r.get("trades",[])),
              r.get("capital",0)))
    conn.commit()
    conn.close()


def save_backtest_trades(run_id, trades):
    """保存回测交易流水"""
    if not trades:
        return
    conn = get_conn()
    # 先清空该run_id的数据（防止重复）
    conn.execute("DELETE FROM backtest_trades WHERE run_id=?", (run_id,))
    for t in trades:
        action = t.get("type", "买入")
        conn.execute("""
            INSERT INTO backtest_trades 
            (run_id, date, code, name, board, action, price, shares, pnl_pct, capital, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, t.get("date",""), t.get("code",""), t.get("name",""),
              t.get("board",""), action, t.get("price",0), t.get("shares",0),
              t.get("pnl_pct",0), t.get("capital",0), t.get("reason","")))
    conn.commit()
    conn.close()


def save_backtest_equity(run_id, equity_curves):
    """保存回测净值曲线（等权组合）"""
    if not equity_curves:
        return
    from collections import defaultdict
    merged = defaultdict(list)
    for eq in equity_curves:
        for point in eq:
            merged[point["date"]].append(point["total_value"])
    
    conn = get_conn()
    for date in sorted(merged.keys()):
        vals = merged[date]
        total_count = len(merged)
        avg_val = sum(vals) / len(vals) * (len(vals) / max(total_count, 1)) + 100000 * (1 - len(vals) / max(total_count, 1))
        conn.execute("""
            INSERT OR REPLACE INTO backtest_equity (run_id, date, total_value)
            VALUES (?, ?, ?)
        """, (run_id, date, round(avg_val, 2)))
    conn.commit()
    conn.close()


def save_daily_backtest_summary(date, run_id, results):
    """保存每日回测摘要"""
    if not results:
        return
    stock_count = len(results)
    avg_ret = sum(r.get("total_return",0) for r in results) / stock_count
    avg_dd = sum(r.get("max_drawdown",0) for r in results) / stock_count
    wins = sum(1 for r in results if r.get("total_return",0) > 0)
    win_rate = wins / stock_count * 100
    portfolio_ret = avg_ret
    
    sorted_by_ret = sorted(results, key=lambda x: -x.get("total_return",0))
    best = sorted_by_ret[0] if sorted_by_ret else {}
    worst = sorted_by_ret[-1] if len(sorted_by_ret) > 1 else {}
    top3 = sorted_by_ret[:3]
    
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO daily_backtest
        (date, run_id, stock_count, avg_return, avg_drawdown, win_rate, portfolio_return,
         best_stock, best_return, worst_stock, worst_return,
         top3_codes, top3_returns)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, run_id, stock_count, round(avg_ret,2), round(avg_dd,2),
          round(win_rate,1), round(portfolio_ret,2),
          best.get("code",""), best.get("total_return",0),
          worst.get("code",""), worst.get("total_return",0),
          json.dumps([r.get("code") for r in top3], ensure_ascii=False),
          json.dumps([round(r.get("total_return",0),2) for r in top3], ensure_ascii=False)))
    conn.commit()
    conn.close()


# ============================================================
# 查询接口
# ============================================================

def get_latest_daily_snapshot():
    """获取最近一次快照"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_daily_snapshots(limit=60):
    """获取近期快照列表"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_daily_factors(date, sentiment, policy_score, cctv_impact, composite_score,
                       sentiment_source="fallback", policy_source="fallback", cctv_source="fallback",
                       hot_topics=None, policy_titles=None, cctv_headlines=None):
    """保存每日外部因子"""
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO daily_factors
        (date, sentiment, policy_score, cctv_impact, composite_score,
         sentiment_source, policy_source, cctv_source,
         hot_topics, policy_titles, cctv_headlines)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, sentiment, policy_score, cctv_impact, composite_score,
          sentiment_source, policy_source, cctv_source,
          json.dumps(hot_topics or [], ensure_ascii=False),
          json.dumps(policy_titles or [], ensure_ascii=False),
          json.dumps(cctv_headlines or [], ensure_ascii=False)))
    conn.commit()
    conn.close()


def save_factor_effectiveness(date, analysis: dict):
    """保存因子有效性分析结果"""
    conn = get_conn()
    # 先清理旧数据
    conn.execute("DELETE FROM factor_effectiveness WHERE date=?", (date,))
    for factor_name, info in analysis.items():
        conn.execute("""
            INSERT INTO factor_effectiveness
            (date, factor_name, ic, rank_ic, directional_acc, long_short_spread, is_effective, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, factor_name, info.get("ic", 0), info.get("rank_ic", 0),
              info.get("directional_acc", 0), info.get("long_short_spread", 0),
              1 if info.get("is_effective", False) else 0,
              info.get("sample_count", 0)))
    conn.commit()
    conn.close()


def save_model_version(version, train_date, accuracy, cv_r2, feature_count,
                       effective_factor_count, effective_factors, feature_importance,
                       pred_pct, data_span, stock_count):
    """保存模型版本"""
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO model_versions
        (version, train_date, accuracy, cv_r2, feature_count, effective_factor_count,
         effective_factors, feature_importance, pred_pct, data_span, stock_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (version, train_date, accuracy, cv_r2, feature_count, effective_factor_count,
          json.dumps(effective_factors, ensure_ascii=False),
          json.dumps(dict(feature_importance), ensure_ascii=False),
          pred_pct, data_span, stock_count))
    conn.commit()
    conn.close()


def get_latest_model_version():
    """获取最新模型版本"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM model_versions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_daily_factors():
    """获取最新外部因子"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_factors ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_effective_factors(date=None, min_ic=0.02):
    """获取有效因子列表"""
    conn = get_conn()
    if date:
        rows = conn.execute(
            "SELECT * FROM factor_effectiveness WHERE date=? AND is_effective=1",
            (date,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM factor_effectiveness WHERE is_effective=1 ORDER BY ic DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_trades(limit=50):
    """获取近期交易"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_backtest():
    """获取最近一次回测汇总"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM backtest_summary ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_backtest_history(limit=10):
    """获取回测历史"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM backtest_summary ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_backtest_history(limit=30):
    """获取每日回测历史"""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM daily_backtest ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
