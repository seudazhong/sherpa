const screens = [...document.querySelectorAll("[data-screen]")];
const targets = [...document.querySelectorAll("[data-screen-target]")];
const select = document.querySelector("[data-prototype-select]");
const sidebar = document.querySelector(".sidebar");
const backdrop = document.querySelector(".backdrop");
const menuToggle = document.querySelector("[data-menu-toggle]");

const knownScreens = new Set(screens.map((screen) => screen.dataset.screen));

function closeMenu() {
  sidebar?.classList.remove("open");
  backdrop?.classList.remove("open");
  menuToggle?.setAttribute("aria-expanded", "false");
}

function showScreen(name, updateHash = true) {
  const next = knownScreens.has(name) ? name : "overview";

  for (const screen of screens) {
    screen.hidden = screen.dataset.screen !== next;
  }

  for (const target of targets) {
    const active = target.dataset.screenTarget === next;
    target.classList.toggle("active", active);
    if (target.classList.contains("nav-item")) {
      target.setAttribute("aria-current", active ? "page" : "false");
    }
  }

  if (select) select.value = next;
  if (updateHash) history.replaceState(null, "", `#${next}`);
  document.querySelector(".main")?.scrollTo?.({ top: 0 });
  window.scrollTo({ top: 0, behavior: "auto" });
  closeMenu();
}

for (const target of targets) {
  target.addEventListener("click", (event) => {
    event.preventDefault();
    showScreen(target.dataset.screenTarget);
  });
}

select?.addEventListener("change", () => showScreen(select.value));

menuToggle?.addEventListener("click", () => {
  const open = !sidebar?.classList.contains("open");
  sidebar?.classList.toggle("open", open);
  backdrop?.classList.toggle("open", open);
  menuToggle.setAttribute("aria-expanded", String(open));
});

document.querySelector("[data-menu-close]")?.addEventListener("click", closeMenu);

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenu();
});

window.addEventListener("hashchange", () => showScreen(location.hash.slice(1), false));

showScreen(location.hash.slice(1) || "overview", false);
