# GLEIF Knowledge Graph — SPARQL schema

Snapshot: GLEIF Golden Copy publish `20260723-1600`. Every class, property and value below is present in the graph with the stated count.


## Prefixes

```sparql
PREFIX gleif-L1: <https://www.gleif.org/ontology/L1/>
PREFIX gleif-L2: <https://www.gleif.org/ontology/L2/>
PREFIX gleif-base: <https://www.gleif.org/ontology/Base/>
PREFIX gleif-elf: <https://www.gleif.org/ontology/EntityLegalForm/>
PREFIX gleif-ra: <https://www.gleif.org/ontology/RegistrationAuthority/>
PREFIX gleif-re: <https://www.gleif.org/ontology/ReportingException/>
PREFIX kgx: <https://example.org/gleif-kg/ontology/>
PREFIX lei: <https://www.gleif.org/data/lei/>
PREFIX jur: <https://www.gleif.org/data/jurisdiction/>
PREFIX elf: <https://www.gleif.org/data/elf/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
```


## Node shapes

- **Entities** are IRIs `lei:{LEI}`, e.g. `lei:001GPB6A9XPE8XJICC14`. The LEI is also a literal via `gleif-L1:LEI`.
- **Relationships are REIFIED**: a relationship record node links to both endpoints via `gleif-L2:hasChild` / `gleif-L2:hasParent`. For traversal, prefer the direct `kgx:has*` shortcut edges below, which connect entity to entity and support `+` closure.
- **Reporting exceptions** are nodes stating why an entity has NO parent; they link to the entity via `gleif-re:hasReportingEntity`.
- Jurisdictions are IRIs `jur:{ISO-3166 code}` (e.g. `jur:DE` for Germany, `jur:US`). Their `rdfs:label` is the **code itself**, not the country name — match on the IRI.
- Legal forms are IRIs `elf:{ELF code}` whose `rdfs:label` IS human-readable (e.g. `elf:6QQB` → "Gesellschaft mit beschränkter Haftung").


## Relationship direction — get this right

Every relationship edge points **from the subordinate entity to the superior one**. The subject is always the child/fund/branch; the object is always the parent/manager/umbrella.

```sparql
?child    kgx:hasDirectConsolidationParent    ?parent      # child -> its parent
?child    kgx:hasUltimateConsolidationParent  ?parent      # child -> its ultimate parent
?fund     kgx:hasFundManager                  ?manager     # fund -> managing entity
?subfund  kgx:hasUmbrellaFund                 ?umbrella    # sub-fund -> umbrella fund
?feeder   kgx:hasMasterFund                   ?master      # feeder fund -> master fund
?branch   kgx:hasBranchParent                 ?headOffice  # branch -> head office
```

Consequently:

```sparql
# ANCESTORS of X (X is the subject):
lei:X kgx:hasDirectConsolidationParent+ ?ancestor .

# DESCENDANTS / subsidiaries of X (X is the object):
?descendant kgx:hasDirectConsolidationParent+ lei:X .
```

Writing these backwards produces a valid query that silently returns nothing.


## Expressing "the register cannot answer this"

If the graph does not hold the requested information (e.g. shareholding percentages, revenue, employee counts — none of which the LEI register records), return a query that provably yields **zero rows**:

```sparql
SELECT ?x WHERE { FILTER(false) }
```
Do **not** write an empty group pattern `WHERE { }`. In SPARQL that matches the empty solution mapping and returns **one** row with unbound variables, not zero. Likewise, do not match an entity and simply omit the missing property — that also returns a row.


## Notes that matter for correctness

- GLEIF Level 2 is **accounting consolidation** (IFRS 10), not equity ownership. There are **no shareholding percentages** in this graph.
- The **ultimate** parent is recorded directly as its own edge (`kgx:hasUltimateConsolidationParent`); it does **not** need transitive traversal.
- Most entities have **no** parent: they carry a reporting exception instead. A question about a parent may correctly have an empty answer.
- **Do NOT filter by registration status unless the question explicitly asks for current / active / live entities.** Most questions do not. Many valid answers are `LAPSED` or `RETIRED` entities, and adding `gleif-L1:ISSUED` would wrongly exclude them. Only when the question does ask, add `?e gleif-base:hasRegistrationStatus gleif-L1:ISSUED`.
- Legal names are plain literals with **no language tag**. Match them as `"Some Name"`, never `"Some Name"@en`.
