#!/usr/bin/env python3
"""
每日异常天气推送
Usage: python scripts/daily_push.py
Crontab: 0 9 * * 1-5
"""
import sys
import os
from datetime import datetime
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.model import get_anomaly_detector
from backend.database import get_database
from backend.feishu_pusher import get_feishu_pusher


async def main():
    print(f"[{datetime.now()}] 开始异常天气分析...")

    detector = get_anomaly_detector()
    db = get_database()
    pusher = get_feishu_pusher()

    # 检测异常
    anomalies = detector.detect_anomalies()
    print(f"发现 {len(anomalies)} 个异常城市")

    # 推送到飞书
    if anomalies:
        success = await pusher.push_anomaly_report(anomalies)
        if success:
            print("飞书推送成功")
        else:
            print("飞书推送失败（可能配置不完整）")
    else:
        print("无异常，跳过推送")


if __name__ == '__main__':
    asyncio.run(main())
