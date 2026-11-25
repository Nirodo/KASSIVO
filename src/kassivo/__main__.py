import sys

import uvicorn

from kassivo.db import MigrationError, SchemaTooNewError
from kassivo.settings import default_db_path, startup_lines
from kassivo.web.app import create_app


def main() -> None:
    db = default_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    for line in startup_lines(db):
        print(line, flush=True)
    try:
        app = create_app(db)
    except (SchemaTooNewError, MigrationError) as exc:
        print(exc, file=sys.stderr, flush=True)
        sys.exit(1)
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
