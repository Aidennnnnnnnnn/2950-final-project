"""
Cornell Class Roster API collector for INFO 2950 course project.
API docs: https://classes.cornell.edu/content/SP25/api-details
Rate limit per docs: no more than 1 request per second.
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
RAW_DIR = "raw"          # cached API responses live here
MIN_GAP = 1.05           # seconds between requests (docs: max 1/sec)

ROSTERS = [
    "FA14", "SP15", "FA15", "SP16", "FA16", "SP17", "FA17", "SP18",
    "FA18", "SP19", "FA19", "SP20", "FA20", "SP21", "FA21", "SP22",
    "FA22", "SP23", "FA23", "SP24", "FA24", "SP25"
]

SUBJECTS = [
    "CS", "INFO", "ECE", "ORIE", "STSCI", "MATH", "PHYS", "BIOG",
    "ECON", "GOVT", "PSYCH", "SOC", "COMM", "PHIL", "ENGL", "HIST", "AEM", "HD"
]

HEADERS = {
    # NetID added for polite academic API identification
    "User-Agent": "Cornell-INFO2950-StudentProject/1.0 (contact: adh242@cornell.edu)"
}

os.makedirs(RAW_DIR, exist_ok=True)
fetch_log = []
_last_request = 0.0


# ---------------------------------------------------------------
# Polite, cached GET
# ---------------------------------------------------------------
def polite_get(url, cache_path):
    """Return parsed JSON. Uses the disk cache if present; otherwise
    waits so requests stay >= MIN_GAP apart, even after errors."""
    global _last_request

    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    wait = MIN_GAP - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        fetch_log.append((url, resp.status_code))
        if resp.status_code != 200:
            return None
        data = resp.json()
        with open(cache_path, "w") as f:
            json.dump(data, f)
        return data
    except Exception as e:
        fetch_log.append((url, repr(e)))
        return None
    finally:
        _last_request = time.monotonic()


# ---------------------------------------------------------------
# Stage 1: verify rosters and subject codes actually exist
# ---------------------------------------------------------------
print("Checking available rosters...")
rosters_json = polite_get(f"{BASE}/config/rosters.json",
                          f"{RAW_DIR}/config_rosters.json")

if not rosters_json or "data" not in rosters_json:
    raise RuntimeError("Failed to fetch initial rosters list from Cornell API.")

available = {r.get("slug") for r in rosters_json["data"]["rosters"]}
missing_rosters = [r for r in ROSTERS if r not in available]
if missing_rosters:
    print("  Not available, skipping:", missing_rosters)
rosters_to_fetch = [r for r in ROSTERS if r in available]

subject_coverage = []   # (roster, subject, exists?)
for roster in rosters_to_fetch:
    subj_json = polite_get(f"{BASE}/config/subjects.json?roster={roster}",
                           f"{RAW_DIR}/config_subjects_{roster}.json")
    if subj_json is None:
        print(f"  Could not get subject list for {roster}")
        continue
    codes = {s.get("value") for s in subj_json["data"]["subjects"]}
    for subj in SUBJECTS:
        subject_coverage.append((roster, subj, subj in codes))

coverage_df = pd.DataFrame(subject_coverage,
                           columns=["roster", "subject", "exists"])
coverage_df.to_csv("subject_coverage.csv", index=False)
gaps = coverage_df[~coverage_df["exists"]]
if len(gaps):
    print(f"  {len(gaps)} roster/subject combos don't exist (see subject_coverage.csv)")


# ---------------------------------------------------------------
# Stage 2: fetch classes (only combos that exist)
# ---------------------------------------------------------------
to_fetch = coverage_df[coverage_df["exists"]]
print(f"Fetching {len(to_fetch)} roster/subject combos "
      f"(cached ones are skipped, ~1.05 sec each otherwise)...")

for roster, subj in zip(to_fetch["roster"], to_fetch["subject"]):
    polite_get(f"{BASE}/search/classes.json?roster={roster}&subject={subj}",
               f"{RAW_DIR}/{roster}_{subj}.json")

pd.DataFrame(fetch_log, columns=["url", "status"]).to_csv("fetch_log.csv", index=False)


# ---------------------------------------------------------------
# Stage 3: parse cached files into DataFrames (no network)
# ---------------------------------------------------------------
rows = []
for roster, subj in zip(to_fetch["roster"], to_fetch["subject"]):
    path = f"{RAW_DIR}/{roster}_{subj}.json"
    if not os.path.exists(path):
        continue
    with open(path) as f:
        data = json.load(f)

    catalog_dttm = (data.get("meta") or {}).get("catalogDttm")
    semester = "Fall" if roster[:2] == "FA" else "Spring"
    year = 2000 + int(roster[2:])

    for c in (data.get("data") or {}).get("classes", []):
        rows.append({
            "roster": roster,
            "year": year,
            "semester": semester,
            "queried_subject": subj,
            "subject": c.get("subject"),
            "catalog_nbr": c.get("catalogNbr"),
            "crse_id": c.get("crseId"),
            "title": c.get("titleLong") or c.get("titleShort") or "",
            "description": c.get("description") or c.get("catalogDescription") or "",
            "catalog_dttm": catalog_dttm,
        })

df = pd.DataFrame(rows)
df.to_csv("cornell_courses_raw.csv", index=False)

if df["crse_id"].notna().any():
    df_unique = df.drop_duplicates(subset=["roster", "crse_id"])
    df_unique.to_csv("cornell_courses_dedup.csv", index=False)
    print(f"\nDone: {len(df)} total offerings saved to cornell_courses_raw.csv.")
    print(f"Done: {len(df_unique)} unique courses saved to cornell_courses_dedup.csv.")
else:
    print(f"\nDone: {len(df)} rows saved to cornell_courses_raw.csv.")

print("Finished! All files cached and CSVs created successfully.")