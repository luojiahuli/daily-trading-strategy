"""
天气异常检测模型 - 基于历史统计的Z-score检测
"""
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from .database import get_database

logger = logging.getLogger(__name__)

# 异常类型
ANOMALY_TYPES = {
    "high_temp": {"name": "高温", "icon": "🔥", "severity_colors": ["#fcd34d", "#f97316", "#ef4444"]},
    "low_temp": {"name": "低温", "icon": "❄️", "severity_colors": ["#67e8f9", "#38bdf8", "#0ea5e9"]},
    "heavy_rain": {"name": "暴雨", "icon": "🌧️", "severity_colors": ["#c4b5fd", "#a78bfa", "#8b5cf6"]},
    "strong_wind": {"name": "大风", "icon": "💨", "severity_colors": ["#d1d5db", "#9ca3af", "#6b7280"]},
}


def calculate_zscore(current: float, mean: float, std: float) -> float:
    """计算Z-score"""
    if std == 0:
        return 0
    return (current - mean) / std


def get_severity(z_score: float) -> int:
    """根据Z-score确定严重程度 0-3"""
    z = abs(z_score)
    if z < 1.5:
        return 0
    elif z < 2.0:
        return 1
    elif z < 2.5:
        return 2
    else:
        return 3


def generate_anomaly_description(city: dict, anomaly_type: str, current: float, mean: float, z_score: float) -> str:
    """生成异常描述"""
    deviation = round(current - mean, 1)
    if anomaly_type == "high_temp":
        if z_score > 2.5:
            return f"气温异常偏高，当前{current}°C，比历史同期均值高出{deviation}°C（Z={z_score:.1f}），需警惕热射病"
        else:
            return f"气温偏高，当前{current}°C，比历史同期均值高出{deviation}°C（Z={z_score:.1f}）"
    elif anomaly_type == "low_temp":
        if z_score > 2.5:
            return f"气温异常偏低，当前{current}°C，比历史同期均值低{abs(deviation)}°C（Z={z_score:.1f}），需注意防寒"
        else:
            return f"气温偏低，当前{current}°C，比历史同期均值低{abs(deviation)}°C（Z={z_score:.1f}）"
    elif anomaly_type == "heavy_rain":
        return f"降水量异常偏多，当前{current}mm，比历史同期均值多{deviation}mm（Z={z_score:.1f}），谨防洪涝"
    elif anomaly_type == "strong_wind":
        return f"风速异常偏大，当前{current}m/s，比历史同期均值高{deviation}m/s（Z={z_score:.1f}）"
    return ""


class WeatherAnomalyDetector:
    """天气异常检测器"""

    def __init__(self):
        self.db = get_database()

    def get_city_baseline(self, city_code: str) -> dict:
        """获取城市历史基线数据"""
        # 获取最近365天的历史数据
        history = self.db.get_history(city_code, days=365)

        if len(history) < 7:
            # 数据不足，返回默认基线
            return {
                "temp_mean": 20,
                "temp_std": 8,
                "precip_mean": 2,
                "precip_std": 5,
                "wind_mean": 3,
                "wind_std": 2,
            }

        temps = [h["temp_avg"] for h in history if h.get("temp_avg")]
        precips = [h["precipitation"] for h in history if h.get("precipitation") is not None]
        winds = [h["wind_speed"] for h in history if h.get("wind_speed") is not None]

        return {
            "temp_mean": np.mean(temps) if temps else 20,
            "temp_std": np.std(temps) if temps else 8,
            "precip_mean": np.mean(precips) if precips else 2,
            "precip_std": np.std(precips) if precips else 5,
            "wind_mean": np.mean(winds) if winds else 3,
            "wind_std": np.std(winds) if winds else 2,
        }

    def detect_city_anomaly(self, city: dict) -> Optional[dict]:
        """检测单个城市的异常"""
        city_code = city["city_code"]

        # 获取基线
        baseline = self.get_city_baseline(city_code)

        anomalies = []

        # 检测温度异常
        if city.get("temp_avg") is not None:
            z_temp = calculate_zscore(
                city["temp_avg"],
                baseline["temp_mean"],
                baseline["temp_std"]
            )
            if abs(z_temp) >= 1.5:
                if z_temp > 0:
                    anomaly_type = "high_temp"
                else:
                    anomaly_type = "low_temp"
                severity = get_severity(z_temp)
                anomalies.append({
                    "city_code": city_code,
                    "city_name": city["city_name"],
                    "province": city.get("province", ""),
                    "anomaly_type": anomaly_type,
                    "anomaly_name": ANOMALY_TYPES[anomaly_type]["name"],
                    "anomaly_icon": ANOMALY_TYPES[anomaly_type]["icon"],
                    "z_score": round(z_temp, 2),
                    "current_value": city["temp_avg"],
                    "historical_mean": round(baseline["temp_mean"], 1),
                    "severity": severity,
                    "description": generate_anomaly_description(
                        city, anomaly_type, city["temp_avg"],
                        baseline["temp_mean"], z_temp
                    ),
                    "lat": city.get("lat", 0),
                    "lon": city.get("lon", 0),
                })

        # 检测降水异常
        if city.get("precipitation") is not None and city["precipitation"] > 0:
            z_precip = calculate_zscore(
                city["precipitation"],
                baseline["precip_mean"],
                baseline["precip_std"]
            )
            if z_precip >= 1.5:
                severity = get_severity(z_precip)
                anomalies.append({
                    "city_code": city_code,
                    "city_name": city["city_name"],
                    "province": city.get("province", ""),
                    "anomaly_type": "heavy_rain",
                    "anomaly_name": ANOMALY_TYPES["heavy_rain"]["name"],
                    "anomaly_icon": ANOMALY_TYPES["heavy_rain"]["icon"],
                    "z_score": round(z_precip, 2),
                    "current_value": city["precipitation"],
                    "historical_mean": round(baseline["precip_mean"], 1),
                    "severity": severity,
                    "description": generate_anomaly_description(
                        city, "heavy_rain", city["precipitation"],
                        baseline["precip_mean"], z_precip
                    ),
                    "lat": city.get("lat", 0),
                    "lon": city.get("lon", 0),
                })

        # 检测风力异常
        if city.get("wind_speed") is not None and city["wind_speed"] > 0:
            z_wind = calculate_zscore(
                city["wind_speed"],
                baseline["wind_mean"],
                baseline["wind_std"]
            )
            if z_wind >= 1.5:
                severity = get_severity(z_wind)
                anomalies.append({
                    "city_code": city_code,
                    "city_name": city["city_name"],
                    "province": city.get("province", ""),
                    "anomaly_type": "strong_wind",
                    "anomaly_name": ANOMALY_TYPES["strong_wind"]["name"],
                    "anomaly_icon": ANOMALY_TYPES["strong_wind"]["icon"],
                    "z_score": round(z_wind, 2),
                    "current_value": city["wind_speed"],
                    "historical_mean": round(baseline["wind_mean"], 1),
                    "severity": severity,
                    "description": generate_anomaly_description(
                        city, "strong_wind", city["wind_speed"],
                        baseline["wind_mean"], z_wind
                    ),
                    "lat": city.get("lat", 0),
                    "lon": city.get("lon", 0),
                })

        # 返回最严重的异常
        if anomalies:
            return max(anomalies, key=lambda x: x["severity"])
        return None

    def detect_anomalies(self, cities: list = None) -> list:
        """检测所有城市的异常"""
        if cities is None:
            # 获取最新天气数据
            latest = self.db.get_latest_weather()
            if not latest:
                # 如果数据库为空，生成模拟数据
                from .crawler import get_weather_crawler
                crawler = get_weather_crawler()
                latest = crawler.crawl_all()
            cities = latest

        anomalies = []
        for city in cities:
            anomaly = self.detect_city_anomaly(city)
            if anomaly:
                anomalies.append(anomaly)

        # 按严重程度排序
        anomalies.sort(key=lambda x: -x["severity"])
        return anomalies

    def get_dashboard_data(self) -> dict:
        """获取前端dashboard所需的全部数据"""
        from .crawler import get_weather_crawler, CITY_LIST

        # 获取最新天气或模拟数据
        latest = self.db.get_latest_weather()

        if not latest:
            crawler = get_weather_crawler()
            latest = crawler.crawl_all()

        # 检测异常
        anomalies = self.detect_anomalies(latest)

        # 构建城市数据（包含异常状态）
        cities_data = []
        anomaly_codes = {a["city_code"]: a for a in anomalies}

        for city in latest:
            city_code = city.get("city_code", "")
            anomaly = anomaly_codes.get(city_code)
            cities_data.append({
                "city_code": city_code,
                "city_name": city.get("city_name", ""),
                "province": city.get("province", ""),
                "lat": city.get("lat", 0),
                "lon": city.get("lon", 0),
                "temp": city.get("temp_avg"),
                "temp_max": city.get("temp_max"),
                "temp_min": city.get("temp_min"),
                "humidity": city.get("humidity"),
                "wind_speed": city.get("wind_speed"),
                "weather_text": city.get("weather_text", ""),
                "precipitation": city.get("precipitation", 0),
                "anomaly": anomaly["anomaly_type"] if anomaly else None,
                "anomaly_name": anomaly["anomaly_name"] if anomaly else None,
                "severity": anomaly["severity"] if anomaly else 0,
                "z_score": anomaly["z_score"] if anomaly else 0,
            })

        # 如果数据库为空但需要更多城市，添加不在数据库中的城市
        existing_codes = {c["city_code"] for c in cities_data}
        for city_meta in CITY_LIST:
            if city_meta["city_code"] not in existing_codes:
                cities_data.append({
                    "city_code": city_meta["city_code"],
                    "city_name": city_meta["city_name"],
                    "province": city_meta["province"],
                    "lat": city_meta["lat"],
                    "lon": city_meta["lon"],
                    "temp": None,
                    "anomaly": None,
                    "severity": 0,
                })

        # 统计
        total_cities = len(cities_data)
        anomaly_count = len(anomalies)
        severe_count = sum(1 for a in anomalies if a["severity"] >= 2)
        moderate_count = sum(1 for a in anomalies if a["severity"] == 1)

        # 按类型统计
        type_counts = {}
        for a in anomalies:
            t = a["anomaly_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "cities": cities_data,
            "anomalies": anomalies,
            "stats": {
                "total_cities": total_cities,
                "anomaly_count": anomaly_count,
                "severe_count": severe_count,
                "moderate_count": moderate_count,
                "type_counts": type_counts,
            },
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


_detector: Optional[WeatherAnomalyDetector] = None


def get_anomaly_detector() -> WeatherAnomalyDetector:
    global _detector
    if _detector is None:
        _detector = WeatherAnomalyDetector()
    return _detector
