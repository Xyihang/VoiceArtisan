"""声匠 · VoiceArtisan 一键安装器（PySide6）

重写版：布局按 diseno-qt / taste-qt 的设计口径重做（不是把 tk 版搬过来）。

设计决策（对应技能里的三条要点）
1. 频率优先：这是「一天可能点几次到几十次」的安装向导，所以动画几乎为零；
   只有浮层提示做 180ms EASE_OUT 淡入，其余状态切换即时。
2. 单一主行动：底部只放一个主按钮，它会随进度变成「下一步：安装依赖 → 构建前端 →
   生成配置 → 启动服务 → 打开界面」，用户不需要记住顺序；其余操作降级为次要按钮。
3. 密度与层级：左栏固定（项目 + 步骤），中栏是唯一视觉焦点（进度 + 体检），
   日志是可收起的抽屉。不用卡片套卡片，分组靠留白与 1px 分隔线。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QPropertyAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voiceartisan_launcher as core  # noqa: E402

APP_TITLE = "声匠 VoiceArtisan 一键安装器"
APP_VERSION = getattr(core, "APP_VERSION", "1.1.60")
RADIUS = 8
TOAST_MS = 180


# --------------------------------------------------------------------------- #
# 动效（唯一一条曲线，唯一一个时长）
# --------------------------------------------------------------------------- #
_EASE = None


def ease_out() -> QEasingCurve:
    global _EASE
    if _EASE is None:
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.23, 1), QPointF(0.32, 1), QPointF(1.0, 1.0))
        _EASE = curve
    return _EASE


def system_prefers_less_motion() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        enabled = ctypes.c_bool(True)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return not enabled.value
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 主题
# --------------------------------------------------------------------------- #
LIGHT = {
    "bg": "#F4F6F9", "surface": "#FFFFFF", "sunken": "#F8FAFC", "line": "#E3E7EE",
    "text": "#14171D", "muted": "#5B6472", "faint": "#8891A0",
    "accent": "#1F6FEB", "accent_dark": "#1A5FD0", "accent_soft": "#E9F1FE",
    "ok": "#15803D", "warn": "#B45309", "fail": "#B91C1C",
    "log_bg": "#0E1116", "log_fg": "#D6DEE9", "sel": "#EDF3FE",
}
DARK = {
    "bg": "#13161B", "surface": "#1A1E25", "sunken": "#20252D", "line": "#2A313A",
    "text": "#E7EAF0", "muted": "#98A2B3", "faint": "#727C8B",
    "accent": "#4C8DFF", "accent_dark": "#3D79E0", "accent_soft": "#1E2A3D",
    "ok": "#4ADE80", "warn": "#FBBF24", "fail": "#F87171",
    "log_bg": "#0B0E12", "log_fg": "#C9D3E0", "sel": "#22314A",
}


def build_qss(t: dict) -> str:
    return f"""
    QWidget {{
        background: transparent;
        color: {t['text']};
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QWidget#Root {{ background: {t['bg']}; }}
    QLabel {{ background: transparent; }}
    QLabel#Brand {{ font-size: 15px; font-weight: 600; }}
    QLabel#Tag {{ color: {t['faint']}; font-size: 11px; }}
    QLabel#Eyebrow {{ color: {t['muted']}; font-size: 11px; font-weight: 600; }}
    QLabel#Hero {{ font-size: 30px; font-weight: 600; }}
    QLabel#HeroSub {{ color: {t['muted']}; font-size: 12px; }}
    QLabel#Meta {{ color: {t['faint']}; font-size: 12px; }}
    QLabel#Hint {{ color: {t['faint']}; font-size: 12px; }}
    QLabel#Empty {{ color: {t['faint']}; font-size: 12px; }}

    QFrame#Panel {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: {RADIUS}px; }}
    QFrame#Sunken {{ background: {t['sunken']}; border: 1px solid {t['line']}; border-radius: {RADIUS}px; }}
    QFrame#Hair {{ background: {t['line']}; border: none; max-height: 1px; min-height: 1px; }}
    QFrame#VRail {{ background: {t['line']}; border: none; max-width: 1px; min-width: 1px; }}

    QLineEdit {{
        background: {t['surface']}; border: 1px solid {t['line']}; border-radius: {RADIUS}px;
        padding: 6px 10px; selection-background-color: {t['accent']}; selection-color: #FFFFFF;
    }}
    QLineEdit:focus {{ border-color: {t['accent']}; }}
    QLineEdit#GhostEdit {{ background: {t['sunken']}; color: {t['muted']}; font-size: 12px; }}

    QPushButton {{
        background: {t['surface']}; border: 1px solid {t['line']}; border-radius: {RADIUS}px;
        padding: 7px 14px; color: {t['text']};
    }}
    QPushButton:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}
    QPushButton:pressed {{ background: {t['sunken']}; border-color: {t['accent_dark']}; }}
    QPushButton:disabled {{ color: {t['faint']}; background: {t['sunken']}; border-color: {t['line']}; }}

    QPushButton#Primary {{
        background: {t['accent']}; border: 1px solid {t['accent']}; color: #FFFFFF; font-weight: 600;
        padding: 9px 18px;
    }}
    QPushButton#Primary:hover {{ background: {t['accent_dark']}; border-color: {t['accent_dark']}; color: #FFFFFF; }}
    QPushButton#Primary:pressed {{ background: {t['accent_dark']}; }}
    QPushButton#Primary:disabled {{ background: {t['accent_soft']}; border-color: {t['accent_soft']}; color: {t['faint']}; }}

    QPushButton#Link {{ border: none; background: transparent; color: {t['accent']}; padding: 4px 6px; }}
    QPushButton#Link:hover {{ color: {t['accent_dark']}; }}
    QPushButton#Danger {{ color: {t['fail']}; }}
    QPushButton#Danger:hover {{ border-color: {t['fail']}; color: {t['fail']}; }}
    QPushButton#Segment {{
        border: none; border-radius: {RADIUS - 2}px; padding: 5px 12px; color: {t['muted']};
        background: transparent;
    }}
    QPushButton#Segment:hover {{ color: {t['text']}; }}
    QPushButton#Segment:checked {{ background: {t['surface']}; color: {t['text']}; font-weight: 600; }}

    QProgressBar {{ background: {t['sunken']}; border: none; border-radius: 5px; height: 8px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 5px; }}

    QTableWidget {{
        background: {t['surface']}; border: none;
        selection-background-color: {t['sel']}; selection-color: {t['text']}; outline: none;
    }}
    QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {t['line']}; }}
    QHeaderView::section {{
        background: {t['sunken']}; color: {t['muted']}; border: none;
        border-bottom: 1px solid {t['line']}; padding: 6px 10px; font-weight: 600;
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 5px; min-height: 26px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t['line']}; border-radius: 5px; min-width: 26px; }}

    QPlainTextEdit#Log {{
        background: {t['log_bg']}; color: {t['log_fg']}; border: 1px solid {t['line']};
        border-radius: {RADIUS}px; font-family: "Cascadia Mono", Consolas, monospace;
        font-size: 12px; padding: 8px; selection-background-color: {t['accent_dark']};
    }}
    QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']}; padding: 6px 8px; }}
    """


STEP_DEFS = (
    ("select", "选择目录", "确认解压后的项目根目录"),
    ("checks", "环境体检", "Python / 磁盘 / GPU / 模型"),
    ("deps", "安装依赖", "torch 与后端依赖"),
    ("frontend", "构建前端", "已有 dist 可跳过"),
    ("config", "生成配置", "config/config.yaml"),
    ("service", "启动服务", "https://localhost:8443"),
    ("done", "完成", "可以开始用了"),
)
TASK_TO_STEP = {
    "环境体检": "checks", "安装依赖": "deps", "构建前端": "frontend",
    "生成配置": "config", "启动服务": "service", "停止服务": None, "创建 venv": None,
}
NEXT_ORDER = ("deps", "frontend", "config", "service")
NEXT_LABEL = {
    "deps": ("安装依赖", "下一步：安装依赖"),
    "frontend": ("构建前端", "下一步：构建前端"),
    "config": ("生成配置", "下一步：生成配置"),
    "service": ("启动服务", "下一步：启动服务"),
}
STATE_DOT = {"idle": "○", "running": "◐", "ok": "●", "failed": "✕", "skipped": "·"}


def make_brand_pixmap(size: int, accent: str, accent2: str) -> QPixmap:
    """用 QPainter 画品牌标记（渐变圆角块 + 声波），不依赖外部图标文件。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor(accent))
    grad.setColorAt(1.0, QColor(accent2))
    painter.fillPath(path, grad)
    pen = QPen(QColor(255, 255, 255, 220))
    pen.setWidthF(max(1.4, size * 0.06))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    center = size / 2
    for line_x, half in ((center - size * 0.22, size * 0.10), (center, size * 0.20), (center + size * 0.22, size * 0.10)):
        painter.drawLine(QPointF(line_x, center - half), QPointF(line_x, center + half))
    painter.end()
    return pm


# --------------------------------------------------------------------------- #
# 后台任务
# --------------------------------------------------------------------------- #
class TaskSignals(QObject):
    log = Signal(str)
    progress = Signal(str, object, object, str)
    done = Signal(str, bool)


class Task(QThread):
    def __init__(self, label: str, fn, signals: TaskSignals) -> None:
        super().__init__()
        self.label = label
        self.fn = fn
        self.signals = signals

    def log(self, message: str) -> None:
        self.signals.log.emit(str(message))

    def progress(self, phase: str, done=None, total=None, text: str = "") -> None:
        self.signals.progress.emit(phase, done, total, text)

    def run(self) -> None:  # noqa: D102
        ok = True
        try:
            self.fn(self)
        except Exception as exc:  # noqa: BLE001
            import traceback

            ok = False
            self.log(f"===== 失败：{self.label}（{type(exc).__name__}: {exc}）")
            self.log(traceback.format_exc().rstrip())
        finally:
            self.signals.done.emit(self.label, ok)


# --------------------------------------------------------------------------- #
# 步骤行（左栏）
# --------------------------------------------------------------------------- #
class StepRow(QWidget):
    def __init__(self, index: int, key: str, title: str, desc: str, pal: dict) -> None:
        super().__init__()
        self.key = key
        self.title = title
        self.desc = desc
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(9)
        self.dot = QLabel(STATE_DOT["idle"])
        self.dot.setFixedWidth(13)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.name = QLabel(f"{index}. {title}")
        self.sub = QLabel(desc)
        self.sub.setObjectName("Hint")
        text_box.addWidget(self.name)
        text_box.addWidget(self.sub)
        lay.addWidget(self.dot)
        lay.addLayout(text_box, 1)
        self.set_state("idle", pal)

    def set_state(self, state: str, pal: dict, detail: str = "") -> None:
        color = {
            "idle": pal["faint"], "running": pal["accent"], "ok": pal["ok"],
            "failed": pal["fail"], "skipped": pal["faint"],
        }.get(state, pal["faint"])
        self.dot.setText(STATE_DOT.get(state, "○"))
        self.dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        weight = "600" if state in ("running", "ok") else "400"
        name_color = pal["text"] if state != "idle" else pal["muted"]
        self.name.setStyleSheet(f"color: {name_color}; font-weight: {weight};")
        # 说明不占行高：放到 tooltip 里，左栏保持 7 行等高，视线不用来回跳
        self.sub.setVisible(False)
        self.name.setToolTip(detail or self.desc)
        self.setToolTip(detail or self.desc)


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.addWidget(self._label)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = None
        self.hide()

    def show_text(self, text: str, tone: str = "info", reduce_motion: bool = False) -> None:
        colors = {
            "info": ("#1F6FEB", "#FFFFFF"), "ok": ("#15803D", "#FFFFFF"),
            "warn": ("#B45309", "#FFFFFF"), "fail": ("#B91C1C", "#FFFFFF"),
        }
        bg, fg = colors.get(tone, colors["info"])
        self.setStyleSheet(
            f"QFrame#Toast {{ background: {bg}; border-radius: {RADIUS}px; }}"
            f"QLabel {{ color: {fg}; font-size: 12px; }}"
        )
        self._label.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(max(16, parent.width() - self.width() - 20), 18)
        self.show()
        self.raise_()
        if reduce_motion:
            self._effect.setOpacity(1.0)
        else:
            self._anim = QPropertyAnimation(self._effect, b"opacity", self)
            self._anim.setDuration(TOAST_MS)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setEasingCurve(ease_out())
            self._anim.start()
        QTimer.singleShot(2800, self.hide)


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class Launcher(QMainWindow):
    def __init__(self, root: str = "") -> None:
        super().__init__()
        self.dark = self._system_is_dark()
        self.pal = DARK if self.dark else LIGHT
        self.reduce_motion = system_prefers_less_motion()
        self.root_dir = Path(root) if root else Path("")
        self.step_states = {key: "idle" for key, _t, _d in STEP_DEFS}
        self.step_detail: dict[str, str] = {}
        self.checks: dict[str, object] = {}
        self.task: Task | None = None
        self.service_proc: subprocess.Popen | None = None
        self.last_action = ""
        self.progress_start = 0.0
        self.log_open = False  # 默认收起：日志是排查时才看的东西，不占首屏
        self.log_height = 120
        self._rows: dict[str, StepRow] = {}

        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self._fit_to_screen()

        self.signals = TaskSignals()
        self.signals.log.connect(self.append_log)
        self.signals.progress.connect(self._on_progress)
        self.signals.done.connect(self._on_done)

        self._build_ui()
        self._apply_theme()
        self._load_saved()
        self._update_buttons()
        self.append_log(f"{APP_TITLE} 已启动。确认项目目录后点「刷新检测」开始。")
        if self._root_valid():
            QTimer.singleShot(300, self.run_checks)

    # ---------------- 屏幕适配 ----------------
    def _enforce_fit(self) -> None:
        """内容变化（展开日志等）后重新压回屏幕可用区域。"""
        target = getattr(self, "_target_size", None)
        if not target:
            return
        width = min(target[0], self.width())
        height = min(target[1], self.height())
        self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))

    def _fit_to_screen(self) -> None:
        """窗口必须落在屏幕可用区域内：否则底部主按钮会被裁掉。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1080, 700)
            self.setMinimumSize(900, 560)
            return
        avail = screen.availableGeometry()
        dpr = screen.devicePixelRatio() or 1.0
        # 注意：部分 Windows 缩放设置下 availableGeometry() 给的是物理像素，
        # 而 resize()/move() 用的是逻辑像素，这里统一按 dpr 换算，否则窗口会顶出屏幕。
        logical_w = avail.width() / dpr
        logical_h = avail.height() / dpr
        width = int(min(1180, logical_w * 0.94))
        height = int(min(800, logical_h * 0.94))
        self.resize(width, height)
        self.setMinimumSize(min(860, width), min(520, height))
        self._target_size = (width, height)
        self._target_pos = (
            int(avail.x() / dpr + (logical_w - width) / 2),
            int(avail.y() / dpr + (logical_h - height) / 2),
        )

    def showEvent(self, event) -> None:  # noqa: N802
        """布局算出最小尺寸后再压一次，防止内容把窗口顶出屏幕。"""
        super().showEvent(event)
        target = getattr(self, "_target_size", None)
        pos = getattr(self, "_target_pos", None)
        if target:
            def _apply() -> None:
                self.resize(*target)
                if pos:
                    self.move(*pos)
            QTimer.singleShot(0, _apply)

    # ---------------- 主题 ----------------
    @staticmethod
    def _system_is_dark() -> bool:
        forced = os.environ.get("QWENTTS_LAUNCHER_THEME", "").strip().lower()
        if forced in ("dark", "light"):
            return forced == "dark"
        try:
            return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:  # noqa: BLE001
            return False

    def _apply_theme(self) -> None:
        self.pal = DARK if self.dark else LIGHT
        self.setStyleSheet(build_qss(self.pal))
        self.brand_icon.setPixmap(make_brand_pixmap(26, self.pal["accent"], "#0D9488"))
        self.theme_btn.setText("浅色" if self.dark else "深色")
        for key, row in self._rows.items():
            row.set_state(self.step_states.get(key, "idle"), self.pal, self.step_detail.get(key, ""))

    # ---------------- UI ----------------
    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Panel")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        return frame, lay

    def _hair(self) -> QFrame:
        line = QFrame()
        line.setObjectName("Hair")
        line.setFixedHeight(1)
        return line

    def _build_ui(self) -> None:
        root_widget = QWidget()
        root_widget.setObjectName("Root")
        self.setCentralWidget(root_widget)
        outer = QVBoxLayout(root_widget)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # ---------- 顶栏 ----------
        bar = QFrame()
        bar.setObjectName("Panel")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 8, 12, 8)
        bar_lay.setSpacing(10)
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(26, 26)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel("声匠 VoiceArtisan")
        brand.setObjectName("Brand")
        tag = QLabel(f"一键安装器 · v{APP_VERSION}")
        tag.setObjectName("Tag")
        brand_text.addWidget(brand)
        brand_text.addWidget(tag)
        bar_lay.addWidget(self.brand_icon)
        bar_lay.addLayout(brand_text)
        bar_lay.addStretch(1)

        # 运行环境：分段控件（比下拉更直观，一眼看到当前用什么 Python）
        seg = QFrame()
        seg.setObjectName("Sunken")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        self.seg_system = QPushButton("系统 Python")
        self.seg_venv = QPushButton("项目内 venv")
        for btn in (self.seg_system, self.seg_venv):
            btn.setObjectName("Segment")
            btn.setCheckable(True)
        self.seg_system.setChecked(True)
        self.seg_system.clicked.connect(lambda: self._set_env(core.ENV_SYSTEM))
        self.seg_venv.clicked.connect(lambda: self._set_env(core.ENV_VENV))
        seg_lay.addWidget(self.seg_system)
        seg_lay.addWidget(self.seg_venv)
        bar_lay.addWidget(seg)

        self.venv_btn = QPushButton("创建 venv")
        self.venv_btn.clicked.connect(self.create_venv)
        bar_lay.addWidget(self.venv_btn)
        self.theme_btn = QPushButton("深色")
        self.theme_btn.setFixedWidth(64)
        self.theme_btn.clicked.connect(self.toggle_theme)
        bar_lay.addWidget(self.theme_btn)
        outer.addWidget(bar)

        # ---------- 主体：左栏 + 中栏 ----------
        body = QHBoxLayout()
        body.setSpacing(10)

        # 左栏
        left = QVBoxLayout()
        left.setSpacing(10)

        path_panel, path_lay = self._panel()
        eyebrow = QLabel("项目目录")
        eyebrow.setObjectName("Eyebrow")
        path_lay.addWidget(eyebrow)
        self.root_edit = QLineEdit()
        self.root_edit.setPlaceholderText(r"D:\VoiceArtisan")
        self.root_edit.editingFinished.connect(self._on_root_edited)
        path_lay.addWidget(self.root_edit)
        path_actions = QHBoxLayout()
        path_actions.setSpacing(6)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self.browse_root)
        refresh = QPushButton("刷新检测")
        refresh.clicked.connect(self.force_refresh)
        path_actions.addWidget(browse)
        path_actions.addWidget(refresh)
        path_lay.addLayout(path_actions)
        self.root_hint = QLabel("")
        self.root_hint.setObjectName("Hint")
        self.root_hint.setWordWrap(True)
        path_lay.addWidget(self.root_hint)
        left.addWidget(path_panel)

        steps_panel, steps_lay = self._panel()
        steps_title = QLabel("安装步骤")
        steps_title.setObjectName("Eyebrow")
        steps_lay.addWidget(steps_title)
        for idx, (key, title, desc) in enumerate(STEP_DEFS, 1):
            row = StepRow(idx, key, title, desc, self.pal)
            self._rows[key] = row
            steps_lay.addWidget(row)
        steps_lay.addStretch(1)
        tip = QLabel("装依赖/下模型期间可以关掉本窗口，进度在后端继续跑。")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        steps_lay.addWidget(tip)
        left.addWidget(steps_panel, 1)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setFixedWidth(268)
        body.addWidget(left_box)

        # 中栏
        center = QVBoxLayout()
        center.setSpacing(10)

        hero, hero_lay = self._panel()
        hero_top = QHBoxLayout()
        hero_top.setSpacing(12)
        self.hero_value = QLabel("待开始")
        self.hero_value.setObjectName("Hero")
        hero_text = QVBoxLayout()
        hero_text.setSpacing(2)
        self.hero_phase = QLabel("环境体检还没有跑")
        self.hero_phase.setObjectName("HeroSub")
        self.hero_action = QLabel("点「刷新检测」查看这台机器能不能直接跑起来")
        self.hero_action.setObjectName("Meta")
        self.hero_action.setWordWrap(True)
        hero_text.addWidget(self.hero_phase)
        hero_text.addWidget(self.hero_action)
        self.hero_meta = QLabel("")
        self.hero_meta.setObjectName("Meta")
        self.hero_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        hero_top.addWidget(self.hero_value)
        hero_top.addLayout(hero_text, 1)
        hero_top.addWidget(self.hero_meta)
        hero_lay.addLayout(hero_top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        hero_lay.addWidget(self.progress)

        log_row = QHBoxLayout()
        log_row.setSpacing(6)
        self.log_hint = QLabel("展开日志可看到每一步的真实输出")
        self.log_hint.setObjectName("Hint")
        self.log_toggle = QPushButton("查看日志")
        self.log_toggle.setCheckable(False)
        self.log_toggle.setObjectName("Link")
        self.log_toggle.clicked.connect(self._toggle_log)
        log_row.addWidget(self.log_hint, 1)
        log_row.addWidget(self.log_toggle)
        hero_lay.addLayout(log_row)
        center.addWidget(hero)

        checks_panel, checks_lay = self._panel()
        checks_head = QHBoxLayout()
        checks_head.setSpacing(8)
        checks_title = QLabel("环境体检")
        checks_title.setObjectName("Eyebrow")
        self.checks_count = QLabel("尚未检测")
        self.checks_count.setObjectName("Tag")
        self.copy_btn = QPushButton("复制修复命令")
        self.copy_btn.setObjectName("Link")
        self.copy_btn.clicked.connect(self.copy_fix_hint)
        checks_head.addWidget(checks_title)
        checks_head.addWidget(self.checks_count)
        checks_head.addStretch(1)
        checks_head.addWidget(self.copy_btn)
        checks_lay.addLayout(checks_head)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "检查项", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(1, 170)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.itemSelectionChanged.connect(self._on_check_selected)
        self.table.setMinimumHeight(96)
        checks_lay.addWidget(self.table, 1)

        self.check_hint = QLabel("")
        self.check_hint.setObjectName("Hint")
        self.check_hint.setWordWrap(True)
        checks_lay.addWidget(self.check_hint)
        center.addWidget(checks_panel, 1)

        # 日志抽屉
        self.log_panel, log_lay = self._panel()
        self.log_panel.setMaximumHeight(150)
        self.log_panel.setVisible(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(120)
        log_lay.addWidget(self.log_view)
        center.addWidget(self.log_panel)

        center_box = QWidget()
        center_box.setLayout(center)
        body.addWidget(center_box, 1)
        outer.addLayout(body, 1)

        # ---------- 底部行动区 ----------
        bottom = QFrame()
        bottom.setObjectName("Panel")
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(12, 8, 12, 8)
        bottom_lay.setSpacing(8)

        self.manual_edit = QLineEdit()
        self.manual_edit.setObjectName("GhostEdit")
        self.manual_edit.setReadOnly(True)
        bottom_lay.addWidget(self.manual_edit)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_next = QPushButton("开始体检")
        self.btn_next.setObjectName("Primary")
        self.btn_next.setMinimumWidth(150)
        self.btn_next.setAutoDefault(False)
        self.btn_next.setDefault(False)
        self.btn_next.clicked.connect(self.run_next)
        actions.addWidget(self.btn_next)

        self.btn_stop = QPushButton("停止服务")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.clicked.connect(self.stop_service)
        self.btn_open = QPushButton("打开界面")
        self.btn_open.clicked.connect(lambda: webbrowser.open(core.HOME_URL))
        self.btn_logs = QPushButton("日志目录")
        self.btn_logs.clicked.connect(self.open_log_dir)
        self.btn_retry = QPushButton("重试上一步")
        self.btn_retry.setEnabled(False)
        self.btn_retry.clicked.connect(self.retry_last)
        for btn in (self.btn_stop, self.btn_open, self.btn_logs, self.btn_retry):
            actions.addWidget(btn)
        actions.addStretch(1)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("Meta")
        actions.addWidget(self.status_label)
        bottom_lay.addLayout(actions)
        outer.addWidget(bottom)

        self.toast = Toast(self)
        self._set_manual_commands()

    # ---------------- 环境 / 目录 ----------------
    def _set_env(self, choice: str) -> None:
        self.seg_system.setChecked(choice == core.ENV_SYSTEM)
        self.seg_venv.setChecked(choice == core.ENV_VENV)
        core.save_config(env=choice)
        self._set_manual_commands()
        self.append_log(f"运行环境：{'项目内 venv' if choice == core.ENV_VENV else '系统 Python'}")

    def current_env(self) -> str:
        return core.ENV_VENV if self.seg_venv.isChecked() else core.ENV_SYSTEM

    def _current_root(self) -> Path | None:
        raw = self.root_edit.text().strip().strip('"')
        return Path(raw) if raw else None

    def _root_valid(self) -> bool:
        root = self._current_root()
        return bool(root and root.is_dir() and not core.detect_project_root(root))

    def _load_saved(self) -> None:
        saved = core.load_saved_config()
        env = core.normalize_env_choice(saved.get("env"))
        self._set_env(env)
        initial = str(self.root_dir or saved.get("root") or "")
        if not initial:
            cwd = Path.cwd()
            if (cwd / "backend/app/main.py").is_file():
                initial = str(cwd)
        if initial:
            self.root_edit.setText(initial)
            self.root_dir = Path(initial)
        self._refresh_root_hint()
        self._set_manual_commands()

    def _refresh_root_hint(self) -> None:
        root = self._current_root()
        if root is None or not root.is_dir():
            self.root_hint.setText("还没有选择目录")
            self._set_step("select", "idle")
            return
        missing = core.detect_project_root(root)
        if missing:
            self.root_hint.setText("不是项目根：缺 " + "、".join(missing))
            self._set_step("select", "idle")
            return
        self.root_hint.setText("目录正确")
        self._set_step("select", "ok", str(root))

    # ---------------- 步骤 / 状态 ----------------
    def _set_step(self, key: str, state: str, detail: str = "") -> None:
        if key not in self._rows:
            return
        self.step_states[key] = state
        if detail:
            self.step_detail[key] = detail
        self._rows[key].set_state(state, self.pal, self.step_detail.get(key, ""))
        self._refresh_next_button()

    def _next_key(self) -> str:
        for key in NEXT_ORDER:
            if self.step_states.get(key) != "ok":
                return key
        return "open"

    def _refresh_next_button(self) -> None:
        if self.task is not None and self.task.isRunning():
            return
        key = self._next_key()
        if key == "open":
            self.btn_next.setText("打开界面")
            self.btn_next.setEnabled(True)
            return
        task_label, text = NEXT_LABEL[key]
        self.btn_next.setText(text)
        enabled = self._root_valid()
        if key == "service":
            enabled = enabled and self.step_states.get("deps") == "ok"
        self.btn_next.setEnabled(enabled)

    # ---------------- 主行动 ----------------
    def run_next(self) -> None:
        key = self._next_key()
        if key == "open":
            webbrowser.open(core.HOME_URL)
            self.toast.show_text(f"已在浏览器打开 {core.HOME_URL}", "info", self.reduce_motion)
            return
        {
            "deps": self.install_deps,
            "frontend": self.build_frontend,
            "config": self.generate_config,
            "service": self.start_service,
        }[key]()

    # ---------------- 日志 ----------------
    def append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _toggle_log(self) -> None:
        self.log_open = not self.log_open
        self.log_panel.setVisible(self.log_open)
        self.log_toggle.setText("隐藏日志" if self.log_open else "查看日志")
        self._enforce_fit()

    # ---------------- 进度 ----------------
    def _on_progress(self, phase: str, done, total, text: str) -> None:
        if not self.progress_start:
            self.progress_start = time.time()
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            pct = max(0, min(100, round(done * 100 / total)))
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)
            self.hero_value.setText(f"{pct}%")
            self.hero_phase.setText(phase)
            self.hero_action.setText(text or "处理中…")
            self.setWindowTitle(f"{APP_TITLE} · {phase} {pct}%")
        else:
            self.progress.setRange(0, 0)
            self.hero_value.setText("…")
            self.hero_phase.setText(phase)
            self.hero_action.setText(text or "处理中…")
            self.setWindowTitle(f"{APP_TITLE} · {phase}")
        elapsed = time.time() - self.progress_start
        meta = f"已用 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        if isinstance(done, int) and isinstance(total, int) and total > 0 and done > 0:
            eta = elapsed / done * (total - done)
            meta += f"\n还需 ≈ {int(eta // 60):02d}:{int(eta % 60):02d}"
        self.hero_meta.setText(meta)

    # ---------------- 任务 ----------------
    def start_task(self, label: str, fn) -> None:
        if self.task is not None and self.task.isRunning():
            self.toast.show_text("已有任务在执行", "warn", self.reduce_motion)
            return
        self.last_action = label
        self.append_log(f"[动作] 启动任务：{label}")
        step_key = TASK_TO_STEP.get(label)
        if step_key:
            self._set_step(step_key, "running")
        self.progress_start = 0.0
        self.progress.setRange(0, 0)
        self.hero_value.setText("…")
        self.hero_phase.setText(label)
        self.hero_action.setText("准备中…")
        self.hero_meta.setText("")
        self.append_log(f"===== 开始：{label} =====")
        self.task = Task(label, fn, self.signals)
        self.task.start()
        self._update_buttons()

    def _on_done(self, label: str, ok: bool) -> None:
        step_key = TASK_TO_STEP.get(label)
        if step_key:
            self._set_step(step_key, "ok" if ok else "failed")
        self.append_log(f"===== 结束：{label}（{'成功' if ok else '失败'}） =====")
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if ok else 0)
        if ok:
            self.hero_value.setText("完成")
            self.hero_phase.setText(label)
            self.hero_action.setText("可以继续下一步")
            self.hero_meta.setText("")
            self.setWindowTitle(f"{APP_TITLE} · {label} 完成")
            self.toast.show_text(f"{label} 完成", "ok", self.reduce_motion)
            self.btn_retry.setEnabled(False)
        else:
            self.hero_value.setText("失败")
            self.hero_phase.setText(label)
            self.hero_action.setText("看下方日志定位原因，可点「重试上一步」")
            self.hero_meta.setText("")
            self.setWindowTitle(f"{APP_TITLE} · {label} 失败")
            self.toast.show_text(f"{label} 失败", "fail", self.reduce_motion)
            self.btn_retry.setEnabled(True)
        self.status_label.setText(f"{label}：{'成功' if ok else '失败'}")
        try:
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        except Exception:  # noqa: BLE001
            pass
        self._update_buttons()
        if ok and step_key in ("deps", "config", "frontend"):
            QTimer.singleShot(400, self.run_checks_quiet)

    def _update_buttons(self) -> None:
        running = self.task is not None and self.task.isRunning()
        for btn in (self.btn_stop, self.venv_btn):
            btn.setEnabled(not running)
        self.btn_next.setEnabled(not running and self._root_valid())
        if not running:
            self._refresh_next_button()

    # ---------------- 各操作 ----------------
    def browse_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择解压后的项目根目录")
        if not chosen:
            return
        self.root_edit.setText(chosen)
        self._on_root_edited()

    def _on_root_edited(self) -> None:
        raw = self.root_edit.text().strip().strip('"')
        self.root_dir = Path(raw) if raw else Path("")
        self._refresh_root_hint()
        if self._root_valid():
            core.save_config(root=raw, env=self.current_env())
            self._set_manual_commands()
            self.run_checks()

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self._apply_theme()

    def run_checks(self) -> None:
        if not self._root_valid():
            self.toast.show_text("先选择正确的项目目录", "warn", self.reduce_motion)
            return
        self.table.setRowCount(0)
        self.checks.clear()
        self.checks_count.setText("检测中…")
        self.start_task("环境体检", self._work_checks)

    def run_checks_quiet(self) -> None:
        if self.task is not None and self.task.isRunning():
            return
        self.run_checks()

    def force_refresh(self) -> None:
        try:
            core.clear_probe_cache(self._probe_python())
        except Exception:  # noqa: BLE001
            pass
        self.append_log("已清除 Python / pip 探测缓存，重新体检…")
        self.run_checks()

    def _probe_python(self) -> str | None:
        try:
            return (core.check_python(core.detect_python()).extra or {}).get("exe")
        except Exception:  # noqa: BLE001
            return None

    def _work_checks(self, task: Task) -> None:
        root = self._current_root()
        assert root is not None
        choice = self.current_env()
        task.log("快检：Python / pip / venv / 磁盘 / 配置 / 前端产物")
        py_check = core.check_python(core.detect_python())
        python_exe = (py_check.extra or {}).get("exe")
        self._add_check_row(py_check)
        task.progress("环境体检", 1, 20, "Python 已就绪")
        rest = [
            core.detect_pip(python_exe), core.detect_venv(root, choice), core.detect_disk(root),
            core.detect_config(root), core.detect_dist(root),
        ]
        for idx, check in enumerate(rest, start=2):
            self._add_check_row(check)
            task.progress("环境体检", idx, 20, check.name)
        task.log("慢检：依赖 / GPU / Node / ffmpeg / 模型体积（并发）")
        for idx, check in enumerate(core.collect_checks_slow(root, python_exe), start=6):
            self._add_check_row(check)
            task.progress("环境体检", min(20, idx), 20, check.name)
        task.progress("环境体检", 20, 20, "完成")
        fails = [c for c in self.checks.values() if getattr(c, "status", "") == core.STATUS_FAIL]
        warns = [c for c in self.checks.values() if getattr(c, "status", "") == core.STATUS_WARN]
        task.log(f"体检完成：{len(self.checks)} 项，失败 {len(fails)}，提醒 {len(warns)}")
        self._sync_steps_from_checks()

    def _sync_steps_from_checks(self) -> None:
        """已满足的前置直接标完成：装过的人不用再走一遍流程。"""
        by_key = {}
        for check in self.checks.values():
            key = str(getattr(check, "key", "")).split("#")[0]
            by_key.setdefault(key, check)
        mapping = (("deps", "pydeps", "依赖已就绪"), ("frontend", "dist", "已有构建产物"),
                   ("config", "config", "配置已存在"))
        for step_key, check_key, detail in mapping:
            check = by_key.get(check_key)
            if check is not None and getattr(check, "ok", False):
                self._set_step(step_key, "ok", detail)
        ready = [key for flag, key in
                 (("1.7B", "deps"), ("0.6B", "deps")) if flag]  # 占位，保持可读
        self._refresh_next_button()

    def _add_check_row(self, check) -> None:
        key = str(getattr(check, "key", "") or "check")
        seq, base = 1, key
        while key in self.checks:
            seq += 1
            key = f"{base}#{seq}"
        self.checks[key] = check
        icons = {core.STATUS_OK: "✓", core.STATUS_WARN: "!", core.STATUS_FAIL: "✕"}
        colors = {
            core.STATUS_OK: self.pal["ok"], core.STATUS_WARN: self.pal["warn"],
            core.STATUS_FAIL: self.pal["fail"],
        }
        detail = str(getattr(check, "detail", ""))
        hint = getattr(check, "fix_hint", "")
        if not getattr(check, "ok", False) and hint:
            detail = f"{detail} → {hint}"
        row = self.table.rowCount()
        self.table.insertRow(row)
        icon_item = QTableWidgetItem(icons.get(check.status, "?"))
        icon_item.setForeground(QColor(colors.get(check.status, self.pal["faint"])))
        icon_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_item.setData(Qt.ItemDataRole.UserRole, key)
        name_item = QTableWidgetItem(str(getattr(check, "name", "")))
        name_item.setData(Qt.ItemDataRole.UserRole, key)
        detail_item = QTableWidgetItem(detail)
        detail_item.setToolTip(detail)
        detail_item.setData(Qt.ItemDataRole.UserRole, key)
        self.table.setItem(row, 0, icon_item)
        self.table.setItem(row, 1, name_item)
        self.table.setItem(row, 2, detail_item)
        self.checks_count.setText(f"{len(self.checks)} 项")

    def create_venv(self) -> None:
        root = self._current_root()
        if root is None:
            self.toast.show_text("先选择项目目录", "warn", self.reduce_motion)
            return
        force = False
        if core.venv_exists(root):
            answer = QMessageBox.question(
                self, APP_TITLE,
                f"venv 已存在：\n{core.venv_dir(root)}\n\n删除并重建？（约 1-3 分钟）\n选 No 表示直接切换到它。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._set_env(core.ENV_VENV)
                self.append_log("venv 已存在，直接切换为项目内 venv。")
                return
            force = True
        self._set_env(core.ENV_VENV)

        def work(task: Task) -> None:
            core.create_venv_core(root, self._probe_python(), task.log, force=force)
            task.log("venv 准备完成")

        self.start_task("创建 venv", work)

    def install_deps(self) -> None:
        root = self._current_root()
        if root is None or not self._root_valid():
            self.toast.show_text("先选择正确的项目目录", "warn", self.reduce_motion)
            return
        python = self._resolve_python()
        if not python:
            return

        def work(task: Task) -> None:
            lock = root / "requirements.pins.txt"
            req = lock if lock.is_file() else (root / "requirements.txt")
            torch_req = root / "requirements-torch.txt"
            task.log("先装 torch 三件套（pytorch 源），再装其余锁定依赖（国内镜像）")
            rc, _ = core.run_capture([python, "-c", "import torch"], timeout=90)
            if rc != 0:
                if torch_req.is_file():
                    self._run_stream(task, [python, "-m", "pip", "install", "-r", str(torch_req),
                                            "--index-url", core.TORCH_INDEX], root, pip=True)
                else:
                    self._run_stream(task, [python, "-m", "pip", "install", "torch", "torchaudio",
                                            "--index-url", core.TORCH_INDEX], root, pip=True)
            else:
                task.log("torch 已可导入，跳过")
            code = self._run_stream(task, [python, "-m", "pip", "install", "-r", str(req),
                                           "-i", core.PYPI_MIRROR], root, pip=True)
            if code != 0:
                task.log("镜像失败，重试官方源…")
                code = self._run_stream(task, [python, "-m", "pip", "install", "-r", str(req)], root, pip=True)
            if code != 0:
                raise RuntimeError("依赖安装失败，详见日志")

        self.start_task("安装依赖", work)

    def build_frontend(self) -> None:
        root = self._current_root()
        if root is None or not self._root_valid():
            self.toast.show_text("先选择正确的项目目录", "warn", self.reduce_motion)
            return

        def work(task: Task) -> None:
            frontend = root / "frontend"
            dist_index = frontend / "dist" / "index.html"
            if dist_index.is_file():
                task.log(f"已有预构建产物：{dist_index}")
                task.log("无需 Node/npm；确需重建请先删除 frontend/dist")
                return
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("未找到 npm：请安装 Node.js LTS")
            if self._run_stream(task, [npm, "install", "--registry", core.NPM_REGISTRY,
                                       "--no-audit", "--no-fund"], frontend) != 0:
                raise RuntimeError("npm install 失败")
            if self._run_stream(task, [npm, "run", "build"], frontend) != 0:
                raise RuntimeError("npm run build 失败")

        self.start_task("构建前端", work)

    def generate_config(self) -> None:
        root = self._current_root()
        if root is None or not self._root_valid():
            self.toast.show_text("先选择正确的项目目录", "warn", self.reduce_motion)
            return

        def work(task: Task) -> None:
            target = root / "config" / "config.yaml"
            source = root / "config" / "config.example.yaml"
            if target.is_file():
                task.log(f"配置已存在，不覆盖：{target}")
                return
            if not source.is_file():
                raise RuntimeError("缺少 config/config.example.yaml")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            task.log(f"已生成：{target}")

        self.start_task("生成配置", work)

    def start_service(self) -> None:
        root = self._current_root()
        if root is None or not self._root_valid():
            self.toast.show_text("先选择正确的项目目录", "warn", self.reduce_motion)
            return
        python = self._resolve_python()
        if not python:
            return

        def work(task: Task) -> None:
            task.log("检查后端依赖…")
            deps = core.probe_python_deps_cached(python)
            missing = [name for name, key in (("torch", "torch"), ("qwen-tts", "qwen_tts"))
                       if not (deps.get(key) or "")]
            if missing:
                raise RuntimeError("缺依赖：" + "、".join(missing) + "，先点「安装依赖」")
            if core.is_port_open("127.0.0.1", core.PREFERRED_PORT):
                raise RuntimeError(f"端口 {core.PREFERRED_PORT} 被占用")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(root / "backend")
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            task.log(f"启动服务：{python} -m app.main")
            proc = subprocess.Popen(
                [python, "-m", "app.main"], cwd=str(root / "backend"), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
            )
            self.service_proc = proc

            def pump() -> None:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    task.log(line.rstrip())
                task.log(f"服务进程已退出，退出码 {proc.returncode}")

            threading.Thread(target=pump, name="service-out", daemon=True).start()
            deadline = time.time() + 60
            attempt = 0
            while time.time() < deadline:
                attempt += 1
                task.progress("启动服务", attempt, 40, f"探测 {core.HOME_URL}（第 {attempt} 次）")
                if proc.poll() is not None:
                    raise RuntimeError("服务进程提前退出，详见日志")
                if self._health_ok():
                    task.progress("启动服务", 40, 40, "服务已就绪")
                    task.log(f"服务已就绪 {core.HOME_URL}")
                    return
                time.sleep(1.5)
            raise RuntimeError("60 秒内未就绪，详见日志")

        self.start_task("启动服务", work)

    @staticmethod
    def _health_ok() -> bool:
        import ssl
        import urllib.error
        import urllib.request

        context = ssl._create_unverified_context()
        for url in core.HEALTH_URLS:
            try:
                with urllib.request.urlopen(url, timeout=3, context=context) as resp:
                    if 200 <= resp.status < 500:
                        return True
            except urllib.error.HTTPError:
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def stop_service(self) -> None:
        proc = self.service_proc
        if proc is None or proc.poll() is not None:
            self.toast.show_text("没有由本程序启动的服务", "info", self.reduce_motion)
            return

        def work(task: Task) -> None:
            task.log(f"停止服务 PID={proc.pid}")
            core.run_capture(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=30)
            self.service_proc = None

        self.start_task("停止服务", work)

    def retry_last(self) -> None:
        handler = {
            "安装依赖": self.install_deps, "构建前端": self.build_frontend,
            "生成配置": self.generate_config, "启动服务": self.start_service,
            "环境体检": self.run_checks,
        }.get(self.last_action)
        if handler is None:
            self.toast.show_text("没有可重试的上一步", "info", self.reduce_motion)
            return
        self.append_log(f"重试：{self.last_action}")
        handler()

    def open_log_dir(self) -> None:
        root = self._current_root()
        target = (root / "data" / "logs") if root else Path.cwd()
        try:
            target.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                webbrowser.open(str(target))
            self.append_log(f"已打开：{target}")
        except Exception as exc:  # noqa: BLE001
            self.toast.show_text(f"打开失败：{exc}", "fail", self.reduce_motion)

    def copy_fix_hint(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.toast.show_text("先在体检表里选一行", "info", self.reduce_motion)
            return
        item = self.table.item(row, 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        check = self.checks.get(str(key))
        if check is None:
            return
        payload = getattr(check, "command", "") or getattr(check, "fix_hint", "")
        if not payload:
            self.toast.show_text("这一项没有修复命令", "info", self.reduce_motion)
            return
        QApplication.clipboard().setText(str(payload))
        self.toast.show_text("修复命令已复制", "ok", self.reduce_motion)

    def _on_check_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        check = self.checks.get(str(key))
        if check is None:
            return
        if not getattr(check, "ok", False):
            self.check_hint.setText(f"{check.name}：{getattr(check, 'fix_hint', '') or '无额外提示'}")
        else:
            self.check_hint.setText(f"{check.name}：{getattr(check, 'detail', '')}")

    # ---------------- 子进程 ----------------
    def _resolve_python(self) -> str | None:
        if self.current_env() == core.ENV_VENV:
            root = self._current_root()
            if root is not None:
                exe = core.venv_python_path(root)
                if exe.is_file():
                    return str(exe)
            self.toast.show_text("venv 不存在，先点「创建 venv」", "warn", self.reduce_motion)
            return None
        exe = self._probe_python()
        if not exe:
            self.toast.show_text("找不到 Python 3.10+", "fail", self.reduce_motion)
        return exe

    def _set_manual_commands(self) -> None:
        root = str(self._current_root() or r"<项目根>")
        py = (
            str(core.venv_python_path(self._current_root()))
            if self.current_env() == core.ENV_VENV and self._current_root() is not None
            else "python"
        )
        self.manual_edit.setText(
            f'{py} -m pip install -r requirements-torch.txt --index-url {core.TORCH_INDEX}  ｜  '
            f'{py} -m pip install -r requirements.pins.txt -i {core.PYPI_MIRROR}  ｜  '
            f'{py} scripts/download_models.py --size 1.7B --out "{root}\\models"'
        )
        self.manual_edit.setCursorPosition(0)

    def _run_stream(self, task: Task, cmd, cwd: Path | None = None, pip: bool = False) -> int:
        task.log("$ " + core.format_cmd(cmd))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            bufsize=0, creationflags=flags,
        )
        state = {"done": 0, "total": self._pip_total(cmd) if pip else 0}
        if pip:
            task.progress("安装依赖", 0, state["total"] or None, "启动 pip…")
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = core.decode_process_line(raw)
                task.log(line)
                if pip:
                    self._pip_line(task, line, state)
        code = proc.wait()
        task.log(f"  退出码：{code}")
        return code

    @staticmethod
    def _pip_total(cmd) -> int:
        try:
            for idx, part in enumerate(cmd):
                if part in ("-r", "--requirement") and idx + 1 < len(cmd):
                    req = Path(str(cmd[idx + 1]))
                    if req.is_file():
                        lines = [ln for ln in req.read_text(encoding="utf-8", errors="ignore").splitlines()
                                 if ln.strip() and not ln.strip().startswith("#")]
                        return max(1, len(lines))
        except OSError:
            pass
        return 0

    @staticmethod
    def _pip_line(task: Task, line: str, state: dict) -> None:
        low = line.lower()
        total = state.get("total") or None
        if low.startswith("collecting "):
            state["done"] = state.get("done", 0) + 1
            task.progress("安装依赖", state["done"], total, "收集 " + line.split("Collecting", 1)[1].strip()[:40])
        elif low.startswith(("downloading ", "using cached ")):
            task.progress("安装依赖", state.get("done", 0), total, "下载 " + line.split(maxsplit=2)[-1][:50])
        elif low.startswith("installing collected packages"):
            task.progress("安装依赖", state.get("done", 0), total, "安装已下载的包…")
        elif low.startswith("requirement already satisfied"):
            state["done"] = state.get("done", 0) + 1
            task.progress("安装依赖", state["done"], total, "已满足")

    # ---------------- 关闭 ----------------
    def closeEvent(self, event) -> None:  # noqa: N802
        running = self.task is not None and self.task.isRunning()
        service_alive = self.service_proc is not None and self.service_proc.poll() is None
        if running or service_alive:
            parts = []
            if running:
                parts.append(f"任务「{self.last_action}」正在执行")
            if service_alive:
                parts.append("服务仍在运行")
            answer = QMessageBox.question(
                self, APP_TITLE, "、".join(parts) + "，确定退出吗？\n（退出会停止本程序启动的服务）"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if service_alive and self.service_proc is not None:
            core.run_capture(["taskkill", "/PID", str(self.service_proc.pid), "/T", "/F"], timeout=20)
        event.accept()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="声匠 VoiceArtisan 一键安装器（PySide6）")
    parser.add_argument("--root", default="")
    parser.add_argument("--self-test", nargs="?", const="", metavar="ROOT")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--env", default=core.ENV_SYSTEM, choices=[core.ENV_SYSTEM, core.ENV_VENV])
    args, _unknown = parser.parse_known_args(argv)

    if args.self_test is not None:
        root = args.self_test or args.root or str(Path.cwd())
        return core.self_test(Path(root), args.json, core.normalize_env_choice(args.env))

    app = QApplication(sys.argv[:1])
    app.setApplicationName("声匠 VoiceArtisan 一键安装器")
    window = Launcher(root=args.root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
