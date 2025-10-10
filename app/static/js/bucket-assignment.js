(function (window) {
  if (window.BucketAssignment) {
    return;
  }

  const CSRF_META_SELECTOR = 'meta[name="csrf-token"]';

  function getCsrfToken() {
    if (window.Zissou && typeof window.Zissou.getCsrfToken === 'function') {
      return window.Zissou.getCsrfToken();
    }
    const meta = document.querySelector(CSRF_META_SELECTOR);
    return meta ? meta.getAttribute('content') : '';
  }

  function toPayload(response) {
    if (!response.ok) {
      throw new Error(`Bucket assignment failed with status ${response.status}`);
    }
    return response.json();
  }

  function update(endpoint, bucketIds) {
    if (!endpoint) {
      return Promise.reject(new Error('Missing bucket update endpoint.'));
    }
    const ids = Array.isArray(bucketIds) ? bucketIds : [];
    return fetch(endpoint, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      credentials: 'include',
      body: JSON.stringify({ bucket_ids: ids }),
    }).then(toPayload);
  }

  window.BucketAssignment = {
    update,
  };
})(window);
