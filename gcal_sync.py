"""
gcal_sync.py — push incubation milestones to Google Calendar (OAuth).

One-way sync: the app creates/updates/deletes calendar events to mirror the
incubation schedule. Each milestone gets a deterministic event ID derived from
the incubator id + milestone day, so re-syncing updates the same event instead
of creating duplicates, and removing a schedule deletes its events.

Requires (pip install):
    google-api-python-client google-auth-oauthlib google-auth-httplib2

Auth: the user creates an OAuth "Desktop app" credential in Google Cloud,
downloads the JSON, and authorizes once in a browser. The refreshable token is
cached locally (never committed).
"""
from datetime import timedelta

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def available() -> bool:
    """True if the Google client libraries are importable."""
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        return True
    except Exception:
        return False


def make_event_id(incubator_id: int, day: int) -> str:
    """Deterministic Google event id (base32hex: chars a-v + 0-9 only)."""
    return f"beeinc{incubator_id}m{day}"


class GoogleCalendar:
    def __init__(self, creds_file: str, token_file: str, calendar_id: str = "primary"):
        self.creds_file  = creds_file
        self.token_file  = token_file
        self.calendar_id = calendar_id or "primary"
        self._creds   = None
        self._service = None
        self.error    = ""

    # ── Auth ────────────────────────────────────────────────────────────────

    def connect(self, interactive: bool = True) -> bool:
        """Load cached token (refreshing if needed). If none and interactive,
        run the browser consent flow. Returns True on success."""
        import os
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = None
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception:
                creds = None

        if creds and creds.valid:
            self._creds = creds
            return True
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                self._creds = creds
                return True
            except Exception as exc:
                self.error = f"Token refresh failed: {exc}"

        if not interactive:
            if not self.error:
                self.error = "Not authorized yet — click Connect."
            return False

        if not self.creds_file or not os.path.exists(self.creds_file):
            self.error = "Credentials JSON not found. Set its path in Settings."
            return False
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(self.creds_file, SCOPES)
            creds = flow.run_local_server(port=0)
            self._save_token(creds)
            self._creds = creds
            return True
        except Exception as exc:
            self.error = f"Authorization failed: {exc}"
            return False

    def _save_token(self, creds):
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except OSError:
            pass

    def _svc(self):
        if self._service is None:
            from googleapiclient.discovery import build
            self._service = build("calendar", "v3", credentials=self._creds,
                                  cache_discovery=False)
        return self._service

    # ── Event operations ────────────────────────────────────────────────────

    def upsert(self, eid: str, summary: str, day_date) -> bool:
        """Create or update an all-day event with a fixed id."""
        from googleapiclient.errors import HttpError
        body = {
            "summary": summary,
            "start": {"date": day_date.isoformat()},
            "end":   {"date": (day_date + timedelta(days=1)).isoformat()},
        }
        try:
            self._svc().events().update(
                calendarId=self.calendar_id, eventId=eid, body=body).execute()
            return True
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                try:
                    ins = dict(body, id=eid)
                    self._svc().events().insert(
                        calendarId=self.calendar_id, body=ins).execute()
                    return True
                except HttpError as e2:
                    self.error = str(e2)
                    return False
            self.error = str(e)
            return False

    def delete(self, eid: str) -> bool:
        """Delete an event; treat already-gone as success."""
        from googleapiclient.errors import HttpError
        try:
            self._svc().events().delete(
                calendarId=self.calendar_id, eventId=eid).execute()
            return True
        except HttpError as e:
            if any(c in str(e) for c in ("404", "410")):
                return True
            self.error = str(e)
            return False
