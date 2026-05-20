#!/usr/bin/env python3
"""推送回测报告截图到飞书"""
import os, sys, json, ssl, urllib.request, http.client, time
from urllib.parse import urlencode

# 读取 .env
ENV_PATH = os.path.expanduser("~/.hermes/.env")
env = {}
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

APP_ID = env.get("FEISHU_APP_ID", "")
APP_SECRET = env.get("FEISHU_APP_SECRET", "")
CHAT_ID = env.get("FEISHU_CHAT_ID", "")

# 最新的回测报告
report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viz_output")
html_files = [f for f in os.listdir(report_dir) if f.startswith("backtest_report_") and f.endswith(".html")]
if not html_files:
    print("❌ 未找到回测报告")
    sys.exit(1)

latest = sorted(html_files)[-1]
html_path = os.path.join(report_dir, latest)
png_path = html_path.replace(".html", ".png")
print(f"📄 报告: {html_path}")

# 1. Playwright 截图
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

print("📸 截图...")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 800, "height": 600})
    page.goto("file://" + os.path.abspath(html_path), wait_until="networkidle")
    page.wait_for_timeout(3000)
    page.screenshot(path=png_path, full_page=True)
    browser.close()

print(f"✅ 截图完成: {png_path} ({os.path.getsize(png_path)//1024}KB)")

# 2. 获取飞书 token
print("🔑 获取飞书 token...")
def get_token():
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    return json.loads(resp.read())["tenant_access_token"]

token = get_token()
print("✅ Token 获取成功")

# 3. 上传图片
print("⬆️ 上传图片...")
boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
with open(png_path, "rb") as f:
    img_data = f.read()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
    f"message\r\n"
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="image"; filename="backtest.png"\r\n'
    f"Content-Type: image/png\r\n\r\n"
).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

conn = http.client.HTTPSConnection("open.feishu.cn", timeout=30,
                                   context=ssl._create_unverified_context())
conn.request("POST", "/open-apis/im/v1/images", body=body,
             headers={"Authorization": f"Bearer {token}",
                      "Content-Type": f"multipart/form-data; boundary={boundary}"})

resp = conn.getresponse()
resp_data = json.loads(resp.read())
img_key = resp_data.get("data", {}).get("image_key", "")
if not img_key:
    print(f"❌ 上传失败: {resp_data}")
    sys.exit(1)
print(f"✅ 图片上传成功: {img_key}")

# 4. 发送图片消息
print("📤 推送飞书...")
payload = json.dumps({
    "receive_id": CHAT_ID,
    "msg_type": "image",
    "content": json.dumps({"image_key": img_key})
}).encode()

req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
    data=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=30, context=ctx)
result = json.loads(resp.read())

if result.get("code") == 0:
    print("✅ 飞书推送成功！")
else:
    print(f"❌ 推送失败: {result}")
