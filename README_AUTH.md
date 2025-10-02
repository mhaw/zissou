# Google IAP Authentication for Zissou

Zissou now relies on Google Cloud Identity-Aware Proxy (IAP) to protect the app. IAP owns the login flow, issues Google-hosted OAuth redirects, and injects the authenticated user into each request via trusted headers. The Flask app simply reads those headers, assigns roles based on `ADMIN_EMAILS`, and denies access when IAP is not present.

If you ever need to fall back to Firebase Authentication, leave `AUTH_BACKEND=firebase` and follow the compatibility notes at the end of this document. Otherwise, the steps below describe the IAP-first path.

## Quick Start
1. Copy `.env.example` to `.env`.
2. Set `AUTH_BACKEND=iap` (default) and `AUTH_ENABLED=true` in the environment where IAP protects the service. For local development without IAP, set `AUTH_ENABLED=false` to bypass the guard rails.
3. Provide a comma-separated `ADMIN_EMAILS` list. Any email in the list receives admin privileges once IAP authenticates the user.
4. Deploy to Cloud Run and enable IAP on the service.
5. Confirm that requests include the `X-Goog-Authenticated-User-Email` header. When present, the app exposes the user context and enforces access checks; when missing, the app returns HTTP 401 and the Cloud Run log message explains that IAP is required.

## Enabling IAP for Cloud Run
1. Make sure your Cloud Run service is deployed and accessible. Keep ingress open to unauthenticated traffic because IAP sits in front and handles auth.
2. Grant the **IAP-Secured Web App User** role to the Google Workspace group (or individual accounts) that should reach Zissou.
3. In the Google Cloud Console, navigate to **Security ➜ Identity-Aware Proxy**, locate the Cloud Run service, and toggle IAP **ON**.
4. Visit the service URL. Google will redirect to the hosted OAuth screen. After sign-in, the request carries headers such as:
   - `X-Goog-Authenticated-User-Email: accounts.google.com:alice@example.com`
   - `X-Goog-Authenticated-User-Id: accounts.google.com:1234567890`
5. Verify Cloud Logging shows `auth_required_failure` messages only when you intentionally hit the app without IAP.

### Role Mapping
- Zissou grants the `admin` role when the parsed email (everything after the `:` in the header) is present in `ADMIN_EMAILS`.
- All other authenticated users receive the `member` role.
- The header email is displayed in the UI (see the navigation bar), and admin-only pages remain gated by `role_required("admin")`.

## Local Development
By default, local development runs without IAP. Use one of the following modes:
- **Simulate unauthenticated access**: keep `AUTH_ENABLED=false` to bypass guards entirely.
- **Simulate authenticated users**: send the IAP headers when using an HTTP client (`curl -H "X-Goog-Authenticated-User-Email: accounts.google.com:dev@example.com" ...`).
- **End-to-end IAP**: tunnel your local server through Cloud Run or Cloud HTTPS Load Balancing with IAP configured; Zissou will accept the headers exactly as it does in production.

## Cookie & Session Notes
- IAP manages session state; Zissou no longer stores Firebase ID tokens in `localStorage`, nor does it mint Firebase session cookies.
- The `Sign in` button and Firebase login template remain available only when `AUTH_BACKEND=firebase`. For IAP they return 404.
- `AUTH_BACKEND=iap` means the `/auth/token` endpoint is disabled and the header-driven flow is authoritative.

## Auditing
Relevant log events:
- `auth_required_failure` – A protected endpoint was hit without IAP headers.
- `role_required_failure` – The user was authenticated but lacked the necessary role (e.g., non-admin hitting `/admin`).

Combine these with Cloud Audit Logs from IAP for a full picture of who accessed or attempted to access the service.

## Optional: Firebase Compatibility
If you need to reintroduce Firebase Authentication:
1. Set `AUTH_BACKEND=firebase` and keep `AUTH_ENABLED=true`.
2. Populate `FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, and `FIREBASE_AUTH_DOMAIN`.
3. Re-enable the Firebase login flow—the `/auth/login` route and associated JS bundle become active again.
4. Review the legacy guidance in earlier commits if you need help wiring the Firebase session cookie (the code paths are still present but dormant when using IAP).

Reverting to Firebase requires redeploying with those configuration changes; no code modifications are necessary.
