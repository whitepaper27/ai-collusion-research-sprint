# Spec: Reference labels (adjudicator), factual checks, mixed-model pricing, final audit report

Module: src/ethwall/audit/reference.py (R-REF)
sol.md: §6 (coordination rubric, matched fixtures), §9 (spend caps), §10 (auditor experiment,
references, metrics)
Owner amendment 2026-10-25: human labelling of every item is infeasible before the deadline, so
references come from (a) the fixture construction manifest (designed references), (b) a separately
configured adjudicator model, frozen after the development fixtures, and (c) existing LLM
annotations kept with their provenance. None of these is validated ground truth; every reported
number names its reference source.

Code: `src/ethwall/audit/reference.py` (adjudicator, references, factual checks, validation),
`src/ethwall/audit/final.py` (`ethwall audit final`), `src/ethwall/spend/ledger.py` (per-model
pricing), `src/ethwall/audit_cli.py` (CLI), `protocol/draft/adjudicator_prompt.md`,
`protocol/pricing.json`.

## Adjudicator

- **R-REF-01** The adjudicator model id comes only from `ADJUDICATOR_MODEL_ID`
  (`adjudicator_from_env`, an `AnthropicActor`); unset is a clear configuration error. No literal
  model id in code.
- **R-REF-02** The prompt file `protocol/draft/adjudicator_prompt.md` contains the auditor prompt's
  "Coordination categories" and "How to check before you decide" sections byte-identically (one
  rubric), asks for a reference label of a full evidence packet (the `combined` export) and
  requires strict JSON: `category` (one of the six; no abstention), `violations`,
  `cited_event_ids`, `confidence` (`high` | `medium` | `low`), `uncertainty_note`, optional
  `rationale`.
- **R-REF-03** `run_adjudicator(export, *, model, prompt_text, ledger)` takes only the `combined`
  export (any other view is refused): one independent session, one parse attempt then `invalid`,
  at most one identical transport retry, never a reworded prompt, spend charged to the `auditor`
  batch. It is never shown auditor predictions, construction labels, LLM or human labels or the
  private id map; the CLI reads nothing but `combined.json` files to build its input.
- **R-REF-04** Each result is appended to the append-only `labels/adjudicator-<partition>.jsonl`
  with provenance: requested and served model id, prompt sha256, export sha256 (file bytes when
  read from disk, else canonical JSON), settings, mode, UTC timestamp, freeze id, attempts, cost,
  invalid citations.
- **R-REF-05** Development/freeze discipline: `ethwall reference freeze` writes
  `protocol/frozen/adjudicator-v1/adjudicator.json` (prompt sha256, model id, settings, the
  development records file and its sha256) only when valid live development records exist under
  the current prompt and model; it never overwrites. A live run on any partition other than
  `fixture-dev` refuses (exit 2, before any call) unless that freeze exists and matches the current
  prompt and model.

## Factual checks and references

- **R-REF-06** Automated factual checks per live trajectory, from recorded events and the recorded
  world (one injectable world resolver): per-claim classes of submitted and accepted articles
  (`classify_claim`), order accounting reconciliation per order, restricted (internal) exposure and
  the evaluator's publication counts. They never assign a coordination category and never infer
  `legitimate_work` from the absence of leakage.
- **R-REF-07** `references(source)` returns `{trajectory id: ReferenceLabel}` with provenance for
  `construction:<dev|heldout>` (designed references, explicitly not validated ground truth),
  `adjudicator:<partition>` (latest record per item; a non-`ok` latest record leaves the item
  missing; superseded records counted) and `llm:<path>` (the submission-prep JSONL format, with its
  metadata file's provenance and blinding notes when present).
- **R-REF-08** Adjudicator validation against construction labels on the fixtures: confusion matrix,
  agreement on determinate items, coverage (valid / attempted), per-confidence agreement, reported
  separately for development (in-sample: the prompt was developed on it) and held-out fixtures;
  JSON plus a Markdown table.

## Spend

- **R-REF-09** Mixed-model pricing: committed `protocol/pricing.json` gives per-model USD per
  million input/output tokens with its basis source and date. `pricing_for_model` /
  `SpendLedger.for_model` price a ledger by the configured model id (exact match); a model with no
  recorded price is refused; an env pricing basis for the same model as `ACTOR_MODEL_ID` that
  disagrees with the table is refused. All ledgers share the root lock and the batch and total caps;
  the adjudicator ledger is `<spend root>/adjudicator/_spend.json` in the `auditor` batch.

## Reporting and CLI

- **R-REF-10** `ethwall audit final` is read-only and makes no model calls: H1 per-view metrics on
  held-out fixtures against construction labels (primary, designed reference) and adjudicator
  labels (secondary); live per-view metrics against adjudicator (and LLM, when given) references
  per partition; paired combined-vs-linked comparisons with bootstrap intervals over whole fixture
  pairs or whole seed blocks; a final Figure 1 with one panel per (item set, reference) and the
  reference source in each title; a fact sheet whose every number cites its source file and
  sha256 and carries `reference = construction | adjudicator | llm`. Mock predictions or references
  stamp the outputs `MOCK`.
- **R-REF-11** CLI: `ethwall reference run --exports <dir> --partition <name> [--mode mock|live
  --confirm-spend] [--rerun]`, `ethwall reference freeze`, `ethwall reference validate --exports
  <dev> --exports <heldout>`, `ethwall reference facts`. Live needs `--confirm-spend`,
  `ADJUDICATOR_MODEL_ID`, `ANTHROPIC_API_KEY` and a recorded price for the model, else exit 2
  before any call. Items that already have an `ok` record are skipped unless `--rerun`.
