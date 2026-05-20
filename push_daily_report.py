#!/usr/bin/env python3
"""推送今日实盘模拟日报到飞书"""
import os, json, urllib.request, ssl, http.client
from dotenv import load_dotenv

load_dotenv()
app_id = os.environ['FEISHU_APP_ID']
app_secret = os.environ['FEISHU_APP_SECRET']
chat_id = 'os.environ.get("FEISHU_CHAT_ID", "")'

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 1. 获取token
req = urllib.request.Request(
    'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
    data=json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode(),
    headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=30, context=ctx)
token = json.loads(resp.read())['tenant_access_token']
print(f'Token OK: {token[:10]}...')

# 2. 上传图片
image_path = '/Users/mac13/workspace/viz_output/stock_daily_20260518.png'
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
with open(image_path, 'rb') as f:
    img_data = f.read()

body_parts = []
body_parts.append(f'--{boundary}\r\n')
body_parts.append('Content-Disposition: form-data; name="image_type"\r\n\r\n')
body_parts.append('message\r\n')
body_parts.append(f'--{boundary}\r\n')
body_parts.append('Content-Disposition: form-data; name="image"; filename="stock_daily.png"\r\n')
body_parts.append('Content-Type: image/png\r\n\r\n')

body = ''.join(body_parts).encode() + img_data + f'\r\n--{boundary}--\r\n'.encode()

conn = http.client.HTTPSConnection('open.feishu.cn', timeout=60,
                                   context=ssl._create_unverified_context())
conn.request('POST', '/open-apis/im/v1/images', body=body,
             headers={'Authorization': f'Bearer {token}',
                      'Content-Type': f'multipart/form-data; boundary={boundary}'})
resp = conn.getresponse()
upload_resp = json.loads(resp.read())
upload_code = upload_resp.get('code', -1)
image_key = upload_resp.get('data', {}).get('image_key', '')
print(f'Upload code={upload_code}, image_key={image_key[:20] if image_key else "NONE"}...')

if not image_key:
    print(f'Upload failed: {upload_resp}')
    exit(1)

# 3. 发送图片
payload = json.dumps({
    'receive_id': chat_id,
    'msg_type': 'image',
    'content': json.dumps({'image_key': image_key})
}).encode()

req = urllib.request.Request(
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
    data=payload,
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req, timeout=30, context=ctx)
send_resp = json.loads(resp.read())
send_code = send_resp.get('code', -1)
print(f'Image send code={send_code}')

# 4. 发送文字摘要
summary = (
    '📊 每日实盘模拟日报 | 2026-05-18（周一）\n\n'
    '今日操作：\n'
    '❌ 止损 千里科技(601777) 持仓5天 -1,035\n'
    '✅ 止盈 英杰电气(300820) 持仓2天 +926\n'
    '🟢 买入 风华高科(000636)、首华燃气(300483)、华兰股份(301093)\n\n'
    '当前持仓3只 | 总资产 ¥101,273 | 累计收益 +1.27%'
)

text_payload = json.dumps({
    'receive_id': chat_id,
    'msg_type': 'text',
    'content': json.dumps({'text': summary})
}).encode()

req2 = urllib.request.Request(
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
    data=text_payload,
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
resp2 = urllib.request.urlopen(req2, timeout=30, context=ctx)
text_code = json.loads(resp2.read()).get('code', -1)
print(f'Text send code={text_code}')
print('Done!')
