#!/usr/bin/env python3
"""
Hermes 心跳监控 — LaunchAgent 版本
完全独立于 Hermes 进程，工作时间每 30 分钟检测一次
"""
import os, json, ssl, urllib.request, socket
from pathlib import Path
from datetime import datetime

# 读 API Key
DASHSCOPE_API_KEY = None
FEISHU_APP_ID = None
FEISHU_APP_SECRET = None
FEISHU_CHAT_ID = None
FEISHU_USER_ID = None

for line in open(os.path.expanduser("~/.hermes/.env")):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        if k == "DASHSCOPE_API_KEY": DASHSCOPE_API_KEY = v
        if k == "FEISHU_APP_ID": FEISHU_APP_ID = v
        if k == "FEISHU_APP_SECRET": FEISHU_APP_SECRET = v
        if k == "FEISHU_CHAT_ID": FEISHU_CHAT_ID = v
        if k == "FEISHU_USER_ID": FEISHU_USER_ID = v

ALIVE_FILE = Path.home() / ".hermes" / ".last_heartbeat"
LOG_FILE   = Path.home() / "workspace" / "hermes_heartbeat.log"
DATA_DIR   = Path.home() / "workspace"
DATA_DIR.mkdir(parents=True, exist_ok=True)

HERMES_PORTS = [18789, 9119, 8787]

# Cooldown: 防止 Hermes 重启后短时间内重复告警（同一事件 10 分钟内不重复告警）
COOLDOWN_FILE = Path.home() / ".hermes" / ".heartbeat_cooldown"
COOLDOWN_SECS = 600


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] [launchd] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_work_hours():
    """判断当前是否工作时间（周一~周五 9:00~18:00）"""
    now = datetime.now()
    weekday = now.weekday()  # 0=周一, 6=周日
    hour = now.hour
    return 0 <= weekday <= 4 and 9 <= hour < 18


def check_cooldown():
    """检查是否在冷却期内"""
    if not COOLDOWN_FILE.exists():
        return False
    try:
        last = datetime.fromisoformat(COOLDOWN_FILE.read_text().strip())
        elapsed = (datetime.now() - last).total_seconds()
        return elapsed < COOLDOWN_SECS
    except Exception:
        return False


def set_cooldown():
    COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_FILE.write_text(datetime.now().isoformat())


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


def feishu_send_text(token, text):
    content = json.dumps({"text": text})
    payload = json.dumps({
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": content
    }).encode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    return json.loads(resp.read())


def feishu_send_mention(token, user_id, text):
    content = json.dumps({
        "text": text,
        "mentions": [{"key": "@", "id": {"type": "user_id", "user_id": user_id}, "name": "用户"}]
    })
    payload = json.dumps({
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": content
    }).encode()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    return json.loads(resp.read())


def check_port(port, timeout=3):
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_hermes_alive():
    open_ports = [str(p) for p in HERMES_PORTS if check_port(p)]
    if open_ports:
        return True, f"端口 {', '.join(open_ports)} 开放"
    return False, f"所有端口 {HERMES_PORTS} 均无响应"


def send_alert(detail):
    now = datetime.now()
    msg = (
        f"🚨 **Hermes 心跳告警**\n"
        f"时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"状态: {detail}\n"
        f"请检查 Mac Mini 是否正常运行！"
    )

    token = get_feishu_token()
    if not token:
        log("❌ 无法获取飞书 Token")
        return

    if FEISHU_USER_ID:
        try:
            feishu_send_mention(token, FEISHU_USER_ID, msg)
            log(f"✅ 告警已发送（@mention）")
            return
        except Exception as e:
            log(f"⚠️ @mention 失败: {e}，尝试普通消息...")
    feishu_send_text(token, msg)
    log(f"✅ 告警已发送")


def main():
    # 1. 判断是否工作时间
    if not is_work_hours():
        return  # 非工作时间，静默退出

    log(f"心跳检测开始...")

    # 2. 检测 Hermes 存活
    alive, detail = check_hermes_alive()

    if alive:
        ALIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALIVE_FILE.write_text(datetime.now().isoformat())
        log(f"✅ Hermes 存活 ({detail})")
        return

    log(f"❌ Hermes 无响应 ({detail})")

    # 3. 检查 cooldown，避免重复告警
    if check_cooldown():
        log(f"⚠️ 在冷却期内（{COOLDOWN_SECS}s），跳过告警")
        return

    # 4. 发送告警
    log(f"发送告警...")
    send_alert(detail)
    set_cooldown()


if __name__ == "__main__":
    main()
