# Authentication Runbook: Local Development vs. Production

This runbook provides instructions and considerations for setting up and managing authentication in different environments.

## Local Development Setup

For local development, it is highly recommended to use the Firebase Emulator Suite to avoid hitting live Firebase services and incurring costs or rate limits.

### 1. Environment Variables (`.env`)

Create a `.env` file in your project root (copy from `.env.example`) and configure the following:

*   `ENV=development`
*   `AUTH_ENABLED=true`
*   `AUTH_BACKEND=firebase`
*   `FIREBASE_PROJECT_ID=your-firebase-project-id` (can be a dummy value if using emulators, but should match your `firebase.json` project ID)
*   `FIREBASE_WEB_API_KEY=your-firebase-web-api-key` (can be a dummy value)
*   `FIREBASE_AUTH_DOMAIN=localhost:9099` (or the host:port where your Auth emulator is running)
*   `ALLOWED_ORIGINS=http://localhost:5000,http://127.0.0.1:5000` (ensure these match your local Flask server)
*   `CANONICAL_HOST=localhost:5000` (or your local Flask server host:port)
*   `ADMIN_EMAILS=your-email@example.com` (for testing admin roles)

### 2. Firebase Emulator Suite

*   **Install Firebase CLI:** If you haven't already, install the Firebase CLI globally:
    ```bash
    npm install -g firebase-tools
    ```
*   **Login to Firebase:**
    ```bash
    firebase login
    ```
*   **Initialize Emulators:** In your Firebase project directory (where `firebase.json` is located), initialize the emulators:
    ```bash
    firebase init emulators
    ```
    Select "Authentication" and "Firestore".
*   **Start Emulators:**
    ```bash
    firebase emulators:start --only auth,firestore
    ```
    This will typically start the Auth emulator on `localhost:9099` and Firestore on `localhost:8080`.
*   **Configure Client-side to use Emulators:**
    *   The client-side Firebase SDK in `app/static/js/login.js` will automatically pick up `FIREBASE_AUTH_DOMAIN=localhost:9099` from the backend-provided config.
    *   For Firestore, ensure your `google.cloud.firestore.Client()` initialization (e.g., in `app/utils/firestore_session.py` or `app/services/users.py`) is configured to use the emulator if `FIRESTORE_EMULATOR_HOST` is set.

### 3. Run Flask Application

*   Activate your Python virtual environment:
    ```bash
    source .venv/bin/activate
    ```
*   Run the Flask development server:
    ```bash
    make dev
    ```
    Your app should be accessible at `http://localhost:5000`.

### 4. Grant Admin Role (for testing)

To test admin-only features, grant yourself an admin role in the emulator:

```bash
make grant-admin-role email=your-email@example.com
```

## Production Deployment (Google Cloud Run)

### 1. Environment Variables (Cloud Run)

Configure the following environment variables directly in your Cloud Run service settings:

*   `ENV=production`
*   `AUTH_ENABLED=true`
*   `AUTH_BACKEND=firebase`
*   `FIREBASE_PROJECT_ID=your-live-firebase-project-id`
*   `FIREBASE_WEB_API_KEY=your-live-firebase-web-api-key`
*   `FIREBASE_AUTH_DOMAIN=your-live-firebase-auth-domain` (e.g., `your-project.firebaseapp.com`)
*   `ALLOWED_ORIGINS=https://your-custom-domain.com,https://your-service-xxxxxx-uc.a.run.app` (comma-separated)
*   `CANONICAL_HOST=your-custom-domain.com`
*   `ADMIN_EMAILS=your-admin-email@example.com`
*   `FLASK_SESSION_COOKIE_SECURE=true` (highly recommended)
*   `SERVICE_ACCOUNT_EMAIL=your-cloud-run-service-account@your-project.iam.gserviceaccount.com` (for Cloud Tasks token verification)
*   `GCP_PROJECT_ID`, `GCP_REGION`, `GCS_BUCKET`, `SECRET_KEY`, `CSRF_SECRET_KEY`, `CLOUD_TASKS_QUEUE`, `CLOUD_TASKS_LOCATION`, `RATELIMIT_STORAGE_URI` (e.g., `firestore://rate_limits`)

### 2. Firebase Console Configuration

Ensure the Firebase Console settings (Authorized Domains, Sign-in methods) are correctly configured for your production domains as per `CHECKLIST.md`.

### 3. GCP Console Configuration

Ensure the GCP Console settings (OAuth Consent Screen, Credentials) are correctly configured for your production domains as per `CHECKLIST.md`.

### 4. Deployment

Deploy your application to Cloud Run using the `make deploy` command or via the GCP Console.

## Key Differences

| Feature                 | Local Development (Emulator)                               | Production (Live Firebase/GCP)                                   |
| :---------------------- | :--------------------------------------------------------- | :--------------------------------------------------------------- |
| **Firebase Services**   | Firebase Emulator Suite (Auth, Firestore)                  | Live Firebase Authentication, Firestore, etc.                    |
| **Domains/Origins**     | `localhost:5000`, `localhost:9099`                         | `your-custom-domain.com`, `*.run.app`, `your-project.firebaseapp.com` |
| **API Keys/IDs**        | Can use dummy values or emulator-specific configs          | Live Firebase project credentials                                |
| **Service Accounts**    | `GOOGLE_APPLICATION_CREDENTIALS` for local Admin SDK       | Cloud Run service account with appropriate IAM roles             |
| **Session Cookies**     | `SameSite=Lax` (or `None` if cross-site)                   | `Secure; HttpOnly; SameSite=Lax` (or `None` if cross-site)       |
| **CSP**                 | Configured for `localhost` and emulator domains            | Configured for production domains and Firebase services          |
