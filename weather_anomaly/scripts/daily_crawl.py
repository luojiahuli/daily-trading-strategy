#!/usr/bin/env python3
"""
每日天气数据爬取
Usage: python scripts/daily_crawl.py
Crontab: 0 8 * * 1-5
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import get_database, save_city, save_daily_weather
from backend.crawler import get_weather_crawler, CITY_LIST


def main():
    print(f"[{datetime.now()}] 开始爬取天气数据...")

    db = get_database()
    crawler = get_weather_crawler()

    # 确保城市数据在数据库中
    for city in CITY_LIST:
        try:
            save_city(city)
        except Exception:
            pass

    # 获取所有城市
    cities = db.get_all_cities()
    print(f"共 {len(cities)} 个城市")

    # 爬取并保存
    saved = 0
    for city in cities:
        data = crawler.get_weather(city['city_code'])
        if data:
            if db.save_daily_weather(data):
                saved += 1

    print(f"[{datetime.now()}] 完成，保存 {saved} 条数据")


if __name__ == '__main__':
    main()
