# Content Security Policy (CSP) Directives

This document details the Content Security Policy (CSP) directives implemented in Zissou and their rationale, particularly concerning Firebase Authentication.

## Implementation

CSP is enforced using `Flask-Talisman` in `app/__init__.py`. Directives are configured based on environment variables and Firebase requirements.

## Rationale

CSP helps mitigate Cross-Site Scripting (XSS) and other content injection attacks by specifying which sources of content (scripts, styles, images, etc.) are allowed to be loaded and executed by the browser.

For Firebase Authentication, specific domains must be whitelisted to allow the Firebase Web SDK to function correctly.

## Key Directives

The following are the core CSP directives configured:

*   **`default-src 'self'`**:
    *   **Rationale:** Default fallback for any resource type not explicitly defined. Restricts all content to come from the same origin as the document.
*   **`connect-src`**:
    *   `'self'`
    *   `https://securetoken.googleapis.com`: **Rationale:** Required for Firebase Authentication to exchange credentials and mint ID tokens.
    *   `https://identitytoolkit.googleapis.com`: **Rationale:** Used by Firebase Authentication for various identity-related operations.
    *   `https://www.googleapis.com`: **Rationale:** General Google APIs, potentially used by Firebase or other Google services.
    *   `https://firebaseinstallations.googleapis.com`: **Rationale:** Used by Firebase Installations for managing app installations.
    *   `CSP_ADDITIONAL_CONNECT_SRC` (from `.env`): **Rationale:** Allows administrators to add custom domains for `connect-src` if needed for other services.
*   **`script-src`**:
    *   `'self'`
    *   `https://www.gstatic.com`: **Rationale:** Hosts Firebase Web SDK files (e.g., `firebase-app.js`, `firebase-auth.js`).
    *   `https://apis.google.com`: **Rationale:** Used by Google Sign-In library (GSI) and other Google APIs.
    *   `CSP_ADDITIONAL_SCRIPT_SRC` (from `.env`): **Rationale:** Allows adding other trusted script sources.
*   **`style-src`**:
    *   `'self'`
    *   `data:`: **Rationale:** Allows inline styles that use data URIs.
    *   `CSP_ADDITIONAL_STYLE_SRC` (from `.env`): **Rationale:** Allows adding other trusted style sources.
*   **`img-src 'self' data:`**:
    *   **Rationale:** Allows images from the same origin and data URIs (e.g., for small embedded images).
*   **`font-src 'self' https://fonts.gstatic.com`**:
    *   **Rationale:** Allows fonts from the same origin and Google Fonts.
*   **`frame-src`**:
    *   `'self'`
    *   `https://apis.google.com`: **Rationale:** Used by Google Sign-In if it uses iframes for authentication.
    *   `https://{FIREBASE_AUTH_DOMAIN}`: **Rationale:** Dynamically added if `FIREBASE_AUTH_DOMAIN` is configured, essential for Firebase Authentication popups/redirects that might use iframes.
*   **`base-uri 'self'`**:
    *   **Rationale:** Restricts the URLs that can be used in a document's `<base>` element.
*   **`form-action 'self'`**:
    *   **Rationale:** Restricts the URLs that can be used as the target for form submissions.

## Reporting

CSP violation reports can be configured using the `CSP_REPORT_URI` and `CSP_REPORT_TO_ENDPOINT` environment variables. This allows monitoring and identifying potential CSP bypasses or legitimate content being blocked.
