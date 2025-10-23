# Zissou: The Belafonte Boombox

Zissou turns articles into your own personal podcast. Provide a URL, and it will extract the text, convert it to high-quality audio, and generate a private RSS feed you can add to your favorite podcast player.

- Optional AI summaries and auto-tagging keep Firestore items enriched without delaying TTS.
- A dark mode toggle in the navbar respects system defaults and persists per browser.

## Architecture Overview

The application is a Python Flask web server that runs on Google Cloud Run. It uses Google Cloud Tasks for asynchronous background processing.

```
+----------------------------------------------------------------------+
|                      User via Browser/Podcast App                    |
+----------------------------------------------------------------------+
      | (1. Submit URL)                         ^ (7. Subscribe to RSS)
      v                                         |
+----------------------------------------------------------------------+
|                  Zissou Application (Flask on Cloud Run)             |
|                                                                      |
| +------------------+      +------------------+      +----------------+ |
| |  Web UI/Routes   |----->|  Firestore       |<-----|  Feed Service  | |
| | (Submit, Admin)  |      |  (Task &         |      |  (Generate RSS)| |
| +------------------+      |   Item Metadata) |      +----------------+ |
|         |                 +------------------+               ^         |
|         | (2. Enqueue Task)                                  | (6. Read)
|         v                                                    |         |
| +------------------------------------------------------------+         |
| |                  Google Cloud Tasks (Task Queue)           |         |
| +------------------------------------------------------------+         |
|         | (3. Push Task)                                     |         |
|         v                                                    |         |
| +------------------+                                         |         |
| | Task Handler     | (4. Parse, TTS)   (5. Store Audio & Metadata)     |
| | (/tasks/process) |----------------------------------------+         |
| +------------------+                                                   |
|         |                                                              |
|         +----->[Google Text-to-Speech]--->[Google Cloud Storage]<------+
|                                                                      |
+----------------------------------------------------------------------+
```

## Getting Started

### Local Development (Prerequisites)

- Python 3.11+
- `gcloud` CLI authenticated (`gcloud auth login`)
- Docker and Docker Compose (Optional, for running with production-like environment)

### 1. Initial Setup

Clone the repository and set up the environment.

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your environment file
# Edit this file with your GCP_PROJECT_ID, GCS_BUCKET, and a new SECRET_KEY
cp .env.example .env
```

For local development, ensure `ENV` is set to `development` in your `.env` file. In this mode, article processing happens **synchronously** upon submission, and does not use the Cloud Tasks queue.

### 2. Running Locally

This is the best method for active development.

```bash
# Make sure your .env file is configured
make dev

# Or run manually:
# flask --app app/main.py run -p 8080
```

The app will be available at `http://localhost:8080`.

## Testing

### Backend unit tests

Run the Flask unit test suite with:

```bash
make test
```

### Playwright end-to-end checks

Install the browser binaries once per machine and execute the Playwright scenarios. These tests expect a local server on `http://localhost:8080`; start it separately with `make dev` or set `PLAYWRIGHT_BASE_URL` to point at another environment.

```bash
npx playwright install
npm run test:e2e
```

HTML reports are written to `reports/playwright`.

### Frontend API calls & CSRF

All authenticated AJAX calls must include the Flask-WTF CSRF token. The base layout exposes it via:

```html
<meta name="csrf-token" content="{{ csrf_token() }}">
```

Client scripts should read this value (see `window.Zissou.getCsrfToken()`), send it as the `X-CSRFToken` header, and opt into `credentials: 'include'` on `fetch` so session cookies accompany the request. The bucket and tag editors already follow this pattern—reuse the helper for any new endpoints to avoid `400 CSRF token missing` responses.

## Cloud Deployment

### Step 1: Set up GCP Infrastructure

This command is idempotent and only needs to be run once. It provisions all necessary APIs, service accounts, the Cloud Tasks queue, and a GCS bucket.

```bash
# This will use the values from your .env file
./infra/setup.sh
```
This script will also update your `.env` file with the names of the resources it creates, such as the service account email and task queue name.

The queue provisioning step now enables a `zissou-tasks-dlq` dead-letter queue and enforces exponential retry backoff (`--max-doublings`, `--min/max-backoff`, `--max-retry-duration`). After running the setup, you can confirm the policy with `gcloud tasks queues describe zissou-tasks --location $GCP_REGION`.

### Step 2: Deploy to Cloud Run

This command builds the container, pushes it to the Artifact Registry, and deploys it to Cloud Run.

```bash
./infra/deploy_cloud_run.sh
```

The script will output the URL of your deployed service.

### Step 2b: Deploy Firestore Indexes & Rules

The API depends on a handful of composite indexes plus per-user security rules so one account cannot read or mutate another user's library. Both artifacts live in the repo (`firestore.indexes.json`, `firestore.rules`)—reapply them any time a change lands in main.

**Using the Firebase CLI (recommended):**

```bash
firebase deploy --only firestore:indexes,firestore:rules
```

Need to propagate just the indexes? Run:

```bash
firebase deploy --only firestore:indexes
```

**Using `gcloud` directly:**

```bash
# Apply the duplicate-protection index used during task submission
gcloud firestore indexes composite create \
  --collection-group=items \
  --query-scope=COLLECTION \
  --field-config=field-path=sourceUrl,order=ASCENDING \
  --field-config=field-path=createdAt,order=DESCENDING

# Repeat as needed for any additional entries in firestore.indexes.json

# Update security rules to enforce per-user isolation
gcloud firestore security-rules update firestore.rules
```

Re-running either path is safe. Firestore skips indexes that already exist, and rules updates are atomic.

### Step 3: Configure Service URL

After the first deployment, you must add the service's URL to your `.env` file. This is required for the application to correctly create authenticated tasks.

1.  Get the URL from the output of the deploy script or by running:
    ```bash
    gcloud run services describe zissou --platform managed --region [YOUR_GCP_REGION] --format 'value(status.url)'
    ```
2.  Add it to your `.env` file:
    ```
    SERVICE_URL="https://zissou-....run.app"
    ```
3.  Re-deploy the application for the new environment variable to take effect:
    ```bash
    ./infra/deploy_cloud_run.sh
    ```

When `AUTH_ENABLED=true`, Cloud Tasks must be able to mint OIDC tokens with the `SERVICE_ACCOUNT_EMAIL` you configure. Ensure the account has permission to invoke the service and that `SERVICE_URL` points at the deployed HTTPS entrypoint—missing values now cause startup to fail fast with a clear error message.

## Configuration

### Authentication

Zissou defaults to `AUTH_BACKEND=iap`, which expects Google Identity-Aware Proxy to inject the `X-Goog-Authenticated-User-Email` header. Set `AUTH_ENABLED=true` in environments where IAP is active so every `@auth_required` route enforces the check. When developing locally without IAP, leave `AUTH_ENABLED=false` or send the headers manually while testing.

Role-based access is controlled via the `ADMIN_EMAILS` environment variable. Provide a comma-separated list of Google account emails; anyone in the list becomes an admin once IAP authenticates the request.

```
AUTH_BACKEND=iap
AUTH_ENABLED=true
ADMIN_EMAILS=user1@example.com,user2@example.com
```

To fall back to Firebase Authentication, change `AUTH_BACKEND` to `firebase` and populate the Firebase-specific environment variables listed in `.env.example`. The legacy login UI and session-cookie flow remain available for that mode but are no longer recommended.

### How to Configure Voices

You can set the default TTS voice in your `.env` file using the `TTS_VOICE` variable. The following creative profiles are available:

| Profile Name      | Description                                                  |
|-------------------|--------------------------------------------------------------|
| `captains-log`    | Neural2 narrator with a warm, steady tone for long-form listening. |
| `deep-dive`       | Studio voice with relaxed pacing for reflective features.    |
| `first-mate`      | Conversational UK delivery that keeps the tempo upbeat.      |
| `science-officer` | Crisp Australian narration ideal for technical copy.         |

For more advanced configuration, you can edit the `VOICE_PROFILES` dictionary in `app/services/tts.py` to adjust the `speaking_rate` and `pitch` of each voice.

### Resilient Fetching and Archive Recovery

The parser now retries transient HTTP failures with randomized browser-like headers, exponential backoff, and `Retry-After` support. When direct extraction looks truncated (short body, paywall copy, etc.), Zissou will attempt to reuse an archived snapshot from archive.today and then the Wayback Machine. Snapshot creation hooks are stubbed so you can wire them into Cloud Tasks or Celery without changing the parser.

### AI Summaries & Auto Tagging

- `ENABLE_SUMMARY=true` enables asynchronous article summarisation. Choose a provider with `SUMMARY_PROVIDER=openai|gemini` and supply the relevant API key (`OPENAI_API_KEY` or `GEMINI_API_KEY`). Optional overrides: `SUMMARY_MODEL` and `SUMMARY_MAX_WORDS`.
- `ENABLE_AUTO_TAGS=true` generates `auto_tags` for each item after processing. Configure `AUTO_TAG_PROVIDER=openai|gemini` and, if desired, `AUTO_TAG_MODEL` or `AUTO_TAG_LIMIT`.
- Without a provider or API key the services fall back to lightweight, on-box heuristics so the pipeline stays non-blocking.
- Set `AI_ENRICHMENT_MAX_WORKERS` (default 2) and `AI_ENRICHMENT_MAX_CHARS` (default 12000) to cap concurrency and context length for cost control.

Key environment variables:
- `FETCH_MAX_RETRIES`, `FETCH_BACKOFF_FACTOR`, `FETCH_MAX_BACKOFF_SECONDS` – tune retry cadence.
- `FETCH_ACCEPT_LANGUAGE_OPTIONS`, `FETCH_ACCEPT_OPTIONS` – pipe-separated header rotations.
- `PARSER_TRUNCATION_MIN_LENGTH`, `TRUNCATION_BLOCKING_PHRASES` – adjust truncation heuristics.
- `ARCHIVE_TODAY_BASE_URL`, `WAYBACK_API_URL`, `ARCHIVE_REQUEST_INTERVAL_SECONDS`, `ARCHIVE_TIMEOUT`, `ARCHIVE_CONCURRENCY` – steer archive endpoints, rate limits, and total wait time.
- `FALLBACK_MIN_LENGTH` – bypass archive fallbacks when high-fidelity extractors return long-form content (default 1500 characters).

### Server-Side Caching

Browse and bucket listings are now cached server-side for anonymous users via Flask-Caching. The default setup uses the in-process `SimpleCache`, but production deployments should point at a shared backend such as Redis.

Key environment variables:
- `CACHE_TYPE` – cache backend (defaults to `SimpleCache`).
- `CACHE_REDIS_URL` – Redis connection string when using `RedisCache`.
- `CACHE_DEFAULT_TIMEOUT` – cache TTL in seconds for rendered pages (defaults to `300`).

Caches are automatically invalidated whenever item tags or bucket assignments change to ensure fresh listings.

### Audio Chunking Safeguards

Each SSML request now enforces the Google Text-to-Speech 5 KB input limit with adaptive chunking. If SSML substitutions expand a fragment beyond the limit, the task handler automatically retries with smaller text slices before synthesizing. Tune the behaviour via:
- `TTS_REQUEST_BYTE_LIMIT` – hard maximum per fragment (default `5000`).
- `TTS_SAFETY_MARGIN_BYTES` – buffer reserved for SSML markup overhead (default `400`).
- `TTS_MAX_CHUNK_BYTES` – starting plaintext chunk size before SSML (default `4800`, capped by the margin).
- `TTS_MIN_CHUNK_BYTES` – lowest plaintext chunk size when retrying (default `600`).

### How to Define Buckets

Buckets are categories for your articles. Each bucket gets its own RSS feed.

1.  Navigate to the `/buckets` page on the web UI.
2.  Fill out the "Create New Bucket" form.
    - **Name**: The human-readable title (e.g., "Tech News").
    - **Slug**: The URL-friendly identifier (e.g., `tech-news`).
    - **Description**: A short summary for the bucket.
3.  Assign articles to your buckets from the item detail page. The RSS feed for each bucket will then be available at `/feeds/<your-slug>.xml`.

## Browse Items Experience

The main item listing page (`/`) and bucket-specific item pages (`/buckets/<slug>/items`) have been significantly enhanced to improve content discovery and consumption. Key features include:

-   **Unified Search & Filter Bar**: A prominent search bar allows you to quickly find items by title, source host, or tags. A collapsible filter section provides advanced options.
-   **Dynamic Filtering**: Filter items by:
    -   **Sort Order**: Newest First (default), Oldest First, Title (A-Z/Z-A), Duration (Shortest/Longest).
    -   **Bucket**: Filter by a specific bucket.
    -   **Tags**: Multi-select with autocomplete chips; add or remove tags inline without leaving the list.
    -   **Duration**: Use quick chips to filter by duration (< 5 min, 5-15 min, > 15 min).
-   **Cursor-Based Pagination**: Items are loaded in chunks with a "Load More" button, providing a smooth browsing experience.
-   **Enhanced Item Cards**: Each item is displayed in a rich card format, featuring:
    -   **Inline Audio Player**: Play audio directly from the listing.
    -   **Status Badges**: Clear visual indicators for item processing status (Ready, Processing, Failed).
    -   **Quick Actions**: Easily copy item links, copy RSS enclosure URLs, or jump to bucket and tag management.
    - **Metadata At-a-Glance**: Card badges surface duration, publish date, assigned buckets, and color-coded tags for quick scanning.
    - **Archive**: Archive articles to hide them from the main list without deleting them.
-   **Drag Prototype**: Try the drag-to-bucket prototype above the grid to see how quick categorisation might work (UI only for now).

All filtering, sorting, and pagination are handled server-side to ensure performance and scalability.

## Item Detail Page

The item detail page provides a comprehensive view of a single article, including the full text, audio player, and management tools.

- **Full Text View**: The full extracted text of the article is displayed with proper paragraph formatting.
- **Audio Player**: A mobile-friendly player with live progress, Media Session metadata, and accurate duration reporting for the generated speech.
- **Bucket Assignment**: Assign or unassign the item to one or more buckets.
- **Tag Management**: A chip-based editor with autocomplete accelerates adding, removing, and reordering tags.
- **Metadata and Metrics**: View detailed metadata about the item, including:
    - Source URL with a quick "Copy to Clipboard" button.
    - Publication and creation dates.
    - Processing metrics like processing time, voice setting used, and pipeline tools.
- **Share Preview**: Sending an item link now renders a mobile-friendly card with the article title, source, artwork, and narrated audio cue.
- **Narration polish**: Audio starts with a gentle intro, adds a half-second pause, and benefits from smarter acronym pronunciation.

## Quick Capture Tools

- **Bookmarklet**: Visit the Submit URL page to install the "Save to Zissou" bookmarklet. Click it while reading and the article URL opens in Zissou, ready to queue.
- **Readwise Importer**: Paste a public Readwise Reeder shared view at `/import/readwise`, preview the entries, and queue selected articles in bulk.
- **Drag-to-bucket prototype**: On the browse page you can drag any article card into a bucket drop zone to explore the planned interaction for RSS curation.

## Admin View

The application includes an admin view at `/admin` that provides a comprehensive overview of all submitted article processing tasks.

- **Queue Dashboard**: Summary cards highlight queued, in-progress, completed, and failed workloads, including 24-hour throughput so you can tell if the pipeline is keeping up.
- **Filter & Sort Controls**: Quickly slice the queue by status, adjust sort order, tweak page size, or use the inline quick filter and sortable column headers to reorganize without a reload.
- **Stage Age & Staleness Alerts**: Each row surfaces how long a task has been in its current state and highlights any work that is idle beyond configurable thresholds.
- **Detailed Diagnostics**: Inline badges show retry counts, structured error codes, and direct links to the task progress view or finished item.
- **Recovery Actions**: Retry failed or stale tasks in-place; the system clears previous errors, bumps the retry counter, and re-queues work through Cloud Tasks.
- **Article Deletes**: Remove published items (and their audio assets) directly from the queue table with confirmation safeguards.

## Troubleshooting

- **`lxml` build fails on macOS**: This is a common issue, especially if you are not using the recommended Python 3.11. The error log might mention `failed building wheel for lxml` or `/usr/bin/clang' failed with exit code 1`.
  - **Solution 1 (Primary):** Ensure you are using Python 3.11. This project is not compatible with pre-release versions like Python 3.13. Use a tool like `pyenv` to manage your Python version.
  - **Solution 2 (If using Python 3.11):** If it still fails, you may need to help the compiler find the required C libraries. Install them with Homebrew and export the following environment variables before running `pip install`:
    ```bash
    brew install libxml2 libxslt
    export LDFLAGS="-L/opt/homebrew/opt/libxml2/lib -L/opt/homebrew/opt/libxslt/lib"
    export CPPFLAGS="-I/opt/homebrew/opt/libxml2/include -I/opt/homebrew/opt/libxslt/include"
    
    # Then try installing again
    pip install -r requirements.txt
    ```
- **Parsing Reliability**: Extraction now cascades across Trafilatura, Newspaper3k, Readability, and a BeautifulSoup heuristic, picking the longest credible payload. If content looks truncated, the fetcher replays the request with alternate headers/referrers before falling back to archive snapshots.
- **TTS Quotas**: Google Cloud TTS has usage quotas. The application truncates long articles to stay within the 5000-byte per-request limit of the standard TTS API. Full-length audio for very long articles would require implementing the Long Audio API.
- **CORS Issues**: If you access the API from a different domain, you may need to configure CORS on the Flask app.
- **Authentication**: Google Sign-In is stubbed but not implemented. To enable it, you must set `AUTH_GOOGLE_SIGNIN=true` in `.env` and implement the OAuth2 flow in the routes.

## Pipeline Reliability Settings

The processing pipeline exposes a few environment variables to tune resilience and performance:

- `TTS_AUDIO_ENCODING`: Controls both the chunk decoding format and the exported file extension (e.g., `MP3`, `LINEAR16`, `OGG_OPUS`).
- `TTS_MAX_ATTEMPTS` / `TTS_RETRY_INITIAL_BACKOFF`: Retry configuration for Google TTS synthesis (defaults: 3 attempts, 0.5s initial backoff).
- `TTS_MAX_CHUNK_BYTES`: UTF-8 byte budget per TTS request (default 4800) used when splitting article text.
- `TTS_NORMALIZE_AUDIO`: Set to `false` to disable post-processing normalization.
- `PARSER_REQUEST_TIMEOUT_SECONDS` / `PARSER_USER_AGENT`: Override the HTTP timeout and user agent used during article fetches.
- `STORAGE_UPLOAD_ATTEMPTS` / `STORAGE_RETRY_INITIAL_BACKOFF`: Retry settings for Google Cloud Storage uploads.

## Article Processing Pipeline

The Cloud Tasks handler now performs additional resilience checks before persisting audio:

- **Duplicate protection**: Tasks short-circuit if an item already exists for the submitted URL, preventing unnecessary reprocessing and costs.
- **Bucket validation**: Submitted bucket IDs are verified up front to avoid orphaned Firestore references.
- **Byte-aware chunking**: Text is split by UTF-8 byte size (with graceful fallback for long sentences) so every request stays within the 5 KB Google TTS limit, even for non-ASCII content.
- **Extractor cascade**: Article parsing races Trafilatura, Newspaper3k, Readability, and a BeautifulSoup heuristic, records per-engine win rates, and replays fetches with alternate headers when the first pass looks truncated.
- **Client retries & normalization**: Google TTS and Cloud Storage uploads reuse shared clients, apply bounded exponential backoff, respect the configured `TTS_AUDIO_ENCODING`, and normalize the stitched audio for consistent playback volume.
- **Narrated Preface**: Each audio file now opens with a spoken summary of the title, source, author, and publication date before the article begins, followed by a paced SSML break.
- **Pronunciation Lexicon**: Common acronyms (RSS, HTTP, SaaS, AI) are expanded in TTS via SSML `<sub>` tags so the narration sounds natural.
- **Phase metrics & error codes**: Items now store per-phase timings, chunk counts, and text length, and background tasks emit structured error codes to make admin diagnostics faster.
