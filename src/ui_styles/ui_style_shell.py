#!/usr/bin/env python3
"""
ui_style_shell.py — Config-driven PyQt5 UI shell (TinyImgApp style).

Turn ANY Python function into a styled desktop app from a JSON config.
Visual identity matches the TinyImgApp family exactly: transparent
frameless window with #CDEBF0 border, large cover image, pill-shaped
combo selectors (radius 20, underline, padding 20), single-button
folder pickers, black "cooking" run button, close button only.
No labels, no log panel, no clutter — simple and elegant.

Dialogs are Bauhaus style: black field, accent border, one flat
geometric mark centered on top (● info / ▲ warning / ■ error),
text below, OK button centered. No emoji, no system icons.

FILE STRUCTURE (search these markers to jump around):

    [1] STYLE CUSTOMIZATION   <- edit fonts / sizes / colors HERE
    [2] THEME                 stylesheet builders (read from [1])
    [3] CLOSE BUTTON          top-right ✕
    [4] LOGO RESOLUTION       robust cover image search
    [5] WORKER THREAD         runs your function off the UI thread
    [6] PARAM WIDGETS         one config entry -> one widget
    [7] MAIN SHELL            window layout + run flow + drag
    [8] HELPERS               load_config / run_shell
    [9] SELF-DEMO             python ui_style_shell.py

Quick start:

    from ui_style_shell import run_shell

    def my_task(folder: str, size: int, export: bool,
                progress_callback=None) -> str:
        ...
        return "done"

    CONFIG = {
        "app": {"title": "MyApp", "logo": "static/cover.png",
                "size": [540, 880]},
        "params": [
            {"name": "folder", "type": "folder",
             "label": "Select Image Folder", "required": True},
            {"name": "size", "type": "select",
             "label": "Radial Copies",
             "options": [700, 500, 300], "default": 700},
            {"name": "export", "type": "toggle",
             "label": "Export TXT", "default": True},
        ],
        "run": {"label": "Start Cooking"},
    }

    run_shell(my_task, CONFIG)

Config schema:

    app:
        title   str    fallback text when no logo image found
        logo    str    image path (searched robustly, see logo resolution)
        size    [w,h]  window size (default [540, 880])
        accent / accent_hover   colors (defaults #CDEBF0 / #BEE0E8)
        close_size       int    close button diameter (overrides [1])
        close_font_size  int    close ✕ glyph size (overrides [1])

    params: ordered list — UI built strictly in this order
        name      str   REQUIRED. kwarg name passed to the function
        type      str   REQUIRED: toggle | select | input | password |
                        int | float | folder | file | save_file
        label     str   caption shown in the widget. For folder/file/
                        save_file it's the button text; for toggle it's
                        the checkbox text; for select it's the caption
                        shown to the LEFT of the dropdown (e.g. "Radial
                        Copies") so the user knows what the number means
                        before they open it. Defaults to the title-cased
                        param name if omitted — always settable in config.
        default         initial value
        required  bool  block run until set
        options   list  for select
        placeholder str for input
        filter    str   for file/save_file dialogs
        visible_when {"name": ..., "equals": ...}

    run:
        label str   run button text (default "Start Cooking")

Function contract:
    * receives every param as a keyword argument
    * MAY declare progress_callback (int 0-100), log_callback (str),
      cancel_check () -> bool; injected only if the signature accepts them
    * returned string is shown in the success dialog
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from PyQt5.QtCore import Qt, QPoint, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QComboBox,
    QCheckBox, QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox,
    QProgressBar, QDialog, QSizePolicy,
)

__all__ = ["FunctionShell", "run_shell", "Theme", "load_config"]
__version__ = "2.6.0"

logger = logging.getLogger(__name__)


# =========================================================================== #
# [1] STYLE CUSTOMIZATION — edit these, no need to touch code below
# =========================================================================== #

# ── Close button (top-right ✕) ─────────────────────────────────────────────
CLOSE_BTN_SIZE      = 64          # circle diameter in px (was 34, too small)
CLOSE_FONT_SIZE     = 30          # ✕ glyph size in px
CLOSE_FONT_BOLD     = True        # bold glyph
CLOSE_HOVER_COLOR   = "white"     # hover color
CLOSE_MARGIN        = 8           # gap around the button

# ── Fonts ──────────────────────────────────────────────────────────────────
FONT_FAMILY         = "Arial"     # global font family
SELECT_FONT_SIZE    = 20          # combo selector (select) text size
SELECT_FONT_BOLD    = True        # combo selector (select) text bold
SELECT_LABEL_DIM    = 0.72        # select caption size, as a ratio of
                                   # SELECT_FONT_SIZE (caption is smaller
                                   # / lighter than the value so the two
                                   # are visually distinct)
PILL_FONT_SIZE      = 18          # toggles (pill widgets)
PILL_FONT_BOLD      = True        # toggle text bold
PICKER_FONT_SIZE    = 20          # folder / file picker buttons
PICKER_FONT_BOLD    = True        # picker button text bold
RUN_FONT_SIZE       = 18          # black "Start Cooking" run button
RUN_FONT_BOLD       = True        # run button text bold
INPUT_FONT_SIZE     = 18          # QLineEdit text inputs
INPUT_FONT_BOLD     = True        # input text bold
TITLE_FONT_SIZE     = 24          # fallback title (when no logo image)

# ── Dialog (Bauhaus style) ─────────────────────────────────────────────────
DIALOG_FONT_SIZE    = 17          # dialog body text
DIALOG_SYMBOL_SIZE  = 44          # geometric symbol size (px)
DIALOG_RADIUS       = 12          # dialog corner radius
DIALOG_BORDER       = 2           # dialog border thickness
DIALOG_MIN_W        = 380         # dialog minimum width
DIALOG_SYMBOLS = {                # flat geometric marks, no emoji
    "info":    "●",
    "warning": "▲",
    "error":   "■",
}

# ── Colors (app config "accent"/"accent_hover" override these) ─────────────
ACCENT              = "#CDEBF0"   # main pastel accent
ACCENT_HOVER        = "#BEE0E8"   # hover accent
INK                 = "black"     # text / run-button background

# ── Shape & spacing ────────────────────────────────────────────────────────
ROW_GAP             = 2           # vertical gap between rows (layout spacing)
WINDOW_RADIUS       = 20          # window corner radius
WINDOW_BORDER       = 2           # window border thickness
PILL_RADIUS         = 20          # pill widgets corner radius
PILL_PADDING        = 20          # pill widgets inner padding
BTN_RADIUS          = 8           # picker buttons corner radius
BTN_PADDING         = 10          # picker buttons inner padding
BTN_MARGIN          = 10          # picker buttons outer margin (also used
                                   # for the select pill and toggle, so all
                                   # three line up to the exact same width)
BTN_HEIGHT          = 56          # shared fixed height for picker buttons,
                                   # the select pill, and the toggle — set in
                                   # code via setFixedHeight so all three
                                   # always match pixel-for-pixel regardless
                                   # of each widget's own natural sizeHint
PROGRESS_HEIGHT     = 20          # progress bar height

# ── Logo ───────────────────────────────────────────────────────────────────
LOGO_MAX_W          = 500         # cover image max width
LOGO_MAX_H          = 800         # cover image max height


# =========================================================================== #
# [2] THEME — stylesheet builders (all values come from section [1])
# =========================================================================== #

class Theme:
    def __init__(self, accent: str = ACCENT,
                 accent_hover: str = ACCENT_HOVER, ink: str = INK) -> None:
        self.accent = accent
        self.accent_hover = accent_hover
        self.ink = ink

    # window + generic buttons + text inputs
    def root(self) -> str:
        picker_weight = "bold" if PICKER_FONT_BOLD else "normal"
        input_weight = "bold" if INPUT_FONT_BOLD else "normal"
        return f"""
            QWidget {{
                font-family: '{FONT_FAMILY}';
                background-color: transparent;
                border: {WINDOW_BORDER}px solid {self.accent};
                border-radius: {WINDOW_RADIUS}px;
            }}
            QPushButton {{
                background-color: {self.accent};
                color: {self.ink};
                font-weight: {picker_weight};
                font-size: {PICKER_FONT_SIZE}px;
                border-radius: {BTN_RADIUS}px;
                padding: {BTN_PADDING}px;
                margin: {BTN_MARGIN}px;
            }}
            QPushButton:hover {{
                background-color: {self.accent_hover};
            }}
            QLineEdit {{
                border: 2px solid #ccc;
                border-radius: {BTN_RADIUS}px;
                padding: 8px;
                margin: {BTN_MARGIN}px;
                background-color: white;
                color: {self.ink};
                font-weight: {input_weight};
                font-size: {INPUT_FONT_SIZE}px;
            }}
        """

    # select pill CONTAINER (caption + combo live inside this). Scoped to
    # #SelectPill only so it never fights the generic QWidget rule above.
    # Uses the SAME radius / font size / weight as the picker buttons
    # (BTN_RADIUS, PICKER_FONT_SIZE) — plus a shared fixed height set in
    # code — so the select row is visually identical to a picker button,
    # just with an extra caption on the left.
    def select_pill(self) -> str:
        weight = "bold" if PICKER_FONT_BOLD else "normal"
        label_size = max(10, round(PICKER_FONT_SIZE * SELECT_LABEL_DIM))
        return f"""
            QWidget#SelectPill {{
                background-color: {self.accent};
                border: none;
                border-radius: {BTN_RADIUS}px;
                margin: {BTN_MARGIN}px;
            }}
            QWidget#SelectPill QLabel#SelectCaption {{
                background: transparent;
                border: none;
                color: {self.ink};
                font-size: {label_size}px;
                font-weight: {weight};
            }}
            QWidget#SelectPill QComboBox {{
                background: transparent;
                border: none;
                color: {self.ink};
                font-size: {PICKER_FONT_SIZE}px;
                font-weight: {weight};
                padding: 0px;
            }}
            QWidget#SelectPill QComboBox:hover {{
                color: {self.ink};
            }}
            QWidget#SelectPill QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
            QWidget#SelectPill QComboBox::down-arrow {{
                width: 10px;
                height: 10px;
            }}
            QWidget#SelectPill QComboBox QAbstractItemView {{
                background-color: white;
                color: {self.ink};
                selection-background-color: {self.accent_hover};
                selection-color: {self.ink};
                outline: none;
                padding: 4px;
            }}
        """

    # black run button
    def cooking(self) -> str:
        weight = "bold" if RUN_FONT_BOLD else "normal"
        return f"""
            font-size: {RUN_FONT_SIZE}px;
            font-weight: {weight};
            color: {self.accent};
            background-color: {self.ink};
            border-radius: {PILL_RADIUS}px;
            text-decoration: underline;
            padding: {PILL_PADDING}px;
        """

    def progress(self) -> str:
        return f"""
            QProgressBar {{
                border: 2px solid {self.accent};
                border-radius: {BTN_RADIUS}px;
                background: white;
                height: {PROGRESS_HEIGHT}px;
            }}
            QProgressBar::chunk {{
                background: {self.ink};
                border-radius: {BTN_RADIUS}px;
            }}
        """

    # Bauhaus dialog: black field, accent border, accent text
    def message_box(self) -> str:
        return f"""
            QDialog {{
                background-color: {self.ink};
                border: {DIALOG_BORDER}px solid {self.accent};
                border-radius: {DIALOG_RADIUS}px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {self.accent};
            }}
            QPushButton {{
                background-color: {self.ink};
                color: {self.accent};
                border: {DIALOG_BORDER}px solid {self.accent};
                border-radius: {BTN_RADIUS}px;
                padding: 8px 28px;
                margin: 0px;
                font-size: {DIALOG_FONT_SIZE}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {self.accent};
                color: {self.ink};
            }}
        """


# =========================================================================== #
# [3] CLOSE BUTTON — big bold ✕, size/font from section [1] or app config
# =========================================================================== #

class CloseButton(QPushButton):
    def __init__(self, parent: QWidget, theme: Theme,
                 size: int = CLOSE_BTN_SIZE,
                 font_size: int = CLOSE_FONT_SIZE) -> None:
        super().__init__("✕", parent)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        weight = "bold" if CLOSE_FONT_BOLD else "normal"
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {theme.accent};
                color: {theme.ink};
                border: none;
                border-radius: {size // 2}px;
                font-weight: {weight};
                font-size: {font_size}px;
                margin: {CLOSE_MARGIN}px;
                padding: 0px;
            }}
            QPushButton:hover {{ background-color: {CLOSE_HOVER_COLOR}; }}
        """)
        self.clicked.connect(parent.close)


# =========================================================================== #
# [4] LOGO RESOLUTION — robust cover image search
# =========================================================================== #

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp")


def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS          # PyInstaller temp dir
    return os.path.dirname(os.path.abspath(sys.argv[0] or __file__))


def find_logo(hint: str | None) -> str | None:
    """Resolve a logo image: explicit path, else search static/assets dirs
    for anything matching 'cover' / 'logo'."""
    if hint and os.path.exists(hint):
        return hint
    root = _app_root()
    search_dirs = [os.path.join(root, d)
                   for d in ("static", "assets", "resources", "")]
    patterns = ([Path(hint).stem] if hint else []) + ["cover", "logo"]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for pat in patterns:
            for ext in _IMG_EXT:
                p = os.path.join(d, f"{pat}{ext}")
                if os.path.exists(p):
                    return p
    return None


# =========================================================================== #
# [5] WORKER THREAD — runs the wrapped function off the UI thread
# =========================================================================== #

class _Worker(QThread):
    progressed = pyqtSignal(int)
    logged = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable, kwargs: dict[str, Any]) -> None:
        super().__init__()
        self.fn = fn
        self.kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            sig = inspect.signature(self.fn)
            kwargs = dict(self.kwargs)
            # inject callbacks only if the function's signature accepts them
            if "progress_callback" in sig.parameters:
                kwargs["progress_callback"] = self.progressed.emit
            if "log_callback" in sig.parameters:
                kwargs["log_callback"] = self.logged.emit
            if "cancel_check" in sig.parameters:
                kwargs["cancel_check"] = lambda: self._cancelled
            self.finished_ok.emit(self.fn(**kwargs))
        except Exception:
            self.failed.emit(traceback.format_exc())


# =========================================================================== #
# [6] PARAM WIDGETS — one config entry -> one widget, no caption labels
#     (except "select", which shows a small caption so the user knows what
#     the number means before they open the dropdown — see _build_select)
# =========================================================================== #

class _ParamRow:
    """One config entry -> a single widget."""

    def __init__(self, spec: dict, theme: Theme, parent: QWidget) -> None:
        self.spec = spec
        self.name: str = spec["name"]
        self.type: str = spec["type"]
        self.required: bool = bool(spec.get("required", False))
        self.label_text: str = spec.get(
            "label", self.name.replace("_", " ").title())

        builder = getattr(self, f"_build_{self.type}", None)
        if builder is None:
            raise ValueError(
                f"unknown param type '{self.type}' for '{self.name}'")
        self.widget: QWidget = builder(spec, theme, parent)
        if spec.get("tooltip"):
            self.widget.setToolTip(spec["tooltip"])

    # ---- builders --------------------------------------------------------- #

    def _build_select(self, spec, theme, parent) -> QWidget:
        """Pill-shaped row: a small caption (from config 'label', e.g.
        'Radial Copies') on the left so the user knows what the value
        means BEFORE opening the dropdown, and the combo box value on
        the right. Wrapped in one #SelectPill container so the whole
        row is exactly the same width as the picker/run buttons above
        and below it (see Theme.select_pill)."""
        container = QWidget(parent)
        container.setObjectName("SelectPill")
        container.setStyleSheet(theme.select_pill())
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        container.setFixedHeight(BTN_HEIGHT)

        row = QHBoxLayout(container)
        row.setContentsMargins(BTN_PADDING * 2, 0, BTN_PADDING * 2, 0)
        row.setSpacing(10)

        caption = QLabel(self.label_text, container)
        caption.setObjectName("SelectCaption")
        row.addWidget(caption)

        combo = QComboBox(container)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.setCursor(Qt.PointingHandCursor)
        self._values = list(spec.get("options", []))
        combo.addItems([str(o) for o in self._values])
        if "default" in spec and spec["default"] in self._values:
            combo.setCurrentIndex(self._values.index(spec["default"]))
        row.addWidget(combo, 1)

        self.get = lambda: (self._values[combo.currentIndex()]
                            if self._values else None)
        self._signal = combo.currentIndexChanged
        return container

    def _build_toggle(self, spec, theme, parent) -> QWidget:
        """Styled to match the picker buttons exactly: same corner radius,
        same font size/weight, same outer margin, same fixed height — just
        with a small switch indicator in place of button text alone."""
        w = QCheckBox(self.label_text, parent)
        w.setChecked(bool(spec.get("default", False)))
        w.setCursor(Qt.PointingHandCursor)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        w.setFixedHeight(BTN_HEIGHT)
        weight = "bold" if PICKER_FONT_BOLD else "normal"
        w.setStyleSheet(f"""
            QCheckBox {{
                font-size: {PICKER_FONT_SIZE}px;
                font-weight: {weight};
                color: {theme.ink};
                background-color: {theme.accent};
                border-radius: {BTN_RADIUS}px;
                padding: 0px {BTN_PADDING * 2}px;
                margin: {BTN_MARGIN}px;
                spacing: 12px;
            }}
            QCheckBox::indicator {{
                width: 40px; height: 22px; border-radius: 11px;
                background-color: white;
            }}
            QCheckBox::indicator:checked {{
                background-color: {theme.ink};
            }}
        """)
        self.get = w.isChecked
        self._signal = w.stateChanged
        return w

    def _build_input(self, spec, theme, parent) -> QWidget:
        w = QLineEdit(parent)
        w.setPlaceholderText(spec.get("placeholder", self.label_text))
        w.setText(str(spec.get("default", "")))
        self.get = w.text
        self._signal = w.textChanged
        return w

    def _build_password(self, spec, theme, parent) -> QWidget:
        w = self._build_input(spec, theme, parent)
        w.setEchoMode(QLineEdit.Password)
        return w

    def _build_int(self, spec, theme, parent) -> QWidget:
        w = self._build_input(spec, theme, parent)
        self.get = lambda: int(w.text() or 0)
        return w

    def _build_float(self, spec, theme, parent) -> QWidget:
        w = self._build_input(spec, theme, parent)
        self.get = lambda: float(w.text() or 0.0)
        return w

    def _build_folder(self, spec, theme, parent) -> QWidget:
        return self._picker(spec, theme, parent, mode="folder")

    def _build_file(self, spec, theme, parent) -> QWidget:
        return self._picker(spec, theme, parent, mode="file")

    def _build_save_file(self, spec, theme, parent) -> QWidget:
        return self._picker(spec, theme, parent, mode="save")

    def _picker(self, spec, theme, parent, mode: str) -> QWidget:
        """Single button, reference style: text swaps to the chosen name."""
        btn = QPushButton(self.label_text, parent)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(BTN_HEIGHT)
        self._path = str(spec.get("default", ""))
        if self._path:
            btn.setText(Path(self._path).name)

        def pick() -> None:
            if mode == "folder":
                p = QFileDialog.getExistingDirectory(
                    parent, "Select Folder", self._path or str(Path.home()))
            elif mode == "file":
                p, _ = QFileDialog.getOpenFileName(
                    parent, "Select File", self._path or str(Path.home()),
                    spec.get("filter", "All files (*.*)"))
            else:
                p, _ = QFileDialog.getSaveFileName(
                    parent, "Save As", self._path or str(Path.home()),
                    spec.get("filter", "All files (*.*)"))
            if p:
                self._path = p
                btn.setText(f"{self.label_text}  ·  {Path(p).name}")

        btn.clicked.connect(pick)
        self.get = lambda: self._path
        self._signal = None
        return btn

    # ---- validation / conditional visibility ------------------------------ #

    def validate(self) -> str | None:
        if self.required:
            v = self.get()
            if v is None or (isinstance(v, str) and not v.strip()):
                return (f"Please select {self.label_text.lower()} "
                        f"before starting the process.")
        return None

    def apply_visibility(self, values: dict[str, Any]) -> None:
        cond = self.spec.get("visible_when")
        if not cond:
            return
        self.widget.setVisible(values.get(cond.get("name"))
                               == cond.get("equals"))


# =========================================================================== #
# [7] MAIN SHELL — window layout + run flow + frameless drag
# =========================================================================== #

class FunctionShell(QWidget):
    """Config-driven app window wrapping a single function."""

    def __init__(self, fn: Callable,
                 config: dict | str | os.PathLike) -> None:
        super().__init__()
        self.fn = fn
        self.config = load_config(config)
        app_cfg = self.config.get("app", {})
        self.theme = Theme(
            accent=app_cfg.get("accent", ACCENT),
            accent_hover=app_cfg.get("accent_hover", ACCENT_HOVER))
        self.rows: list[_ParamRow] = []
        self.worker: _Worker | None = None
        self.oldPos = self.pos()
        self._dragging = False
        self._build_ui()
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ #
    # layout order (top -> bottom):
    #   close button | logo | params (config order) | progress | run button
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        app_cfg = self.config.get("app", {})
        run_cfg = self.config.get("run", {})

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("App")
        self.setStyleSheet(self.theme.root() + self.theme.select_pill())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_GAP)

        # -- title bar: close button only (size/font from [1] or config) -- #
        bar = QHBoxLayout()
        bar.addWidget(
            CloseButton(
                self, self.theme,
                size=int(app_cfg.get("close_size", CLOSE_BTN_SIZE)),
                font_size=int(app_cfg.get("close_font_size",
                                          CLOSE_FONT_SIZE))),
            alignment=Qt.AlignRight)
        layout.addLayout(bar)

        # -- logo / cover image -- #
        layout.addWidget(self._logo_label(app_cfg))

        # -- params: strictly in config order, no labels (except select,
        #    which gets a caption — see _build_select) -- #
        for spec in self.config.get("params", []):
            row = _ParamRow(spec, self.theme, self)
            self.rows.append(row)
            layout.addWidget(row.widget)

        # -- progress bar (hidden until run) -- #
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(self.theme.progress())
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # -- run button: black cooking style -- #
        run_btn = QPushButton(run_cfg.get("label", "Start Cooking"), self)
        run_btn.setStyleSheet(self.theme.cooking())
        run_btn.setCursor(Qt.PointingHandCursor)
        run_btn.clicked.connect(self._on_run)
        self.run_btn = run_btn
        layout.addWidget(run_btn)

        w, h = app_cfg.get("size", [540, 880])
        self.resize(w, h)
        self.setLayout(layout)

        # live conditional visibility
        self._refresh_visibility()
        for row in self.rows:
            if getattr(row, "_signal", None) is not None:
                row._signal.connect(self._refresh_visibility)

    def _logo_label(self, app_cfg: dict) -> QLabel:
        logo = QLabel(self)
        logo.setAlignment(Qt.AlignCenter)
        path = find_logo(app_cfg.get("logo"))
        if path:
            pixmap = QPixmap(path).scaled(
                LOGO_MAX_W, LOGO_MAX_H,
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo.setPixmap(pixmap)
            logo.setStyleSheet("border: none;")
        else:
            logo.setText(app_cfg.get("title", "App"))
            logo.setStyleSheet(f"""
                QLabel {{
                    background-color: {self.theme.accent};
                    color: {self.theme.ink};
                    font-size: {TITLE_FONT_SIZE}px;
                    font-weight: bold;
                    border-radius: 10px;
                    padding: 20px;
                }}""")
        return logo

    # ---- value collection / validation ------------------------------- #

    def values(self) -> dict[str, Any]:
        return {row.name: row.get() for row in self.rows
                if row.widget.isVisible() or not self.isVisible()}

    def _refresh_visibility(self, *_a) -> None:
        vals = {row.name: row.get() for row in self.rows}
        for row in self.rows:
            row.apply_visibility(vals)

    def _validate(self) -> bool:
        for row in self.rows:
            if not row.widget.isVisible() and self.isVisible():
                continue
            err = row.validate()
            if err:
                self._message(QMessageBox.Warning, "Input Error", err)
                return False
        return True

    # ---- run flow ------------------------------------------------------ #

    def _on_run(self) -> None:
        if self.worker is not None:
            return
        if not self._validate():
            return
        kwargs = self.values()
        logger.info("running %s with %s", self.fn.__name__, kwargs)

        self.run_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        self.worker = _Worker(self.fn, kwargs)
        self.worker.progressed.connect(self.progress_bar.setValue)
        self.worker.logged.connect(lambda s: logger.info("%s", s))
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_done(self, result: Any) -> None:
        self.progress_bar.hide()
        self.run_btn.setEnabled(True)
        self.worker = None
        text = "The workflow has been completed successfully."
        if isinstance(result, str) and result:
            text += f"\n\n{result}"
        self._message(QMessageBox.Information, "Success", text)

    def _on_fail(self, tb: str) -> None:
        logger.error("worker failed:\n%s", tb)
        self.progress_bar.hide()
        self.run_btn.setEnabled(True)
        self.worker = None
        self._message(QMessageBox.Warning, "Error",
                      tb.strip().splitlines()[-1])

    # ---- Bauhaus dialog: symbol centered on top, text below ----------- #

    def _message(self, icon, title: str, text: str) -> None:
        """Black field, accent border. Geometric mark (● ▲ ■) centered,
        body text below, OK button centered. No emoji, no system icons."""
        kind = {QMessageBox.Information: "info",
                QMessageBox.Warning: "warning",
                QMessageBox.Critical: "error"}.get(icon, "info")

        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        dlg.setStyleSheet(self.theme.message_box())
        dlg.setMinimumWidth(DIALOG_MIN_W)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(32, 32, 32, 28)
        lay.setSpacing(18)

        # -- geometric symbol, centered --
        symbol = QLabel(DIALOG_SYMBOLS.get(kind, "●"), dlg)
        symbol.setAlignment(Qt.AlignCenter)
        symbol.setStyleSheet(
            f"font-size: {DIALOG_SYMBOL_SIZE}px;"
            f"color: {self.theme.accent};")
        lay.addWidget(symbol)

        # -- body text below, centered --
        body = QLabel(text, dlg)
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        body.setStyleSheet(
            f"font-size: {DIALOG_FONT_SIZE}px;"
            f"color: {self.theme.accent};")
        lay.addWidget(body)

        # -- OK button, centered --
        ok = QPushButton("OK", dlg)
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(dlg.accept)
        lay.addWidget(ok, alignment=Qt.AlignCenter)

        dlg.exec_()

    # ---- frameless drag -------------------------------------------------- #
    # Only start dragging when the press lands on bare window background
    # (childAt(...) is None) — e.g. the border strip or the logo. If the
    # press lands on any child widget (combo box, button, checkbox, input),
    # we let that widget handle it and do NOT start a drag. This is what
    # fixed the "window jumps when I open the select dropdown" bug: before,
    # every press anywhere in the window updated self.oldPos and every move
    # (including the tiny mouse jitter of clicking a combo box open) shifted
    # the whole frameless window.

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.pos())
            if child is None:
                self._dragging = True
                self.oldPos = event.globalPos()
            else:
                self._dragging = False

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and event.buttons() == Qt.LeftButton:
            delta = QPoint(event.globalPos() - self.oldPos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.oldPos = event.globalPos()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def closeEvent(self, event) -> None:
        if self.worker:
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()


# =========================================================================== #
# [8] HELPERS — load_config / run_shell
# =========================================================================== #

def load_config(config: dict | str | os.PathLike) -> dict:
    """Accept a dict, a JSON string, or a path to a .json file."""
    if isinstance(config, dict):
        cfg = config
    else:
        text = str(config)
        if text.lstrip().startswith("{"):
            cfg = json.loads(text)
        else:
            cfg = json.loads(Path(text).read_text(encoding="utf-8"))
    if "params" not in cfg:
        raise ValueError("config must contain a 'params' list")
    seen: set[str] = set()
    for p in cfg["params"]:
        if "name" not in p or "type" not in p:
            raise ValueError(f"param missing name/type: {p}")
        if p["name"] in seen:
            raise ValueError(f"duplicate param name: {p['name']}")
        seen.add(p["name"])
    return cfg


def run_shell(fn: Callable, config: dict | str | os.PathLike) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    shell = FunctionShell(fn, config)
    shell.show()
    return app.exec_()


# =========================================================================== #
# [9] SELF-DEMO — python ui_style_shell.py
# =========================================================================== #

if __name__ == "__main__":
    import time

    def demo_task(folder: str, size, fmt: str,
                  progress_callback=None) -> str:
        for i in range(1, 11):
            time.sleep(0.1)
            if progress_callback:
                progress_callback(i * 10)
        return f"{folder or '-'} @ {size} -> {fmt}"

    DEMO_CONFIG = {
        "app": {"title": "TinyImgApp", "size": [540, 880]},
        "params": [
            {"name": "size", "type": "select", "label": "Cover Size",
             "options": [700, 600, 500, 400, 300, 200, 100],
             "default": 700},
            {"name": "fmt", "type": "select", "label": "Output Format",
             "options": ["PNG", "WEBP"], "default": "PNG"},
            {"name": "folder", "type": "folder",
             "label": "Select Image Folder", "required": True},
        ],
        "run": {"label": "Start Cooking"},
    }

    sys.exit(run_shell(demo_task, DEMO_CONFIG))