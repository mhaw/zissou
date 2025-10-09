# Firebase & GCP Console Settings Checklist

This checklist outlines the necessary configurations in the Firebase Console and Google Cloud Platform (GCP) for authentication to function correctly.

## Firebase Console (https://console.firebase.google.com/)

### 1. Authentication -> Sign-in method

*   **Google:**
    *   Ensure "Google" provider is enabled.
    *   Verify the associated project support email is correct.
*   **Email/Password (Optional):**
    *   If email/password sign-in is desired, ensure it is enabled.
*   **Other Providers (Optional):**
    *   Enable any other authentication providers as needed.

### 2. Authentication -> Settings -> Authorized domains

*   Add all domains where your application will be hosted:
    *   `localhost` (for local development)
    *   `*.run.app` (for Google Cloud Run default URLs)
    *   `your-custom-domain.com` (e.g., `zissou-audio.com`)
    *   Ensure `FIREBASE_AUTH_DOMAIN` from your `.env` is correctly reflected here.

### 3. Project Settings -> General -> Your apps -> Web app

*   Locate your web app's configuration snippet.
*   Verify that the `apiKey`, `authDomain`, and `projectId` match the values set in your `.env` file for `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`, and `FIREBASE_PROJECT_ID` respectively.

## Google Cloud Platform (GCP) Console (https://console.cloud.google.com/)

### 1. APIs & Services -> OAuth Consent Screen

*   **User Type:** Ensure this is correctly configured (e.g., "External" for public apps, "Internal" for organization-only apps).
*   **Authorized domains:**
    *   Add all domains where your application will be hosted (same as Firebase Authorized Domains).
*   **Scopes:** Ensure necessary scopes are requested (e.g., `...auth/userinfo.email`, `...auth/userinfo.profile`).

### 2. APIs & Services -> Credentials

*   **OAuth 2.0 Client IDs (Web application):**
    *   If using Google Sign-In via an OAuth client (not just Firebase's built-in Google provider), verify:
        *   **Authorized JavaScript origins:** Must include all origins where your app runs (e.g., `http://localhost:5000`, `https://your-service-xxxxxx-uc.a.run.app`, `https://zissou-audio.com`).
        *   **Authorized redirect URIs:** Must include all paths where Google will redirect after authentication (e.g., `http://localhost:5000/auth/callback`, `https://your-service-xxxxxx-uc.a.run.app/auth/callback`, `https://zissou-audio.com/auth/callback`).
*   **Service Accounts:**
    *   Ensure the service account used for `GOOGLE_APPLICATION_CREDENTIALS` (if set) has the necessary permissions (e.g., Firebase Admin SDK access, Firestore access).

### 3. Cloud Run Service

*   **Authentication:** Ensure your Cloud Run service is configured to allow unauthenticated invocations if you are handling authentication within the application itself (i.e., Firebase Auth). If using IAP, it would be set to require authentication.
*   **Environment Variables:** Verify that all required environment variables (e.g., `FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `ALLOWED_ORIGINS`, `CANONICAL_HOST`, `ADMIN_EMAILS`) are correctly set in your Cloud Run service configuration.
