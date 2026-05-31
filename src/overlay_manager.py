"""OverlayManager: 管理多个 DynamicIsland 悬浮窗实例，自动堆叠和清理"""
from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtWidgets import QApplication

try:
    from .overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from .protocol import StatusMessage, CAPSULE_GAP, MAX_VISIBLE_OVERLAYS, IDLE_HIDE_TIMEOUT_MS
except ImportError:
    from overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from protocol import StatusMessage, CAPSULE_GAP, MAX_VISIBLE_OVERLAYS, IDLE_HIDE_TIMEOUT_MS


class OverlayManager(QObject):
    """为每个活跃项目创建独立悬浮窗，垂直堆叠，idle 后自动隐藏"""

    def __init__(self, monitor, parent=None):
        super().__init__(parent)
        self._monitor = monitor
        self._overlays: dict[str, DynamicIsland] = {}
        self._idle_timers: dict[str, QTimer] = {}
        self._visible = True        # 全局显示/隐藏
        self._order: list[str] = []  # 首次出现顺序（决定堆叠）

        # 连接 monitor 信号
        monitor.status_changed.connect(self._on_status_changed)
        monitor.project_idle.connect(self._on_project_idle)
        monitor.projects_updated.connect(self._on_projects_updated)

    # ── 公开 API ──

    def all_projects(self) -> list[str]:
        """所有已知项目"""
        return list(self._overlays.keys())

    def active_projects(self) -> list[str]:
        """当前可见（非隐藏）的项目"""
        return [name for name, ov in self._overlays.items() if ov.isVisible()]

    def get_overlay(self, project: str) -> DynamicIsland | None:
        return self._overlays.get(project)

    def set_global_visible(self, visible: bool):
        """全局显示/隐藏所有悬浮窗"""
        self._visible = visible
        for name, ov in self._overlays.items():
            ov.setVisible(visible)
        if visible:
            self._reposition()

    def update_status_direct(self, project: str, msg: StatusMessage):
        """直接更新某项目状态（供 ReasonixMonitor 等低延迟回调使用）"""
        self._on_status_changed(msg, project)

    def hide_project(self, project: str):
        """强制隐藏某项目（供 ReasonixMonitor on_hide 回调）"""
        if project in self._overlays:
            self._overlays[project].hide()
            self._reposition()

    def show_project(self, project: str):
        """强制显示某项目（供 ReasonixMonitor on_show 回调）"""
        if project in self._overlays:
            if self._visible:
                self._overlays[project].show()
                self._reposition()

    def write_test_status(self, status: str, message: str, project: str):
        """手动写入测试状态（供托盘测试菜单）"""
        try:
            from .hooks import write_status_manually
        except ImportError:
            from hooks import write_status_manually
        write_status_manually(status, message, project)

    # ── 信号处理 ──

    def _on_status_changed(self, msg: StatusMessage, project: str):
        is_idle = msg.is_idle()

        # 始终确保 overlay 存在——否则首次 idle 状态时什么都不会显示
        self._ensure_overlay(project)
        ov = self._overlays[project]
        ov.update_status(msg)

        if is_idle:
            # idle：显示（低透明度），然后启动隐藏倒计时
            if self._visible:
                ov.show()
            self._reposition()
            self._schedule_idle_hide(project)
        else:
            # 活跃：取消隐藏计时，确保显示
            self._cancel_idle_timer(project)
            if self._visible:
                ov.show()
            self._reposition()

    def _on_project_idle(self, project: str):
        """monitor 确认项目已 idle（文件 idle 持续 2s）"""
        # 由 _schedule_idle_hide 处理，这里做最终兜底
        if project not in self._idle_timers:
            self._schedule_idle_hide(project)

    def _on_projects_updated(self, projects: list[str]):
        """项目列表变化时不需要特殊处理，status_changed 已覆盖"""
        pass

    # ── Overlay 生命周期 ──

    def _ensure_overlay(self, project: str):
        """确保某项目有对应的 overlay 实例"""
        if project not in self._overlays:
            ov = DynamicIsland()
            ov.set_project(project)
            self._overlays[project] = ov
            # 记录首次出现顺序
            if project not in self._order:
                self._order.append(project)

    def _schedule_idle_hide(self, project: str):
        """idle 后延迟隐藏"""
        if project not in self._overlays:
            return  # 没有 overlay，无需隐藏

        # 取消已有的 timer
        self._cancel_idle_timer(project)

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda p=project: self._do_hide(p))
        timer.start(IDLE_HIDE_TIMEOUT_MS)
        self._idle_timers[project] = timer

    def _cancel_idle_timer(self, project: str):
        if project in self._idle_timers:
            try:
                self._idle_timers[project].stop()
            except RuntimeError:
                pass
            del self._idle_timers[project]

    def _do_hide(self, project: str):
        """真正隐藏 overlay 并重新堆叠"""
        self._idle_timers.pop(project, None)
        if project in self._overlays:
            self._overlays[project].hide()
        self._reposition()

    # ── 堆叠布局 ──

    def _reposition(self):
        """将可见的 overlay 从上到下堆叠"""
        # 按首次出现顺序排列（保持稳定）
        visible_names = [n for n in self._order if n in self._overlays and self._overlays[n].isVisible()]

        # 数量上限
        if len(visible_names) > MAX_VISIBLE_OVERLAYS:
            visible_names = visible_names[:MAX_VISIBLE_OVERLAYS]

        screen = QApplication.primaryScreen().availableGeometry()
        anchor_x = (screen.width() - 400) // 2

        for i, name in enumerate(visible_names):
            y = screen.top() + TOP_MARGIN + i * (CAPSULE_HEIGHT + CAPSULE_GAP)
            self._overlays[name].move(anchor_x, y)
