# Zissou Authentication Guide

Zissou uses a built-in authentication system powered by **Firebase Authentication**. This approach is cost-effective and self-contained, allowing the application to manage its own users and sessions without requiring additional cloud infrastructure like load balancers.

For local development or simple deployments, this is the recommended and default method.

## Quick Start: Firebase Authentication
1.  Copy `.env.example` to `.env`.
2.  Ensure `AUTH_BACKEND=firebase` and `AUTH_ENABLED=true` are set in your `.env` file.
3.  Create a Firebase project and a Web App within it to get your project credentials.
4.  Add your Firebase credentials to the `.env` file:
    - `FIREBASE_PROJECT_ID`
    - `FIREBASE_WEB_API_KEY`
    - `FIREBASE_AUTH_DOMAIN`
5.  Provide a comma-separated `ADMIN_EMAILS` list. Any user signing in with an email on this list will be granted `admin` privileges.
6.  Deploy the application. Users will be directed to the app's own login page.

## Local Development
By default, local development can run without authentication checks.
- **Simulate unauthenticated access**: Set `AUTH_ENABLED=false` in your `.env` file to bypass all authentication and authorization checks. This is the easiest way to work on UI and other features.
- **Test with authentication**: Set `AUTH_ENABLED=true` and ensure your Firebase environment variables are configured. You will be redirected to the login page just like in production.

## Cookie & Session Notes
- When using Firebase auth, the application creates a session cookie after a successful login. The user's Firebase ID token is stored to manage the session.
- The `/auth/login` route, `/auth/token` endpoint, and associated JavaScript are all active when `AUTH_BACKEND=firebase`.

## Auditing
Relevant log events for the Firebase backend:
- `auth_required_failure` – A protected endpoint was hit without a valid session cookie.
- `role_required_failure` – The user was authenticated but lacked the necessary role (e.g., non-admin hitting `/admin`).

---

## Advanced Option: Google Cloud Identity-Aware Proxy (IAP)

For environments requiring infrastructure-level perimeter security, Zissou supports Google Cloud IAP. In this mode, Zissou delegates the entire login flow to Google's infrastructure. IAP authenticates the user, and then injects the user's verified identity into request headers that the Flask app trusts.

**This is an advanced setup that requires an External HTTPS Load Balancer and will incur additional costs.**

### Enabling IAP
1.  **Set Environment Variable**: Change your environment variable to `AUTH_BACKEND=iap`.
2.  **Deploy with a Load Balancer**: Deploy your Cloud Run service and place it behind an External HTTPS Load Balancer. The service ingress should be set to `internal-and-cloud-load-balancing`.
3.  **Enable IAP**: In the Google Cloud Console, navigate to **Security ➜ Identity-Aware Proxy**, find the load balancer's backend service, and toggle IAP **ON**.
4.  **Grant Access**: Grant the **IAP-Secured Web App User** role to the Google accounts or groups that should have access.
5.  **App Behavior**: When a request arrives, the app will look for the `X-Goog-Authenticated-User-Email` header to identify the user. If the header is missing, it will return an HTTP 401 error. The app's own login pages are disabled.

### Role Mapping with IAP
- Zissou grants the `admin` role when the user's email (from the header) is in the `ADMIN_EMAILS` list.
- All other authenticated users receive the `member` role.