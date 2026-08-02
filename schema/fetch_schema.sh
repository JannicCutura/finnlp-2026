#!/usr/bin/env bash
# Fetch the GLEIF semantic layer: RDF ontology modules + reference code lists.
#
# These define the CLASSES and PREDICATES that generated SPARQL must target, and
# supply the schema text injected into the model prompt. All are small (~1.5 MB
# total) and ARE machine-downloadable -- unlike the bulk golden-copy data, whose
# hosts (leidata/goldencopy.gleif.org) 403 every non-browser client and must be
# downloaded by hand (see data/unzip_gleif.sh).
#
# Ontology modules are served from their namespace IRI by content negotiation
# (303 -> text/turtle), so the namespace URL *is* the stable download URL.
#
# Code-list URLs embed a version + date, so the newest is DISCOVERED by scraping
# the code-list page rather than hardcoded (hardcoded links go stale).
#
# Usage: bash fetch_schema.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ONT="$HERE/ontology"
CL="$HERE/codelists"
mkdir -p "$ONT" "$CL"
rc=0

echo "== GLEIF ontology modules (Turtle) =="
for m in Base L1 L2 EntityLegalForm RegistrationAuthority ReportingException; do
  out="$ONT/$m.ttl"
  if curl -fsSL -m 60 -H "Accept: text/turtle" -o "$out" "https://www.gleif.org/ontology/$m/"; then
    printf "  %-26s %8s bytes\n" "$m.ttl" "$(stat -c%s "$out")"
  else
    echo "  $m FAILED"; rc=1
  fi
done

# Scrape the newest (non-changelog) CSV from a GLEIF code-list page.
grab_codelist () {
  local page="$1" pat="$2" excl="$3" label="$4" url out
  url=$(curl -sL -m 60 "$page" \
        | grep -oiE "href=\"[^\"]*${pat}[^\"]*\.csv\"" \
        | sed -E 's/^href="//; s/"$//' \
        | grep -viE "$excl" | sort -u | tail -1)
  if [[ -z "$url" ]]; then echo "  $label: no CSV link found on $page"; return 1; fi
  out="$CL/$(basename "$url")"
  if curl -fsSL -m 120 -o "$out" "$url"; then
    printf "  %-42s %8s bytes\n" "$(basename "$out")" "$(stat -c%s "$out")"
  else
    echo "  $label FAILED"; return 1
  fi
}

echo "== reference code lists (newest version, discovered) =="
grab_codelist "https://www.gleif.org/en/about-lei/code-lists/iso-20275-entity-legal-forms-code-list" \
              "elf-code-list" "changes-from|changelog" "ELF" || rc=1
grab_codelist "https://www.gleif.org/en/about-lei/code-lists/gleif-registration-authorities-list" \
              "ra-list" "changelog" "RA" || rc=1

echo "=========="
find "$ONT" "$CL" -type f | sort
[[ $rc -eq 0 ]] && echo "ALL DONE" || echo "COMPLETED WITH ERRORS (rc=$rc)"
exit $rc
