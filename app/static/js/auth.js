const token = localStorage.getItem("token");

if (token) {
  const headers = {
    Authorization: `Bearer ${token}`,
  };

  document.body.addEventListener("htmx:configRequest", (event) => {
    event.detail.headers = { ...event.detail.headers, ...headers };
  });
}
