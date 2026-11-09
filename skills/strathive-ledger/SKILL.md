---
name: strathive-ledger
description: Use the local StratHive CLI to open accounts, manage strategy runs and pools, record or correct executed trades, query assets, or reconcile a broker snapshot.
---

Run `strathive --help` to discover commands. Use the command’s `--help` for named options. Keep positional IDs for single-record lookups. Use `strathive --json ...` for operations and parse the single result envelope. Provide the same global `--db PATH` for all commands when the user selects a ledger.

- Use `account list`, `pool list`, `strategy list`, `run list`, `trade list`, and `security lookup` to resolve identities. Ask for any missing transaction facts or ambiguous identity before writing. Pass command arguments safely without interpolating user text as shell syntax.
- For opening with holdings, prepare the JSON described in [inputs](references/inputs.md). The sum allocated to pools must match the declared account positions. Ask for the broker's opening cutoff, and for the actual execution time of subsequent trades.
- Check actual fees from the user's fill if supplied. Use fee overrides as provided. For deletion, show the identified trade and require authorization before running `trade delete --trade-id ID --yes`.
- Report committed writes only after `ok: true`. On failure, relay the error and correct the inputs before another attempt. If a write outcome is unknown, query the ledger before retrying.
- Reconciliation is read-only. Report cash and quantity differences and any incomparable costs separately. Pass a cost basis only when the user has confirmed it matches the ledger's moving-average method.
