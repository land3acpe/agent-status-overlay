"""ReasonixMonitor: 监控 Reasonix Code 会话事件，自动推导并推送状态"""
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

DEFAULT_SESSIONS_DIR = os.path.expanduser("~/.reasonix/sessions")
IDLE_TIMEOUT_SEC = 5       # 无事件后 idle 等待
SHORT_IDLE_SEC = 3         # model.final 后快速回落 idle
THINKING_TIMEOUT_SEC = 60
AUTO_HIDE_SEC = 300        # 无活动 5 分钟后通知 overlay 隐藏
POLL_INTERVAL = 0.1

TOOL_STATUS_MAP = {
    "read_file": "reading", "list_directory": "reading",
    "directory_tree": "reading", "search_files": "reading",
    "search_content": "reading", "glob": "reading",
    "get_file_info": "reading", "get_symbols": "reading",
    "find_in_code": "reading", "explore": "reading",
    "research": "reading", "recall_memory": "reading",
    "web_search": "reading", "web_fetch": "reading",
}


class ReasonixMonitor:
    """监控 Reasonix 事件文件，自动推送状态到 agent-status"""

    def __init__(self, sessions_dir: str = DEFAULT_SESSIONS_DIR,
                 on_status: Callable[[StatusMessage], None] | None = None,
                 on_hide: Callable[[], None] | None = None,
                 on_show: Callable[[], None] | None = None):
        self.sessions_dir = Path(sessions_dir)
        self._on_status = on_status
        self._on_hide = on_hide
        self._on_show = on_show
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_event_time = time.time()
        self._thinking_since = 0.0
        self._expecting_output = False
        self._final_just_happened = False
        self._is_idle = False
        self._hidden = False
        self._files_pos: dict[str, int] = {}

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        self._infer_initial_state()

        while self._running:
            try:
                event_files = sorted(
                    self.sessions_dir.glob("*.events.jsonl"),
                    key=lambda f: f.stat().st_mtime, reverse=True
                )
                for ef in event_files:
                    self._tail_file(ef)

                now = time.time()
                if self._expecting_output:
                    if now - self._thinking_since > THINKING_TIMEOUT_SEC:
                        self._expecting_output = False
                        self._push("idle", "空闲")
                else:
                    timeout = SHORT_IDLE_SEC if self._final_just_happened else IDLE_TIMEOUT_SEC
                    if now - self._last_event_time > timeout:
                        self._final_just_happened = False
                        self._push("idle", "空闲")

                # 自动隐藏：长时间无活动
                inactive = now - self._last_event_time
                if inactive > AUTO_HIDE_SEC and not self._hidden:
                    self._hidden = True
                    if self._on_hide:
                        self._on_hide()

                time.sleep(POLL_INTERVAL)
            except OSError:
                time.sleep(1)

    def _infer_initial_state(self):
        """启动时读取最近事件推断当前状态"""
        try:
            event_files = sorted(
                self.sessions_dir.glob("*.events.jsonl"),
                key=lambda f: f.stat().st_mtime, reverse=True
            )
            if not event_files:
                return
            latest = event_files[0]
            with open(latest, "r", encoding="utf-8") as f:
                lines = f.readlines()[-20:]
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    ts_str = event.get("ts", "")
                    if ts_str:
                        et = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if (datetime.now(timezone.utc) - et).total_seconds() > 60:
                            continue
                    etype = event.get("type", "")
                    if etype in ("tool.result", "tool.call",
                                 "model.final", "status"):
                        self._expecting_output = True
                        self._thinking_since = time.time()
                        self._push("thinking", "推理中...")
                    break
                except (json.JSONDecodeError, ValueError):
                    continue
        except OSError:
            pass

    def _tail_file(self, filepath: Path):
        """读取文件新行"""
        key = str(filepath)
        try:
            size = filepath.stat().st_size
            if key not in self._files_pos:
                self._files_pos[key] = size
            last_pos = self._files_pos[key]

            if size < last_pos:
                last_pos = 0
            if size == last_pos:
                return

            with open(filepath, "r", encoding="utf-8") as f:
                f.seek(last_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        self._handle_event(event)
                    except json.JSONDecodeError:
                        pass
            self._files_pos[key] = size
        except OSError:
            pass

    def _handle_event(self, event: dict):
        etype = event.get("type", "")
        # 跳过 30 秒以上的旧事件
        try:
            ts = event.get("ts", "")
            if ts:
                et = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - et).total_seconds() > 30:
                    return
        except (ValueError, TypeError):
            pass

        self._last_event_time = time.time()
        self._final_just_happened = False

        if self._hidden:
            self._hidden = False
            if self._on_show:
                self._on_show()

        if etype in ("tool.result", "tool.call"):
            self._expecting_output = True
            self._thinking_since = time.time()
            self._push("thinking", "分析工具结果中...")

        elif etype in ("tool.intent", "tool.dispatched", "tool.preparing"):
            tool_name = event.get("name", "")
            status = TOOL_STATUS_MAP.get(tool_name, "executing")
            self._push(status, tool_name or status)

        elif etype == "model.turn.started":
            self._expecting_output = True
            self._thinking_since = time.time()
            self._push("thinking", "分析用户输入中...")

        elif etype == "model.final":
            self._expecting_output = False
            self._final_just_happened = True
            self._push("thinking", "准备下一步...")

        elif etype == "status":
            text = event.get("text", "")
            if "思考" in text:
                self._expecting_output = True
                self._thinking_since = time.time()
                self._push("thinking", text[:60])
            elif "等待" in text:
                self._push("waiting", text[:60])
            elif "错误" in text or "失败" in text:
                self._push("error", text[:60])

    def _push(self, status: str, message: str):
        if status == "idle" and self._is_idle:
            return  # 已经是 idle，不重复推送
        self._is_idle = (status == "idle")
        msg = StatusMessage(
            status=status, message=message,
            timestamp=datetime.now().isoformat(),
        )
        if self._on_status:
            self._on_status(msg)
