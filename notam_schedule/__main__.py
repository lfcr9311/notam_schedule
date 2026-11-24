"""python -m notam_schedule -> schedule x NOTAM analysis (see cli/analyze.py)."""
import sys

from .cli.analyze import main

if __name__ == "__main__":
    sys.exit(main())
