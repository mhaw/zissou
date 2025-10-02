const tokenKey = "token";

try {
  if (typeof window !== "undefined" && window.localStorage) {
    window.localStorage.removeItem(tokenKey);
  }
} catch (error) {
  console.warn("Unable to access localStorage while clearing legacy auth token", error);
}

// htmx requests no longer require client-side auth headers when using IAP.
