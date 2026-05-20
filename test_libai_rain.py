#!/usr/bin/env python3
"""李白情境对话测试 — 下雨天心情不好版"""
import sys
sys.path.insert(0, 'hermesbot/mac/libai')
from libai_personality import LibaiPersonalityEngine, LibaiMood
from datetime import datetime

class MockTTS:
    def __call__(self, text):
        print("[李白]", text)

class MockSerial:
    def __call__(self, data):
        pass

libai = LibaiPersonalityEngine(hermes_client=MockTTS(), serial_sender=MockSerial())

now = datetime.now()
print("时间:", now.strftime("%Y-%m-%d %H:%M"), "- 下午")
print("天气: 下雨")
print("情绪: 心情不太好")
print("问: 有什么有意思的话题?")
print()

libai.poetry_inspiration = 85
libai.loneliness = 60
libai.heroic_level = 50
libai.wine_mood = 30
libai.mood = LibaiMood.POETIC
libai.on_lonely_long()
libai.update_mood()

print("=" * 55)
print("情境感知 - 雨天+忧愁 相关诗句")
print("=" * 55)

for kw in ["雨", "愁", "酒", "月"]:
    results = libai.search_poems(kw, 2)
    for r in results:
        t = r["title"]
        line = r["line"]
        print("  ", t, ":", line)

print()
print("=" * 55)
print("李白人格提示词")
print("=" * 55)
print(libai.get_personality_prompt())
