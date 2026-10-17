# Provenance and third-party notices

## Project code

No blanket open-source software license has been assigned to this repository. Public availability does not replace the applicable ownership and component license terms.

`wgforge/artifacts.py` and `wgforge/safeformula.py` adapt the native artifact IO and bounded formula evaluator from the user's existing `briefcase_forge/briefcase_forge/` directory. The provider ledger/path-broker implementation in `wgforge/runtime.py` and `wgforge/sandbox.py` also reuses and extends design/code from that prior pipeline. The previous directory was preserved. No standalone original license notice was present in the provided donor package; any pre-existing ownership or license obligations still apply. This notice does not grant rights the project owner does not hold.

The other new scenario contracts, occupational planning, source discovery/selection, campaign registry, independent audit/repair orchestration, dataset gates, evaluation checks, training adapters, tests, and documentation implement the user's requested upgrade. Methodological inspiration is credited below; it does not transfer ownership or licenses from the cited paper/model to this code.

## WorkGenesis research

Method reference: WorkGenesis, arXiv:2609.39325v1, https://arxiv.org/html/2609.39325v1.

The paper's raw HTML/PDF is not included. `wgforge/data/paper_prompts/` contains Appendix D prompt transcriptions extracted from the supplied paper, with page ranges and hashes in `source.json`; formatting and execution bindings differ. These texts are attributed to WorkGenesis and retain their upstream terms. The paper's distribution license is not represented as a permissive software/prompt license. Legacy independently written role prompts also remain. `docs/REPRODUCTION_CONTRACT.md` records implementation differences.

The released reference model https://huggingface.co/endless-frontier/Fx-Work declares Apache-2.0. No model weights are bundled, and that model declaration is not treated as a license for this project's generated scenarios, arbitrary public anchors, or the undisclosed training corpus.

The `paper` optional dependency installs official Stirrup at revision `247f24d56b2108235880ed2a2baea5d35b5a67ee`; its source is not vendored. Current paper profiles use Stirrup for the outer agent loop and sandbox tools. Codex/Claude subscription profiles add a serialized-history/action bridge. The older bounded runner is retained separately. Neither the selected revision nor the reconstructed Docker environment is claimed to be the paper's unpublished original pin/image.

## USGS numerical source snapshot

The package includes an exact 38,105-byte USGS earthquake catalog CSV snapshot. Publisher: U.S. Geological Survey, Earthquake Hazards Program. Query: catalog=us, October 1–11, 2026, ordered ascending, limit 200. Retrieval date and exact URL/SHA-256 are in `examples/anchors/usgs_manifest.json` and the installed copy under `wgforge/data/anchors/`.

Official policy: https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits. USGS-authored/produced information is generally in the United States public domain, subject to third-party exceptions. This snapshot's rights basis is scoped to USGS-produced numerical records, with every `net` field checked as `us`; it includes no third-party media or agency logos. Attribution is retained. A public-domain conclusion for these reviewed bytes does not automatically cover another download or imply USGS endorsement. Source values can be revised after retrieval.

## O*NET database

The bundled official O*NET 30.0 database excerpts/full source tables and license text are covered by the applicable O*NET Database CC BY 4.0 terms. Attribution: O*NET Resource Center, U.S. Department of Labor/Employment and Training Administration, https://www.onetcenter.org/database.html. License: https://www.onetcenter.org/license_db.html and https://creativecommons.org/licenses/by/4.0/.

Exact download URLs, timestamps, byte hashes, and scoped permissions are in `examples/anchors/onet_manifest.json`. Raw public files are unchanged. The compact occupational activity inventory is an explicitly curated derivative; its selected rows retain source line/hash references. Deliverable-family mappings, sampling choices, exclusions, and repeated seeded instances are this project's additions, not O*NET assertions. No endorsement is implied.

The paper's 65 occupational labels are factual metadata extracted and cited in `configs/paper_occupations.json`. That file records explicit O*NET mappings, including aliases and a broader buyer label mapped to multiple codes. Missing activity rows remain disclosed. The older curated demo inventory is separate from the paper-profile occupation inventory.

## Dependency licenses

The Python and optional GPU/tokenizer libraries retain their own upstream licenses. Dependency installation is separate; consult the installed distributions and official repositories for full notices. No package license is inferred from the paper or model. The source manifests' explicit training, redistribution, and public-access permissions are separate from software dependency licensing.
