"""sys.path bootstrap so `risk_backup` resolves when pytest runs from the repo root.

`risk_backup` is a path package (ADR 0031 §12 — mounted into the production
image, not installed). Tests import it directly; this conftest makes the
`src/` layout importable without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
