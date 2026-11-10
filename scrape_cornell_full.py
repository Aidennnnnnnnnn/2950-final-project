"""
Cornell Class Roster API -- FULL-database collector (INFO 2950 project).
API docs: https://classes.cornell.edu/content/SP25/api-details
Docs rule: "no more than 1 request per second."

"100%" here means: every roster x every subject the API itself lists.
Classes hidden by "Schedule Print" are hidden from the API too (per docs),
so nothing can collect those.

Safe to stop and restart at any time: every successful response is cached
in raw/, and cached files are never re-requested.
"""

import json
import os
import time

import pandas as pd
import requests

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
BASE = "https://classes.cornell.edu/api/2.0"
RAW_DIR = "raw"
MIN_GAP = 1.05                 # seconds between requests (docs: max 1/sec)
RETRY_CODES = {429, 500, 502, 503, 504}   # "busy / try later" responses
BACKOFF = [10, 30, 90]         # seconds to wait before retry 1, 2, 3

# Test on a small slice first, e.g. ["SP25"]. None = every roster.
ROSTER_LIMIT = None

HEADERS = {
    "User-Agent": "Cornell-INFO2950-StudentProject/1.0 (contact: adh242@cornell.edu)"
}

SEASONS = {"FA": "Fall", "SP": "Spring", "SU": "Summer", "WI": "Winter"}

os.makedirs(RAW_DIR, exist_ok=True)
fetch_log = []
_last_request = 0.0


# ---------------------------------------------------------------
# Polite, cached GET with retries
# ---------------------------------------------------------------
def polite_get(url, cache_path):
    """Return parsed JSON, or None on failure.
    - Cached file exists -> read it, no network.
    - Otherwise wait so requests stay >= MIN_GAP apart.
    - On 429 / 5xx / network error -> back off and retry (up to 3 times).
    - Other errors (e.g. 404) -> give up immediately.
    """
    global _last_request

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    for attempt in range(len(BACKOFF) + 1):
        retry_after = None
        wait = MIN_GAP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            fetch_log.append((url, resp.status_code, attempt))
            if resp.status_code == 200:
                data = resp.json()
                tmp = cache_path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, cache_path)   # never leaves a half-written cache file
                return data
            if resp.status_code not in RETRY_CODES:
                return None
            retry_after = resp.headers.get("Retry-After")
        except (requests.RequestException, ValueError) as e:
            fetch_log.append((url, repr(e), attempt))
        finally:
            _last_request = time.monotonic()

        if attempt < len(BACKOFF):
            pause = BACKOFF[attempt]
            if retry_after and retry_after.isdigit():   # server says how long to wait
                pause = max(pause, int(retry_after))
            print(f"   retry {attempt + 1}/{len(BACKOFF)} in {pause}s: {url}")
            time.sleep(pause)

    return None


def scalars(d, prefix=""):
    """Keep only simple values (text, numbers, booleans, None) from a dict.
    This flattens whatever fields the API sends, so no field names are guessed."""
    return {prefix + k: v for k, v in d.items() if not isinstance(v, (dict, list))}


# ---------------------------------------------------------------
# Stage 1: ask the API for EVERY roster (Fall, Spring, Summer, Winter)
# ---------------------------------------------------------------
print("Stage 1: listing rosters...")
rosters_json = polite_get(f"{BASE}/config/rosters.json", f"{RAW_DIR}/config_rosters.json")
if not rosters_json or "data" not in rosters_json:
    raise RuntimeError("Failed to fetch the roster list from the Cornell API.")

rosters = [r.get("slug") for r in rosters_json["data"]["rosters"] if r.get("slug")]
if ROSTER_LIMIT:
    rosters = [r for r in rosters if r in ROSTER_LIMIT]
print(f"  {len(rosters)} rosters: {rosters[0]} ... {rosters[-1]}")


# ---------------------------------------------------------------
# Stage 2: ask the API for EVERY subject in each roster
# ---------------------------------------------------------------
print("Stage 2: listing subjects per roster...")
pairs = []   # (roster, subject)
for roster in rosters:
    subj_json = polite_get(f"{BASE}/config/subjects.json?roster={roster}",
                           f"{RAW_DIR}/config_subjects_{roster}.json")
    if not subj_json or "data" not in subj_json:
        print(f"  Could not get subject list for {roster}")
        continue
    for s in subj_json["data"]["subjects"]:
        if s.get("value"):
            pairs.append((roster, s["value"]))

pd.DataFrame(pairs, columns=["roster", "subject"]).to_csv("roster_subject_pairs.csv", index=False)


# ---------------------------------------------------------------
# Stage 2b: lookup lists (careers, groups, levels) + subjects, per roster
#   ~3 extra requests per roster (a few minutes total)
#   Shape is flattened generically: any list inside "data" becomes rows,
#   so no field names are guessed.
# ---------------------------------------------------------------
print("Stage 2b: fetching lookup lists...")
LOOKUPS = ["acadCareers", "acadGroups", "classLevels", "subjects"]
lookup_rows = []
for roster in rosters:
    for method in LOOKUPS:
        j = polite_get(f"{BASE}/config/{method}.json?roster={roster}",
                       f"{RAW_DIR}/config_{method}_{roster}.json")
        if not j or not isinstance(j.get("data"), dict):
            print(f"  Could not get {method} for {roster}")
            continue
        for list_name, items in j["data"].items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    lookup_rows.append({"roster": roster, "lookup": method,
                                        "list": list_name, **scalars(item)})

lookups = pd.DataFrame(lookup_rows)
lookups.to_csv("cornell_lookups.csv", index=False)
for method in LOOKUPS:
    n = int((lookups["lookup"] == method).sum()) if len(lookups) else 0
    print(f"  {method}: {n} rows")

uncached = sum(not os.path.exists(f"{RAW_DIR}/{r}_{s}.json") for r, s in pairs)
print(f"  {len(pairs)} roster/subject pairs, {uncached} not yet cached")
print(f"  Estimated time: ~{uncached * MIN_GAP / 3600:.1f} hours (safe to stop and resume)")


# ---------------------------------------------------------------
# Stage 3: fetch classes for every pair
# ---------------------------------------------------------------
print("Stage 3: fetching classes...")
for i, (roster, subj) in enumerate(pairs, 1):
    polite_get(f"{BASE}/search/classes.json?roster={roster}&subject={subj}",
               f"{RAW_DIR}/{roster}_{subj}.json")
    if i % 100 == 0:
        print(f"  {i}/{len(pairs)} done")

pd.DataFrame(fetch_log, columns=["url", "status", "attempt"]).to_csv("fetch_log.csv", index=False)


# ---------------------------------------------------------------
# Stage 4: parse cached files (no network)
#   all_classes.jsonl      -> LOSSLESS: every class object, untouched
#   cornell_classes.csv    -> one row per class listing, all top-level simple fields
#   cornell_meetings.csv   -> one row per enroll group x section x meeting
# ---------------------------------------------------------------
print("Stage 4: parsing...")
class_rows, meeting_rows = [], []

with open("all_classes.jsonl", "w") as out:
    for roster, subj in pairs:
        path = f"{RAW_DIR}/{roster}_{subj}.json"
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)

        meta = data.get("meta") or {}
        season = SEASONS.get(roster[:2], roster[:2])
        year = 2000 + int(roster[2:])

        for c in (data.get("data") or {}).get("classes") or []:
            out.write(json.dumps({"roster": roster, "queried_subject": subj,
                                  "meta": meta, "class": c}) + "\n")

            row = {"roster": roster, "year": year, "semester": season,
                   "queried_subject": subj,
                   "catalog_dttm": meta.get("catalogDttm")}   # absent FA25+ (per docs)
            row.update(scalars(c))
            class_rows.append(row)

            key = {"roster": roster, "subject": c.get("subject"),
                   "catalogNbr": c.get("catalogNbr"), "crseId": c.get("crseId")}
            for g_i, g in enumerate(c.get("enrollGroups") or []):
                for s in g.get("classSections") or []:
                    for m in (s.get("meetings") or [{}]):
                        meeting_rows.append({
                            **key, "enroll_group": g_i,
                            **scalars(g, "group_"), **scalars(s, "sec_"), **scalars(m, "mtg_"),
                            "instructors_json": json.dumps(m.get("instructors") or []),
                        })

classes = pd.DataFrame(class_rows)
classes.to_csv("cornell_classes.csv", index=False)
pd.DataFrame(meeting_rows).to_csv("cornell_meetings.csv", index=False)

print(f"\nDone: {len(classes)} class listings -> cornell_classes.csv")
print(f"Done: {len(meeting_rows)} meeting rows -> cornell_meetings.csv")
print("Lossless copy of everything -> all_classes.jsonl")
if len(classes) and not meeting_rows:
    print("WARNING: no meeting rows found. Check the nesting names in all_classes.jsonl "
          "(enrollGroups -> classSections -> meetings) and adjust Stage 4.")

failed = [u for u, st, a in fetch_log if st != 200 and a == len(BACKOFF)]
if failed:
    print(f"{len(failed)} requests failed after all retries (see fetch_log.csv). "
          "Re-run the script later; it will only retry those.")
