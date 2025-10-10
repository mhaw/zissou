(function () {
    const CSRF_META_SELECTOR = 'meta[name="csrf-token"]';

    function getCsrfToken() {
        const meta = document.querySelector(CSRF_META_SELECTOR);
        return meta ? meta.getAttribute('content') : '';
    }

    window.Zissou = window.Zissou || {};
    window.Zissou.getCsrfToken = getCsrfToken;
    const COLOR_CLASSES = [
        'border-sky-200 bg-sky-50 text-sky-700',
        'border-violet-200 bg-violet-50 text-violet-700',
        'border-emerald-200 bg-emerald-50 text-emerald-700',
        'border-amber-200 bg-amber-50 text-amber-700',
        'border-rose-200 bg-rose-50 text-rose-700',
        'border-teal-200 bg-teal-50 text-teal-700',
        'border-indigo-200 bg-indigo-50 text-indigo-700',
        'border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700',
        'border-lime-200 bg-lime-50 text-lime-700',
        'border-slate-200 bg-slate-50 text-slate-700'
    ];

    function hashTag(tag) {
        let hash = 0;
        for (let i = 0; i < tag.length; i += 1) {
            hash = (hash << 5) - hash + tag.charCodeAt(i);
            hash |= 0;
        }
        return Math.abs(hash);
    }

    function colorClassFor(tag) {
        if (!tag) {
            return 'border-slate-200 bg-slate-100 text-slate-700';
        }
        const index = hashTag(tag.toLowerCase()) % COLOR_CLASSES.length;
        return COLOR_CLASSES[index];
    }

    const registry = {
        instances: new Set(),
        register(instance) {
            this.instances.add(instance);
        },
        unregister(instance) {
            this.instances.delete(instance);
        },
        clearByMode(mode) {
            this.instances.forEach((instance) => {
                if (!mode || instance.mode === mode) {
                    instance.clear();
                }
            });
        }
    };

    function normaliseTag(tag, available) {
        if (!tag) {
            return '';
        }
        const lower = tag.toLowerCase();
        const match = available.find((candidate) => candidate.toLowerCase() === lower);
        return match || tag;
    }

    class TagSelector {
        constructor(root) {
            this.root = root;
            this.mode = root.dataset.mode || 'edit';
            this.fieldName = root.dataset.fieldName || 'tags';
            this.available = Array.from(new Set(JSON.parse(root.dataset.allTags || '[]')));
            this.selected = new Set(JSON.parse(root.dataset.selectedTags || '[]'));

            this.chipList = root.querySelector('[data-tag-chip-list]');
            this.input = root.querySelector('[data-tag-input]');
            this.suggestions = root.querySelector('[data-tag-suggestions]');
            this.hiddenInputs = root.querySelector('[data-tag-hidden-inputs]');
            this.activeSuggestionIndex = -1;

            this.handleOutsideClick = this.handleOutsideClick.bind(this);
            this.bindEvents();
            this.render();
            registry.register(this);
        }

        bindEvents() {
            if (!this.input) {
                return;
            }
            this.input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ',' || event.key === 'Tab') {
                    event.preventDefault();
                    this.commitFromInput();
                } else if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    this.highlightNextSuggestion();
                } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    this.highlightPreviousSuggestion();
                } else if (event.key === 'Escape') {
                    this.hideSuggestions();
                }
            });

            this.input.addEventListener('input', () => {
                this.updateSuggestions();
            });

            this.suggestions?.addEventListener('mousedown', (event) => {
                const button = event.target.closest('button[data-tag-value]');
                if (!button) {
                    return;
                }
                event.preventDefault();
                const value = button.getAttribute('data-tag-value');
                this.addTag(value);
            });

            this.chipList?.addEventListener('click', (event) => {
                const chip = event.target.closest('button[data-remove-tag]');
                if (!chip) {
                    return;
                }
                event.preventDefault();
                const value = chip.getAttribute('data-remove-tag');
                this.removeTag(value);
            });

            document.addEventListener('click', this.handleOutsideClick);
        }

        destroy() {
            document.removeEventListener('click', this.handleOutsideClick);
            registry.unregister(this);
        }

        handleOutsideClick(event) {
            if (!this.root.contains(event.target)) {
                this.hideSuggestions();
            }
        }

        commitFromInput() {
            const value = (this.input?.value || '').trim();
            if (!value) {
                const highlighted = this.suggestions?.querySelector('button[data-tag-value].is-active');
                if (highlighted) {
                    this.addTag(highlighted.getAttribute('data-tag-value'));
                }
                return;
            }
            const normalised = normaliseTag(value, this.available);
            this.addTag(normalised);
        }

        addTag(tag) {
            const clean = tag ? tag.trim() : '';
            if (!clean) {
                this.resetInput();
                return;
            }
            const normalised = normaliseTag(clean, this.available);
            if (!this.available.includes(normalised)) {
                this.available.push(normalised);
            }
            this.selected.add(normalised);
            this.render();
            this.resetInput();
        }

        removeTag(tag) {
            if (!tag) {
                return;
            }
            this.selected.delete(tag);
            this.render();
        }

        clear() {
            this.selected.clear();
            this.render();
            this.resetInput();
        }

        resetInput() {
            if (this.input) {
                this.input.value = '';
            }
            this.hideSuggestions();
        }

        render() {
            this.renderChips();
            this.renderHiddenInputs();
            this.updateSuggestions();
        }

        renderChips() {
            if (!this.chipList) {
                return;
            }
            this.chipList.innerHTML = '';
            this.selected.forEach((tag) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.setAttribute('data-remove-tag', tag);
                button.className = `inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold transition ${colorClassFor(tag)}`;
                button.innerHTML = `
                    <span>${tag}</span>
                    <svg class="h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                `;
                this.chipList.appendChild(button);
            });
            if (!this.selected.size && this.mode === 'filter') {
                const placeholder = document.createElement('span');
                placeholder.className = 'rounded-full border border-dashed border-slate-200 px-3 py-1 text-xs text-slate-400';
                placeholder.textContent = 'No tags selected';
                this.chipList.appendChild(placeholder);
            }
        }

        renderHiddenInputs() {
            if (!this.hiddenInputs) {
                return;
            }
            this.hiddenInputs.innerHTML = '';
            this.selected.forEach((tag) => {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = this.fieldName;
                input.value = tag;
                this.hiddenInputs.appendChild(input);
            });
        }

        updateSuggestions() {
            if (!this.suggestions || !this.input) {
                return;
            }
            const query = this.input.value.trim().toLowerCase();
            const options = this.available
                .filter((tag) => !this.selected.has(tag))
                .filter((tag) => tag.toLowerCase().includes(query))
                .slice(0, 6);

            this.suggestions.innerHTML = '';
            if (!query && !options.length) {
                this.hideSuggestions();
                return;
            }

            options.forEach((tag, index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-blue-50';
                button.setAttribute('data-tag-value', tag);
                if (index === 0) {
                    button.classList.add('is-active');
                    this.activeSuggestionIndex = 0;
                }
                button.innerHTML = `
                    <span class="inline-flex h-2.5 w-2.5 rounded-full ${colorClassFor(tag)}"></span>
                    <span>${tag}</span>
                `;
                this.suggestions.appendChild(button);
            });

            if (options.length) {
                this.suggestions.classList.remove('hidden');
            } else {
                this.hideSuggestions();
            }
        }

        hideSuggestions() {
            if (!this.suggestions) {
                return;
            }
            this.suggestions.classList.add('hidden');
            this.suggestions.innerHTML = '';
            this.activeSuggestionIndex = -1;
        }

        highlightNextSuggestion() {
            this.adjustHighlightedSuggestion(1);
        }

        highlightPreviousSuggestion() {
            this.adjustHighlightedSuggestion(-1);
        }

        adjustHighlightedSuggestion(direction) {
            if (!this.suggestions || this.suggestions.classList.contains('hidden')) {
                return;
            }
            const buttons = Array.from(this.suggestions.querySelectorAll('button[data-tag-value]'));
            if (!buttons.length) {
                return;
            }
            this.activeSuggestionIndex = (this.activeSuggestionIndex + direction + buttons.length) % buttons.length;
            buttons.forEach((button, index) => {
                if (index === this.activeSuggestionIndex) {
                    button.classList.add('is-active', 'bg-blue-50');
                } else {
                    button.classList.remove('is-active', 'bg-blue-50');
                }
            });
        }
    }

    function initTagSelectors(root) {
        (root || document).querySelectorAll('[data-tag-selector]').forEach((element) => {
            if (!element.__tagSelector) {
                element.__tagSelector = new TagSelector(element);
            }
        });
    }

    window.TagSelectorRegistry = registry;

    document.addEventListener('DOMContentLoaded', () => {
        initTagSelectors(document);
    });

    if (window.htmx) {
        document.body.addEventListener('htmx:afterSwap', (event) => {
            initTagSelectors(event.target);
        });
        document.body.addEventListener('htmx:beforeCleanupElement', (event) => {
            const element = event.target;
            if (element.__tagSelector) {
                element.__tagSelector.destroy();
            }
        });
    }
})();
