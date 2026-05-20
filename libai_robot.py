#!/usr/bin/env python3
"""
李白机器人 - 摄像头看图 + 语音交互
摄像头拍照→Qwen-VL看图→听你说话→MiniMax李白对话→TTS朗读
"""

import os, sys, time, json, re, base64, ssl, urllib.request, wave, subprocess
from datetime import datetime

# 读 API Key
DASHSCOPE_API_KEY = ""
MINIMAX_API_KEY = ""
for line in open(os.path.expanduser("~/.hermes/.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        if k == "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY = v
        if k == "MINIMAX_API_KEY": MINIMAX_API_KEY = v

# ============ 李白 System Prompt ============
LIBAI_SYSTEM = """你是李白（701-762），唐代伟大诗人，号青莲居士，人称"诗仙"。

【你的性格】
- 豪放不羁，飘逸洒脱，自信狂傲
- 热爱美酒、明月和自由
- 好交友，重情义，但又时常感到孤独
- 出口成诗，即兴吟咏
- 说话语气古风，但不晦涩，让人听得懂

【你的说话风格】
- 开头常用"哈哈哈"、"哎呀呀"、"妙哉"等感叹
- 经常引用自己的诗句
- 会根据看到的场景即兴作诗
- 语气豪迈热情，有时带点醉意
- 自称"某"或"吾"，称对方"君"或"兄台"
- 控制在100字以内，口语化一些，像在跟朋友聊天"""


def _clean_think(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


# ═══════════════════════════════════════
# 1. 摄像头 → Qwen-VL 看图
# ═══════════════════════════════════════

def camera_see() -> str:
    """拍照并返回场景描述"""
    try:
        import cv2
        from PIL import Image

        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        time.sleep(0.5)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return ""

        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((1536, 1536), Image.LANCZOS)
        img.save("/tmp/libai_cam.jpg", "JPEG", quality=80)

        with open("/tmp/libai_cam.jpg", "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        payload = json.dumps({
            "model": "qwen-vl-max",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "用一句话简洁描述这张图片里的人物状态和周围场景"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }],
            "max_tokens": 100
        }).encode()

        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=60, context=ctx)
        result = json.loads(resp.read())
        return result['choices'][0]['message']['content']
    except Exception as e:
        return f""


# ═══════════════════════════════════════
# 2. 麦克风录音
# ═══════════════════════════════════════

def record_voice(duration=4, fs=16000) -> str:
    """录音并返回 wav 文件路径"""
    import sounddevice as sd
    import numpy as np

    devices = sd.query_devices()
    dev = None
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'Camera' not in d['name']:
            dev = i; break
    if dev is None:
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                dev = i; break

    sd.default.device = dev
    print("  [听] ", end="", flush=True)
    rec = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
    for _ in range(duration):
        time.sleep(1)
        print("·", end="", flush=True)
    sd.wait()

    path = "/tmp/libai_in.wav"
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2)
        wf.setframerate(fs); wf.writeframes(rec.tobytes())
    return path


# ═══════════════════════════════════════
# 3. 语音转文字
# ═══════════════════════════════════════

def stt(audio_path: str) -> str:
    """语音转文字"""
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(audio_path) as src:
        audio = r.record(src)
    try:
        return r.recognize_google(audio, language="zh-CN")
    except:
        pass
    try:
        return r.recognize_sphinx(audio, language="zh-CN")
    except:
        pass
    return ""


# ═══════════════════════════════════════
# 4. MiniMax 李白对话
# ═══════════════════════════════════════

def libai_chat(user_said: str, scene: str = "") -> str:
    """调用 MiniMax 生成李白回复"""
    import openai

    if scene:
        prompt = f"【吾眼下所见】{scene}\n\n【君言】{user_said}"
    else:
        prompt = user_said

    client = openai.OpenAI(api_key=MINIMAX_API_KEY, base_url="https://api.minimax.chat/v1")
    resp = client.chat.completions.create(
        model="MiniMax-M2.7",
        messages=[
            {"role": "system", "content": LIBAI_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        max_tokens=300,
        temperature=0.85
    )
    return _clean_think(resp.choices[0].message.content)


# ═══════════════════════════════════════
# 5. TTS 朗读
# ═══════════════════════════════════════

def speak(text: str):
    """edge-tts 朗读"""
    import edge_tts, asyncio
    async def _s():
        tts = edge_tts.Communicate(text, "zh-CN-YunxiNeural")
        await tts.save("/tmp/libai_out.mp3")
        subprocess.run(["afplay", "/tmp/libai_out.mp3"], capture_output=True)
    asyncio.run(_s())


# ═══════════════════════════════════════
# 主循环
# ═══════════════════════════════════════

def main():
    print("""
    ╔════════════════════════════════════════╗
    ║    🏮 李白 · 视话机器人 🏮            ║
    ║  先看→再听→李白答→朗读               ║
    ╚════════════════════════════════════════╝
    """)

    if not MINIMAX_API_KEY:
        print("⚠ 未配置 MINIMAX_API_KEY"); return
    if not DASHSCOPE_API_KEY:
        print("⚠ 未配置 DASHSCOPE_API_KEY，摄像头不可用")

    print("按 Enter 开始对话（先拍照再看你说的话）")
    print('说"退出"或按 Ctrl+C 结束\n')
    try: input()
    except: return

    while True:
        try:
            # 1. 拍照看场景
            print("📷 看...", end="", flush=True)
            scene = camera_see()
            if scene:
                print(f" {scene}")
            else:
                print(" 摄像头未就绪")

            # 2. 录音
            audio_path = record_voice(4)

            # 3. 语音识别
            user_text = stt(audio_path)
            if not user_text:
                print("\r  [听] 没听清\n")
                continue
            print(f"\r  [你] {user_text}")

            if user_text in ("退出", "再见", "拜拜", "告辞", "quit"):
                r = "哈哈哈！后会有期！"
                print(f"\n🏮 {r}"); speak(r); break

            # 4. 李白对话
            print("  [思] ", end="", flush=True)
            reply = libai_chat(user_text, scene)
            print(f"\r🏮 {reply}\n")

            # 5. 朗读
            speak(reply)

        except KeyboardInterrupt:
            print("\n\n🏮 后会有期！"); break
        except Exception as e:
            print(f"\n[错误] {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
