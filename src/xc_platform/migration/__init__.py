"""The one-time historical backfill (Task 5).

This package migrates the frozen 2023-2025 baseline
(``data/merged/season_results.csv``) into the normalized SQLite schema built
in Task 4. It is deliberately separate from :mod:`xc_platform.ingest`
(Task 6+, live 2026 RunSignup/Tavily intake): the historical baseline's
entity identity is already fully resolved -- every distinct
``athlete_full_name`` and ``team_name`` string in the frozen CSV *is* the
accepted canonical identity (R1.8, the baseline is the parity target, not
something to re-resolve) -- so this package writes canonical entities
directly instead of going through the staged-then-resolved workflow live
ingestion requires.

:class:`~xc_platform.migration.canonical_writer.HistoricalCanonicalWriter` is
therefore one of the "separately authorized, validated workflow tools"
Requirement 10.3 carves out as a canonical write path outside the live
resolution workflow -- a one-time administrative migration, not an adapter
or an agent.
"""

from __future__ import annotations
