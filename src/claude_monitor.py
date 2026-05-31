"""ClaudeCodeMonitor: 监控 Claude Code 会话事件文件，直接推导并推送状态。

绕过 hooks 机制，直接读取 Claude Code 的 session JSONL 和 Reasonix 的事件文件。
"""
import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable

try:
    from .protocol import StatusMessage
    from .hooks import write_status_manually
except ImportError:
    from protocol import StatusMessage
    from hooks import write_status_manually

DEFAULT_SESSIONS_DIR = os.path.expanduser("~/.claude/projects")
DEFAULT_REASONIX_DIR = os.path.expanduser("~/.reasonix/sessions")
POLL_INTERVAL = 0.5
IDLE_TIMEOUT_SEC = 3.0         # 无事件后显示 idle
THINKING_TIMEOUT_SEC = 120     # 长时间无输出也保持 thinking

# 工具名 → 状态映射
TOOL_STATUS_MAP = {
    "read_file": "reading", "list_directory": "reading",
    "directory_tree": "reading", "search_files": "reading",
    "search_content": "reading", "get_file_info": "reading",
    "glob": "reading", "grep": "reading", "find": "reading",
    "read": "reading", "search": "reading", "explore": "reading",
    "web_search": "reading", "web_fetch": "reading",
    "recall_memory": "reading",
    "write_file": "coding", "edit_file": "coding",
    "edit": "coding", "write": "coding",
    "bash": "executing", "shell": "executing",
    "execute_command": "executing",
}

class ClaudeCodeMonitor:
    """监控 Claude Code + Reasonix 事件文件，自动推导状态"""

    def __init__(self,
                 projects_dir: str = DEFAULT_SESSIONS_DIR,
                 reasonix_dir: str = DEFAULT_REASONIX_DIR,
                 on_status: Callable[[StatusMessage], None] | None = None,
                 project: str = "TianqinWu"):
        self.projects_dir = Path(projects_dir)
        self.reasonix_dir = Path(reasonix_dir)
        self._on_status = on_status
        self._project = project
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_event_time = time.time()
        self._is_idle = False
        self._files_pos: dict[str, int] = {}
        self._last_status = "idle"

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            try:
                found_events = False

                # 1. 监控 Reasonix 事件文件（更详细的工具事件）
                reasonix_files = sorted(
                    self.reasonix_dir.glob("code-*.events.jsonl"),
                    key=lambda f: f.stat().st_mtime, reverse=True
                )
                for rf in reasonix_files:
                    if self._tail_reasonix(rf):
                        found_events = True

                # 2. 监控 Claude Code session JSONL（user/assistant 事件）
                project_dir = self.projects_dir / f"C--Users-{self._project}"
                if project_dir.exists():
                    session_files = sorted(
                        project_dir.glob("*.jsonl"),
                        key=lambda f: f.stat().st_mtime, reverse=True
                    )
                    for sf in session_files[:2]:  # 只看最近 2 个 session
                        if self._tail_session(sf):
                            found_events = True

                # 3. 超时 → idle
                now = time.time()
                if not found_events:
                    inactive = now - self._last_event_time
                    if inactive > IDLE_TIMEOUT_SEC and not self._is_idle:
                        self._is_idle = True
                        self._push("idle", "空闲")
                else:
                    self._is_idle = False

                time.sleep(POLL_INTERVAL)

            except OSError:
                time.sleep(2)

    def _tail_reasonix(self, filepath: Path) -> bool:
        """读取 Reasonix 事件文件的增量，返回是否有新事件"""
        key = "r:" + str(filepath)
        try:
            size = filepath.stat().st_size
            if key not in self._files_pos:
                self._files_pos[key] = max(0, size - 4096)  # 从末尾附近开始
            last_pos = self._files_pos[key]
            if size < last_pos:
                last_pos = 0
            if size == last_pos:
                return False

            found = False
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        self._handle_reasonix_event(event)
                        found = True
                    except json.JSONDecodeError:
                        pass
            self._files_pos[key] = size
            return found
        except OSError:
            return False

    def _tail_session(self, filepath: Path) -> bool:
        """读取 Claude Code session JSONL，返回是否有新事件"""
        key = "s:" + str(filepath)
        try:
            size = filepath.stat().st_size
            if key not in self._files_pos:
                self._files_pos[key] = max(0, size - 8192)
            last_pos = self._files_pos[key]
            if size < last_pos:
                last_pos = 0
            if size == last_pos:
                return False

            found = False
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if self._handle_session_event(event):
                            found = True
                    except json.JSONDecodeError:
                        pass
            self._files_pos[key] = size
            return found
        except OSError:
            return False

    def _is_recent(self, event: dict) -> bool:
        """检查事件是否在 30 秒内（过滤旧事件）"""
        ts = event.get("ts", "")
        if ts:
            try:
                et = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - et).total_seconds() < 30
            except (ValueError, TypeError):
                pass
        # 没有时间戳的事件使用文件位置判断（新事件）
        return True

    def _handle_reasonix_event(self, event: dict):
        """处理 Reasonix 格式事件"""
        if not self._is_recent(event):
            return

        etype = event.get("type", "")

        if etype in ("tool.result", "tool.call"):
            self._push("thinking", "分析工具结果中...")

        elif etype in ("tool.intent", "tool.dispatched", "tool.preparing"):
            tool_name = event.get("name", "")
            status = TOOL_STATUS_MAP.get(tool_name, "executing")
            self._push(status, tool_name or status)

        elif etype == "model.turn.started":
            self._push("thinking", "分析用户输入中...")

        elif etype == "model.final":
            self._push("thinking", "准备下一步...")

        elif etype == "status":
            text = event.get("text", "")
            if "思考" in text:
                self._push("thinking", text[:60])
            elif "等待" in text:
                self._push("waiting", text[:60])
            elif "错误" in text or "失败" in text:
                self._push("error", text[:60])

    def _handle_session_event(self, event: dict) -> bool:
        """处理 Claude Code session JSONL 事件"""
        if not self._is_recent(event):
            return False

        etype = event.get("type", "")

        if etype == "user":
            self._push("thinking", "理解用户意图中...")
            return True

        elif etype == "assistant":
            # assistant 事件包含模型输出内容
            msg = event.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                # tool_use blocks
                tool_names = set()
                has_text = False
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            tool_names.add(block.get("name", "tool"))
                        elif block.get("type") == "text":
                            has_text = True
                if tool_names:
                    status = TOOL_STATUS_MAP.get(list(tool_names)[0], "executing")
                    self._push(status, ", ".join(tool_names)[:40])
                elif has_text:
                    self._push("thinking", "生成回复中...")
            return True

        elif etype == "last-prompt":
            self._push("thinking", "分析中...")
            return True

        elif etype == "system":
            subtype = event.get("subtype", "")
            if subtype == "turn_duration":
                pass  # 不改变状态
            return True

        return False

    def _push(self, status: str, message: str):
        """推送状态（去重）"""
        now = time.time()
        self._last_event_time = now

        if status == self._last_status and status != "thinking":
            # 相同非 thinking 状态不重复推送
            return
        self._last_status = status

        msg = StatusMessage(
            status=status, message=message,
            timestamp=datetime.now().isoformat(),
        )
        write_status_manually(status, message, self._project)
        if self._on_status:
            self._on_status(msg)
