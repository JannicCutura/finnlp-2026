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


## Classes

| class | instances | meaning |
|---|--:|---|
| `gleif-re:ReportingException` | 6,253,913 | A single exception to a specified reporting requirement, giving reasons and references where applicable. |
| `gleif-L1:RegisteredEntity` | 3,382,301 | LEI-registered entities including, but not limited to, unique parties that are legally or financially responsi |
| `gleif-base:Entity` | 3,382,301 | A partnership, corporation, or other organization having the capacity to negotiate contracts, assume financial |
| `gleif-re:DirectConsolidationReportingException` | 3,130,557 | The legal entity has declined to report a direct accounting consolidation parent, based on applicable accounti |
| `gleif-re:UltimateConsolidationReportingException` | 3,123,356 | The legal entity has declined to report an ultimate accounting consolidation parent, based on applicable accou |
| `gleif-L1:LegalEntity` | 3,011,791 | LEI-registered entities that are legally or financially responsible for the performance of financial transacti |
| `gleif-base:LegalEntityRelationship` | 481,557 | abstract superclass to represent a reified directed relationship |
| `gleif-L2:AccountingConsolidation` | 257,652 | Accounting consolidation holds when '[in the] financial statements of a group [...] the assets, liabilities, e |
| `gleif-L1:Fund` | 246,235 | The legal entity is a fund. |
| `kgx:FundManagement` | 148,117 | The fund is managed by the target entity (RR-CDF IS_FUND-MANAGED_BY). Minted here: GLEIF's published L2 ontolo |
| `gleif-L2:UltimateConsolidation` | 131,923 | The 'child' entity has its accounts fully consolidated by the 'parent' entity, in the sense given by the accou |
| `gleif-L2:DirectConsolidation` | 125,729 | The 'child' entity has its accounts fully consolidated by the 'parent' entity, in the sense given by the accou |
| `gleif-L1:SoleProprietor` | 122,391 | The legal entity represents an individual acting in a business capacity |
| `kgx:SubFundRelationship` | 72,463 | The fund is a sub-fund of the target umbrella fund (RR-CDF IS_SUBFUND_OF). Minted here. |
| `gleif-elf:EntityLegalForm` | 2,475 | The legal form of the entity, taken from the ISO Entity Legal Form (ELF) code list maintained by GLEIF.  |
| `gleif-L2:InternationalBranchRelationship` | 1,939 | Child is a lead international branch or international branch network outside of the head office’s jurisdiction |
| `gleif-L1:Branch` | 1,884 | The entity is a branch of another legal entity. |
| `kgx:FeederRelationship` | 1,386 | The fund is a feeder into the target master fund (RR-CDF IS_FEEDER_TO). Minted here. |
| `<https://www.omg.org/spec/LCC/Countries/CountryRepresentation/GeographicRegion>` | 307 |  |

## Properties

| property | triples | meaning |
|---|--:|---|
| `gleif-re:hasExceptionReason` | 6,268,193 | A single reason provided by the legal entity for declining to provide the mandatory report of a specified type |
| `gleif-re:hasReportingEntity` | 6,253,913 | The LEI-registered legal entity which raised this exception. |
| `gleif-base:hasInitialRegistrationDate` | 3,382,301 | The date on which an identifier or other registered item was first registered. |
| `gleif-base:hasEntityStatus` | 3,382,301 | Indicates the status of the entity (i.e., active, inactive). |
| `gleif-L1:LEI` | 3,382,301 | The ISO 17442 compatible identifier for the legal entity recorded. |
| `gleif-base:hasCity` | 3,382,301 | The mandatory name of the city. |
| `gleif-L1:hasLegalName` | 3,382,301 | The legal name of the entity. |
| `gleif-base:hasLastUpdateDate` | 3,382,301 | The date that the detail of a specific registration in the registry was last revised. |
| `gleif-base:hasRegistrationStatus` | 3,382,301 | indicates the status of a specific registration, such as for an identifier or license |
| `gleif-L1:hasLegalForm` | 3,382,301 | The legal form of the entity, taken from the ISO 20275 Entity Legal Form (ELF) data set maintained by GLEIF. |
| `gleif-base:hasLegalJurisdiction` | 3,382,297 | The jurisdiction of legal formation and registration of the entity (and upon which the LegalForm data element  |
| `gleif-base:hasTarget` | 481,557 | The entity that plays the target of the directed relationship. |
| `gleif-L2:hasChild` | 481,557 | The entity that plays the child role. |
| `gleif-L2:hasParent` | 481,557 | The entity that plays the parent role. |
| `gleif-base:hasSource` | 481,557 | The entity that is the source of the directed relationship. |
| `gleif-L2:hasRelationshipStatus` | 481,557 | Indicates the status of the relationship (i.e., active, inactive). |
| `kgx:hasFundManager` | 148,117 | Shortcut for the minted FundManagement relationship (RR-CDF IS_FUND-MANAGED_BY). |
| `kgx:hasUltimateConsolidationParent` | 131,923 | Shortcut for the reified L2:UltimateConsolidation record. Note GLEIF records the ultimate parent directly, so  |
| `kgx:hasDirectConsolidationParent` | 125,729 | Shortcut for the reified L2:DirectConsolidation record. Transitive traversal over the reified form is prohibit |
| `kgx:hasUmbrellaFund` | 72,463 | Shortcut for the minted SubFundRelationship (RR-CDF IS_SUBFUND_OF). |
| `gleif-re:hasExceptionReference` | 2,870 | References of the law, regulation or other element of the legal framework to support reason(s) provided by the |
| `rdfs:label` | 2,791 |  |
| `kgx:hasBranchParent` | 1,939 | Shortcut for the reified L2:InternationalBranchRelationship record. |
| `kgx:hasMasterFund` | 1,386 | Shortcut for the minted FeederRelationship (RR-CDF IS_FEEDER_TO). |
| `<http://www.w3.org/2004/02/skos/core#definition>` | 9 |  |
| `rdfs:subClassOf` | 3 |  |

## Controlled values

- **Registration status:** `gleif-L1:ISSUED` (1,940,941), `gleif-L1:LAPSED` (1,185,130), `gleif-L1:RETIRED` (247,276), `gleif-L1:DUPLICATE` (6,461), `gleif-L1:ANNULLED` (1,660), `gleif-L1:PENDING_TRANSFER` (544), `gleif-L1:PENDING_ARCHIVAL` (289)
- **Entity status:** `gleif-base:ACTIVE` (3,126,464), `gleif-base:INACTIVE` (246,866), `gleif-base:NULL` (8,971)
- **Relationship status:** `gleif-L2:ACTIVE` (481,161), `gleif-L2:NULL` (336), `gleif-L2:INACTIVE` (60)
- **Exception reason:** `gleif-re:NATURAL_PERSONS` (2,303,504), `gleif-re:NON_CONSOLIDATING` (2,178,410), `gleif-re:NO_KNOWN_PERSON` (1,327,098), `gleif-re:NO_LEI` (239,097), `gleif-re:NON_PUBLIC` (216,076), `gleif-re:CONSENT_NOT_OBTAINED` (2,774), `gleif-re:BINDING_LEGAL_COMMITMENTS` (368), `gleif-re:DETRIMENT_NOT_EXCLUDED` (329), `gleif-re:DISCLOSURE_DETRIMENTAL` (304), `gleif-re:LEGAL_OBSTACLES` (233)

## Notes that matter for correctness

- GLEIF Level 2 is **accounting consolidation** (IFRS 10), not equity ownership. There are **no shareholding percentages** in this graph.
- The **ultimate** parent is recorded directly as its own edge (`kgx:hasUltimateConsolidationParent`); it does **not** need transitive traversal.
- Most entities have **no** parent: they carry a reporting exception instead. A question about a parent may correctly have an empty answer.
- **Do NOT filter by registration status unless the question explicitly asks for current / active / live entities.** Most questions do not. Many valid answers are `LAPSED` or `RETIRED` entities, and adding `gleif-L1:ISSUED` would wrongly exclude them. Only when the question does ask, add `?e gleif-base:hasRegistrationStatus gleif-L1:ISSUED`.
- Legal names are plain literals with **no language tag**. Match them as `"Some Name"`, never `"Some Name"@en`.
