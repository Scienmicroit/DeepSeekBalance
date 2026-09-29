"""DeepSeek API 余额监控 - 托盘 + 主界面
用法:
    DeepSeekBalance.exe           显示主界面
    DeepSeekBalance.exe --hidden  仅托盘（开机自启用）
    python main.py --check        命令行查一次余额
    python main.py --selftest     离线自测
"""
import base64
import ctypes
import ctypes.wintypes
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap, QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
    QPushButton, QRadioButton, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

BALANCE_URL = "https://api.deepseek.com/user/balance"
CONSOLE_URL = "https://platform.deepseek.com/usage"
CONFIG_FILE = Path(os.environ["APPDATA"]) / "DeepSeekBalance" / "config.json"
DEFAULT_INTERVAL = 60


# ---------- Windows DPAPI 加密 ----------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.c_void_p)]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32
# 必须声明：LocalFree 的参数是 64 位指针，不声明会按 32 位 int 转换而溢出
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _dpapi(data: bytes, protect: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    inp = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))
    out = _DATA_BLOB()
    fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
    desc = "DeepSeekBalance" if protect else None
    if not fn(ctypes.byref(inp), desc, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI 加解密失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(out.pbData)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg), "utf-8")


def load_api_key() -> str | None:
    try:
        cfg = load_config()
        return _dpapi(base64.b64decode(cfg["key"]), protect=False).decode("utf-8")
    except (OSError, KeyError, ValueError):
        return None


def save_api_key(key: str) -> None:
    cfg = load_config()
    cfg["key"] = base64.b64encode(_dpapi(key.encode(), protect=True)).decode()
    save_config(cfg)


# ---------- 磨砂亚克力（Win10/11 未公开 API） ----------

ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def enable_acrylic(hwnd: int, tint: int = 0xCCFFFFFF) -> bool:
    """开启窗口磨砂亚克力。tint 为 0xAABBGGRR，默认 80% 白。失败返回 False。"""
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    policy = _ACCENTPOLICY(ACCENT_ENABLE_ACRYLICBLURBEHIND, 2, tint, 0)
    data = _WINCOMPATTRDATA(WCA_ACCENT_POLICY,
                            ctypes.cast(ctypes.byref(policy), ctypes.c_void_p),
                            ctypes.sizeof(policy))
    return bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))


def enable_round_corners(hwnd: int) -> bool:
    """Win11 原生圆角。顺带拿到 DWM 系统阴影——所以不要再调 SetWindowRgn，
    窗口 region 会把阴影一起剪掉（实测阴影差值从 22 掉到 0）。"""
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                              ctypes.c_void_p, ctypes.c_uint]
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        rc = dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33,
                                       ctypes.byref(pref), ctypes.sizeof(pref))
        return rc == 0
    except (AttributeError, OSError):
        return False


def _round_window(hwnd: int, radius: int = 26) -> None:
    """Win10 兜底：自己裁圆角。代价是没有系统阴影，属于已知取舍。"""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
    except (AttributeError, OSError):
        pass


# ---------- 外观 ----------

def _arrow_png(up: bool) -> str:
    """Qt 的 QSS 箭头只能引用图片，运行时画两个三角存到临时目录。"""
    out = Path(tempfile.gettempdir()) / "ds-balance-ui"
    out.mkdir(exist_ok=True)
    path = out / ("arrow-up.png" if up else "arrow-down.png")
    if not path.exists():
        pm = QPixmap(18, 18)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#2563EB"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(4, 11), QPointF(14, 11), QPointF(9, 6)]) if up
                      else QPolygonF([QPointF(4, 7), QPointF(14, 7), QPointF(9, 12)]))
        p.end()
        pm.save(str(path))
    return path.as_posix()


def theme_qss() -> str:
    return _THEME_QSS.format(arrow_up=_arrow_png(True), arrow_down=_arrow_png(False))


_THEME_QSS = """
* {{ font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", "Segoe UI"; color: #334155; }}
QMainWindow, #glow {{ background: transparent; }}
#card {{ background: rgba(255, 255, 255, 150); border: 1px solid rgba(37, 99, 235, 45); border-radius: 18px; }}
#caption {{ color: #64748B; font-size: 13px; }}
#balance {{ color: #2563EB; font-size: 46px; font-weight: 700; }}
#sub {{ color: #64748B; font-size: 13px; }}
#status {{ color: #64748B; font-size: 12px; }}
#statusError {{ color: #DC2626; font-size: 12px; }}
#title {{ color: #1E293B; font-size: 13px; font-weight: 600; }}
QLabel#fieldLabel {{ color: #64748B; font-size: 13px; }}
QPushButton {{
    background: rgba(255, 255, 255, 160); border: 1px solid rgba(37, 99, 235, 70);
    border-radius: 10px; padding: 9px 18px; color: #2563EB; font-size: 13px;
}}
QPushButton:hover {{ background: rgba(37, 99, 235, 30); border-color: rgba(37, 99, 235, 120); }}
QPushButton:pressed {{ background: rgba(37, 99, 235, 70); }}
QPushButton#primary {{ background: #2563EB; color: #FFFFFF; border: none; }}
QPushButton#primary:hover {{ background: #1D4ED8; }}
QPushButton#primary:disabled {{ background: rgba(37, 99, 235, 100); }}
QPushButton#captionBtn, QPushButton#closeBtn, QPushButton#settingsBtn {{
    background: transparent; border: none; border-radius: 6px; padding: 0;
    color: #64748B; font-size: 14px;
}}
QPushButton#settingsBtn {{ font-family: "Segoe UI Symbol"; font-size: 15px; }}
QPushButton#captionBtn:hover, QPushButton#settingsBtn:hover {{
    background: rgba(37, 99, 235, 40); color: #2563EB;
}}
QPushButton#closeBtn:hover {{ background: rgba(220, 38, 38, 210); color: #FFFFFF; }}
QRadioButton {{ color: #334155; font-size: 13px; spacing: 8px; padding: 2px 0; }}
QSpinBox {{
    background: rgba(255, 255, 255, 160); border: 1px solid rgba(37, 99, 235, 70);
    border-radius: 10px; padding: 3px 30px 3px 12px; color: #2563EB; font-size: 13px;
    min-height: 30px;
}}
QSpinBox:focus {{ border-color: #2563EB; }}
QSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 22px; margin: 4px 4px 0 0; border: none;
    background: transparent; border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 22px; margin: 0 4px 4px 0; border: none;
    background: transparent; border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: rgba(37, 99, 235, 45); }}
QSpinBox::up-arrow {{ image: url({arrow_up}); width: 14px; height: 14px; }}
QSpinBox::down-arrow {{ image: url({arrow_down}); width: 14px; height: 14px; }}
QLineEdit {{
    background: rgba(255, 255, 255, 200); border: 1px solid rgba(37, 99, 235, 70);
    border-radius: 10px; padding: 8px 12px; font-size: 13px;
}}
QLineEdit:focus {{ border-color: #2563EB; }}
"""


class GlowBackground(QWidget):
    """白色磨砂底 + 蓝色光晕点缀。亚克力不可用时退化为不透明白底。"""

    def __init__(self, acrylic_ok: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("glow")
        self.acrylic_ok = acrylic_ok

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 底色交给系统磨砂时留半透明，让亚克力透出来；否则纯白兜底
        p.fillRect(self.rect(), QColor(255, 255, 255, 80 if self.acrylic_ok else 255))

        # 蓝色光晕点缀：只压在右上、左下两个角，中间留白
        span = max(w, h)
        for cx, cy, radius, alpha in (
            (w * 1.02, h * -0.06, span * 0.55, 85),
            (w * -0.02, h * 1.06, span * 0.52, 65),
        ):
            grad = QRadialGradient(QPointF(cx, cy), radius)
            grad.setColorAt(0.0, QColor(59, 130, 246, alpha))
            grad.setColorAt(0.55, QColor(59, 130, 246, int(alpha * 0.35)))
            grad.setColorAt(1.0, QColor(59, 130, 246, 0))
            p.setBrush(grad)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), radius, radius)
        p.end()


# ---------- 余额查询 ----------

def fetch_balance(api_key: str) -> list[dict]:
    req = urllib.request.Request(BALANCE_URL, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API 返回 {e.code}（请检查 API Key）") from e
    except OSError as e:
        raise RuntimeError(f"网络错误: {e}") from e
    infos = data.get("balance_infos")
    if not infos:
        raise RuntimeError("响应中无余额数据")
    return infos


def format_balance(infos: list[dict]) -> str:
    return " / ".join(
        f"{i.get('currency')} {i.get('total_balance')}（充值 {i.get('topped_up_balance')} + 赠送 {i.get('granted_balance')}）"
        for i in infos
    )


def selftest() -> int:
    sample = {"balance_infos": [{"currency": "CNY", "total_balance": "110.00",
                                "granted_balance": "10.00", "topped_up_balance": "100.00"}]}
    out = format_balance(sample["balance_infos"])
    assert out == "CNY 110.00（充值 100.00 + 赠送 10.00）", out
    assert _dpapi(_dpapi(b"sk-test-123", True), protect=False) == b"sk-test-123"
    print("selftest ok")
    return 0


def check() -> int:
    key = load_api_key()
    if not key:
        print("No API key configured. 请先运行主程序输入 Key")
        return 1
    try:
        print(format_balance(fetch_balance(key)))
    except Exception as e:
        print(f"查询失败: {e}")
        return 1
    return 0


# ---------- 界面 ----------

def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#4D6BFE"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    f = QFont("Segoe UI", 30)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    p.drawText(pm.rect(), Qt.AlignCenter, "¥")
    p.end()
    return QIcon(pm)


class TitleBar(QWidget):
    """自绘标题栏：半透明窗口下原生标题栏不绘制文字，只能自己画。"""

    def __init__(self, parent):
        super().__init__(parent)
        self._drag_offset = None
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 10, 0)
        lay.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(make_icon().pixmap(18, 18))
        lay.addWidget(icon)
        title = QLabel("DeepSeek 余额监控")
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addStretch()

        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("settingsBtn")
        self.btn_settings.setToolTip("设置")
        self.btn_settings.setFixedSize(34, 26)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(lambda: self.window().toggle_settings())
        lay.addWidget(self.btn_settings)

        for text, slot, name in (("—", self._minimize, "captionBtn"),
                                 ("✕", self._close, "closeBtn")):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.setFixedSize(34, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

    def set_settings_state(self, in_settings: bool):
        self.btn_settings.setText("←" if in_settings else "⚙")
        self.btn_settings.setToolTip("返回" if in_settings else "设置")

    def _minimize(self):
        self.window().showMinimized()

    def _close(self):
        self.window().on_close_clicked()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.window().frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        self.window().hide_to_tray()


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.api_key = load_api_key()
        self.cfg = load_config()
        self.last_infos = None
        self._notified = False

        self.setWindowTitle("DeepSeek 余额监控")
        self.setWindowIcon(make_icon())
        # 无边框 + 半透明：拿系统亚克力做磨砂底，标题栏只能自绘
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(460, 370)
        # 必须先建出原生窗口句柄，亚克力/圆角/最小化动画才能挂上去
        hwnd = int(self.winId())
        self.acrylic_ok = enable_acrylic(hwnd)
        if not enable_round_corners(hwnd):
            _round_window(hwnd)

        glow = GlowBackground(self.acrylic_ok)
        self.setCentralWidget(glow)
        root = QVBoxLayout(glow)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_main_page())
        self.stack.addWidget(self._build_settings_page())
        root.addWidget(self.stack)

        self.setup_tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.set_interval(int(self.cfg.get("interval", DEFAULT_INTERVAL)))

        if not self.api_key:
            self.toggle_settings(True)
            self.set_status("请先在设置里填入 API Key", error=True)
        else:
            self.refresh()

    # ---------- 页面 ----------

    def _build_main_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 6, 24, 24)
        lay.setSpacing(14)

        card = QFrame()
        card.setObjectName("card")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(24, 22, 24, 22)
        card_lay.setSpacing(6)

        caption = QLabel("当前余额")
        caption.setObjectName("caption")
        self.big = QLabel("¥ --")
        self.big.setObjectName("balance")
        self.sub = QLabel("等待查询…")
        self.sub.setObjectName("sub")
        for w in (caption, self.big, self.sub):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_lay.addWidget(w)
        lay.addWidget(card)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch()

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.btn_refresh = QPushButton("立即刷新")
        self.btn_refresh.setObjectName("primary")
        self.btn_refresh.clicked.connect(self.refresh)
        btns.addWidget(self.btn_refresh)
        btn_console = QPushButton("打开控制台")
        btn_console.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CONSOLE_URL)))
        btns.addWidget(btn_console)
        lay.addLayout(btns)
        return page

    def _field(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 6, 24, 24)
        lay.setSpacing(16)

        lay.addWidget(self._field("点击关闭按钮时"))
        row = QHBoxLayout()
        row.setSpacing(18)
        self.rb_tray = QRadioButton("最小化到托盘")
        self.rb_quit = QRadioButton("直接退出应用")
        for rb in (self.rb_tray, self.rb_quit):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            rb.toggled.connect(self.on_close_action_changed)
            row.addWidget(rb)
        row.addStretch()
        lay.addLayout(row)

        lay.addWidget(self._field("刷新间隔"))
        self.spin = QSpinBox()
        self.spin.setRange(30, 600)
        self.spin.setSuffix(" 秒")
        self.spin.setFixedWidth(120)
        self.spin.valueChanged.connect(self.on_interval_changed)
        spin_row = QHBoxLayout()
        spin_row.addWidget(self.spin)
        spin_row.addStretch()
        lay.addLayout(spin_row)

        lay.addWidget(self._field("DeepSeek API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        if self.api_key:
            self.key_edit.setText(self.api_key)
        self.key_edit.returnPressed.connect(self.save_key_from_settings)
        key_row.addWidget(self.key_edit)
        self.btn_save_key = QPushButton("保存")
        self.btn_save_key.setObjectName("primary")
        self.btn_save_key.clicked.connect(self.save_key_from_settings)
        key_row.addWidget(self.btn_save_key)
        lay.addLayout(key_row)

        self.settings_status = QLabel("Key 加密后只保存在本机")
        self.settings_status.setObjectName("status")
        lay.addWidget(self.settings_status)
        lay.addStretch()

        self.rb_tray.setChecked(self.cfg.get("close_action", "tray") != "quit")
        self.rb_quit.setChecked(self.cfg.get("close_action", "tray") == "quit")
        return page

    def toggle_settings(self, show_settings: bool | None = None):
        if show_settings is None:
            show_settings = self.stack.currentIndex() == 0   # 当前在主界面 -> 切到设置
        self.stack.setCurrentIndex(1 if show_settings else 0)
        self.title_bar.set_settings_state(show_settings)

    def save_key_from_settings(self):
        key = self.key_edit.text().strip()
        if not key:
            self.settings_status.setText("API Key 不能为空")
            return
        self.api_key = key
        save_api_key(key)
        self.settings_status.setText("已保存")
        self.refresh()

    # ---------- 托盘 ----------

    def setup_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("DeepSeek 余额监控")
        menu = QMenu()
        self.act_tray_balance = menu.addAction("余额：查询中…")
        self.act_tray_balance.setEnabled(False)   # 只作展示，不可点
        menu.addSeparator()
        menu.addAction("显示主界面", self.show_window)
        menu.addAction("立即刷新", self.refresh)
        menu.addSeparator()
        menu.addAction("退出", self.really_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self.show_window()

    def show_window(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def on_close_clicked(self):
        """关闭按钮的行为由设置决定。"""
        if self.cfg.get("close_action", "tray") == "quit":
            self.really_quit()
        else:
            self.hide_to_tray()

    def hide_to_tray(self):
        """藏到托盘并首次提示，避免用户以为程序已退出。"""
        self.hide()
        if not self._notified:
            self._notified = True
            self.tray.showMessage("DeepSeek 余额监控", "已最小化到托盘，右键托盘图标可退出",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)

    def closeEvent(self, event):
        # 统一走 on_close_clicked，Alt+F4 也遵循设置里的关闭行为
        event.ignore()
        self.on_close_clicked()

    def really_quit(self):
        self.tray.hide()
        self.app.quit()

    # ---------- 刷新 ----------

    def set_status(self, text, error=False):
        self.status.setText(text)
        self.status.setObjectName("statusError" if error else "status")
        self.status.setStyleSheet("")   # 触发按新 objectName 重新套用 QSS
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def refresh(self):
        if not self.api_key:
            return
        self.btn_refresh.setEnabled(False)
        try:
            self.last_infos = fetch_balance(self.api_key)
            self.big.setText(f"¥ {self.last_infos[0]['total_balance']}")
            if len(self.last_infos) == 1:
                i = self.last_infos[0]
                self.sub.setText(f"充值 ¥{i['topped_up_balance']} + 赠送 ¥{i['granted_balance']}")
            else:
                self.sub.setText(format_balance(self.last_infos))
            self.set_status(f"上次更新 {datetime.now():%H:%M:%S}")
            self.act_tray_balance.setText(f"余额：¥{self.last_infos[0]['total_balance']}")
            self.tray.setToolTip(format_balance(self.last_infos))
        except Exception as e:
            self.set_status(f"更新失败: {e}", error=True)
            self.act_tray_balance.setText("余额：获取失败")
            self.tray.setToolTip("DeepSeek 余额更新失败")
        finally:
            self.btn_refresh.setEnabled(True)

    def on_close_action_changed(self):
        self.cfg["close_action"] = "quit" if self.rb_quit.isChecked() else "tray"
        save_config(self.cfg)

    def on_interval_changed(self, secs: int):
        self.cfg["interval"] = secs
        save_config(self.cfg)
        self.timer.start(secs * 1000)

    def set_interval(self, secs: int):
        self.spin.blockSignals(True)
        self.spin.setValue(secs)
        self.spin.blockSignals(False)
        self.timer.start(secs * 1000)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(theme_qss())
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow(app)
    if "--hidden" not in sys.argv:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--check" in sys.argv:
        sys.exit(check())
    main()
