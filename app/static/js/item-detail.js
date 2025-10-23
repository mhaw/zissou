(function() {
    if (!window.fetch) {
        return;
    }

    function csrfToken() {
        if (window.Zissou && typeof window.Zissou.csrf === 'function') {
            return window.Zissou.csrf();
        }
        if (window.Zissou && typeof window.Zissou.getCsrfToken === 'function') {
            return window.Zissou.getCsrfToken();
        }
        return '';
    }

    function updateStatus(element, message, state) {
        if (!element) {
            return;
        }
        const classes = {
            pending: 'text-slate-500',
            success: 'text-emerald-600',
            error: 'text-rose-600'
        };
        element.classList.remove('hidden', 'text-slate-500', 'text-emerald-600', 'text-rose-600');
        element.classList.add(classes[state] || 'text-slate-500');
        element.textContent = message;
        if (state === 'success') {
            window.setTimeout(() => {
                element.classList.add('hidden');
            }, 3000);
        }
    }

    class BucketSelector {
        constructor(root) {
            this.root = root;
            this.fieldName = root.dataset.fieldName || 'bucket_ids';
            this.options = this.parseOptions(root.dataset.allBuckets);
            this.optionLookup = new Map();
            this.options.forEach((option) => {
                this.optionLookup.set(option.id, option);
            });
            this.selected = new Set(this.parseSelected(root.dataset.selectedBuckets));
            this.chipList = root.querySelector('[data-bucket-chip-list]');
            this.optionList = root.querySelector('[data-bucket-option-list]');
            this.hiddenInputs = root.querySelector('[data-bucket-hidden-inputs]');
            this.countLabel = root.querySelector('[data-bucket-selector-count]');
            this.createUrl = root.dataset.createUrl;
            this.handleOptionClick = this.handleOptionClick.bind(this);
            this.handleChipClick = this.handleChipClick.bind(this);
            this.bindEvents();
            this.render();
            root.__bucketSelector = this;
        }

        parseOptions(raw) {
            if (!raw) {
                return [];
            }
            try {
                const parsed = JSON.parse(raw);
                if (!Array.isArray(parsed)) {
                    return [];
                }
                const seen = new Set();
                return parsed.reduce((acc, candidate) => {
                    if (!candidate || typeof candidate !== 'object') {
                        return acc;
                    }
                    const identifier = typeof candidate.id === 'string' ? candidate.id.trim() : '';
                    if (!identifier || seen.has(identifier)) {
                        return acc;
                    }
                    const name = typeof candidate.name === 'string' && candidate.name.trim()
                        ? candidate.name.trim()
                        : identifier;
                    seen.add(identifier);
                    acc.push({ id: identifier, name });
                    return acc;
                }, []);
            } catch (error) {
                console.warn('Unable to parse bucket options', error);
                return [];
            }
        }

        parseSelected(raw) {
            if (!raw) {
                return [];
            }
            try {
                const parsed = JSON.parse(raw);
                if (!Array.isArray(parsed)) {
                    return [];
                }
                return parsed
                    .filter((value) => typeof value === 'string' && value.trim().length > 0)
                    .map((value) => value.trim());
            } catch (error) {
                console.warn('Unable to parse selected buckets', error);
                return [];
            }
        }

        bindEvents() {
            this.optionList?.addEventListener('click', this.handleOptionClick);
            this.chipList?.addEventListener('click', this.handleChipClick);
        }

        handleOptionClick(event) {
            const button = event.target.closest('button[data-bucket-id]');
            if (!button) {
                return;
            }
            event.preventDefault();
            const bucketId = button.dataset.bucketId;
            if (!bucketId) {
                return;
            }
            this.toggle(bucketId);
        }

        handleChipClick(event) {
            const removeButton = event.target.closest('button[data-remove-bucket]');
            if (!removeButton) {
                return;
            }
            event.preventDefault();
            const bucketId = removeButton.dataset.removeBucket;
            if (!bucketId) {
                return;
            }
            this.remove(bucketId);
        }

        toggle(bucketId) {
            if (!bucketId) {
                return;
            }
            if (this.selected.has(bucketId)) {
                this.remove(bucketId);
            } else {
                this.add(bucketId);
            }
        }

        add(bucketId) {
            if (!bucketId) {
                return;
            }
            const next = new Set(this.selected);
            next.add(bucketId);
            this.selected = next;
            this.render();
        }

        remove(bucketId) {
            if (!bucketId) {
                return;
            }
            if (!this.selected.has(bucketId)) {
                return;
            }
            const next = Array.from(this.selected).filter((value) => value !== bucketId);
            this.selected = new Set(next);
            this.render();
        }

        setSelection(values) {
            const safe = Array.isArray(values)
                ? values.filter((value) => typeof value === 'string' && value.trim().length > 0)
                : [];
            this.selected = new Set(safe.map((value) => value.trim()));
            this.render();
        }

        render() {
            this.renderChips();
            this.renderOptions();
            this.renderHiddenInputs();
            this.updateMetadata();
        }

        renderChips() {
            if (!this.chipList) {
                return;
            }
            this.chipList.innerHTML = '';
            const ordered = Array.from(this.selected);
            if (!ordered.length) {
                const placeholder = document.createElement('span');
                placeholder.className = 'inline-flex items-center gap-2 rounded-full border border-dashed border-slate-300 px-3 py-1 text-xs text-slate-500';
                placeholder.textContent = 'No buckets selected yet';
                this.chipList.appendChild(placeholder);
                return;
            }
            ordered.forEach((bucketId) => {
                const option = this.optionLookup.get(bucketId);
                const name = option ? option.name : bucketId;
                const wrapper = document.createElement('span');
                wrapper.className = 'inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700';
                wrapper.setAttribute('data-bucket-id', bucketId);

                const label = document.createElement('span');
                label.textContent = name;

                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'rounded-full p-0.5 text-blue-600 hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-200';
                removeButton.setAttribute('data-remove-bucket', bucketId);
                removeButton.setAttribute('aria-label', `Remove ${name}`);
                removeButton.innerHTML = '<svg class="h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>';

                wrapper.appendChild(label);
                wrapper.appendChild(removeButton);
                this.chipList.appendChild(wrapper);
            });
        }

        renderOptions() {
            if (!this.optionList) {
                return;
            }
            this.optionList.innerHTML = '';
            if (!this.options.length) {
                const message = document.createElement('p');
                message.className = 'text-sm text-slate-500';
                if (this.createUrl) {
                    message.textContent = 'No buckets available yet. ';
                    const link = document.createElement('a');
                    link.className = 'text-blue-600 hover:underline';
                    link.href = this.createUrl;
                    link.textContent = 'Create one?';
                    message.appendChild(link);
                } else {
                    message.textContent = 'No buckets available yet.';
                }
                this.optionList.appendChild(message);
                return;
            }
            this.options.forEach((option) => {
                const selected = this.selected.has(option.id);
                const button = document.createElement('button');
                button.type = 'button';
                button.setAttribute('data-bucket-id', option.id);
                button.setAttribute('aria-pressed', selected ? 'true' : 'false');
                button.className = 'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-blue-200';
                if (selected) {
                    button.classList.add('border-blue-500', 'bg-blue-600', 'text-white', 'shadow');
                    const icon = document.createElement('svg');
                    icon.setAttribute('class', 'h-3.5 w-3.5');
                    icon.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
                    icon.setAttribute('fill', 'none');
                    icon.setAttribute('viewBox', '0 0 24 24');
                    icon.setAttribute('stroke', 'currentColor');
                    icon.setAttribute('stroke-width', '2');
                    icon.setAttribute('aria-hidden', 'true');
                    icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />';
                    const label = document.createElement('span');
                    label.textContent = option.name;
                    button.appendChild(icon);
                    button.appendChild(label);
                } else {
                    button.classList.add('border-slate-200', 'bg-white', 'text-slate-600', 'hover:bg-slate-50');
                    const label = document.createElement('span');
                    label.textContent = option.name;
                    button.appendChild(label);
                }
                this.optionList.appendChild(button);
            });
        }

        renderHiddenInputs() {
            if (!this.hiddenInputs) {
                return;
            }
            this.hiddenInputs.innerHTML = '';
            this.selected.forEach((bucketId) => {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = this.fieldName;
                input.value = bucketId;
                this.hiddenInputs.appendChild(input);
            });
        }

        updateMetadata() {
            const selection = Array.from(this.selected);
            try {
                this.root.dataset.selectedBuckets = JSON.stringify(selection);
            } catch (error) {
                // Ignore dataset serialisation errors.
            }
            if (this.countLabel) {
                this.countLabel.textContent = String(selection.length);
            }
        }
    }

    function initBucketSelectors() {
        document.querySelectorAll('[data-bucket-selector]').forEach((element) => {
            if (!element.__bucketSelector) {
                new BucketSelector(element);
            }
        });
    }

    function handleBucketsSubmit(event, form) {
        event.preventDefault();
        const endpoint = form.dataset.endpoint;
        if (!endpoint) {
            return;
        }
        const statusElement = form.querySelector('[data-bucket-status]');
        const selectorRoot = form.querySelector('[data-bucket-selector]');
        const selector = selectorRoot && selectorRoot.__bucketSelector;
        if (!selector) {
            updateStatus(statusElement, 'Bucket selector unavailable.', 'error');
            return;
        }
        const previousSelection = Array.from(selector.selected);
        updateStatus(statusElement, 'Saving bucket assignments…', 'pending');

        const assignmentClient = window.BucketAssignment;
        if (!assignmentClient || typeof assignmentClient.update !== 'function') {
            console.error('BucketAssignment helper unavailable.');
            selector.setSelection(previousSelection);
            updateStatus(statusElement, 'Bucket assignment unavailable.', 'error');
            return;
        }

        assignmentClient
            .update(endpoint, previousSelection)
            .then((payload) => {
                if (Array.isArray(payload.bucket_ids)) {
                    if (Array.isArray(payload.bucket_names)) {
                        payload.bucket_ids.forEach((bucketId, index) => {
                            const rawName = payload.bucket_names[index];
                            const name = typeof rawName === 'string' && rawName.trim() ? rawName.trim() : bucketId;
                            const existing = selector.optionLookup.get(bucketId);
                            if (existing) {
                                existing.name = name;
                            } else {
                                const newOption = { id: bucketId, name };
                                selector.options.push(newOption);
                                selector.optionLookup.set(bucketId, newOption);
                            }
                        });
                    }
                    selector.setSelection(payload.bucket_ids);
                }
                if (payload.bucket_summary_html) {
                    const summary = document.querySelector('[data-bucket-summary]');
                    if (summary) {
                        summary.innerHTML = payload.bucket_summary_html;
                    }
                }
                updateStatus(statusElement, 'Buckets updated', 'success');
            })
            .catch((error) => {
                console.error('Failed to update buckets', error);
                selector.setSelection(previousSelection);
                updateStatus(statusElement, 'Unable to save buckets. Please retry.', 'error');
            });
    }

    function handleTagsSubmit(event, form) {
        event.preventDefault();
        const endpoint = form.dataset.endpoint;
        if (!endpoint) {
            return;
        }
        const statusElement = form.querySelector('[data-tag-status]');
        const selectorRoot = form.querySelector('[data-tag-selector]');
        const tagSelector = selectorRoot && selectorRoot.__tagSelector;
        if (!tagSelector) {
            updateStatus(statusElement, 'Tag editor unavailable.', 'error');
            return;
        }

        const previousTags = Array.from(tagSelector.selected);
        updateStatus(statusElement, 'Saving tags…', 'pending');

        fetch(endpoint, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken(),
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            credentials: 'same-origin',
            body: JSON.stringify({ tags: previousTags })
        }).then((response) => {
            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }
            return response.json();
        }).then((payload) => {
            if (Array.isArray(payload.tags)) {
                tagSelector.selected = new Set(payload.tags);
                tagSelector.render();
                tagSelector.renderHiddenInputs();
            }
            if (Array.isArray(payload.available_tags)) {
                tagSelector.available = Array.from(new Set(payload.available_tags));
                selectorRoot.dataset.allTags = JSON.stringify(tagSelector.available);
            }
            if (payload.tag_summary_html) {
                const summary = document.querySelector('[data-tag-summary]');
                if (summary) {
                    summary.innerHTML = payload.tag_summary_html;
                }
            }
            updateStatus(statusElement, 'Tags updated', 'success');
        }).catch((error) => {
            console.error('Failed to update tags', error);
            tagSelector.selected = new Set(previousTags);
            tagSelector.render();
            tagSelector.renderHiddenInputs();
            updateStatus(statusElement, 'Unable to save tags. Please retry.', 'error');
        });
    }

    function initAsyncForms() {
        document.querySelectorAll('form[data-async-form]').forEach((form) => {
            const type = form.dataset.asyncForm;
            if (type === 'buckets') {
                form.addEventListener('submit', (event) => handleBucketsSubmit(event, form));
            } else if (type === 'tags') {
                form.addEventListener('submit', (event) => handleTagsSubmit(event, form));
            }
        });
    }

    function formatClockTime(totalSeconds) {
        if (!Number.isFinite(totalSeconds) || totalSeconds < 0) {
            return '0:00';
        }
        const rounded = Math.floor(totalSeconds);
        const seconds = rounded % 60;
        const minutes = Math.floor(rounded / 60) % 60;
        const hours = Math.floor(rounded / 3600);
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
        }
        return `${Math.floor(rounded / 60)}:${String(seconds).padStart(2, '0')}`;
    }

    function triggerHapticFeedback() {
        if (navigator.vibrate) {
            navigator.vibrate(50);
        }
    }

    function initMediaSession(audio, host) {
        if (!('mediaSession' in navigator)) {
            return;
        }
        const title = host.dataset.title || document.title || 'Zissou';
        const artist = host.dataset.artist || '';
        const source = host.dataset.source || window.location.href;
        const artworkSrc = host.dataset.artwork;

        try {
            const artwork = artworkSrc
                ? [
                    { src: artworkSrc, sizes: '512x512', type: 'image/png' },
                    { src: artworkSrc, sizes: '256x256', type: 'image/png' }
                ]
                : undefined;
            navigator.mediaSession.metadata = new window.MediaMetadata({
                title,
                artist,
                album: source,
                artwork
            });
        } catch (error) {
            console.debug('Unable to set media session metadata', error);
        }

        const updatePositionState = () => {
            if (typeof navigator.mediaSession.setPositionState !== 'function') {
                return;
            }
            if (!Number.isFinite(audio.duration) || audio.duration <= 0) {
                return;
            }
            try {
                navigator.mediaSession.setPositionState({
                    duration: audio.duration,
                    playbackRate: audio.playbackRate,
                    position: audio.currentTime
                });
            } catch (error) {
                console.debug('Unable to update media session position', error);
            }
        };

        audio.addEventListener('timeupdate', updatePositionState);
        audio.addEventListener('ratechange', updatePositionState);
        audio.addEventListener('loadedmetadata', updatePositionState);

        const actionHandlers = {
            play: () => audio.play().catch(() => undefined),
            pause: () => audio.pause(),
            stop: () => {
                audio.pause();
                audio.currentTime = 0;
            },
            seekbackward: (details) => {
                const seekOffset = (details && details.seekOffset) || 10;
                audio.currentTime = Math.max(audio.currentTime - seekOffset, 0);
            },
            seekforward: (details) => {
                const seekOffset = (details && details.seekOffset) || 10;
                if (Number.isFinite(audio.duration)) {
                    audio.currentTime = Math.min(audio.currentTime + seekOffset, audio.duration);
                } else {
                    audio.currentTime += seekOffset;
                }
            },
            seekto: (details) => {
                if (details && typeof details.seekTime === 'number') {
                    audio.currentTime = details.seekTime;
                }
            }
        };

        Object.entries(actionHandlers).forEach(([action, handler]) => {
            try {
                navigator.mediaSession.setActionHandler(action, handler);
            } catch (error) {
                console.debug(`Unable to set media action ${action}`, error);
            }
        });

        const updatePlaybackState = () => {
            try {
                navigator.mediaSession.playbackState = audio.paused ? 'paused' : 'playing';
            } catch (error) {
                console.debug('Unable to set playback state', error);
            }
        };

        audio.addEventListener('play', updatePlaybackState);
        audio.addEventListener('pause', updatePlaybackState);
        audio.addEventListener('ended', updatePlaybackState);
        updatePlaybackState();
    }

    function initAudioPlayer() {
        const host = document.querySelector('[data-audio-player]');
        if (!host) {
            return;
        }
        const audio = host.querySelector('audio');
        if (!audio) {
            return;
        }

        const currentLabel = host.querySelector('[data-audio-current]');
        const durationLabel = host.querySelector('[data-audio-duration]');
        const progressTrack = host.querySelector('[data-audio-progress-track]');
        const progressBar = host.querySelector('[data-audio-progress-bar]');
        const playPauseBtn = host.querySelector('[data-audio-play-pause]');
        const playIcon = host.querySelector('[data-play-icon]');
        const pauseIcon = host.querySelector('[data-pause-icon]');
        const skipButtons = host.querySelectorAll('[data-audio-skip]');
        const playbackRateSelector = host.querySelector('[data-playback-rate-selector]');
        const volumeControl = host.querySelector('[data-volume-control]');
        const muteToggle = host.querySelector('[data-mute-toggle]');
        const volumeSlider = host.querySelector('[data-volume-slider]');

        const initialDuration = parseFloat(host.dataset.duration);
        if (Number.isFinite(initialDuration) && durationLabel) {
            durationLabel.textContent = formatClockTime(initialDuration);
        }

        const updateProgress = () => {
            if (!currentLabel || !progressBar || !progressTrack) {
                return;
            }
            currentLabel.textContent = formatClockTime(audio.currentTime);
            const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (Number.isFinite(initialDuration) ? initialDuration : NaN);
            const percent = Number.isFinite(duration) && duration > 0 ? Math.min((audio.currentTime / duration) * 100, 100) : 0;
            progressBar.style.width = `${percent}%`;
            progressTrack.setAttribute('aria-valuenow', String(Math.round(percent)));
        };

        const updateDuration = () => {
            if (!durationLabel) {
                return;
            }
            if (Number.isFinite(audio.duration) && audio.duration > 0) {
                durationLabel.textContent = formatClockTime(audio.duration);
            }
        };

        const togglePlay = () => {
            triggerHapticFeedback();
            if (audio.paused) {
                audio.play();
            } else {
                audio.pause();
            }
        };

        const updatePlayPauseIcon = () => {
            if (!playIcon || !pauseIcon) return;
            if (audio.paused) {
                playIcon.classList.remove('hidden');
                pauseIcon.classList.add('hidden');
            } else {
                playIcon.classList.add('hidden');
                pauseIcon.classList.remove('hidden');
            }
        };

        playPauseBtn.addEventListener('click', togglePlay);
        audio.addEventListener('play', updatePlayPauseIcon);
        audio.addEventListener('pause', updatePlayPauseIcon);

        skipButtons.forEach(button => {
            button.addEventListener('click', () => {
                triggerHapticFeedback();
                const skipTime = parseFloat(button.dataset.audioSkip);
                audio.currentTime = Math.max(0, audio.currentTime + skipTime);
            });
        });

        if (playbackRateSelector) {
            const rateButton = playbackRateSelector.querySelector('button');
            const rateOptions = playbackRateSelector.querySelector('div');
            rateButton.addEventListener('click', () => {
                rateOptions.classList.toggle('hidden');
            });
            rateOptions.addEventListener('click', (e) => {
                if (e.target.dataset.rate) {
                    const rate = parseFloat(e.target.dataset.rate);
                    audio.playbackRate = rate;
                    rateButton.textContent = `${rate}x`;
                    rateOptions.classList.add('hidden');
                }
            });
        }

        if (volumeControl) {
            muteToggle.addEventListener('click', () => {
                triggerHapticFeedback();
                audio.muted = !audio.muted;
            });

            volumeSlider.addEventListener('input', (e) => {
                audio.volume = parseFloat(e.target.value);
            });

            audio.addEventListener('volumechange', () => {
                volumeSlider.value = audio.volume;
                // You can also update mute toggle icon here based on volume and muted state
            });
        }
        
        const storageKey = `audio-progress-${host.dataset.source}`;
        
        audio.addEventListener('loadedmetadata', () => {
            const savedTime = localStorage.getItem(storageKey);
            if (savedTime) {
                audio.currentTime = parseFloat(savedTime);
            }
            updateDuration();
            updateProgress();
        });
        
        audio.addEventListener('timeupdate', () => {
            localStorage.setItem(storageKey, audio.currentTime);
            updateProgress();
        });
        
        audio.addEventListener('seeking', updateProgress);
        audio.addEventListener('ratechange', updateProgress);
        audio.addEventListener('ended', () => {
            if (progressBar) {
                progressBar.style.width = '100%';
            }
            if (progressTrack) {
                progressTrack.setAttribute('aria-valuenow', '100');
            }
            if (currentLabel && Number.isFinite(audio.duration)) {
                currentLabel.textContent = formatClockTime(audio.duration);
            }
            localStorage.removeItem(storageKey);
        });

        initMediaSession(audio, host);
    }

    document.addEventListener('DOMContentLoaded', () => {
        initBucketSelectors();
        initAsyncForms();
        initAudioPlayer();
    });
})();
