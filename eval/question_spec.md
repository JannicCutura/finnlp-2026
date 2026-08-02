# Question-authoring spec (neutral, written before instantiation)

**Purpose.** Fix *what an analyst or supervisor would actually ask* about legal-entity
reference data, independently of what our graph makes convenient. Question TYPES come
from this document. Only afterwards are they instantiated with concrete entities drawn
from the snapshot.

This ordering is the guard against the obvious methodological failure: authoring
questions that flatter the traversal we happen to have built.

## Who is asking

Three realistic personas, all working from a name or an identifier:

1. **KYC / onboarding analyst** — "who is this counterparty, is the record current,
   where is it incorporated, what legal form, who ultimately stands behind it."
2. **Prudential supervisor** — "what sits under this group, how many entities in this
   jurisdiction, which of our reporting population is stale."
3. **Fund operations / market data** — "who manages this fund, what umbrella does this
   sub-fund belong to, which funds does this manager run."

## Question families (independent of our schema)

| Family | Analyst intent | Example phrasing (generic) |
|---|---|---|
| **F1 Identity** | resolve an identifier to an entity | "What is the legal name of LEI …?" |
| **F2 Attributes** | describe one entity | "Where is X incorporated? What legal form? Is the record current?" |
| **F3 Reverse lookup** | resolve a name to an identifier | "What is the LEI of the entity called …?" |
| **F4 Upward relation** | who stands above this entity | "Who is X's parent / ultimate parent / fund manager?" |
| **F5 Downward relation** | what sits beneath this entity | "Which entities does X consolidate? Which funds does X manage?" |
| **F6 Chained relation** | multi-step structure | "Who manages the umbrella of this sub-fund?" "Trace the chain above X." |
| **F7 Population filter** | subset by attribute | "Entities in jurisdiction J with legal form L." |
| **F8 Counting / ranking** | aggregate over a population | "How many …? Which jurisdictions have the most …?" |
| **F9 Absence with reason** | why is something not reported | "Does X report a parent? If not, why not?" |
| **F10 Out-of-scope** | information the register does not hold | "What % of X is owned by Y?" "What is X's revenue?" |
| **F11 Non-existent** | identifier or name not in the register | "What is the legal name of LEI <invalid>?" |

## Mapping to evaluation tiers

| Tier | Families | Target n |
|---|---|--:|
| a — lookup | F1, F2, F3 | 10 |
| b — single-hop | F4, F5 | 10 |
| c — multi-hop | F6 | 8 |
| d — attribute filter | F7 | 7 |
| e — aggregate | F8 | 7 |
| f — abstention | F9 (f1), F10 (f2), F11 (f3) | 12 |
| | | **54** |

## Balance rule (important)

Tiers are balanced **by evaluation value, not by frequency in the register**. 95.6% of
entities carry only a reporting exception; sampling proportionally would make
"there is no parent" correct for almost every relational question, and a model that
always abstains would score well. Abstention is therefore capped at ~22% of items, and
tier f deliberately mixes three *different* reasons for emptiness (recorded exception /
out-of-scope / non-existent) so that indiscriminate abstention cannot succeed: f1 items
require the *reason* to be returned, not merely an empty result.

## Authoring rules

1. Phrase questions in business language. Never name a predicate, class or IRI.
2. Use entity **names** as often as identifiers — real users have both.
3. Every gold query is **verified by execution** against snapshot `20260723-1600`.
4. Gold answers are the reference query's result set, not a hand-written string.
5. Include entities that are **LAPSED/RETIRED**, not only ISSUED — a correct answer
   about a lapsed entity is still a correct answer.
6. Few-shot exemplars for the prompt are drawn from a **disjoint pool**
   (`eval/fewshot.json`), never from this set, to avoid test leakage.
7. Expected-empty items are marked `"expect_empty": true` so the scorer can apply the
   QALD empty-answer convention rather than treating empty as failure.
