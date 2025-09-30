# Firebase Authentication for Zissou

This document captures the narrow set of changes required to enable Google-only sign-in through Firebase Authentication while keeping the rollout safe and reversible.

## Prerequisites
- Firebase project enabled for Authentication (Google provider only).
- The Firebase web app configuration (API key, auth domain, project ID).
- Application Default Credentials available in the runtime (Cloud Run provides this automatically).
- Populate `.env` (or Cloud Run environment variables) with the keys listed in `.env.example`.

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
