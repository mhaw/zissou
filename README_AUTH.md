# Firebase Authentication for Zissou

This document captures the narrow set of changes required to enable Google-only sign-in through Firebase Authentication while keeping the rollout safe and reversible.

## Prerequisites
- Firebase project enabled for Authentication (Google provider only).
- The Firebase web app configuration (API key, auth domain, project ID).
- Application Default Credentials available in the runtime (Cloud Run provides this automatically).
- Populate `.env` (or Cloud Run environment variables) with the keys listed in `.env.example`).

## Content Security Policy (CSP)
To ensure the security of the application, a Content Security Policy is enforced using Flask-Talisman. This policy helps mitigate Cross-Site Scripting (XSS) attacks by specifying which sources of content are allowed to be loaded and executed by the browser.

**Key Directives for Firebase Authentication:**
- `script-src`: Allows scripts from `'self'`, `https://www.gstatic.com` (Firebase JS SDK), `https://apis.google.com` (federated auth iframe), and `https://unpkg.com` (htmx CDN). Inline scripts are permitted via dynamically generated nonces.
- `connect-src`: Allows connections to `'self'`, `https://securetoken.googleapis.com`, `https://identitytoolkit.googleapis.com`, `https://www.googleapis.com`, and `https://firebaseinstallations.googleapis.com`. Add more origins (e.g., Firestore REST) via `CSP_ADDITIONAL_CONNECT_SRC` if needed.
- `frame-src`: Limited to `'self'`, `https://apis.google.com`, and your configured `FIREBASE_AUTH_DOMAIN` to enable the Google sign-in bridge.
- `report-uri`: Set through `CSP_REPORT_URI`; when present, violations are forwarded externally instead of the legacy `/csp-violation-report` endpoint.
- `Report-To`: Controlled by `CSP_REPORT_TO` and `CSP_REPORT_TO_ENDPOINT` to surface structured violation reports (defaults to the same endpoint as `CSP_REPORT_URI`).

**Nonce Implementation:**
Inline `<script>` tags are secured using nonces. Flask-Talisman automatically generates a unique nonce for each request and injects it into the `script-src` header. All inline scripts in the templates must include `nonce="{{ csp_nonce() }}"` to be executed.


## Local Setup
1. Copy `.env.example` to `.env` and fill in:
   - `FLASK_SECRET_KEY`
   - `FIREBASE_PROJECT_ID`
   - `FIREBASE_WEB_API_KEY`
   - `FIREBASE_AUTH_DOMAIN`
   - Optional: override `FLASK_SESSION_COOKIE_NAME`; defaults to `flask_session`.
   - Optional: set `FLASK_SESSION_COOKIE_SECURE=false` for local HTTP testing only.
2. Set `AUTH_ENABLED=true` once you are ready to exercise the login flow. Leaving it `false` keeps all routes public.
3. Install dependencies: `make setup` (or `pip install -r requirements.txt`).
4. Run the dev server: `make dev`.
5. Visit `http://localhost:8080/dashboard` → you should be redirected to `/auth/login`.
6. Complete Google sign-in, inspect the session cookie, then retry `/dashboard` to confirm access.

### Note on Secure Cookies
The Firebase session cookie (`fb_session`) is always issued with the `Secure`, `HttpOnly`, `SameSite=None`, and `Path=/` attributes. When developing over plain HTTP, Chrome may not persist it. If that blocks testing, temporarily proxy through HTTPS. The Flask session cookie (`flask_session`) can be relaxed locally by exporting `FLASK_SESSION_COOKIE_SECURE=false` before starting the app.

## Cloud Run Deployment Checklist
1. Ensure the Cloud Run service account has permission to verify Firebase tokens (default roles are sufficient for most projects).
2. Set the following variables when deploying:
   - `FLASK_SECRET_KEY`
   - `FIREBASE_PROJECT_ID`
   - `FIREBASE_WEB_API_KEY`
   - `FIREBASE_AUTH_DOMAIN`
   - `FLASK_SESSION_COOKIE_NAME=flask_session`
   - `FLASK_SESSION_COOKIE_SECURE=true`
   - `AUTH_ENABLED=true`
    - Optional CSP hardening:
      - `CSP_REPORT_URI=https://csp-reporting.example.com/collect`
      - `CSP_REPORT_TO_ENDPOINT=https://csp-reporting.example.com/collect`
      - `CSP_ADDITIONAL_CONNECT_SRC="https://firestore.googleapis.com"`
      - `CSP_ADDITIONAL_SCRIPT_SRC="https://www.googletagmanager.com"`
      - `CSP_ADDITIONAL_STYLE_SRC="https://fonts.googleapis.com"`
3. Keep the Cloud Run ingress open to unauthenticated traffic; enforcement happens in-app.
4. Deploy as usual (`./infra/deploy_cloud_run.sh`).

## Rollout Strategy & Monitoring
- Start with `AUTH_ENABLED=false` in production. Toggle to `true` once Firebase config is verified.
- Cloud Logging will include `Firebase session created` entries containing the user UID and email for auditing.
- Consider enabling structured logging sinks or alerts if repeated failures appear in `/auth/sessionLogin`.

## Rollback Plan
1. Set `AUTH_ENABLED=false` and redeploy to immediately disable gating.
2. If the issue persists, roll back the deployment or remove the blueprint registration for `app.routes.auth`.
3. Cookies are HttpOnly; users will need to re-authenticate once auth is re-enabled.

## Troubleshooting
- **`session cookie create failed`**: Usually indicates mismatched Firebase project settings or stale ID tokens. Confirm the web app uses the same project as the Admin SDK.
- **Redirect loops**: Check that `AUTH_ENABLED` is set to `true` in all environments and that the session cookie domain matches the deployed hostname.
- **Cookie missing in local dev**: Chrome refuses `Secure` cookies on plain HTTP. Use an HTTPS tunnel or temporarily relax `FLASK_SESSION_COOKIE_SECURE` only for local testing.

## Verification Checklist
Use the manual test commands listed in the task hand-off (also captured separately in the main testing checklist) before and after deployment.
