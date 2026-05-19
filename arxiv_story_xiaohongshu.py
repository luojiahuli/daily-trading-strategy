#!/usr/bin/env python3
"""
将 arxiv 论文日报串成小红书故事笔记，每天 07:45 推送。

流程：
1. 读取当日 arxiv_daily 报告 Markdown（若当日报告不存在则读取最新）
2. 用 DeepSeek 将论文标题串成故事
3. 生成小红书风格笔记
4. 推送飞书（文字 + 封面图）
"""
import os, sys, re, json, ssl, base64, urllib.request, http.client
from datetime import datetime, timedelta, timezone

# ── 路径 ──
ARXIV_DIR = os.path.join(os.path.dirname(__file__), "arxiv_daily")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "daily_reports", "stories")
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

# ── 1. 读取最新 arxiv 报告 ──
def get_latest_arxiv_report():
    """获取最新的 arxiv_daily markdown 报告"""
    if not os.path.isdir(ARXIV_DIR):
        print(f"[!] arxiv_daily 目录不存在: {ARXIV_DIR}")
        return None, None

    files = [f for f in os.listdir(ARXIV_DIR) if f.startswith("arxiv_daily_") and f.endswith(".md")]
    if not files:
        print("[!] 没有找到 arxiv_daily 报告")
        return None, None

    files.sort(reverse=True)
    latest = files[0]
    path = os.path.join(ARXIV_DIR, latest)
    content = open(path, encoding="utf-8").read()
    print(f"[✓] 读取报告: {latest}")
    return content, latest

# ── 2. 解析论文标题列表 ──
def parse_papers_from_report(md_content):
    """从 markdown 中提取论文标题和分类"""
    papers = []
    # 匹配 "### N. 论文标题"
    pattern = r"### \d+\.\s+(.+?)\n"
    titles = re.findall(pattern, md_content)

    # 匹配 ID、分数
    ids = re.findall(r"\*\*ID:\*\* `(.+?)`", md_content)
    scores = re.findall(r"\*\*热点评分:\*\* (\d+)", md_content)
    categories = re.findall(r"\*\*分类:\*\* (.+)", md_content)

    for i, title in enumerate(titles):
        pid = ids[i] if i < len(ids) else ""
        score = int(scores[i]) if i < len(scores) else 0
        cat = categories[i] if i < len(categories) else ""
        papers.append({"title": title, "id": pid, "score": score, "category": cat})

    # 尝试提取摘要
    abstracts = re.findall(r"> (.+?)\n\n---", md_content, re.DOTALL)
    for i, ab in enumerate(abstracts):
        if i < len(papers):
            papers[i]["abstract"] = ab.strip()[:200]

    return papers

# ── 3. 用 DeepSeek 把论文串成故事 ──
def generate_story(papers, date_str, api_key):
    """调用 DeepSeek 把论文串成小红书风格故事"""
    if not papers:
        return _fallback_story(date_str)

    # 构建论文列表文本
    paper_lines = []
    for i, p in enumerate(papers[:8], 1):
        paper_lines.append(f"{i}. 「{p['title']}」")
        if p.get("abstract"):
            paper_lines.append(f"   简介: {p['abstract'][:150]}")
    paper_text = "\n".join(paper_lines)

    prompt = f"""你是"AI论文宇宙"的驻场记者，专门把每天最前沿的AI论文串成有趣的小故事。

今天的日期: {date_str}
今天的论文清单（共{len(papers)}篇）:

{paper_text}

请按以下要求创作一篇小红书笔记：
1. **标题**（≤20字，带emoji吸睛）
2. **开头**（设定一个"论文宇宙"场景，比如让论文标题里的人物/概念在平行世界里发生奇妙互动）
3. **正文**（用故事把3-5篇最有趣的论文串起来，比如："昨天，机器人学会喵喵叫了...原来不是真的猫，而是...")
4. **结尾**（总结并引导互动）
5. **标签**（#AI论文 #每日科技 #前沿科技 等6-8个）

要求：
- 口语化，像在跟朋友聊天
- 每段短小（3-5行），手机阅读友好
- 让非技术读者也能看懂、觉得有趣
- 保持科技前沿的真实感（不要编造不存在的内容）
- 把论文标题自然融入故事中

输出格式（JSON）:
{{
  "title": "小红书标题",
  "body": "正文内容（含换行和emoji，之间用\\n---\\n分隔章节）",
  "tags": "#标签1 #标签2 #标签3"
}}"""

    try:
        import openai
        client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是AI论文周刊记者，用有趣的故事把学术论文讲给普通人听。"},
                      {"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1500,
        )
        text = resp.choices[0].message.content
        # 尝试解析 JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 从文本中提取 JSON
            m = re.search(r'\{.*"title".*"body".*"tags".*\}', text, re.DOTALL)
            if m:
                return json.loads(m.group())
            # fallback
            return _fallback_story(date_str)
    except Exception as e:
        print(f"[!] DeepSeek 调用失败: {e}")
        return _fallback_story(date_str)


def _fallback_story(date_str):
    """当 AI 调用失败时的备用内容"""
    return {
        "title": f"🤖 {date_str} AI论文宇宙日报",
        "body": f"今天AI界又出了新论文！\n\n从 arXiv 上扫到了不少有意思的研究\n来看看今天有什么好玩的吧 👇\n\n---\n\n📖 详情请见 09:15 的论文日报推送\n\n你感兴趣哪个方向？评论区告诉我～",
        "tags": "#AI #论文日报 #前沿科技"
    }


# ── 4. 生成封面图 ──
def generate_cover(story, date_str, output_path):
    """用 Pillow 生成小红书封面图"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[!] Pillow 未安装，跳过封面图")
        return None

    W, H = 1080, 1440

    # 找字体
    font_search_bold = [
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/local/share/fonts/NotoSansCJK-Bold.ttc",
        "/opt/homebrew/share/fonts/NotoSansCJK-Bold.ttc",
    ]
    font_search_reg = [
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/local/share/fonts/NotoSansCJK-Regular.ttc",
        "/opt/homebrew/share/fonts/NotoSansCJK-Regular.ttc",
    ]

    bold_path = next((p for p in font_search_bold if os.path.exists(p)), None)
    reg_path = next((p for p in font_search_reg if os.path.exists(p)), None)

    if not bold_path:
        print("[!] 找不到中文字体，跳过封面图")
        return None

    img = Image.new("RGBA", (W, H))
    draw = ImageDraw.Draw(img)

    # 渐变背景 (紫色)
    for y in range(H):
        r = y / H
        c1, c2 = (102, 126, 234), (240, 147, 251)
        c = tuple(int(c1[i] + (c2[i] - c1[i]) * r) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c + (255,))

    # 装饰圆
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for cx, cy, rad, alpha in [(200, 200, 150, 30), (900, 350, 200, 20),
                                 (150, 1100, 180, 25), (850, 1200, 130, 30)]:
        od.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                   fill=(255, 255, 255, alpha))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # 文章标题
    title = story.get("title", "").replace("🔥", "").replace("🤖", "").replace("💡", "").replace("✨", "").strip()

    font_title = ImageFont.truetype(bold_path, 72)

    # 自动换行
    max_w = 800
    words = list(title)
    lines = []
    cur = ""
    for ch in words:
        test = cur + ch
        bb = draw.textbbox((0, 0), test, font=font_title)
        if bb[2] - bb[0] <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)

    if not lines:
        lines = ["AI", "论文日报"]

    # 居中
    line_h = 90
    total_h = len(lines) * line_h
    start_y = (H - total_h) // 2 - 60

    for i, line in enumerate(lines):
        bb = draw.textbbox((0, 0), line, font=font_title)
        tw = bb[2] - bb[0]
        x = (W - tw) // 2
        # 高亮最后一行
        if i == len(lines) - 1 and len(lines) > 1:
            hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            hd = ImageDraw.Draw(hl)
            hd.rounded_rectangle([x - 20, start_y - 8, x + tw + 20, start_y + line_h - 10],
                                 radius=12, fill=(255, 255, 255, 50))
            img = Image.alpha_composite(img, hl)
            draw = ImageDraw.Draw(img)
        draw.text((x, start_y), line, fill="white", font=font_title)
        start_y += line_h

    # 日期
    if reg_path:
        font_date = ImageFont.truetype(reg_path, 36)
        date_text = date_str
        bb = draw.textbbox((0, 0), date_text, font=font_date)
        tw = bb[2] - bb[0]
        draw.text(((W - tw) // 2, start_y + 40), date_text, fill=(255, 255, 255, 200), font=font_date)

    # Save
    img.convert("RGB").save(output_path, "PNG", quality=95)
    print(f"[✓] 封面图已生成: {output_path} ({os.path.getsize(output_path)//1024}KB)")
    return output_path


# ── 5. 推送飞书 ──
def push_to_feishu(story, date_str, cover_path, creds):
    """推送故事到飞书（文字消息）"""
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

    # 先发封面图
    image_key = ""
    if cover_path and os.path.exists(cover_path):
        try:
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            with open(cover_path, "rb") as f:
                img_data = f.read()
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
                f"message\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="cover.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode("utf-8") + img_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

            conn = http.client.HTTPSConnection("open.feishu.cn", timeout=30,
                                               context=ssl._create_unverified_context())
            conn.request("POST", "/open-apis/im/v1/images",
                         body=body,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": f"multipart/form-data; boundary={boundary}"})
            resp = conn.getresponse()
            result = json.loads(resp.read().decode())
            image_key = result.get("data", {}).get("image_key", "")
            if image_key:
                print("[✓] 封面图上传成功")
        except Exception as e:
            print(f"[!] 封面图上传失败: {e}")

    # 构建消息
    title = story.get("title", "")
    body_text = story.get("body", "")
    tags = story.get("tags", "")

    # 推封面图
    if image_key:
        payload = json.dumps({
            "receive_id": chat_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key})
        }).encode()
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=30, context=ctx)

    # 推文字
    msg = f"📖 论文故事会 | {date_str}\n\n{title}\n\n{body_text}\n\n{tags}"
    payload = json.dumps({
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": msg})
    }).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    result = json.loads(resp.read())
    if result.get("code") == 0:
        print("[✓] 文章已推送到飞书")
    else:
        print(f"[!] 推送失败: {result.get('msg')}")


# ── Main ──
def main():
    print("=" * 50)
    print("📖 Arxiv 论文故事会 - 小红书风格生成")
    today = datetime.now().strftime("%Y-%m-%d")
    # 如果是早上 07:45，昨天的论文
    now_h = datetime.now().hour
    if now_h < 9:
        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        date_str = today
    print(f"📅 日期: {date_str}")
    print()

    # 1. 读取报告
    md_content, filename = get_latest_arxiv_report()
    if not md_content:
        print("[!] 没有可用的 arxiv 报告")
        return

    # 2. 解析论文
    papers = parse_papers_from_report(md_content)
    print(f"[✓] 解析到 {len(papers)} 篇论文")
    for p in papers[:5]:
        print(f"    [{p['score']}] {p['title'][:50]}")

    # 3. 生成故事
    print("\n🤖 用 DeepSeek 串故事...", end="", flush=True)
    creds = load_env()
    api_key = creds.get("DEEPSEEK_API_KEY", "")
    story = generate_story(papers, date_str, api_key)
    print(" ✓")
    print(f"   标题: {story.get('title', '')}")
    print(f"   正文长度: {len(story.get('body', ''))} 字")
    print(f"   标签: {story.get('tags', '')}")

    # 4. 保存草稿
    cover_path = os.path.join(OUTPUT_DIR, f"story_cover_{date_str}.png")
    generate_cover(story, date_str, cover_path)
    output_path = os.path.join(OUTPUT_DIR, f"story_{date_str}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# 📖 论文故事会 | {date_str}\n\n")
        f.write(f"## {story.get('title', '')}\n\n")
        f.write(story.get("body", ""))
        f.write(f"\n\n---\n\n")
        f.write(story.get("tags", ""))
    print(f"[✓] 草稿已保存: {output_path}")

    # 5. 推送飞书
    print("\n📤 推送飞书...")
    push_to_feishu(story, date_str, cover_path, creds)

    # 输出给 cronjob 使用
    print("\n" + "=" * 50)
    print("STORY_START")
    print(json.dumps(story, ensure_ascii=False))
    print("STORY_END")
    print("=" * 50)


if __name__ == "__main__":
    main()
