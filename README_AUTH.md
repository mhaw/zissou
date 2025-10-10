# Zissou Authentication Guide

Zissou uses a built-in authentication system powered by **Firebase Authentication**. This approach is cost-effective and self-contained, allowing the application to manage its own users and sessions.

For local development or simple deployments, this is the recommended and default method.

## Quick Start: Firebase Authentication
1.  Copy `.env.example` to `.env`.
2.  Ensure `AUTH_ENABLED=true` is set in your `.env` file.
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
- The application creates a session cookie after a successful login. The user's Firebase ID token is stored to manage the session.
- The `/auth/login` route, `/auth/token` endpoint, and associated JavaScript are all active when using Firebase authentication.

## Auditing
Relevant log events for the Firebase backend:
- `auth_required_failure` – A protected endpoint was hit without a valid session cookie.
- `role_required_failure` – The user was authenticated but lacked the necessary role (e.g., non-admin hitting `/admin`).
