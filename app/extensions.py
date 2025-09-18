try:
    from flask_caching import Cache  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback for offline dev/test
    from functools import wraps
    import logging

    logger = logging.getLogger(__name__)

    class Cache:  # minimal fallback shim
        def __init__(self):
            logger.warning("Flask-Caching not installed; using no-op cache shim.")

        def init_app(self, app):  # pylint: disable=unused-argument
            return None

        def cached(self, *args, **kwargs):  # pylint: disable=unused-argument
            def decorator(func):
                @wraps(func)
                def wrapper(*w_args, **w_kwargs):
                    return func(*w_args, **w_kwargs)

                return wrapper

            return decorator

        def delete_memoized(self, *args, **kwargs):  # pylint: disable=unused-argument
            return None


cache = Cache()
