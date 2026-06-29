"""
create_shortcut.py  —  One-time setup script for the Bee Incubation Manager.

Creates:
  1. bee.ico    — app icon from logo.png (falls back to drawn bee if missing)
  2. Desktop shortcut (.lnk) that launches the app

Run once:
    python create_shortcut.py

Re-run any time you move the folder or replace logo.png to update icons.
"""
import os
import sys
import subprocess
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
APP_DIR    = Path(__file__).parent.resolve()
APP_SCRIPT = APP_DIR / "incubation_app.py"
ICON_PATH  = APP_DIR / "bee.ico"
def _get_desktop() -> Path:
    """Use Windows Shell to find the real Desktop folder (works with OneDrive)."""
    import subprocess
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         '[Environment]::GetFolderPath("Desktop")'],
        capture_output=True, text=True,
    )
    p = result.stdout.strip()
    if p and Path(p).exists():
        return Path(p)
    # Fallback: try OneDrive Desktop, then plain Desktop
    for candidate in [
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Desktop",
    ]:
        if candidate.exists():
            return candidate
    return Path.home() / "Desktop"   # last resort (may not exist)

DESKTOP    = _get_desktop()
SHORTCUT   = DESKTOP / "Bee Incubation.lnk"

# Use pythonw.exe (GUI-only, no console window)
_py_dir = Path(sys.executable).parent
PYTHONW = _py_dir / "pythonw.exe"
if not PYTHONW.exists():
    PYTHONW = Path(sys.executable)   # fallback: python.exe


# ── Icon generation ───────────────────────────────────────────────────────────

LOGO_PNG = APP_DIR / "logo.png"   # source image — place your logo here


def _make_transparent(img):
    """Convert white / near-white pixels to transparent (for clean icon on dark taskbar)."""
    img = img.convert("RGBA")
    data = img.getdata()
    new_data = []
    for r, g, b, a in data:
        if r > 230 and g > 230 and b > 230:   # white or near-white background
            new_data.append((r, g, b, 0))
        else:
            new_data.append((r, g, b, a))
    img.putdata(new_data)
    return img


def _draw_bee_fallback(size: int):
    """Simple fallback bee drawn with Pillow — only used if logo.png is missing."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = float(size)
    r   = lambda x0, y0, x1, y1: [round(x0*s), round(y0*s), round(x1*s), round(y1*s)]
    lw  = lambda f: max(1, round(f * s))
    d.ellipse(r(0.03,0.08,0.44,0.52), fill=(200,220,255,155))
    d.ellipse(r(0.56,0.08,0.97,0.52), fill=(200,220,255,155))
    d.ellipse(r(0.23,0.28,0.77,0.92), fill="#FFD700", outline="#9A6B00", width=lw(0.04))
    for yo in [0.40, 0.52, 0.64]:
        d.rectangle([round(0.29*s), round(yo*s), round(0.71*s), round((yo+0.08)*s)], fill="#1A1400")
    d.ellipse(r(0.31,0.11,0.69,0.36), fill="#2A1E00")
    return img


def make_icon() -> str | None:
    """
    Build bee.ico from logo.png (transparent background, multiple sizes).
    Falls back to the drawn bee if logo.png is not present.
    Returns the .ico path string, or None on failure.
    """
    try:
        from PIL import Image
    except ImportError:
        print("  [icon] Pillow not installed — skipping icon.")
        return None

    app_icon = APP_DIR / "app_icon.png"
    if app_icon.exists():
        print(f"  [icon] Using {app_icon.name}")
        base = Image.open(app_icon).convert("RGBA").resize((256, 256), Image.LANCZOS)
    elif LOGO_PNG.exists():
        print(f"  [icon] Using {LOGO_PNG.name}")
        base = _make_transparent(Image.open(LOGO_PNG))
        base = base.resize((256, 256), Image.LANCZOS)
    else:
        print(f"  [icon] logo.png not found — using drawn bee fallback.")
        print(f"         (Copy your logo PNG to {LOGO_PNG} and re-run to update.)")
        base = _draw_bee_fallback(256)

    sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
    base.save(str(ICON_PATH), format="ICO", sizes=sizes)
    print(f"  [icon] Saved {ICON_PATH.name}")
    return str(ICON_PATH)


# ── Shortcut creation ─────────────────────────────────────────────────────────

def make_shortcut(icon_path: str | None):
    """Create (or overwrite) a .lnk shortcut on the Desktop via a temp .ps1 file."""

    icon_loc = str(ICON_PATH) if (icon_path and ICON_PATH.exists()) else str(PYTHONW)

    # Write a self-contained .ps1 — avoids all shell-escaping issues
    ps1_path = Path(os.environ.get("TEMP", str(Path.home()))) / "_bee_shortcut.ps1"
    ps1_content = (
        f"$ws  = New-Object -ComObject WScript.Shell\n"
        f"$sc  = $ws.CreateShortcut('{SHORTCUT}')\n"
        f"$sc.TargetPath       = '{PYTHONW}'\n"
        f"$sc.Arguments        = '\"{APP_SCRIPT}\"'\n"
        f"$sc.WorkingDirectory = '{APP_DIR}'\n"
        f"$sc.Description      = 'Bee Incubation Manager'\n"
        f"$sc.IconLocation     = '{icon_loc},0'\n"
        f"$sc.Save()\n"
        f"Write-Host 'OK'\n"
    )
    ps1_path.write_text(ps1_content, encoding="utf-8")

    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
        capture_output=True, text=True,
    )
    ps1_path.unlink(missing_ok=True)

    if result.returncode == 0 and "OK" in result.stdout:
        print(f"  [shortcut] Created: {SHORTCUT}")
    else:
        print(f"  [shortcut] PowerShell error:\n{result.stderr}")
        print("  Trying VBS fallback...")
        _make_shortcut_vbs(icon_path)


def _make_shortcut_vbs(icon_path: str | None):
    """Fallback: write a .vbs script and shell-execute it."""
    icon_loc = str(ICON_PATH) if (icon_path and ICON_PATH.exists()) else str(PYTHONW)
    vbs_path = APP_DIR / "_tmp_shortcut.vbs"
    vbs_content = f'''Set ws = CreateObject("WScript.Shell")
Set sc = ws.CreateShortcut("{SHORTCUT}")
sc.TargetPath = "{PYTHONW}"
sc.Arguments = """{APP_SCRIPT}"""
sc.WorkingDirectory = "{APP_DIR}"
sc.Description = "Bee Incubation Manager"
sc.IconLocation = "{icon_loc}"
sc.Save
'''
    vbs_path.write_text(vbs_content)
    result = subprocess.run(["cscript", "//Nologo", str(vbs_path)],
                            capture_output=True, text=True)
    vbs_path.unlink(missing_ok=True)
    if result.returncode == 0:
        print(f"  [shortcut] Created (vbs fallback): {SHORTCUT}")
    else:
        print(f"  [shortcut] Failed: {result.stderr}")
        print(f"\n  Manual fallback: right-click Desktop → New Shortcut")
        print(f"  Target:  {PYTHONW}")
        print(f"  Args:    \"{APP_SCRIPT}\"")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Bee Incubation Manager — desktop setup")
    print(f"  App:     {APP_SCRIPT}")
    print(f"  Python:  {PYTHONW}")
    print()

    icon = make_icon()
    make_shortcut(icon)

    print()
    print("Done!  'Bee Incubation' shortcut is on your Desktop.")
    print("The shortcut runs the .py file directly, so code changes are")
    print("picked up automatically on the next launch — no recompiling needed.")
    input("\nPress Enter to close...")
