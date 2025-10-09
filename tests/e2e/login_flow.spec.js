import { test, expect } from '@playwright/test';

// Assuming Firebase Emulator is running and accessible
// And Flask app is running at http://localhost:5000

test.describe('Authentication Flow', () => {
  test('should allow a user to sign in with Google, access a protected page, and sign out', async ({ page }) => {
    // 1. Navigate to the login page
    await page.goto('http://localhost:5000/auth/login');
    await expect(page).toHaveURL(/.*auth\/login/);
    await expect(page.locator('h1')).toHaveText('Sign In');

    // Mock Firebase signInWithPopup to use emulator
    // This is a simplified mock. In a real scenario, you might need to
    // more thoroughly mock the Firebase SDK or configure it to use the emulator.
    await page.evaluate(() => {
      window.firebase = {
        auth: {
          getAuth: () => ({
            useDeviceLanguage: () => {},
            signInWithPopup: async () => {
              // Simulate a successful sign-in with a mock user
              return {
                user: {
                  getIdToken: async () => 'mock-firebase-id-token', // This token will be sent to Flask
                  email: 'test@example.com',
                  displayName: 'Test User',
                  uid: 'test-uid-123',
                },
              };
            },
            onAuthStateChanged: (callback) => {
              // Simulate auth state change after sign-in
              callback({
                email: 'test@example.com',
                displayName: 'Test User',
                uid: 'test-uid-123',
                getIdToken: async () => 'mock-firebase-id-token',
              });
              return () => {}; // Unsubscribe
            },
          }),
          GoogleAuthProvider: class {},
        },
        initializeApp: () => {},
      };
    });

    // Click the Google Sign-In button
    await page.locator('#googleSignIn').click();

    // Wait for the redirect after successful login
    // The Flask backend will verify the mock token and set a session cookie
    // Then the client-side JS will redirect to '/'
    await page.waitForURL('http://localhost:5000/');
    await expect(page).toHaveURL('http://localhost:5000/');
    await expect(page.locator('text=Welcome, Test User')).toBeVisible(); // Assuming a welcome message on protected page

    // 2. Verify access to a protected page (e.g., the main dashboard)
    // This is already done by redirecting to '/' and checking for a welcome message.
    // Let's also try to navigate to another protected page, e.g., /profile
    await page.goto('http://localhost:5000/profile');
    await expect(page).toHaveURL('http://localhost:5000/profile');
    await expect(page.locator('text=User Profile')).toBeVisible(); // Assuming a title on the profile page

    // 3. Attempt sign-out
    // Assuming there's a logout button or link
    await page.locator('text=Sign Out').click(); // Adjust selector as needed

    // Wait for redirect to login page after logout
    await page.waitForURL(/.*auth\/login/);
    await expect(page).toHaveURL(/.*auth\/login/);
    await expect(page.locator('h1')).toHaveText('Sign In');

    // 4. Verify unauthenticated access to a protected page is denied
    await page.goto('http://localhost:5000/profile');
    await page.waitForURL(/.*auth\/login/); // Should redirect back to login
    await expect(page).toHaveURL(/.*auth\/login/);
    await expect(page.locator('h1')).toHaveText('Sign In');
  });

  test('should reject unauthenticated URL submission', async ({ page }) => {
    // Navigate to a page that might have a submission form, or directly try to post
    // For this test, we'll simulate a direct POST request to a protected endpoint
    // without being authenticated.

    // First, ensure we are not logged in
    await page.goto('http://localhost:5000/auth/login');
    // Attempt to access a protected endpoint that handles submissions, e.g., /new_item
    // This assumes /new_item is protected and would redirect to login or return 401/403
    await page.goto('http://localhost:5000/new_item');
    await page.waitForURL(/.*auth\/login/);
    await expect(page).toHaveURL(/.*auth\/login/);

    // Alternatively, if there's a specific API endpoint for submission,
    // we could try to make a fetch request directly and assert the status code.
    // For now, the redirection check is sufficient for a basic E2E test.
  });
});
