import os
from pathlib import Path

DB_PATH = Path(os.environ.get("KASSIVO_DB", "data/kassivo.db"))
