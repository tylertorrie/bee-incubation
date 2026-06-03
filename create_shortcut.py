"""
create_shortcut.py  —  One-time setup script for the Bee Incubation Manager.

Creates:
  1. bee.ico    — custom bee icon (drawn with Pillow)
  2. Desktop shortcut (.lnk) that always runs the live .py source

Run once:
    python create_shortcut.py

Re-run any time you move the folder to update the shortcut path.
The app itself auto-picks up code changes because it runs the .py directly.
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

def _draw_bee(size: int):
    """Draw a bee at the given pixel size; return RGBA PIL Image."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = float(size)

    def r(x0, y0, x1, y1):
        return [round(x0 * s), round(y0 * s), round(x1 * s), round(y1 * s)]

    def p(x, y):
        return (round(x * s), round(y * s))

    def lw(frac):
        return max(1, round(frac * s))

    # Wings (semi-transparent, drawn behind body)
    d.ellipse(r(0.03, 0.08, 0.44, 0.52), fill=(200, 220, 255, 155))
    d.ellipse(r(0.56, 0.08, 0.97, 0.52), fill=(200, 220, 255, 155))
    d.ellipse(r(0.08, 0.36, 0.44, 0.62), fill=(180, 210, 255, 120))
    d.ellipse(r(0.56, 0.36, 0.92, 0.62), fill=(180, 210, 255, 120))

    # Body (yellow oval)
    d.ellipse(r(0.23, 0.28, 0.77, 0.92),
              fill="#FFD700", outline="#9A6B00", width=lw(0.04))

    # Black stripes (3 bands)
    xl, xr = round(0.26 * s), round(0.74 * s)
    stripe_offsets = [0.40, 0.52, 0.64]
    stripe_h       = 0.08
    for yo in stripe_offsets:
        d.rectangle([xl + lw(0.03), round(yo * s),
                     xr - lw(0.03), round((yo + stripe_h) * s)],
                    fill="#1A1400")

    # Head
    d.ellipse(r(0.31, 0.11, 0.69, 0.36),
              fill="#2A1E00", outline="#1A1400", width=lw(0.03))

    # Eyes (white)
    er = lw(0.055)
    for ex, ey in [(0.41, 0.22), (0.59, 0.22)]:
        cx, cy = round(ex * s), round(ey * s)
        d.ellipse([cx - er, cy - er, cx + er, cy + er], fill="white")

    # Antennae
    aw = lw(0.045)
    d.line([p(0.42, 0.14), p(0.27, 0.02)], fill="#2A1E00", width=aw)
    d.line([p(0.58, 0.14), p(0.73, 0.02)], fill="#2A1E00", width=aw)
    dr = lw(0.07)
    for ax, ay in [(0.26, 0.02), (0.74, 0.02)]:
        cx, cy = round(ax * s), round(ay * s)
        d.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill="#2A1E00")

    # Stinger
    d.polygon([p(0.50, 0.97), p(0.42, 0.88), p(0.58, 0.88)], fill="#9A6B00")

    return img


def make_icon() -> str | None:
    """Generate bee.ico at multiple sizes. Returns path string or None on failure."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("  [icon] Pillow not installed — shortcut will use the Python icon.")
        return None

    # Draw at 256×256 (highest quality); Pillow downsamples to smaller sizes
    base   = _draw_bee(256)
    sizes  = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(
        str(ICON_PATH),
        format="ICO",
        sizes=sizes,
    )
    print(f"  [icon] Saved: {ICON_PATH}")
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
