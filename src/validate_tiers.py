"""Hand-written reference query per evaluation tier — the ingest gate.

Every tier in agent-files/initial-proposal.md section 7 must be answerable by a
query a human wrote, BEFORE we author the question set or ask a model to
generate anything. If a tier cannot be expressed or is unacceptably slow here,
the tier design is wrong and must change now rather than mid-evaluation.

Of particular interest is tier (c): GLEIF models relationships as REIFIED
records, so transitive traversal needs an inverse+sequence property path
    (^gleif-L2:hasChild/gleif-L2:hasParent)+
rather than a plain `hasParent+`. Whether that performs at ~69M triples is the
open risk this script settles.

Usage:
    python validate_tiers.py --store data/oxigraph
"""

from __future__ import annotations
import argparse, sys, time
from pyoxigraph import Store

# GLEIF legal names are multilingual; the Windows console defaults to cp1252 and
# would crash on the first non-Latin-1 name.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PREFIXES = """
PREFIX gleif-L1:   <https://www.gleif.org/ontology/L1/>
PREFIX gleif-L2:   <https://www.gleif.org/ontology/L2/>
PREFIX gleif-base: <https://www.gleif.org/ontology/Base/>
PREFIX gleif-elf:  <https://www.gleif.org/ontology/EntityLegalForm/>
PREFIX gleif-re:   <https://www.gleif.org/ontology/ReportingException/>
PREFIX kgx:        <https://example.org/gleif-kg/ontology/>
PREFIX rdfs:       <http://www.w3.org/2000/01/rdf-schema#>
PREFIX lei:        <https://www.gleif.org/data/lei/>
PREFIX jur:        <https://www.gleif.org/data/jurisdiction/>
"""

QUERIES: list[tuple[str, str, str]] = [
    ("a  lookup", "identity + attributes of one entity", """
SELECT ?name ?jurLabel ?elfLabel ?regStatus WHERE {
  ?e gleif-L1:LEI "001GPB6A9XPE8XJICC14" ;
     gleif-L1:hasLegalName ?name ;
     gleif-base:hasLegalJurisdiction ?jur ;
     gleif-base:hasRegistrationStatus ?regStatus .
  ?jur rdfs:label ?jurLabel .
  OPTIONAL { ?e gleif-L1:hasLegalForm ?elf . ?elf rdfs:label ?elfLabel }
} LIMIT 5"""),

    ("b1 single-hop", "direct consolidation parent", """
SELECT ?childName ?parentName WHERE {
  ?rel a gleif-L2:DirectConsolidation ;
       gleif-L2:hasChild ?c ; gleif-L2:hasParent ?p .
  ?c gleif-L1:hasLegalName ?childName .
  ?p gleif-L1:hasLegalName ?parentName .
} LIMIT 5"""),

    ("b2 single-hop", "fund -> managing entity (minted class)", """
SELECT ?fundName ?managerName WHERE {
  ?rel a kgx:FundManagement ;
       gleif-L2:hasChild ?f ; gleif-L2:hasParent ?m .
  ?f gleif-L1:hasLegalName ?fundName .
  ?m gleif-L1:hasLegalName ?managerName .
} LIMIT 5"""),

    ("c1 multi-hop", "explicit 2-step consolidation chain", """
SELECT ?aName ?bName ?cName WHERE {
  ?r1 a gleif-L2:DirectConsolidation ; gleif-L2:hasChild ?a ; gleif-L2:hasParent ?b .
  ?r2 a gleif-L2:DirectConsolidation ; gleif-L2:hasChild ?b ; gleif-L2:hasParent ?c .
  ?a gleif-L1:hasLegalName ?aName .
  ?b gleif-L1:hasLegalName ?bName .
  ?c gleif-L1:hasLegalName ?cName .
} LIMIT 5"""),

    ("c2 multi-hop", "subfund -> umbrella -> manager (no stored shortcut)", """
SELECT ?subName ?umbName ?mgrName WHERE {
  ?r1 a kgx:SubFundRelationship ; gleif-L2:hasChild ?s ; gleif-L2:hasParent ?u .
  ?r2 a kgx:FundManagement      ; gleif-L2:hasChild ?u ; gleif-L2:hasParent ?m .
  ?s gleif-L1:hasLegalName ?subName .
  ?u gleif-L1:hasLegalName ?umbName .
  ?m gleif-L1:hasLegalName ?mgrName .
} LIMIT 5"""),

    ("d  filter", "ISSUED entities in a jurisdiction, by legal form", """
SELECT ?elfLabel (COUNT(?e) AS ?n) WHERE {
  ?e gleif-base:hasLegalJurisdiction jur:DE ;
     gleif-base:hasRegistrationStatus gleif-L1:ISSUED ;
     gleif-L1:hasLegalForm ?elf .
  ?elf rdfs:label ?elfLabel .
} GROUP BY ?elfLabel ORDER BY DESC(?n) LIMIT 5"""),

    ("e  aggregate", "top jurisdictions by ISSUED entity count", """
SELECT ?jurLabel (COUNT(?e) AS ?n) WHERE {
  ?e gleif-base:hasLegalJurisdiction ?j ;
     gleif-base:hasRegistrationStatus gleif-L1:ISSUED .
  ?j rdfs:label ?jurLabel .
} GROUP BY ?jurLabel ORDER BY DESC(?n) LIMIT 5"""),

    ("f1 abstain", "no parent, but a MACHINE-READABLE reason exists", """
SELECT ?name ?reason WHERE {
  ?exc a gleif-re:DirectConsolidationReportingException ;
       gleif-re:hasReportingEntity ?e ;
       gleif-re:hasExceptionReason ?reason .
  ?e gleif-L1:hasLegalName ?name .
  FILTER NOT EXISTS { ?r a gleif-L2:DirectConsolidation ; gleif-L2:hasChild ?e }
} LIMIT 5"""),

    ("f3 abstain", "nonexistent LEI -> MUST return zero rows", """
SELECT ?name WHERE {
  ?e gleif-L1:LEI "ZZZZNOTAREALLEI00000" ; gleif-L1:hasLegalName ?name .
} LIMIT 5"""),
]

# Tier (c) transitive path: seeded from a real chain, then walked with the
# inverse+sequence property path that reification forces.
SEED_Q = """
SELECT ?a WHERE {
  ?r1 a gleif-L2:DirectConsolidation ; gleif-L2:hasChild ?a ; gleif-L2:hasParent ?b .
  ?r2 a gleif-L2:DirectConsolidation ; gleif-L2:hasChild ?b ; gleif-L2:hasParent ?c .
} LIMIT 1"""

# Faithful to GLEIF's reified model, but measured at 58.2s for a 2-row walk at
# 69M triples. Kept for the paper's systems finding; not used in the eval loop.
TRANSITIVE_REIFIED = """
SELECT ?ancName WHERE {
  <%s> (^gleif-L2:hasChild/gleif-L2:hasParent)+ ?anc .
  ?anc gleif-L1:hasLegalName ?ancName .
} LIMIT 10"""

# Identical results over the denormalized shortcut edge, in <0.01s.
TRANSITIVE_SHORTCUT = """
SELECT ?ancName WHERE {
  <%s> kgx:hasDirectConsolidationParent+ ?anc .
  ?anc gleif-L1:hasLegalName ?ancName .
} LIMIT 10"""


def run(store, label, desc, q, expect_empty=False):
    t0 = time.time()
    try:
        rows = list(store.query(PREFIXES + q))
    except Exception as e:
        print(f"  [{label}] {desc}\n     ERROR: {type(e).__name__}: {e}")
        return False
    dt = time.time() - t0
    ok = (len(rows) == 0) if expect_empty else (len(rows) > 0)
    flag = "OK " if ok else "FAIL"
    print(f"  [{flag}] {label:15} {dt:7.2f}s  rows={len(rows):<3} {desc}")
    for r in rows[:3]:
        # a QuerySolution iterates over its VALUES (terms), not variable names
        vals = []
        for term in r:
            if term is None:
                vals.append("-")
                continue
            s = str(term)
            if s.startswith("<") and s.endswith(">"):          # IRI -> last segment
                s = s[1:-1].rstrip("/").rsplit("/", 1)[-1]
            else:                                              # literal -> bare lexical form
                s = s.split('"^^')[0]
                if s.startswith('"'):
                    s = s[1:].rsplit('"', 1)[0]
            vals.append(s)
        print(f"          {' | '.join(vals)[:150]}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--compare-paths", action="store_true",
                    help="also run the faithful reified traversal (slow: ~60s) to document the gap")
    a = ap.parse_args()
    store = Store(a.store)

    print("=== TIER VALIDATION ===")
    allok = True
    for label, desc, q in QUERIES:
        allok &= run(store, label, desc, q, expect_empty=label.startswith("f3"))

    print("\n=== TIER (c) TRANSITIVE PROPERTY PATH (the reification risk) ===")
    seed = list(store.query(PREFIXES + SEED_Q))
    if not seed:
        print("  no chain seed found — cannot test transitive path")
        allok = False
    else:
        s = str(seed[0]["a"]).strip("<>")
        print(f"  seed: {s}")
        allok &= run(store, "c3 shortcut", "hasDirectConsolidationParent+ ancestors",
                     TRANSITIVE_SHORTCUT % s)
        if a.compare_paths:
            run(store, "c3 reified", "(^hasChild/hasParent)+ -- faithful, slow",
                TRANSITIVE_REIFIED % s)

    print("\n" + ("ALL TIERS ANSWERABLE" if allok else "SOME TIERS FAILED — tier design must change"))


if __name__ == "__main__":
    main()
