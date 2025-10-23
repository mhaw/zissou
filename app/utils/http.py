from urllib.parse import urlparse, urljoin
from flask import request, url_for


def get_safe_redirect(target, default_endpoint="main.index"):
    """
    Ensures a redirect target is safe by checking if it points to the same host.
    If the target is unsafe or empty, it returns the URL for a default endpoint.

    Args:
        target (str): The redirect URL to validate.
        default_endpoint (str): The Flask endpoint to use as a fallback.

    Returns:
        str: A safe redirect URL.
    """
    if not target or not isinstance(target, str):
        return url_for(default_endpoint)

    # Handle scheme-relative URLs like //example.com by rejecting them.
    if target.startswith("//"):
        return url_for(default_endpoint)

    host_url = request.host_url
    # urljoin is used to correctly handle relative paths.
    final_url = urljoin(host_url, target)

    # The main security check: ensure the netloc of the joined URL
    # is the same as the netloc of the request's host.
    if urlparse(final_url).netloc == urlparse(host_url).netloc:
        # The target is safe, so we can use it.
        # Note: We return the original target, not final_url, to preserve relative paths
        # if they are desired. The browser will handle turning it into an absolute URL.
        return target

    return url_for(default_endpoint)
