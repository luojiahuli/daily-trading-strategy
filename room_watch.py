#!/usr/bin/env python3
"""
房间观察日记 - 每天定时拍照记录房间内的变化，推送飞书
"""

import os, sys, time, json, re, base64, ssl, urllib.request, http.client, threading
from datetime import datetime
from pathlib import Path

# 读 API Key
DASHSCOPE_API_KEY = ""
FEISHU_APP_ID = ""
FEISHU_APP_SECRET = ""
FEISHU_CHAT_ID = ""
for line in open(os.path.expanduser("~/.hermes/.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        if k == "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY = v
        if k == "FEISHU_APP_ID": FEISHU_APP_ID = v
        if k == "FEISHU_APP_SECRET": FEISHU_APP_SECRET = v
        if k == "FEISHU_CHAT_ID": FEISHU_CHAT_ID = v

# 保存目录
DATA_DIR = Path.home() / "workspace" / "room_watch"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "daily_log.md"


# ═══════════════════════════════════════
# 1. 拍照
# ═══════════════════════════════════════

def camera_capture(save_path):
    """
    使用 macOS screencapture 拍照（速度快、稳定）
    """
    import subprocess
    from pathlib import Path

    try:
        result = subprocess.run(
            ["screencapture", "-x", "-T", "0", str(save_path)],
            capture_output=True, timeout=10
        )
        if save_path.exists() and save_path.stat().st_size > 1000:
            print(" ✅ 拍照成功")
            return True
        print(f" ⚠️ screencapture 文件无效 (size={save_path.stat().st_size if save_path.exists() else 0})")
    except subprocess.TimeoutExpired:
        print(" ⚠️ screencapture 超时")
    except Exception as e:
        print(f" ⚠️ screencapture 失败: {e}")
    return False


# ═══════════════════════════════════════
# 2. Qwen-VL 分析房间
# ═══════════════════════════════════════

def analyze_room(image_path):
    from PIL import Image
    img = Image.open(image_path)
    # 如果是 RGBA 模式（PNG），转换为 RGB 以便存为 JPEG
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    img.thumbnail((1536, 1536), Image.LANCZOS)
    img.save("/tmp/room_analyze.jpg", "JPEG", quality=80)

    with open("/tmp/room_analyze.jpg", "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    prompt = """请仔细观察这张照片（可能是摄像头画面或屏幕截图），从以下几个方面描述：
1. 光线情况（明亮/昏暗？自然光还是灯光？）
2. 人物状态（画面中有人吗？在做什么？）
3. 物品变化（有没有新的物品出现或消失？）
4. 桌面/书架（东西摆放整齐还是杂乱？）
5. 整体氛围

请用简洁的段落描述，控制在200字以内。注意：如果这是屏幕截图而非实际房间照片，请说明"这是屏幕截图，画面内容可能不是当前房间实际情况"。"""

    payload = json.dumps({
        "model": "qwen-vl-max",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]
        }],
        "max_tokens": 500
    }).encode()

    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
    )
    ctx2 = ssl.create_default_context()
    ctx2.check_hostname = False
    ctx2.verify_mode = ssl.CERT_NONE
    resp = urllib.request.urlopen(req, timeout=120, context=ctx2)
    result = json.loads(resp.read())
    return result['choices'][0]['message']['content']


# ═══════════════════════════════════════
# 3. 获取飞书 Token
# ═══════════════════════════════════════

def get_feishu_token():
    payload = json.dumps({
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }).encode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    result = json.loads(resp.read())
    return result.get("tenant_access_token", "")


# ═══════════════════════════════════════
# 4. 上传图片到飞书
# ═══════════════════════════════════════

def upload_feishu_image(token, image_path):
    import http.client
    import mimetypes
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"

    with open(image_path, "rb") as f:
        img_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
        f"message\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="room.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8") + img_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    conn = http.client.HTTPSConnection("open.feishu.cn", timeout=30,
                                       context=ssl._create_unverified_context())
    conn.request("POST", "/open-apis/im/v1/images",
                 body=body,
                 headers={
                     "Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"
                 })
    resp = conn.getresponse()
    result = json.loads(resp.read().decode())
    return result.get("data", {}).get("image_key", "")


# ═══════════════════════════════════════
# 5. 推送消息到飞书
# ═══════════════════════════════════════

def send_feishu_text(token, text):
    content = json.dumps({"text": text})
    payload = json.dumps({
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": content
    }).encode()
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    return json.loads(resp.read())


def send_feishu_image(token, image_key):
    content = json.dumps({"image_key": image_key})
    payload = json.dumps({
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "image",
        "content": content
    }).encode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    return json.loads(resp.read())


# ═══════════════════════════════════════
# 6. 记录日志
# ═══════════════════════════════════════

def append_log(scene_desc, img_name):
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    if not LOG_FILE.exists() or date_str not in LOG_FILE.read_text():
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## {date_str}\n\n")

    entry = f"""### {time_str}
![](img_name)

{scene_desc}

"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


# ═══════════════════════════════════════
# Main
# ═══════════════════════════════════════

def main():
    now = datetime.now()
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y-%m-%d")

    print(f"🏠 房间观察 - {date_str} {time_str}")

    # 1. 拍照
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    img_name = f"room_{timestamp}.jpg"
    img_path = DATA_DIR / img_name

    print("📷 拍照...", end="", flush=True)
    ok = camera_capture(img_path)
    if not ok:
        print(" ❌")
        return
    # camera_capture now handles its own print status (✅ or error)
    if not (DATA_DIR / img_name).exists():
        return

    # 2. 分析
    print("🔍 分析...", end="", flush=True)
    try:
        scene = analyze_room(img_path)
        print(" ✅")
    except Exception as e:
        print(f" ❌ 分析失败: {e}")
        scene = f"⚠️ 拍照成功但分析失败（可能是光线或网络问题），图片已保存: {img_name}"

    # 3. 记录日志
    append_log(scene, img_name)
    print("📝 已记录")

    # 4. 推送飞书
    print("📤 推送飞书...", end="", flush=True)
    try:
        token = get_feishu_token()
        if not token:
            print(" ❌ 获取token失败")
            return

        # 先发图片
        image_key = upload_feishu_image(token, str(img_path))
        if image_key:
            send_feishu_image(token, image_key)
        else:
            print(" ⚠ 图片上传失败")

        # 再发文字描述
        msg = (
            f"🏠 房间观察报告\n"
            f"📅 {date_str} {time_str}\n\n"
            f"{scene}\n"
        )
        send_feishu_text(token, msg)
        print(" ✅")
    except Exception as e:
        print(f" ❌ {e}")
        return

    print(f"\n📋 {scene}\n")


if __name__ == "__main__":
    main()
