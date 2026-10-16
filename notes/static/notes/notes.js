(() => {
  const list = document.getElementById("note-list");
  const noteForm = document.getElementById("note-form");
  const searchForm = document.getElementById("search-form");
  const searchInput = searchForm.querySelector("input[name=q]");
  const errorBox = document.getElementById("form-errors");
  const csrfToken = noteForm.querySelector("[name=csrfmiddlewaretoken]").value;

  let pending = 0;
  let searchController = null;

  // Viser loaderen så længe mindst én forespørgsel er i gang.
  async function ajax(url, options = {}) {
    pending++;
    document.body.classList.add("loading");
    try {
      const headers = { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrfToken };
      return await fetch(url, { ...options, headers });
    } finally {
      if (--pending === 0) document.body.classList.remove("loading");
    }
  }

  function showErrors(text) {
    errorBox.textContent = text;
    errorBox.classList.toggle("d-none", !text);
  }

  function currentQuery() {
    return searchInput.value.trim();
  }

  async function loadList() {
    if (searchController) searchController.abort();
    searchController = new AbortController();
    const q = currentQuery();
    const url = q ? `?q=${encodeURIComponent(q)}` : location.pathname;
    try {
      const response = await ajax(url, { signal: searchController.signal });
      list.innerHTML = await response.text();
      history.replaceState(null, "", url);
    } catch (err) {
      if (err.name !== "AbortError") throw err;
    }
  }

  // Live-søgning med en lille forsinkelse, så der ikke sendes et kald pr. tastetryk.
  let timer;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(loadList, 250);
  });
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(timer);
    loadList();
  });

  noteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showErrors("");
    const q = currentQuery();
    const response = await ajax(q ? `?q=${encodeURIComponent(q)}` : location.pathname, {
      method: "POST",
      body: new FormData(noteForm),
    });
    const data = await response.json();
    if (response.ok) {
      list.innerHTML = data.html;
      noteForm.reset();
    } else {
      showErrors(Object.values(data.errors).flat().join(" "));
    }
  });

  // Event delegation, så slet-knapper i nyindlæste lister også virker.
  list.addEventListener("submit", async (event) => {
    const form = event.target.closest(".delete-form");
    if (!form) return;
    event.preventDefault();
    const response = await ajax(form.action, { method: "POST" });
    if (response.ok) form.closest(".note").remove();
    if (!list.querySelector(".note")) loadList();
  });
})();
