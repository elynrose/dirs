"""Convenience entrypoint: ``python scripts/run_final_cut.py --help``

Forwards to ``troubleshoot_compile.py final-cut`` (same flags).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_API = _SCRIPTS.parent
for _p in (_API, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import troubleshoot_compile as _tc

if __name__ == "__main__":
    raise SystemExit(_tc.main(["final-cut", *sys.argv[1:]]))
