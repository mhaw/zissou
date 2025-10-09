# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Changed the default authentication method from Google Cloud IAP to the built-in Firebase Authentication. This simplifies the deployment process and removes the need for a costly external HTTPS Load Balancer, making the application more accessible and cost-effective for internal use.
- The `AUTH_BACKEND` environment variable now defaults to `firebase`.
- Documentation in `README_AUTH.md` has been updated to reflect Firebase as the primary, recommended authentication strategy.
- Google Cloud IAP remains a fully supported option for advanced use cases requiring infrastructure-level perimeter security.

### Security
- **Content Security Policy (CSP)**: Implemented a strict, nonce-based CSP to mitigate XSS attacks. The policy is configured to allow the necessary Firebase domains for authentication while blocking untrusted scripts and resources. A reporting endpoint has been added to monitor for violations.


### Added
- **Environment validation**: Application startup now fails fast with a clear error when required Cloud Tasks or Firebase env vars are missing.
- **User-Specific Voices**: Users can now set a default voice for their articles in their profile.
- **Smart Buckets**: Create rule-based buckets to automatically categorize articles based on domain, title, or other metadata.
- **Article Archiving**: Archive articles to hide them from the main list without deleting them.
- **Improved Error Handling in UI**: The item detail page now displays a user-friendly error message if the article processing has failed.

### Changed
- **Authentication**: Default authentication now relies on Google Identity-Aware Proxy (IAP); the Flask app reads trusted headers instead of minting Firebase session cookies. `AUTH_BACKEND=firebase` remains available as an opt-in fallback.



### Changed
- **RSS Podcast Feeds**: Expanded RSS/iTunes metadata (author, subtitle, summary, artwork), standardized GUIDs/pubDate/keywords, added per-episode duration/enclosure details, and improved enclosure MIME/length for better podcast client compatibility.
- **Extractor Resilience**: Added a cascading pipeline that evaluates Trafilatura, Newspaper3k, Readability, and a BeautifulSoup heuristic with per-engine win metrics and hybrid refetches (rotating headers/referrers) to recover truncated articles from defensive sites like NYTimes and The New Yorker.
- **Audio Playback UX**: Item detail pages now expose Media Session metadata, inline progress, and accurate duration labels so mobile players display the correct title, artwork, and scrubber position.
- **RSS GUIDs**: Feed entries now use a deterministic SHA-256 of the source URL so podcast clients stop redownloading items after audio refreshes.
- **Browse Pagination**: Replaced the old HTMX placeholder with a real load-more experience that appends cards in-place and handles JSON responses.
- **Text Normalisation**: Article extraction now runs through a cleaning pipeline that normalises encoding glitches, strips boilerplate, and compresses whitespace before TTS or feed generation.
- **Firestore Indexes**: Expanded composite indexes to cover tag and bucket filters combined with title/duration sort orders, preventing Firestore 400s under common queries.
- **Cloud Tasks Queue**: Provisioning script now pins a dead-letter queue and exponential backoff (min/max backoff, max doublings, retry duration) so failing jobs are retried safely and quarantined after repeated errors.
- **Voice Palette**: Default profiles now use newer Neural2/Studio narrators with tuned rate and pitch for smoother long-form listening.
- **Share Previews**: Item detail pages now emit Open Graph and Twitter metadata tuned to the article, so mobile share sheets surface rich titles, descriptions, and artwork.
- **Browse Items Layout**: Item cards now present metadata in dedicated sections (duration, publish date, buckets, tags) for faster scanning across the grid.
- **Audio Preface**: Generated audio now opens with a narrated summary of the title, source, author, and publication date before diving into the article body.
- **Deploy Script**: Cloud Run deploys now slugify the service/image name to satisfy Artifact Registry lowercase requirements.
- **Task Processing**: Migrated the background task processing system from a custom, polling-based worker (`worker.py`) to a robust, push-based system using **Google Cloud Tasks**. This resolves issues with latency, scalability, and reliability. The application now enqueues tasks in a managed queue, which dispatches them to a secure webhook handler within the main Flask application. This eliminates the single point of failure and allows for automatic scaling and retries.
- **Admin View**: The admin page at `/admin` layers queue health dashboards, status filtering, stage-age/staleness alerts, inline retry actions, and now tighter spacing, a quick filter, and client-side column sorting so you can triage without leaving the page.
- **Article Processing Pipeline**: Google TTS calls now reuse a shared client, respect the configured `TTS_AUDIO_ENCODING` end-to-end, split text by UTF-8 byte length with safe fallbacks, retry with bounded backoff, normalize the stitched audio, and skip work when a URL was already processed.
- **Parser Fetching**: Article extraction now uses a configurable user agent and request timeout before delegating to newspaper3k or trafilatura, improving reliability on slow or defensive sites.
- **UI Polish**: Added a shared script block, responsive navigation header, improved search/filter overlay behavior, and richer item detail copy-to-clipboard feedback for a smoother browsing experience.

### Removed
- Removed the standalone `worker.py` script, as its functionality is now handled by Google Cloud Tasks and a new task handler route within the main application.

### Fixed
- **App bootstrap**: Prevented duplicate auth blueprint registration and ensured the rate-limiter uses the shared extension so `create_app` no longer raises on missing imports.
- **Session cookies**: Guarded auth request hooks against unset `g.user` values and missing Firebase project IDs, eliminating crashes in local/test environments.
- **Cloud Tasks admin tooling**: Bulk import now reuses the authenticated admin context, honors patched helpers in tests, and continues to audit queued URLs.
- **Task lifecycle**: Normalised task timestamps to UTC, carried the user ID through retries, and restored the legacy `submit_task` helper for existing callers.
- **Article Extraction**: Updated Trafilatura integration to handle v1.6+ metadata API changes so feeds and tasks no longer spam warnings or fall back unnecessarily.
- **Logging & Tracing**: Resolved the OpenTelemetry `trace` NameError and made task context enrichment resilient when tracing is disabled.
- **Cloud Tasks Auth**: Adjusted token audience verification so Google Cloud Tasks callbacks authenticate against both the service base URL and `/tasks/process` endpoint, eliminating 500s during webhook delivery.
- **UI Regressions**: Restored item detail copy-to-clipboard controls, corrected HTMX pagination targets, and aligned filter overlays so clear actions reset search state.
- **Deployment**: Resolved a series of deployment and setup script errors, including incorrect environment variable handling, invalid paths, race conditions with IAM propagation, and incorrect Docker build contexts for ARM-based development environments (e.g., Apple Silicon).
- **TTS Pipeline**: Fixed a bug where long articles would cause the Text-to-Speech API to fail. The article processing task now automatically chunks text into smaller segments, synthesizes them individually, and stitches them together into a single audio file.
- **Data Model**: Corrected a data model inconsistency where documents in Firestore contained a `bucketId` field not present in the `Item` dataclass, causing runtime errors during item processing.
- **Database Queries**: Fixed a bug where queries for items in a bucket were using an incorrect field name (`bucket_refs` instead of `buckets`), which would have caused filtering to fail.

### Added
- **Admin Article Deletes**: Queue operators can remove published items directly from `/admin`, which detaches related tasks and reclaims the backing audio blob in Cloud Storage.
- **Browser Bookmarklet**: A "Save to Zissou" bookmarklet pre-fills the submission form with the current tab so articles can be queued in two clicks.
- **Readwise Importer**: Paste a public Readwise Reeder shared view, preview the entries, and enqueue selected articles in bulk with optional voice/bucket overrides.
- **Bucket DnD Prototype**: The browse page now includes a drag-to-bucket prototype to explore quick RSS curation workflows (UI only, no persistence yet).
- **Server-Side Caching**: Introduced Flask-Caching with anonymous page caching for the browse and bucket listings, including cache-busting when tags or buckets change.
- **Optimistic Editing API**: Added JSON endpoints and optimistic UI flows for updating tags and bucket assignments directly from the item detail view.
- **SSML Enhancements**: Added SSML wrapping with intro breaks and a lightweight pronunciation lexicon so acronyms (RSS, HTTP, SaaS, AI) render naturally.
- **TTS Pipeline Enhancements**:
- **Firestore Indexes**: Added an items composite index (`sourceUrl` ASC, `createdAt` DESC) so duplicate-detection queries no longer 400 when Cloud Tasks retries processing.
    - Added adjustable speaking rate and pitch to the TTS pipeline.
    - Added audio normalization to ensure consistent volume.
    - Added basic SSML support.
- **Item Detail Page Improvements**:
    - Added a "Back to List" button for easier navigation.
    - Added a "Copy to Clipboard" button for the source URL.
    - The page now displays tags associated with an item.
- **Tag Management UX**:
    - Replaced the plain text box with an autocomplete, chip-based editor for managing tags on item detail pages.
    - Shared the same component with the browse filter overlay to support multi-select tag filtering.
- **Processing Metrics**:
    - The application now captures and displays processing metrics (processing time, voice setting, pipeline tools) for each item, which is useful for troubleshooting.
- **Browse Items Experience**: Significantly enhanced the main item listing (`/`) and bucket-specific item pages (`/buckets/<slug>/items`) with a unified search and filter bar, dynamic filtering (by tags, buckets, duration), cursor-based pagination with a "Load More" button, and redesigned item cards featuring inline audio players, status badges, and quick action buttons. This includes:
    - New Jinja2 filters for duration formatting, datetime formatting, and URL host extraction.
    - Server-side filtering, sorting, and cursor-based pagination in `items_service.list_items`.
    - New partials for search/filter bar, item cards, and pagination controls.
    - Integration of HTMX for dynamic filtering and pagination without full page reloads.
    - Functionality to retrieve all unique tags for filtering.
    - Multi-tag filtering UI with inline chips and autocomplete suggestions.
- **Task Diagnostics**: Background tasks now persist structured `errorCode` values alongside human-readable messages and record per-phase metrics (parse/TTS/upload durations, chunk counts, text length) for easier monitoring.
- **Queue Analytics & Retries**: Tasks track retry counts, expose status and throughput aggregations, and power the admin dashboard with queue metrics and stale-task detection.

## [0.1.0] - 2025-09-09

### Added
- Caching for Firestore queries (`list_items`, `get_item`, `list_buckets`, `get_bucket`) to improve performance and reduce cost.
- Placeholder text and descriptive hints to the "Create New Bucket" form for a better user experience.
- A `CHANGELOG.md` file to track project changes.
- A `firestore.indexes.json` file to formally define required indexes.
- The application logo to the main header for branding.
- **RSS Feed Pagination**: Implemented pagination for RSS feeds, including Atom links for navigation.
- **ETag Headers**: Added ETag headers to RSS feed responses for efficient caching.
- **Content-Hashed Audio URLs**: Implemented content-hashing for audio URLs to ensure stable GUIDs and force re-downloads on content changes.

### Changed
- **RSS Podcast Feeds**: Expanded RSS/iTunes metadata (author, subtitle, summary, artwork), standardized GUIDs/pubDate/keywords, added per-episode duration/enclosure details, and improved enclosure MIME/length for better podcast client compatibility.
- **UX:** Separated the URL submission form from the main item list into its own dedicated `/new` page to create a clearer navigation flow.
- **Performance:** Refactored Firestore service (`items.py`) to use a single, module-level client instance instead of creating a new one for each function call. This reduces latency on all database operations.
- **Reliability:** Replaced the volatile, in-memory task tracking system with a persistent Firestore collection (`tasks`). This ensures that the status of background jobs is not lost on application restarts or in scaled environments.
- RSS feed item GUIDs now use the stable, permanent Firestore document ID instead of the GCS audio URL. This prevents podcast clients from re-downloading episodes if the audio URL changes.
- The item display on the index page now shows audio duration in `MMm SSs` format and includes the article's publication date for better readability.
- The article parser (`trafilatura`) now converts articles directly to plain text instead of HTML. This provides cleaner input for the text-to-speech service and results in more natural-sounding audio.
- **RSS Feed Description**: Truncated article text in RSS feed descriptions to a summary length (250 characters) for leaner feeds.
- **Buckets Service**: Refactored `buckets` service to return `Bucket` model objects and use a shared Firestore client.
- **Items Service**: Updated `items` service to return `(items, has_next)` tuple for robust pagination detection.
- **RSS Feed Metadata**: Removed `.env` fallbacks for iTunes metadata in RSS feeds, relying solely on `Bucket` model fields.

### Fixed
- **Rendering Error:** Fixed a `jinja2.exceptions.UndefinedError` by ensuring that date strings retrieved from Firestore are correctly parsed back into `datetime` objects before being passed to templates.
- **Runtime Error:** Corrected a `TypeError` in the item service that was introduced during a previous fix, ensuring the application remains stable.
- **Image Paths:** Corrected the paths for the favicon and Open Graph (`og:image`) in the base HTML template to point to the correct `img` subdirectory.
- **Feed Generation Error**: Resolved `AttributeError: 'dict' object has no attribute 'name'` in feed generation by ensuring `Bucket` objects are used.
- **ETag Generation Error**: Fixed `AttributeError: 'bytes' object has no attribute 'encode'` in feed ETag generation by correctly handling `bytes` content.
- **Syntax Error**: Resolved `SyntaxError` in `app/routes/main.py` caused by a faulty `replace` operation.
