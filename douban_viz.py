#!/usr/bin/env python3
"""
豆瓣榜单可视化 - 电影/图书/音乐 Top250
爬取数据 → 生成HTML可视化 → Playwright截图 → 推送飞书
"""
import requests
import re
import os
import sys
import json
from datetime import datetime
from pathlib import Path

# 尝试导入playwright
try:
    from playwright.sync_api import sync_playwright
    HAS_PW = True
except ImportError:
    HAS_PW = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.douban.com/",
}

OUTPUT_DIR = Path(__file__).parent / "douban_reports"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============ 爬虫 ============

def crawl_douban_movies() -> list:
    """爬取豆瓣电影Top250"""
    url = "https://movie.douban.com/top250"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    items = re.findall(r'<div class="item">(.*?)</div>\s*</div>', resp.text, re.DOTALL)
    
    movies = []
    for item in items:
        titles = re.findall(r'<span class="title">([^<]+)</span>', item)
        title = titles[0].replace("&nbsp;", " ").strip() if titles else ""
        rates = re.findall(r'class="rating_num"[^>]*>(\d+\.\d+)</span>', item)
        rate = float(rates[0]) if rates else 0
        people = re.findall(r'(\d+)人评价', item)
        people_count = int(people[0].replace(",","")) if people else 0
        inq = re.findall(r'<span class="inq">([^<]+)</span>', item)
        quote = inq[0] if inq else ""
        movies.append({
            "title": title, "rate": rate, "people": people_count,
            "quote": quote, "category": "电影"
        })
    return movies

def crawl_douban_books() -> list:
    """爬取豆瓣图书Top250"""
    url = "https://book.douban.com/top250"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    items = re.findall(r'<tr class="item">(.*?)</tr>', resp.text, re.DOTALL)
    
    books = []
    for item in items:
        titles = re.findall(r'<div class="pl2">\s*<a[^>]*title="([^"]+)"', item)
        title = titles[0].strip() if titles else ""
        rates = re.findall(r'<span class="rating_nums">(\d+\.\d+)</span>', item)
        rate = float(rates[0]) if rates else 0
        people = re.findall(r'(\d+)人评价', item)
        people_count = int(people[0].replace(",","")) if people else 0
        books.append({
            "title": title, "rate": rate, "people": people_count,
            "quote": "", "category": "图书"
        })
    return books

def crawl_douban_music() -> list:
    """爬取豆瓣音乐Top250"""
    url = "https://music.douban.com/top250"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    # 音乐用table结构: class="item" id="xxx"
    items = re.findall(r'class="item" id="\d+">(.*?)</tr>', resp.text, re.DOTALL)
    
    music = []
    for item in items:
        # 标题 - pl2里的a标签内容
        titles = re.findall(r'<div class="pl2">\s*<a[^>]*>\s*([^<\n]+)', item)
        title = titles[0].strip() if titles else ""
        # 评分
        rates = re.findall(r'<span class="rating_nums">(\d+\.\d+)</span>', item)
        rate = float(rates[0]) if rates else 0
        # 评价人数
        people = re.findall(r'(\d+)人评价', item)
        people_count = int(people[0].replace(",","")) if people else 0
        music.append({
            "title": title, "rate": rate, "people": people_count,
            "quote": "", "category": "音乐"
        })
    return music

# ============ HTML生成 ============

def build_html(movies: list, books: list, music: list) -> str:
    """生成可视化HTML报告"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 各品类统计
    m_avg = sum(m["rate"] for m in movies) / len(movies) if movies else 0
    b_avg = sum(b["rate"] for b in books) / len(books) if books else 0
    u_avg = sum(u["rate"] for u in music) / len(music) if music else 0
    
    # Top 10 电影 (按评分)
    top_movies = sorted(movies, key=lambda x: x["rate"], reverse=True)[:10]
    # Top 10 图书
    top_books = sorted(books, key=lambda x: x["rate"], reverse=True)[:10]
    # Top 10 音乐
    top_music = sorted(music, key=lambda x: x["rate"], reverse=True)[:10]
    
    # 高分电影分布 (9.5+, 9.0-9.5, 8.5-9.0, <8.5)
    def categorize(items):
        return {
            "9.5+": len([x for x in items if x["rate"] >= 9.5]),
            "9.0-9.5": len([x for x in items if 9.0 <= x["rate"] < 9.5]),
            "8.5-9.0": len([x for x in items if 8.5 <= x["rate"] < 9.0]),
            "8.0-8.5": len([x for x in items if x["rate"] < 8.5]),
        }
    
    movie_dist = categorize(movies)
    book_dist = categorize(books)
    
    def make_table(items, color):
        rows = ""
        for i, item in enumerate(items, 1):
            bar_width = int(item["rate"] / 10 * 100)
            rows += f"""
            <tr>
                <td class="rank">{i}</td>
                <td class="title">{item['title'][:20]}</td>
                <td class="score">{item['rate']}</td>
                <td class="bar-cell">
                    <div class="bar" style="width:{bar_width}%;background:{color}"></div>
                </td>
                <td class="people">{item['people']:,}</td>
            </tr>"""
        return rows

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>豆瓣榜单日报</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1419; color: #e7e9ea; min-height: 100vh; }
        
        .header { background: linear-gradient(135deg, #1a1f29, #2d3748); padding: 28px 36px; border-bottom: 1px solid #2d3748; }
        .header h1 { font-size: 26px; font-weight: 700; background: linear-gradient(90deg, #ff6b6b, #ffa502, #ffd93d); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header .time { color: #718096; font-size: 13px; margin-top: 6px; }
        
        .stats-bar { display: flex; gap: 20px; padding: 20px 36px; background: #1a1f29; border-bottom: 1px solid #2d3748; flex-wrap: wrap; }
        .stat-card { background: #252d3a; border-radius: 12px; padding: 16px 24px; border: 1px solid #2d3748; flex: 1; min-width: 200px; }
        .stat-card .label { font-size: 12px; color: #718096; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
        .stat-card .value { font-size: 28px; font-weight: 700; }
        .stat-card .sub { font-size: 11px; color: #718096; margin-top: 4px; }
        
        .stat-card.movies { border-left: 4px solid #ff6b6b; }
        .stat-card.movies .value { color: #ff6b6b; }
        .stat-card.books { border-left: 4px solid #4ecdc4; }
        .stat-card.books .value { color: #4ecdc4; }
        .stat-card.music { border-left: 4px solid #ffd93d; }
        .stat-card.music .value { color: #ffd93d; }
        
        .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 20px 36px; }
        .chart-card { background: #1a1f29; border-radius: 12px; border: 1px solid #2d3748; padding: 20px; }
        .chart-title { font-size: 14px; font-weight: 600; color: #a0aec0; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.05em; }
        
        .tables { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; padding: 0 36px 24px; }
        .table-card { background: #1a1f29; border-radius: 12px; border: 1px solid #2d3748; overflow: hidden; }
        .table-header { padding: 16px 20px; border-bottom: 1px solid #2d3748; display: flex; justify-content: space-between; align-items: center; }
        .table-header h3 { font-size: 15px; font-weight: 600; }
        .table-header .badge { background: #2d3748; border-radius: 20px; padding: 3px 10px; font-size: 11px; color: #a0aec0; }
        
        table { width: 100%; border-collapse: collapse; }
        th { padding: 10px 12px; text-align: left; font-size: 11px; color: #718096; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #2d3748; }
        td { padding: 10px 12px; font-size: 13px; border-bottom: 1px solid #1e2530; }
        tr:last-child td { border-bottom: none; }
        
        td.rank { font-weight: 700; color: #718096; width: 30px; }
        td.title { font-weight: 500; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        td.score { font-weight: 700; color: #ffd93d; width: 50px; }
        td.people { color: #718096; font-size: 12px; width: 80px; }
        td.bar-cell { width: 100px; }
        
        .bar { height: 6px; border-radius: 3px; background: #4ecdc4; }
        
        .footer { padding: 16px 36px; background: #1a1f29; border-top: 1px solid #2d3748; text-align: center; color: #4a5568; font-size: 12px; }
        
        @media (max-width: 1200px) { 
            .tables { grid-template-columns: 1fr 1fr; }
            .charts { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎬 豆瓣榜单日报</h1>
        <div class="time">""" + ts + """ · 数据来源: 豆瓣</div>
    </div>
    
    <div class="stats-bar">
        <div class="stat-card movies">
            <div class="label">电影 Top250</div>
            <div class="value">""" + str(len(movies)) + """</div>
            <div class="sub">均分 """ + f"{m_avg:.2f}" + """ · 高分佳作 """ + str(movie_dist['9.5+']+movie_dist['9.0-9.5']) + """ 部</div>
        </div>
        <div class="stat-card books">
            <div class="label">图书 Top250</div>
            <div class="value">""" + str(len(books)) + """</div>
            <div class="sub">均分 """ + f"{b_avg:.2f}" + """ · 高分佳作 """ + str(book_dist['9.5+']+book_dist['9.0-9.5']) + """ 本</div>
        </div>
        <div class="stat-card music">
            <div class="label">音乐 Top250</div>
            <div class="value">""" + str(len(music)) + """</div>
            <div class="sub">均分 """ + f"{u_avg:.2f}" + """</div>
        </div>
    </div>
    
    <div class="charts">
        <div class="chart-card">
            <div class="chart-title">📊 评分分布对比</div>
            <canvas id="distChart" height="200"></canvas>
        </div>
        <div class="chart-card">
            <div class="chart-title">🔥 热门程度 (评价人数)</div>
            <canvas id="heatChart" height="200"></canvas>
        </div>
    </div>
    
    <div class="tables">
        <div class="table-card">
            <div class="table-header">
                <h3>🎬 电影 Top10</h3>
                <span class="badge">豆瓣电影</span>
            </div>
            <table>
                <thead><tr><th>#</th><th>名称</th><th>评分</th><th>热度</th><th>评价</th></tr></thead>
                <tbody>""" + make_table(top_movies, '#ff6b6b') + """</tbody>
            </table>
        </div>
        <div class="table-card">
            <div class="table-header">
                <h3>📚 图书 Top10</h3>
                <span class="badge">豆瓣读书</span>
            </div>
            <table>
                <thead><tr><th>#</th><th>名称</th><th>评分</th><th>热度</th><th>评价</th></tr></thead>
                <tbody>""" + make_table(top_books, '#4ecdc4') + """</tbody>
            </table>
        </div>
        <div class="table-card">
            <div class="table-header">
                <h3>🎵 音乐 Top10</h3>
                <span class="badge">豆瓣音乐</span>
            </div>
            <table>
                <thead><tr><th>#</th><th>名称</th><th>评分</th><th>热度</th><th>评价</th></tr></thead>
                <tbody>""" + make_table(top_music, '#ffd93d') + """</tbody>
            </table>
        </div>
    </div>
    
    <div class="footer">
        豆瓣榜单日报 · 自动爬取 · """ + ts + """
    </div>
    
    <script>
    // 评分分布对比
    new Chart(document.getElementById('distChart'), {
        type: 'bar',
        data: {
            labels: ['9.5+', '9.0-9.5', '8.5-9.0', '<8.5'],
            datasets: [
                { label: '电影', data: [""" + str(movie_dist['9.5+']) + """, """ + str(movie_dist['9.0-9.5']) + """, """ + str(movie_dist['8.5-9.0']) + """, """ + str(movie_dist['8.0-8.5']) + """], backgroundColor: '#ff6b6b' },
                { label: '图书', data: [""" + str(book_dist['9.5+']) + """, """ + str(book_dist['9.0-9.5']) + """, """ + str(book_dist['8.5-9.0']) + """, """ + str(book_dist['8.0-8.5']) + """], backgroundColor: '#4ecdc4' },
            ]
        },
        options: { responsive: true, plugins: { legend: { labels: { color: '#a0aec0' } } }, scales: { x: { ticks: { color: '#718096' }, grid: { color: '#2d3748' } }, y: { ticks: { color: '#718096' }, grid: { color: '#2d3748' } } } }
    });
    
    // 热门程度 (Top 5 movies by people)
    new Chart(document.getElementById('heatChart'), {
        type: 'bar',
        data: {
            labels: """ + json.dumps([m['title'][:15] for m in top_movies[:5]]) + """,
            datasets: [{
                label: '评价人数',
                data: """ + json.dumps([m['people'] for m in top_movies[:5]]) + """,
                backgroundColor: ['#ff6b6b', '#ffa502', '#ffd93d', '#4ecdc4', '#45aaf2']
            }]
        },
        options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#718096' }, grid: { color: '#2d3748' } }, y: { ticks: { color: '#e7e9ea' }, grid: { display: false } } } }
    });
    </script>
</body>
</html>"""
    return html


def save_html(html: str) -> Path:
    """保存HTML文件"""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUTPUT_DIR / f"douban_{ts}.html"
    path.write_text(html, encoding="utf-8")
    return path


def screenshot(html_path: Path) -> Path:
    """使用Playwright截图"""
    if not HAS_PW:
        print("Playwright未安装，跳过截图")
        return None
    
    png_path = html_path.with_suffix(".png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(f"file://{html_path}")
        page.wait_for_timeout(2000)  # 等待图表渲染
        page.screenshot(path=str(png_path), full_page=False)
        browser.close()
    return png_path


# ============ 主流程 ============

def main():
    print("=" * 50)
    print("🎬 豆瓣榜单日报")
    print("=" * 50)
    
    print("\n[1/4] 爬取电影 Top250...")
    movies = crawl_douban_movies()
    print(f"  获取 {len(movies)} 部电影")
    
    print("\n[2/4] 爬取图书 Top250...")
    books = crawl_douban_books()
    print(f"  获取 {len(books)} 本书")
    
    print("\n[3/4] 爬取音乐 Top250...")
    music = crawl_douban_music()
    print(f"  获取 {len(music)} 张专辑")
    
    print("\n[4/4] 生成可视化报告...")
    html = build_html(movies, books, music)
    html_path = save_html(html)
    print(f"  HTML: {html_path}")
    
    png_path = screenshot(html_path)
    if png_path:
        print(f"  截图: {png_path}")
    
    print("\n" + "=" * 50)
    print("✅ 完成!")
    print("=" * 50)
    
    return html_path, png_path


if __name__ == "__main__":
    main()
