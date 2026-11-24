"""python -m notam_schedule.ml -> ML lifecycle CLI (see cli/ml.py)."""
import sys

from ..cli.ml import main

if __name__ == "__main__":
    sys.exit(main())
