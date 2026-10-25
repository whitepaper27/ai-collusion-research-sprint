# Plan: <short title>

Issue: #<n> · Author: <human or agent> · Status: draft | approved | done

## Goal
<What changes for the user, in one or two sentences. Not how.>

## Acceptance criteria
<!-- Each one must be checkable by a test or a command. These are the contract. -->
- [ ] <Given ..., when ..., then ...>
- [ ] <...>
- [ ] `./scripts/verify.sh` passes

## Out of scope
- <What this change deliberately does not do>

## Approach
<The design in a few paragraphs: which modules change, data model changes, API changes.
Name the existing code you're following as the pattern.>

## Files
| File | Change |
|---|---|
| `path/to/file.ts` | <new / modified: what> |

## Risks and reversibility
<!-- Hard-to-reverse decisions (data model, auth, tenancy, billing, public API) need a human
     to approve this plan before implementation. Everything else: decide in the PR. -->
- Reversible? <yes / no — why>
- <Risk> → <mitigation>

## Steps
1. <Failing test(s) for the acceptance criteria>
2. <Implementation step>
3. <Verify: command(s) and what passing looks like>
