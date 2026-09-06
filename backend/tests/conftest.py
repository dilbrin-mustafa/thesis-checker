from __future__ import annotations

import sys
from pathlib import Path

# Ensure `import app...` resolves to backend/app without installation.
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
