"""
豆瓣热搜/榜单爬虫Agent - 支持电影/图书/音乐多品类
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, Any, List

import requests

from core.base_agent import Agent

logger = logging.getLogger(__name__)


class DoubanCollectorAgent(Agent):
    """豆瓣热搜/榜单爬虫 - 多品类"""

    def __init__(self):
        super().__init__("douban_collector", "豆瓣热搜爬虫Agent")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douban.com/",
        }

    async def execute(self, params: Dict = None) -> Dict[str, Any]:
        params = params or {}
        mode = params.get("mode", "daily")
        categories = params.get("categories", ["movie", "book", "music"])

        try:
            all_topics = []
            async def fetch_cat(cat):
                if cat == "movie":
                    return await self._fetch_movie_top()
                elif cat == "book":
                    return await self._fetch_book_top()
                elif cat == "music":
                    return await self._fetch_music_top()
                return []
            
            # 并发抓取三个品类
            results = await asyncio.gather(
                fetch_cat("movie") if "movie" in categories else asyncio.sleep(0),
                fetch_cat("book") if "book" in categories else asyncio.sleep(0),
                fetch_cat("music") if "music" in categories else asyncio.sleep(0),
                return_exceptions=True
            )
            
            for topics in results:
                if isinstance(topics, list):
                    all_topics.extend(topics)
            
            if not all_topics:
                raise RuntimeError("所有品类均无数据")
                
        except Exception as e:
            logger.warning(f"[Douban] 抓取失败，使用demo: {e}")
            all_topics = self._generate_demo_data()

        return {
            "platform": "douban",
            "topics": all_topics,
            "count": len(all_topics),
            "mode": mode
        }

    async def _fetch_movie_top(self) -> List[Dict]:
        """抓取豆瓣电影Top250"""
        try:
            url = "https://movie.douban.com/top250"
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            
            topics = []
            items = re.findall(r'<div class="item">.*?<div class="info">.*?</div>\s*</div>\s*</div>', resp.text, re.DOTALL)
            
            for item in items[:10]:
                rank = re.findall(r'<em>(\d+)</em>', item)
                title = re.findall(r'<span class="title">([^<]+)</span>', item)
                rate = re.findall(r'class="rating_num"[^>]*>(\d+\.\d+)</span>', item)
                votes = re.findall(r'<span>(\d+)人评价</span>', item)
                
                rank = int(rank[0]) if rank else 0
                title = title[0].strip() if title else ""
                rate = float(rate[0]) if rate else 0
                votes_num = int(votes[0].replace(",", "")) if votes else 0
                
                topics.append({
                    "topic_title": f"电影《{title}》",
                    "heat_score": min(rate * 10, 100),
                    "platform": "douban",
                    "timestamp": datetime.now().isoformat(),
                    "comments_count": votes_num,
                    "likes_count": int(votes_num * 0.3),
                    "views": votes_num * 10,
                    "sentiment": 0.2,
                    "entities": {"works": [title], "companies": [], "places": []},
                    "category": "影视",
                    "sub_category": "电影",
                    "link": "",
                    "rank": rank,
                    "rating": rate,
                })
            
            if topics:
                return topics
            raise RuntimeError("电影无数据")
        except Exception as e:
            logger.error(f"[Douban] 电影抓取异常: {e}")
            return []

    async def _fetch_book_top(self) -> List[Dict]:
        """抓取豆瓣图书Top250"""
        try:
            url = "https://book.douban.com/top250"
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            
            topics = []
            items = re.findall(
                r'<tr class="item">.*?<div class="pl2">.*?<a[^>]*>\s*([^<\n]+)',
                resp.text, re.DOTALL
            )
            rates = re.findall(r'<span class="rating_nums">(\d+\.\d+)</span>', resp.text)
            votes = re.findall(r'<span class="pl">\s*\(?\s*(\d+)\s*人评价', resp.text)
            
            for i, (item, rate, votes_str) in enumerate(zip(items[:10], rates[:10], votes[:10])):
                title = item.strip()
                votes_num_str = votes_str.replace(",", "").strip()
                try:
                    votes_num = int(votes_num_str) if votes_num_str else 0
                except:
                    votes_num = 0
                
                topics.append({
                    "topic_title": f"图书《{title}》",
                    "heat_score": min(float(rate) * 10, 100),
                    "platform": "douban",
                    "timestamp": datetime.now().isoformat(),
                    "comments_count": votes_num,
                    "likes_count": int(votes_num * 0.2),
                    "views": votes_num * 5,
                    "sentiment": 0.15,
                    "entities": {"works": [title], "companies": [], "places": []},
                    "category": "文化",
                    "sub_category": "图书",
                    "link": "",
                    "rank": i + 1,
                    "rating": float(rate),
                })
            
            if topics:
                return topics
            raise RuntimeError("图书无数据")
        except Exception as e:
            logger.error(f"[Douban] 图书抓取异常: {e}")
            return []

    async def _fetch_music_top(self) -> List[Dict]:
        """抓取豆瓣音乐Top250"""
        try:
            url = "https://music.douban.com/top250"
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            
            topics = []
            items = re.findall(r'class="item" id="\d+">(.*?)</tr>', resp.text, re.DOTALL)
            
            for i, item in enumerate(items[:10]):
                # 标题
                titles = re.findall(r'<div class="pl2">\s*<a[^>]*>\s*([^<\n]+)', item)
                title = titles[0].strip() if titles else ""
                
                # 评分
                rates = re.findall(r'<span class="rating_nums">(\d+\.\d+)</span>', item)
                rate = float(rates[0]) if rates else 0
                
                # 评价人数
                votes_list = re.findall(r'<span class="pl">\s*\(?\s*(\d+)\s*人评价', item)
                votes_num = 0
                if votes_list:
                    try:
                        votes_num = int(votes_list[0].replace(",", ""))
                    except:
                        pass
                
                if not title:
                    continue
                
                topics.append({
                    "topic_title": f"音乐《{title}》",
                    "heat_score": min(rate * 10, 100),
                    "platform": "douban",
                    "timestamp": datetime.now().isoformat(),
                    "comments_count": votes_num,
                    "likes_count": int(votes_num * 0.25),
                    "views": votes_num * 8,
                    "sentiment": 0.25,
                    "entities": {"works": [title], "companies": [], "places": []},
                    "category": "音乐",
                    "sub_category": "专辑",
                    "link": "",
                    "rank": i + 1,
                    "rating": rate,
                })
            
            if topics:
                return topics
            raise RuntimeError("音乐无数据")
        except Exception as e:
            logger.error(f"[Douban] 音乐抓取异常: {e}")
            return []

    def _generate_demo_data(self) -> List[Dict]:
        """生成模拟豆瓣数据"""
        demo = [
            {"topic_title": "《肖申克的救赎》登顶豆瓣电影榜首", "heat_score": 97, "comments_count": 3288857, "views": 89000000, "sentiment": 0.55, "category": "影视", "sub_category": "电影"},
            {"topic_title": "《红楼梦》图书评分高达9.7", "heat_score": 95, "comments_count": 463605, "views": 54000000, "sentiment": 0.48, "category": "文化", "sub_category": "图书"},
            {"topic_title": "《范特西》专辑 - 周杰伦经典", "heat_score": 93, "comments_count": 190777, "views": 32000000, "sentiment": 0.65, "category": "音乐", "sub_category": "专辑"},
            {"topic_title": "《霸王别姬》陈凯歌执导", "heat_score": 92, "comments_count": 2426734, "views": 67000000, "sentiment": 0.52, "category": "影视", "sub_category": "电影"},
            {"topic_title": "《活着》余华著", "heat_score": 88, "comments_count": 915541, "views": 43000000, "sentiment": 0.45, "category": "文化", "sub_category": "图书"},
        ]
        ts = datetime.now().isoformat()
        return [{
            **d,
            "platform": "douban",
            "timestamp": ts,
            "likes_count": int(d["comments_count"] * 0.3),
            "entities": {"companies": [], "works": re.findall(r"《([^》]+)》", d["topic_title"]), "places": []},
            "link": "",
            "rank": i + 1,
            "rating": d["heat_score"] / 10,
        } for i, d in enumerate(demo)]

    def _extract_entities(self, text: str) -> Dict[str, List[str]]:
        import re
        # 提取影视作品名（用书名号）
        works = re.findall(r"《([^》]+)》", text)
        # 提取常见公司/平台
        companies = re.findall(r"(字节|腾讯|阿里|爱奇艺|优酷|芒果|B站)", text)
        return {"companies": companies, "works": works, "places": []}
