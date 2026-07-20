"""GUI smoke test — builds the app, visits every view, and opens every dialog.

Not collected by pytest (filename isn't test_*), because it opens real windows.
Run it manually, especially after moving GUI code around:

    python tests/smoke_gui.py

Background services (git sync, QR server, Govee polling, email, alert checker,
backups) are stubbed out, so this has no side effects — no network, no git, no
writes beyond what building a view naturally reads.

Exit 0 = everything built and rendered. Exit 1 = at least one failure, with
tracebacks printed.
"""
import os
import sys
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

import incubation_app as ia

# Neutralise anything that touches the network, git, or schedules timers.
_STUB = ["_start_govee", "_start_qr_server", "_start_alert_checker",
         "_start_email_scheduler", "_git_pull", "_start_auto_sync",
         "_start_code_watcher", "_tick", "_run_db_backup", "_check_db_conflicts"]
for _name in _STUB:
    if hasattr(ia.IncubationApp, _name):
        setattr(ia.IncubationApp, _name, lambda self, *a, **k: None)

errors = []
app = None

try:
    app = ia.IncubationApp()
    app.update()
    print("views found:", ", ".join(app._views.keys()))

    for v in list(app._views.keys()):
        if v == "inc_detail":
            continue                        # needs an incubator; covered below
        try:
            app.show_view(v)
            app.update()
            print(f"  OK    {v}")
        except Exception:
            errors.append((v, traceback.format_exc()))
            print(f"  FAIL  {v}")

    import incubation_db as db
    incs = db.get_incubators(include_hidden=True)

    # Incubator detail view
    try:
        opener = (getattr(app, "_show_inc_detail", None)
                  or getattr(app, "_open_inc_detail_window", None))
        if incs and opener:
            opener(incs[0])
            app.update()
            print(f"  OK    inc_detail ({incs[0]['name']})")
        else:
            print("  SKIP  inc_detail")
    except Exception:
        errors.append(("inc_detail", traceback.format_exc()))
        print("  FAIL  inc_detail")

    # Dialogs
    try:
        from views.dialogs import (IncubatorDialog, BatchDialog, SampleDialog,
                                   TrayDialog, QRDialog, AlertsDialog,
                                   _VocDeviceManager, _WifiNetworkManager)
        inc = incs[0] if incs else None
        trays = db.get_trays()[:1] if hasattr(db, "get_trays") else []
        tray = trays[0] if trays else None

        cases = [
            ("IncubatorDialog",     lambda: IncubatorDialog(app, inc)),
            ("BatchDialog",         lambda: BatchDialog(app)),
            ("SampleDialog",        lambda: SampleDialog(app)),
            ("TrayDialog",          lambda: TrayDialog(app)),
            ("AlertsDialog",        lambda: AlertsDialog(app)),
            ("_VocDeviceManager",   lambda: _VocDeviceManager(app)),
            ("_WifiNetworkManager", lambda: _WifiNetworkManager(app)),
        ]
        if tray:
            cases.append(("QRDialog", lambda: QRDialog(app, tray)))

        for name, ctor in cases:
            try:
                d = ctor()
                app.update()
                d.destroy()
                print(f"  OK    dialog {name}")
            except Exception:
                errors.append((f"dialog {name}", traceback.format_exc()))
                print(f"  FAIL  dialog {name}")
    except Exception:
        errors.append(("dialogs-import", traceback.format_exc()))
        print("  FAIL  dialogs import")

except Exception:
    errors.append(("startup", traceback.format_exc()))
    print("  FAIL  startup")

# Deliberately no app.destroy(): pending debounced after() jobs fire against
# destroyed widgets and add noise. os._exit tears the process down instead.
if errors:
    print("\n===== FAILURES =====")
    for name, tb in errors:
        print(f"\n--- {name} ---\n{tb}")
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(1)

print("\nSMOKE OK - every view and dialog built and rendered")
sys.stdout.flush(); sys.stderr.flush()
os._exit(0)
