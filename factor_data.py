#!/usr/bin/env python3
"""
因子数据获取模块 — 舆情数据、国家政策、央视新闻联播数据
所有数据缓存在本地，按日更新
"""
import os, sys, json, time, re, ssl, urllib.request, hashlib
from datetime import datetime, timedelta
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache", "factors")
os.makedirs(CACHE_DIR, exist_ok=True)

# SSL 绕过（macOS 证书问题）
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# ============================================================
# 缓存管理
# ============================================================
def _cache_path(name: str, date_str: str = None) -> str:
    """获取缓存文件路径"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(CACHE_DIR, f"{name}_{date_str}.json")

def _load_cache(path: str, max_age_hours: int = 6) -> dict:
    """加载缓存（带过期时间）"""
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        age = (time.time() - mtime) / 3600
        if age > max_age_hours:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def _save_cache(path: str, data):
    """保存缓存"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 1. 舆情数据获取（Tavily搜索财经新闻）
# ============================================================
def get_market_sentiment(date_str: str = None) -> dict:
    """
    获取当日市场舆情情绪
    返回: {
        'date': str,
        'overall_sentiment': float,  # -1 ~ 1
        'positive_count': int,
        'negative_count': int,
        'neutral_count': int,
        'keywords': list[str],
        'hot_topics': list[str],
        'source': str
    }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    # 尝试从缓存加载
    cached = _load_cache(_cache_path("sentiment", date_str), max_age_hours=8)
    if cached:
        cached["source"] = "cache"
        return cached
    
    try:
        # 检查是否有 Tavily API Key
        api_key = ""
        env_path = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("TAVILY_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
        
        if not api_key:
            return _fallback_sentiment(date_str)
        
        from tavily import TavilyClient
        
        client = TavilyClient(api_key=api_key)
        
        # 搜索 A 股市场综合新闻
        queries = [
            f"A股市场 今日行情 {date_str[:7]}",
            f"中国股市 财经新闻 {date_str[:7]}",
            f"中国宏观经济 政策 最新",
        ]
        
        all_results = []
        for q in queries:
            try:
                resp = client.search(query=q, search_depth="basic", max_results=10)
                all_results.extend(resp.get("results", []))
                time.sleep(0.5)
            except:
                pass
        
        # 分析情感
        pos_words = ["上涨", "涨停", "利好", "反弹", "突破", "增长", "提振", "上涨", "牛市", "放量",
                     "资金流入", "超预期", "回升", "走强", "活跃"]
        neg_words = ["下跌", "跌停", "利空", "回调", "破位", "下降", "打压", "下跌", "熊市", "缩量",
                     "资金流出", "不及预期", "回落", "走弱", "低迷"]
        
        positive, negative, neutral = 0, 0, 0
        keywords = []
        topics = []
        
        for r in all_results:
            title = (r.get("title", "") + " " + r.get("content", "")).lower()
            pos_count = sum(1 for w in pos_words if w in title)
            neg_count = sum(1 for w in neg_words if w in title)
            if pos_count > neg_count:
                positive += 1
            elif neg_count > pos_count:
                negative += 1
            else:
                neutral += 1
        
        total = positive + negative + neutral
        sentiment = (positive - negative) / max(total, 1)
        
        # 提取关键词
        all_text = " ".join(r.get("title", "") + " " + r.get("content", "") for r in all_results)
        # 简单的词频统计
        from collections import Counter
        import re as _re
        words = _re.findall(r'[\u4e00-\u9fff]{2,4}', all_text)
        word_freq = Counter(words).most_common(20)
        keywords = [w for w, c in word_freq[:10]]
        
        # 提取标题作为热点话题
        topics = [r.get("title", "")[:30] for r in all_results[:5] if r.get("title")]
        
        result = {
            "date": date_str,
            "overall_sentiment": round(sentiment, 3),
            "positive_count": positive,
            "negative_count": negative,
            "neutral_count": neutral,
            "keywords": keywords,
            "hot_topics": topics,
            "total_articles": total,
            "source": "tavily"
        }
        
        _save_cache(_cache_path("sentiment", date_str), result)
        return result
        
    except Exception as e:
        print(f"  ⚠️ Tavily舆情获取失败: {e}", file=sys.stderr)
        return _fallback_sentiment(date_str)


def _fallback_sentiment(date_str: str) -> dict:
    """舆情降级方案 — 基于历史数据的默认值"""
    return {
        "date": date_str,
        "overall_sentiment": 0,
        "positive_count": 0,
        "negative_count": 0,
        "neutral_count": 0,
        "keywords": [],
        "hot_topics": [],
        "total_articles": 0,
        "source": "fallback"
    }


# ============================================================
# 2. 国家政策数据获取
# ============================================================
def get_policy_data(date_str: str = None) -> dict:
    """
    获取近期国家政策数据（金融/经济相关）
    返回: {
        'date': str,
        'policies': [{'title': str, 'source': str, 'date': str, 'summary': str, 'impact_score': float}],
        'overall_policy_score': float,  # -1 ~ 1
    }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    cached = _load_cache(_cache_path("policy", date_str), max_age_hours=12)
    if cached:
        return cached
    
    try:
        from tavily import TavilyClient
        
        api_key = ""
        env_path = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("TAVILY_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
        
        if not api_key:
            return _fallback_policy(date_str)
        
        client = TavilyClient(api_key=api_key)
        
        queries = [
            "中国 经济政策 金融政策 最新",
            "中国人民银行 货币政策 最新",
            "证监会 股市政策 最新",
            "国务院 经济刺激政策 最新",
        ]
        
        all_policies = []
        seen_titles = set()
        
        for q in queries:
            try:
                resp = client.search(query=q, search_depth="basic", max_results=8)
                for r in resp.get("results", []):
                    title = r.get("title", "")
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_policies.append({
                            "title": title,
                            "source": r.get("url", "")[:50],
                            "date": date_str,
                            "summary": (r.get("content", "") or "")[:200],
                            "impact_score": _estimate_policy_impact(title + " " + (r.get("content", "") or ""))
                        })
                time.sleep(0.3)
            except:
                pass
        
        # 计算综合政策得分
        scores = [p["impact_score"] for p in all_policies]
        overall_score = sum(scores) / max(len(scores), 1) if scores else 0
        
        result = {
            "date": date_str,
            "policies": all_policies[:10],
            "overall_policy_score": round(overall_score, 3),
            "policy_count": len(all_policies),
            "source": "tavily"
        }
        
        _save_cache(_cache_path("policy", date_str), result)
        return result
        
    except Exception as e:
        print(f"  ⚠️ 政策数据获取失败: {e}", file=sys.stderr)
        return _fallback_policy(date_str)


def _estimate_policy_impact(text: str) -> float:
    """估算政策影响得分 -1 ~ 1"""
    strong_pos = ["利好", "降准", "降息", "减税", "放水", "宽松", "刺激", "支持", "提振", "做多"]
    strong_neg = ["利空", "加息", "加税", "收紧", "打压", "限制", "监管", "处罚", "做空", "紧缩"]
    weak_pos = ["推动", "促进", "鼓励", "加大", "提高", "扩大"]
    weak_neg = ["减少", "降低", "压缩", "收缩", "控制", "防范"]
    
    score = 0.0
    text_lower = text.lower()
    for w in strong_pos:
        if w in text_lower:
            score += 0.3
    for w in strong_neg:
        if w in text_lower:
            score -= 0.3
    for w in weak_pos:
        if w in text_lower:
            score += 0.1
    for w in weak_neg:
        if w in text_lower:
            score -= 0.1
    return max(-1, min(1, score))


def _fallback_policy(date_str: str) -> dict:
    """政策降级方案"""
    return {
        "date": date_str,
        "policies": [],
        "overall_policy_score": 0,
        "policy_count": 0,
        "source": "fallback"
    }


# ============================================================
# 3. 央视新闻联播数据获取
# ============================================================
def get_cctv_news(date_str: str = None) -> dict:
    """
    获取央视新闻联播头条数据
    返回: {
        'date': str,
        'headlines': [{'title': str, 'summary': str, 'order': int}],
        'economy_related': bool,
        'stock_related': bool,
        'market_impact': float,  # -1 ~ 1
        'total_items': int
    }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    cached = _load_cache(_cache_path("cctv", date_str), max_age_hours=12)
    if cached:
        return cached
    
    try:
        # 尝试从央视网抓取新闻联播文字版
        headlines = _scrape_cctv_text(date_str)
        if not headlines:
            # 降级：用Tavily搜索
            headlines = _search_cctv_via_tavily(date_str)
        
        if not headlines:
            return _fallback_cctv(date_str)
        
        # 分析是否经济和股市相关
        economy_kw = ["经济", "金融", "股市", "投资", "消费", "GDP", "增长", "贸易", "产业", "改革", "财政", "货币"]
        stock_kw = ["股市", "证券", "资本", "上市", "交易", "行情", "A股", "板块", "资金"]
        
        all_text = " ".join(h["title"] + " " + h.get("summary", "") for h in headlines)
        economy_related = any(k in all_text for k in economy_kw)
        stock_related = any(k in all_text for k in stock_kw)
        
        # 市场影响估算
        impact = 0.0
        if economy_related:
            impact += 0.2
        if stock_related:
            impact += 0.3
        
        pos_words = ["增长", "好转", "发展", "推进", "扩大", "支持", "提升"]
        neg_words = ["风险", "下降", "放缓", "危机", "困难", "挑战"]
        for w in pos_words:
            if w in all_text:
                impact += 0.05
        for w in neg_words:
            if w in all_text:
                impact -= 0.05
        
        impact = max(-1, min(1, impact))
        
        result = {
            "date": date_str,
            "headlines": headlines[:10],
            "economy_related": economy_related,
            "stock_related": stock_related,
            "market_impact": round(impact, 3),
            "total_items": len(headlines),
            "source": "cctv"
        }
        
        _save_cache(_cache_path("cctv", date_str), result)
        return result
        
    except Exception as e:
        print(f"  ⚠️ 新闻联播数据获取失败: {e}", file=sys.stderr)
        return _fallback_cctv(date_str)


def _scrape_cctv_text(date_str: str) -> list:
    """从央视网抓取新闻联播文字版"""
    try:
        # 尝试从 cctv.com 获取
        month_str = date_str[:7].replace("-", "")
        day_str = date_str[-2:]
        url = f"https://tv.cctv.com/lm/xwlb/{month_str}{day_str}.shtml"
        
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        })
        resp = urllib.request.urlopen(req, timeout=10, context=_CTX)
        html = resp.read().decode("utf-8", errors="ignore")
        
        # 提取标题
        headlines = []
        # 匹配 <li><a ...>标题</a></li> 或 <span class="title">标题</span>
        pattern = r'<[^>]*>(?:[\u4e00-\u9fff]{4,30})</[^>]*>'
        titles = re.findall(r'(?:title|tit|text)[^>]*>([^<]{4,60})<', html)
        
        for t in titles[:15]:
            t = t.strip()
            if len(t) >= 4 and len(t) <= 50:
                headlines.append({"title": t, "summary": "", "order": len(headlines) + 1})
        
        return headlines[:10]
    except:
        return []


def _search_cctv_via_tavily(date_str: str) -> list:
    """通过Tavily搜索新闻联播内容"""
    try:
        from tavily import TavilyClient
        
        api_key = ""
        env_path = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("TAVILY_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                        break
        
        if not api_key:
            return []
        
        client = TavilyClient(api_key=api_key)
        queries = [
            f"新闻联播 {date_str[:7]} 主要内容",
            f"央视新闻联播 今日要闻 {date_str[:7]}",
        ]
        
        headlines = []
        seen = set()
        for q in queries:
            try:
                resp = client.search(query=q, search_depth="basic", max_results=8)
                for r in resp.get("results", []):
                    title = r.get("title", "")
                    if title and title not in seen:
                        seen.add(title)
                        headlines.append({
                            "title": title[:40],
                            "summary": (r.get("content", "") or "")[:150],
                            "order": len(headlines) + 1
                        })
                time.sleep(0.3)
            except:
                pass
        
        return headlines[:8]
    except:
        return []


def _fallback_cctv(date_str: str) -> dict:
    """新闻联播降级方案"""
    return {
        "date": date_str,
        "headlines": [],
        "economy_related": False,
        "stock_related": False,
        "market_impact": 0,
        "total_items": 0,
        "source": "fallback"
    }


# ============================================================
# 4. 综合因子获取
# ============================================================
def get_all_factors(date_str: str = None) -> dict:
    """
    获取所有外部因子
    返回: {
        'date': str,
        'sentiment': {...},
        'policy': {...},
        'cctv': {...},
        'composite_score': float,
        'factor_vector': [sentiment, policy, cctv_impact, ...]
    }
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    print(f"  📡 获取外部因子数据 ({date_str})...")
    
    sentiment = get_market_sentiment(date_str)
    policy = get_policy_data(date_str)
    cctv = get_cctv_news(date_str)
    
    # 综合得分
    s = sentiment.get("overall_sentiment", 0)
    p = policy.get("overall_policy_score", 0)
    c = cctv.get("market_impact", 0)
    
    composite = s * 0.3 + p * 0.4 + c * 0.3
    
    return {
        "date": date_str,
        "sentiment": sentiment,
        "policy": policy,
        "cctv": cctv,
        "composite_score": round(composite, 3),
        "factor_vector": [round(s, 3), round(p, 3), round(c, 3)],
        "sentiment_source": sentiment.get("source", "fallback"),
        "policy_source": policy.get("source", "fallback"),
        "cctv_source": cctv.get("source", "fallback"),
    }


# ============================================================
# 5. 历史因子回填（逐日补全历史因子数据）
# ============================================================
def backfill_history_factors(start_date: str = "2024-06-01"):
    """
    回填历史因子数据，用于模型训练
    生成 CSV 文件: .cache/factors/history_factors.csv
    """
    print(f"📥 回填历史因子数据 ({start_date} ~ 至今)...")
    
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.now()
    
    # 读取已有的历史因子缓存
    factor_records = []
    
    # 先看已有的CSV
    csv_path = os.path.join(CACHE_DIR, "history_factors.csv")
    if os.path.exists(csv_path):
        import pandas as pd
        existing = pd.read_csv(csv_path)
        factor_records = existing.to_dict("records")
        last_date = existing["date"].max() if len(existing) > 0 else start_date
        print(f"  已有 {len(factor_records)} 条记录，从 {last_date} 继续")
        start = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
    
    current = start
    count = 0
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        # 跳过周末
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        
        # 检查是否已有缓存
        if any(r["date"] == ds for r in factor_records):
            current += timedelta(days=1)
            continue
        
        factors = get_all_factors(ds)
        factor_records.append({
            "date": ds,
            "sentiment": factors["factor_vector"][0],
            "policy_score": factors["factor_vector"][1],
            "cctv_impact": factors["factor_vector"][2],
            "composite": factors["composite_score"],
        })
        count += 1
        
        if count % 10 == 0:
            print(f"  已获取 {count} 天数据...")
        
        # 避免请求频率过高
        time.sleep(0.2)
        current += timedelta(days=1)
    
    # 保存CSV
    import pandas as pd
    df = pd.DataFrame(factor_records)
    df = df.drop_duplicates(subset=["date"])
    df = df.sort_values("date")
    df.to_csv(csv_path, index=False)
    print(f"✅ 历史因子数据已保存: {csv_path} ({len(df)} 条)")
    return df


if __name__ == "__main__":
    # 测试
    import json
    factors = get_all_factors()
    print(json.dumps(factors, ensure_ascii=False, indent=2, default=str))
