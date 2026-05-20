#!/usr/bin/env python3
"""
截图HTML看板 → 推送飞书图片消息

用法:
  python3 push_stock_viz.py                     # 截图默认看板+推送飞书
  python3 push_stock_viz.py --path xxx.html     # 指定HTML
  python3 push_stock_viz.py --no-send           # 仅截图，不推送

环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET (从~/.hermes/.env自动读取)
"""

import os, sys, json, subprocess, tempfile
from pathlib import Path

HOME = Path.home()
ENV_PATH = HOME / ".hermes" / ".env"
HTML_PATH = Path(__file__).parent / "viz_output" / "backtest_live_dashboard.html"

# ── 读取飞书凭据 ──────────────────────────────────────────────
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

# ── 截图 ──────────────────────────────────────────────────────
def screenshot(html_path: str, output_path: str = None) -> str:
    """用Playwright截图HTML页面, 返回截图路径"""
    if output_path is None:
        output_path = str(Path(html_path).with_suffix(".png"))

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"file://{os.path.abspath(html_path)}", wait_until="networkidle")
        # 等ECharts渲染
        page.wait_for_timeout(2000)
        # 截图: 完整页面而不是视口
        page.screenshot(path=output_path, full_page=True)
        browser.close()

    size = os.path.getsize(output_path)
    print(f"  ✓ 截图: {output_path} ({size/1024:.0f} KB)")
    return output_path

# ── 上传图片到飞书 ────────────────────────────────────────────
def upload_image(token: str, image_path: str) -> str:
    """上传图片到飞书, 返回image_key"""
    import requests
    with open(image_path, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("dashboard.png", f, "image/png")},
            data={"image_type": "message"},
        )
    data = resp.json()
    if data.get("code") == 0:
        img_key = data["data"]["image_key"]
        print(f"  ✓ 图片上传成功: {img_key}")
        return img_key
    else:
        print(f"  ✗ 图片上传失败: {data.get('msg')}")
        return None

# ── 发飞书消息 ────────────────────────────────────────────────
def send_feishu(token: str, chat_id: str, image_key: str, text: str = ""):
    """发送图片消息到飞书聊天"""
    import requests
    content = {"image_key": image_key}
    if text:
        content["text"] = text
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": chat_id,
            "msg_type": "image",
            "content": json.dumps(content),
        },
    )
    data = resp.json()
    if data.get("code") == 0:
        print(f"  ✓ 已推送到飞书 (chat_id: {chat_id[:20]}...)")
    else:
        print(f"  ✗ 推送失败: {data.get('msg')}")

# ── 取 token ──────────────────────────────────────────────────
def get_tenant_token(creds: dict) -> str:
    import requests
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": creds.get("FEISHU_APP_ID", ""),
            "app_secret": creds.get("FEISHU_APP_SECRET", ""),
        },
    )
    return resp.json().get("tenant_access_token", "")

# ── 主流程 ────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=str(HTML_PATH), help="HTML文件路径")
    parser.add_argument("--chat-id", default="os.environ.get("FEISHU_CHAT_ID", "")", help="飞书chat_id")
    parser.add_argument("--no-send", action="store_true", help="仅截图不推送")
    parser.add_argument("--output", help="截图输出路径")
    args = parser.parse_args()

    # 1. 截图
    html_file = args.path
    if not os.path.exists(html_file):
        print(f"✗ HTML文件不存在: {html_file}")
        # 尝试先生成
        subprocess.run([sys.executable, str(Path(__file__).parent / "gen_viz.py")])
        if not os.path.exists(html_file):
            return

    png_path = args.output or str(Path(html_file).with_suffix(".png"))
    print(f"📸 截图: {html_file}")
    screenshot(html_file, png_path)

    if args.no_send:
        print("  --no-send 模式，不推送飞书")
        return

    # 2. 推送到飞书
    creds = load_env()
    if not creds.get("FEISHU_APP_ID") or not creds.get("FEISHU_APP_SECRET"):
        print("✗ 飞书凭据未配置 (FEISHU_APP_ID/FEISHU_APP_SECRET)")
        return

    token = get_tenant_token(creds)
    if not token:
        print("✗ 获取飞书token失败")
        return

    img_key = upload_image(token, png_path)
    if not img_key:
        return

    send_feishu(token, args.chat_id, img_key)
    print(f"\n✅ 完成")

if __name__ == "__main__":
    main()
