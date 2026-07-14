# ZI02 VOC sensor → Bee Incubation app

Streams Winsen ZI02 PID readings from the Raspberry Pi (`vapsens`) into the
incubation app's database, across different wifi networks, with local buffering
so nothing is lost during outages.

## How it works

```
 ZI02 sensor ──UART──▶ Pi (zi02_service.py)
                         │  1. average readings into 30s windows
                         │  2. write to local SQLite buffer  (survives reboots/outages)
                         │  3. sync buffer ──HTTP POST /reading──▶ App computer
                         │        (over Tailscale)                    │
                         │                                            ▼
                         └── mark rows synced on 200 OK        writes to incubation.db
```

The **app computer is the only thing that writes to `incubation.db`** (it's a
Google-Drive-synced SQLite file — two writers would corrupt it). The Pi never
touches the database directly; it only POSTs to the app's built-in web server.

Because the ingest endpoint lives in the desktop app, readings land in the DB
**while the app is open**. When it's closed, the Pi keeps buffering and syncs
the backlog (with correct original timestamps) as soon as the app is running.

## One-time setup

### 1. Tailscale on both machines
On the **app computer** and the **Pi**, install Tailscale and log into the same
account (https://tailscale.com/download). Then on the app computer:
```
tailscale ip -4        # note this address, e.g. 100.101.102.103
```
Confirm the Pi can reach it:
```
ping 100.101.102.103
curl http://100.101.102.103:5151/health      # -> {"status":"ok"}  (app must be open)
```

### 2. (Optional) set an ingest token
In the app, set **Settings → voc_ingest_token** to a random string if you want
to require auth on the ingest endpoint. Leave blank to accept readings from any
device on your Tailscale network.

### 3. Install the service on the Pi
```
sudo mkdir -p /opt/vapsens /etc/vapsens
sudo cp zi02_service.py /opt/vapsens/
sudo cp vapsens.conf.example /etc/vapsens/vapsens.conf
sudo cp vapsens.service /etc/systemd/system/

sudo nano /etc/vapsens/vapsens.conf     # set APP_URL (and INGEST_TOKEN if used)

pip3 install pyserial                    # if not already installed
sudo usermod -aG dialout incu1nvap       # serial access (once)

sudo systemctl daemon-reload
sudo systemctl enable --now vapsens
```

### 4. Verify
```
systemctl status vapsens
journalctl -u vapsens -f
```
You should see lines like:
```
stored 0.412 ppm (avg of 30 samples)
synced 2 reading(s); 0 pending
```
Then in the app go to **Settings ▸ Vapona Sensors**. The new sensor appears
automatically (identified by its hardware ID). Assign it to an incubator and
position and Save — readings then show on that incubator's **Vapona Monitor**
tab whenever the incubator is on.

## Config reference
See `vapsens.conf.example` — every option is documented there. Key ones:
- `APP_URL` — the app computer's Tailscale address + `:5151`
- `INGEST_TOKEN` — must match the app setting (or blank on both)
- `HARDWARE_ID` — auto-detected from the Pi; normally leave blank. The app maps
  this id → incubator/position (assign it under **Settings ▸ Vapona Sensors**).
- `INCUBATOR_ID` / `POSITION` — optional one-time seed only, used if the app has
  never seen this device before. Assignment is owned by the app thereafter.
- `SAMPLE_SECONDS` — averaging window (default 900s / 15 min)
- `WIFI_MANAGE` — `1` to let the app provision Wi-Fi networks (default), `0` to disable

## Wi-Fi provisioning
Each incubator has its own Wi-Fi network. Rather than reflashing a sensor when
you move it, the app holds the master list of networks (**Settings ▸ Vapona
Sensors ▸ Sensor Wi-Fi Networks**) and every sensor is provisioned with **all**
of them. NetworkManager then auto-connects to whichever network is in range, so
moving a sensor between incubators needs no reconfiguration.

- The service only **adds/updates** NetworkManager profiles (named `vapwifi-*`)
  — it never deletes the connection a Pi is currently using, so a bad password
  can't strand a sensor.
- Networks are fetched every `CONFIG_SECONDS` (default 5 min). Passwords are only
  sent when the request carries the correct `INGEST_TOKEN` (set one in the app's
  Settings so credentials aren't served openly).
- Requires passwordless `sudo nmcli` on the Pi (already configured on `vapsens`).

## Troubleshooting
| Symptom | Fix |
|---|---|
| `app unreachable` in the log | App not open, or Tailscale down. Data keeps buffering — it'll catch up. |
| `app rejected batch` | Token mismatch. Check the app's `voc_ingest_token` matches `INGEST_TOKEN`. |
| `serial open failed` | Check wiring / `enable_uart=1` / user in `dialout` group. |
| `skipped: device unassigned` | Assign the sensor to an incubator under **Settings ▸ Vapona Sensors**. |
| No readings in app | Confirm the incubator is **on** (readings are only stored when it is, like the temp sensors). |

## Notes
- The ZI02 has **no temperature output**, so `temp_c` is sent as null; the app
  gets incubator temperature from its Govee sensors separately.
- The local buffer (`/var/lib/vapsens/buffer.db`) auto-prunes synced rows older
  than `PRUNE_DAYS` (14 by default), so it stays small.
- `systemd` restarts the service on crash and starts it on boot.
