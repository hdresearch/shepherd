"""Test wiring for the prototype's documented examples.

Puts the SIMULATION shim on sys.path so ``import shepherd`` resolves to
``docs_src/_sim/shepherd`` (the unshipped surface, simulated). When the
real facade ships, delete the insert below and the same examples run against
the product — that swap is the prototyped migration contract.
"""

import sys
from pathlib import Path

_DOCS_SRC = Path(__file__).resolve().parent
_SIM = _DOCS_SRC / "_sim"
for _p in (str(_SIM), str(_DOCS_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
