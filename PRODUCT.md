# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Delegated: Python standard library and SQLite, for a locally maintainable single-user application.

## Users
A personal investor recording the ownership of actual brokerage assets across strategy runs.

## Product Purpose
Maintain a trustworthy shared ledger accessible through Web and CLI, starting from current cash and holdings without inventing earlier trades.

## Operating Context
Local, single-user use. Cash means ledger cash; no market prices, frozen funds or automated orders are included.

## Capabilities and Constraints
Accounts, manual strategies, runs, pools, initial holding allocation, cash movements and a replaceable local securities directory. Account cash and security quantities must equal their allocations. Source: GitHub issues #1 and #2 and GLOSSARY.md.

## Product Principles
- Preserve facts and make asset ownership clear.
- Explain failures without hiding existing records.
- Keep common operations usable without market connectivity.
