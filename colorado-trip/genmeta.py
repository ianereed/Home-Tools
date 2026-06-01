"""
genmeta.py — manual-edit detection + provenance for generated colorado-trip tabs.

Every generative builder in this project (rebuild_trip_tabs.py, add_dining_guide.py,
update_activities_mtb.py, add_trailhead_distances.py, ...) regenerates whole tabs from
Python source. That means any edit made BY HAND in the live Google Sheet — by Ian or
Anny — is silently destroyed on the next rebuild.

This module prevents that. It fingerprints each tab as it is generated (sha256 of the
tab's *values*) and stores the fingerprint + a timestamp in a hidden "_genmeta" tab in
the spreadsheet. Before a builder overwrites a tab, it compares the live tab's current
hash against the stored as-generated hash:

    same hash  -> untouched since we generated it -> safe to rebuild
    different  -> a human edited it -> "dirty" -> DO NOT overwrite; surface for review

The store lives in the sheet (not a git sidecar) so it works no matter which session or
machine runs the builder. Fingerprints are over cell VALUES only (get_all_values), so
intentional formatting-only human changes are not tracked — by design.

Typical use in a builder
-------------------------
    import genmeta
    meta = genmeta.load(sh)                      # read _genmeta once at start

    # inside flush(), before overwriting an existing tab:
    if genmeta.is_dirty(sh, title, meta) and title not in FORCE:
        DIRTY.append(title); return existing_gid          # skip — don't clobber

    ... write the tab ...
    genmeta.record(sh, title, meta)              # after writing (re-reads live values)

    genmeta.save(sh, meta)                       # persist _genmeta at the very end
    genmeta.report(DIRTY)                        # print the skipped-tabs summary

The visible per-page marker that tells a human "this tab is generated, your edits are
tracked" is added by the builder itself (see Tab.genmarker in rebuild_trip_tabs.py) —
it's part of the tab content, so it's covered by the same fingerprint.
"""

import hashlib
import json
import datetime
import gspread

META_TAB = "_genmeta"
_HEADER = ["tab", "content_sha", "last_generated"]


def _today():
    return datetime.date.today().isoformat()


def _normalize(values):
    """Trim trailing empty cells per row and trailing all-empty rows, so incidental
    grid-size differences never cause a false 'dirty'. Returns a list of lists of str."""
    rows = []
    for row in values:
        r = [("" if c is None else str(c)) for c in row]
        while r and r[-1] == "":
            r.pop()
        rows.append(r)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def content_hash(values):
    """Stable sha256 over a 2-D values grid (as returned by ws.get_all_values())."""
    norm = _normalize(values)
    blob = json.dumps(norm, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _live_values(sh, title):
    """Live cell values for a tab (single API read), or None if it doesn't exist."""
    try:
        resp = sh.values_get(f"'{title}'")
    except gspread.exceptions.APIError:
        return None
    return resp.get("values", [])


def load(sh):
    """Read the _genmeta tab into {title: {'sha': str, 'ts': str}}. Empty dict if the
    tab doesn't exist yet (first-ever run)."""
    try:
        ws = sh.worksheet(META_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return {}
    meta = {}
    for row in ws.get_all_values()[1:]:  # skip header
        if not row or not row[0].strip():
            continue
        title = row[0]
        sha = row[1] if len(row) > 1 else ""
        ts = row[2] if len(row) > 2 else ""
        meta[title] = {"sha": sha, "ts": ts}
    return meta


def is_dirty(sh, title, meta):
    """True iff a human edited `title` since we last generated it. False when there is
    no baseline yet (first generation) or the tab doesn't exist live."""
    base = meta.get(title)
    if not base or not base.get("sha"):
        return False
    live = _live_values(sh, title)
    if live is None:
        return False
    return content_hash(live) != base["sha"]


def record(sh, title, meta):
    """Fingerprint the freshly-written live tab and store it in `meta` (in memory).
    Re-reads the live values so the hash matches exactly what is_dirty() will read."""
    live = _live_values(sh, title)
    if live is None:
        return
    meta[title] = {"sha": content_hash(live), "ts": _today()}


def save(sh, meta):
    """Persist `meta` back to the hidden _genmeta tab (creates + hides it if needed)."""
    try:
        ws = sh.worksheet(META_TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=META_TAB, rows=max(len(meta) + 10, 50), cols=3)
    rows = [_HEADER] + [[t, meta[t]["sha"], meta[t].get("ts", "")] for t in sorted(meta)]
    ws.clear()
    ws.update(range_name="A1", values=rows)
    # hide it from casual view — it's machine bookkeeping, not trip content
    try:
        sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "hidden": True},
            "fields": "hidden"}}]})
    except Exception:
        pass  # hiding is cosmetic; never fail the run over it


def report(dirty):
    """Print a human summary of tabs that were skipped because of manual edits."""
    if not dirty:
        return
    print("\n" + "=" * 60)
    print(f"⚠️  {len(dirty)} tab(s) had MANUAL EDITS and were NOT overwritten:")
    for t in dirty:
        print(f"     • {t}")
    print("   Fold those edits into the Python source, then rerun.")
    print(f"   To overwrite intentionally:  --force \"{dirty[0]}\"   (or --force-all)")
    print("=" * 60)


MARKER_PREFIX = "🤖 Auto-generated"


def marker_text(builder="rebuild_trip_tabs.py"):
    """The visible per-page contract line. Builders drop this into each tab's footer."""
    return (f"{MARKER_PREFIX} {_today()} by {builder} — edits you make directly in this "
            "tab are auto-detected and held for review before the next rebuild (they "
            "won't be silently overwritten). To intentionally replace it, rerun the "
            "builder with --force.")
