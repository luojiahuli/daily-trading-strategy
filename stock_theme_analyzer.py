#!/usr/bin/env python3
"""
股票题材分析系统 v2.0 - 追踪各大题材板块热度变化，推荐强势题材股

数据源:
  - akshare/同花顺 (概念板块列表、历史K线数据)
  - 聚合数据API (个股实时行情)

使用方式:
  # 显示所有板块热度排名
  python3 stock_theme_analyzer.py

  # 查看指定板块详情
  python3 stock_theme_analyzer.py --board "AI PC"

  # 查看板块排名+前N热门板块的个股推荐
  python3 stock_theme_analyzer.py --top 20 --recommend-top 5

  # 指定分析天数
  python3 stock_theme_analyzer.py --days 365
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

# ============================================================
# 配置
# ============================================================

JUHE_API_KEY=os.environ.get("JUHE_API_KEY", "")
JUHE_STOCK_URL = "https://web.juhe.cn/finance/stock/hs"

# 数据缓存目录
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
BOARD_LIST_CACHE = os.path.join(CACHE_DIR, "board_list.json")
BOARD_CONS_CACHE_DIR = os.path.join(CACHE_DIR, "board_cons")

# ============================================================
# 辅助函数
# ============================================================

FALLBACK_AVAILABLE = False
try:
    from stock_data_fallback import (
        get_kline_fallback, get_realtime_fallback, get_batch_realtime_fallback,
        sina_kline, tencent_realtime
    )
    FALLBACK_AVAILABLE = True
except ImportError:
    pass


def ensure_akshare():
    """确保akshare可用"""
    try:
        import akshare as ak
        return ak
    except ImportError:
        print("[*] 安装 akshare (数据源库)...")
        os.system(f"{sys.executable} -m pip install akshare -q")
        import akshare as ak
        return ak


def get_board_data_fallback():
    """当akshare不可用时的板块数据降级方案"""
    if FALLBACK_AVAILABLE:
        boards = get_sina_board_list()
        if boards:
            return [{"name": b["name"], "code": b["code"]} for b in boards]
    return []


def get_kline_fallback_wrapper(symbol: str, datalen: int = 60) -> Optional[list]:
    """带akshare和降级的K线数据获取"""
    if FALLBACK_AVAILABLE:
        return get_kline_fallback(symbol, datalen=datalen)
    return None


def ensure_cache_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(BOARD_CONS_CACHE_DIR, exist_ok=True)


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ============================================================
# 板块数据模块
# ============================================================

def get_all_concept_boards(use_cache=True) -> list[dict]:
    """
    获取所有同花顺概念板块列表

    返回: [{"name": "AI PC", "code": "309121"}, ...]
    """
    ensure_cache_dirs()
    if use_cache and os.path.exists(BOARD_LIST_CACHE):
        cache_age = time.time() - os.path.getmtime(BOARD_LIST_CACHE)
        if cache_age < 86400:
            with open(BOARD_LIST_CACHE, "r") as f:
                return json.load(f)

    ak = ensure_akshare()
    print("[*] 正在获取概念板块列表 (同花顺)...")
    try:
        df = ak.stock_board_concept_name_ths()
        boards = df.to_dict("records")
        with open(BOARD_LIST_CACHE, "w") as f:
            json.dump(boards, f, ensure_ascii=False)
        print(f"[+] 获取到 {len(boards)} 个概念板块")
        return boards
    except Exception as e:
        print(f"[!] akshare获取板块列表失败: {e}")
        # 降级到新浪备用数据源
        print("[*] 降级到新浪备用数据源...")
        fallback = get_board_data_fallback()
        if fallback:
            with open(BOARD_LIST_CACHE, "w") as f:
                json.dump(fallback, f, ensure_ascii=False)
            print(f"[+] 备用数据源获取到 {len(fallback)} 个概念板块")
            return fallback
        if os.path.exists(BOARD_LIST_CACHE):
            with open(BOARD_LIST_CACHE, "r") as f:
                return json.load(f)
        return []


def get_board_history(symbol: str) -> Optional[list]:
    """
    获取同花顺概念板块历史K线数据 (所有可用数据)

    返回: [{"日期": ..., "开盘价": ..., "收盘价": ..., ...}, ...]
    """
    ak = ensure_akshare()
    try:
        df = ak.stock_board_concept_index_ths(symbol=symbol)
        if df is None or df.empty:
            return None
        df = df.sort_values("日期")
        df["日期"] = df["日期"].astype(str)
        return df.to_dict("records")
    except Exception as e:
        print(f"[!] akshare获取板块K线失败: {e}")
        # 对于板块K线，暂时无法降级（备用数据源主要是个股）
        # 返回None让上层使用缓存
        return None


def get_board_info_ths(symbol: str) -> Optional[dict]:
    """
    获取同花顺概念板块的概览信息

    返回: {"今开": ..., "昨收": ..., "板块涨幅": ..., "涨跌家数": ..., "资金净流入": ..., ...}
    """
    ak = ensure_akshare()
    try:
        df = ak.stock_board_concept_info_ths(symbol=symbol)
        if df is None or df.empty:
            return None
        result = {}
        for _, row in df.iterrows():
            result[row["项目"]] = row["值"]
        return result
    except Exception:
        return None


def get_board_cons_from_ths_page(board_name: str) -> Optional[list]:
    """
    从同花顺概念板块页面爬取成分股列表

    方法: 直接请求同花顺的API接口获取成分股数据
    """
    try:
        # 先找到板块编码
        # 同花顺概念板块编码通常是 BK开头+数字
        ak = ensure_akshare()
        boards = get_all_concept_boards(use_cache=True)
        board_code = None
        for b in boards:
            if b["name"] == board_name:
                board_code = b["code"]
                break
        if not board_code:
            return None

        # 同花顺板块成分股API
        url = f"https://data.10jqka.com.cn/financial/ggcx/op/czq/"
        params = {
            "callback": "jQuery",
            "stockcode": board_code,
            "type": "concept",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://data.10jqka.com.cn/",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            stocks = data.get("data", {}).get("list", [])
            result = []
            for s in stocks:
                result.append({
                    "代码": s.get("code", ""),
                    "名称": s.get("name", ""),
                    "涨幅": s.get("zdf", ""),
                    "现价": s.get("price", ""),
                })
            return result
    except Exception:
        pass
    return None


# ============================================================
# 个股数据模块 (聚合数据API)
# ============================================================

def get_stock_quote(code: str) -> Optional[dict]:
    """通过聚合数据API获取个股行情"""
    code = str(code).strip()
    if code.startswith(("6", "9")):
        gid = f"sh{code}"
    elif code.startswith(("0", "3")):
        gid = f"sz{code}"
    elif code.startswith(("4", "8")):
        gid = f"bj{code}"
    else:
        gid = f"sh{code}"

    params = {"key": JUHE_API_KEY, "gid": gid}
    try:
        resp = requests.get(JUHE_STOCK_URL, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("error_code") == 0 and data.get("result"):
                items = data["result"]
                if isinstance(items, list) and len(items) > 0:
                    item = items[0]
                    if isinstance(item, dict) and "data" in item:
                        return item["data"]
    except Exception:
        pass
    return None


# ============================================================
# 分析引擎
# ============================================================

def calc_board_analysis(history: list) -> dict:
    """
    计算板块的综合分析数据

    评分维度:
      - 短期涨幅 (近5日 vs 5日前): 权重 0.30
      - 中期涨幅 (近20日 vs 20日前): 权重 0.25
      - 长期涨幅 (近60日 vs 60日前): 权重 0.15
      - 成交量变化 (近5日均量 vs 前20日均量): 权重 0.20
      - 趋势强度 (近20日斜率): 权重 0.10

    返回: dict 包含各项指标和综合评分
    """
    if not history or len(history) < 5:
        return {"score": 0, "indicators": {"error": "数据不足"}}

    recent = history[-min(len(history), 500):]
    closes = [r.get("收盘价", 0) for r in recent]
    volumes = [r.get("成交量", 0) for r in recent]

    if not closes or closes[-1] == 0:
        return {"score": 0, "indicators": {"error": "数据异常"}}

    latest_close = closes[-1]

    def pct(days):
        if len(closes) <= days:
            return 0.0
        old = closes[-(days + 1)]
        return (latest_close - old) / old * 100 if old else 0.0

    # 各周期涨跌幅
    pct_5d = pct(5)
    pct_10d = pct(10)
    pct_20d = pct(20)
    pct_60d = pct(60)
    pct_120d = pct(120)
    pct_250d = pct(250)

    # 成交量分析
    vol_ratio = 1.0
    if len(volumes) > 25:
        avg_vol_prev20 = sum(volumes[-25:-5]) / 20
        avg_vol_recent5 = sum(volumes[-5:]) / 5
        if avg_vol_prev20 > 0:
            vol_ratio = avg_vol_recent5 / avg_vol_prev20

    # 趋势斜率 (近20日)
    trend_slope = 0
    if len(closes) >= 20:
        y = closes[-20:]
        x = list(range(20))
        n = 20
        x_m = sum(x) / n
        y_m = sum(y) / n
        num = sum((xi - x_m) * (yi - y_m) for xi, yi in zip(x, y))
        den = sum((xi - x_m) ** 2 for xi in x)
        if den > 0:
            trend_slope = num / den
        # 归一化到百分比
        if y_m > 0:
            trend_slope = trend_slope / y_m * 100

    # 综合评分 (0-100)
    score_short = max(0, min(40, pct_5d * 3 + 20))     # 短期最高40分
    score_mid = max(0, min(25, pct_20d * 1.2 + 10))     # 中期最高25分
    score_long = max(0, min(15, pct_60d * 0.3 + 5))     # 长期最高15分
    score_vol = max(0, min(30, (vol_ratio - 0.5) * 30))  # 量比最高30分
    score_trend = max(-10, min(10, trend_slope * 2))     # 趋势最高10分

    # 扣分项: 如果近期涨幅过大(超30%)可能已到高位
    overheat_penalty = max(0, (pct_5d - 25) * 1) if pct_5d > 25 else 0

    total = score_short + score_mid + score_long + score_vol + max(0, score_trend) - overheat_penalty
    total = max(0, min(100, total))

    # 近60日高点检测
    near_high = False
    max_60d_pct = 0
    if len(closes) > 60:
        max_60d = max(closes[-60:])
        max_60d_pct = (latest_close / max_60d - 1) * 100
        near_high = (latest_close / max_60d) >= 0.9

    return {
        "score": round(total, 1),
        "indicators": {
            "最新价": latest_close,
            "pct_5d": round(pct_5d, 2),
            "pct_10d": round(pct_10d, 2),
            "pct_20d": round(pct_20d, 2),
            "pct_60d": round(pct_60d, 2),
            "pct_120d": round(pct_120d, 2),
            "pct_250d": round(pct_250d, 2),
            "量比": round(vol_ratio, 2),
            "趋势斜率": round(trend_slope, 4),
            "近60日高点差%": round(max_60d_pct, 2),
            "近高点": near_high,
        }
    }


# ============================================================
# 输出模块
# ============================================================

def print_ranking(boards_with_analysis: list, top_n: int = 20):
    """打印板块热度排名"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'=' * 110}")
    print(f"  题材板块热度排名 (更新: {now})")
    print(f"  {'排名':>4} {'板块名称':<22} {'热度':>6} {'5日涨跌':>10} {'20日涨跌':>10} {'60日涨跌':>10} {'量比':>8} {'趋势':>8} {'近高点'} ")
    print(f"{'=' * 110}")

    for i, b in enumerate(boards_with_analysis[:top_n]):
        ind = b["analysis"]["indicators"]
        score = b["analysis"]["score"]
        trend_str = f"{ind.get('趋势斜率', 0):+.2f}"
        near = "Y" if ind.get("近高点") else "N"
        print(
            f"  {i+1:>4} {b['name']:<22} {score:>5.1f} "
            f"{ind.get('pct_5d', 0):>+9.2f}% "
            f"{ind.get('pct_20d', 0):>+9.2f}% "
            f"{ind.get('pct_60d', 0):>+9.2f}% "
            f"{ind.get('量比', 1):>7.2f} "
            f"{trend_str:>8} "
            f"{near:>6}"
        )

    print(f"{'=' * 110}")
    print(f"  注: 热度评分综合考量短期涨幅、中期趋势、成交量变化。分数越高表示关注价值越大。")
    print(f"      近高点=Y 表示距60日高点10%以内，说明板块处于阶段性高位。")
    print()


def print_board_detail(board_name: str, analysis: dict, cons: list = None):
    """打印板块详情"""
    ind = analysis["indicators"]
    score = analysis["score"]

    print(f"\n{'=' * 80}")
    print(f"  [{board_name}] 板块分析详情")
    print(f"{'=' * 80}")
    print(f"  综合热度评分: {score:.1f}/100")
    print(f"  {'指标':<20} {'数值'}")
    print(f"  {'-' * 40}")
    print(f"  {'最新价':<20} {ind.get('最新价', 'N/A')}")
    print(f"  {'近5日涨跌':<20} {ind.get('pct_5d', 0):>+8.2f}%")
    print(f"  {'近10日涨跌':<20} {ind.get('pct_10d', 0):>+8.2f}%")
    print(f"  {'近20日涨跌':<20} {ind.get('pct_20d', 0):>+8.2f}%")
    print(f"  {'近60日涨跌':<20} {ind.get('pct_60d', 0):>+8.2f}%")
    print(f"  {'近120日涨跌':<20} {ind.get('pct_120d', 0):>+8.2f}%")
    print(f"  {'近250日涨跌':<20} {ind.get('pct_250d', 0):>+8.2f}%")
    print(f"  {'成交量比(近5/前20)':<20} {ind.get('量比', 1):>8.2f}")
    print(f"  {'趋势斜率':<20} {ind.get('趋势斜率', 0):>+8.4f}")
    print(f"  {'距60日高点':<20} {ind.get('近60日高点差%', 0):>+8.2f}%")

    if cons:
        print(f"\n  [-] 板块龙头/强势股 (近5日涨幅排序):")
        print(f"  {'代码':<10} {'名称':<12} {'现价':>10} {'涨幅%':>8} {'涨跌额':>10}")
        print(f"  {'-' * 50}")
        for s in cons[:10]:
            inc = safe_float(s.get("涨幅", 0))
            price = safe_float(s.get("现价", 0))
            change = price * inc / 100
            print(f"  {s['代码']:<10} {s['名称']:<12} {price:>10.2f} {inc:>+7.2f}% {change:>+9.2f}")
    else:
        print(f"\n  [i] 使用 --stock 选项查看本板块个股推荐")

    print(f"{'=' * 80}")


# ============================================================
# 缓存管理
# ============================================================

def save_analysis_cache(name: str, data: dict):
    cache_file = os.path.join(CACHE_DIR, f"analysis_{name.replace('/', '_')}.json")
    with open(cache_file, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def load_analysis_cache(name: str) -> Optional[dict]:
    cache_file = os.path.join(CACHE_DIR, f"analysis_{name.replace('/', '_')}.json")
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return json.load(f)
    return None


# ============================================================
# 同花顺成分股数据爬取 (直接API)
# ============================================================

def get_board_constituents(board_name: str) -> Optional[list]:
    """
    通过同花顺板块详情页获取成分股数据

    返回: [{"代码": ..., "名称": ..., "现价": ..., "涨幅": ..., "换手": ..., ...}, ...]
    """
    ensure_cache_dirs()
    cache_file = os.path.join(BOARD_CONS_CACHE_DIR, f"{board_name.replace('/', '_')}.json")

    # 检查缓存 (5分钟内有效)
    if os.path.exists(cache_file):
        cache_age = time.time() - os.path.getmtime(cache_file)
        if cache_age < 300:
            with open(cache_file, "r") as f:
                return json.load(f)

    try:
        # 获取板块编码
        boards = get_all_concept_boards(use_cache=True)
        board_code = None
        for b in boards:
            if b["name"] == board_name:
                board_code = b["code"]
                break
        if not board_code:
            print(f"  [!] 未找到板块编码: {board_name}")
            return None

        # 从同花顺详情页爬取成分股
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://q.10jqka.com.cn/",
        }
        url = f"https://q.10jqka.com.cn/gn/detail/code/{board_code}/"
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return None

        rows = table.find_all("tr")
        stock_list = []
        for row in rows[1:]:  # 跳过表头
            cells = row.find_all("td")
            if len(cells) >= 5:
                stock_list.append({
                    "代码": cells[1].get_text(strip=True),
                    "名称": cells[2].get_text(strip=True),
                    "现价": cells[3].get_text(strip=True),
                    "涨幅": cells[4].get_text(strip=True),
                    "涨跌": cells[5].get_text(strip=True) if len(cells) > 5 else "0",
                    "换手": cells[7].get_text(strip=True) if len(cells) > 7 else "0",
                    "量比": cells[8].get_text(strip=True) if len(cells) > 8 else "0",
                })

        if stock_list:
            with open(cache_file, "w") as f:
                json.dump(stock_list, f, ensure_ascii=False)
            return stock_list

    except Exception as e:
        print(f"  [!] 获取成分股失败: {e}")

    return None


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="股票题材热度分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 stock_theme_analyzer.py              # 显示所有板块热度排名
  python3 stock_theme_analyzer.py --top 30     # 显示前30名
  python3 stock_theme_analyzer.py --board "AI PC"  # 查看AI PC板块详情
  python3 stock_theme_analyzer.py --board "AI PC" --stock  # 加个股推荐
  python3 stock_theme_analyzer.py --recommend-top 5     # 排名后自动推荐前5板块个股
  python3 stock_theme_analyzer.py --days 365  --top 20  # 基于1年数据分析
  python3 stock_theme_analyzer.py --list       # 列出所有板块名称
        """
    )
    parser.add_argument("--days", type=int, default=60,
                        help="分析天数基准 (默认: 60)")
    parser.add_argument("--quick", action="store_true",
                        help="快速模式: 只分析前50个热门板块(节省时间)")
    parser.add_argument("--top", type=int, default=20,
                        help="显示排名前N的板块 (默认: 20)")
    parser.add_argument("--board", type=str, default=None,
                        help="查看指定板块详情")
    parser.add_argument("--stock", action="store_true",
                        help="查看板块详情时同时推荐个股")
    parser.add_argument("--recommend-top", type=int, default=0,
                        help="排名后自动推荐前N个热门板块的个股")
    parser.add_argument("--no-cache", action="store_true",
                        help="不缓存板块列表")
    parser.add_argument("--list", action="store_true",
                        help="列出所有概念板块名称")
    parser.add_argument("--export", type=str, default=None,
                        help="导出排名到CSV文件")
    args = parser.parse_args()

    print("""
  ╔══════════════════════════════════════════╗
  ║    股票题材热度分析系统 v2.0             ║
  ║    数据源: 同花顺 + 聚合数据             ║
  ╚══════════════════════════════════════════╝
    """)

    boards = get_all_concept_boards(use_cache=not args.no_cache)
    if not boards:
        print("[!] 无法获取板块数据，请检查网络")
        return

    print(f"[*] 共 {len(boards)} 个概念板块")

    # --list 模式: 只列出板块名
    if args.list:
        print(f"\n所有概念板块 ({len(boards)} 个):\n")
        for i, b in enumerate(boards, 1):
            print(f"  {i:>4}. {b['name']}")
        return

    # --board 模式: 分析单个板块
    if args.board:
        target = None
        for b in boards:
            if args.board.lower() in b["name"].lower():
                target = b
                break
        if not target:
            print(f"[!] 未找到包含 '{args.board}' 的板块")
            return

        print(f"\n[*] 获取 [{target['name']}] 板块数据...")
        history = get_board_history(target["name"])
        if not history:
            print(f"[!] 无法获取板块历史数据")
            return

        print(f"[+] 获取到 {len(history)} 个交易日数据")

        analysis = calc_board_analysis(history)

        cons = None
        if args.stock:
            print(f"[*] 获取板块成分股...")
            cons = get_board_constituents(target["name"])
            if cons:
                print(f"[+] 获取到 {len(cons)} 只成分股")
            else:
                print(f"[!] 无法获取成分股数据")

        print_board_detail(target["name"], analysis, cons)

        # 推荐个股时也打印聚合数据行情
        if args.stock and cons:
            print(f"\n[*] 使用聚合数据API查询前5只个股详情...")
            for i, s in enumerate(cons[:5]):
                quote = get_stock_quote(s["代码"])
                if quote:
                    print(f"  {s['代码']} {s['名称']}: "
                          f"最新 {quote.get('nowPri','?')} "
                          f"涨跌 {quote.get('increPer','?')}% "
                          f"今开 {quote.get('todayStartPri','?')} "
                          f"最高 {quote.get('todayMax','?')} "
                          f"最低 {quote.get('todayMin','?')}")
                time.sleep(0.3)
        return

    # 默认模式: 全板块排名分析
    print(f"[*] 正在获取各板块历史数据进行热度分析...")
    if args.quick:
        # 快速模式: 只分析50个主流板块
        # 按同花顺默认排序取前50（通常是成交活跃的板块）
        quick_boards = boards[:50]
        print(f"    (快速模式: 分析前50个热门板块, 周期: {args.days}天)\\n")
    else:
        quick_boards = boards
        print(f"    (分析周期: {args.days}天, 共 {len(boards)} 个板块)\\n")

    all_results = []
    total = len(quick_boards)
    errors = 0
    skipped = 0

    for idx, board in enumerate(quick_boards):
        name = board["name"]
        # 进度指示
        if (idx + 1) % 10 == 0 or idx == 0:
            print(f"  [{idx+1}/{total}] 处理中... ({len(all_results)} 完成)", end="\r", flush=True)

        history = get_board_history(name)
        if history and len(history) >= 5:
            analysis = calc_board_analysis(history)
            all_results.append({
                "name": name,
                "code": board["code"],
                "analysis": analysis,
            })
        else:
            skipped += 1

        # 控制请求频率
        time.sleep(0.3)

    print(f"\n[+] 分析完成! 成功: {len(all_results)}, 跳过(数据不足): {skipped}")

    # 排序
    all_results.sort(key=lambda x: x["analysis"]["score"], reverse=True)

    # 打印排名
    print_ranking(all_results, top_n=args.top)

    # 自动推荐前N热门板块的个股
    if args.recommend_top > 0:
        top_n_recommend = min(args.recommend_top, len(all_results))
        print(f"[*] 为前 {top_n_recommend} 个热门板块推荐个股...")

        for rank, board in enumerate(all_results[:top_n_recommend], 1):
            print(f"\n  [{rank}/{top_n_recommend}] {board['name']} (热度:{board['analysis']['score']:.1f})")
            cons = get_board_constituents(board["name"])
            if cons:
                # 按涨幅排序
                cons_sorted = sorted(cons, key=lambda x: safe_float(x.get("涨幅", 0)), reverse=True)
                print(f"  {'代码':<10} {'名称':<12} {'现价':>10} {'涨幅%':>8}")
                print(f"  {'-' * 40}")
                for s in cons_sorted[:5]:
                    print(f"  {s['代码']:<10} {s['名称']:<12} {safe_float(s.get('现价', 0)):>10.2f} {safe_float(s.get('涨幅', 0)):>+7.2f}%")
            else:
                print(f"  [!] 无法获取成分股")
            time.sleep(0.5)

    # 导出CSV
    if args.export:
        import csv
        with open(args.export, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["排名", "板块名称", "热度评分", "5日涨跌%", "20日涨跌%",
                           "60日涨跌%", "量比", "趋势斜率", "近60日高点差%", "近高点"])
            for i, b in enumerate(all_results[:args.top], 1):
                ind = b["analysis"]["indicators"]
                writer.writerow([
                    i, b["name"], b["analysis"]["score"],
                    ind.get("pct_5d", 0), ind.get("pct_20d", 0),
                    ind.get("pct_60d", 0), ind.get("量比", 1),
                    ind.get("趋势斜率", 0), ind.get("近60日高点差%", 0),
                    "是" if ind.get("近高点") else "否"
                ])
        print(f"\n[+] 已导出到: {args.export}")

    print(f"\n[*] 分析完成！使用 --board 板块名 查看详情")


if __name__ == "__main__":
    main()
