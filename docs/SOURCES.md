# Sources, rights, and occupation planning

## What is real in this repository

`examples/anchors/usgs_manifest.json` describes a **real HTTPS retrieval**, not generated demo data. Its immutable raw CSV contains the first 200 events returned by the official USGS catalog query for October 1–11, 2026, ordered by time and limited to 200. Every returned `net` is `us`. The saved response has 38,105 bytes and SHA-256 `4382a1162b5f63c3afcc6eb29b71d2198d6d4793fb07b0efbfde395ed31acd04`. It is a bounded pilot, **not a complete ten-day earthquake census**. The source can revise future responses; the saved bytes remain the reproducibility anchor.

- Data: https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&starttime=2026-10-01&endtime=2026-10-11&catalog=us&orderby=time-asc&limit=200
- Service reference: https://earthquake.usgs.gov/fdsnws/event/1/
- Rights evidence: https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits

The rights assessment is scoped to USGS-produced numerical catalog data. USGS describes its own data as U.S. public domain and warns that third-party materials may differ. This pilot contains no third-party photographs, logos, or personal records. Credit U.S. Geological Survey, Earthquake Hazards Program. This assessment does not label every publicly accessible USGS asset reusable.

`examples/anchors/onet_manifest.json` records three real, unmodified O*NET 30.0 downloadable files: Occupation Data, Content Model Reference, and Work Activities. Their exact bytes, including original line endings, are stored under `raw/<sha256>.txt`. The Work Activities table contains 73,308 rows covering 894 occupation codes. The raw files were fetched through the environment-provided HTTPS egress proxy with TLS verification, HTTP-error rejection, HTTPS-only protocol, and explicit byte/time bounds. This one-time fixture retrieval is disclosed in each manifest; it is **not** a claim that the separately implemented pinned downloader was live exercised here.

The O*NET files are licensed CC BY 4.0. Credit: O*NET 30.0 Database, U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), https://www.onetcenter.org/database.html, https://creativecommons.org/licenses/by/4.0/. O*NET® is a trademark of USDOL/ETA. Raw source files are unchanged; activity selection and deliverable mappings are WorkGenesis Forge modifications, not approved, endorsed, or tested by USDOL/ETA. The official database license also covers earlier versions: https://www.onetcenter.org/license_db.html.

## Source APIs

- `discover_sources(query, config) -> list[dict]`: direct URL intake or genuine SerpAPI Google search.
- `download_source(record, out_dir, config) -> dict`: bounded download into read-only, content-addressed raw storage; supplied `record.path` is ignored.
- `load_source_manifest(path) -> dict`: validates every source's required fields and exact bytes.
- `verify_source_record(record, base) -> None`: integrity and provenance checks, without approving reuse.
- `require_production_rights(record) -> None`: rejects pending/denied, synthetic, or incompletely approved sources.

A downloaded source records `id`, `path`, origin `url`, `final_url`, `sha256`, `bytes`, `title`, `retrieved_at`, `synthetic`, `provenance_kind`, `license`, `rights_status`, and response metadata. `synthetic=false` and `provenance_kind=exact-public` mean saved original public bytes. Authored/extracted artifacts must remain distinct derivatives linked to source IDs and hashes. A SHA-256 proves byte integrity, not truth, authorship, or a license; manifests themselves are not cryptographically signed.

Public availability never establishes reuse rights. Discovery returns `rights_status=pending` and `license=unknown`. Pending sources may be cached and inspected. Production requires an identified license, concrete `rights_basis`, `rights_evidence`, `rights_status=approved`, and explicit `rights_permissions` booleans for **training**, **redistribution**, and **public_access**, all true. Operators must assess each scope and retain attribution/conditions. Setting a flag is an operator attestation, not automatic legal verification. No domain-wide permission inference is made.

## Direct intake and paid search

Settings may be nested under `sources` or passed directly:

```json
{
  "sources": {
    "adapter": "direct",
    "direct_urls": ["https://example.org/public.csv"],
    "allowed_hosts": ["example.org"],
    "max_bytes": 1000000,
    "timeout_seconds": 20,
    "max_redirects": 0,
    "allowed_mime_types": ["text/csv", "text/plain"],
    "max_results": 10
  }
}
```

For search, choose `adapter=serpapi`, explicitly set `enable_paid_search=true`, and provision the environment variable named by `serpapi_key_env` (default `SERPAPI_API_KEY`). This invokes the documented `https://serpapi.com/search.json?engine=google&q=...` API and parses `organic_results`. Only exact allowlisted source hosts survive discovery. The credential is sent only to the official TLS-verified, pinned SerpAPI endpoint; credential-bearing request URLs and provider error details are not persisted. **No paid search call was made during implementation or tests.** An existing key alone never enables paid search. SerpAPI reference: https://serpapi.com/search-api.

## Network and file boundaries

The runtime downloader requires HTTPS, exact explicit host allowlists, no embedded credentials/fragments, and port 443. Every DNS answer must be public; mixed public/private answers fail. Connections pin the validated numeric IP while preserving hostname TLS certificate verification. Redirects default to denied; explicitly permitted redirects repeat all protocol/host/DNS checks. Ambient proxies are deliberately ignored because proxy resolution can bypass pinning. In a proxy-only runtime, use pre-fetched verified manifests or a separately reviewed egress implementation rather than weakening these checks.

The downloader bounds DNS wait, total retrieval time (including a header/body watchdog), declared and streamed bytes, redirects, and MIME types. It requests identity encoding, rejects compressed transfers and empty/truncated data, sniffs HTML/error-page payloads, validates UTF-8/JSON and PDF/Office signatures, and fails closed on unknown content. It does not execute, render, or trust downloaded content as instructions. A matching Office signature is a container check, not macro/malware clearance; downstream extraction needs its own safe-parser limits.

Manifest verification rejects traversal, absolute paths, symlink components, hard links, FIFOs/special files, size/hash disagreement, contradictory provenance, and duplicate IDs. Content-addressed storage never overwrites an existing file. File mode is read-only as a guardrail; owner/admin edits remain detectable by verification. This is not a hostile-local-user filesystem sandbox.

## Planning and held-out boundaries

`plan_tasks(config) -> list[dict]` returns `profession`, `occupation_code`, `activity`, `activity_id`, `deliverable_family`, `seed`, `sampling_seed`, `plan_id`, `split`, and activity provenance. Default families are `spreadsheet`, `document`, and `presentation`.

The default `curated_smoke` adapter loads `examples/anchors/onet_curated_smoke.json`: 20 real activity/occupation linkages across five professions, expanded to 60 possible family slots. Every claimed activity is checked against its exact saved O*NET row and hash; profession titles are checked against Occupation Data. This deliberately small curated smoke fixture is not a workforce-distribution model. Family mappings are authored design choices, not O*NET claims.

For dynamic production planning, use:

```json
{
  "planning": {
    "adapter": "onet",
    "onet_version": "30.0",
    "onet_manifest_path": "examples/anchors/onet_manifest.json",
    "occupation_source_id": "onet30_occupations",
    "activity_source_id": "onet30_work_activities",
    "minimum_importance": 3.5,
    "count": 1000,
    "seed": 42,
    "deliverable_families": ["spreadsheet", "document"],
    "exclusions": {"occupation_codes": ["11-1011.00"]},
    "heldout": {"occupation_codes": ["19-2042.00"]},
    "heldout_fraction": 0.1,
    "split_seed": 100,
    "split": "train"
  }
}
```

The real O*NET adapter joins occupation/activity files, selects importance (`IM`) rows, honors suppression/not-relevant flags, and applies optional importance thresholds. There is no production profession whitelist. Without `onet_manifest_path`, `allow_network=true` explicitly enables fetching version-pinned official TSV files into `cache_dir`. Automatic O*NET rights attribution is confined to the official downloadable database URL prefix. Newer CSV files can be ingested through a verified manifest; version-specific file layout still needs to be configured correctly.

Alternatively supply `inventory` or `inventory_path` with arbitrary occupations and activity rows, optionally nested under `activities`. Custom inventories are labeled unverified `user_inventory`, never silently promoted to O*NET facts. Filters `occupation_codes` and `professions` restrict intake. Exclusions and held-out selectors support occupation codes, professions, activities, activity IDs, and deliverable families. Unknown selector keys fail. Fractional held-out assignment groups an occupation/activity pair across artifact families to avoid cross-family leakage; it is independent of sampling seed. Explicit family-only held-out selectors intentionally hold out only that family. Use `split=heldout` to sample held-out slots. Exclusions are applied before sampling. Supply shortages raise an error rather than duplicate slots. Stable ordering and seeded sampling make unchanged inventories reproducible.

The default planner prefers the repository `examples/anchors` directory, then installed package `wgforge/data/anchors` data. You can also set `curated_smoke_path`/`onet_manifest_path` explicitly. The planner does not fabricate a missing asset or silently switch to synthetic occupations.

## Verification run

`python -m unittest discover -s tests -p 'test_sources.py' -v` and `python -m unittest discover -s tests -p 'test_planning.py' -v` cover 39 offline tests at initial handoff. Tests mock the HTTP boundary and never call paid services. They validate the real pilot's bytes, full O*NET dynamic planning, seeded reproducibility, held-out separation, URL/DNS/redirect defenses, body/header bounds, MIME/HTML failures, integrity tampering, special-file rejection, and rights gating. Live source discovery and the pinned downloader require a suitable network environment and remain separate integration checks.
