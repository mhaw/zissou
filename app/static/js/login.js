import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  updateProfile,
  getIdToken,
  fetchSignInMethodsForEmail,
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
const form = document.getElementById("auth-form");
const emailInput = document.getElementById("email");
const nameGroup = document.getElementById("name-group");
const passwordGroup = document.getElementById("password-group");
const nameInput = document.getElementById("name");
const passwordInput = document.getElementById("password");
const googleSignInButton = document.getElementById("googleSignIn");
const continueBtn = document.getElementById("continue-btn");
const feedbackEl = document.getElementById("feedback");
const forgotPasswordLink = document.getElementById("forgot-password-link");

// --- State ---
let state = "initial"; // initial, email-submitted, sign-in, sign-up
let userEmail = "";

// --- UI Functions ---
const showFeedback = (message, type = "error") => {
  if (!feedbackEl) {
    return;
  }
  feedbackEl.textContent = message;
  feedbackEl.className = `feedback ${type}`;
};

const render = () => {
  const isSignUp = state === "sign-up";
  const isSignIn = state === "sign-in";

  nameGroup?.classList.toggle("hidden", !isSignUp);
  passwordGroup?.classList.toggle("hidden", !(isSignIn || isSignUp));

  if (continueBtn) {
    if (isSignUp) {
      continueBtn.textContent = "Create Account";
    } else if (isSignIn) {
      continueBtn.textContent = "Sign In";
    } else {
      continueBtn.textContent = "Continue";
    }
  }

  if (nameInput) {
    nameInput.required = isSignUp;
  }
  if (passwordInput) {
    passwordInput.required = isSignIn || isSignUp;
  }
};

const setLoading = (isLoading) => {
  if (googleSignInButton) {
    googleSignInButton.disabled = isLoading;
  }
  if (continueBtn) {
    continueBtn.disabled = isLoading;
    if (isLoading) {
      continueBtn.textContent = "Loading...";
    } else {
      render();
    }
  }
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
      setLoading(false);
    }
  });
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showFeedback("", "");
    setLoading(true);
    userEmail = emailInput?.value || "";

    try {
      const methods = userEmail
        ? await fetchSignInMethodsForEmail(auth, userEmail)
        : [];

      if (methods.includes("password")) {
        state = "sign-in";
        render();
        const password = passwordInput?.value || "";
        if (!password) {
          showFeedback("Please enter your password.");
          return;
        }

        const result = await signInWithEmailAndPassword(auth, userEmail, password);
        const idToken = await getIdToken(result.user, true);
        await sendIdTokenToServer(idToken);
        return;
      }

      state = "sign-up";
      render();
      const password = passwordInput?.value || "";
      const displayName = nameInput?.value || "";
      if (!password || !displayName) {
        showFeedback("Please provide your name and create a password.");
        return;
      }

      const result = await createUserWithEmailAndPassword(auth, userEmail, password);
      await updateProfile(result.user, { displayName });
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
            state = "sign-in";
            render();
            return "Invalid credentials. Please try again.";
          case "auth/email-already-in-use":
            state = "sign-in";
            render();
            return "Email already in use. Please sign in.";
          default:
            state = "initial";
            render();
            return "We couldn't sign you in. Please try again.";
        }
      })();
      showFeedback(message);
    } finally {
      setLoading(false);
    }
  });
}

if (forgotPasswordLink) {
  forgotPasswordLink.addEventListener("click", async (event) => {
    event.preventDefault();
    const email = emailInput?.value;
    if (!email) {
      showFeedback("Please enter your email address to reset your password.");
      return;
    }

    setLoading(true);
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
      setLoading(false);
    }
  });
}

// Initial render
render();