#!/usr/bin/env python3
"""Shim: the builder now lives in the package, so an installed copy can use it.

Keeping this path working matters -- it is the command in the README, in CI and
in every finding that tells a reader how to refresh stale data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itemize.build_data import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
