"""AgentStatusOverlay 入口：系统托盘 + 多项目灵动岛悬浮窗 + 自动堆叠"""
import sys
import os
import atexit
import signal
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import QIcon, QAction

try:
    from .overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from .monitor import StatusMonitor, DEFAULT_STATE_DIR
    from .hooks import configure_hooks, restore_config, write_status_manually
    from .socket_server import SocketServer
    from .reasonix_monitor import ReasonixMonitor
    from .claude_monitor import ClaudeCodeMonitor
    from .overlay_manager import OverlayManager
    from .protocol import MAX_VISIBLE_OVERLAYS
except ImportError:
    from overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from monitor import StatusMonitor, DEFAULT_STATE_DIR
    from hooks import configure_hooks, restore_config, write_status_manually
    from socket_server import SocketServer
    from reasonix_monitor import ReasonixMonitor
    from claude_monitor import ClaudeCodeMonitor
    from overlay_manager import OverlayManager
    from protocol import MAX_VISIBLE_OVERLAYS


def _resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


class TrayManager:
    """系统托盘管理：动态项目菜单 + 全局显示控制"""

    def __init__(self, app: QApplication, overlay_mgr: OverlayManager,
                 monitor: StatusMonitor):
        self.app = app
        self.overlay_mgr = overlay_mgr
        self.monitor = monitor
        self._show_all = True
        self._project_actions: list[QAction] = []

        icon_path = _resource_path("resources/icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("AgentStatusOverlay")
        self._build_menu()
        self.tray.show()

    def _build_menu(self):
        menu = QMenu()

        # 标题
        menu.addAction("AgentStatusOverlay").setEnabled(False)
        menu.addSeparator()

        # 全局显示/隐藏
        show_action = QAction("显示悬浮窗", menu)
        show_action.setCheckable(True)
        show_action.setChecked(self._show_all)
        show_action.triggered.connect(self._toggle_global_visible)
        menu.addAction(show_action)

        # ── 活跃项目列表 ──
        menu.addSeparator()
        active = self.overlay_mgr.active_projects()
        all_projects = self.overlay_mgr.all_projects()

        if active:
            menu.addAction(f"📁 活跃 ({len(active)}/{min(len(all_projects), MAX_VISIBLE_OVERLAYS)})").setEnabled(False)
        else:
            menu.addAction("📁 项目（空闲中）").setEnabled(False)

        self._project_actions.clear()
        display_projects = all_projects[:MAX_VISIBLE_OVERLAYS] if all_projects else []
        for p in display_projects:
            is_active = p in active
            icon = "🟢" if is_active else "⚫"
            action = QAction(f"  {icon} {p}", menu)
            action.setEnabled(False)
            self._project_actions.append(action)
            menu.addAction(action)

        if len(all_projects) > MAX_VISIBLE_OVERLAYS:
            menu.addAction(f"  ... 共 {len(all_projects)} 个项目").setEnabled(False)

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
        # 选择一个目标项目写入测试数据
        target = (active[0] if active
                  else all_projects[0] if all_projects
                  else "default")

        status_menu.addAction(f"→ {target}").setEnabled(False)
        status_menu.addSeparator()

        for status, label in test_states:
            action = QAction(f"{label} ({status})", menu)
            action.triggered.connect(
                lambda checked, s=status, l=label, p=target:
                    self.overlay_mgr.write_test_status(s, l, p)
            )
            status_menu.addAction(action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _toggle_global_visible(self, checked: bool):
        self._show_all = checked
        self.overlay_mgr.set_global_visible(checked)

    def on_status_changed(self, msg, project: str):
        """状态变化 → 刷新托盘菜单"""
        self._build_menu()

    def refresh_projects(self, projects: list[str]):
        """项目列表变化 → 刷新托盘菜单"""
        self._build_menu()


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

    # ── 状态监听（追踪全部项目）──
    monitor = StatusMonitor()
    monitor.start()

    # ── 多窗口管理器 ──
    overlay_mgr = OverlayManager(monitor)

    # ── Socket 推送服务 ──
    socket_srv = SocketServer()
    socket_srv.start()

    # ── Reasonix Code 事件监控（低延迟回调 → OverlayManager）──
    reasonix_mon = ReasonixMonitor(
        on_status=lambda msg: overlay_mgr.update_status_direct("reasonix", msg),
        on_hide=lambda: overlay_mgr.hide_project("reasonix"),
        on_show=lambda: overlay_mgr.show_project("reasonix"),
    )
    reasonix_mon.start()

    # ── Claude Code 事件监控（直接读 session 文件，不依赖 hooks）──
    claude_mon = ClaudeCodeMonitor(
        on_status=lambda msg: overlay_mgr.update_status_direct("TianqinWu", msg),
        project="TianqinWu",
    )
    claude_mon.start()

    # ── 系统托盘 ──
    tray_mgr = TrayManager(app, overlay_mgr, monitor)
    monitor.status_changed.connect(tray_mgr.on_status_changed)
    monitor.projects_updated.connect(tray_mgr.refresh_projects)

    # ── 运行标记（供 Claude Code hook PID 存活检测）──
    RUNNING_MARKER = os.path.join(
        os.path.expanduser("~"), ".agent-status", ".overlay.running"
    )
    os.makedirs(os.path.dirname(RUNNING_MARKER), exist_ok=True)
    with open(RUNNING_MARKER, "w") as _f:
        _f.write(str(os.getpid()))

    # ── 退出清理 ──
    def on_quit():
        monitor.stop()
        socket_srv.stop()
        reasonix_mon.stop()
        claude_mon.stop()
        restore_config()
        try:
            Path(RUNNING_MARKER).unlink(missing_ok=True)
        except Exception:
            pass

    app.aboutToQuit.connect(on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
