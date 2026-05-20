#!/usr/bin/env python3
"""
实盘模拟交易系统 - 10万本金，两周运行

策略: 策略A 基础窗口期
  - 买入: 前一日涨幅2~9%的个股，次日开盘买入
  - 卖出: 盘中达+5%止盈 / -3%止损 / 持仓5日到期
  - 仓位: 等权重，最多同时3只，单只≤33%

每日流程:
  1. 扫描板块 -> 筛选候选股 -> 回测验证
  2. 检查持仓 -> 止盈/止损/到期卖出
  3. 买入新的候选股（最多3只）
  4. 更新资金曲线
  5. 输出日报

数据源: 新浪财经API（实时K线 + 当日行情）
"""

import os, sys, json, time, re as re2
from datetime import datetime, timedelta, date
from typing import Optional

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests as req

sys.path.insert(0, os.path.dirname(__file__))
from stock_theme_analyzer import get_all_concept_boards, get_board_constituents, safe_float

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "live_trading")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 配置
# ============================================================
INITIAL_CAPITAL = 100000  # 10万
MAX_POSITIONS = 3         # 最多3只
STOP_LOSS = -3.0         # 止损-3%
TAKE_PROFIT = 5.0        # 止盈+5%
MAX_HOLD = 5             # 最长5日
COMMISSION_RATE = 0.0003 # 佣金万三
STAMP_TAX = 0.001        # 印花税千一（卖出）

# ============================================================
# 数据获取
# ============================================================
def get_kline(code: str, days: int = 45) -> Optional[list]:
    """新浪财经获取日K线"""
    try:
        code = code.strip()
        symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
        url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(days)}
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = req.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200: return None
        match = re2.search(r'\[.*\]', resp.text)
        if not match: return None
        data = json.loads(match.group())
        records = []
        for d in data:
            records.append({"date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                          "high": float(d["high"]), "low": float(d["low"])})
            if len(records) >= 2:
                prev = records[-2]["close"]
                records[-1]["pct"] = round((records[-1]["close"] - prev) / prev * 100, 2)
            else:
                records[-1]["pct"] = 0.0
        return records
    except:
        return None


def get_today_quotes(codes: list) -> dict:
    """通过聚合数据API获取多只个股实时行情"""
    quotes = {}
    api_key = os.environ.get("JUHE_API_KEY", "")
    for code in codes:
        try:
            symbol = f"sh{code}" if code.startswith(("6","9")) else f"sz{code}"
            params = {"key": api_key, "gid": symbol}
            resp = req.get("https://web.juhe.cn/finance/stock/hs", params=params, timeout=10)
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
                                "volume": safe_float(d.get("traNumber", 0)),
                            }
        except:
            pass
        time.sleep(0.3)
    return quotes


# ============================================================
# 选股引擎
# ============================================================
def screen_candidates(trade_date: str) -> list:
    """
    扫描板块成分股，筛选窗口期候选股

    用 trade_date 前一天的数据来判断买入信号
    """
    print(f"  [*] 扫描候选股 ({trade_date})...")
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
    # 只保留窗口期条件：涨幅2~9%
    window_candidates = [c for c in candidates if 2.0 <= c["inc"] <= 9.0]
    print(f"    [+] 共 {len(window_candidates)} 只窗口期候选 (全部{len(candidates)})")
    return window_candidates[:MAX_POSITIONS * 2]


# ============================================================
# 交易系统
# ============================================================
class TradingSystem:
    def __init__(self):
        self.capital = INITIAL_CAPITAL
        self.positions = []  # [{code, name, board, buy_date, buy_price, shares, cost}]
        self.trade_log = []  # [{date, action, code, name, price, shares, pnl, reason}]
        self.daily_snapshots = []  # [{date, capital, position_value, total_value, pct}]
        self.current_date = None

    def value(self):
        """当前总资产 = 现金 + 持仓市值"""
        pos_value = sum(p["shares"] * p["current_price"] for p in self.positions)
        return self.capital + pos_value

    def check_stop_loss_take_profit(self, date_str: str, quotes: dict):
        """盘中检查止盈止损"""
        sold = []
        for p in self.positions[:]:
            quote = quotes.get(p["code"])
            if not quote:
                continue
            p["current_price"] = quote["price"]
            p["high"] = quote["high"]
            p["low"] = quote["low"]

            # 持仓天数
            buy_dt = datetime.strptime(p["buy_date"], "%Y-%m-%d")
            curr_dt = datetime.strptime(date_str, "%Y-%m-%d")
            hold_days = (curr_dt - buy_dt).days

            # 交易成本计算
            def cost_of_buy(price, shares):
                return price * shares * (1 + COMMISSION_RATE)

            def proceed_of_sell(price, shares):
                return price * shares * (1 - COMMISSION_RATE - STAMP_TAX)

            buy_cost = cost_of_buy(p["buy_price"], p["shares"])

            # 检查盘中止盈
            high_pct = (p["high"] - p["buy_price"]) / p["buy_price"] * 100
            if high_pct >= TAKE_PROFIT:
                sell_price = p["buy_price"] * (1 + TAKE_PROFIT / 100)
                sell_proceed = proceed_of_sell(sell_price, p["shares"])
                pnl = sell_proceed - buy_cost
                pnl_pct = (sell_proceed / buy_cost - 1) * 100

                self.trade_log.append({
                    "date": date_str, "action": "卖出", "code": p["code"], "name": p["name"],
                    "buy_date": p["buy_date"], "hold_days": hold_days,
                    "buy_price": round(p["buy_price"], 2),
                    "sell_price": round(sell_price, 2),
                    "shares": p["shares"],
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": f"止盈+{TAKE_PROFIT:.0f}%",
                })
                self.capital += sell_proceed
                print(f"    [止盈] {p['name']}({p['code']}) 持仓{hold_days}天 盈利{pnl_pct:+.2f}% +{pnl:.2f}")
                self.positions.remove(p)
                sold.append(p)
                continue

            # 检查盘中止损
            low_pct = (p["low"] - p["buy_price"]) / p["buy_price"] * 100
            if low_pct <= STOP_LOSS:
                sell_price = p["buy_price"] * (1 + STOP_LOSS / 100)
                sell_proceed = proceed_of_sell(sell_price, p["shares"])
                pnl = sell_proceed - buy_cost
                pnl_pct = (sell_proceed / buy_cost - 1) * 100

                self.trade_log.append({
                    "date": date_str, "action": "卖出", "code": p["code"], "name": p["name"],
                    "buy_date": p["buy_date"], "hold_days": hold_days,
                    "buy_price": round(p["buy_price"], 2),
                    "sell_price": round(sell_price, 2),
                    "shares": p["shares"],
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": f"止损{STOP_LOSS:.0f}%",
                })
                self.capital += sell_proceed
                print(f"    [止损] {p['name']}({p['code']}) 持仓{hold_days}天 亏损{pnl_pct:+.2f}% {pnl:.2f}")
                self.positions.remove(p)
                sold.append(p)

        return sold

    def check_expired(self, date_str: str):
        """检查到期卖出（盘中未触发止盈止损的）"""
        sold = []
        for p in self.positions[:]:
            buy_dt = datetime.strptime(p["buy_date"], "%Y-%m-%d")
            curr_dt = datetime.strptime(date_str, "%Y-%m-%d")
            hold_days = (curr_dt - buy_dt).days

            if hold_days >= MAX_HOLD:
                # 用当前价格成交（收盘价）
                sell_price = p.get("current_price", p["buy_price"])
                buy_cost = p["shares"] * p["buy_price"] * (1 + COMMISSION_RATE)
                sell_proceed = sell_price * p["shares"] * (1 - COMMISSION_RATE - STAMP_TAX)
                pnl = sell_proceed - buy_cost
                pnl_pct = (sell_proceed / buy_cost - 1) * 100

                self.trade_log.append({
                    "date": date_str, "action": "卖出", "code": p["code"], "name": p["name"],
                    "buy_date": p["buy_date"], "hold_days": hold_days,
                    "buy_price": round(p["buy_price"], 2),
                    "sell_price": round(sell_price, 2),
                    "shares": p["shares"],
                    "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                    "reason": "到期卖出",
                })
                self.capital += sell_proceed
                print(f"    [到期] {p['name']}({p['code']}) 持仓{hold_days}天 收益{pnl_pct:+.2f}% {pnl:+.2f}")
                self.positions.remove(p)
                sold.append(p)

        return sold

    def buy_new(self, date_str: str, candidates: list, quotes: dict):
        """买入新的候选股"""
        available_slots = MAX_POSITIONS - len(self.positions)
        if available_slots <= 0 or not candidates:
            return

        capital_per = self.capital / (MAX_POSITIONS - len(self.positions) + 1)
        bought = 0

        for c in candidates:
            if bought >= available_slots:
                break

            code = c["code"]
            quote = quotes.get(code)
            if not quote:
                continue

            # 检查是否已持仓
            if any(p["code"] == code for p in self.positions):
                continue

            buy_price = quote["open"] if quote["open"] > 0 else quote["price"]
            shares = int(capital_per / (buy_price * 100)) * 100  # 整手
            if shares < 100:
                continue

            cost = shares * buy_price * (1 + COMMISSION_RATE)
            if cost > self.capital:
                continue

            self.capital -= cost
            self.positions.append({
                "code": code, "name": c["name"], "board": c["board"],
                "buy_date": date_str, "buy_price": buy_price,
                "shares": shares, "current_price": buy_price,
                "high": buy_price, "low": buy_price,
            })

            self.trade_log.append({
                "date": date_str, "action": "买入", "code": code, "name": c["name"],
                "buy_date": date_str, "hold_days": 0,
                "buy_price": round(buy_price, 2), "sell_price": 0,
                "shares": shares, "pnl": 0, "pnl_pct": 0,
                "reason": f"策略A买入",
            })
            bought += 1
            print(f"    [买入] {c['name']}({code}) @ {buy_price:.2f} x{shares}股 = {cost:.2f}")

    def daily_update(self, date_str: str, quotes: dict):
        """每日更新持仓现价"""
        for p in self.positions:
            q = quotes.get(p["code"])
            if q:
                p["current_price"] = q["price"]
                p["high"] = max(p.get("high", 0), q["high"])
                p["low"] = min(p.get("low", 99999), q["low"])

        total = self.value()
        prev_total = self.daily_snapshots[-1]["total_value"] if self.daily_snapshots else INITIAL_CAPITAL
        daily_pct = (total / prev_total - 1) * 100

        self.daily_snapshots.append({
            "date": date_str,
            "capital": round(self.capital, 2),
            "position_value": round(total - self.capital, 2),
            "total_value": round(total, 2),
            "daily_pct": round(daily_pct, 2),
            "total_pct": round((total / INITIAL_CAPITAL - 1) * 100, 2),
            "position_count": len(self.positions),
        })

    def print_status(self, date_str: str):
        total = self.value()
        total_pct = (total / INITIAL_CAPITAL - 1) * 100
        print(f"\n  {'='*50}")
        print(f"  [{date_str}] 收盘持仓")
        print(f"  {'='*50}")
        print(f"  现金: {self.capital:>10.2f}")
        print(f"  持仓: {total - self.capital:>10.2f} ({len(self.positions)} 只)")
        print(f"  总资产: {total:>10.2f}")
        print(f"  总收益: {total_pct:>+7.2f}%")

        if self.positions:
            print(f"\n  {'代码':<10} {'名称':<10} {'买入日':<12} {'买入价':>8} {'现价':>8} {'涨幅%':>7} {'持仓':>4}")
            print(f"  {'-'*55}")
            for p in self.positions:
                pct = (p["current_price"] - p["buy_price"]) / p["buy_price"] * 100
                buy_dt = datetime.strptime(p["buy_date"], "%Y-%m-%d")
                curr_dt = datetime.strptime(date_str, "%Y-%m-%d")
                hd = (curr_dt - buy_dt).days
                print(f"  {p['code']:<10} {p['name']:<10} {p['buy_date']:<12} {p['buy_price']:>8.2f} {p['current_price']:>8.2f} {pct:>+6.2f}% {hd:>3}天")

        print(f"\n  最近交易:")
        for t in self.trade_log[-5:]:
            print(f"    {t['date']} {t['action']} {t['name']}({t['code']}) "
                  f"{'@ '+str(t['buy_price']):>10} {'→ '+str(t['sell_price']):>10} "
                  f"{'pnl:'+str(t.get('pnl_pct','')):>8}" if t['action']=='买入' else
                  f"  {t['date']} {t['action']} {t['name']}({t['code']}) "
                  f"{'@ '+str(t['sell_price']):>10} {t.get('pnl_pct',0):>+7.2f}% {t.get('reason','')}")

    def simulate_week(self, start_date: str, days: int = 10):
        """
        模拟从start_date开始，连续days个交易日

        注意: 这里用历史K线模拟（已发生的数据）
        """
        print(f"\n{'='*60}")
        print(f"  实盘模拟开始")
        print(f"  初始资金: {INITIAL_CAPITAL:,}")
        print(f"  策略: 策略A 基础窗口期")
        print(f"  起止: {start_date} 起 {days}个交易日")
        print(f"{'='*60}")

        # 先获取候选股列表
        print("\n[*] 初始化候选股池...")
        all_candidates = screen_candidates(start_date)

        # 获取全量K线模拟
        print("\n[*] 获取K线数据用于回放...")
        stock_klines = {}
        for c in all_candidates[:MAX_POSITIONS * 2]:
            kl = get_kline(c["code"], days=50)
            if kl and len(kl) >= 10:
                stock_klines[c["code"]] = {"info": c, "kline": kl}
            time.sleep(0.3)

        print(f"[+] {len(stock_klines)} 只有效K线")

        # 收集所有交易日
        all_dates = set()
        for sk in stock_klines.values():
            for k in sk["kline"]:
                all_dates.add(k["date"])
        all_dates = sorted([d for d in all_dates if d >= start_date])

        if not all_dates:
            print("[!] 无交易日数据")
            return

        # 初始化快照
        self.daily_snapshots.append({
            "date": start_date,
            "capital": INITIAL_CAPITAL,
            "position_value": 0,
            "total_value": INITIAL_CAPITAL,
            "daily_pct": 0,
            "total_pct": 0,
            "position_count": 0,
        })

        # 逐日模拟
        for idx, trade_date in enumerate(all_dates[:days]):
            # 检查是否周末
            dt = datetime.strptime(trade_date, "%Y-%m-%d")
            if dt.weekday() >= 5:
                continue

            prev_date_idx = all_dates.index(trade_date)
            if prev_date_idx > 0:
                prev_date = all_dates[prev_date_idx - 1]
            else:
                prev_date = trade_date

            print(f"\n── [{idx+1}/{min(days,len(all_dates))}] {trade_date} {'='*40}")

            # 构建当日行情快照（从K线中取）
            quotes = {}
            for code, sk in stock_klines.items():
                kl = sk["kline"]
                entry = None
                for k in kl:
                    if k["date"] == trade_date:
                        entry = k
                        break
                if entry:
                    quotes[code] = {
                        "price": entry["close"],
                        "open": entry["open"],
                        "high": entry["high"],
                        "low": entry["low"],
                        "pct": entry["pct"],
                    }

            # 1. 检查止盈止损（盘中）
            self.check_stop_loss_take_profit(trade_date, quotes)

            # 2. 更新现价
            self.daily_update(trade_date, quotes)

            # 3. 检查到期
            self.check_expired(trade_date)

            # 4. 重建quotes（卖出后续可能要买入）
            for code, sk in stock_klines.items():
                kl = sk["kline"]
                entry = None
                for k in kl:
                    if k["date"] == trade_date:
                        entry = k
                        break
                if entry:
                    quotes[code] = {
                        "price": entry["close"],
                        "open": entry["open"],
                        "high": entry["high"],
                        "low": entry["low"],
                        "pct": entry["pct"],
                    }

            # 5. 买入新候选股
            # 用前一天的涨幅判断买入信号
            prev_klines = {}
            for code, sk in stock_klines.items():
                kl = sk["kline"]
                for i, k in enumerate(kl):
                    if k["date"] == trade_date and i > 0:
                        prev_klines[code] = kl[i - 1]
                        break
                if code not in prev_klines and len(kl) > 1:
                    prev_klines[code] = kl[-2]

            buy_candidates = []
            for code, sk in stock_klines.items():
                prev = prev_klines.get(code)
                if prev and 2.0 <= prev.get("pct", 0) <= 9.0:
                    buy_candidates.append(sk["info"])

            if buy_candidates:
                # 排除已持仓的
                held_codes = {p["code"] for p in self.positions}
                buy_candidates = [c for c in buy_candidates if c["code"] not in held_codes]
                buy_candidates.sort(key=lambda x: x["inc"], reverse=True)
                self.buy_new(trade_date, buy_candidates[:3], quotes)

            # 6. 更新快照
            self.daily_update(trade_date, quotes)

            # 7. 打印状态
            self.print_status(trade_date)

        # 最终清算
        self.final_settle(all_dates[-1] if all_dates else start_date)

    def final_settle(self, date_str: str):
        print(f"\n{'='*60}")
        print(f"  最终清算 - {date_str}")
        print(f"{'='*60}")

        total = self.value()
        total_pct = (total / INITIAL_CAPITAL - 1) * 100
        total_trades = len(self.trade_log)
        buy_trades = [t for t in self.trade_log if t["action"] == "买入"]
        sell_trades = [t for t in self.trade_log if t["action"] == "卖出"]
        win_trades = [t for t in sell_trades if t.get("pnl_pct", 0) > 0]
        lose_trades = [t for t in sell_trades if t.get("pnl_pct", 0) <= 0]

        print(f"\n  初始资金: {INITIAL_CAPITAL:>10,}")
        print(f"  最终资产: {total:>10.2f}")
        print(f"  总收益: {total_pct:>+7.2f}%  ({total - INITIAL_CAPITAL:+.2f})")
        print(f"\n  交易统计:")
        print(f"    总交易: {total_trades} 笔 (买入{len(buy_trades)}/卖出{len(sell_trades)})")
        print(f"    盈利: {len(win_trades)} 笔")
        print(f"    亏损: {len(lose_trades)} 笔")
        print(f"    胜率: {len(win_trades)/max(len(sell_trades),1)*100:.1f}%")

        if sell_trades:
            avg_win = sum(t["pnl_pct"] for t in win_trades) / max(len(win_trades), 1)
            avg_lose = sum(t["pnl_pct"] for t in lose_trades) / max(len(lose_trades), 1)
            print(f"    平均盈利: {avg_win:+.2f}%")
            print(f"    平均亏损: {avg_lose:+.2f}%")

        # 输出每日净值曲线
        print(f"\n  每日净值:")
        print(f"  {'日期':<12} {'总资产':>10} {'日收益':>8} {'累计%':>8} {'持仓':>4}")
        print(f"  {'-'*42}")
        for s in self.daily_snapshots:
            print(f"  {s['date']:<12} {s['total_value']:>10.2f} {s['daily_pct']:>+7.2f}% {s['total_pct']:>+7.2f}% {s['position_count']:>4}")

        # 保存结果
        result = {
            "initial_capital": INITIAL_CAPITAL,
            "final_value": round(total, 2),
            "total_pnl": round(total_pct, 2),
            "total_pnl_amount": round(total - INITIAL_CAPITAL, 2),
            "total_trades": total_trades,
            "buy_trades": len(buy_trades),
            "sell_trades": len(sell_trades),
            "win_trades": len(win_trades),
            "lose_trades": len(lose_trades),
            "win_rate": round(len(win_trades) / max(len(sell_trades), 1) * 100, 1),
            "trade_log": self.trade_log,
            "daily_snapshots": self.daily_snapshots,
        }
        result_path = os.path.join(OUTPUT_DIR, f"simulation_result.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
        print(f"\n[+] 结果已保存: {result_path}")


# ============================================================
# 主流程
# ============================================================
def main():
    print("""
  ╔══════════════════════════════════════════╗
  ║    实盘模拟交易系统                      ║
  ║    10万本金 | 策略A基础窗口期           ║
  ╚══════════════════════════════════════════╝
    """)

    # 确定回测范围 - 用最近两周的交易日数据
    # 用历史K线模拟：从最近一个周一开始，10个交易日
    today = datetime.now()
    # 找最近的周一
    last_monday = today - timedelta(days=today.weekday())
    if today.weekday() < 5:
        # 如果是交易日，再往前推两周
        last_monday = last_monday - timedelta(days=14)

    start = last_monday.strftime("%Y-%m-%d")
    print(f"[*] 模拟起始日: {start}")
    print(f"[*] 模拟周期: 10个交易日（约2周）\n")

    system = TradingSystem()
    system.simulate_week(start, days=10)


if __name__ == "__main__":
    main()
