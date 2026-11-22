import sys
from pathlib import Path

# Reuse the synthetic discs from the engine tests.
sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))
