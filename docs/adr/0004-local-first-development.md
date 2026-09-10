# 0004 — Keep development local and lightweight with Docker Compose

## Status

Accepted

## Context

Development must remain practical on a laptop with approximately 16 GB RAM. Heavy orchestration would obscure the initial network-boundary learning objective.

## Decision

Use Docker Compose for three small service containers, health checks and a shared network, with conservative runtime limits. Require no cloud or Kubernetes infrastructure.

## Consequences

Developers get reproducible service DNS and independent processes with modest runtime resources. Docker Desktop and builds add overhead. Compose is a development environment, not a production deployment design; scaling, high availability and cloud orchestration remain later decisions.

