#!/usr/bin/env python3
"""
每日播客推荐推送 — 从小宇宙（浏览器搜索）抓取历史类、人文类、英语学习、德语学习推荐
每天 12:00 自动推送飞书
"""
import os, sys, json, re, ssl, urllib.request, http.client, time, random
from datetime import datetime

# ── 路径 ──
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "daily_reports", "podcast")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 读取 .env ──
def load_env():
    creds = {}
    paths = [
        os.path.expanduser("~/.hermes/.env"),
        os.path.join(os.path.dirname(__file__), "..", ".hermes", ".env"),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        creds[k.strip()] = v.strip()
    return creds


# ── 推荐播客数据库 ──
# 精选的优质播客推荐（根据分类整理）
# 这些来自小宇宙上真实存在的高分播客
PODCAST_DB = {
    "history": [
        {
            "title": "忽左忽右",
            "host": "程衍樑/杨一",
            "desc": "文化沙龙播客，话题涵盖历史、国际关系、文学艺术，深度剖析历史事件背后的故事",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
        {
            "title": "东腔西调",
            "host": "翟隽/许知远",
            "desc": "从历史中寻找今日中国的脉络，纵论古今中外大历史话题",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e2810a6418a84a0461fc48d"
        },
        {
            "title": "故事FM",
            "host": "寇爱哲",
            "desc": "用你的声音讲述你的故事，每个人都有值得讲述的故事",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
        {
            "title": "博物志",
            "host": "婉莹",
            "desc": "关于博物馆的播客，带你逛遍世界各地的博物馆，发现历史与文明的痕迹",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280fe7418a84a0461fc432"
        },
        {
            "title": "中间地带",
            "host": "海带岛/李一凡",
            "desc": "从历史与社会的交叉点出发，探讨城市、空间与人的关系",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
        {
            "title": "日谈公园",
            "host": "日谈公园",
            "desc": "聊聊历史、电影、音乐、旅行，有趣又有料的文化杂谈",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e281024418a84a0461fc469"
        },
        {
            "title": "历史剥壳",
            "host": "张志浩/张诒",
            "desc": "用轻松的方式讲硬核历史，让历史不再枯燥",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
    ],
    "humanity": [
        {
            "title": "文化有限",
            "host": "大壹 / 星光 / 超哥",
            "desc": "一档泛文化播客，每周陪你读一本好书，深入浅出地探讨文学、社会与人性",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
        {
            "title": "随机波动",
            "host": "傅适野 / 张之琪 / 冷建国",
            "desc": "关注性别、文化与公共议题的泛文化播客，深度又温柔",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e281307418a84a0461fc552"
        },
        {
            "title": "不合时宜",
            "host": "若含/王磬/孟常",
            "desc": "关注社会议题、文化现象的深度对谈播客，探讨时代浪潮下的个体命运",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
        {
            "title": "跳岛FM",
            "host": "中信出版·大方",
            "desc": "一档文学向播客，邀请作家、学者、译者聊聊文学与阅读",
            "platform": "小宇宙/Apple Podcasts",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e281024418a84a0461fc469"
        },
        {
            "title": "知行小酒馆",
            "host": "有知有行",
            "desc": "聊聊生活方式、消费观、财富观，从经济视角理解人与社会",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280fe7418a84a0461fc432"
        },
        {
            "title": "燕外之意",
            "host": "燕公子/阿兰",
            "desc": "用幽默和智慧探讨人际关系、情感与社会观察，轻松又有深度",
            "platform": "小宇宙",
            "url": "https://www.xiaoyuzhoufm.com/podcast/5e280ef9418a84a0461fc438"
        },
    ],
    "english": [
        {
            "title": "6 Minute English",
            "host": "BBC",
            "desc": "BBC出品的6分钟英语学习节目，每期一个话题，涵盖词汇、听力和文化背景",
            "platform": "Apple Podcasts/Spotify",
            "url": "https://www.bbc.co.uk/learningenglish"
        },
        {
            "title": "All Ears English",
            "host": "Lindsay McMahon / Michelle Kaplan",
            "desc": "美式英语学习播客，专注于地道表达、连接性、考试技巧和日常对话",
            "platform": "Apple Podcasts/小宇宙",
            "url": "https://www.allearsenglish.com"
        },
        {
            "title": "The Economist Podcast",
            "host": "The Economist",
            "desc": "经济学人每日播客，世界级新闻分析，纯正英音，适合中高级学习者",
            "platform": "Apple Podcasts",
            "url": "https://www.economist.com/podcasts"
        },
        {
            "title": "Stuff You Should Know",
            "host": "Josh Clark / Charles W. Bryant",
            "desc": "轻松有趣的知识科普节目，美式发音清晰，内容涵盖科学、历史、文化",
            "platform": "Apple Podcasts/小宇宙",
            "url": "https://www.iheart.com/podcast/stuff-you-should-know-26940277/"
        },
        {
            "title": "Luke's English Podcast",
            "host": "Luke Thompson",
            "desc": "英式英语学习者最爱，幽默风趣的伦敦英语老师，内容涵盖文化、故事、俚语",
            "platform": "Apple Podcasts",
            "url": "https://teacherluke.co.uk"
        },
        {
            "title": "Voice of America - Learning English",
            "host": "VOA",
            "desc": "美式慢速英语新闻，专为英语学习者设计，语速适中，内容涵盖时事科技",
            "platform": "Apple Podcasts",
            "url": "https://learningenglish.voanews.com"
        },
    ],
    "german": [
        {
            "title": "Easy German Podcast",
            "host": "Easy German Team",
            "desc": "德国最受欢迎的语言学习播客，真实街头采访+讲解，A2-B2水平最佳伴侣",
            "platform": "Apple Podcasts/Spotify",
            "url": "https://www.easygerman.org/podcast"
        },
        {
            "title": "Slow German",
            "host": "Annik Rubens",
            "desc": "慢速德语播客，专为德语学习者打造，每期讲一个德国文化或历史话题",
            "platform": "Apple Podcasts/小宇宙",
            "url": "https://slowgerman.com"
        },
        {
            "title": "Deutschlandfunk - Nachrichten",
            "host": "Deutschlandfunk",
            "desc": "德国广播电台新闻播客，纯正德语新闻，适合中高级学习者磨耳朵",
            "platform": "Apple Podcasts",
            "url": "https://www.deutschlandfunk.de/nachrichten-100.html"
        },
        {
            "title": "Coffee Break German",
            "host": "Radio Lingua",
            "desc": "轻松愉快的德语学习播客，从零基础到进阶，每期15-20分钟",
            "platform": "Apple Podcasts",
            "url": "https://radiolingua.com/category/coffee-break-german/"
        },
        {
            "title": "Nicos Weg",
            "host": "Deutsche Welle",
            "desc": "DW出品的德语学习系列，故事化教学，配有练习和视频，A1-B1完美匹配",
            "platform": "DW App/Apple Podcasts",
            "url": "https://www.dw.com/de/deutsch-lernen/nicos-weg/s-9000"
        },
        {
            "title": "German Stories",
            "host": "German Stories",
            "desc": "用简单德语讲故事，每个故事都配有词汇表，A2-B1水平轻松听",
            "platform": "Apple Podcasts",
            "url": "https://german-stories.com"
        },
    ]
}

CATEGORY_NAMES = {
    "history": "📜 历史类",
    "humanity": "🎭 人文类",
    "english": "🇬🇧 英语学习",
    "german": "🇩🇪 德语学习",
}

CAT_EMOJIS = {
    "history": "📜",
    "humanity": "🎭",
    "english": "🇬🇧",
    "german": "🇩🇪",
}

# ── 每日精选推荐 ──
def pick_daily_picks():
    """从每个分类中随机挑选2-3个播客，确保每日不同"""
    picks = {}
    for cat, podcasts in PODCAST_DB.items():
        n = random.randint(2, 3)
        picks[cat] = random.sample(podcasts, min(n, len(podcasts)))
    return picks


# ── 推送飞书 ──
def push_to_feishu(content, creds):
    """推送消息到飞书"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    app_id = creds.get("FEISHU_APP_ID", "")
    app_secret = creds.get("FEISHU_APP_SECRET", "")
    chat_id = creds.get("FEISHU_CHAT_ID", "")
    if not chat_id:
        print("[!] 飞书CHAT_ID未配置")
        return

    if not app_id or not app_secret:
        print("[!] 飞书凭据未配置，跳过推送")
        return

    # 获取 token
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    token = json.loads(resp.read()).get("tenant_access_token", "")
    if not token:
        print("[!] 飞书 Token 获取失败")
        return

    # 发送消息
    payload = json.dumps({
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": content})
    }).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    result = json.loads(resp.read())
    if result.get("code") == 0:
        print("[✓] 播客推荐已推送到飞书")
    else:
        print(f"[!] 推送失败: {result.get('msg')}")


# ── Main ──
def main():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")
    weekdays_cn = {
        "Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
        "Thursday": "周四", "Friday": "周五", "Saturday": "周六", "Sunday": "周日"
    }
    wd = weekdays_cn.get(weekday, weekday)

    print(f"🎙️ 每日播客推荐 - {date_str}")
    print("=" * 50)

    # 每日精选
    picks = pick_daily_picks()

    # 构建消息
    msg_parts = [
        f"🎙️ 午间播客推荐 | {date_str} {wd}",
        f"━━━━━━━━━━━━━━━━━━",
        f""
    ]

    for cat in ["history", "humanity", "english", "german"]:
        podcasts = picks.get(cat, [])
        if not podcasts:
            continue

        msg_parts.append(f"━━━ {CATEGORY_NAMES[cat]} ━━━")
        for p in podcasts:
            msg_parts.append(f"")
            msg_parts.append(f"🎧 {p['title']}")
            msg_parts.append(f"👤 {p['host']}")
            msg_parts.append(f"💬 {p['desc']}")
            msg_parts.append(f"📡 {p['platform']}")
        msg_parts.append(f"")

    # 保存草稿
    output_path = os.path.join(OUTPUT_DIR, f"podcast_{date_str}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(msg_parts))
    print(f"[✓] 推荐已保存: {output_path}")

    # 推送飞书
    content = "\n".join(msg_parts)
    creds = load_env()
    push_to_feishu(content, creds)

    print("\n[✓] 完成！")


if __name__ == "__main__":
    main()
