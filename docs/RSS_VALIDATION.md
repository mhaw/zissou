### RSS Troubleshooting & Validation

If you encounter issues with a bucket's RSS feed, follow these steps:

1.  **Reproduce Locally**: Save the raw XML output of the feed to a file. You can do this from your browser or using `curl`:
    ```sh
    curl http://localhost:8080/feeds/your-bucket-slug.xml > feed.xml
    ```

2.  **Run the Validator**: Use the provided validation script to check for common errors.
    ```sh
    python tools/validate_feed.py feed.xml
    ```
    The script will report any missing fields or structural problems.

3.  **Manual Verification Checklist**:
    - [ ] **Well-formed XML**: Does the feed open correctly in a browser?
    - [ ] **Required Channel Fields**: Does the feed have a main `title`, `link`, and `description`?
    - [ ] **Required Item Fields**: Does *every* item have a `title`, `guid` (same as `id`), `pubDate`, `link`, and `enclosure`?
    - [ ] **Absolute URLs**: Are the `enclosure` URLs absolute (i.e., start with `https://`)?
    - [ ] **GUID Stability**: Is the `guid` tag populated with the item's Firestore ID? It should not be the title or URL.
    - [ ] **Date Format**: Is the `pubDate` in RFC-822 format (e.g., `Sat, 07 Sep 2024 15:50:00 +0000`)?
