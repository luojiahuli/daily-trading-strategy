#!/usr/bin/env python3
"""生成英语学习日报HTML并推送飞书"""
import os, json, ssl, urllib.request, http.client, base64
from datetime import datetime
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()
app_id = os.environ['FEISHU_APP_ID']
app_secret = os.environ['FEISHU_APP_SECRET']
chat_id = 'os.environ.get("FEISHU_CHAT_ID", "")'

# 读取markdown内容
md_path = '/Users/mac13/workspace/daily_english_20260518.md'
with open(md_path) as f:
    content = f.read()

# 构建HTML
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #0a0a1a; color: #e8e8f0; padding: 0; }}
.container {{ max-width: 720px; margin: 0 auto; padding: 40px 32px; }}
.header {{ text-align: center; padding: 32px 0 24px; border-bottom: 2px solid #2a2a4a; }}
.header h1 {{ font-size: 28px; font-weight: 800; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.header .sub {{ color: #8888aa; font-size: 14px; margin-top: 6px; }}
.header .date {{ display: inline-block; background: #1a1a3a; color: #aaaacc; padding: 4px 16px; border-radius: 20px; font-size: 13px; margin-top: 10px; }}
.badge {{ display: inline-block; background: linear-gradient(135deg, #667eea44, #764ba244); color: #b8b8ff; padding: 3px 12px; border-radius: 12px; font-size: 11px; font-weight: 600; margin-bottom: 8px; border: 1px solid #667eea33; }}
.section {{ background: #12122a; border-radius: 16px; padding: 24px; margin: 20px 0; border: 1px solid #2a2a4a; }}
.section h2 {{ font-size: 18px; font-weight: 700; color: #ccccee; margin-bottom: 12px; }}
.section .source {{ color: #8888bb; font-size: 12px; margin-bottom: 8px; }}
.section .quote {{ background: #0d0d20; border-left: 3px solid #667eea; padding: 12px 16px; border-radius: 0 8px 8px 0; font-style: italic; color: #bbbbdd; font-size: 14px; line-height: 1.6; margin: 12px 0; }}
.vocab-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
.vocab-table th {{ background: #1a1a3a; color: #8888bb; padding: 8px 12px; text-align: left; font-weight: 600; }}
.vocab-table td {{ padding: 8px 12px; border-bottom: 1px solid #1a1a2a; }}
.vocab-table tr:last-child td {{ border-bottom: none; }}
.vocab-table .word {{ font-weight: 600; color: #aabbff; }}
.vocab-table .pron {{ color: #8888aa; font-size: 12px; }}
.vocab-table .meaning {{ color: #bbb; }}
.vocab-table .biz {{ color: #99bbaa; font-size: 12px; }}
.dialog {{ background: #0d0d20; border-radius: 12px; padding: 16px; margin: 12px 0; font-size: 13px; line-height: 1.6; }}
.dialog .speaker-a {{ color: #88bbff; }}
.dialog .speaker-b {{ color: #99cc88; }}
.tip {{ background: #1a1a3a; border-radius: 10px; padding: 12px 16px; margin: 10px 0; font-size: 13px; color: #bbbbdd; border-left: 3px solid #ffcc44; }}
.tip strong {{ color: #ffcc44; }}
.phrases {{ display: grid; gap: 8px; }}
.phrase-item {{ background: #0d0d20; border-radius: 10px; padding: 10px 14px; }}
.phrase-item .num {{ display: inline-block; width: 22px; height: 22px; line-height: 22px; text-align: center; background: #667eea; border-radius: 50%; font-size: 11px; font-weight: 700; color: white; margin-right: 8px; }}
.phrase-item .phrase {{ font-weight: 600; color: #ccddff; }}
.phrase-item .meaning {{ color: #88aacc; font-size: 12px; }}
.phrase-item .example {{ color: #9999bb; font-size: 12px; margin-top: 4px; }}
.quote-block {{ background: linear-gradient(135deg, #1a1a3a, #12122a); border-radius: 16px; padding: 24px; text-align: center; border: 1px solid #2a2a4a; }}
.quote-block .quote-text {{ font-size: 16px; font-style: italic; color: #ccddff; line-height: 1.6; }}
.quote-block .quote-author {{ color: #8888bb; font-size: 13px; margin-top: 10px; }}
.homework {{ background: #1a2a1a; border: 1px dashed #44aa66; border-radius: 16px; padding: 20px; text-align: center; margin: 20px 0; }}
.homework p {{ color: #88ddaa; font-size: 14px; }}
.homework .cta {{ color: #66ddaa; font-size: 16px; font-weight: 700; margin-top: 8px; }}
.footer {{ text-align: center; padding: 24px; color: #555577; font-size: 12px; }}
.tag {{ display: inline-block; background: #1a1a3a; color: #8888bb; padding: 2px 10px; border-radius: 10px; font-size: 11px; margin: 2px; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>🌍 Daily English Digest</h1>
  <div class="sub">每天5分钟，跟外媒学地道商务英语</div>
  <div class="date">📅 2026-05-18 周一 · Vol.1</div>
</div>

<div style="text-align:center; margin: 16px 0;">
  <span class="tag">#外企英语</span>
  <span class="tag">#商务沟通</span>
  <span class="tag">#TheEconomist</span>
  <span class="tag">#BBC</span>
  <span class="tag">#CNN</span>
</div>

<!-- 1. 中东 -->
<div class="section">
  <div class="badge">🇮🇱 国际 · 中东局势</div>
  <h2>"A Stark Warning" — 核电站遭袭，中东局势升级</h2>
  <div class="source">📰 BBC / CNN · May 18, 2026</div>
  
  <div class="quote">"The UAE and Saudi Arabia have issued stark warnings after an attack on a nuclear facility, raising fears of a wider regional escalation."</div>
  
  <table class="vocab-table">
    <tr><th>单词</th><th>意思</th><th>商务用法</th></tr>
    <tr>
      <td><span class="word">stark warning</span><br><span class="pron">/stɑːk ˈwɔːnɪŋ/</span></td>
      <td class="meaning">严厉警告</td>
      <td class="biz">"The board issued a stark warning about Q3 losses."</td>
    </tr>
    <tr>
      <td><span class="word">escalation</span><br><span class="pron">/ˌeskəˈleɪʃn/</span></td>
      <td class="meaning">升级、恶化</td>
      <td class="biz">"We need to avoid escalation of this client dispute."</td>
    </tr>
    <tr>
      <td><span class="word">facility</span><br><span class="pron">/fəˈsɪləti/</span></td>
      <td class="meaning">设施、工厂</td>
      <td class="biz">"Our production facility in Vietnam is fully operational."</td>
    </tr>
    <tr>
      <td><span class="word">allegedly</span><br><span class="pron">/əˈledʒɪdli/</span></td>
      <td class="meaning">据称</td>
      <td class="biz">"He allegedly violated the non-disclosure agreement."</td>
    </tr>
  </table>
  
  <div class="dialog">
    <div><span class="speaker-a">👤 A:</span> "Did you see the news about the nuclear facility attack?"</div>
    <div><span class="speaker-b">👤 B:</span> "Yeah, the UAE's response was a stark warning. Reminds me of the supply chain risks we discussed last quarter."</div>
    <div><span class="speaker-a">👤 A:</span> "Exactly. Any escalation in that region directly impacts our shipping routes."</div>
  </div>
  
  <div class="tip">
    <strong>💡 小贴士：</strong> "issue a warning" 是非常正式的商务搭配，比 "give a warning" 更权威。邮件里写 "We issue a formal warning regarding…" 比 "We want to warn you about…" 专业得多。
  </div>
</div>

<!-- 2. AI -->
<div class="section">
  <div class="badge">🤖 科技 · AI 伦理</div>
  <h2>"Subtly Steer Opinion" — AI正在悄悄操控你的想法？</h2>
  <div class="source">📰 The New Yorker / The Economist · May 18, 2026</div>
  
  <div class="quote">"A new study reveals that AI-generated text, when used to polish social media posts, can subtly steer collective opinion without users even realizing it."</div>
  
  <table class="vocab-table">
    <tr><th>单词</th><th>意思</th><th>商务用法</th></tr>
    <tr>
      <td><span class="word">steer</span><br><span class="pron">/stɪər/</span></td>
      <td class="meaning">引导、操控</td>
      <td class="biz">"We need to steer the meeting toward a decision."</td>
    </tr>
    <tr>
      <td><span class="word">polish</span><br><span class="pron">/ˈpɒlɪʃ/</span></td>
      <td class="meaning">润色、打磨</td>
      <td class="biz">"Can you polish this proposal before the client meeting?"</td>
    </tr>
    <tr>
      <td><span class="word">collective</span><br><span class="pron">/kəˈlektɪv/</span></td>
      <td class="meaning">集体的</td>
      <td class="biz">"This requires a collective effort from all departments."</td>
    </tr>
    <tr>
      <td><span class="word">subtly</span><br><span class="pron">/ˈsʌtəli/</span></td>
      <td class="meaning">微妙地</td>
      <td class="biz">"The price increase was subtly communicated in the contract."</td>
    </tr>
  </table>
  
  <div class="dialog">
    <div><span class="speaker-a">👤 A:</span> "Our marketing team is using AI to polish our LinkedIn posts."</div>
    <div><span class="speaker-b">👤 B:</span> "That's interesting, but did you see the study about AI steering public opinion?"</div>
    <div><span class="speaker-a">👤 A:</span> "Yeah, it's subtle but real. We should be transparent when content is AI-assisted."</div>
    <div><span class="speaker-b">👤 B:</span> "Agreed. In B2B, trust is everything."</div>
  </div>
  
  <div class="tip">
    <strong>💡 小贴士：</strong> "subtle" 在商务英语中很常用。"a subtle difference"（微妙的差异）比 "a small difference" 更高级。否定时用 "not subtle at all" 非常地道。
  </div>
</div>

<!-- 3. 趣闻 -->
<div class="section">
  <div class="badge">👽 趣闻 · 科技圈热聊</div>
  <h2>"Extraterrestrial Species" — CIA研究员爆料：4种外星生物</h2>
  <div class="source">📰 NBC News / New York Post · May 18, 2026</div>
  
  <div class="quote">"A former CIA-funded researcher has claimed that the US government has identified four distinct extraterrestrial species — and some are 'strikingly humanoid'."</div>
  
  <table class="vocab-table">
    <tr><th>单词</th><th>意思</th><th>商务用法</th></tr>
    <tr>
      <td><span class="word">extraterrestrial</span><br><span class="pron">/ˌekstrətəˈrestriəl/</span></td>
      <td class="meaning">外星（的）</td>
      <td class="biz">日常不常用，但在debrief场合用这个词很霸气</td>
    </tr>
    <tr>
      <td><span class="word">distinct</span><br><span class="pron">/dɪˈstɪŋkt/</span></td>
      <td class="meaning">不同的、明显的</td>
      <td class="biz">"We identified three distinct market segments."</td>
    </tr>
    <tr>
      <td><span class="word">allegation</span><br><span class="pron">/ˌæləˈɡeɪʃn/</span></td>
      <td class="meaning">指称、指控</td>
      <td class="biz">"The allegation of fraud was never proven."</td>
    </tr>
  </table>
  
  <div class="dialog">
    <div><span class="speaker-a">👤 A:</span> "Did you catch that alien story? CIA-funded researcher claimed four species."</div>
    <div><span class="speaker-b">👤 B:</span> "Haha, trending everywhere. But in our line of work, 'allegations' need hard evidence."</div>
    <div><span class="speaker-a">👤 A:</span> "True. Reminds me — the client's allegation about our delivery delay needs a formal response."</div>
  </div>
  
  <div class="tip">
    <strong>💡 小贴士：</strong> "allegation" 是正式用词，常用于法律和合规场景。"The allegation remains unsubstantiated"（该指控尚未被证实）—— 这句在外企邮件和法律文件中非常高频。
  </div>
</div>

<!-- 地道短语 -->
<div class="section">
  <div class="badge">🔥 高频 · 外企黑话</div>
  <h2>本周必懂的商务短语 TOP 5</h2>
  
  <div class="phrases">
    <div class="phrase-item">
      <span class="num">1</span> <span class="phrase">on the same page</span>
      <span class="meaning">—— 达成共识</span>
      <div class="example">"Let's make sure we're on the same page before the presentation."</div>
    </div>
    <div class="phrase-item">
      <span class="num">2</span> <span class="phrase">circle back</span>
      <span class="meaning">—— 回头再讨论</span>
      <div class="example">"Let me circle back to you on that after I check with legal."</div>
    </div>
    <div class="phrase-item">
      <span class="num">3</span> <span class="phrase">touch base</span>
      <span class="meaning">—— 联系、碰头</span>
      <div class="example">"I'll touch base with the team to get an update."</div>
    </div>
    <div class="phrase-item">
      <span class="num">4</span> <span class="phrase">ballpark figure</span>
      <span class="meaning">—— 大概数字</span>
      <div class="example">"Can you give me a ballpark figure for the budget?"</div>
    </div>
    <div class="phrase-item">
      <span class="num">5</span> <span class="phrase">loop someone in</span>
      <span class="meaning">—— 拉人进群/抄送</span>
      <div class="example">"Please loop in Sarah from procurement on this thread."</div>
    </div>
  </div>
</div>

<!-- 金句 -->
<div class="quote-block">
  <div class="quote-text">"The single biggest problem in communication is the illusion that it has taken place."</div>
  <div class="quote-author">— George Bernard Shaw</div>
  <div style="color:#8888bb; font-size:12px; margin-top:12px;">💡 发完邮件不代表沟通完成。用英语跟进时，加一句 <span style="color:#aabbff;">"Just checking in — did you get a chance to review?"</span></div>
</div>

<!-- 作业 -->
<div class="homework">
  <p>📝 今日作业（5分钟）</p>
  <div style="color:#88ddaa; font-size:13px; margin-top:8px; line-height:1.6;">
    用今天学的任意3个词汇写一段50词的英语工作邮件<br>
    <span style="color:#66aa88;">回复"我写好了"，我帮你批改！</span>
  </div>
</div>

<div class="footer">
  🌍 Daily English Digest · 每天5分钟，英语更地道<br>
  Created with ❤️ by Hermes Agent
</div>

</div>
</body>
</html>
'''

html_path = '/Users/mac13/workspace/viz_output/daily_english_20260518.html'
with open(html_path, 'w') as f:
    f.write(html)

print(f'HTML saved: {html_path}')

# 截图
png_path = '/Users/mac13/workspace/viz_output/daily_english_20260518.png'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 800, 'height': 600})
    page.goto('file://' + os.path.abspath(html_path), wait_until='networkidle')
    page.wait_for_timeout(3000)
    page.screenshot(path=png_path, full_page=True)
    browser.close()

png_size = os.path.getsize(png_path)
print(f'Screenshot: {png_path} ({png_size} bytes)')

# 推飞书
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
    data=json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode(),
    headers={'Content-Type': 'application/json'})
token = json.loads(urllib.request.urlopen(req, timeout=30, context=ctx).read())['tenant_access_token']
print('Token OK')

boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
with open(png_path, 'rb') as f:
    img_data = f.read()

parts = []
parts.append('--' + boundary + '\r\n')
parts.append('Content-Disposition: form-data; name="image_type"\r\n\r\n')
parts.append('message\r\n')
parts.append('--' + boundary + '\r\n')
parts.append('Content-Disposition: form-data; name="image"; filename="english_daily.png"\r\n')
parts.append('Content-Type: image/png\r\n\r\n')
body = ''.join(parts).encode() + img_data + ('\r\n--' + boundary + '--\r\n').encode()

conn = http.client.HTTPSConnection('open.feishu.cn', timeout=60,
                                   context=ssl._create_unverified_context())
conn.request('POST', '/open-apis/im/v1/images', body=body,
             headers={'Authorization': 'Bearer ' + token,
                      'Content-Type': 'multipart/form-data; boundary=' + boundary})
resp = conn.getresponse()
upload = json.loads(resp.read())
image_key = upload.get('data', {}).get('image_key', '')
print('Upload code=' + str(upload.get('code', -1)) + ' key=' + image_key[:30])

# 发图片
payload = json.dumps({
    'receive_id': chat_id, 'msg_type': 'image',
    'content': json.dumps({'image_key': image_key})
}).encode()
req2 = urllib.request.Request(
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
    data=payload,
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
resp2 = urllib.request.urlopen(req2, timeout=30, context=ctx)
send_code = json.loads(resp2.read()).get('code', -1)
print('Send code=' + str(send_code))

# 发文字摘要
summary = (
    '🌍 Daily English Digest | 2026-05-18（周一）\n\n'
    '每天5分钟，跟外媒学地道商务英语\n\n'
    '📰 今日三篇：\n'
    '1️⃣ "A Stark Warning" — 中东核电站遭袭，学 escalation / facility\n'
    '2️⃣ "Subtly Steer Opinion" — AI操控舆论，学 steer / polish / subtle\n'
    '3️⃣ "Extraterrestrial Species" — CIA外星人爆料，学 allegation / distinct\n\n'
    '🔥 外企黑话TOP5：on the same page / circle back / touch base / ballpark figure / loop in\n\n'
    '📝 今日作业：用今天3个词写一段50词工作邮件，我帮你批改！'
)
text_payload = json.dumps({
    'receive_id': chat_id, 'msg_type': 'text',
    'content': json.dumps({'text': summary})
}).encode()
req3 = urllib.request.Request(
    'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id',
    data=text_payload,
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
resp3 = urllib.request.urlopen(req3, timeout=30, context=ctx)
text_code = json.loads(resp3.read()).get('code', -1)
print('Text send code=' + str(text_code))
print('All done!')
