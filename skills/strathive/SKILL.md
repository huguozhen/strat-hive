---
name: strathive
description: Use the local StratHive CLI to manage strategy-run ledgers, record executed trades, reconcile broker accounts, value holdings, query or export historical market bars, and install or uninstall the optional history component.
---

1. Verify the requested command with `strathive … --help`. Use the installed CLI's options and default JSON envelope; report a version mismatch if a required command is absent.
2. Select the workflow reference:
   - Accounts, strategy versions, runs, executed trades, holdings handoffs, reconciliation and valuation: [ledger](references/ledger.md). Read [structured inputs](references/inputs.md) when supplying opening holdings, commissions, handoff prices or broker CSV.
   - Quotes, historical OHLCV, adjustment, aggregation, exports or data synchronization: [market data](references/market.md).
   - Installing, repairing, inspecting or removing the optional history component: [components](references/components.md).
3. Pass a user-selected ledger through the global `--db PATH` option. Market-data and component commands can run without a ledger. Resolve requested identities and scope before mutations; pass input as data rather than shell syntax.
4. Report the CLI's actual outcome, including missing prices, incomplete coverage and degraded quotes. The history component is optional; installing it does not start a market-wide collection or a timer, and uninstalling it retains historical data and the ledger.
