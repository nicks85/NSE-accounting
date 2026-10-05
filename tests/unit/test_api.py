from engine import api


def test_engine_version() -> None:
    assert api.engine_version() == "0.0.1"
