# 0001 — Start Aegis as a monorepo

## Status

Accepted

## Context

ShopSim and the future monitoring, analysis, infrastructure and UI components will evolve together in a small portfolio project.

## Decision

Use one repository, with one Go module for the initial ShopSim services. Create directories only for implemented work.

## Consequences

Related contracts and documentation can change together and local setup stays simple. Repository-wide checks may grow over time; independent release boundaries and language-specific tooling may be needed later.

