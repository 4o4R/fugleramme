// The species page's search: fuzzy over the list species.json carries, so a
// visitor can check a bird by any of its names, with or without a frame.
(function () {
  const box = document.getElementById("species-search");
  if (!box) return;
  const gaps = document.getElementById("species-gaps");
  const status = document.getElementById("species-status");
  const body = document.getElementById("species-rows");
  const LIMIT = 500;
  const FILES = "https://github.com/arnegiacomo/fugleramme/blob/main/assets/artwork/";

  const cell = (...content) => {
    const td = document.createElement("td");
    td.append(...content);
    return td;
  };
  const link = (href, text) => Object.assign(document.createElement("a"), { href, textContent: text });
  const art = (row) => {
    if (!row.plates.length) return ["no art"];
    const links = row.plates.flatMap((file, i) => [i ? ", " : "", link(FILES + file, `plate ${i + 1}`)]);
    return row.detectable === false ? [...links, " (BirdNET cannot detect it)"] : links;
  };
  const name = (row) => {
    if (!row.label) return [row.name];
    return [row.name, document.createElement("br"), Object.assign(document.createElement("small"), { textContent: `BirdNET label: ${row.label}` })];
  };
  const render = (rows) => {
    body.replaceChildren(
      ...rows.slice(0, LIMIT).map((row) => {
        const tr = document.createElement("tr");
        tr.append(cell(...name(row)), cell(row.common), cell(...art(row)));
        return tr;
      }),
    );
    status.textContent = rows.length > LIMIT ? `Showing ${LIMIT} of ${rows.length} matches` : `${rows.length} ${rows.length === 1 ? "match" : "matches"}`;
  };

  fetch("../species.json")
    .then((response) => response.json())
    .then((all) => {
      const fuse = new Fuse(all, { keys: ["name", "common", "label"], threshold: 0.3, ignoreLocation: true, minMatchCharLength: 2 });
      const withArt = all.filter((row) => row.plates.length).length;
      const update = () => {
        const query = box.value.trim();
        let rows = query ? fuse.search(query).map((hit) => hit.item) : all.filter((row) => gaps.checked || row.plates.length);
        if (gaps.checked) rows = rows.filter((row) => !row.plates.length);
        render(rows);
        if (!query && !gaps.checked) status.textContent = `${withArt} of ${all.length} species have art.`;
      };
      box.addEventListener("input", update);
      gaps.addEventListener("change", update);
      update();
    })
    .catch(() => {
      status.textContent = "The species list could not be loaded.";
    });
})();
