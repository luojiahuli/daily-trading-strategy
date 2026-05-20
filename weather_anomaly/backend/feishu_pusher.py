"""
飞书推送模块
"""
import os
import logging
from typing import Optional
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# 飞书 API 地址
FEISHU_API = "https://open.feishu.cn/open-apis"


def get_feishu_config() -> dict:
    """从环境变量获取飞书配置"""
    return {
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "chat_id": os.getenv("FEISHU_CHAT_ID", ""),
    }


async def get_tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取 tenant_access_token"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret}
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("tenant_access_token")
    except Exception as e:
        logger.error(f"获取飞书token失败: {e}")
    return None


async def send_message(token: str, chat_id: str, msg_type: str, content: dict) -> bool:
    """发送消息到飞书群"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{FEISHU_API}/im/v1/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                params={"receive_id_type": "chat_id"},
                json={
                    "receive_id": chat_id,
                    "msg_type": msg_type,
                    "content": content,
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("code") == 0
            logger.warning(f"飞书消息发送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"飞书消息发送异常: {e}")
    return False


async def upload_image(token: str, image_path: str) -> Optional[str]:
    """上传图片到飞书，返回 image_key"""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(image_path, "rb") as f:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/images",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"image_type": "message"},
                    files={"image": f}
                )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {}).get("image_key")
    except Exception as e:
        logger.error(f"飞书图片上传失败: {e}")
    return None


def build_anomaly_card(anomalies: list) -> dict:
    """构建异常天气卡片"""
    if not anomalies:
        return {
            "msg_type": "text",
            "content": {"text": "今日无异常天气，祖国大地一片祥和 🎉"}
        }

    # 按严重程度分组
    severe = [a for a in anomalies if a.get("severity", 0) >= 2]
    moderate = [a for a in anomalies if a.get("severity", 0) == 1]

    lines = [
        f"🌡️ 中国异常天气预报",
        f"📅 {datetime.now().strftime('%Y年%m月%d日 %H:%M')}",
        f"监测城市: {len(anomalies)}个 | 异常: {len(severe)}个严重 + {len(moderate)}个轻度",
        "",
        "━━━━━━━━━━━━━━━",
    ]

    # 严重异常
    if severe:
        lines.append("🔴 严重异常")
        for a in severe[:5]:  # 最多显示5个
            icon = a.get("anomaly_icon", "⚠️")
            lines.append(
                f"{icon} {a.get('city_name','')}/{a.get('province','')} "
                f"{a.get('anomaly_name','')} "
                f"当前{a.get('current_value','')} "
                f"(均值{a.get('historical_mean','')})"
            )
        lines.append("")

    # 轻度异常
    if moderate:
        lines.append("🟡 轻度异常")
        for a in moderate[:5]:
            icon = a.get("anomaly_icon", "⚠️")
            lines.append(
                f"{icon} {a.get('city_name','')}/{a.get('province','')} "
                f"{a.get('anomaly_name','')} "
                f"当前{a.get('current_value','')} "
                f"(均值{a.get('historical_mean','')})"
            )
        lines.append("")

    # 提示
    lines.extend([
        "━━━━━━━━━━━━━━━",
        "📊 数据基于历史均值Z-score检测",
        "🔍 查看详情请访问可视化大屏",
    ])

    text = "\n".join(lines)
    return {
        "msg_type": "text",
        "content": {"text": text}
    }


class FeishuPusher:
    """飞书推送器"""

    def __init__(self, app_id: str = "", app_secret: str = "", chat_id: str = ""):
        self.app_id = app_id or get_feishu_config().get("app_id", "")
        self.app_secret = app_secret or get_feishu_config().get("app_secret", "")
        self.chat_id = chat_id or get_feishu_config().get("chat_id", "")
        self._token: Optional[str] = None

    async def ensure_token(self) -> bool:
        """确保有有效的token"""
        if self._token:
            return True
        self._token = await get_tenant_access_token(self.app_id, self.app_secret)
        return self._token is not None

    async def push_anomaly_report(self, anomalies: list) -> bool:
        """推送异常天气报告"""
        if not self.app_id or not self.app_secret or not self.chat_id:
            logger.warning("飞书配置不完整，跳过推送")
            return False

        if not await self.ensure_token():
            logger.error("无法获取飞书token")
            return False

        card = build_anomaly_card(anomalies)
        return await send_message(self._token, self.chat_id, card["msg_type"], card["content"])

    async def push_screenshot(self, image_path: str, caption: str = "") -> bool:
        """推送截图"""
        if not self.app_id or not self.app_secret or not self.chat_id:
            logger.warning("飞书配置不完整，跳过推送")
            return False

        if not os.path.exists(image_path):
            logger.warning(f"截图文件不存在: {image_path}")
            return False

        if not await self.ensure_token():
            logger.error("无法获取飞书token")
            return False

        # 上传图片
        image_key = await upload_image(self._token, image_path)
        if not image_key:
            logger.error("图片上传失败")
            return False

        # 发送图片消息
        content = {"image_key": image_key}
        if caption:
            # 先发图片
            ok = await send_message(self._token, self.chat_id, "image", content)
            if ok:
                # 再发文字
                await send_message(
                    self._token, self.chat_id, "text",
                    {"text": caption}
                )
            return ok

        return await send_message(self._token, self.chat_id, "image", content)


# 全局实例
_pusher: Optional[FeishuPusher] = None


def get_feishu_pusher() -> FeishuPusher:
    global _pusher
    if _pusher is None:
        cfg = get_feishu_config()
        _pusher = FeishuPusher(cfg["app_id"], cfg["app_secret"], cfg["chat_id"])
    return _pusher
