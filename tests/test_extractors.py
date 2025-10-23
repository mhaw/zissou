from app.services import parser


def test_extractors_load():
    """Ensure extractor registry validates without raising errors."""
    parser._validate_extractors()
