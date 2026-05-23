#!/usr/bin/env python3
"""
热点话题词云可视化 - 按平台分类聚类
基于matplotlib + wordcloud生成多平台词云对比图
"""
import asyncio
import logging
import os
import sys
import re
import json
from datetime import datetime
from collections import Counter

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from wordcloud import WordCloud
import numpy as np

# 尝试多个中文字体
import matplotlib.font_manager as fm
_font_cache = {}

def get_chinese_font():
    """获取可用的中文字体"""
    if _font_cache:
        return _font_cache
    
    candidates = [
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
    ]
    
    for path in candidates:
        if os.path.exists(path):
            prop = fm.FontProperties(fname=path)
            _font_cache['matplotlib'] = prop
            _font_cache['wordcloud'] = path
            return _font_cache
    
    # 回退到系统默认
    return None

# 预加载字体
get_chinese_font()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.collectors.weibo_collector import WeiboCollectorAgent
from agents.collectors.zhihu_collector import ZhihuCollectorAgent
from agents.collectors.xiaohongshu_collector import XiaohongshuCollectorAgent
from agents.collectors.maimai_collector import MaimaiCollectorAgent
from agents.collectors.douban_collector import DoubanCollectorAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# 平台配色
PLATFORM_COLORS = {
    'weibo': '#ff6b6b',
    'zhihu': '#4ecdc4',
    'xiaohongshu': '#ff6b9d',
    'maimai': '#ffd93d',
    'douban': '#5ac8c8',
}
PLATFORM_NAMES = {
    'weibo': '微博',
    'zhihu': '知乎',
    'xiaohongshu': '小红书',
    'maimai': '脉脉',
    'douban': '豆瓣',
}

# 停用词
STOPWORDS = set([
    '的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你',
    '会', '着', '没有', '看', '好', '自己', '这', '什么', '她', '他', '它', '们', '那', '能', '这个', '那个', '怎么', '为什么',
    '可以', '已经', '因为', '所以', '但是', '如果', '虽然', '还是', '或者', '以及', '对于', '关于', '通过', '根据', '进行',
    '开始', '之后', '之前', '现在', '今天', '昨天', '今年', '去年', '每年', '每天', '这个时候', '的时候', '之后', '来说',
    '还有', '这些', '那些', '这样', '那样', '如何', '怎么', '为什么', '哪里', '哪个', '哪些', '多少', '几', '第', '最',
    '真的', '其实', '应该', '可能', '应该', '知道', '觉得', '感觉', '发现', '觉得', '认为', '看到', '听到', '拿到',
    '一个', '这个', '那个', '哪些', '几个', '每个', '其他', '另外', '同样', '甚至', '更加', '非常', '特别', '比较',
    '越来越', '越来越', '一点', '一些', '一样', '一起', '一直', '一定', '一起', '一样', '一点', '一定',
])


def extract_keywords(topics: list) -> Counter:
    """从话题中提取关键词"""
    word_freq = Counter()
    for topic in topics:
        title = topic.get('topic_title', '')
        # 清理标题
        title = re.sub(r'[《》【】（）\(\)（）\[\]]', ' ', title)
        title = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s]', ' ', title)
        words = title.split()
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in STOPWORDS:
                word_freq[w] += 1
    return word_freq


def make_wordcloud(text: str, color: str, size=(400, 300)) -> np.ndarray:
    """生成单个词云图片"""
    if not text.strip():
        return np.zeros((size[1], size[0], 3), dtype=np.uint8)
    
    # 创建mask - 圆形风格
    x, y = np.ogrid[:size[1], :size[0]]
    center_x, center_y = size[0] // 2, size[1] // 2
    mask = ((x - center_y) ** 2 + (y - center_x) ** 2) <= (min(size) // 2) ** 2
    mask = 255 * mask.astype(np.uint8)
    
    def color_func(word, font_size, position, orientation, random_state=None, **kwargs):
        # 颜色在主色调附近变化
        import colorsys
        h, s, l = random_state.randint(0, 30) / 100, 0.5, random_state.randint(40, 70) / 100
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'
    
    wc = WordCloud(
        font_path=None,  # 系统字体
        background_color='white',
        mask=mask,
        max_words=60,
        min_font_size=10,
        max_font_size=80,
        width=size[0],
        height=size[1],
        prefer_horizontal=0.7,
        color_func=color_func if False else None,  # 使用默认配色
        relative_scaling=0.5,
    )
    wc.generate(text)
    
    # 重新着色
    img = wc.to_array()
    return img


def plot_platform_wordclouds(all_topics_by_platform: dict, output_path: str):
    """绘制多平台词云对比图"""
    platforms = list(all_topics_by_platform.keys())
    n = len(platforms)
    
    if n == 0:
        logger.warning("无数据，跳过词云生成")
        return
    
    # 根据平台数量决定布局
    if n <= 2:
        cols, rows = n, 1
        fig_size = (14, 5)
    elif n <= 4:
        cols, rows = 2, 2
        fig_size = (14, 10)
    elif n <= 6:
        cols, rows = 3, 2
        fig_size = (18, 10)
    else:
        cols, rows = 3, 3
        fig_size = (18, 15)
    
    # 设置字体
    font_info = get_chinese_font()
    font_prop = font_info['matplotlib'] if font_info and 'matplotlib' in font_info else None

    fig, axes = plt.subplots(rows, cols, figsize=fig_size)
    axes = axes.flatten() if n > 1 else [axes]

    # 标题
    fig.suptitle('各平台热点话题词云', fontsize=18, fontweight='bold', y=0.98, fontproperties=font_prop)
    
    for i, (platform, topics) in enumerate(all_topics_by_platform.items()):
        ax = axes[i]
        ax.set_axis_off()
        
        pname = PLATFORM_NAMES.get(platform, platform)
        pcolor = PLATFORM_COLORS.get(platform, '#888888')
        
        # 提取关键词
        word_freq = extract_keywords(topics)
        if not word_freq:
            ax.text(0.5, 0.5, f'{pname}\n(无数据)', ha='center', va='center', fontsize=14, fontproperties=font_prop)
            ax.set_title(f'{pname}', fontsize=14, color=pcolor, fontweight='bold', pad=10, fontproperties=font_prop)
            continue
        
        # 生成词云
        text = ' '.join(f'{w}' for w, c in word_freq.most_common(80))
        
        # 创建词云
        mask = np.zeros((300, 400), dtype=np.uint8)
        x, y = np.ogrid[:300, :400]
        cx, cy = 200, 150
        r = 140
        mask[((x - cx) ** 2 + (y - cy) ** 2) <= r ** 2] = 255
        
        font_info = get_chinese_font()
        font_path = font_info['wordcloud'] if font_info else None

        wc = WordCloud(
            font_path=font_path,
            background_color='white',
            mask=mask,
            max_words=50,
            min_font_size=12,
            max_font_size=70,
            prefer_horizontal=0.8,
            colormap='RdYlBu_r' if i % 2 == 0 else 'RdYlGn_r',
        )
        wc.generate(text)
        
        ax.imshow(wc.to_array())
        ax.set_title(f'{pname} · {len(topics)}条话题', fontsize=13, color=pcolor, fontweight='bold', pad=8, fontproperties=font_prop)
        ax.set_axis_off()
    
    # 隐藏多余的子图
    for j in range(n, len(axes)):
        axes[j].set_axis_off()
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    logger.info(f"  词云图已保存: {output_path}")


def plot_keyword_bar_chart(all_topics_by_platform: dict, output_path: str):
    """绘制关键词频次条形图 - 按平台对比"""
    platforms = list(all_topics_by_platform.keys())
    
    # 收集所有平台的top关键词
    all_words = Counter()
    for topics in all_topics_by_platform.values():
        all_words += extract_keywords(topics)
    
    top_words = [w for w, c in all_words.most_common(30)]
    
    fig, ax = plt.subplots(figsize=(14, 8))

    # 设置中文字体
    font_info = get_chinese_font()
    font_prop = font_info['matplotlib'] if font_info and 'matplotlib' in font_info else None

    x = np.arange(len(top_words))
    bar_width = 0.15

    for i, (platform, topics) in enumerate(all_topics_by_platform.items()):
        word_freq = extract_keywords(topics)
        counts = [word_freq.get(w, 0) for w in top_words]
        color = PLATFORM_COLORS.get(platform, '#888')
        ax.barh(x + i * bar_width, counts, bar_width, label=PLATFORM_NAMES.get(platform, platform), color=color, alpha=0.8)

    ax.set_yticks(x + bar_width * (len(platforms) - 1) / 2)
    ax.set_yticklabels(top_words, fontsize=11, fontproperties=font_prop)
    ax.set_xlabel('出现频次', fontsize=12, fontproperties=font_prop)
    ax.set_title('各平台热点关键词对比 TOP30', fontsize=16, fontweight='bold', pad=15, fontproperties=font_prop)
    ax.legend(loc='lower right', fontsize=10, prop=font_prop)
    ax.invert_yaxis()
    
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    logger.info(f"  关键词对比图已保存: {output_path}")


def plot_platform_heatmap(all_topics_by_platform: dict, output_path: str):
    """绘制平台-关键词热力图"""
    platforms = list(all_topics_by_platform.keys())
    
    all_words = Counter()
    for topics in all_topics_by_platform.values():
        all_words += extract_keywords(topics)
    top_words = [w for w, c in all_words.most_common(25)]
    
    # 构建矩阵
    matrix = []
    for platform in platforms:
        word_freq = extract_keywords(all_topics_by_platform[platform])
        row = [word_freq.get(w, 0) for w in top_words]
        matrix.append(row)
    
    matrix = np.array(matrix)
    
    fig, ax = plt.subplots(figsize=(14, 6))

    # 设置中文字体
    font_info = get_chinese_font()
    font_prop = font_info['matplotlib'] if font_info and 'matplotlib' in font_info else None

    im = ax.imshow(matrix, cmap='YlOrRd', aspect='auto')

    ax.set_xticks(np.arange(len(top_words)))
    ax.set_yticks(np.arange(len(platforms)))
    ax.set_xticklabels(top_words, rotation=45, ha='right', fontsize=10)
    ax.set_yticklabels([PLATFORM_NAMES.get(p, p) for p in platforms], fontsize=11, fontproperties=font_prop)

    # 添加数值标签
    for i in range(len(platforms)):
        for j in range(len(top_words)):
            val = matrix[i, j]
            if val > 0:
                text_color = 'white' if val > matrix.max() * 0.5 else 'black'
                ax.text(j, i, str(val), ha='center', va='center', color=text_color, fontsize=8)

    plt.colorbar(im, ax=ax, label='出现频次')
    ax.set_title('平台-关键词热力图', fontsize=16, fontweight='bold', pad=15, fontproperties=font_prop)
    
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    logger.info(f"  热力图已保存: {output_path}")


async def main():
    logger.info("=" * 50)
    logger.info("🎯 热点话题词云可视化系统")
    logger.info("=" * 50)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "wordcloud")
    
    # Step 1: 并发抓取所有平台数据
    logger.info("[Step 1] 采集各平台数据...")
    
    weibo = WeiboCollectorAgent()
    zhihu = ZhihuCollectorAgent()
    xhs = XiaohongshuCollectorAgent()
    maimai = MaimaiCollectorAgent()
    douban = DoubanCollectorAgent()
    
    results = await asyncio.gather(
        weibo.execute(), zhihu.execute(), xhs.execute(),
        maimai.execute(), douban.execute(),
        return_exceptions=True
    )
    
    all_topics_by_platform = {}
    for platform, result in zip(['weibo', 'zhihu', 'xiaohongshu', 'maimai', 'douban'], results):
        if isinstance(result, Exception):
            logger.error(f"  {platform} 抓取失败: {result}")
            all_topics_by_platform[platform] = []
        else:
            topics = result.get('topics', [])
            all_topics_by_platform[platform] = topics
            logger.info(f"  {platform}: {len(topics)} 条")
    
    # 汇总所有话题用于全局分析
    all_topics = []
    for topics in all_topics_by_platform.values():
        all_topics.extend(topics)
    
    # Step 2: 生成词云图
    logger.info("[Step 2] 生成词云可视化...")
    
    # 图1: 各平台词云
    wc_path = os.path.join(out_dir, f"wordcloud_platforms_{timestamp}.png")
    plot_platform_wordclouds(all_topics_by_platform, wc_path)
    
    # 图2: 关键词对比条形图
    bar_path = os.path.join(out_dir, f"keyword_bars_{timestamp}.png")
    plot_keyword_bar_chart(all_topics_by_platform, bar_path)
    
    # 图3: 热力图
    heat_path = os.path.join(out_dir, f"platform_keyword_heatmap_{timestamp}.png")
    plot_platform_heatmap(all_topics_by_platform, heat_path)
    
    # Step 3: 生成综合HTML报告
    logger.info("[Step 3] 生成HTML报告...")
    
    # 平台统计摘要
    platform_summary = {}
    for platform, topics in all_topics_by_platform.items():
        word_freq = extract_keywords(topics)
        top_kw = [w for w, c in word_freq.most_common(10)]
        platform_summary[platform] = {
            'count': len(topics),
            'top_keywords': top_kw,
            'word_count': sum(word_freq.values()),
        }
    
    html_path = os.path.join(out_dir, f"wordcloud_report_{timestamp}.html")
    
    # 生成HTML
    platform_cards_html = ''
    for platform, info in platform_summary.items():
        pname = PLATFORM_NAMES.get(platform, platform)
        pcolor = PLATFORM_COLORS.get(platform, '#888')
        keywords = ', '.join(info['top_keywords'][:8])
        platform_cards_html += f'''
        <div class="platform-card" style="border-left: 4px solid {pcolor}">
            <div class="platform-name">{pname}</div>
            <div class="platform-count">{info['count']} 条话题 · {info['word_count']} 个关键词</div>
            <div class="platform-kw">关键词: {keywords}</div>
        </div>'''
    
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>热点话题词云报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
        .header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 24px 32px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }}
        .header h1 {{ font-size: 22px; font-weight: 600; background: linear-gradient(90deg, #f59e0b, #ef4444); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .header .time {{ color: #94a3b8; font-size: 13px; }}
        .platform-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding: 20px 32px; }}
        .platform-card {{ background: #1e293b; border-radius: 10px; padding: 16px; border: 1px solid #334155; }}
        .platform-name {{ font-size: 16px; font-weight: 600; margin-bottom: 8px; color: #f59e0b; }}
        .platform-count {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; }}
        .platform-kw {{ font-size: 11px; color: #64748b; line-height: 1.6; }}
        .viz-section {{ padding: 20px 32px; }}
        .viz-title {{ font-size: 14px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; }}
        .viz-img {{ width: 100%; border-radius: 12px; border: 1px solid #334155; margin-bottom: 24px; }}
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        .footer {{ text-align: center; padding: 24px; color: #64748b; font-size: 12px; border-top: 1px solid #334155; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔥 热点话题词云报告</h1>
        <span class="time">{timestamp_str}</span>
    </div>
    
    <div class="platform-cards">
        {platform_cards_html}
    </div>
    
    <div class="viz-section">
        <div class="viz-title">📊 各平台词云对比</div>
        <img class="viz-img" src="wordcloud_platforms_{timestamp}.png" alt="各平台词云">
    </div>
    
    <div class="viz-section">
        <div class="viz-title">📈 关键词频次对比 TOP30</div>
        <img class="viz-img" src="keyword_bars_{timestamp}.png" alt="关键词对比">
    </div>
    
    <div class="viz-section">
        <div class="viz-title">🔥 平台-关键词热力图</div>
        <img class="viz-img" src="platform_keyword_heatmap_{timestamp}.png" alt="热力图">
    </div>
    
    <div class="footer">
        热点话题词云报告 | 数据来源：微博/知乎/小红书/脉脉/豆瓣 | {timestamp_str}
    </div>
</body>
</html>'''
    
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    logger.info(f"  HTML报告: {html_path}")
    
    # Step 4: Playwright截图
    logger.info("[Step 4] Playwright截图...")
    screenshot_path = os.path.join(out_dir, f"wordcloud_report_{timestamp}.png")
    
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})
            await page.goto(f'file://{html_path}')
            await page.wait_for_load_state('networkidle')
            await page.screenshot(path=screenshot_path, full_page=True)
            await browser.close()
        logger.info(f"  截图已保存: {screenshot_path}")
    except Exception as e:
        logger.warning(f"  Playwright截图失败: {e}")
        screenshot_path = None
    
    logger.info("=" * 50)
    logger.info("✅ 词云可视化完成!")
    logger.info(f"  输出目录: {out_dir}")
    logger.info("=" * 50)
    
    return {
        'html': html_path,
        'screenshot': screenshot_path,
        'wordcloud': wc_path,
        'bar_chart': bar_path,
        'heatmap': heat_path,
    }


if __name__ == '__main__':
    result = asyncio.run(main())
    print(json.dumps(result, ensure_ascii=False, indent=2))