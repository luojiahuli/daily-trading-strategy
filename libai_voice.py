#!/usr/bin/env python3
"""
李白语音机器人 - 麦克风输入 → MiniMax李白对话 → TTS语音输出
"""

import os, sys, time, json, re, wave, subprocess
from datetime import datetime

# 读 API Key
MINIMAX_API_KEY = ""
for line in open(os.path.expanduser("~/.hermes/.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
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
- 语气豪迈热情，有时带点醉意
- 自称"某"或"吾"，称对方"君"或"兄台"
- 控制在100字以内，口语化一些，像在跟朋友聊天"""


def _clean_think(text):
    """去掉 MiniMax 的 <think> 思考标签"""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()


def record_audio(duration=4, fs=16000):
    """用 sounddevice 录音"""
    import sounddevice as sd
    import numpy as np

    devices = sd.query_devices()
    input_device = None
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'Camera' not in d['name']:
            input_device = i
            break
    if input_device is None:
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                input_device = i
                break

    sd.default.device = input_device
    print("  [听] ", end="", flush=True)
    recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
    for i in range(duration):
        time.sleep(0.5)
        print("·", end="", flush=True)
        time.sleep(0.5)
        print("·", end="", flush=True)
    sd.wait()

    path = "/tmp/libai_input.wav"
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(recording.tobytes())
    return path


def speech_to_text(audio_path):
    """语音转文字 - 优先 Google STT"""
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(audio_path) as source:
        audio = r.record(source)

    # 先试 Google
    try:
        return r.recognize_google(audio, language="zh-CN")
    except Exception:
        pass

    # 再试 Sphinx（离线）
    try:
        return r.recognize_sphinx(audio, language="zh-CN")
    except Exception:
        pass

    return ""


def libai_chat(text):
    """MiniMax 李白对话"""
    import openai
    client = openai.OpenAI(api_key=MINIMAX_API_KEY, base_url="https://api.minimax.chat/v1")
    resp = client.chat.completions.create(
        model="MiniMax-M2.7",
        messages=[
            {"role": "system", "content": LIBAI_SYSTEM},
            {"role": "user", "content": text}
        ],
        max_tokens=300,
        temperature=0.85
    )
    return _clean_think(resp.choices[0].message.content)


def tts_speak(text):
    """edge-tts 朗读"""
    import edge_tts, asyncio
    async def _s():
        tts = edge_tts.Communicate(text, "zh-CN-YunxiNeural")
        await tts.save("/tmp/libai_speech.mp3")
        subprocess.run(["afplay", "/tmp/libai_speech.mp3"], capture_output=True)
    asyncio.run(_s())


def main():
    print("""
    ╔══════════════════════════════════╗
    ║     🏮 李白语音机器人 · 启动 🏮  ║
    ║   诗仙降临，与君共语！           ║
    ╚══════════════════════════════════╝
    """)

    if not MINIMAX_API_KEY:
        print("⚠ 未配置 MINIMAX_API_KEY")
        return

    import speech_recognition as sr
    try:
        sr.Microphone()
        mic_ok = True
    except:
        mic_ok = False

    print(f"🎤 麦克风: {'✅ 就绪' if mic_ok else '⚠ 需授权'}")
    print("按 Enter 开始对话（之后自动识别语音）")
    print("输入 /q 退出\n")
    try:
        input()
    except:
        return

    while True:
        try:
            # 1. 录音
            audio_path = record_audio(duration=4)

            # 2. 语音识别
            user_text = speech_to_text(audio_path)
            if not user_text:
                print("\r  [听] (没听清)")
                continue
            print(f"\r  [你] {user_text}")

            if user_text in ("退出", "再见", "拜拜", "告辞"):
                print("\n🏮 哈哈哈！后会有期！")
                tts_speak("哈哈哈！后会有期！")
                break

            # 3. 李白对话
            print("  [思] ", end="", flush=True)
            reply = libai_chat(user_text)
            print(f"\r🏮 {reply}\n")

            # 4. 朗读
            tts_speak(reply)

        except KeyboardInterrupt:
            print("\n\n🏮 某家去也！后会有期！")
            break
        except Exception as e:
            print(f"\n[错误] {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()
