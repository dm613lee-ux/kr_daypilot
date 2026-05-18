# Experiments

This folder records strategy research separately from production code.

Purpose:

- Keep every strategy hypothesis explicit.
- Track failures as useful research evidence.
- Prevent repeated tuning without a record.
- Promote only strategies that pass out-of-sample and paper-trading gates.

Files:

```text
registry.csv
strategy_hypotheses.json
registry_rg2.csv
```

Future experiment output format:

```text
EXP_YYYYMMDD_NNN/
  config.json
  trades.csv
  metrics.json
  report.html
  reviewer_notes.md
```

Experiment states:

```text
planned
running
failed_research
paper_only
live_candidate
retired
```

No API keys, credentials, or raw private data should be stored here.

`registry_rg2.csv` stores Research Gate 2 run-level records for the multi-factor
recommendation engine. RG2 promotion is capped at `paper_only`; live order
automation is never enabled from this registry.
