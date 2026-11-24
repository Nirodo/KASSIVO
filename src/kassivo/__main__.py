import os
from pathlib import Path

import uvicorn

from kassivo.web.app import create_app


def main() -> None:
    db = Path(os.environ.get("KASSIVO_DB", "data/kassivo.db"))
    db.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(db), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
