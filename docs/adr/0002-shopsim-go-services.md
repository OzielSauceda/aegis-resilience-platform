# 0002 — Implement initial ShopSim services in Go

## Status

Accepted

## Context

The test target needs real network boundaries, predictable resource use and code that exposes HTTP behavior clearly.

## Decision

Use Go 1.27.x (initially 1.27.1) and the standard library for three independently executable services.

## Consequences

Static binaries and built-in HTTP/testing tools keep builds and runtime small. Separate processes still introduce networking and partial failures. Future Python analysis remains a separate concern; Go is not a commitment for every Aegis component.

