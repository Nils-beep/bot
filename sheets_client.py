# sheets_client.py
import os, json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import calendar

SPREADSHEET_ID = "1lCXsPkRyTQff15z7RD7bRV_l4R0ciU1U5oalMD9XOdc"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]

def _build_creds():
    """
    Prefer GOOGLE_SA_JSON (the JSON content) if present.
    Otherwise use GOOGLE_APPLICATION_CREDENTIALS (a file path).
    """
    if os.getenv("GOOGLE_SA_JSON"):
        return Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SA_JSON"]), scopes=SCOPES
        )
    elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=SCOPES
        )
    else:
        raise RuntimeError(
            "No Google credentials found. Set GOOGLE_SA_JSON or GOOGLE_APPLICATION_CREDENTIALS."
        )

_creds  = _build_creds()
_svc    = build("sheets", "v4", credentials=_creds)
_values = _svc.spreadsheets().values()

TAB = "'Schedule(ignore)'"     # visible schedule tab
START_ROW = 6
MONTH_COLS = [("A","B","C"), ("E","F","G"), ("I","J","K")]  # [weekday, date, Raid?]

CANT_TAB = "Cant"  # a simple log: who can't raid on which date
CANT_RANGE = f"'{CANT_TAB}'!A1:D1"


def initialize_sheets():
    """Ensure Cant tab exists with headers."""
    meta = _svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if CANT_TAB not in titles:
        _svc.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests":[{"addSheet":{"properties":{"title": CANT_TAB}}}]}
        ).execute()
        _values.update(
            spreadsheetId=SPREADSHEET_ID,
            range=CANT_RANGE,
            valueInputOption="USER_ENTERED",
            body={"values":[["Timestamp","UserID","UserTag","Date (dd.mm.yyyy)"]]}
        ).execute()

def record_cant(user_id: int, user_tag: str, date_str: str):
    """Append a row to Cant tab."""
    initialize_sheets()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{CANT_TAB}'!A:D",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values":[[ts, str(user_id), user_tag, date_str]]}
    ).execute()

def _read_month_block(cols: tuple[str,str,str]):
    """Read current values from a month block; return list of rows and mapping date->(row_index, current_flag)."""
    c1, c2, c3 = cols
    rng = f"{TAB}!{c1}{START_ROW}:{c3}{START_ROW+30}"
    resp = _values.get(spreadsheetId=SPREADSHEET_ID, range=rng).execute()
    rows = resp.get("values", []) or []
    index_by_date = {}
    for i, r in enumerate(rows):
        if len(r) >= 2 and r[1]:
            date_s = r[1].strip()
            cur = (r[2].strip() if len(r) >=3 and r[2] else "")
            index_by_date[date_s] = (i, cur)
    return rows, index_by_date

def set_raid_date_in_visible_table(date_str: str, can_raid: bool) -> bool:
    """Set Raid? for a date to ✔/✖ in any of the three month blocks."""
    value = "✔" if can_raid else "✖"
    for (c1, c2, c3) in MONTH_COLS:
        rows, idx = _read_month_block((c1,c2,c3))
        hit = idx.get(date_str)
        if hit:
            row_i, _ = hit
            target_row = START_ROW + row_i
            _values.update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{TAB}!{c3}{target_row}",
                valueInputOption="USER_ENTERED",
                body={"values":[[value]]}
            ).execute()
            return True
    return False

def toggle_raid_date_in_visible_table(date_str: str) -> str | None:
    """Flip ✔/✖ for date; returns new value or None if date not found."""
    for (c1, c2, c3) in MONTH_COLS:
        rows, idx = _read_month_block((c1,c2,c3))
        hit = idx.get(date_str)
        if hit:
            row_i, cur = hit
            new_val = "✖" if cur == "✔" else "✔"
            target_row = START_ROW + row_i
            _values.update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{TAB}!{c3}{target_row}",
                valueInputOption="USER_ENTERED",
                body={"values":[[new_val]]}
            ).execute()
            return new_val
    return None

def _write_month_default(year: int, month: int, start_day: int, cols: tuple[str,str,str]):
    """Overwrite one month block with defaults: Mon/Wed/Thu = ✔, else ✖."""
    c1, c2, c3 = cols
    mlen = calendar.monthrange(year, month)[1]

    # Header: "Month YYYY"
    hdr = datetime(year, month, 1).strftime("%B %Y")
    _values.update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{TAB}!{c1}4",
        valueInputOption="USER_ENTERED",
        body={"values": [[hdr]]}
    ).execute()

    # Rows: [Weekday, dd.mm.yyyy, Raid?]
    rows = []
    for d in range(start_day, mlen + 1):
        dt = datetime(year, month, d)
        wd = dt.weekday()  # Mon=0 ... Sun=6
        raid = "✔" if wd in (0, 2, 3) else "✖"
        rows.append([dt.strftime("%A"), dt.strftime("%d.%m.%Y"), raid])

    if rows:
        _values.update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TAB}!{c1}{START_ROW}:{c3}{START_ROW + len(rows) - 1}",
            valueInputOption="USER_ENTERED",
            body={"values": rows}
        ).execute()

    # Clear leftover lines up to 31 rows
    left = 31 - len(rows)
    if left > 0:
        empties = [["", "", ""]] * left
        _values.update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TAB}!{c1}{START_ROW + len(rows)}:{c3}{START_ROW + 30}",
            valueInputOption="USER_ENTERED",
            body={"values": empties}
        ).execute()

def rebuild_schedule(start_current_from_today: bool = True):
    """
    Rebuild the 3-month schedule:
      - current month: from today (or from day 1 if start_current_from_today=False)
      - next 2 months: from day 1
    Overwrites “Raid?” with defaults (Mon/Wed/Thu = ✔).
    """
    today = datetime.today()
    for idx, cols in enumerate(MONTH_COLS):
        m0 = today.month - 1 + idx
        y  = today.year + (m0 // 12)
        m  = (m0 % 12) + 1
        start_day = (today.day if (idx == 0 and start_current_from_today) else 1)
        _write_month_default(y, m, start_day, cols)

# ===== Daily refresh that preserves overrides ACROSS blocks =====

_WD_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

def _ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")

def _collect_overrides_all_blocks() -> dict[str, str]:
    """
    Read all three visible blocks and collect any explicit ✔/✖ by date,
    regardless of which block the date is currently in.
    """
    overrides: dict[str, str] = {}
    for (c1, c2, c3) in MONTH_COLS:
        rng = f"{TAB}!{c1}{START_ROW}:{c3}{START_ROW+30}"
        resp = _values.get(spreadsheetId=SPREADSHEET_ID, range=rng).execute()
        rows = resp.get("values", []) or []
        for r in rows:
            if len(r) >= 3 and r[1]:
                date_s = r[1].strip()
                flag = r[2].strip()
                if flag in ("✔", "✖"):
                    overrides[date_s] = flag
    return overrides

def _desired_window(today: datetime) -> list[tuple[str,str,str]]:
    """
    Build desired 3-month window starting today.
    Returns rows: (Weekday, dd.mm.yyyy, default_flag)
    Defaults: Mon/Wed/Thu = ✔, otherwise ✖.
    """
    rows: list[tuple[str,str,str]] = []
    for idx in range(3):
        m0 = today.month - 1 + idx
        y  = today.year + (m0 // 12)
        m  = (m0 % 12) + 1
        start_day = today.day if idx == 0 else 1
        mlen = calendar.monthrange(y, m)[1]
        for d in range(start_day, mlen + 1):
            dt = datetime(y, m, d)
            wd = dt.weekday()
            default = "✔" if wd in (0, 2, 3) else "✖"
            rows.append((_WD_NAMES[wd], _ddmmyyyy(dt), default))
    return rows

def refresh_schedule_preserve_overrides():
    """
    Refresh the 3 blocks daily:
      - Recompute the 3-month window starting today.
      - Update headers.
      - For each date, use a global override (✔/✖) if it exists anywhere
        in the current sheet; otherwise use the default.
      - Clear leftover rows.
    """
    today = datetime.today()
    overrides = _collect_overrides_all_blocks()   # <-- key change: global overrides
    desired = _desired_window(today)

    # compute which (year,month) each block represents now
    month_tags: list[tuple[int,int]] = []
    for idx in range(3):
        m0 = today.month - 1 + idx
        y  = today.year + (m0 // 12)
        m  = (m0 % 12) + 1
        month_tags.append((y, m))

    # split desired rows per visual block
    per_block: list[list[tuple[str,str,str]]] = [[], [], []]
    for wd, date_s, default_flag in desired:
        dt = datetime.strptime(date_s, "%d.%m.%Y")
        ym = (dt.year, dt.month)
        blk = month_tags.index(ym) if ym in month_tags else 2
        per_block[blk].append((wd, date_s, default_flag))

    # write each block
    for blk_idx, (c1, c2, c3) in enumerate(MONTH_COLS):
        y, m = month_tags[blk_idx]

        # header
        hdr = datetime(y, m, 1).strftime("%B %Y")
        _values.update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TAB}!{c1}4",
            valueInputOption="USER_ENTERED",
            body={"values": [[hdr]]}
        ).execute()

        # build rows using override map
        desired_rows = per_block[blk_idx]
        new_block: list[list[str]] = []
        for wd, date_s, default_flag in desired_rows:
            use_flag = overrides.get(date_s, default_flag)
            new_block.append([wd, date_s, use_flag])

        # write desired rows
        if new_block:
            _values.update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{TAB}!{c1}{START_ROW}:{c3}{START_ROW+len(new_block)-1}",
                valueInputOption="USER_ENTERED",
                body={"values": new_block}
            ).execute()

        # clear leftovers
        leftover = 31 - len(new_block)
        if leftover > 0:
            empties = [["","",""]]*leftover
            _values.update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{TAB}!{c1}{START_ROW+len(new_block)}:{c3}{START_ROW+30}",
                valueInputOption="USER_ENTERED",
                body={"values": empties}
            ).execute()

# The name column is the column *after* the "Raid?" column in each block:
# (A,B,C) -> D, (E,F,G) -> H, (I,J,K) -> L
def _next_col(col_letter: str) -> str:
    return chr(ord(col_letter) + 1)

def _read_cell(range_a1: str) -> str:
    resp = _values.get(spreadsheetId=SPREADSHEET_ID, range=range_a1).execute()
    vals = resp.get("values", [])
    return (vals[0][0].strip() if vals and vals[0] else "")

def _write_cell(range_a1: str, value: str):
    _values.update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_a1,
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]}
    ).execute()

def add_cant_user(date_str: str, user_name: str) -> tuple[bool, str]:
    """
    Add user_name to the “names” cell next to Raid? for the given date
    and set Raid? to ✖. Returns (True, joined_names) if date found, else (False, "").
    """
    for (c1, c2, c3) in MONTH_COLS:
        rows, idx = _read_month_block((c1, c2, c3))
        hit = idx.get(date_str)
        if not hit:
            continue
        row_i, _cur = hit
        target_row = START_ROW + row_i

        raid_col = c3
        names_col = _next_col(c3)   # D / H / L

        names_rng = f"{TAB}!{names_col}{target_row}"
        current = _read_cell(names_rng)
        # maintain a comma-separated, trimmed, case-insensitive set
        existing = [p.strip() for p in current.split(",") if p.strip()] if current else []
        # avoid duplicates (case-insensitive)
        if user_name.lower() not in [x.lower() for x in existing]:
            existing.append(user_name)

        joined = ", ".join(existing)

        # write names and force ✖
        _write_cell(names_rng, joined)
        _write_cell(f"{TAB}!{raid_col}{target_row}", "✖")

        return True, joined
    return False, ""

def remove_cant_user(date_str: str, user_name: str) -> tuple[bool, str, str]:
    """
    Remove user_name from the names list for the date.
    If the list becomes empty -> set Raid? to ✔, else keep ✖.
    Returns (found, new_flag, joined_names). If not found: (False, "", "").
    """
    for (c1, c2, c3) in MONTH_COLS:
        rows, idx = _read_month_block((c1, c2, c3))
        hit = idx.get(date_str)
        if not hit:
            continue
        row_i, cur_flag = hit
        target_row = START_ROW + row_i

        raid_col = c3
        names_col = _next_col(c3)

        names_rng = f"{TAB}!{names_col}{target_row}"
        current = _read_cell(names_rng)
        items = [p.strip() for p in current.split(",") if p.strip()] if current else []
        # remove case-insensitively
        items = [x for x in items if x.lower() != user_name.lower()]
        joined = ", ".join(items)

        if items:
            # still people who can't: keep ✖
            _write_cell(f"{TAB}!{raid_col}{target_row}", "✖")
        else:
            # nobody left: flip to ✔
            _write_cell(f"{TAB}!{raid_col}{target_row}", "✔")

        _write_cell(names_rng, joined)
        new_flag = "✖" if items else "✔"
        return True, new_flag, joined
    return False, "", ""

