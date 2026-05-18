#!/usr/bin/env python3
"""
每日AI/ML热门论文抓取 & 摘要生成
使用 arxiv API
"""
import os, sys, json, re, time, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "arxiv_daily")
os.makedirs(OUTPUT_DIR, exist_ok=True)

NOW = datetime.now()
DATE_STR = NOW.strftime("%Y-%m-%d")
TS = NOW.strftime("%Y%m%d_%H%M")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom'
}

# 热点方向关键词评分
KEYWORDS_SCORE = {
    "agent": 5, "reinforcement learning": 4, "rlhf": 4, "dpo": 4, "grpo": 5,
    "alignment": 3, "fine.?tun": 3, "post.?train": 3,
    "reasoning": 4, "chain.?of.?thought": 3,
    "large language model": 5, "llm": 5,
    "multimodal": 4, "vision.?language": 4, "vlm": 4,
    "diffusion": 3, "world model": 4,
    "efficien": 2, "scaling": 3, "quantiz": 2,
    "kv.?cache": 5, "attention": 3, "transformer": 3,
    "moe": 3, "mixture of expert": 3,
    "retrieval": 2, "rag": 3, "embedding": 2,
    "safety": 2, "benchmark": 2,
    "state.?space": 2, "mamba": 3,
    "optimizer": 3, "training": 3,
    "inference": 4, "generat": 2,
    "continual": 2,
    "reward": 3, "policy": 2, "rl": 3,
    "knowledge graph": 2, "graph": 1,
    "survey": 2, "review": 2,
    "instruction": 2, "steer": 2,
    "coding": 2, "math": 2,
    "pre.?train": 3, "foundation model": 3,
    "hallucinat": 3, "factual": 2,
    "token": 2, "context": 3,
    "open.?source": 2, "open.?weight": 2,
    "dataset": 1, "data": 1,
    "compress": 2, "accelerat": 2,
    "prompt": 2, "in.?context": 2,
    "embed": 2, "vector": 1,
    "speculat": 2, "draft": 2,
    "multi.?head": 2, "cross.?attention": 2,
}


def fetch_arxiv_api(category, max_results=25):
    """通过arxiv API获取论文"""
    url = (f"https://export.arxiv.org/api/query?"
           f"search_query=cat:{quote(category)}&"
           f"sortBy=submittedDate&sortOrder=descending&"
           f"max_results={max_results}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        root = ET.fromstring(resp.text)

        papers = []
        for entry in root.findall('atom:entry', NS):
            paper_id = entry.find('atom:id', NS).text.split('/')[-1]
            paper_id = paper_id.replace('v1', '').replace('v2', '').strip()

            title = entry.find('atom:title', NS).text.strip()
            title = re.sub(r'\s+', ' ', title)

            summary = entry.find('atom:summary', NS).text.strip()
            summary = re.sub(r'\s+', ' ', summary)

            # 分类
            categories = []
            for cat in entry.findall('atom:category', NS):
                c = cat.get('term', '')
                if c:
                    categories.append(c)

            papers.append({
                "id": paper_id,
                "title": title,
                "abstract": summary,
                "subjects": ", ".join(categories),
                "url": f"https://arxiv.org/abs/{paper_id}"
            })

        return papers
    except Exception as e:
        print(f"[ERROR] arxiv API {category}: {e}")
        return []


def score_paper(title, abstract, subjects):
    """评分论文重要性"""
    score = 0
    text = (title + " " + abstract + " " + subjects).lower()

    for kw, bonus in KEYWORDS_SCORE.items():
        if re.search(kw, text):
            score += bonus

    return score


def main():
    print(f"[*] Arxiv 每日论文抓取 - {DATE_STR}")
    print()

    all_papers = []

    for cat, name in [("cs.AI", "cs.AI — Artificial Intelligence"),
                      ("cs.LG", "cs.LG — Machine Learning")]:
        print(f"[*] 获取 {name}...")
        papers = fetch_arxiv_api(cat, max_results=30)
        print(f"    → {len(papers)} 篇")

        for p in papers:
            p['score'] = score_paper(p['title'], p['abstract'], p.get('subjects', ''))
            p['source'] = name

        scored = [p for p in papers if p['score'] >= 3]  # 过滤低分
        scored.sort(key=lambda x: x['score'], reverse=True)

        print(f"    → 热点相关: {len(scored)} 篇")
        for p in scored[:5]:
            print(f"      [{p['score']}] {p['title'][:60]}")
        all_papers.extend(scored)
        time.sleep(1)

    # 全面排序去重
    all_papers.sort(key=lambda x: x['score'], reverse=True)
    seen_ids = set()
    unique_papers = []
    for p in all_papers:
        if p['id'] not in seen_ids:
            seen_ids.add(p['id'])
            unique_papers.append(p)
    all_papers = unique_papers

    # ── 调用DeepSeek为每篇论文生成中文解析 ──────────────────
    top10 = all_papers[:10]
    print(f"\n[*] 为 {len(top10)} 篇论文生成中文解析...")
    for p in top10:
        p['zh_summary'] = _generate_chinese_summary(p['title'], p['abstract'])
        print(f"  ✓ [{p['score']}] {p['title'][:50]}...")

    # 生成报告
    report_parts = [
        f"# 📰 AI/ML 热门论文日报\n",
        f"**日期:** {DATE_STR} | **数据来源:** arXiv.org | **扫描:** cs.AI + cs.LG\n",
        f"---\n"
    ]

    if not all_papers:
        report_parts.append("> ⚠️ 今日未检测到热点相关论文\n")
    else:
        report_parts.append("## 🏆 今日最热论文 TOP 10\n")
        for i, p in enumerate(all_papers[:10], 1):
            report_parts.append(f"### {i}. {p['title']}\n")
            report_parts.append(f"- **ID:** `{p['id']}` | **分类:** {p['source']}")
            report_parts.append(f"- **热点评分:** {p['score']}")
            report_parts.append(f"- 🔗 [论文页]({p['url']}) | 📥 [PDF下载](https://arxiv.org/pdf/{p['id']})\n")

            abs_text = p['abstract'][:300]
            if len(p['abstract']) > 300:
                abs_text += "..."
            report_parts.append(f"> {abs_text}\n")
            report_parts.append("---\n")

        # 按分类展示
        report_parts.append("## 🔬 按分类浏览\n")
        for source in ["cs.AI — Artificial Intelligence", "cs.LG — Machine Learning"]:
            cat_papers = [p for p in all_papers if p['source'] == source]
            if cat_papers:
                report_parts.append(f"### {source}\n")
                for i, p in enumerate(cat_papers[:8], 1):
                    report_parts.append(
                        f"{i}. [{p['title']}]({p['url']}) — [📥 PDF](https://arxiv.org/pdf/{p['id']})"
                    )
                report_parts.append("")

    # 统计
    report_parts.append(f"\n**扫描统计:** cs.AI + cs.LG 共扫描，{len(all_papers)} 篇热点相关论文 | 抓取时间: {NOW.strftime('%H:%M')}\n")

    report = "\n".join(report_parts)

    # 保存
    report_path = os.path.join(OUTPUT_DIR, f"arxiv_daily_{DATE_STR}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n[+] 报告已保存: {report_path}")

    # 输出报告（给cronjob）
    print("\n" + "=" * 60)
    print("REPORT_START")
    print(report)
    print("REPORT_END")
    print("=" * 60)

    # 生成HTML并截图推送飞书
    try:
        _push_to_feishu_as_image(all_papers[:10], DATE_STR)
    except Exception as e:
        print(f"[!] 截图推送失败 (不影响报告): {e}")


def _generate_chinese_summary(title: str, abstract: str) -> dict:
    """调用DeepSeek生成论文中文解析"""
    import openai
    key = ""
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("DEEPSEEK_API_KEY"):
                    key = line.strip().split("=", 1)[1]
                    break

    if not key:
        return {"summary": "（中文解析暂不可用）", "keypoints": ["-"]}

    client = openai.OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": """你是专业的AI论文解读助手。请用中文详细解析论文。

要求：
1. 先写一段200-300字的核心内容概述，包含解决了什么问题、方法是什么
2. 再列出3-5个关键要点/创新点，每个要点50-100字
3. 最后用一句话总结这项工作的意义或影响

输出格式（JSON）：
{
  "summary": "核心概述（200-300字）",
  "keypoints": ["要点1（50-100字）", "要点2（50-100字）", "要点3（50-100字）", "要点4（50-100字）"],
  "impact": "一句话意义总结（30-50字）"
}

确保总字数不少于500字。只输出JSON。"""},
            {"role": "user", "content": f"论文标题: {title}\n摘要: {abstract[:1000]}\n\n请详细解析。"}],
            temperature=0.3,
            max_tokens=800,
        )
        import json
        text = resp.choices[0].message.content
        # 尝试解析JSON，如果失败则用普通文本
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"summary": text[:200], "keypoints": ["-"]}
    except Exception as e:
        print(f"    [!] 解析失败: {e}")
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "system", "content": "用中文1句话概括这篇论文的核心贡献，然后列出3个要点，用|分隔。"},
                          {"role": "user", "content": f"标题: {title}\n摘要: {abstract[:500]}"}],
                temperature=0.3, max_tokens=200
            )
            text = resp.choices[0].message.content
            parts = text.split("|")
            return {"summary": parts[0].strip(), "keypoints": [p.strip() for p in parts[1:]] if len(parts) > 1 else [text[:100]]}
        except:
            return {"summary": abstract[:150], "keypoints": ["-"]}


def _generate_html_report(papers: list, date_str: str) -> str:
    """用论文数据直接生成Stripe风格HTML，包含中文解析"""
    now = datetime.now().strftime("%m-%d %H:%M")
    items = []
    for i, p in enumerate(papers[:10], 1):
        zh = p.get('zh_summary', {})
        summary = zh.get('summary', '') if isinstance(zh, dict) else ''
        kps = zh.get('keypoints', []) if isinstance(zh, dict) else []
        impact = zh.get('impact', '') if isinstance(zh, dict) else ''
        pts = ''.join(f'<li>{kp}</li>' for kp in kps[:5]) if kps else ''
        impact_html = f'<div class="impact">🎯 {impact}</div>' if impact else ''
        items.append(f'''
<div class="paper">
  <div class="num">{i}</div>
  <div style="flex:1">
    <div class="title">{p['title']}</div>
    <div class="meta">📄 {p.get('id','')} · 🔥 热度 {p['score']} · {p.get('source','')}</div>
    <div class="zh-summary">📖 {summary}</div>
    {"<ul class='kps'>"+pts+"</ul>" if pts else ""}
    {impact_html}
    <div class="links"><a href="{p.get('url','#')}">🔗 arXiv</a> · <a href="https://arxiv.org/pdf/{p.get('id','')}">📥 PDF</a></div>
  </div>
</div>''')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8">
<title>AI论文日报 {date_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Source Sans 3",system-ui,sans-serif;font-weight:300;color:#061b31;background:#ffffff;padding:32px;max-width:800px;margin:0 auto}}
h1{{font-size:26px;font-weight:300;letter-spacing:-0.5px;color:#533afd;margin-bottom:2px}}
.meta{{font-size:12px;color:#64748b;margin-bottom:20px}}
.paper{{display:flex;gap:14px;padding:14px;margin-bottom:10px;background:#f8fafc;border:1px solid #e5edf5;border-radius:5px;align-items:flex-start}}
.num{{font-size:18px;font-weight:400;color:#533afd;min-width:28px;line-height:1.4}}
.title{{font-size:14px;font-weight:400;color:#1c1e54;line-height:1.4}}
.meta{{font-size:11px;color:#64748b;margin:2px 0}}
.zh-summary{{font-size:12px;color:#061b31;margin:4px 0;line-height:1.5;background:rgba(83,58,253,0.04);padding:6px 8px;border-radius:4px;border-left:2px solid #533afd}}
.kps{{margin:4px 0 4px 12px;font-size:11px;color:#64748b;line-height:1.6}}
.impact{{font-size:11px;color:#108c3d;margin:4px 0;padding:4px 8px;background:rgba(21,190,83,0.06);border-radius:4px;border-left:2px solid #15be53}}
.links{{font-size:11px;margin-top:4px}}
.links a{{color:#533afd;text-decoration:none}}
.links a:hover{{text-decoration:underline}}
.footer{{font-size:10px;color:#94a3b8;text-align:center;margin-top:24px}}
</style></head>
<body>
<h1>📰 AI/ML 热门论文日报</h1>
<div class="meta">{date_str} · 扫描 cs.AI + cs.LG · 中文解析 by DeepSeek · 更新 {now}</div>
{''.join(items)}
<div class="footer">生成: arxiv_daily.py · playwright截图推送</div>
</body>
</html>'''
    return html


def _push_to_feishu_as_image(all_papers: list, date_str: str):
    """生成HTML → 截图 → 推送飞书"""
    print("[*] 生成论文日报HTML（含中文解析）...")
    html_content = _generate_html_report(all_papers, date_str)

    output_dir = os.path.join(os.path.dirname(__file__), "arxiv_daily")
    html_path = os.path.join(output_dir, f"arxiv_daily_{date_str}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  ✓ HTML: {html_path}")

    # 截图
    from playwright.sync_api import sync_playwright
    png_path = html_path.replace(".html", ".png")
    print("[*] 截图...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.goto(f"file://{os.path.abspath(html_path)}", wait_until="networkidle")
        page.wait_for_timeout(2000)
        page.screenshot(path=png_path, full_page=True)
        browser.close()
    print(f"  ✓ 截图: {png_path} ({os.path.getsize(png_path)/1024:.0f} KB)")

    # 推送飞书
    import requests as req
    creds = {}
    env_path = os.path.join(os.path.dirname(__file__), "..", ".hermes", ".env")
    alt_path = os.path.expanduser("~/.hermes/.env")
    for p in [env_path, alt_path]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        creds[k.strip()] = v.strip()
                    if "FEISHU_APP_ID" in creds and "FEISHU_APP_SECRET" in creds:
                        break

    if not creds.get("FEISHU_APP_ID") or not creds.get("FEISHU_APP_SECRET"):
        print("  ✗ 飞书凭据未配置，跳过推送")
        return

    # token
    resp = req.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": creds["FEISHU_APP_ID"], "app_secret": creds["FEISHU_APP_SECRET"]})
    token = resp.json().get("tenant_access_token", "")
    if not token:
        print("  ✗ token获取失败")
        return

    # 上传图片
    with open(png_path, "rb") as f:
        resp = req.post("https://open.feishu.cn/open-apis/im/v1/images",
                        headers={"Authorization": f"Bearer {token}"},
                        files={"image": ("arxiv.png", f, "image/png")},
                        data={"image_type": "message"})
    img_data = resp.json()
    if img_data.get("code") != 0:
        print(f"  ✗ 图片上传失败: {img_data.get('msg')}")
        return
    img_key = img_data["data"]["image_key"]

    # 发消息
    chat_id = creds.get("FEISHU_CHAT_ID", "oc_ed483f60e1bc9408534038ee155eaf5d")
    resp = req.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"receive_id": chat_id, "msg_type": "image",
                          "content": json.dumps({"image_key": img_key})})
    if resp.json().get("code") == 0:
        print(f"  ✓ 论文日报图片已推送到飞书")
    else:
        print(f"  ✗ 推送失败: {resp.json().get('msg')}")


if __name__ == "__main__":
    main()
