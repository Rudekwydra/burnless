# Burnless Security And Release Hygiene Audit

Date: 2026-05-04

## Executive Summary

The repository is close to releasable hygiene: package build/test flow works,
runtime secrets are already stored outside the repo, and local env files have
correct `0600` permissions. The main release gap was operational: the PyPI
token existed at `~/.config/burnless/pypi.env`, but the release flow did not
load it, causing `twine upload` to prompt interactively and fail.

## Findings

### A1 — PyPI Token Not Loaded By Release Flow

Severity: High

Impact: releases fail in non-interactive environments and encourage ad-hoc
token handling.

Fix: added `scripts/release_pypi.sh`, which sources
`~/.config/burnless/pypi.env`, builds, checks, and optionally uploads.

### A2 — Repo Did Not Publish A Secret Schema

Severity: Medium

Impact: contributors cannot tell which env vars are expected without inspecting
local machine state or failing commands.

Fix: added `.env.example`, `RELEASE.md`, and `SECURITY.md`.

### A3 — Ignore Rules Needed Explicit Secret Coverage

Severity: Medium

Impact: common local files such as `.env.local`, `.pypirc`, or private-key
formats could be accidentally staged.

Fix: expanded `.gitignore` for `.env.*`, `.pypirc`, `*.pem`, `*.p12`, and `*.p8`
while allowing `.env.example`.

### A4 — Public Supabase Publishable Key In Static Site

Severity: Informational

Impact: publishable browser keys are not secrets, but they require Supabase RLS
or equivalent server-side protections. This audit did not verify Supabase RLS.

Fix: documented the distinction in `SECURITY.md`. RLS verification remains a
separate infrastructure check.

## Local Secret Inventory

Found local files:

- `~/.config/burnless/anthropic.env`
- `~/.config/burnless/pypi.env`
- `~/.config/burnless/stripe.env`
- `~/.config/burnless/supabase.env`

All checked files have `0600` permissions.

## Cleanup

Generated `_codex_*` logs were removed from the working tree. Future generated
logs should stay ignored or be stored under `.burnless/`/`bench/results/` as
appropriate.
