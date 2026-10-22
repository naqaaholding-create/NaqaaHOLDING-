from collections import Counter

from crypto_routes import router


def test_crypto_routes_have_no_duplicate_method_and_path_pairs():
    registered = [
        (method, route.path)
        for route in router.routes
        for method in (getattr(route, "methods", None) or set())
    ]
    duplicates = [
        (method, path, count)
        for (method, path), count in Counter(registered).items()
        if count > 1
    ]
    assert duplicates == []
