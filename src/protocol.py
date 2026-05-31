"""Agent 状态协议：枚举定义 + JSON schema 校验"""
import json
from enum import Enum
from dataclasses import dataclass, asdict
from typing import Optional


class AgentStatus(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    THINKING = "thinking"
    READING = "reading"
    CODING = "coding"
    EXECUTING = "executing"
    WAITING = "waiting"
    ERROR = "error"


# 状态视觉配置: (图标, 颜色(hex), 动画模式)
STATUS_STYLE = {
    AgentStatus.IDLE:      ("💤", "#6c7086", "static"),
    AgentStatus.PLANNING:  ("📋", "#89b4fa", "pulse"),
    AgentStatus.THINKING:  ("🧠", "#f9e2af", "pulse-fast"),
    AgentStatus.READING:   ("📖", "#94e2d5", "pulse"),
    AgentStatus.CODING:    ("💻", "#a6e3a1", "pulse"),
    AgentStatus.EXECUTING: ("⚡", "#cba6f7", "pulse-fast"),
    AgentStatus.WAITING:   ("⏳", "#f2cdcd", "blink"),
    AgentStatus.ERROR:     ("❌", "#f38ba8", "blink-fast"),
}


@dataclass
class StatusMessage:
    status: str
    message: str = ""
    timestamp: str = ""
    metadata: Optional[dict] = None

    @staticmethod
    def from_json(data: str | dict):
        if isinstance(data, str):
            data = json.loads(data)
        return StatusMessage(
            status=data.get("status", "idle"),
            message=data.get("message", ""),
            timestamp=data.get("timestamp", ""),
            metadata=data.get("metadata"),
        )

    def to_json(self) -> str:
        d = {"status": self.status, "message": self.message,
             "timestamp": self.timestamp}
        if self.metadata:
            d["metadata"] = self.metadata
        return json.dumps(d, ensure_ascii=False)

    def get_status_enum(self) -> AgentStatus:
        try:
            return AgentStatus(self.status)
        except ValueError:
            return AgentStatus.IDLE

    def get_icon(self) -> str:
        return STATUS_STYLE[self.get_status_enum()][0]

    def get_color(self) -> str:
        return STATUS_STYLE[self.get_status_enum()][1]

    def get_anim(self) -> str:
        return STATUS_STYLE[self.get_status_enum()][2]
