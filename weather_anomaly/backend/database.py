"""
天气数据数据库 - SQLite 存储历史数据
"""
import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "weather.db")


def get_db_path() -> str:
    p = os.getenv("WEATHER_DB_PATH", DB_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """初始化数据库表"""
    conn = get_connection()
    cur = conn.cursor()

    # 城市主表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS city_master (
            city_code TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            province TEXT NOT NULL,
            lat REAL,
            lon REAL,
            elevation REAL
        )
    """)

    # 每日天气数据
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_weather (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_code TEXT NOT NULL,
            city_name TEXT NOT NULL,
            province TEXT,
            date TEXT NOT NULL,
            temp_max REAL,
            temp_min REAL,
            temp_avg REAL,
            precipitation REAL DEFAULT 0,
            humidity INTEGER,
            wind_speed REAL DEFAULT 0,
            weather_text TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city_code, date)
        )
    """)

    # 创建索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_city_date ON daily_weather(city_code, date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_weather(date)")

    conn.commit()
    conn.close()
    logger.info("数据库初始化完成")


def get_all_cities() -> list[dict]:
    """获取所有城市"""
    conn = get_connection()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM city_master ORDER BY province, city_name").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_city(city: dict):
    """保存城市"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO city_master (city_code, city_name, province, lat, lon, elevation)
        VALUES (:city_code, :city_name, :province, :lat, :lon, :elevation)
    """, city)
    conn.commit()
    conn.close()


def save_daily_weather(data: dict) -> bool:
    """保存每日天气数据"""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR REPLACE INTO daily_weather
            (city_code, city_name, province, date, temp_max, temp_min, temp_avg,
             precipitation, humidity, wind_speed, weather_text)
            VALUES (:city_code, :city_name, :province, :date, :temp_max, :temp_min, :temp_avg,
                    :precipitation, :humidity, :wind_speed, :weather_text)
        """, {
            "city_code": data.get("city_code"),
            "city_name": data.get("city_name"),
            "province": data.get("province"),
            "date": data.get("date"),
            "temp_max": data.get("temp_max"),
            "temp_min": data.get("temp_min"),
            "temp_avg": data.get("temp_avg"),
            "precipitation": data.get("precipitation", 0),
            "humidity": data.get("humidity"),
            "wind_speed": data.get("wind_speed", 0),
            "weather_text": data.get("weather_text", ""),
        })
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"保存天气数据失败: {e}")
        conn.close()
        return False


def get_history(city_code: str, days: int = 30) -> list[dict]:
    """获取城市历史天气"""
    conn = get_connection()
    cur = conn.cursor()
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = cur.execute("""
        SELECT * FROM daily_weather
        WHERE city_code = ? AND date >= ?
        ORDER BY date DESC
    """, (city_code, start_date)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_weather(days: int = 7) -> list[dict]:
    """获取最近N天的所有城市天气"""
    conn = get_connection()
    cur = conn.cursor()
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = cur.execute("""
        SELECT * FROM daily_weather
        WHERE date >= ?
        ORDER BY city_code, date DESC
    """, (start_date,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_weather() -> list[dict]:
    """获取所有城市最新一天的数据"""
    conn = get_connection()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT w.* FROM daily_weather w
        INNER JOIN (
            SELECT city_code, MAX(date) as max_date
            FROM daily_weather
            GROUP BY city_code
        ) latest ON w.city_code = latest.city_code AND w.date = latest.max_date
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# 初始化数据库
init_database()

# 全局实例
_db: Optional['Database'] = None


class Database:
    def __init__(self):
        pass

    def get_all_cities(self) -> list[dict]:
        return get_all_cities()

    def save_city(self, city: dict):
        save_city(city)

    def save_daily_weather(self, data: dict) -> bool:
        return save_daily_weather(data)

    def get_history(self, city_code: str, days: int = 30) -> list[dict]:
        return get_history(city_code, days)

    def get_recent_weather(self, days: int = 7) -> list[dict]:
        return get_recent_weather(days)

    def get_latest_weather(self) -> list[dict]:
        return get_latest_weather()


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
