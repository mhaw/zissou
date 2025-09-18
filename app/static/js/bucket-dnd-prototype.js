(function () {
  const lab = document.querySelector('[data-dnd-lab]');
  if (!lab) {
    return;
  }

  const dropZones = Array.from(lab.querySelectorAll('[data-drop-target]'));
  const statusEl = lab.querySelector('[data-dnd-status]');
  const dock = document.querySelector('[data-dnd-dock]');
  const panel = dock ? dock.querySelector('[data-dnd-panel]') : null;
  const hideButton = dock ? dock.querySelector('[data-dnd-toggle]') : null;
  const restoreButton = dock ? dock.querySelector('[data-dnd-restore]') : null;
  const STATUS_CLASSES = {
    info: 'text-slate-600',
    pending: 'text-blue-600',
    success: 'text-emerald-600',
    error: 'text-rose-600',
  };

  let isCollapsed = false;

  function setDockState(collapsed) {
    if (!dock) {
      return;
    }
    isCollapsed = !!collapsed;
    dock.setAttribute('data-collapsed', isCollapsed ? 'true' : 'false');
    if (panel) {
      panel.classList.toggle('hidden', isCollapsed);
    }
    if (hideButton) {
      hideButton.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
      const label = hideButton.querySelector('span');
      if (label) {
        label.textContent = isCollapsed ? 'Show' : 'Hide';
      }
    }
    if (restoreButton) {
      restoreButton.classList.toggle('hidden', !isCollapsed);
      restoreButton.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
    }
  }

  function wireDockControls() {
    if (!dock) {
      return;
    }
    setDockState(false);
    if (hideButton) {
      hideButton.addEventListener('click', (event) => {
        event.preventDefault();
        setDockState(true);
      });
    }
    if (restoreButton) {
      restoreButton.addEventListener('click', (event) => {
        event.preventDefault();
        setDockState(false);
      });
    }
  }

  let activeCard = null;
  let statusTimeout = null;

  wireDockControls();

  function setStatus(message, tone) {
    if (!statusEl) {
      return;
    }
    const state = tone || 'info';
    if (statusTimeout) {
      window.clearTimeout(statusTimeout);
      statusTimeout = null;
    }
    statusEl.classList.remove('hidden');
    statusEl.textContent = message;
    Object.values(STATUS_CLASSES).forEach((className) => {
      statusEl.classList.remove(className);
    });
    statusEl.classList.add(STATUS_CLASSES[state] || STATUS_CLASSES.info);
    if (state === 'success') {
      statusTimeout = window.setTimeout(() => {
        statusEl.classList.add('hidden');
      }, 3000);
    }
  }

  function resetDropState(activeZone) {
    dropZones.forEach((zone) => {
      if (zone !== activeZone) {
        zone.removeAttribute('data-state');
      }
    });
    if (activeCard) {
      activeCard.setAttribute('aria-grabbed', 'false');
      activeCard = null;
    }
  }

  function parseBucketIds(card) {
    if (!card) {
      return [];
    }
    const raw = card.dataset.itemBuckets;
    if (!raw) {
      return [];
    }
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed.filter((value) => typeof value === 'string' && value.trim().length > 0);
    } catch (error) {
      console.warn('Unable to parse bucket metadata for card', error);
      return [];
    }
  }

  function storeBucketIds(card, bucketIds) {
    if (!card) {
      return;
    }
    try {
      card.dataset.itemBuckets = JSON.stringify(bucketIds);
    } catch (error) {
      console.warn('Unable to persist bucket metadata on card', error);
    }
  }

  function ensurePlaceholder(listEl) {
    const placeholder = document.createElement('span');
    placeholder.className = 'inline-flex items-center gap-2 rounded-full border border-dashed border-slate-300 px-3 py-1 text-xs text-slate-500';
    placeholder.setAttribute('data-item-bucket-empty', '');
    placeholder.textContent = 'Drop into a bucket to assign';
    listEl.appendChild(placeholder);
  }

  function renderBucketChips(card, bucketIds, bucketNames) {
    const section = card.querySelector('[data-item-buckets-section]');
    if (!section) {
      return;
    }
    const listEl = section.querySelector('[data-item-bucket-list]');
    if (!listEl) {
      return;
    }
    listEl.innerHTML = '';
    if (!bucketIds.length) {
      ensurePlaceholder(listEl);
      return;
    }

    bucketIds.forEach((bucketId, index) => {
      const name = bucketNames && bucketNames[index] ? bucketNames[index] : `Bucket ${index + 1}`;
      const chip = document.createElement('span');
      chip.className = 'inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800';
      chip.setAttribute('data-item-bucket-chip', '');
      chip.setAttribute('data-bucket-id', bucketId);
      chip.innerHTML = `
        <svg class="h-3.5 w-3.5 text-amber-500" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 2a1 1 0 01.894.553l1.382 2.764 3.05.443a1 1 0 01.554 1.706l-2.207 2.152.521 3.04a1 1 0 01-1.451 1.054L10 12.347l-2.743 1.44a1 1 0 01-1.451-1.054l.521-3.04L4.12 7.466a1 1 0 01.554-1.706l3.05-.443L9.106 2.553A1 1 0 0110 2z"></path>
        </svg>
        ${name}
      `;
      listEl.appendChild(chip);
    });
  }

  function assignCardToBucket(card, zone) {
    const bucketId = zone.dataset.bucketId;
    const bucketName = zone.dataset.bucketName || 'Selected bucket';
    if (!bucketId) {
      setStatus('Bucket unavailable for assignment.', 'error');
      return;
    }
    const endpoint = card.dataset.bucketEndpoint;
    if (!endpoint) {
      setStatus('Assignment endpoint missing for this item.', 'error');
      return;
    }

    const currentBucketIds = parseBucketIds(card);
    if (currentBucketIds.includes(bucketId)) {
      setStatus(`Already assigned to ${bucketName}.`, 'info');
      zone.setAttribute('data-state', 'duplicate');
      window.setTimeout(() => {
        zone.removeAttribute('data-state');
      }, 600);
      return;
    }

    const updatedBucketIds = currentBucketIds.concat(bucketId);
    zone.setAttribute('data-state', 'pending');
    setStatus(`Adding to ${bucketName}…`, 'pending');

    const assignmentClient = window.BucketAssignment;
    if (!assignmentClient || typeof assignmentClient.update !== 'function') {
      console.error('BucketAssignment helper unavailable.');
      zone.setAttribute('data-state', 'error');
      setStatus('Bucket assignment unavailable.', 'error');
      window.setTimeout(() => {
        zone.removeAttribute('data-state');
      }, 900);
      return;
    }

    assignmentClient
      .update(endpoint, updatedBucketIds)
      .then((payload) => {
        const bucketIds = Array.isArray(payload.bucket_ids) ? payload.bucket_ids : updatedBucketIds;
        const bucketNames = Array.isArray(payload.bucket_names) ? payload.bucket_names : bucketIds.map(() => bucketName);
        storeBucketIds(card, bucketIds);
        renderBucketChips(card, bucketIds, bucketNames);
        zone.setAttribute('data-state', 'success');
        setStatus(`Assigned to ${bucketName}.`, 'success');
        window.setTimeout(() => {
          zone.removeAttribute('data-state');
        }, 900);
      })
      .catch((error) => {
        console.error('Failed to assign bucket via drag-and-drop', error);
        zone.setAttribute('data-state', 'error');
        setStatus('Unable to assign bucket. Please retry.', 'error');
        window.setTimeout(() => {
          zone.removeAttribute('data-state');
        }, 900);
      });
  }

  document.addEventListener('dragstart', (event) => {
    const card = event.target.closest('[data-drag-item]');
    if (!card) {
      return;
    }
    if (dock && isCollapsed) {
      setDockState(false);
    }
    activeCard = card;
    card.setAttribute('aria-grabbed', 'true');
    const title = card.dataset.itemTitle || 'Untitled article';
    try {
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData('text/plain', title);
    } catch (error) {
      // Ignore browsers that block programmatic dataTransfer usage.
    }
    lab.setAttribute('data-state', 'dragging');
  });

  document.addEventListener('dragend', () => {
    lab.removeAttribute('data-state');
    resetDropState();
  });

  dropZones.forEach((zone) => {
    zone.addEventListener('dragenter', (event) => {
      if (!activeCard) {
        return;
      }
      event.preventDefault();
      zone.setAttribute('data-state', 'active');
    });

    zone.addEventListener('dragover', (event) => {
      if (!activeCard) {
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
    });

    zone.addEventListener('dragleave', () => {
      if (zone.getAttribute('data-state') === 'active') {
        zone.removeAttribute('data-state');
      }
    });

    zone.addEventListener('drop', (event) => {
      if (!activeCard) {
        return;
      }
      event.preventDefault();
      const card = activeCard;
      lab.removeAttribute('data-state');
      resetDropState(zone);
      assignCardToBucket(card, zone);
    });
  });
})();
