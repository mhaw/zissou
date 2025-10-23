import os
import sys
from importlib import import_module
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

CHECK_IMPORTS_MODE = "--check-imports" in sys.argv
if CHECK_IMPORTS_MODE:
    os.environ.setdefault("ZISSOU_SKIP_FIRESTORE_INIT", "1")

app_module = import_module("app")
create_app = app_module.create_app

app = create_app()

if __name__ == "__main__":
    if "--check-imports" in sys.argv:
        print("Import check successful.")
        sys.exit(0)
    app.run(host="0.0.0.0", port=8080, debug=True)
