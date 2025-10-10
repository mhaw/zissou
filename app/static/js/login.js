import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  updateProfile,
  getIdToken,
  sendPasswordResetEmail,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const { firebaseConfig: configRaw, nextPath: nextPathRaw, sessionLoginUrl } = document.body.dataset;

let firebaseConfig = {};
try {
  firebaseConfig = configRaw ? JSON.parse(configRaw) : {};
} catch (error) {
  console.error("Failed to parse Firebase config", error);
}

const nextPath = nextPathRaw || "/";
const tokenEndpoint = "/auth/token";

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
auth.useDeviceLanguage();

// --- DOM Elements ---
const feedbackEl = document.getElementById("feedback");
const googleSignInButton = document.getElementById("googleSignIn");

const signinTab = document.getElementById("signin-tab");
const signupTab = document.getElementById("signup-tab");
const signinForm = document.getElementById("signin-form");
const signupForm = document.getElementById("signup-form");

const signinEmailInput = document.getElementById("signin-email");
const signinPasswordInput = document.getElementById("signin-password");
const forgotPasswordLink = document.getElementById("forgot-password-link");

const signupNameInput = document.getElementById("signup-name");
const signupEmailInput = document.getElementById("signup-email");
const signupPasswordInput = document.getElementById("signup-password");

// --- UI Functions ---
const showFeedback = (message, type = "error") => {
  if (!feedbackEl) {
    return;
  }
  feedbackEl.textContent = message;
  feedbackEl.className = `feedback ${type}`;
};

const setLoading = (isLoading, formType = null) => {
  if (googleSignInButton) {
    googleSignInButton.disabled = isLoading;
  }

  const signinBtn = signinForm?.querySelector("button[type='submit']");
  const signupBtn = signupForm?.querySelector("button[type='submit']");

  if (formType === 'signin' && signinBtn) {
    signinBtn.disabled = isLoading;
    signinBtn.textContent = isLoading ? "Loading..." : "Sign In";
  } else if (formType === 'signup' && signupBtn) {
    signupBtn.disabled = isLoading;
    signupBtn.textContent = isLoading ? "Loading..." : "Create Account";
  } else if (formType === null) {
    // For Google Sign-In or general loading
    if (signinBtn) signinBtn.disabled = isLoading;
    if (signupBtn) signupBtn.disabled = isLoading;
    if (!isLoading) {
      // Reset text if not specific form loading
      if (signinBtn) signinBtn.textContent = "Sign In";
      if (signupBtn) signupBtn.textContent = "Create Account";
    }
  }
};

const switchTab = (targetFormId) => {
  const allTabs = [signinTab, signupTab];
  const allForms = [signinForm, signupForm];

  allTabs.forEach(tab => {
    if (tab) tab.classList.remove("active");
  });
  allForms.forEach(form => {
    if (form) form.classList.add("hidden");
  });

  const activeTab = document.getElementById(`${targetFormId.replace('-form', '')}-tab`);
  const activeForm = document.getElementById(targetFormId);

  if (activeTab) activeTab.classList.add("active");
  if (activeForm) activeForm.classList.remove("hidden");

  showFeedback("", ""); // Clear feedback on tab switch
};

// --- Auth Logic ---
const sendIdTokenToServer = async (idToken) => {
  const csrfToken = document.querySelector('input[name="csrf_token"]').value;
  const response = await fetch(tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
    credentials: "include",
    redirect: "follow",
    body: JSON.stringify({ idToken, rememberMe: true, next: nextPath }),
  });

  if (response.ok) {
    const data = await response.json();
    localStorage.setItem("token", data.token);
    window.location.assign(nextPath || "/");
    return;
  }

  let message = "Server rejected login request";
  try {
    const errorData = await response.json();
    if (errorData?.error) {
      message = errorData.error;
    }
  } catch (error) {
    console.error("Failed to parse login error response", error);
  }

  throw new Error(message);
};

// --- Event Listeners ---
if (googleSignInButton) {
  googleSignInButton.addEventListener("click", async () => {
    showFeedback("", "");
    setLoading(true);
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });

    try {
      const result = await signInWithPopup(auth, provider);
      const idToken = await getIdToken(result.user, true);
      await sendIdTokenToServer(idToken);
    } catch (error) {
      console.error("Google sign-in failed", error);
      showFeedback("We couldn't sign you in with Google. Please try again.");
    } finally {
      setLoading(false);
    }
  });
}

if (signinForm) {
  signinForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showFeedback("", "");
    setLoading(true, 'signin');

    const email = signinEmailInput?.value || "";
    const password = signinPasswordInput?.value || "";

    if (!email || !password) {
      showFeedback("Please enter both email and password.");
      setLoading(false, 'signin');
      return;
    }

    try {
      const result = await signInWithEmailAndPassword(auth, email, password);
      const idToken = await getIdToken(result.user, true);
      await sendIdTokenToServer(idToken);
    } catch (error) {
      console.error("Email sign-in failed", error);
      const message = (() => {
        switch (error.code) {
          case "auth/invalid-email":
            return "Please enter a valid email address.";
          case "auth/user-disabled":
            return "This account has been disabled. Contact support.";
          case "auth/user-not-found":
          case "auth/wrong-password":
            return "Invalid email or password. Please try again.";
          default:
            return "We couldn't sign you in. Please try again.";
        }
      })();
      showFeedback(message);
    } finally {
      setLoading(false, 'signin');
    }
  });
}

if (signupForm) {
  signupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showFeedback("", "");
    setLoading(true, 'signup');

    const name = signupNameInput?.value || "";
    const email = signupEmailInput?.value || "";
    const password = signupPasswordInput?.value || "";

    if (!name || !email || !password) {
      showFeedback("Please provide your name, email, and password.");
      setLoading(false, 'signup');
      return;
    }

    try {
      const result = await createUserWithEmailAndPassword(auth, email, password);
      await updateProfile(result.user, { displayName: name });
      const idToken = await getIdToken(result.user, true);
      await sendIdTokenToServer(idToken);
    } catch (error) {
      console.error("Email sign-up failed", error);
      const message = (() => {
        switch (error.code) {
          case "auth/invalid-email":
            return "Please enter a valid email address.";
          case "auth/email-already-in-use":
            return "This email is already in use. Please sign in instead.";
          case "auth/weak-password":
            return "Password is too weak. Please choose a stronger password.";
          default:
            return "We couldn't create your account. Please try again.";
        }
      })();
      showFeedback(message);
    } finally {
      setLoading(false, 'signup');
    }
  });
}

if (forgotPasswordLink) {
  forgotPasswordLink.addEventListener("click", async (event) => {
    event.preventDefault();
    const email = signinEmailInput?.value;
    if (!email) {
      showFeedback("Please enter your email address to reset your password.");
      return;
    }

    setLoading(true, 'signin');
    showFeedback("", "");
    try {
      await sendPasswordResetEmail(auth, email);
      showFeedback("Password reset email sent! Please check your inbox.", "success");
    } catch (error) {
      console.error("Password reset failed", error);
      showFeedback(
        "Could not send password reset email. Please check the address and try again."
      );
    } finally {
      setLoading(false, 'signin');
    }
  });
}

if (signinTab) {
  signinTab.addEventListener("click", () => switchTab("signin-form"));
}

if (signupTab) {
  signupTab.addEventListener("click", () => switchTab("signup-form"));
}

// Initial setup: ensure sign-in form is active by default
switchTab("signin-form");
