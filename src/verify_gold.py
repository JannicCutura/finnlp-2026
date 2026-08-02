"""Verify every gold reference query by execution.

A gold query that returns nothing when it should return something is worse than
no gold at all: the scorer would mark a CORRECT model answer as wrong. This runs
every gold, checks it against its `expect_empty` flag, and reports the answer so
it can be eyeballed.

Usage:
    python verify_gold.py --store data/oxigraph --questions eval/questions.json
"""

from __future__ import annotations
import argparse, json, sys, time
from pyoxigraph import Store

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PREFIXES = """
PREFIX gleif-L1:   <https://www.gleif.org/ontology/L1/>
PREFIX gleif-L2:   <https://www.gleif.org/ontology/L2/>
PREFIX gleif-base: <https://www.gleif.org/ontology/Base/>
PREFIX gleif-elf:  <https://www.gleif.org/ontology/EntityLegalForm/>
PREFIX gleif-ra:   <https://www.gleif.org/ontology/RegistrationAuthority/>
PREFIX gleif-re:   <https://www.gleif.org/ontology/ReportingException/>
PREFIX kgx:        <https://example.org/gleif-kg/ontology/>
PREFIX rdfs:       <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lei:        <https://www.gleif.org/data/lei/>
PREFIX jur:        <https://www.gleif.org/data/jurisdiction/>
PREFIX elf:        <https://www.gleif.org/data/elf/>
"""


def fmt(term):
    if term is None:
        return "-"
    s = str(term)
    if s.startswith("<") and s.endswith(">"):
        return s[1:-1].rsplit("/", 1)[-1]
    s = s.split('"^^')[0]
    return s[1:].rsplit('"', 1)[0] if s.startswith('"') else s


def run_gold(store, gold, limit=1000):
    t0 = time.time()
    rows = []
    for i, sol in enumerate(store.query(PREFIXES + gold)):
        if i >= limit:
            break
        rows.append(tuple(fmt(t) for t in sol))
    return rows, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--questions", default="eval/questions.json")
    a = ap.parse_args()
    store = Store(a.store)
    items = json.load(open(a.questions, encoding="utf-8"))

    bad, slow = [], []
    by_tier = {}
    for it in items:
        exp_empty = it.get("expect_empty", False)
        try:
            rows, dt = run_gold(store, it["gold"])
        except Exception as e:
            print(f"[ERROR ] {it['id']:5} {type(e).__name__}: {str(e)[:120]}")
            bad.append(it["id"]); continue

        ok = (len(rows) == 0) if exp_empty else (len(rows) > 0)
        tag = "OK    " if ok else "BAD   "
        if not ok:
            bad.append(it["id"])
        if dt > 5:
            slow.append((it["id"], round(dt, 1)))
        by_tier.setdefault(it["tier"], []).append(ok)
        preview = "; ".join(" | ".join(r) for r in rows[:2])[:96]
        print(f"[{tag}] {it['id']:5} t={it['tier']:3} {dt:6.2f}s rows={len(rows):<5} "
              f"{'(expect empty)' if exp_empty else preview}")

    print("\n--- per tier ---")
    for t, oks in sorted(by_tier.items()):
        print(f"  tier {t:3}: {sum(oks)}/{len(oks)} gold queries valid")
    if slow:
        print("\n--- slow golds (>5s) ---")
        for i, d in slow:
            print(f"  {i}: {d}s")
    print(f"\n{len(items)-len(bad)}/{len(items)} gold queries verified"
          + (f"   PROBLEMS: {', '.join(bad)}" if bad else "   ALL GOOD"))


if __name__ == "__main__":
    main()
