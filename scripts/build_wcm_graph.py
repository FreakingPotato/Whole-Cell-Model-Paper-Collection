#!/usr/bin/env python3
"""Compatibility entrypoint for the SQLite-backed WCM build pipeline."""

from __future__ import annotations

from wcm.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
