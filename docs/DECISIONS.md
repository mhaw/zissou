# Design and Architectural Decisions

This document records the key decisions made during the initial design of the Zissou application.

## 1. Backend: Python + Flask

- **Decision**: Use Python 3.11+ with the Flask framework.
- **Reasoning**: Flask is a lightweight and flexible framework, making it ideal for a starter application. It has a low learning curve and a rich ecosystem of extensions. Python is excellent for scripting, web backends, and interacting with cloud services.
- **Alternative**: Django is a more batteries-included framework, but its complexity is overkill for this project's initial scope.

## 2. Text Parsing: `trafilatura`

- **Decision**: Use `trafilatura` as the primary article extraction library.
- **Reasoning**: It is a robust, pure-Python library that is very effective at extracting main content from HTML. It does not require a browser engine, making it lightweight and fast.
- **Tradeoff**: It may fail on single-page applications or sites that heavily rely on JavaScript to render content. A stub for a `Playwright` fallback has been included to address this, but it adds significant complexity and dependencies (a full browser engine).

## 3. Task Handling: In-Process

- **Decision**: For the initial build, text-to-speech conversion happens in-process during the web request.
- **Reasoning**: This simplifies the architecture significantly. There is no need for a separate worker process or a message queue (like Redis, RabbitMQ, or Cloud Tasks).
- **How to Change**: The architecture is designed for this to be replaced. The processing logic can be moved into a function that is called by a Cloud Task. The web handler would enqueue the task and immediately return a status to the user.

## 4. Frontend: Jinja + Tailwind CSS (via CDN)

- **Decision**: Use server-side rendered Jinja templates with Tailwind CSS loaded from a CDN.
- **Reasoning**: This approach avoids a complex frontend build pipeline (no Node.js, webpack, etc.). It's simple, fast to develop, and sufficient for the project's UI needs. Using the CDN is the simplest way to get started with a utility-first CSS framework.
- **How to Change**: For a more complex UI, you could introduce a Node.js build step to compile Tailwind CSS locally, allowing for customization of the `tailwind.config.js` file. For a full SPA, you would replace the Jinja templates with a framework like React or Vue.

## 5. Persistence: Firestore and Google Cloud Storage

- **Decision**: Use Firestore for metadata and GCS for audio files.
- **Reasoning**: This is a standard, scalable pattern for serverless applications on GCP. Firestore provides a flexible NoSQL database that is easy to work with from Python. GCS is the canonical service for storing large binary objects and serving them publicly.
- **Alternative**: A SQL database like Cloud SQL could be used, but would require more schema management. Storing audio files in the database is not recommended.

## 6. Authentication: Disabled by Default

- **Decision**: Include stubs for Google Sign-In but leave it disabled.
- **Reasoning**: Authentication adds significant complexity. The core value of the application is accessible without it. By providing stubs, it is clear where and how authentication could be added if the need arises.

## 7. Caching: In-Memory with `cachetools`

- **Decision**: Use `cachetools` for in-memory, time-based caching on Firestore read operations.
- **Reasoning**: Reduces latency and cost for frequently accessed data like bucket lists and item details. `cachetools` is a lightweight, standard Python library with no external dependencies, making it a simple and effective first step for performance optimization.
- **Tradeoff**: The cache is local to each Gunicorn worker and is not shared across multiple Cloud Run instances. For a more robust caching strategy at scale, a centralized cache like Redis or Memorystore would be required.

## 8. RSS Item GUIDs: Firestore Document ID

- **Decision**: Use the unique Firestore document ID of an item as the `<guid>` in the RSS feed.
- **Reasoning**: The item's audio URL on GCS could change if the file is moved or re-encoded. Using the permanent and unique Firestore ID ensures that podcast clients correctly identify an episode and do not re-download it unnecessarily.

## 9. Text Extraction Output: Plain Text

- **Decision**: Configure `trafilatura` to output plain text instead of HTML.
- **Reasoning**: Passing raw HTML to the text-to-speech service can result in unnatural audio as the TTS engine tries to read tags and other artifacts. Converting to clean text first, with proper paragraph breaks, provides a much better input for speech synthesis.
- **Tradeoff**: This approach prevents the use of SSML (Speech Synthesis Markup Language) for finer-grained audio control (e.g., adding emphasis based on HTML tags). A future improvement could involve converting the extracted HTML to SSML instead of plain text.
