(() => {
  const root = document.documentElement;
  const button = document.getElementById("theme-toggle");
  if (!button) return;

  const icon = button.querySelector("i");
  const render = () => {
    const dark = root.dataset.bsTheme === "dark";
    icon.className = dark ? "bi bi-sun" : "bi bi-moon-stars";
  };

  button.addEventListener("click", () => {
    const next = root.dataset.bsTheme === "dark" ? "light" : "dark";
    root.dataset.bsTheme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
    render();
  });
  render();
})();
