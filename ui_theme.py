"""
ui_theme.py  —  shared theme constants and widget helpers for the desktop GUI.

Extracted from incubation_app.py so the app shell and each view module share a
single source of truth for colours, fonts and the small widget factories.
"""
from datetime import datetime
from tkinter import ttk

import customtkinter as ctk


# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GOLD      = "#FFD700"
DK_GOLD   = "#B8860B"
GREEN     = "#4CAF50"
GREEN_LT  = "#7CE08A"
TEAL      = "#10B981"
ORANGE    = "#FF9800"
RED       = "#FF3B30"
RED_LT    = "#FF6A57"
BLUE      = "#3B82F6"
LINK      = "#93C5FD"
BG        = "#0F172A"   # app background
BARBG     = "#0B1220"   # title / status bar
SIDEBAR   = "#111827"
RIGHTPANE = "#0D1524"
CARD      = "#1B2536"   # incubator cards
PANEL     = "#151E2E"   # section / table panels
NESTED    = "#141C2B"   # nested rows
CARD2     = "#202B3D"   # inset tiles / inputs
BORDER    = "#374151"   # strong border
BORDER2   = "#232F42"   # subtle border
SUBBORDER = "#1E293B"   # faint divider
TEXT      = "#F3F4F6"
TEXT2     = "#CBD5E1"
SUBTEXT   = "#9CA3AF"
FAINT     = "#6B7280"
FONT_H    = ("Segoe UI", 14, "bold")
FONT_B    = ("Segoe UI", 11)
FONT_S    = ("Segoe UI", 10)


def _treeview_style():
    """Table style per the design handoff: panel background #151E2E, gold
    bold headers with a subtle divider, 11.5px body text, roomy rows."""
    style = ttk.Style()
    style.theme_use("default")
    style.configure("Dark.Treeview",
        background=PANEL, foreground="#E5E7EB",
        fieldbackground=PANEL, borderwidth=0,
        rowheight=30, font=("Segoe UI", 11))
    style.configure("Dark.Treeview.Heading",
        background=PANEL, foreground=GOLD,
        relief="flat", borderwidth=0, padding=(10, 8),
        font=("Segoe UI", 11, "bold"))
    style.map("Dark.Treeview.Heading",
        background=[("active", PANEL)])
    style.map("Dark.Treeview",
        background=[("selected", "#26374F")],
        foreground=[("selected", TEXT)])


def _label(parent, text, font=FONT_B, color=TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _btn(parent, text, cmd, width=110, height=32, fg=CARD2, hover=BORDER,
         text_color=TEXT, **kw):
    return ctk.CTkButton(parent, text=text, command=cmd, width=width,
                         height=height, fg_color=fg, hover_color=hover,
                         text_color=text_color, corner_radius=6, **kw)


def _btn_primary(parent, text, cmd, width=130):
    """Gold primary action button (spec: #E0A81A→#B8860B, text #1A1206)."""
    return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=34,
                         corner_radius=8, fg_color="#C79114", hover_color="#E0A81A",
                         text_color="#1A1206", font=("Segoe UI", 12, "bold"),
                         border_width=1, border_color=DK_GOLD)


def _btn_secondary(parent, text, cmd, width=120):
    """Neutral secondary action button (spec: #1F2937 bg, #374151 border)."""
    return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=34,
                         corner_radius=8, fg_color="#1F2937", hover_color="#28374D",
                         text_color="#E5E7EB", font=("Segoe UI", 12),
                         border_width=1, border_color=BORDER)

def _poll_age(timestamp_iso: str | None, interval_sec: int = 300) -> tuple[str, str]:
    """
    Return (display_text, color) for how long ago a Govee poll timestamp was.

    Freshness colors scale with the configured poll interval so they stay
    meaningful at any rate (1 min → 1 hr):
      green  = within ~1.5 cycles (healthy)
      orange = within ~3 cycles   (a poll or two missed)
      red    = beyond that, or missing
    """
    if not timestamp_iso:
        return "Never polled", SUBTEXT
    try:
        then    = datetime.fromisoformat(timestamp_iso)
        minutes = (datetime.now() - then).total_seconds() / 60
    except Exception:
        return "Unknown", SUBTEXT
    if minutes < 1:
        text = "Just now"
    elif minutes < 60:
        text = f"{int(minutes)} min ago"
    elif minutes < 120:
        text = "1 hr ago"
    else:
        text = f"{int(minutes // 60)} hrs ago"

    cycle_min  = max(interval_sec / 60, 1)
    green_cut  = cycle_min * 1.5 + 1     # one cycle + slack
    orange_cut = cycle_min * 3   + 2
    color = GREEN if minutes <= green_cut else (ORANGE if minutes <= orange_cut else RED)
    return text, color


def _entry(parent, placeholder="", width=200):
    return ctk.CTkEntry(parent, placeholder_text=placeholder, width=width,
                        fg_color=CARD2, border_color=BORDER, text_color="#CBD5E1",
                        corner_radius=7)


def _mix(fg: str, bg: str, alpha: float) -> str:
    """Blend fg over bg at the given alpha (0-1) → solid hex.
    Used to fake the translucent badge fills from the design spec."""
    fg = fg.lstrip("#"); bg = bg.lstrip("#")
    fr, fgc, fb = int(fg[0:2], 16), int(fg[2:4], 16), int(fg[4:6], 16)
    br, bgc, bb = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
    r = round(fr * alpha + br * (1 - alpha))
    g = round(fgc * alpha + bgc * (1 - alpha))
    b = round(fb * alpha + bb * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


# Operating-mode accent colors (badges + segmented controls)
MODE_COLORS = {"off": "#6B7280", "cool_storage": TEAL,
               "incubation": BLUE, "holding": GOLD}
# Spec-exact pre-blended badge fills (mode color at ~13% over card #1B2536)
MODE_BADGE_BG = {"off": "#252B34", "cool_storage": "#1B2E33",
                 "incubation": "#20293B", "holding": "#31311F"}


def _combo(parent, values, width=200):
    return ctk.CTkComboBox(parent, values=values, width=width,
                           fg_color=CARD, border_color=BORDER,
                           button_color=BORDER, text_color=TEXT,
                           dropdown_fg_color=CARD)


# ── Reusable form helper ──────────────────────────────────────────────────────

class _FormRow:
    """One label + entry widget row in a grid form."""
    def __init__(self, parent, row, label, placeholder="", width=220, widget=None):
        _label(parent, label, font=FONT_S, color=SUBTEXT).grid(
            row=row, column=0, sticky="w", padx=(4, 8), pady=3)
        if widget:
            self.widget = widget
        else:
            self.widget = _entry(parent, placeholder, width=width)
        self.widget.grid(row=row, column=1, sticky="ew", padx=4, pady=3)

    def get(self):
        w = self.widget
        if isinstance(w, ctk.CTkEntry):
            return w.get().strip()
        if isinstance(w, ctk.CTkComboBox):
            return w.get()
        if isinstance(w, ctk.CTkTextbox):
            return w.get("1.0", "end").strip()
        return ""

    def set(self, value):
        w = self.widget
        val = str(value) if value is not None else ""
        if isinstance(w, ctk.CTkEntry):
            w.delete(0, "end"); w.insert(0, val)
        elif isinstance(w, ctk.CTkComboBox):
            w.set(val)
        elif isinstance(w, ctk.CTkTextbox):
            w.delete("1.0", "end"); w.insert("1.0", val)
