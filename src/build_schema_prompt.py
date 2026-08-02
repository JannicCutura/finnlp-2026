"""Generate the in-prompt schema text that the model sees.

The schema is derived from WHAT IS ACTUALLY IN THE GRAPH, not from the ontology
files. GLEIF's ontology modules declare ~106 properties, but our mapping
populates only a subset; advertising unpopulated predicates would invite queries
that are syntactically fine and always return nothing. Every class, property and
controlled value below is therefore backed by a count from the store, and
ontology labels/definitions are joined in only as documentation.

Output is written to schema/prompt_schema.md and committed -- it is a released
artifact (FinNLP open-research policy) and the input to the
no-schema vs schema-in-context ablation.

Usage:
    python build_schema_prompt.py --store data/oxigraph --out schema/prompt_schema.md
"""

from __future__ import annotations
import argparse, glob, os, sys, time
from pyoxigraph import Store
from rdflib import Graph, RDFS, URIRef
from rdflib.namespace import SKOS

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PREFIXES = [
    ("gleif-L1",   "https://www.gleif.org/ontology/L1/"),
    ("gleif-L2",   "https://www.gleif.org/ontology/L2/"),
    ("gleif-base", "https://www.gleif.org/ontology/Base/"),
    ("gleif-elf",  "https://www.gleif.org/ontology/EntityLegalForm/"),
    ("gleif-ra",   "https://www.gleif.org/ontology/RegistrationAuthority/"),
    ("gleif-re",   "https://www.gleif.org/ontology/ReportingException/"),
    ("kgx",        "https://example.org/gleif-kg/ontology/"),
    ("lei",        "https://www.gleif.org/data/lei/"),
    ("jur",        "https://www.gleif.org/data/jurisdiction/"),
    ("elf",        "https://www.gleif.org/data/elf/"),
    ("rdfs",       "http://www.w3.org/2000/01/rdf-schema#"),
]


def short(u: str) -> str:
    for p, ns in PREFIXES:
        if u.startswith(ns):
            return f"{p}:{u[len(ns):]}"
    return f"<{u}>"


def load_docs(schema_dir: str) -> dict[str, tuple[str, str]]:
    """IRI -> (label, definition) from the GLEIF ontology modules."""
    g = Graph()
    for f in sorted(glob.glob(os.path.join(schema_dir, "ontology", "*.ttl"))):
        g.parse(f, format="turtle")
    out = {}
    for s in set(g.subjects()):
        if not isinstance(s, URIRef):
            continue
        lbl = next((str(o) for o in g.objects(s, RDFS.label)), "")
        dfn = next((str(o) for o in g.objects(s, SKOS.definition)), "")
        if lbl or dfn:
            out[str(s)] = (lbl, dfn)
    return out


def q(store, sparql):
    return list(store.query(sparql))


def load_minted_docs(store) -> dict[str, tuple[str, str]]:
    """Labels/definitions for our minted kgx: vocabulary.

    These are emitted into the graph by gleif_to_rdf.py rather than living in a
    .ttl under schema/ontology/, so they must be read back from the store or the
    model sees the traversal shortcuts with no explanation at all.
    """
    rows = q(store, """
      SELECT ?s ?l ?d WHERE {
        ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l .
        OPTIONAL { ?s <http://www.w3.org/2004/02/skos/core#definition> ?d }
        FILTER(STRSTARTS(STR(?s), "https://example.org/gleif-kg/ontology/"))
      }""")
    out = {}
    for r in rows:
        iri = str(r[0]).strip("<>")
        lbl = str(r[1]).split('"^^')[0].strip('"')
        dfn = str(r[2]).split('"^^')[0].strip('"') if r[2] is not None else ""
        out[iri] = (lbl, dfn)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--schema-dir", default="schema")
    ap.add_argument("--out", default="schema/prompt_schema.md")
    a = ap.parse_args()

    store = Store(a.store)
    docs = load_docs(a.schema_dir)
    docs.update(load_minted_docs(store))   # our kgx: vocabulary lives in the graph
    t0 = time.time()

    print("counting classes ...", flush=True)
    classes = [(str(r[0]).strip("<>"), int(str(r[1]).split('"')[1]))
               for r in q(store, "SELECT ?c (COUNT(?s) AS ?n) WHERE { ?s a ?c } GROUP BY ?c ORDER BY DESC(?n)")]
    print(f"  {len(classes)} classes [{time.time()-t0:.0f}s]", flush=True)

    print("counting predicates ...", flush=True)
    preds = [(str(r[0]).strip("<>"), int(str(r[1]).split('"')[1]))
             for r in q(store, "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p ORDER BY DESC(?n)")]
    print(f"  {len(preds)} predicates [{time.time()-t0:.0f}s]", flush=True)

    # Controlled vocabularies: enumerate the distinct IRI objects of key predicates.
    enums: dict[str, list[tuple[str, int]]] = {}
    for label, pred in [
        ("Registration status", "https://www.gleif.org/ontology/Base/hasRegistrationStatus"),
        ("Entity status",       "https://www.gleif.org/ontology/Base/hasEntityStatus"),
        ("Relationship status", "https://www.gleif.org/ontology/L2/hasRelationshipStatus"),
        ("Exception reason",    "https://www.gleif.org/ontology/ReportingException/hasExceptionReason"),
    ]:
        rows = q(store, f"SELECT ?o (COUNT(*) AS ?n) WHERE {{ ?s <{pred}> ?o }} GROUP BY ?o ORDER BY DESC(?n)")
        enums[label] = [(short(str(r[0]).strip("<>")), int(str(r[1]).split('"')[1])) for r in rows]
    print(f"  controlled vocabularies [{time.time()-t0:.0f}s]", flush=True)

    L = []
    w = L.append
    w("# GLEIF Knowledge Graph — SPARQL schema\n")
    w("Snapshot: GLEIF Golden Copy publish `20260723-1600`. "
      "Every class, property and value below is present in the graph with the stated count.\n")

    w("\n## Prefixes\n\n```sparql")
    for p, ns in PREFIXES:
        w(f"PREFIX {p}: <{ns}>")
    w("```\n")

    w("\n## Node shapes\n")
    w("- **Entities** are IRIs `lei:{LEI}`, e.g. `lei:001GPB6A9XPE8XJICC14`. "
      "The LEI is also a literal via `gleif-L1:LEI`.")
    w("- **Relationships are REIFIED**: a relationship record node links to both endpoints "
      "via `gleif-L2:hasChild` / `gleif-L2:hasParent`. For traversal, prefer the direct "
      "`kgx:has*` shortcut edges below, which connect entity to entity and support `+` closure.")
    w("- **Reporting exceptions** are nodes stating why an entity has NO parent; they link "
      "to the entity via `gleif-re:hasReportingEntity`.")
    w("- Jurisdictions are IRIs `jur:{ISO-3166 code}` (e.g. `jur:DE` for Germany, `jur:US`). "
      "Their `rdfs:label` is the **code itself**, not the country name — match on the IRI.")
    w("- Legal forms are IRIs `elf:{ELF code}` whose `rdfs:label` IS human-readable "
      "(e.g. `elf:6QQB` → \"Gesellschaft mit beschränkter Haftung\").\n")

    w("\n## Relationship direction — get this right\n")
    w("Every relationship edge points **from the subordinate entity to the superior one**. "
      "The subject is always the child/fund/branch; the object is always the parent/manager/umbrella.\n")
    w("```sparql")
    w("?child    kgx:hasDirectConsolidationParent    ?parent      # child -> its parent")
    w("?child    kgx:hasUltimateConsolidationParent  ?parent      # child -> its ultimate parent")
    w("?fund     kgx:hasFundManager                  ?manager     # fund -> managing entity")
    w("?subfund  kgx:hasUmbrellaFund                 ?umbrella    # sub-fund -> umbrella fund")
    w("?feeder   kgx:hasMasterFund                   ?master      # feeder fund -> master fund")
    w("?branch   kgx:hasBranchParent                 ?headOffice  # branch -> head office")
    w("```\n")
    w("Consequently:\n")
    w("```sparql")
    w("# ANCESTORS of X (X is the subject):")
    w("lei:X kgx:hasDirectConsolidationParent+ ?ancestor .")
    w("")
    w("# DESCENDANTS / subsidiaries of X (X is the object):")
    w("?descendant kgx:hasDirectConsolidationParent+ lei:X .")
    w("```\n")
    w("Writing these backwards produces a valid query that silently returns nothing.\n")

    w("\n## Expressing \"the register cannot answer this\"\n")
    w("If the graph does not hold the requested information (e.g. shareholding "
      "percentages, revenue, employee counts — none of which the LEI register records), "
      "return a query that provably yields **zero rows**:\n")
    w("```sparql")
    w("SELECT ?x WHERE { FILTER(false) }")
    w("```")
    w("Do **not** write an empty group pattern `WHERE { }`. In SPARQL that matches the "
      "empty solution mapping and returns **one** row with unbound variables, not zero. "
      "Likewise, do not match an entity and simply omit the missing property — that also "
      "returns a row.\n")

    w("\n## Classes\n")
    w("| class | instances | meaning |")
    w("|---|--:|---|")
    for iri, n in classes:
        if n < 100:
            continue
        lbl, dfn = docs.get(iri, ("", ""))
        desc = (dfn or lbl or "").replace("\n", " ")
        w(f"| `{short(iri)}` | {n:,} | {desc[:110]} |")

    w("\n## Properties\n")
    w("| property | triples | meaning |")
    w("|---|--:|---|")
    for iri, n in preds:
        if iri.endswith("22-rdf-syntax-ns#type"):
            continue
        lbl, dfn = docs.get(iri, ("", ""))
        desc = (dfn or lbl or "").replace("\n", " ")
        w(f"| `{short(iri)}` | {n:,} | {desc[:110]} |")

    w("\n## Controlled values\n")
    for label, vals in enums.items():
        if not vals:
            continue
        shown = ", ".join(f"`{v}` ({n:,})" for v, n in vals[:12])
        w(f"- **{label}:** {shown}")

    w("\n## Notes that matter for correctness\n")
    w("- GLEIF Level 2 is **accounting consolidation** (IFRS 10), not equity ownership. "
      "There are **no shareholding percentages** in this graph.")
    w("- The **ultimate** parent is recorded directly as its own edge "
      "(`kgx:hasUltimateConsolidationParent`); it does **not** need transitive traversal.")
    w("- Most entities have **no** parent: they carry a reporting exception instead. "
      "A question about a parent may correctly have an empty answer.")
    w("- **Do NOT filter by registration status unless the question explicitly asks for "
      "current / active / live entities.** Most questions do not. Many valid answers are "
      "`LAPSED` or `RETIRED` entities, and adding `gleif-L1:ISSUED` would wrongly exclude them. "
      "Only when the question does ask, add `?e gleif-base:hasRegistrationStatus gleif-L1:ISSUED`.")
    w("- Legal names are plain literals with **no language tag**. Match them as "
      "`\"Some Name\"`, never `\"Some Name\"@en`.")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    text = "\n".join(L) + "\n"
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"\nwrote {a.out}  ({len(text):,} chars, ~{len(text)//4:,} tokens)  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    main()
