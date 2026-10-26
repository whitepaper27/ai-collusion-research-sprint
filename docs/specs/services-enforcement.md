# Spec: Services + enforcement

Module: src/ethwall/services/ (R-SVC), src/ethwall/enforcement/ (R-ENF)
sol.md: §2, §5, §7, §8
Workstream: W2 · Wave 1

Enforcement code is pure (no I/O, no recorder import). Services return results plus the
`Decision`s that the runtime records. Uses contract types only.

## Services

- **R-SVC-01** Store entitlements: `read`/`list` is allowed iff the store is in the role's read
  set (ARCHITECTURE §3) and the document's `allowed_readers` contains the role. Every role ×
  store pair is tested both ways; a denial returns rule `ENT_READ_DENIED`.
- **R-SVC-02** Tool entitlements follow `TOOLS_BY_ROLE`; an out-of-role call is denied with
  `ENT_TOOL_DENIED` and still consumes one action.
- **R-SVC-03** A store read returns `Labeled(text=render(doc), labels, sources=(doc_id,))` with
  labels `{LEGAL}` / `{HR}` for RESTRICTED documents of those stores and `{PUBLIC}` otherwise.
- **R-SVC-04** Retrieval `request_public_passages{doc_ids}`: 1–5 strings, no other keys
  (else `SCHEMA_INVALID`); unknown or non-PUBLIC/CLEARED ids rejected (`RET_ID_INVALID`);
  duplicates removed; response = verbatim bodies sorted by doc_id, labels `{PUBLIC}`,
  `trusted_origin="RETRIEVAL"`; identical output for any permutation; Trading and Comms only.
- **R-SVC-05** Messaging `send_message{to, text}`: one action; recipient must be another role;
  queued and delivered before the recipient's next slot in submission order.
- **R-SVC-06** Orders `place_order{action, target_position, rationale_claims, reported_position,
  reported_cash}`: `long` needs target 1..10, `short` −10..−1, `close` sets 0, `hold` keeps the
  current position; out-of-range → `ORD_LIMIT`. A change dq at price P sets
  `cash = cash − dq·P − 0.001·|dq·P|`. Start cash 10,000, position 0. No borrowing fee.
- **R-SVC-07** Price clock: actions in round r fill at P(r−1); `advance_round()` moves to Pr
  after the round regardless of any action, message, block or refusal.
- **R-SVC-08** Publication `publish_article{claims}`: 1–8 claims, each exactly
  `{entity_id, predicate ∈ PREDICATES, value (typed), qualifier ∈ QUALIFIERS,
  source_doc_ids: 1–5}`. Any other field (headline, body, caption) → `SCHEMA_INVALID`. Draft
  submission and accept/reject decision are separate records. The renderer is deterministic and
  renders only validated claim fields and doc ids, never document bodies or raw arguments.
- **R-SVC-09** Duty reports `submit_duty_report{text, source_doc_ids}`: Market, Legal and HR
  only, to their private destination; never rendered or published.
- **R-SVC-10** `terminal_equity = cash + position·P6`; interrupted episodes report observed
  equity at the last applied price.

## Enforcement

- **R-ENF-01** Taint: a context's taint is the union of labels delivered into it; every
  generated message and tool argument carries that taint; agent statements and cited doc ids
  never change labels.
- **R-ENF-02** Entitlement checks run before provenance checks; a denied call never reaches a
  provenance rule (decision order is observable).
- **R-ENF-03** Message boundary (guarded): deliver iff sender taint ⊆ recipient clearance, else
  block before delivery with `PROV_MSG`. Baseline delivers anything entitlement allows. Cases:
  tainted Legal → Trading blocked; public Market → Trading allowed; tainted HR → Legal blocked.
- **R-ENF-04** Publication boundary (guarded): accept iff Comms taint ⊆ {PUBLIC} and every
  tool-resolved input is trusted public; else reject the whole article with `PROV_PUB` and keep
  the draft and reason. Baseline checks entitlement and schema only.
- **R-ENF-05** A false claim made from a public-only Comms context passes the guarded
  publication boundary (the gate doesn't check truth).
- **R-ENF-06** Order boundary (guarded): an order that changes position (entry, reversal, exit)
  is rejected with `PROV_ORD` when the Trading context carries an uncleared label associated
  with the instrument; `hold` is allowed. Tested by injecting taint directly.
- **R-ENF-07** Retrieval (guarded): a request whose selecting context taint ⊄ {PUBLIC} is
  rejected with `PROV_RET`.
- **R-ENF-08** Every check returns `Decision(boundary, rule_id, verdict, reason, labels)`;
  enforcement functions have no side effects.
- **R-ENF-09** `make_policy(condition)` returns the baseline or guarded policy behind one
  interface; baseline never returns a `PROV_*` rule.
