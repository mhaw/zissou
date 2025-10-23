from app.services.parser import EXTRACTOR_REGISTRY


def test_extractor_registry_functions_exist():
    """Ensure all extractor functions referenced in EXTRACTOR_REGISTRY are defined and callable."""
    for strategy in EXTRACTOR_REGISTRY.all():
        assert callable(
            strategy.extractor
        ), f"Extractor function for {strategy.name} is not callable."
