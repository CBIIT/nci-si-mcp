// Keep one native Zensical search component and its keyboard behavior.
document.addEventListener("DOMContentLoaded", () => {
  const search = document.querySelector('[data-md-component="search"]');
  const sidebar = document.querySelector(".md-sidebar--secondary .md-sidebar__inner");
  if (!search || !sidebar) return;
  const home = document.createComment("Search returns here on small screens");
  search.before(home);
  const wide = window.matchMedia("(min-width: 60em)");
  const place = () => {
    if (wide.matches) sidebar.prepend(search);
    else home.after(search);
  };
  wide.addEventListener("change", place);
  place();
});
