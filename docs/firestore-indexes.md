# Firestore Indexes

This document outlines the required Firestore indexes for the Zissou application and provides instructions on how to manage them.

## Applying Indexes

The required Firestore indexes are defined in the `firestore.indexes.json` file at the root of the project. To apply these indexes to your Firestore database, you need to have the Firebase CLI installed and configured.

Once the Firebase CLI is set up, run the following command from the project's root directory:

```bash
firebase firestore:indexes:replace firestore.indexes.json
```

This command will update your Firestore project with the indexes defined in the `firestore.indexes.json` file. The process may take a few minutes to complete.

## Index Management

When you add new queries to the application that require composite indexes, you should update the `firestore.indexes.json` file accordingly. The application is designed to detect missing indexes at build time and during runtime, but it's best to keep the index file up to date to avoid any potential issues.

To identify the required indexes, you can either manually inspect your queries or look for warnings in the application logs or CI/CD output. Once you've identified a missing index, add it to the `firestore.indexes.json` file and redeploy the indexes using the command above.
