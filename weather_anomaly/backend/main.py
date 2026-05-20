"""
天气异常检测系统 - FastAPI 服务
"""
import os
import sys
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.database import get_database, save_city, save_daily_weather, get_all_cities, init_database
from backend.crawler import get_weather_crawler, CITY_LIST
from backend.model import get_anomaly_detector, WeatherAnomalyDetector
from backend.feishu_pusher import get_feishu_pusher

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# 全局数据缓存
_dashboard_data = None


async def startup_event():
    """启动时初始化"""
    global _dashboard_data
    logger.info("系统启动，初始化数据...")

    # 初始化城市数据到数据库
    db = get_database()
    for city in CITY_LIST:
        try:
            save_city(city)
        except Exception as e:
            pass

    # 爬取当日天气
    try:
        crawler = get_weather_crawler()
        today_data = crawler.crawl_all()
        logger.info(f"爬取到 {len(today_data)} 条天气数据")

        # 保存到数据库
        for data in today_data:
            save_daily_weather(data)
        logger.info("天气数据已保存")
    except Exception as e:
        logger.warning(f"爬取天气数据失败: {e}")

    # 生成初始dashboard数据
    try:
        detector = get_anomaly_detector()
        _dashboard_data = detector.get_dashboard_data()
        logger.info(f"Dashboard数据生成完成: {len(_dashboard_data.get('cities', []))} 城市, {len(_dashboard_data.get('anomalies', []))} 异常")
    except Exception as e:
        logger.warning(f"生成dashboard数据失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup_event()
    yield


app = FastAPI(
    title="中国异常天气预报系统",
    description="基于历史数据建模的天气异常检测与推送系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/cities")
def get_cities():
    """获取所有城市列表"""
    return get_all_cities()


@app.get("/api/weather/current")
def get_current_weather():
    """获取当前所有城市天气"""
    global _dashboard_data
    if _dashboard_data:
        return _dashboard_data.get("cities", [])
    return []


@app.get("/api/weather/history/{city_code}")
def get_history(city_code: str, days: int = 30):
    """获取城市历史天气"""
    db = get_database()
    return db.get_history(city_code, days)


@app.get("/api/anomalies")
def get_anomalies():
    """获取当前异常城市列表"""
    global _dashboard_data
    if _dashboard_data:
        return _dashboard_data.get("anomalies", [])
    return []


@app.get("/api/dashboard_data")
def get_dashboard():
    """获取前端dashboard所需全部数据"""
    global _dashboard_data
    if _dashboard_data is None:
        detector = get_anomaly_detector()
        _dashboard_data = detector.get_dashboard_data()
    return _dashboard_data


@app.get("/api/refresh")
def refresh_data():
    """刷新数据"""
    global _dashboard_data
    try:
        detector = get_anomaly_detector()
        _dashboard_data = detector.get_dashboard_data()
        return {"status": "ok", "message": "数据已刷新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/push")
async def push_to_feishu():
    """触发飞书推送"""
    global _dashboard_data
    try:
        if _dashboard_data is None:
            detector = get_anomaly_detector()
            _dashboard_data = detector.get_dashboard_data()

        anomalies = _dashboard_data.get("anomalies", [])
        pusher = get_feishu_pusher()

        success = await pusher.push_anomaly_report(anomalies)
        if success:
            return {"status": "ok", "message": "推送成功"}
        else:
            return {"status": "warning", "message": "推送失败（可能配置不完整）"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
def index():
    """返回嵌入数据的HTML页面"""
    global _dashboard_data
    if _dashboard_data is None:
        detector = get_anomaly_detector()
        _dashboard_data = detector.get_dashboard_data()

    import json
    embedded_data = json.dumps(_dashboard_data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>中国异常天气预报系统</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
        .header {{ background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 20px 40px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }}
        .header h1 {{ font-size: 24px; font-weight: 600; background: linear-gradient(90deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .header .time {{ color: #94a3b8; font-size: 14px; }}
        .stats {{ display: flex; gap: 20px; padding: 20px 40px; background: #1e293b; }}
        .stat-card {{ background: #334155; border-radius: 12px; padding: 20px 30px; flex: 1; text-align: center; border: 1px solid #475569; }}
        .stat-card .value {{ font-size: 32px; font-weight: 700; }}
        .stat-card .label {{ color: #94a3b8; font-size: 13px; margin-top: 5px; }}
        .stat-card.alert .value {{ color: #f97316; }}
        .stat-card.severe .value {{ color: #ef4444; }}
        .main {{ display: grid; grid-template-columns: 1fr 400px; gap: 20px; padding: 20px 40px; height: calc(100vh - 200px); }}
        .map-container {{ background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; position: relative; }}
        #map {{ width: 100%; height: 100%; }}
        .sidebar {{ display: flex; flex-direction: column; gap: 15px; overflow-y: auto; }}
        .anomaly-card {{ background: #1e293b; border-radius: 12px; padding: 15px; border: 1px solid #334155; border-left: 4px solid #ef4444; }}
        .anomaly-card.moderate {{ border-left-color: #f97316; }}
        .anomaly-card.mild {{ border-left-color: #f59e0b; }}
        .anomaly-card .city {{ font-size: 16px; font-weight: 600; margin-bottom: 5px; }}
        .anomaly-card .province {{ color: #94a3b8; font-size: 12px; }}
        .anomaly-card .info {{ display: flex; gap: 15px; margin-top: 10px; font-size: 13px; }}
        .anomaly-card .anomaly-type {{ font-weight: 600; }}
        .anomaly-card .anomaly-type.high_temp {{ color: #ef4444; }}
        .anomaly-card .anomaly-type.low_temp {{ color: #38bdf8; }}
        .anomaly-card .anomaly-type.heavy_rain {{ color: #a78bfa; }}
        .anomaly-card .anomaly-type.strong_wind {{ color: #94a3b8; }}
        .anomaly-card .desc {{ color: #94a3b8; font-size: 12px; margin-top: 8px; line-height: 1.4; }}
        .chart-container {{ background: #1e293b; border-radius: 12px; border: 1px solid #334155; padding: 20px; height: 300px; }}
        #trendChart {{ width: 100%; height: 100%; }}
        .footer {{ padding: 15px 40px; background: #1e293b; border-top: 1px solid #334155; text-align: center; color: #64748b; font-size: 12px; }}
        .no-data {{ text-align: center; padding: 40px; color: #64748b; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🌡️ 中国异常天气预报系统</h1>
        <div class="time">最后更新: <span id="lastUpdate">{_dashboard_data.get('last_updated', '')}</span></div>
    </div>

    <div class="stats" id="stats">
        <div class="stat-card">
            <div class="value">{_dashboard_data.get('stats', {}).get('total_cities', 0)}</div>
            <div class="label">监测城市</div>
        </div>
        <div class="stat-card alert">
            <div class="value">{_dashboard_data.get('stats', {}).get('anomaly_count', 0)}</div>
            <div class="label">异常城市</div>
        </div>
        <div class="stat-card severe">
            <div class="value">{_dashboard_data.get('stats', {}).get('severe_count', 0)}</div>
            <div class="label">严重异常</div>
        </div>
        <div class="stat-card">
            <div class="value">{_dashboard_data.get('stats', {}).get('moderate_count', 0)}</div>
            <div class="label">轻度异常</div>
        </div>
    </div>

    <div class="main">
        <div class="map-container">
            <div id="map"></div>
        </div>
        <div class="sidebar" id="sidebar">
            <!-- 异常城市卡片 -->
        </div>
    </div>

    <div class="chart-container" style="margin: 0 40px 20px;">
        <div id="trendChart"></div>
    </div>

    <div class="footer">
        基于历史数据 Z-score 异常检测 | 数据来源：和风天气API + 历史统计建模
    </div>

    <script>
        // 嵌入数据
        window.__EMBEDDED_DATA__ = {embedded_data};

        // 城市坐标映射
        const CITY_COORDS = {{
            "101010100": [116.4, 39.9], "101020100": [121.5, 31.2], "101280100": [113.3, 23.1],
            "101280600": [114.1, 22.5], "101270100": [104.1, 30.7], "101040100": [106.5, 29.5],
            "101200100": [114.3, 30.6], "101110100": [108.9, 34.3], "101180100": [113.6, 34.8],
            "101250100": [113.0, 28.2], "101190100": [118.8, 32.1], "101210100": [120.2, 30.3],
            "101070100": [123.4, 41.8], "101050100": [126.5, 45.8], "101060100": [125.3, 43.9],
            "101260101": [106.7, 26.6], "101160100": [102.7, 25.0], "101300100": [108.3, 22.8],
            "101120100": [117.0, 36.7], "101310100": [110.3, 20.0], "101140100": [91.1, 29.7],
            "101130100": [87.6, 43.8], "101230100": [119.3, 26.1], "101240100": [115.9, 28.7],
            "101020500": [121.5, 31.2], "101050300": [121.6, 38.9], "101190400": [120.6, 31.3],
            "101230500": [118.1, 24.5], "101270400": [104.7, 31.5], "101200200": [111.3, 30.7],
            "101280900": [113.6, 22.3], "101280800": [113.1, 23.0], "101090500": [115.5, 38.9],
            "101100100": [112.5, 37.9], "101080100": [103.8, 36.1], "101300400": [110.3, 25.3],
            "101240400": [114.9, 25.8], "101260500": [106.9, 27.7], "101230300": [118.7, 24.9],
            "101180200": [114.4, 34.8], "101280300": [116.7, 23.4], "101270300": [106.1, 30.8],
            "101110600": [109.5, 36.6], "101190300": [119.4, 32.4], "101190500": [117.2, 34.2],
            "101060300": [126.5, 43.8], "101090200": [118.2, 39.6], "101090300": [119.6, 39.9],
            "101080300": [105.7, 34.6], "101170100": [111.7, 40.8], "101120200": [120.4, 36.1],
            "101150100": [101.8, 36.6], "101270600": [104.6, 28.8], "101210400": [120.7, 28.0],
            "101160200": [100.2, 25.6], "101150200": [94.9, 36.4], "101130300": [89.2, 42.9],
            "101100200": [113.3, 40.1], "101070300": [123.0, 41.1], "101040300": [107.4, 29.7],
            "101140200": [88.9, 29.3], "101021700": [121.4, 31.6], "101180400": [113.9, 35.3],
            "101180800": [114.1, 32.1], "101180600": [112.5, 34.6], "101210300": [121.6, 29.9],
            "101190200": [119.5, 32.2]
        }};

        const ANOMALY_COLORS = {{
            "high_temp": "#ef4444",
            "low_temp": "#38bdf8",
            "heavy_rain": "#a78bfa",
            "strong_wind": "#94a3b8",
            "normal": "#10b981"
        }};

        async function init() {{
            let data;
            if (window.__EMBEDDED_DATA__) {{
                data = window.__EMBEDDED_DATA__;
            }} else {{
                const resp = await fetch('/api/dashboard_data');
                data = await resp.json();
            }}

            renderStats(data.stats);
            renderMap(data.cities, data.anomalies);
            renderSidebar(data.anomalies);
            initTrendChart();
        }}

        function renderStats(stats) {{
            document.getElementById('stats').innerHTML = `
                <div class="stat-card">
                    <div class="value">${{stats.total_cities}}</div>
                    <div class="label">监测城市</div>
                </div>
                <div class="stat-card alert">
                    <div class="value">${{stats.anomaly_count}}</div>
                    <div class="label">异常城市</div>
                </div>
                <div class="stat-card severe">
                    <div class="value">${{stats.severe_count}}</div>
                    <div class="label">严重异常</div>
                </div>
                <div class="stat-card">
                    <div class="value">${{stats.moderate_count}}</div>
                    <div class="label">轻度异常</div>
                </div>
            `;
        }}

        let mapChart, trendChart;
        async function renderMap(cities, anomalies) {{
            mapChart = echarts.init(document.getElementById('map'));

            // 加载中国地图
            try {{
                const response = await fetch('https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json');
                const chinaJson = await response.json();
                echarts.registerMap('china', chinaJson);
            }} catch (e) {{
                console.warn('地图JSON加载失败，使用备用方案');
            }}

            // 散点数据
            const scatterData = cities.map(c => {{
                const coords = CITY_COORDS[c.city_code] || [c.lon, c.lat];
                const color = c.anomaly ? ANOMALY_COLORS[c.anomaly] : ANOMALY_COLORS.normal;
                return {{
                    name: c.city_name,
                    value: [...coords, c.temp, c.anomaly_name || '', c.severity || 0],
                    itemStyle: {{ color, symbolSize: c.anomaly ? 16 : 10 }}
                }};
            }}).filter(d => d.value[0] && d.value[1]);

            const option = {{
                backgroundColor: 'transparent',
                geo: {{
                    map: 'china',
                    roam: true,
                    zoom: 1.2,
                    center: [105, 36],
                    label: {{ show: false }},
                    itemStyle: {{
                        areaColor: '#1e293b',
                        borderColor: '#334155',
                        borderWidth: 1
                    }},
                    emphasis: {{
                        itemStyle: {{ areaColor: '#334155' }},
                        label: {{ show: true, color: '#e2e8f0' }}
                    }}
                }},
                tooltip: {{
                    trigger: 'item',
                    backgroundColor: '#1e293b',
                    borderColor: '#475569',
                    textStyle: {{ color: '#e2e8f0' }},
                    formatter: function(params) {{
                        if (params.data) {{
                            const [lon, lat, temp, anomaly, severity] = params.data.value;
                            let html = `<strong>${{params.data.name}}</strong><br/>`;
                            if (temp) html += `温度: ${{temp}}°C<br/>`;
                            if (anomaly) html += `<span style="color:${{ANOMALY_COLORS[params.data.itemStyle.color] || '#f59e0b'}}">${{anomaly}}</span>`;
                            return html;
                        }}
                        return params.name;
                    }}
                }},
                series: [{{
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: scatterData,
                    symbolSize: function(data) {{ return data[4] > 0 ? 14 : 8; }},
                    itemStyle: {{ opacity: 0.9 }},
                    label: {{
                        show: false,
                        position: 'right',
                        formatter: '{{brand}}',
                        fontSize: 10,
                        color: '#e2e8f0'
                    }}
                }}]
            }};

            mapChart.setOption(option);
            window.addEventListener('resize', () => mapChart.resize());
        }}

        function renderSidebar(anomalies) {{
            const sidebar = document.getElementById('sidebar');
            if (!anomalies || anomalies.length === 0) {{
                sidebar.innerHTML = '<div class="no-data">🎉 暂无异常天气</div>';
                return;
            }}

            sidebar.innerHTML = anomalies.map(a => {{
                const severityClass = a.severity >= 2 ? '' : (a.severity === 1 ? 'moderate' : 'mild');
                return `
                    <div class="anomaly-card ${{severityClass}}" onclick="showTrend('${{a.city_code}}')">
                        <div class="city">{{a.anomaly_icon || ''}} ${{a.city_name}}</div>
                        <div class="province">${{a.province}}</div>
                        <div class="info">
                            <span class="anomaly-type ${{a.anomaly_type}}">${{a.anomaly_name}}</span>
                            <span>当前 ${{a.current_value}}</span>
                            <span>Z=${{a.z_score}}</span>
                        </div>
                        <div class="desc">${{a.description}}</div>
                    </div>
                `;
            }}).join('');
        }}

        function initTrendChart() {{
            trendChart = echarts.init(document.getElementById('trendChart'));
            trendChart.setOption({{
                title: {{ text: '点击城市查看温度趋势', textStyle: {{ color: '#64748b', fontSize: 14 }} }},
                xAxis: {{ type: 'category', data: [], axisLine: {{ lineStyle: {{ color: '#475569' }} }}, axisLabel: {{ color: '#94a3b8' }} }},
                yAxis: {{ type: 'value', axisLine: {{ lineStyle: {{ color: '#475569' }} }}, axisLabel: {{ color: '#94a3b8' }}, splitLine: {{ lineStyle: {{ color: '#334155' }} }} }},
                series: []
            }});
        }}

        async function showTrend(cityCode) {{
            const resp = await fetch(`/api/weather/history/${{cityCode}}?days=30`);
            const history = await resp.json();

            if (!history || history.length === 0) {{
                trendChart.setOption({{
                    title: {{ text: '暂无历史数据', textStyle: {{ color: '#64748b', fontSize: 14 }} }}
                }});
                return;
            }}

            const sorted = history.sort((a, b) => a.date.localeCompare(b.date));
            const dates = sorted.map(h => h.date.slice(5));
            const temps = sorted.map(h => h.temp_avg);
            const mean = temps.reduce((a, b) => a + b, 0) / temps.length;
            const std = Math.sqrt(temps.reduce((a, b) => a + (b - mean) ** 2, 0) / temps.length);

            trendChart.setOption({{
                title: {{ text: `温度趋势 (近30天)`, textStyle: {{ color: '#e2e8f0', fontSize: 14 }}}},
                xAxis: {{ type: 'category', data: dates, axisLine: {{ lineStyle: {{ color: '#475569' }} }}, axisLabel: {{ color: '#94a3b8', rotate: 45 }}}},
                yAxis: {{ type: 'value', name: '°C', axisLine: {{ lineStyle: {{ color: '#475569' }} }}, axisLabel: {{ color: '#94a3b8' }}, splitLine: {{ lineStyle: {{ color: '#334155' }} }}},
                series: [
                    {{ name: '温度', type: 'line', data: temps, smooth: true, lineStyle: {{ color: '#38bdf8' }}, areaStyle: {{ color: 'rgba(56,189,248,0.1)' }} }},
                    {{ name: '均值', type: 'line', data: Array(dates.length).fill(mean), lineStyle: {{ color: '#10b981', type: 'dashed' }} }},
                    {{ name: '+2σ', type: 'line', data: Array(dates.length).fill(mean + 2*std), lineStyle: {{ color: '#f59e0b', type: 'dotted' }} }},
                    {{ name: '-2σ', type: 'line', data: Array(dates.length).fill(mean - 2*std), lineStyle: {{ color: '#f59e0b', type: 'dotted' }} }}
                ]
            }});
        }}

        init();
    </script>
</body>
</html>"""
    return html


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8898"))
    uvicorn.run(app, host="0.0.0.0", port=port)
