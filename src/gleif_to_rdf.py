"""Convert the GLEIF Golden Copy CSVs into N-Triples using GLEIF's own ontology.

Design decisions (see agent-files/initial-proposal.md sections 6 and 15):

* **No reasoner.** Nothing is inferred at query time, so supertypes are
  MATERIALIZED here: an instance of L2:DirectConsolidation is also explicitly
  typed L2:AccountingConsolidation and base:LegalEntityRelationship. This keeps
  execution-accuracy scoring deterministic.

* **GLEIF vocabulary wherever it exists.** Classes/properties come from the
  published ontology modules in schema/ontology/.

* **Documented extension for fund relationships.** GLEIF's L2 ontology defines
  no class for IS_FUND-MANAGED_BY / IS_SUBFUND_OF / IS_FEEDER_TO, although they
  are 47% of the L2 graph. We mint three subclasses of
  base:LegalEntityRelationship in the KGX namespace below. This gap is a
  reportable finding, not a workaround to hide.

* **Only question-relevant L1 fields** are mapped (~15 of 338 columns). The rest
  are address transliterations and alternate-language names that would bloat the
  graph and the in-prompt schema without supporting any evaluation tier.

Usage:
    python gleif_to_rdf.py --data-dir data --out data/gleif.nt [--limit N] [--only-issued]
"""

from __future__ import annotations
import argparse, csv, glob, os, sys, time

csv.field_size_limit(10**9)

L1   = "https://www.gleif.org/ontology/L1/"
L2   = "https://www.gleif.org/ontology/L2/"
BASE = "https://www.gleif.org/ontology/Base/"
ELF  = "https://www.gleif.org/ontology/EntityLegalForm/"
RA   = "https://www.gleif.org/ontology/RegistrationAuthority/"
RE   = "https://www.gleif.org/ontology/ReportingException/"
LCC  = "https://www.omg.org/spec/LCC/Countries/CountryRepresentation/"
RDFT = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD  = "http://www.w3.org/2001/XMLSchema#"

# Our documented extension namespace (fund relationships absent from GLEIF's L2).
KGX  = "https://example.org/gleif-kg/ontology/"
# Instance namespaces.
D_LEI = "https://www.gleif.org/data/lei/"
D_REL = "https://www.gleif.org/data/relationship/"
D_EXC = "https://www.gleif.org/data/exception/"
D_JUR = "https://www.gleif.org/data/jurisdiction/"
D_ELF = "https://www.gleif.org/data/elf/"

_ESC = str.maketrans({"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"})


def lit(s: str, dt: str | None = None) -> str:
    v = '"' + s.translate(_ESC) + '"'
    return f"{v}^^<{dt}>" if dt else v


def iri(u: str) -> str:
    return f"<{u}>"


# GLEIF EntityCategory -> most specific GLEIF class, plus materialized supertypes.
CATEGORY_CLASS = {
    "GENERAL":                    [L1 + "LegalEntity"],
    "FUND":                       [L1 + "Fund"],
    "SOLE_PROPRIETOR":            [L1 + "SoleProprietor"],
    "BRANCH":                     [L1 + "Branch"],
    "RESIDENT_GOVERNMENT_ENTITY": [L1 + "LegalEntity"],
    "INTERNATIONAL_ORGANIZATION": [L1 + "LegalEntity"],
    "":                           [L1 + "LegalEntity"],
}
ENTITY_SUPERTYPES = [L1 + "RegisteredEntity", BASE + "Entity"]

# RR RelationshipType -> (most specific class, [materialized supertypes], shortcut predicate)
#
# The shortcut predicate is a MEASURED NECESSITY, not a convenience. GLEIF models
# relationships as reified records, so transitive traversal must be written
#     ?e (^gleif-L2:hasChild/gleif-L2:hasParent)+ ?ancestor
# which took 58.2s for a single 2-row ancestor walk at 69M triples -- unusable in
# an evaluation loop. The equivalent over a direct predicate,
#     ?e kgx:hasDirectConsolidationParent+ ?ancestor
# returns the identical rows in <0.01s. We therefore emit BOTH: the faithful
# reified record (GLEIF's real semantics) and a denormalized shortcut edge.
REL_CLASS = {
    "IS_DIRECTLY_CONSOLIDATED_BY":   (L2 + "DirectConsolidation",
                                      [L2 + "AccountingConsolidation", BASE + "LegalEntityRelationship"],
                                      KGX + "hasDirectConsolidationParent"),
    "IS_ULTIMATELY_CONSOLIDATED_BY": (L2 + "UltimateConsolidation",
                                      [L2 + "AccountingConsolidation", BASE + "LegalEntityRelationship"],
                                      KGX + "hasUltimateConsolidationParent"),
    "IS_INTERNATIONAL_BRANCH_OF":    (L2 + "InternationalBranchRelationship",
                                      [BASE + "LegalEntityRelationship"],
                                      KGX + "hasBranchParent"),
    # --- documented extension: absent from GLEIF's published L2 ontology ---
    "IS_FUND-MANAGED_BY":            (KGX + "FundManagement",      [BASE + "LegalEntityRelationship"],
                                      KGX + "hasFundManager"),
    "IS_SUBFUND_OF":                 (KGX + "SubFundRelationship", [BASE + "LegalEntityRelationship"],
                                      KGX + "hasUmbrellaFund"),
    "IS_FEEDER_TO":                  (KGX + "FeederRelationship",  [BASE + "LegalEntityRelationship"],
                                      KGX + "hasMasterFund"),
}


def emit_extension_ontology(w) -> None:
    """Declare the three minted fund-relationship classes so the graph is self-describing."""
    for cls, label, defn in [
        (KGX + "FundManagement", "fund management relationship",
         "The fund is managed by the target entity (RR-CDF IS_FUND-MANAGED_BY). "
         "Minted here: GLEIF's published L2 ontology defines no class for this relationship type."),
        (KGX + "SubFundRelationship", "sub-fund relationship",
         "The fund is a sub-fund of the target umbrella fund (RR-CDF IS_SUBFUND_OF). Minted here."),
        (KGX + "FeederRelationship", "feeder relationship",
         "The fund is a feeder into the target master fund (RR-CDF IS_FEEDER_TO). Minted here."),
    ]:
        w(f"{iri(cls)} {iri(RDFT+'type')} {iri('http://www.w3.org/2002/07/owl#Class')} .\n")
        w(f"{iri(cls)} {iri(RDFS+'subClassOf')} {iri(BASE+'LegalEntityRelationship')} .\n")
        w(f"{iri(cls)} {iri(RDFS+'label')} {lit(label)} .\n")
        w(f"{iri(cls)} {iri('http://www.w3.org/2004/02/skos/core#definition')} {lit(defn)} .\n")

    # Denormalized shortcut predicates (entity -> entity), mirroring each reified
    # relationship class. Declared so the graph documents its own denormalization.
    for prop, label, defn in [
        (KGX + "hasDirectConsolidationParent", "has direct consolidation parent",
         "Shortcut for the reified L2:DirectConsolidation record. Transitive traversal over "
         "the reified form is prohibitively slow; this edge makes it tractable."),
        (KGX + "hasUltimateConsolidationParent", "has ultimate consolidation parent",
         "Shortcut for the reified L2:UltimateConsolidation record. Note GLEIF records the "
         "ultimate parent directly, so this is normally a single hop, not a closure."),
        (KGX + "hasBranchParent", "has branch parent",
         "Shortcut for the reified L2:InternationalBranchRelationship record."),
        (KGX + "hasFundManager", "has fund manager",
         "Shortcut for the minted FundManagement relationship (RR-CDF IS_FUND-MANAGED_BY)."),
        (KGX + "hasUmbrellaFund", "has umbrella fund",
         "Shortcut for the minted SubFundRelationship (RR-CDF IS_SUBFUND_OF)."),
        (KGX + "hasMasterFund", "has master fund",
         "Shortcut for the minted FeederRelationship (RR-CDF IS_FEEDER_TO)."),
    ]:
        w(f"{iri(prop)} {iri(RDFT+'type')} {iri('http://www.w3.org/2002/07/owl#ObjectProperty')} .\n")
        w(f"{iri(prop)} {iri(RDFS+'label')} {lit(label)} .\n")
        w(f"{iri(prop)} {iri('http://www.w3.org/2004/02/skos/core#definition')} {lit(defn)} .\n")


def load_elf_labels(schema_dir: str) -> dict[str, str]:
    """ELF code -> English legal-form name, from the ISO 20275 code list."""
    out: dict[str, str] = {}
    files = glob.glob(os.path.join(schema_dir, "codelists", "*elf-code-list*.csv"))
    if not files:
        return out
    with open(files[-1], newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("ELF Code") or "").strip()
            if not code:
                continue
            name = ((row.get("Entity Legal Form name Local name") or "").strip()
                    or (row.get("Entity Legal Form name Transliterated name (per ISO 01-140-10)") or "").strip())
            if code not in out and name:
                out[code] = name
    return out


def convert_l1(path, w, elf_labels, limit=None, only_issued=False):
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {name: i for i, name in enumerate(hdr)}
        need = ["LEI", "Entity.LegalName", "Entity.LegalJurisdiction", "Entity.EntityCategory",
                "Entity.EntityStatus", "Entity.LegalForm.EntityLegalFormCode",
                "Registration.RegistrationStatus", "Registration.InitialRegistrationDate",
                "Registration.LastUpdateDate", "Entity.LegalAddress.City",
                "Entity.LegalAddress.Country", "Entity.EntityCategory"]
        miss = [c for c in need if c not in ix]
        if miss:
            print(f"  !! missing L1 columns (skipping those): {miss}", file=sys.stderr)
        n = 0
        jurisdictions, elfs = set(), set()
        for row in r:
            lei = row[ix["LEI"]]
            if not lei:
                continue
            if only_issued and row[ix["Registration.RegistrationStatus"]] != "ISSUED":
                continue
            s = iri(D_LEI + lei)
            cat = row[ix["Entity.EntityCategory"]] if "Entity.EntityCategory" in ix else ""
            for cls in CATEGORY_CLASS.get(cat, CATEGORY_CLASS[""]) + ENTITY_SUPERTYPES:
                w(f"{s} {iri(RDFT+'type')} {iri(cls)} .\n")
            w(f"{s} {iri(L1+'LEI')} {lit(lei)} .\n")

            def put(col, pred, as_iri=None):
                if col not in ix:
                    return None
                v = row[ix[col]].strip()
                if not v:
                    return None
                w(f"{s} {iri(pred)} {iri(as_iri+v) if as_iri else lit(v)} .\n")
                return v

            put("Entity.LegalName", L1 + "hasLegalName")
            put("Entity.LegalAddress.City", BASE + "hasCity")
            j = put("Entity.LegalJurisdiction", BASE + "hasLegalJurisdiction", D_JUR)
            if j:
                jurisdictions.add(j)
            e = put("Entity.LegalForm.EntityLegalFormCode", L1 + "hasLegalForm", D_ELF)
            if e:
                elfs.add(e)
            for col, pred, dt in [
                ("Registration.InitialRegistrationDate", BASE + "hasInitialRegistrationDate", XSD + "dateTime"),
                ("Registration.LastUpdateDate", BASE + "hasLastUpdateDate", XSD + "dateTime"),
            ]:
                if col in ix and row[ix[col]].strip():
                    w(f"{s} {iri(pred)} {lit(row[ix[col]].strip(), dt)} .\n")
            for col, pred, ns in [
                ("Entity.EntityStatus", BASE + "hasEntityStatus", BASE),
                ("Registration.RegistrationStatus", BASE + "hasRegistrationStatus", L1),
            ]:
                if col in ix and row[ix[col]].strip():
                    w(f"{s} {iri(pred)} {iri(ns + row[ix[col]].strip())} .\n")
            n += 1
            if limit and n >= limit:
                break

    for j in jurisdictions:
        w(f"{iri(D_JUR+j)} {iri(RDFT+'type')} {iri(LCC+'GeographicRegion')} .\n")
        w(f"{iri(D_JUR+j)} {iri(RDFS+'label')} {lit(j)} .\n")
    for e in elfs:
        w(f"{iri(D_ELF+e)} {iri(RDFT+'type')} {iri(ELF+'EntityLegalForm')} .\n")
        lbl = elf_labels.get(e)
        w(f"{iri(D_ELF+e)} {iri(RDFS+'label')} {lit(lbl if lbl else e)} .\n")
    return n


def convert_rr(path, w, limit=None):
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            child = row["Relationship.StartNode.NodeID"]
            parent = row["Relationship.EndNode.NodeID"]
            rtype = row["Relationship.RelationshipType"]
            if not (child and parent and rtype in REL_CLASS):
                continue
            cls, supers, shortcut = REL_CLASS[rtype]
            rid = f"{child}-{rtype}-{parent}"
            s = iri(D_REL + rid)
            for c in [cls] + supers:
                w(f"{s} {iri(RDFT+'type')} {iri(c)} .\n")
            w(f"{s} {iri(L2+'hasChild')} {iri(D_LEI+child)} .\n")
            w(f"{s} {iri(L2+'hasParent')} {iri(D_LEI+parent)} .\n")
            # denormalized shortcut: child -> parent directly (see REL_CLASS note)
            w(f"{iri(D_LEI+child)} {iri(shortcut)} {iri(D_LEI+parent)} .\n")
            # generic source/target aliases, also GLEIF vocabulary
            w(f"{s} {iri(BASE+'hasSource')} {iri(D_LEI+child)} .\n")
            w(f"{s} {iri(BASE+'hasTarget')} {iri(D_LEI+parent)} .\n")
            st = row.get("Relationship.RelationshipStatus", "").strip()
            if st:
                w(f"{s} {iri(L2+'hasRelationshipStatus')} {iri(L2+st)} .\n")
            n += 1
            if limit and n >= limit:
                break
    return n


def convert_repex(path, w, limit=None):
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            lei = row["LEI"]
            cat = row.get("Exception.Category", "").strip()
            if not (lei and cat):
                continue
            cls = (RE + "DirectConsolidationReportingException"
                   if cat.startswith("DIRECT") else RE + "UltimateConsolidationReportingException")
            s = iri(D_EXC + f"{lei}-{cat}")
            w(f"{s} {iri(RDFT+'type')} {iri(cls)} .\n")
            w(f"{s} {iri(RDFT+'type')} {iri(RE+'ReportingException')} .\n")
            w(f"{s} {iri(RE+'hasReportingEntity')} {iri(D_LEI+lei)} .\n")
            for i in range(1, 6):
                v = (row.get(f"Exception.Reason.{i}") or "").strip()
                if v:
                    w(f"{s} {iri(RE+'hasExceptionReason')} {iri(RE+v)} .\n")
            ref = (row.get("Exception.Reference.1") or "").strip()
            if ref:
                w(f"{s} {iri(RE+'hasExceptionReference')} {lit(ref)} .\n")
            n += 1
            if limit and n >= limit:
                break
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--schema-dir", default="schema")
    ap.add_argument("--out", default="build/gleif.nt")
    ap.add_argument("--limit", type=int, default=None, help="max rows per dataset (smoke tests)")
    ap.add_argument("--only-issued", action="store_true", help="restrict L1 to ISSUED entities")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    pick = lambda pat: glob.glob(os.path.join(a.data_dir, pat))[0]
    lei_f, rr_f, repex_f = pick("*-lei2-golden-copy.csv"), pick("*-rr-golden-copy.csv"), pick("*-repex-golden-copy.csv")
    elf_labels = load_elf_labels(a.schema_dir)
    print(f"ELF labels loaded: {len(elf_labels):,}")

    t0 = time.time()
    with open(a.out, "w", encoding="utf-8", newline="") as out:
        w = out.write
        emit_extension_ontology(w)
        n1 = convert_l1(lei_f, w, elf_labels, a.limit, a.only_issued)
        print(f"  L1    {n1:>9,} entities      [{time.time()-t0:.0f}s]")
        n2 = convert_rr(rr_f, w, a.limit)
        print(f"  RR    {n2:>9,} relationships [{time.time()-t0:.0f}s]")
        n3 = convert_repex(repex_f, w, a.limit)
        print(f"  REPEX {n3:>9,} exceptions    [{time.time()-t0:.0f}s]")

    size = os.path.getsize(a.out)
    with open(a.out, encoding="utf-8") as fh:
        triples = sum(1 for _ in fh)
    print(f"\nwrote {a.out}  {size/2**30:.2f} GiB  {triples:,} triples  [{time.time()-t0:.0f}s]")


if __name__ == "__main__":
    main()
