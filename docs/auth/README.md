# Authentication Overview

This document provides an overview of the authentication system in Zissou, which leverages Firebase Authentication for user management and session handling.

## Authentication Flow

The primary authentication flow involves Google Sign-In, with server-side session management.

1.  **Client-side Initiation:**
    *   A user navigates to the `/auth/login` page.
    *   The client-side JavaScript (`app/static/js/login.js`) initializes the Firebase Web SDK using configuration provided by the Flask backend.
    *   The user clicks "Sign in with Google," triggering `signInWithPopup`.
2.  **Firebase Authentication:**
    *   Firebase handles the Google OAuth flow, authenticating the user.
    *   Upon successful authentication, Firebase returns an ID Token to the client.
3.  **Server-side Session Creation:**
    *   The client sends the Firebase ID Token to the Flask backend's `/auth/token` endpoint via a POST request.
    *   The Flask backend uses the Firebase Admin SDK to verify the ID Token.
    *   If valid, a server-side session cookie (`__Host-fb_session`) is minted and set on the user's browser.
    *   The user is then redirected to the requested page (or `/`).
4.  **Authenticated Access:**
    *   For subsequent requests to protected routes, the server verifies the session cookie.
    *   The `g.user` object is populated with user details (UID, email, roles).
    *   Routes protected by `@auth_required` or `@role_required` decorators enforce authentication and authorization based on `g.user`.

## Common Errors and Troubleshooting

*   **Login Loops:**
    *   **Cause:** Often due to misconfigured redirect URIs, authorized domains, or CSP/CORS issues preventing Firebase SDK from communicating or the server from setting cookies.
    *   **Fix:**
        *   Verify Firebase Console settings (Authorized Domains, OAuth Redirect URIs).
        *   Check `ALLOWED_ORIGINS` in `.env` for correct CORS configuration.
        *   Inspect browser console for CSP errors.
*   **CSP Blocks:**
    *   **Cause:** Browser's Content Security Policy preventing scripts, styles, or connections to Firebase domains.
    *   **Fix:** Review `CSP.md` and ensure all necessary Firebase domains are whitelisted in `connect-src`, `script-src`, and `frame-src`. Use `CSP_ADDITIONAL_CONNECT_SRC`, etc., in `.env` if custom domains are needed.
*   **401/403 Errors on Protected Routes:**
    *   **Cause:** User is unauthenticated (401) or lacks required roles (403).
    *   **Fix:**
        *   Ensure the user is properly signed in.
        *   For admin routes, verify the user's email is in `ADMIN_EMAILS` in `.env` and that the admin claim is set (use `make grant-admin-role`).
        *   Check server logs for detailed authentication/authorization failures.

## Local Development with Firebase Emulators

It is highly recommended to use the Firebase Emulator Suite for local development and testing. Refer to `RUNBOOK.md` for detailed setup instructions.
