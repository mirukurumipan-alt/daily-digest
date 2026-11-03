const $ = (s) => document.querySelector(s);
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let data, prefs = store.get("prefs", { order: [], hidden: [] });
let read = new Set(store.get("read", []));
let current = "all";

const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function orderedCats() {
  const ids = data.categories.map((c) => c.id);
  const order = prefs.order.filter((id) => ids.includes(id));
  ids.forEach((id) => { if (!order.includes(id)) order.push(id); });
  return order.map((id) => data.categories.find((c) => c.id === id));
}
const visibleCats = () => orderedCats().filter((c) => !prefs.hidden.includes(c.id));

function render() {
  const cats = visibleCats();
  if (current !== "all" && !cats.some((c) => c.id === current)) current = "all";
  $("#chips").innerHTML = [{ id: "all", name: "すべて" }, ...cats]
    .map((c) => `<button class="chip ${c.id === current ? "on" : ""}" data-id="${c.id}">${esc(c.name)}</button>`).join("");
  const shown = current === "all" ? cats : cats.filter((c) => c.id === current);
  let html = "", total = 0;
  for (const c of shown) {
    const items = data.items.filter((i) => i.category === c.id);
    if (!items.length) continue;
    total += items.length;
    html += `<h2 class="cat">${esc(c.name)}</h2>` + items.map(card).join("");
  }
  $("#list").innerHTML = html || `<p class="empty">表示できる記事がありません</p>`;
  const unread = data.items.filter((i) => !read.has(i.link) && visibleCats().some((c) => c.id === i.category)).length;
  $("#meta").textContent = `${data.generated_at.replace("T", " ")} 更新 ・ ${total}件(未読${unread}) ・ ${data.ai_summary ? "AI要約" : "RSS概要"}`;
}

function card(i) {
  const title = i.title_ja || i.title;
  const en = i.lang === "en" ? `<span class="badge">EN${i.title_ja ? "→日本語要約" : ""}</span>` : "";
  const orig = i.title_ja && i.title_ja !== i.title ? `<span title="原題">原題: ${esc(i.title)}</span>` : "";
  return `<article class="${read.has(i.link) ? "read" : ""}">
    <h3><a href="${esc(i.link)}" target="_blank" rel="noopener" data-link="${esc(i.link)}">${esc(title)}</a></h3>
    ${i.summary ? `<p>${esc(i.summary)}</p>` : ""}
    <div class="info"><span>${esc(i.source)}</span>${en}${orig}</div></article>`;
}

function renderOrder() {
  const all = orderedCats();
  $("#order").innerHTML = all.map((c, n) => `<li data-id="${c.id}">
    <input type="checkbox" id="cb-${c.id}" ${prefs.hidden.includes(c.id) ? "" : "checked"}>
    <label for="cb-${c.id}">${esc(c.name)}</label>
    <button data-mv="-1" ${n === 0 ? "disabled" : ""} aria-label="上へ">↑</button>
    <button data-mv="1" ${n === all.length - 1 ? "disabled" : ""} aria-label="下へ">↓</button></li>`).join("");
}

function savePrefs() { store.set("prefs", prefs); }

document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) { current = chip.dataset.id; render(); scrollTo(0, 0); return; }
  const a = e.target.closest("a[data-link]");
  if (a) { read.add(a.dataset.link); store.set("read", [...read].slice(-500)); a.closest("article").classList.add("read"); return; }
  const mv = e.target.closest("[data-mv]");
  if (mv) {
    const ids = orderedCats().map((c) => c.id), id = mv.closest("li").dataset.id;
    const from = ids.indexOf(id), to = from + Number(mv.dataset.mv);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    prefs.order = ids; savePrefs(); renderOrder(); render();
  }
});
$("#order").addEventListener("change", (e) => {
  const id = e.target.closest("li").dataset.id;
  prefs.hidden = e.target.checked ? prefs.hidden.filter((h) => h !== id) : [...prefs.hidden, id];
  savePrefs(); render();
});
$("#settingsBtn").onclick = () => { renderOrder(); $("#dlg").showModal(); };
$("#close").onclick = () => $("#dlg").close();
$("#reset").onclick = () => { prefs = { order: [], hidden: [] }; savePrefs(); renderOrder(); render(); };

fetch("data/news.json", { cache: "no-cache" })
  .then((r) => r.json())
  .then((d) => { data = d; render(); })
  .catch(() => { $("#meta").textContent = "データを読み込めませんでした"; });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
