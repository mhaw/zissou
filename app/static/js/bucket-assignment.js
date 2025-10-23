(function (window) {
  if (window.BucketAssignment) {
    return;
  }

  function csrfToken() {
    if (window.Zissou && typeof window.Zissou.csrf === 'function') {
      return window.Zissou.csrf();
    }
    return '';
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
        'X-CSRFToken': csrfToken(),
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      credentials: 'same-origin',
      body: JSON.stringify({ bucket_ids: ids }),
    }).then(toPayload);
  }

  window.BucketAssignment = {
    update,
  };
})(window);
