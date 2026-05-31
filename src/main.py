"""AgentStatusOverlay 入口：系统托盘 + 多项目灵动岛悬浮窗 + 自动堆叠"""
import sys
import os
import atexit
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import QIcon, QAction

try:
    from .overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from .monitor import StatusMonitor, DEFAULT_STATE_DIR
    from .hooks import configure_hooks, restore_config
    from .overlay_manager import OverlayManager
except ImportError:
    from overlay import DynamicIsland, CAPSULE_HEIGHT, TOP_MARGIN
    from monitor import StatusMonitor, DEFAULT_STATE_DIR
    from hooks import configure_hooks, restore_config
    from overlay_manager import OverlayManager


def _resource_path(relative_path: str) -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


class TrayManager:
    """极简系统托盘"""

    def __init__(self, app: QApplication, overlay_mgr: OverlayManager):
        self.app = app
        self.overlay_mgr = overlay_mgr
        self._show_all = True

        icon_path = _resource_path("resources/icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("AgentStatusOverlay")
        self._build_menu()
        self.tray.show()

    def _build_menu(self):
        menu = QMenu()
        menu.addAction("AgentStatusOverlay").setEnabled(False)
        menu.addSeparator()

        show_action = QAction("显示悬浮窗", menu)
        show_action.setCheckable(True)
        show_action.setChecked(self._show_all)
        show_action.triggered.connect(self._toggle_global_visible)
        menu.addAction(show_action)

        menu.addSeparator()
        menu.addAction("退出").triggered.connect(self.app.quit)
        self.tray.setContextMenu(menu)

    def _toggle_global_visible(self, checked: bool):
        self._show_all = checked
        self.overlay_mgr.set_global_visible(checked)


def main():
    # ── 自动配置 Claude Code hooks ──
    try:
        configure_hooks()
    except Exception:
        pass

    atexit.register(restore_config)

    # ── Qt 应用 ──
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("AgentStatusOverlay")

    # ── 状态监听（QThread + pyqtSignal，线程安全）──
    monitor = StatusMonitor()
    monitor.start()

    # ── 多窗口管理器 ──
    overlay_mgr = OverlayManager(monitor)

    # ── 极简托盘 ──
    tray_mgr = TrayManager(app, overlay_mgr)

    # ── 运行标记（供 Claude Code hook 检测避免重复启动）──
    RUNNING_MARKER = os.path.join(
        os.path.expanduser("~"), ".agent-status", ".overlay.running"
    )
    os.makedirs(os.path.dirname(RUNNING_MARKER), exist_ok=True)
    with open(RUNNING_MARKER, "w") as _f:
        _f.write(str(os.getpid()))

    # ── 退出清理 ──
    def on_quit():
        monitor.stop()
        restore_config()
        try:
            Path(RUNNING_MARKER).unlink(missing_ok=True)
        except Exception:
            pass

    app.aboutToQuit.connect(on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
