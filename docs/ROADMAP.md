# Project Roadmap

This document outlines the development roadmap for the Zissou application.

## V1.0: Core Functionality

The focus of V1.0 is to deliver the core functionality of the application, providing a stable and usable product.

### Epic: User Authentication & Authorization

*   **Story: As a user, I want to sign in with my Google account so that I can access my content.**
    *   Task: Implement the server-side logic for Google Sign-In.
    *   Task: Create a user profile page.
    *   Task: Associate items and buckets with user accounts.
    *   Task: Secure all relevant endpoints to require authentication.

### Epic: Asynchronous Task Processing

*   **Story: As a user, I want the application to process URLs in the background so that I don't have to wait for the page to load.**
    *   **Task: Make task status persistent (e.g., using Firestore) for production environments. (Completed)**
    *   **Task: Move the parsing and TTS generation logic to a background task. (Completed - Basic Threading)**
    *   Task: Implement a mechanism to notify users when a task is complete.
    *   **Task: Set up a more robust task queue (e.g., Cloud Tasks, Celery) to replace the basic threading implementation. (Completed - Cloud Tasks)**

### Epic: Robust Error Handling & Logging

*   **Story: As a developer, I want the application to have robust error handling and logging so that I can easily debug issues.**
    *   **Task: Implement comprehensive error handling for all external service calls (e.g., Google Cloud Storage, TTS, Firestore). (Completed)**
    *   **Task: Implement structured JSON logging for all application events. (Completed)**
    *   **Task: Create a centralized error reporting mechanism. (Completed - via structured logging)**
    *   **Task: Improve database performance by using a shared client. (Completed)**

### Epic: RSS Feed Personalization

*   **Story: As a user, I want to be able to personalize my RSS feeds so that they reflect my brand.**
    *   **Task: Allow users to configure the author, email, and cover image for their RSS feeds. (Completed)**
    *   **Task: Allow users to configure the iTunes categories and other metadata for their RSS feeds. (Completed)**
    *   **Task: Ensure correct duration format for RSS feed items. (Completed)**
    *   **Task: Use a stable, permanent GUID for feed items. (Completed)**
    *   **Task: Implement RSS feed pagination. (Completed)**
    *   **Task: Add ETag headers to RSS feed responses. (Completed)**
    *   **Task: Ensure content-hashed audio URLs for stable GUIDs. (Completed)**
    *   **Task: Truncate full text in RSS feed descriptions. (Completed)**
    *   **Task: Ensure explicit iTunes fields in Bucket model are used. (Completed)**

## V1.1: Enhanced Content Parsing

The focus of V1.1 is to improve the content parsing capabilities of the application, making it more reliable and versatile.

### Epic: Metadata Enrichment

*   **Story: As a user, I want more relevant metadata extracted from articles.**
    *   **Task: Add a 'Published Date' Field to Item. (Completed)**
    *   **Task: Implement Basic Image Extraction (e.g., Open Graph Image). (Completed)**
    *   **Task: Improve HTML-to-Text conversion for better audio output. (Completed)**

### Epic: Advanced Content Parsing

*   **Story: As a user, I want the application to be able to parse JavaScript-heavy websites so that I can access a wider range of content.**
    *   Task: Implement a Playwright-based parsing service.
    *   Task: Add Playwright to the Dockerfile and `requirements.txt`.
    *   Task: Create a fallback mechanism to use Playwright when the default parser fails.

## V1.2: Improved User Experience

The focus of V1.2 is to improve the user experience of the application, making it more intuitive and enjoyable to use.

### Epic: Frontend Polish

*   **Story: As a user, I want the application to have a polished and responsive UI so that I can have a better user experience.**
    *   **Task: Add loading indicators during processing. (Completed)**
    *   **Task: Implement item display enhancements (image, author, duration). (Completed)**
    *   **Task: Implement item filtering by bucket. (Completed)**
    *   **Task: Implement pagination for item lists. (Completed)**
    *   **Task: Clarify navigation by separating the 'Submit URL' form from the main item list. (Completed)**
    *   **Task: Add application logo to header. (Completed)**
    *   Task: Implement dynamic data refreshing.
    *   Task: Improve the overall design and layout of the application.

### Epic: Bucket Management

*   **Story: As a user, I want to be able to manage my buckets so that I can keep my content organized.**
    *   **Task: Implement the ability to view items within a bucket. (Completed)**
    *   Task: Implement the ability to edit bucket names and descriptions.
    *   Task: Implement the ability to delete buckets.

## Future Ideas

*   **Multi-Language Support**: Add support for other languages in Google Cloud TTS.
*   **Paywall Mitigation**: Investigate techniques for accessing content behind paywalls, potentially by allowing users to provide cookies or credentials.
*   **Transcript Display**: Show the full text of the article alongside the audio player.
*   **CI/CD Pipeline**: Set up a GitHub Actions workflow to automatically run linting, tests, and deployments on push to the main branch.
