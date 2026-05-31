"""StatusMonitor: 多项目 watchdog 文件监听 + 轮询，状态变化时 emit Qt Signal"""
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
    from .protocol import StatusMessage
except ImportError:
    from protocol import StatusMessage


DEFAULT_STATE_DIR = os.path.expanduser("~/.agent-status")
POLL_INTERVAL_MS = 200


class _StatusHandler(FileSystemEventHandler):
    def __init__(self):
        self.changed = False

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            self.changed = True

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            self.changed = True


class StatusMonitor(QThread):
    """后台线程：监听多项目状态文件，自动切换活跃项目"""

    # (StatusMessage, project_name)
    status_changed = pyqtSignal(StatusMessage, str)
    # [project_names]
    projects_updated = pyqtSignal(list)

    def __init__(self, state_dir: str = DEFAULT_STATE_DIR):
        super().__init__()
        self.state_dir = Path(state_dir)
        self._active_project = "default"
        self._running = False
        self._last_key: dict[str, tuple] = {}  # project -> (status, message)

    @property
    def active_project(self) -> str:
        return self._active_project

    def set_active_project(self, name: str):
        """手动切换到指定项目"""
        self._active_project = name
        self._last_key.pop(name, None)  # 强制刷新
        self._check_file()

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
                    self._check_file()

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

    def _check_file(self):
        """读取活跃项目的状态文件"""
        try:
            state_file = self.state_dir / f"{self._active_project}.json"

            if not state_file.exists():
                # 活跃项目文件不存在，自动找最近更新的
                projects = self.list_projects()
                if projects and projects != ["default"]:
                    newest = max(
                        projects,
                        key=lambda p: (self.state_dir / f"{p}.json").stat().st_mtime
                    )
                    if newest != self._active_project:
                        self._active_project = newest

                state_file = self.state_dir / f"{self._active_project}.json"

            if not state_file.exists():
                msg = StatusMessage(status="idle")
            else:
                raw = state_file.read_text(encoding="utf-8").strip()
                if not raw:
                    msg = StatusMessage(status="idle")
                else:
                    msg = StatusMessage.from_json(raw)

            key = (msg.status, msg.message)
            if key != self._last_key.get(self._active_project):
                self._last_key[self._active_project] = key
                self.status_changed.emit(msg, self._active_project)

        except (json.JSONDecodeError, OSError):
            pass

    def stop(self):
        self._running = False
        self.wait(3000)
