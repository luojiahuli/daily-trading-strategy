#!/usr/bin/env python3
"""
飞书推送脚本 - 将每日股票推荐推送到飞书群

使用方法:
  1. 在飞书群中: 群设置 → 群机器人 → 添加机器人 → 自定义机器人
  2. 复制 Webhook URL
  3. 填入下方 FEISHU_WEBHOOK_URL
  4. 测试: python3 feishu_push.py --test
  5. 自动推送: python3 feishu_push.py

支持推送:
  - 每日开盘推荐（热点板块 + 推荐个股）
  - 实盘模拟日报（持仓 + 收益）
  - 产业链传导分析结果
"""

import os, sys, json, time
from datetime import datetime

# ============================================================
# ★★★ 在这里填入你的飞书 Webhook URL ★★★
# ============================================================
FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_WEBHOOK_HERE"

# 数据文件路径
TRACKING_LATEST = os.path.join(os.path.dirname(__file__), "tracking_history", "latest.json")
LIVE_TRADING_RESULT = os.path.join(os.path.dirname(__file__), "live_trading", "simulation_result.json")
CHAIN_ANALYSIS = os.path.join(os.path.dirname(__file__), "viz_output", "industry_chain_analysis.json")


def send_feishu(content: str, title: str = "股票策略日报"):
    """发送消息到飞书"""
    if "YOUR_WEBHOOK_HERE" in FEISHU_WEBHOOK_URL:
        print("[!] 请先在 feishu_push.py 中填入 FEISHU_WEBHOOK_URL")
        print(content)
        return False

    try:
        import requests as req
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue"
                },
                "elements": [
                    {"tag": "markdown", "content": content}
                ]
            }
        }
        resp = req.post(FEISHU_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("code") == 0:
                print(f"[+] 飞书推送成功")
                return True
            else:
                print(f"[!] 飞书返回错误: {result}")
        else:
            print(f"[!] HTTP {resp.status_code}")
        return False
    except Exception as e:
        print(f"[!] 推送失败: {e}")
        return False


def build_daily_report() -> str:
    """构建每日推荐报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not os.path.exists(TRACKING_LATEST):
        return f"**{now}**\n暂无推荐数据，请先运行 python3 stock_tracker.py"

    with open(TRACKING_LATEST) as f:
        data = json.load(f)

    lines = [
        f"**📊 股票题材日报 | {data.get('date', now)}**",
        "",
        "---",
        "",
        "**🔥 热点板块 TOP 10**",
        "| 排名 | 板块 | 热度 | 5日涨跌 | 20日涨跌 |",
        "|------|------|------|--------|---------|",
    ]

    for i, b in enumerate(data.get("board_ranking", [])[:10]):
        lines.append(f"| {i+1} | {b['name']} | {b['score']} | {b['pct_5d']:+.2f}% | {b['pct_20d']:+.2f}% |")

    recs = data.get("recommendations", [])
    if recs:
        lines.extend([
            "",
            "**📈 推荐个股 TOP 5**",
            "| 排名 | 个股 | 板块 | 涨幅 | 回测收益 | 胜率 |",
            "|------|------|------|------|---------|------|",
        ])
        for i, r in enumerate(recs[:5]):
            lines.append(f"| {i+1} | {r['name']}({r['code']}) | {r['board']} | {r['inc']:+.1f}% | {r['total_pnl']:+.1f}% | {r['win_rate']}% |")

    lines.extend([
        "",
        "**📋 操作策略**",
        "- 买入: 前一日涨幅2~9%个股，次日开盘买入",
        "- 卖出: 止盈+5% / 止损-3% / 持仓5日",
        "- 仓位: 等权重，最多3只",
        "",
        f"_数据来源: 同花顺 + 聚合数据 | 生成时间: {now}_",
    ])

    return "\n".join(lines)


def build_trading_report() -> str:
    """构建实盘模拟日报"""
    if not os.path.exists(LIVE_TRADING_RESULT):
        return None

    with open(LIVE_TRADING_RESULT) as f:
        data = json.load(f)

    lines = [
        "**💰 实盘模拟日报**",
        "",
        f"初始资金: 100,000",
        f"**最终资产: {data['final_value']:,.2f}**",
        f"**总收益: {data['total_pnl']:+.2f}%** ({data['total_pnl_amount']:+,.2f})",
        "",
        f"交易统计: {data['total_trades']}笔 | 胜率 {data['win_rate']}%",
        "",
        "**📅 每日净值**",
        "| 日期 | 总资产 | 日收益 | 持仓 |",
        "|------|--------|--------|------|",
    ]

    for s in data.get("daily_snapshots", []):
        if isinstance(s, dict):
            lines.append(f"| {s.get('date','')} | {s.get('total_value',0):,.2f} | {s.get('daily_pct',0):+.2f}% | {s.get('position_count',0)} |")

    return "\n".join(lines)


def build_chain_report() -> str:
    """构建产业链推荐"""
    if not os.path.exists(CHAIN_ANALYSIS):
        return None

    with open(CHAIN_ANALYSIS) as f:
        data = json.load(f)

    lines = ["**🔗 产业链传导推荐 TOP 10**", "",
             "| 排名 | 个股 | 产业链 | 涨幅 | 综合分 | 传导分 |",
             "|------|------|--------|------|--------|--------|"]

    for i, r in enumerate(data.get("all_recommendations", [])[:10]):
        lines.append(f"| {i+1} | {r['name']}({r['code']}) | {r.get('chain','')} | {r['inc']:+.1f}% | {r['chain_score']} | {r['influence_score']} |")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞书推送")
    parser.add_argument("--test", action="store_true", help="测试推送")
    parser.add_argument("--trading", action="store_true", help="推送实盘日报")
    parser.add_argument("--chain", action="store_true", help="推送产业链推荐")
    args = parser.parse_args()

    if args.test:
        content = "**✅ 测试消息**\n\n飞书推送配置成功！\n\n_股票策略日报将在每个交易日 08:55 自动推送_"
        send_feishu(content, title="测试推送")
        return

    reports = []

    # 每日推荐
    report = build_daily_report()
    if report:
        reports.append(report)

    # 实盘模拟
    if args.trading:
        tr = build_trading_report()
        if tr:
            reports.append(tr)

    # 产业链
    if args.chain:
        cr = build_chain_report()
        if cr:
            reports.append(cr)

    if not reports:
        content = "**📊 股票策略日报**\n\n暂无数据，请先运行:\n```\npython3 stock_tracker.py\n```"
        send_feishu(content)
        return

    for r in reports:
        send_feishu(r)
        time.sleep(1)


if __name__ == "__main__":
    main()
