"""StatusMonitor: 多项目 watchdog 文件监听 + 轮询，状态变化时 emit Qt Signal。

追踪全部项目（不再是单一 active project），每个项目独立 emit 信号。
"""
import os
import json
import time
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

try:
    from .protocol import StatusMessage, AgentStatus
except ImportError:
    from protocol import StatusMessage, AgentStatus


DEFAULT_STATE_DIR = os.path.expanduser("~/.agent-status")
POLL_INTERVAL_MS = 200
IDLE_DETECT_DELAY_SEC = 2.0  # 连续 idle 超过此时间才 emit project_idle


class _StatusHandler(FileSystemEventHandler):
    def __init__(self):
        self.changed = False

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            self.changed = True

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            self.changed = True

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            self.changed = True


class StatusMonitor(QThread):
    """后台线程：监听全部项目状态文件，每个项目独立 emit"""

    # (StatusMessage, project_name) — 任一项目状态变化
    status_changed = pyqtSignal(StatusMessage, str)
    # [project_names] — 项目列表变化
    projects_updated = pyqtSignal(list)
    # project_name — 项目变为活跃（非 idle）
    project_active = pyqtSignal(str)
    # project_name — 项目变为闲置（idle 持续 N 秒）
    project_idle = pyqtSignal(str)

    def __init__(self, state_dir: str = DEFAULT_STATE_DIR):
        super().__init__()
        self.state_dir = Path(state_dir)
        self._running = False
        self._last_key: dict[str, tuple] = {}        # project -> (status, message)
        self._idle_since: dict[str, float] = {}       # project -> idle_start_time

    def list_projects(self) -> list[str]:
        """列出所有有状态文件的项目"""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(
                f.stem for f in self.state_dir.glob("*.json")
                if f.stat().st_size > 0
            )
            return files if files else ["default"]
        except OSError:
            return ["default"]

    def run(self):
        self._running = True
        self.state_dir.mkdir(parents=True, exist_ok=True)

        observer = None
        handler = None
        if HAS_WATCHDOG:
            try:
                handler = _StatusHandler()
                observer = Observer()
                observer.schedule(handler, str(self.state_dir), recursive=False)
                observer.start()
            except Exception:
                observer = None

        # 初始读取：已存在的文件不会触发 watchdog 事件
        self._check_all_files()

        last_project_list = []
        poll_counter = 0

        try:
            while self._running:
                changed = False

                if observer and handler:
                    time.sleep(0.1)
                    if handler.changed:
                        handler.changed = False
                        changed = True
                else:
                    time.sleep(POLL_INTERVAL_MS / 1000)
                    changed = True

                if changed:
                    self._check_all_files()

                # 每 2 秒检测项目列表变化
                poll_counter += 1
                if poll_counter >= (2000 // POLL_INTERVAL_MS if not observer else 20):
                    poll_counter = 0
                    projects = self.list_projects()
                    if projects != last_project_list:
                        last_project_list = projects
                        self.projects_updated.emit(projects)

        finally:
            if observer:
                observer.stop()
                observer.join(timeout=2)

    def _check_all_files(self):
        """读取全部项目状态文件，逐个比较并 emit"""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            state_files = sorted(
                self.state_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime if f.exists() else 0,
                reverse=True
            )

            seen = set()
            now = time.time()

            for state_file in state_files:
                if state_file.stat().st_size == 0:
                    continue
                project = state_file.stem
                seen.add(project)

                try:
                    raw = state_file.read_text(encoding="utf-8").strip()
                    if not raw:
                        continue
                    msg = StatusMessage.from_json(raw)
                except (json.JSONDecodeError, OSError):
                    continue

                is_idle = msg.is_idle()
                key = (msg.status, msg.message)
                prev_key = self._last_key.get(project)

                self._last_key[project] = key

                # 状态变化 → emit
                if key != prev_key:
                    self.status_changed.emit(msg, project)

                    if not is_idle and (prev_key is None or prev_key[0] == "idle"):
                        # idle → active
                        self._idle_since.pop(project, None)
                        self.project_active.emit(project)

                # idle 计时
                if is_idle:
                    if project not in self._idle_since:
                        self._idle_since[project] = now
                    elif now - self._idle_since[project] > IDLE_DETECT_DELAY_SEC:
                        # idle 持续，emit 一次
                        self.project_idle.emit(project)
                        self._idle_since.pop(project, None)
                else:
                    self._idle_since.pop(project, None)

            # 文件被删除的项目 → 视为 idle
            for project in list(self._last_key.keys()):
                if project not in seen:
                    self._last_key.pop(project, None)
                    self._idle_since.pop(project, None)
                    idle_msg = StatusMessage(status="idle", message="")
                    self.status_changed.emit(idle_msg, project)
                    self.project_idle.emit(project)

        except OSError:
            pass

    def stop(self):
        self._running = False
        self.wait(3000)
