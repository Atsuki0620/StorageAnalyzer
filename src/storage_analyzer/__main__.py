"""`python -m storage_analyzer` のエントリポイント."""
from __future__ import annotations

import sys

from storage_analyzer.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
