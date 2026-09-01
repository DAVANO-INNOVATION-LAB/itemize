"""Allow `python3 -m itemize` as well as `python3 -m itemize.cli`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
