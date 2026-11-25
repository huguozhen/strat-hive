# 账本操作

1. Use command-level `--help` to verify the installed CLI supports the requested workflow. If a required command or option is missing, report the version mismatch and stop that operation.
2. Run `strathive …` and parse its default JSON result envelope. Pass the same global `--db PATH` when the user selects a ledger. Resolve account, strategy, version, run and security identities before writing. Ask for any missing facts and pass user input as data rather than shell syntax.
3. Apply the relevant workflow:
   - **Version a strategy**: Use `strategy version create --strategy-id ID` to freeze the current name and description. Use version list or show to resolve a requested version. When creating a run, pass `--version` if the user specifies one. Otherwise the CLI binds the latest at creation. Report the bound version from the result.
   - **Open or start**: Identify the asset account and confirm the starting cash and time. For existing holdings, use the [opening format](inputs.md#期初持仓) and allocate the full declared quantity. Starting cash is fixed at the run boundary.
   - **Set commissions**: When opening an account or changing its rates, use the [commission settings](inputs.md#佣金费率) to apply the user's stock and ETF rates separately.
   - **Record or correct**: Enter executed trades and actual fee overrides supplied by the user. Before deleting a trade, show the identified record and obtain authorization.
   - **Close or hand off**: Confirm the source run and intended close time. For a handoff, identify the draft successor in the same account and confirm its starting cash. The CLI calculates boundary values automatically; report the returned price date and any unpriced holdings.
   - **Value assets**: Use `valuation account --account-id ID` or `valuation run --run-id ID`. Follow [valuation guidance](inputs.md#自动取价与估值) for refresh, offline mode, degraded quotes and completing missing boundary values. A missing total is unknown, not zero.
   - **Reconcile**: Use the [broker snapshot format](inputs.md#对账-csv). Report cash and quantity differences separately from incomparable costs. Select a cost basis only when the user confirms it matches.
4. Report a write as committed when the CLI returns `ok: true`. For opening with holdings, recording a trade, and closing or handing off a run, pass a unique `--request-id`. If the outcome is unknown, reuse that ID with identical inputs to recover the result. If request recovery is unavailable, query the ledger before deciding what to do next.
