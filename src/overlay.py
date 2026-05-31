"""DynamicIsland: 灵动岛悬浮窗 — PyQt6 无边框 + 透明 + 置顶胶囊"""
import ctypes
import time
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint,
    QRect, pyqtProperty
)
from PyQt6.QtGui import (
    QPainter, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QPen, QMouseEvent
)
from PyQt6.QtWidgets import QWidget, QApplication

try:
    from .protocol import StatusMessage, AgentStatus, STATUS_STYLE
except ImportError:
    from protocol import StatusMessage, AgentStatus, STATUS_STYLE

# ── 尺寸常量 ──
CAPSULE_WIDTH = 400
CAPSULE_HEIGHT = 40
CAPSULE_RADIUS = 20
EXPANDED_HEIGHT = 84
TOP_MARGIN = 12      # 距屏幕顶部距离

# ── IDLE 透明度 ──
IDLE_OPACITY = 0.30
ACTIVE_OPACITY = 0.92


class DynamicIsland(QWidget):
    """灵动岛悬浮窗"""

    def __init__(self):
        super().__init__()
        self._status_msg = StatusMessage(status="idle")
        self._project_name = "default"
        self._opacity = ACTIVE_OPACITY
        self._expanded = False      # hover 展开
        self._is_fullscreen = False  # 全屏圆点模式
        self._drag_pos: QPoint | None = None
        self._elapsed_sec = 0       # 本地计时（秒）
        self._anim_color = QColor("#6c7086")

        self._setup_window()
        self._setup_timers()
        self._center_at_top()

    # ── 窗口配置 ──
    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CAPSULE_WIDTH, CAPSULE_HEIGHT)
        self.setMouseTracking(True)

        # Windows 毛玻璃效果（DWM）
        self._enable_acrylic()

    def _enable_acrylic(self):
        """Windows 10/11 Acrylic 模糊背景"""
        try:
            hwnd = int(self.winId())
            accent = ctypes.create_string_buffer(16)
            # AccentState = 3 (ACCENT_ENABLE_BLURBEHIND)
            # GradientColor = 0 (transparent)
            ctypes.memmove(ctypes.addressof(accent) + 0, b'\x03\x00\x00\x00', 4)
            ctypes.memmove(ctypes.addressof(accent) + 8, b'\x00\x00\x00\x00', 4)
            ctypes.memmove(ctypes.addressof(accent) + 12, b'\x00\x00\x00\x00', 4)

            ctypes.windll.user32.SetWindowCompositionAttribute(
                hwnd,
                ctypes.byref(accent),
                0
            )
        except Exception:
            pass  # 非 Windows 或 API 不可用时静默跳过

    def _setup_timers(self):
        # 本地计时器（每秒更新）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # 全屏检测（每 2 秒）
        self._fs_timer = QTimer(self)
        self._fs_timer.timeout.connect(self._check_fullscreen)
        self._fs_timer.start(2000)

        # 指示灯动画
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._update_pulse)
        self._pulse_timer.start(50)
        self._pulse_phase = 0.0

    # ── 公开接口 ──
    def set_project(self, name: str):
        """设置当前项目名"""
        self._project_name = name

    def update_status(self, msg: StatusMessage):
        """外部调用：更新状态显示"""
        old_status = self._status_msg.status
        old_message = self._status_msg.message
        self._status_msg = msg

        # 状态真正变化时才重置计时
        if old_status != msg.status or old_message != msg.message:
            self._elapsed_sec = 0

        # 状态切换动画
        if old_status != msg.status:
            self._animate_color_change(msg.get_color())

        # Idle 透明度
        if msg.get_status_enum() == AgentStatus.IDLE:
            self._animate_opacity(IDLE_OPACITY)
        else:
            self._animate_opacity(ACTIVE_OPACITY)

        self._update_size()
        self.update()

    # ── 动画 ──
    def _animate_opacity(self, target: float):
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(self._opacity)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._opacity = target

    def _animate_color_change(self, hex_color: str):
        target = QColor(hex_color)
        start = QColor(self._anim_color)
        self._color_anim = QPropertyAnimation(self, b"_animColor")
        self._color_anim.setDuration(200)
        self._color_anim.setStartValue(start)
        self._color_anim.setEndValue(target)
        self._color_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._color_anim.start()

    def _get_anim_color(self):
        return self._anim_color

    def _set_anim_color(self, color):
        self._anim_color = color
        self.update()

    _animColor = pyqtProperty(QColor, _get_anim_color, _set_anim_color)

    def _update_pulse(self):
        self._pulse_phase += 0.1
        if self._pulse_phase > 6.28:
            self._pulse_phase = 0.0
        if self._status_msg.get_anim() != "static":
            self.update()  # 触发重绘指示灯

    # ── 定时器回调 ──
    def _tick(self):
        self._elapsed_sec += 1
        if self._expanded:
            self.update()  # hover 模式下显示计时

    def _check_fullscreen(self):
        """检测前台窗口是否全屏 → 降低透明度"""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            rect = ctypes.create_string_buffer(16)
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            left = int.from_bytes(rect[0:4], 'little', signed=True)
            top = int.from_bytes(rect[4:8], 'little', signed=True)
            right = int.from_bytes(rect[8:12], 'little', signed=True)
            bottom = int.from_bytes(rect[12:16], 'little', signed=True)

            screen = QApplication.primaryScreen().size()
            ww, wh = right - left, bottom - top
            is_fs = (abs(ww - screen.width()) < 50
                     and abs(wh - screen.height()) < 50)

            if is_fs != self._is_fullscreen:
                self._is_fullscreen = is_fs
                if is_fs:
                    self._animate_opacity(0.35)
                else:
                    target = IDLE_OPACITY if self._status_msg.get_status_enum() == AgentStatus.IDLE else ACTIVE_OPACITY
                    self._animate_opacity(target)
        except Exception:
            pass

    def _update_size(self):
        if self._expanded:
            self.setFixedSize(CAPSULE_WIDTH, EXPANDED_HEIGHT)
        else:
            self.setFixedSize(CAPSULE_WIDTH, CAPSULE_HEIGHT)
        self.update()

    # ── 位置 ──
    def _center_at_top(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - CAPSULE_WIDTH) // 2
        y = screen.top() + TOP_MARGIN
        self.move(x, y)

    def enterEvent(self, event):
        self._expanded = True
        self._update_size()

    def leaveEvent(self, event):
        self._expanded = False
        self._update_size()

    # ── 拖动 ──
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None

    # ── 绘制 ──
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._expanded:
            self._paint_expanded(painter)
        else:
            self._paint_capsule(painter)

    def _paint_capsule(self, painter: QPainter):
        """默认胶囊模式"""
        w, h = self.width(), self.height()

        # 背景（半透明深色）
        bg = QColor(17, 17, 27, int(235 * self._opacity))
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(49, 50, 68, 80), 1))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, CAPSULE_RADIUS, CAPSULE_RADIUS)

        msg = self._status_msg
        icon = msg.get_icon()
        color = self._get_indicator_color()

        font = QFont("Microsoft YaHei", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        fm = QFontMetrics(painter.font())

        x = 16
        # 指示灯
        dot_r = 4
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(x + dot_r, h // 2), dot_r, dot_r)
        x += 16

        # 图标
        painter.setPen(QColor("#cdd6f4"))
        painter.drawText(x, h // 2 + fm.ascent() // 2 - 1, icon)
        x += fm.horizontalAdvance(icon) + 6

        # 状态词
        status_name = msg.status.capitalize()
        painter.setPen(QColor(color))
        font = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(font)
        painter.drawText(x, h // 2 + fm.ascent() // 2 - 1, status_name)
        x += fm.horizontalAdvance(status_name) + 8

        # 分隔点
        if msg.message:
            # 项目名（小标签）
            if self._project_name and self._project_name != "default":
                proj_tag = f"@{self._project_name}"
                tag_font = QFont("Microsoft YaHei", 8)
                tag_font.setStyleHint(QFont.StyleHint.SansSerif)
                painter.setFont(tag_font)
                painter.setPen(QColor("#585b70"))
                t_fm = QFontMetrics(tag_font)
                tag_w = t_fm.horizontalAdvance(proj_tag)
                painter.drawText(x, h // 2 + t_fm.ascent() // 2 - 1, proj_tag)
                x += tag_w + 6

            sep_font = QFont("Microsoft YaHei", 9)
            sep_font.setStyleHint(QFont.StyleHint.SansSerif)
            painter.setPen(QColor("#6c7086"))
            painter.setFont(sep_font)
            painter.drawText(x, h // 2 + fm.ascent() // 2 - 1, "·")
            x += 14

            # 消息（截断）
            msg_text = msg.message
            max_msg_w = w - x - 70
            elided = fm.elidedText(msg_text, Qt.TextElideMode.ElideRight, max_msg_w)
            painter.setPen(QColor("#a6adc8"))
            painter.drawText(x, h // 2 + fm.ascent() // 2 - 1, elided)
            x += min(fm.horizontalAdvance(msg_text), max_msg_w) + 8

        # 计时
        elapsed = self._format_elapsed()
        elapsed_font = QFont("Microsoft YaHei", 9)
        elapsed_font.setStyleHint(QFont.StyleHint.SansSerif)
        efm = QFontMetrics(elapsed_font)
        painter.setPen(QColor("#6c7086"))
        painter.setFont(elapsed_font)
        painter.drawText(w - efm.horizontalAdvance(elapsed) - 16,
                         h // 2 + efm.ascent() // 2 - 1, elapsed)

    def _paint_expanded(self, painter: QPainter):
        """Hover 展开模式"""
        w, h = self.width(), self.height()

        bg = QColor(17, 17, 27, int(240 * self._opacity))
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(49, 50, 68, 100), 1))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, CAPSULE_RADIUS, CAPSULE_RADIUS)

        msg = self._status_msg
        color = self._get_indicator_color()

        # 第一行: 指示灯 + 图标 + 状态名 + 计时
        title_font = QFont("Microsoft YaHei", 11, QFont.Weight.Bold)
        title_font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(title_font)
        fm = QFontMetrics(painter.font())

        y = 10
        x = 16
        dot_r = 5
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(x + dot_r, y + fm.ascent() // 2), dot_r, dot_r)
        x += 18

        icon = msg.get_icon()
        painter.setPen(QColor("#cdd6f4"))
        painter.drawText(x, y + fm.ascent() - 2, icon)
        x += fm.horizontalAdvance(icon) + 6

        status_name = msg.status.capitalize()
        painter.setPen(QColor(color))
        painter.drawText(x, y + fm.ascent() - 2, status_name)
        x += fm.horizontalAdvance(status_name) + 12

        # 项目名
        proj_font = QFont("Microsoft YaHei", 8)
        proj_font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(proj_font)
        painter.setPen(QColor("#585b70"))
        proj_text = f"📁 {self._project_name}"
        painter.drawText(x, y + fm.ascent() - 3, proj_text)
        x += QFontMetrics(proj_font).horizontalAdvance(proj_text) + 10

        elapsed = self._format_elapsed()
        small_font = QFont("Microsoft YaHei", 9)
        small_font.setStyleHint(QFont.StyleHint.SansSerif)
        painter.setFont(small_font)
        painter.setPen(QColor("#6c7086"))
        painter.drawText(w - fm.horizontalAdvance(elapsed) - 16,
                         y + fm.ascent() - 4, elapsed)

        # 分隔线
        y += 28
        painter.setPen(QPen(QColor(69, 71, 90, 80), 1))
        painter.drawLine(16, y, w - 16, y)

        # 第二行: 消息详情
        y += 8
        body_font = QFont("Microsoft YaHei", 9)
        body_font.setStyleHint(QFont.StyleHint.SansSerif)
        if msg.message:
            painter.setFont(body_font)
            painter.setPen(QColor("#a6adc8"))
            painter.drawText(16, y + fm.ascent() - 2,
                             fm.elidedText(msg.message, Qt.TextElideMode.ElideRight, w - 32))
            y += 22

        # 第三行: 元数据
        meta = msg.metadata or {}
        parts = []
        if meta.get("token_count"):
            parts.append(f"Tokens: {meta['token_count']:,}")
        if self._elapsed_sec > 0:
            parts.append(f"耗时: {self._format_elapsed()}")
        if parts:
            painter.setFont(body_font)
            painter.setPen(QColor("#585b70"))
            painter.drawText(16, y + fm.ascent() - 2, "  |  ".join(parts))

    def _get_indicator_color(self) -> QColor:
        """获取当前指示灯颜色（含动画效果）"""
        msg = self._status_msg
        base = QColor(msg.get_color())
        anim = msg.get_anim()

        if anim == "static":
            return base
        elif anim in ("blink", "blink-fast"):
            cycle = self._pulse_phase
            if anim == "blink-fast":
                cycle *= 2
            factor = 1.0 if (int(cycle) % 2 == 0) else 0.2
            return QColor(
                int(base.red() * factor),
                int(base.green() * factor),
                int(base.blue() * factor)
            )
        else:  # pulse / pulse-fast
            speed = 2.0 if anim == "pulse-fast" else 1.0
            factor = 0.6 + 0.4 * abs(self._pulse_phase % (6.28 / speed) - 3.14 / speed) / (3.14 / speed)
            lighter = base.lighter(int(100 + 60 * factor))
            return lighter

    def _format_elapsed(self) -> str:
        elapsed = self._elapsed_sec
        # 如果有精确的 metadata，优先使用
        meta = self._status_msg.metadata or {}
        if meta.get("elapsed_ms"):
            elapsed = meta["elapsed_ms"] / 1000

        if elapsed < 60:
            return f"{int(elapsed)}s"
        elif elapsed < 3600:
            return f"{int(elapsed // 60)}m{int(elapsed % 60)}s"
        else:
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            return f"{h}h{m}m"

    # ── 属性访问器 (for QPropertyAnimation) ──
    def get_windowOpacity(self):
        return self._opacity

    def set_windowOpacity(self, val):
        self._opacity = val
        self.setWindowOpacity(val)

    windowOpacity = pyqtProperty(float, get_windowOpacity, set_windowOpacity)
