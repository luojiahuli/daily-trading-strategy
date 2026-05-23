#!/usr/bin/env python3
"""
推送热点话题词云报告和豆瓣榜单报告到飞书

用法:
  python3 push_wordcloud_douban.py                    # 推送最新词云+豆瓣截图
  python3 push_wordcloud_douban.py --latest          # 同上
  python3 push_wordcloud_douban.py --wc-path <path>  # 指定词云截图
  python3 push_wordcloud_douban.py --douban-path <path>  # 指定豆瓣截图
  python3 push_wordcloud_douban.py --no-send          # 仅截图不上传

环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID (从~/.hermes/.env读取)
"""

import os, sys, json, glob, re
from pathlib import Path
from datetime import datetime

# ── 飞书凭据读取 ────────────────────────────────────────────────────────────
HOME = Path.home()
ENV_PATH = HOME / ".hermes" / ".env"

def load_env():
    creds = {}
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip()
    return creds

# ── 最新文件查找 ─────────────────────────────────────────────────────────────
def find_latest(pattern, base=None):
    """找最新匹配文件"""
    if base:
        pattern = os.path.join(base, pattern)
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

# ── 飞书 API ────────────────────────────────────────────────────────────────
def get_tenant_token(creds):
    import requests
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": creds.get("FEISHU_APP_ID", ""),
            "app_secret": creds.get("FEISHU_APP_SECRET", ""),
        }, timeout=15
    )
    return resp.json().get("tenant_access_token", "")

def upload_image(token, image_path):
    import requests
    with open(image_path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("report.png", f, "image/png")},
            data={"image_type": "message"},
            timeout=30
        )
    data = resp.json()
    if data.get("code") == 0:
        return data["data"]["image_key"]
    print(f"  ✗ 上传失败: {data.get('msg')}")
    return None

def send_message(token, chat_id, msg_type, content):
    import requests
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content),
        },
        timeout=15
    )
    data = resp.json()
    if data.get("code") == 0:
        print(f"  ✓ 消息发送成功")
    else:
        print(f"  ✗ 发送失败: {data.get('msg')}")

def send_text(token, chat_id, text):
    send_message(token, chat_id, "text", {"text": text})

def send_image(token, chat_id, image_key, text=""):
    content = {"image_key": image_key}
    if text:
        content["text"] = text
    send_message(token, chat_id, "image", content)

# ── 主流程 ──────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="推送词云+豆瓣报告到飞书")
    parser.add_argument("--wc-path", help="词云截图路径（默认自动找最新）")
    parser.add_argument("--douban-path", help="豆瓣榜单截图路径（默认自动找最新）")
    parser.add_argument("--no-send", action="store_true", help="仅截图不推送")
    parser.add_argument("--feishu-chat-id", default=os.environ.get("FEISHU_CHAT_ID", ""), help="飞书chat_id")
    args = parser.parse_args()

    # 找最新文件
    wc_path = args.wc_path
    douban_path = args.douban_path

    if not wc_path:
        # 词云截图
        wc_path = find_latest("wordcloud_report_*.png", "~/workspace/hot_topic_system/reports/wordcloud")
        wc_path = os.path.expanduser(wc_path) if wc_path else None

    if not douban_path:
        # 豆瓣报告
        douban_path = find_latest("douban_*.png", "~/workspace/douban_reports")
        douban_path = os.path.expanduser(douban_path) if douban_path else None

    print(f"📊 词云报告: {wc_path or '未找到'}")
    print(f"🎬 豆瓣报告: {douban_path or '未找到'}")

    if args.no_send:
        print("  --no-send 模式，仅展示路径")
        return

    # 加载飞书凭据
    creds = load_env()
    app_id = creds.get("FEISHU_APP_ID", "")
    app_secret = creds.get("FEISHU_APP_SECRET", "")
    chat_id = args.feishu_chat_id or creds.get("FEISHU_CHAT_ID", "")

    if not app_id or not app_secret:
        print("✗ 飞书凭据未配置 (FEISHU_APP_ID/FEISHU_APP_SECRET)")
        return

    if not chat_id:
        print("✗ FEISHU_CHAT_ID 未配置")
        return

    token = get_tenant_token(creds)
    if not token:
        print("✗ 获取飞书token失败")
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 发文本简介
    intro = f"🔥 热点话题日报 · {ts}\n\n📊 各平台词云对比 · 关键词热力图\n🎬 豆瓣电影/图书/音乐 Top榜单\n\n来源: 微博/知乎/小红书/脉脉/豆瓣"
    send_text(token, chat_id, intro)

    # 发词云截图
    if wc_path and os.path.exists(wc_path):
        print(f"📤 上传词云报告: {wc_path}")
        img_key = upload_image(token, wc_path)
        if img_key:
            send_image(token, chat_id, img_key, "📊 热点话题词云报告")
            print(f"  ✓ 词云报告已推送")
        else:
            print(f"  ✗ 词云报告上传失败")

    # 发豆瓣截图
    if douban_path and os.path.exists(douban_path):
        print(f"📤 上传豆瓣榜单: {douban_path}")
        img_key = upload_image(token, douban_path)
        if img_key:
            send_image(token, chat_id, img_key, "🎬 豆瓣榜单日报")
            print(f"  ✓ 豆瓣榜单已推送")
        else:
            print(f"  ✗ 豆瓣榜单上传失败")

    print(f"\n✅ 推送完成 · {ts}")

if __name__ == "__main__":
    main()