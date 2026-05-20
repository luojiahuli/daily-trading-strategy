"""
天气数据爬虫 - 和风天气API + 模拟fallback
"""
import os
import random
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# 中国主要城市列表（地级市）
CITY_LIST = [
    {"city_code": "101010100", "city_name": "北京", "province": "北京", "lat": 39.9, "lon": 116.4},
    {"city_code": "101020100", "city_name": "上海", "province": "上海", "lat": 31.2, "lon": 121.5},
    {"city_code": "101280100", "city_name": "广州", "province": "广东", "lat": 23.1, "lon": 113.3},
    {"city_code": "101280600", "city_name": "深圳", "province": "广东", "lat": 22.5, "lon": 114.1},
    {"city_code": "101270100", "city_name": "成都", "province": "四川", "lat": 30.7, "lon": 104.1},
    {"city_code": "101270400", "city_name": "绵阳", "province": "四川", "lat": 31.5, "lon": 104.7},
    {"city_code": "101040100", "city_name": "重庆", "province": "重庆", "lat": 29.5, "lon": 106.5},
    {"city_code": "101200100", "city_name": "武汉", "province": "湖北", "lat": 30.6, "lon": 114.3},
    {"city_code": "101110100", "city_name": "西安", "province": "陕西", "lat": 34.3, "lon": 108.9},
    {"city_code": "101180100", "city_name": "郑州", "province": "河南", "lat": 34.8, "lon": 113.6},
    {"city_code": "101250100", "city_name": "长沙", "province": "湖南", "lat": 28.2, "lon": 113.0},
    {"city_code": "101190100", "city_name": "南京", "province": "江苏", "lat": 32.1, "lon": 118.8},
    {"city_code": "101210100", "city_name": "杭州", "province": "浙江", "lat": 30.3, "lon": 120.2},
    {"city_code": "101070100", "city_name": "沈阳", "province": "辽宁", "lat": 41.8, "lon": 123.4},
    {"city_code": "101050100", "city_name": "哈尔滨", "province": "黑龙江", "lat": 45.8, "lon": 126.5},
    {"city_code": "101060100", "city_name": "长春", "province": "吉林", "lat": 43.9, "lon": 125.3},
    {"city_code": "101190400", "city_name": "苏州", "province": "江苏", "lat": 31.3, "lon": 120.6},
    {"city_code": "101200200", "city_name": "宜昌", "province": "湖北", "lat": 30.7, "lon": 111.3},
    {"city_code": "101230500", "city_name": "厦门", "province": "福建", "lat": 24.5, "lon": 118.1},
    {"city_code": "101240100", "city_name": "南昌", "province": "江西", "lat": 28.7, "lon": 115.9},
    {"city_code": "101210300", "city_name": "宁波", "province": "浙江", "lat": 29.9, "lon": 121.6},
    {"city_code": "101270600", "city_name": "宜宾", "province": "四川", "lat": 28.8, "lon": 104.6},
    {"city_code": "101260101", "city_name": "贵阳", "province": "贵州", "lat": 26.6, "lon": 106.7},
    {"city_code": "101280900", "city_name": "珠海", "province": "广东", "lat": 22.3, "lon": 113.6},
    {"city_code": "101280800", "city_name": "佛山", "province": "广东", "lat": 23.0, "lon": 113.1},
    {"city_code": "101090500", "city_name": "保定", "province": "河北", "lat": 38.9, "lon": 115.5},
    {"city_code": "101100100", "city_name": "太原", "province": "山西", "lat": 37.9, "lon": 112.5},
    {"city_code": "101080100", "city_name": "兰州", "province": "甘肃", "lat": 36.1, "lon": 103.8},
    {"city_code": "101160100", "city_name": "昆明", "province": "云南", "lat": 25.0, "lon": 102.7},
    {"city_code": "101300100", "city_name": "南宁", "province": "广西", "lat": 22.8, "lon": 108.3},
    {"city_code": "101120100", "city_name": "济南", "province": "山东", "lat": 36.7, "lon": 117.0},
    {"city_code": "101120200", "city_name": "青岛", "province": "山东", "lat": 36.1, "lon": 120.4},
    {"city_code": "101150100", "city_name": "西宁", "province": "青海", "lat": 36.6, "lon": 101.8},
    {"city_code": "101170100", "city_name": "呼和浩特", "province": "内蒙古", "lat": 40.8, "lon": 111.7},
    {"city_code": "101180600", "city_name": "洛阳", "province": "河南", "lat": 34.6, "lon": 112.5},
    {"city_code": "101230100", "city_name": "福州", "province": "福建", "lat": 26.1, "lon": 119.3},
    {"city_code": "101020500", "city_name": "浦东", "province": "上海", "lat": 31.2, "lon": 121.5},
    {"city_code": "101050300", "city_name": "大连", "province": "辽宁", "lat": 38.9, "lon": 121.6},
    {"city_code": "101210400", "city_name": "温州", "province": "浙江", "lat": 28.0, "lon": 120.7},
    {"city_code": "101310100", "city_name": "海口", "province": "海南", "lat": 20.0, "lon": 110.3},
    {"city_code": "101270300", "city_name": "南充", "province": "四川", "lat": 30.8, "lon": 106.1},
    {"city_code": "101060300", "city_name": "吉林", "province": "吉林", "lat": 43.8, "lon": 126.5},
    {"city_code": "101110600", "city_name": "延安", "province": "陕西", "lat": 36.6, "lon": 109.5},
    {"city_code": "101190300", "city_name": "扬州", "province": "江苏", "lat": 32.4, "lon": 119.4},
    {"city_code": "101190500", "city_name": "徐州", "province": "江苏", "lat": 34.2, "lon": 117.2},
    {"city_code": "101300400", "city_name": "桂林", "province": "广西", "lat": 25.3, "lon": 110.3},
    {"city_code": "101080300", "city_name": "天水", "province": "甘肃", "lat": 34.6, "lon": 105.7},
    {"city_code": "101240400", "city_name": "赣州", "province": "江西", "lat": 25.8, "lon": 114.9},
    {"city_code": "101260500", "city_name": "遵义", "province": "贵州", "lat": 27.7, "lon": 106.9},
    {"city_code": "101150200", "city_name": "格尔木", "province": "青海", "lat": 36.4, "lon": 94.9},
    {"city_code": "101230300", "city_name": "泉州", "province": "福建", "lat": 24.9, "lon": 118.7},
    {"city_code": "101180200", "city_name": "开封", "province": "河南", "lat": 34.8, "lon": 114.4},
    {"city_code": "101280300", "city_name": "汕头", "province": "广东", "lat": 23.4, "lon": 116.7},
    {"city_code": "101021700", "city_name": "崇明", "province": "上海", "lat": 31.6, "lon": 121.4},
    {"city_code": "101160200", "city_name": "大理", "province": "云南", "lat": 25.6, "lon": 100.2},
    {"city_code": "101140100", "city_name": "拉萨", "province": "西藏", "lat": 29.7, "lon": 91.1},
    {"city_code": "101130100", "city_name": "乌鲁木齐", "province": "新疆", "lat": 43.8, "lon": 87.6},
    {"city_code": "101180400", "city_name": "新乡", "province": "河南", "lat": 35.3, "lon": 113.9},
    {"city_code": "101180800", "city_name": "信阳", "province": "河南", "lat": 32.1, "lon": 114.1},
    {"city_code": "101090200", "city_name": "唐山", "province": "河北", "lat": 39.6, "lon": 118.2},
    {"city_code": "101090300", "city_name": "秦皇岛", "province": "河北", "lat": 39.9, "lon": 119.6},
    {"city_code": "101130300", "city_name": "吐鲁番", "province": "新疆", "lat": 42.9, "lon": 89.2},
    {"city_code": "101100200", "city_name": "大同", "province": "山西", "lat": 40.1, "lon": 113.3},
    {"city_code": "101070300", "city_name": "鞍山", "province": "辽宁", "lat": 41.1, "lon": 123.0},
    {"city_code": "101040300", "city_name": "涪陵", "province": "重庆", "lat": 29.7, "lon": 107.4},
    {"city_code": "101140200", "city_name": "日喀则", "province": "西藏", "lat": 29.3, "lon": 88.9},
]

# 季节性基础温度（按月份）
MONTHLY_TEMP = {
    1: {"north": -5, "central": 5, "south": 15},
    2: {"north": -2, "central": 8, "south": 18},
    3: {"north": 6, "central": 14, "south": 22},
    4: {"north": 14, "central": 20, "south": 26},
    5: {"north": 20, "central": 25, "south": 29},
    6: {"north": 25, "central": 28, "south": 31},
    7: {"north": 27, "central": 33, "south": 33},
    8: {"north": 26, "central": 32, "south": 32},
    9: {"north": 20, "central": 26, "south": 30},
    10: {"north": 13, "central": 20, "south": 25},
    11: {"north": 4, "central": 13, "south": 20},
    12: {"north": -2, "central": 6, "south": 15},
}

WEATHER_TEXT_OPTIONS = ["晴", "多云", "阴", "小雨", "中雨", "雷阵雨", "晴间多云", "阵雨", "雾", "霾"]


def get_region(city: dict) -> str:
    """根据城市纬度判断区域"""
    lat = city.get("lat", 30)
    if lat > 40:
        return "north"
    elif lat > 30:
        return "central"
    else:
        return "south"


def generate_mock_weather(city: dict) -> dict:
    """生成模拟天气数据"""
    month = datetime.now().month
    region = get_region(city)
    base_temp = MONTHLY_TEMP.get(month, MONTHLY_TEMP[5]).get(region, 20)

    # 添加随机波动
    variance = random.uniform(3, 8)
    temp_avg = round(base_temp + random.uniform(-variance, variance), 1)
    temp_max = round(temp_avg + random.uniform(2, 6), 1)
    temp_min = round(temp_avg - random.uniform(2, 6), 1)

    # 随机降水
    precipitation = round(random.uniform(0, 15) if random.random() > 0.5 else 0, 1)

    return {
        "city_code": city["city_code"],
        "city_name": city["city_name"],
        "province": city["province"],
        "date": datetime.now().strftime("%Y-%m-%d"),
        "temp_max": temp_max,
        "temp_min": temp_min,
        "temp_avg": temp_avg,
        "precipitation": precipitation,
        "humidity": random.randint(30, 95),
        "wind_speed": round(random.uniform(0, 8), 1),
        "weather_text": random.choice(WEATHER_TEXT_OPTIONS),
    }


async def fetch_qweather(city_code: str, key: str) -> Optional[dict]:
    """从和风天气API获取数据"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"https://api.qweather.com/v7/weather/now?location={city_code}&key={key}"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "200":
                    now = data["now"]
                    city_info = next((c for c in CITY_LIST if c["city_code"] == city_code), {})
                    return {
                        "city_code": city_code,
                        "city_name": city_info.get("city_name", ""),
                        "province": city_info.get("province", ""),
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "temp_avg": float(now.get("temp", 20)),
                        "temp_max": float(now.get("temp", 20)) + 3,
                        "temp_min": float(now.get("temp", 20)) - 3,
                        "humidity": int(now.get("humidity", 50)),
                        "wind_speed": float(now.get("windSpeed", 0)),
                        "weather_text": now.get("text", ""),
                        "precipitation": 0,
                    }
    except Exception as e:
        logger.warning(f"和风API调用失败: {e}")
    return None


class WeatherCrawler:
    """天气爬虫"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.today_cache = {}

    async def get_weather(self, city_code: str) -> Optional[dict]:
        """获取单个城市天气"""
        # 检查缓存
        today = datetime.now().strftime("%Y-%m-%d")
        if city_code in self.today_cache:
            return self.today_cache[city_code]

        # 如果有API key，尝试调用
        if self.api_key:
            data = await fetch_qweather(city_code, self.api_key)
            if data:
                self.today_cache[city_code] = data
                return data

        # fallback到模拟数据
        city = next((c for c in CITY_LIST if c["city_code"] == city_code), None)
        if city:
            data = generate_mock_weather(city)
            self.today_cache[city_code] = data
            return data
        return None

    def get_all_cities(self) -> list:
        return CITY_LIST

    async def crawl_all(self) -> list:
        """爬取所有城市天气"""
        results = []
        for city in CITY_LIST:
            data = await self.get_weather(city["city_code"])
            if data:
                results.append(data)
        return results

    def get_mock_weather_for_all(self) -> list:
        """获取所有城市的模拟天气"""
        results = []
        today = datetime.now().strftime("%Y-%m-%d")
        for city in CITY_LIST:
            data = generate_mock_weather(city)
            data["date"] = today
            results.append(data)
        return results


# 同步版本的爬虫（用于cronjob）
class SyncWeatherCrawler:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def get_weather(self, city_code: str) -> Optional[dict]:
        """同步获取单个城市天气"""
        if self.api_key:
            import httpx
            try:
                resp = httpx.get(
                    f"https://api.qweather.com/v7/weather/now?location={city_code}&key={self.api_key}",
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "200":
                        now = data["now"]
                        city_info = next((c for c in CITY_LIST if c["city_code"] == city_code), {})
                        return {
                            "city_code": city_code,
                            "city_name": city_info.get("city_name", ""),
                            "province": city_info.get("province", ""),
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "temp_avg": float(now.get("temp", 20)),
                            "temp_max": float(now.get("temp", 20)) + 3,
                            "temp_min": float(now.get("temp", 20)) - 3,
                            "humidity": int(now.get("humidity", 50)),
                            "wind_speed": float(now.get("windSpeed", 0)),
                            "weather_text": now.get("text", ""),
                            "precipitation": 0,
                        }
            except Exception as e:
                logger.warning(f"和风API调用失败: {e}")

        # fallback
        city = next((c for c in CITY_LIST if c["city_code"] == city_code), None)
        if city:
            return generate_mock_weather(city)
        return None

    def get_all_cities(self) -> list:
        return CITY_LIST

    def crawl_all(self) -> list:
        results = []
        for city in CITY_LIST:
            data = self.get_weather(city["city_code"])
            if data:
                results.append(data)
        return results


_crawler: Optional[SyncWeatherCrawler] = None


def get_weather_crawler() -> SyncWeatherCrawler:
    global _crawler
    if _crawler is None:
        # 从config读取key
        key = os.getenv("QWEATHER_KEY", "")
        _crawler = SyncWeatherCrawler(key)
    return _crawler
