#!/usr/bin/env bash
# Extract the GLEIF golden-copy CSVs from the zips in data/raw/ into data/.
#
# The zips are downloaded MANUALLY from GLEIF's browser UI:
#   https://www.gleif.org/en/lei-data/gleif-golden-copy/download-the-golden-copy
# For each of LEI-CDF (lei2), Relationship Records (rr) and Reporting
# Exceptions (repex): take the *Full File* in *CSV* format and drop the .zip
# into data/raw/. Automated download is not possible -- GLEIF's WAF answers 403
# to every non-browser client (both leidata.gleif.org and goldencopy.gleif.org).
#
# Zips are matched by glob on the dataset name, so the publish-timestamp prefix
# (e.g. 20260723-1600-) does not need to be hardcoded; the newest is used.
#
# Idempotent: a dataset already extracted at the correct size is skipped.
#
# Usage: bash unzip_gleif.sh [RAW_DIR] [OUT_DIR]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW="${1:-$HERE/raw}"
OUT="${2:-$HERE}"
mkdir -p "$OUT"
echo "raw zips: $RAW"
echo "extract to: $OUT"

python - "$RAW" "$OUT" <<'PY'
import sys, os, glob, zipfile, time

raw, out = sys.argv[1], sys.argv[2]
rc = 0
for ds in ("lei2", "rr", "repex"):
    matches = sorted(glob.glob(os.path.join(raw, f"*-gleif-goldencopy-{ds}-golden-copy.csv.zip")))
    if not matches:
        print(f"[{ds:5}] NO ZIP FOUND matching *-gleif-goldencopy-{ds}-golden-copy.csv.zip")
        rc = 1
        continue
    zpath = matches[-1]  # newest publish if several are present
    with zipfile.ZipFile(zpath) as z:
        info = z.infolist()[0]
        target = os.path.join(out, info.filename)
        gib = info.file_size / 2**30
        if os.path.exists(target) and os.path.getsize(target) == info.file_size:
            print(f"[{ds:5}] already extracted ({gib:.2f} GiB), skipping")
            continue
        print(f"[{ds:5}] extracting {info.filename} ({gib:.2f} GiB) ...", flush=True)
        t0 = time.time()
        z.extract(info, out)
        got = os.path.getsize(target)
        if got == info.file_size:
            print(f"[{ds:5}] done in {time.time()-t0:.0f}s -> {info.filename}")
        else:
            print(f"[{ds:5}] SIZE MISMATCH got={got:,} want={info.file_size:,}")
            rc = 1
sys.exit(rc)
PY
rc=$?

echo "=========="
ls -lh "$OUT"/*.csv 2>/dev/null || echo "no CSVs extracted"
[[ $rc -eq 0 ]] && echo "ALL DONE" || echo "COMPLETED WITH ERRORS (rc=$rc)"
exit $rc
