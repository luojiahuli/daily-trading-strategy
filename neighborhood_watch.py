#!/usr/bin/env python3
"""
小区观察日记 - 每天定时拍照记录窗外天气和人的变化
"""

import os, sys, time, json, re, base64, ssl, urllib.request
from datetime import datetime
from pathlib import Path

# 读 API Key
DASHSCOPE_API_KEY = ""
for line in open(os.path.expanduser("~/.hermes/.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        if k == "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY = v

# 保存目录
DATA_DIR = Path.home() / "workspace" / "neighborhood_watch"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "daily_log.md"

def camera_capture(save_path):
    """拍照保存"""
    import cv2
    from PIL import Image
    
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    time.sleep(0.5)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return False
    
    cv2.imwrite(str(save_path), frame)
    return True


def analyze_scene(image_path):
    """Qwen-VL 分析画面"""
    from PIL import Image
    
    img = Image.open(image_path)
    img.thumbnail((1536, 1536), Image.LANCZOS)
    img.save("/tmp/neighbor_analyze.jpg", "JPEG", quality=80)
    
    with open("/tmp/neighbor_analyze.jpg", "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    prompt = """请仔细观察这张窗外照片，从以下几个方面描述：
1. 天气状况（晴天/阴天/下雨？光线如何？）
2. 时间感受（大概是早晨/中午/傍晚/夜晚？）
3. 室外活动（看到多少人？在做什么？行人多不多？）
4. 小区环境（树木、花草、建筑有什么特点？）
5. 整体氛围

请用简洁的段落描述。"""
    
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


def append_log(scene_desc, img_name):
    """追加到每日日志"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    
    # 如果今天第一次写，加个标题
    if not LOG_FILE.exists() or date_str not in LOG_FILE.read_text():
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## {date_str}\n\n")
    
    entry = f"""### {time_str}
![窗外]({img_name})

{scene_desc}

"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    
    print(f"✅ 已记录到 {LOG_FILE}")
    print(f"   {scene_desc[:80]}...")


def main():
    print("=" * 50)
    print(f"🏘 小区观察日记 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    # 拍照
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_name = f"window_{timestamp}.jpg"
    img_path = DATA_DIR / img_name
    
    print("📷 拍照中...", end="", flush=True)
    ok = camera_capture(img_path)
    if not ok:
        print(" ❌ 拍照失败")
        return
    print(" ✅")
    
    # 分析
    print("🔍 分析中...", end="", flush=True)
    scene = analyze_scene(img_path)
    print(" ✅")
    
    # 记录
    append_log(scene, img_name)
    
    print("\n📋 今日记录:")
    print(scene)
    print("\n" + "-" * 50)


if __name__ == "__main__":
    main()
