#!/usr/bin/env python3
"""
备用数据源模块 — A股备用数据源

当 akshare 不可用时（如非交易时段连接断开、国外被墙等），
自动降级到以下备用数据源，保证策略能正常运行。

备用数据源：
1. Sina Finance API — 个股日K线、实时行情
2. 腾讯证券 API — 个股实时行情、板块数据
3. 网易财经 API — 个股日K线
4. 新浪板块页面爬取 — 板块数据
"""

import os, sys, json, re, time
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import requests

# ── Sina Finance API ──────────────────────────────────────────────────

def sina_kline(symbol: str, scale: int = 240, datalen: int = 60) -> Optional[list]:
    """
    通过新浪财经API获取个股日K线数据

    Args:
        symbol: 股票代码, 如 '600536' (不需要前缀)
        scale: 240=日线, 60=60分钟线
        datalen: 返回数据条数

    Returns:
        [{"day": "2026-05-13", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}, ...]
        失败返回 None
    """
    code = symbol.strip()
    # 新浪格式: sh600536 / sz000001
    if code.startswith(("6", "9")):
        prefix = "sh"
    elif code.startswith(("0", "3")):
        prefix = "sz"
    elif code.startswith(("4", "8")):
        prefix = "bj"
    else:
        prefix = "sh"

    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_%20/CN_MarketData.getKLineData"
    params = {"symbol": f"{prefix}{code}", "scale": str(scale), "ma": "no", "datalen": str(datalen)}

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        # 提取 JSON: 响应格式 var _ = [{...}, {...}];
        match = re.search(r'\[.*?\]', resp.text, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        result = []
        for item in data:
            result.append({
                "day": item.get("date", ""),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0)),
            })
        return result
    except Exception as e:
        return None


def sina_realtime(symbol: str) -> Optional[dict]:
    """
    通过新浪财经API获取个股实时行情

    Args:
        symbol: 股票代码, 如 '600536'

    Returns:
        {"name": ..., "open": ..., "close": ..., "high": ..., "low": ..., "volume": ..., "amount": ...}
    """
    code = symbol.strip()
    if code.startswith(("6", "9")):
        prefix = "sh"
    elif code.startswith(("0", "3")):
        prefix = "sz"
    else:
        prefix = "sh"

    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        # 格式: var hq_str_sh600536="中国软件,54.20,54.10,54.50,55.20,53.80,..."
        match = re.search(r'"(.*?)"', resp.text)
        if not match:
            return None
        parts = match.group(1).split(",")
        if len(parts) < 32:
            return None
        return {
            "name": parts[0],
            "open": float(parts[1]) if parts[1] else 0,
            "close": float(parts[2]) if parts[2] else 0,  # 昨收
            "price": float(parts[3]) if parts[3] else 0,  # 当前价
            "high": float(parts[4]) if parts[4] else 0,
            "low": float(parts[5]) if parts[5] else 0,
            "volume": float(parts[8]) if parts[8] else 0,  # 成交量(手)
            "amount": float(parts[9]) if parts[9] else 0,  # 成交额
            "bid": float(parts[10]) if parts[10] else 0,   # 买一
            "ask": float(parts[12]) if parts[12] else 0,   # 卖一
            "date": parts[30] if len(parts) > 30 else "",
            "time": parts[31] if len(parts) > 31 else "",
        }
    except Exception:
        return None


# ── 腾讯证券 API ──────────────────────────────────────────────────────

def tencent_realtime(symbols: list) -> Optional[list]:
    """
    通过腾讯证券API获取批量个股实时行情

    Args:
        symbols: 股票代码列表, 如 ['sh600536', 'sz000001']

    Returns:
        [{"code": ..., "name": ..., "price": ..., ...}, ...]
    """
    if not symbols:
        return None

    # 腾讯格式: 多个股票用逗号分隔
    codes_str = ",".join(
        f"{'sh' if s.startswith(('6','9')) else 'sz'}{s}" if len(s) <= 6 else s
        for s in symbols
    )

    url = f"https://qt.gtimg.cn/q={codes_str}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None

        result = []
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            match = re.search(r'"(.*?)"', line)
            if not match:
                continue
            parts = match.group(1).split("~")
            if len(parts) < 40:
                continue
            result.append({
                "code": parts[2],        # 市场+代码: sh600536
                "name": parts[1],
                "price": float(parts[3]) if parts[3] else 0,
                "close": float(parts[4]) if parts[4] else 0,  # 昨收
                "open": float(parts[5]) if parts[5] else 0,
                "volume": float(parts[6]) if parts[6] else 0,  # 成交量(手)
                "amount": float(parts[37]) if len(parts) > 37 and parts[37] else 0,  # 成交额(万)
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0,  # 涨跌幅%
            })
        return result
    except Exception:
        return None


def tencent_kline(symbol: str, days: int = 60) -> Optional[list]:
    """
    通过腾讯证券API获取个股日K线

    Args:
        symbol: 股票代码, 如 '600536'
        days: 获取天数

    Returns:
        [{"day": "2026-05-13", "open": ..., "close": ..., ...}, ...]
    """
    code = symbol.strip()
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    full_code = f"{prefix}{code}"

    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{full_code},day,,,{days}", "q": "1"}

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 0:
            return None
        # 数据结构: data[full_code]["day"][0] = ["2026-05-13", open, close, high, low, volume]
        klines = data.get("data", {}).get(full_code, {}).get("day", [])
        result = []
        for k in klines:
            if len(k) >= 6:
                result.append({
                    "day": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "volume": float(k[5]),
                })
        return result
    except Exception:
        return None


# ── 网易财经 API ──────────────────────────────────────────────────────

def netease_kline(symbol: str) -> Optional[list]:
    """
    通过网易财经API获取个股日K线数据

    Args:
        symbol: 股票代码, 如 '600536'

    Returns:
        [{"day": ..., "open": ..., "close": ..., ...}, ...]
    """
    code = symbol.strip()
    # 网易格式: 0=深圳, 1=上海
    exchange = "1" if code.startswith(("6", "9")) else "0"

    url = f"http://quotes.money.163.com/service/chddata.html"
    params = {"code": f"{exchange}{code}", "start": "20260101", "end": datetime.now().strftime("%Y%m%d")}

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return None
        result = []
        for line in lines[1:]:  # 跳过表头
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                result.append({
                    "day": parts[0],
                    "open": float(parts[2]) if parts[2] else 0,
                    "high": float(parts[3]) if parts[3] else 0,
                    "low": float(parts[4]) if parts[4] else 0,
                    "close": float(parts[5]) if parts[5] else 0,
                    "volume": float(parts[6]) if len(parts) > 6 and parts[6] else 0,
                })
            except (ValueError, IndexError):
                continue
        return result[::-1] if result else None  # 按日期升序
    except Exception:
        return None


# ── 综合数据获取（带自动降级） ──────────────────────────────────────

DATA_SOURCES = {
    "sina": {"kline": sina_kline, "realtime": sina_realtime, "batch": None},
    "tencent": {"kline": tencent_kline, "realtime": tencent_realtime, "batch": tencent_realtime},
    "netease": {"kline": netease_kline, "realtime": None, "batch": None},
}

def get_kline_fallback(symbol: str, datalen: int = 60) -> Optional[list]:
    """
    获取个股日K线，带自动降级

    优先级: sina -> tencent -> netease
    全部失败返回 None
    """
    # 1. Sina
    data = sina_kline(symbol, datalen=datalen)
    if data:
        return data
    # 2. Tencent
    data = tencent_kline(symbol, days=datalen)
    if data:
        return data
    # 3. NetEase
    data = netease_kline(symbol)
    if data:
        return data[-datalen:] if len(data) > datalen else data
    return None


def get_realtime_fallback(symbol: str) -> Optional[dict]:
    """
    获取个股实时行情，带自动降级

    优先级: sina -> tencent
    """
    data = sina_realtime(symbol)
    if data:
        return data
    batch = tencent_realtime([symbol])
    if batch and len(batch) > 0:
        return batch[0]
    return None


def get_batch_realtime_fallback(symbols: list) -> Optional[list]:
    """
    批量获取个股实时行情

    腾讯支持批量查询，Sina只能单个查
    推荐使用腾讯批量
    """
    return tencent_realtime(symbols)


# ── 板块数据（同花顺页面爬取） ──────────────────────────────────────

def get_sina_board_list() -> Optional[list]:
    """
    通过新浪财经获取概念板块列表

    返回: [{"name": "AI概念", "code": "...", "count": N}, ...]
    """
    url = "https://vip.stock.finance.sina.com.cn/q/go.php/vIndustryRank/kind/gpjyytj/index.phtml"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        # 解析表格
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
        boards = []
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 3:
                name_match = re.search(r'>([^<]+)<', cells[0])
                code_match = re.search(r'code=(\d+)', cells[0])
                count_match = re.search(r'>(\d+)<', cells[2])
                if name_match and code_match:
                    boards.append({
                        "name": name_match.group(1).strip(),
                        "code": code_match.group(1),
                        "count": int(count_match.group(1)) if count_match else 0,
                    })
        return boards
    except Exception:
        return None


if __name__ == "__main__":
    # 测试
    import pprint

    print("=== 测试 Sina K线 ===")
    data = sina_kline("600536")
    if data:
        print(f"  获取到 {len(data)} 条K线数据")
        print(f"  最新: {data[-1]}")

    print("\n=== 测试 Sina 实时行情 ===")
    data = sina_realtime("600536")
    if data:
        print(f"  {data.get('name')}: ¥{data.get('price')}")

    print("\n=== 测试 Tencent 批量行情 ===")
    data = tencent_realtime(["600536", "000001", "300750"])
    if data:
        for d in data:
            print(f"  {d.get('code')} {d.get('name')}: ¥{d.get('price')} ({d.get('change_pct'):+.2f}%)")

    print("\n=== 测试 Tencent K线 ===")
    data = tencent_kline("600536")
    if data:
        print(f"  获取到 {len(data)} 条K线数据")
        print(f"  最新: {data[-1]}")

    print("\n=== 备用数据源就绪 ===")
