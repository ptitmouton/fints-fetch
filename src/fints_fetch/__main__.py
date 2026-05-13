"""Entry point for `python -m fints_fetch`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
