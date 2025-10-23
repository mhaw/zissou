# tests/test_imports.py
def test_import_config_smoke():
    import importlib

    importlib.import_module("app.config")
