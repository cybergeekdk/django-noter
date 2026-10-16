(() => {
  const list = document.getElementById("note-list");
  const noteForm = document.getElementById("note-form");
  const searchForm = document.getElementById("search-form");
  const searchInput = searchForm.querySelector("input[name=q]");
  const categorySelect = searchForm.querySelector("select[name=kategori]");
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

  // Aktuel søgning + kategorifilter som URL (bruges til både liste og oprettelse).
  function currentUrl() {
    const params = new URLSearchParams();
    const q = searchInput.value.trim();
    if (q) params.set("q", q);
    if (categorySelect.value) params.set("kategori", categorySelect.value);
    const qs = params.toString();
    return qs ? `?${qs}` : location.pathname;
  }

  async function loadList() {
    if (searchController) searchController.abort();
    searchController = new AbortController();
    const url = currentUrl();
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
  categorySelect.addEventListener("change", loadList);
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    clearTimeout(timer);
    loadList();
  });

  noteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showErrors("");
    const response = await ajax(currentUrl(), {
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

  // Klik på et kategorimærke filtrerer listen på den kategori.
  list.addEventListener("click", (event) => {
    const badge = event.target.closest(".category-badge");
    if (!badge) return;
    categorySelect.value = badge.dataset.category;
    loadList();
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
