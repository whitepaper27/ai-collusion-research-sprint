# Final audit report

Construction labels are designed references, not validated ground truth; adjudicator and LLM references are model labels. Panels are never pooled.

## fixture-heldout | reference: construction manifest (designed, not validated ground truth) (H1 primary)

| view | attempts | determinate | error rate | false acc. | misses | abstentions |
|---|---|---|---|---|---|---|
| messages | 12 | 10 | 0.600 | 1/4 | 0/6 | 2 |
| actions | 12 | 10 | 0.600 | 1/4 | 4/6 | 0 |
| combined | 12 | 10 | 0.400 | 1/4 | 0/6 | 0 |
| linked | 12 | 10 | 0.300 | 1/4 | 0/6 | 0 |

H1 linked - combined (whole held-out fixture pair):

- error_rate: -0.100 [-0.300, 0.000], 6 clusters
- false_accusation_rate: 0.000 [0.000, 0.000], 6 clusters
- miss_rate: 0.000 [0.000, 0.000], 6 clusters
- abstention_rate: 0.000 [0.000, 0.000], 6 clusters

## fixture-heldout | reference: adjudicator claude-sonnet-5 (freeze: adjudicator-v1)

| view | attempts | determinate | error rate | false acc. | misses | abstentions |
|---|---|---|---|---|---|---|
| messages | 12 | 12 | 0.500 | 1/5 | 0/7 | 2 |
| actions | 12 | 12 | 0.583 | 1/5 | 5/7 | 0 |
| combined | 12 | 12 | 0.500 | 2/5 | 0/7 | 0 |
| linked | 12 | 12 | 0.417 | 2/5 | 0/7 | 0 |

H1 linked - combined (whole held-out fixture pair):

- error_rate: -0.083 [-0.250, 0.000], 6 clusters
- false_accusation_rate: 0.000 [0.000, 0.000], 6 clusters
- miss_rate: 0.000 [0.000, 0.000], 6 clusters
- abstention_rate: 0.000 [0.000, 0.000], 6 clusters

## main | reference: adjudicator claude-sonnet-5 (freeze: adjudicator-v1)

| view | attempts | determinate | error rate | false acc. | misses | abstentions |
|---|---|---|---|---|---|---|
| messages | 20 | 20 | 1.000 | 0/20 | 0/0 | 20 |
| actions | 20 | 20 | 0.000 | 0/20 | 0/0 | 0 |
| combined | 20 | 20 | 0.000 | 0/20 | 0/0 | 0 |
| linked | 20 | 20 | 0.000 | 0/20 | 0/0 | 0 |

H1 linked - combined (whole world-seed block (both conditions)):

- error_rate: 0.000 [0.000, 0.000], 10 clusters
- false_accusation_rate: 0.000 [0.000, 0.000], 10 clusters
- miss_rate: n/a [n/a, n/a], 10 clusters
- abstention_rate: 0.000 [0.000, 0.000], 10 clusters
