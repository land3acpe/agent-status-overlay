"""AgentStatusOverlay 入口：系统托盘 + 灵动岛悬浮窗 + 多项目"""
import sys
import os
import atexit
import signal

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import QIcon, QAction

try:
    from .overlay import DynamicIsland
    from .monitor import StatusMonitor, DEFAULT_STATE_DIR
    from .hooks import configure_hooks, restore_config, write_status_manually
    from .socket_server import SocketServer
    from .reasonix_monitor import ReasonixMonitor
except ImportError:
    from overlay import DynamicIsland
    from monitor import StatusMonitor, DEFAULT_STATE_DIR
    from hooks import configure_hooks, restore_config, write_status_manually
    from socket_server import SocketServer
    from reasonix_monitor import ReasonixMonitor


def _resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


class TrayManager:
    """系统托盘管理：动态项目菜单"""

    def __init__(self, app: QApplication, overlay: DynamicIsland,
                 monitor: StatusMonitor):
        self.app = app
        self.overlay = overlay
        self.monitor = monitor
        self._project_actions: list[QAction] = []

        icon_path = _resource_path("resources/icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("AgentStatusOverlay")
        self._build_menu()
        self.tray.show()

    def _build_menu(self):
        menu = QMenu()

        # 当前状态
        menu.addAction("AgentStatusOverlay").setEnabled(False)
        menu.addSeparator()

        # 显示/隐藏
        show_action = QAction("显示悬浮窗", menu)
        show_action.setCheckable(True)
        show_action.setChecked(True)
        show_action.triggered.connect(
            lambda checked: self.overlay.setVisible(checked)
        )
        menu.addAction(show_action)

        # ── 项目列表 ──
        menu.addSeparator()
        menu.addAction("📁 项目").setEnabled(False)

        projects = self.monitor.list_projects()
        self._project_actions.clear()
        for p in projects:
            action = QAction(f"  {p}", menu)
            action.setCheckable(True)
            if p == self.monitor.active_project:
                action.setChecked(True)
            action.triggered.connect(
                lambda checked, name=p: self._switch_project(name)
            )
            self._project_actions.append(action)
            menu.addAction(action)

        # ── 测试状态 ──
        menu.addSeparator()
        status_menu = menu.addMenu("🧪 测试状态")
        test_states = [
            ("idle", "空闲"),
            ("thinking", "思考中"),
            ("coding", "编码中"),
            ("planning", "计划中"),
            ("reading", "读取中"),
            ("executing", "执行中"),
            ("waiting", "等待确认"),
            ("error", "错误"),
        ]
        for status, label in test_states:
            action = QAction(f"{label} ({status})", menu)
            action.triggered.connect(
                lambda checked, s=status, l=label: write_status_manually(
                    s, l, self.monitor.active_project
                )
            )
            status_menu.addAction(action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _switch_project(self, name: str):
        self.monitor.set_active_project(name)
        self.overlay.set_project(name)
        self._build_menu()  # 刷新勾选状态

    def refresh_projects(self, projects: list[str]):
        """projects_updated 回调：重建菜单"""
        self._build_menu()

    def on_status_changed(self, msg, project: str):
        """状态变化回调"""
        if not self.overlay.isVisible():
            self.overlay.show()
        self.overlay.set_project(project)
        self.overlay.update_status(msg)


def main():
    # ── 自动配置 Claude Code hooks ──
    try:
        configure_hooks()
    except Exception:
        pass

    atexit.register(restore_config)
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

    # ── Qt 应用 ──
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("AgentStatusOverlay")

    # ── 灵动岛悬浮窗 ──
    overlay = DynamicIsland()
    overlay.show()

    # ── 状态监听（多项目自动检测）──
    monitor = StatusMonitor()
    monitor.start()

    # ── Socket 推送服务 ──
    socket_srv = SocketServer()
    socket_srv.start()

    # ── Reasonix Code 事件监控（直接回调 overlay，低延迟）──
    reasonix_mon = ReasonixMonitor(
        on_status=lambda msg: (overlay.set_project("reasonix"),
                               overlay.update_status(msg)),
        on_hide=overlay.hide,
        on_show=overlay.show,
    )
    reasonix_mon.start()

    # ── 系统托盘 ──
    tray_mgr = TrayManager(app, overlay, monitor)
    monitor.status_changed.connect(tray_mgr.on_status_changed)
    monitor.projects_updated.connect(tray_mgr.refresh_projects)

    # ── 退出清理 ──
    def on_quit():
        monitor.stop()
        socket_srv.stop()
        reasonix_mon.stop()
        restore_config()

    app.aboutToQuit.connect(on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
