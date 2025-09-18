(function() {
    function appendHtml(target, html) {
        if (!target || !html) {
            return;
        }
        const template = document.createElement('template');
        template.innerHTML = html.trim();
        const fragment = template.content;
        target.appendChild(fragment);
    }

    function show(element, show = true) {
        if (!element) {
            return;
        }
        element.classList.toggle('hidden', !show);
    }

    function handleError(errorElement, message) {
        if (!errorElement) {
            return;
        }
        errorElement.textContent = message;
        show(errorElement, true);
    }

    function initContainer(container) {
        if (!container) {
            return;
        }
        const button = container.querySelector('[data-load-more-button]');
        const grid = container.querySelector('[data-load-more-grid]');
        const spinner = container.querySelector('[data-load-more-spinner]');
        const errorElement = container.querySelector('[data-load-more-error]');

        if (!button || !grid) {
            return;
        }

        button.addEventListener('click', async () => {
            const nextUrl = button.getAttribute('data-next-url');
            if (!nextUrl || button.dataset.loading === 'true') {
                return;
            }
            button.dataset.loading = 'true';
            button.disabled = true;
            show(errorElement, false);
            show(spinner, true);

            try {
                const response = await fetch(nextUrl, {
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    credentials: 'same-origin'
                });
                if (!response.ok) {
                    throw new Error(`Request failed with status ${response.status}`);
                }
                const payload = await response.json();
                if (payload.items_html) {
                    appendHtml(grid, payload.items_html);
                }
                if (payload.next_url) {
                    button.setAttribute('data-next-url', payload.next_url);
                    button.disabled = false;
                } else {
                    button.remove();
                    show(spinner, false);
                }
                if (!payload.next_url) {
                    const wrapper = container.querySelector('[data-load-more-wrapper]');
                    if (wrapper && !wrapper.querySelector('[data-load-more-button]')) {
                        const spinnerNode = wrapper.querySelector('[data-load-more-spinner]');
                        show(spinnerNode, false);
                    }
                }
            } catch (error) {
                console.error('Failed to load more items', error);
                button.disabled = false;
                handleError(errorElement, 'Unable to load more items. Please try again.');
            } finally {
                delete button.dataset.loading;
                show(spinner, false);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document
            .querySelectorAll('[data-load-more-container]')
            .forEach(initContainer);
    });
})();
