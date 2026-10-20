// Progressive enhancement only: every page works without this file.
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const toast = () => $("#toast");

  function showToast(content, { plain = false, timeout = 0 } = {}) {
    const el = toast();
    if (!el) return;
    el.replaceChildren(content);
    el.classList.toggle("plain", plain);
    el.hidden = false;
    clearTimeout(showToast.timer);
    if (timeout) showToast.timer = setTimeout(hideToast, timeout);
  }
  function hideToast() {
    const el = toast();
    if (el) el.hidden = true;
  }
  function message(text) {
    const p = document.createElement("p");
    p.textContent = text;
    showToast(p, { plain: true, timeout: 3500 });
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return res.json();
  }

  function updateBadge(count) {
    const item = $('.sidebar a[href*="/transactions"]')?.closest("li");
    if (!item) return;
    let badge = $(".badge", item);
    if (!count) { badge?.remove(); return; }
    if (!badge) {
      badge = document.createElement("a");
      badge.className = "badge";
      badge.href = "/transactions?month=all&category=none";
      item.append(badge);
    }
    badge.textContent = count;
    badge.title = `${count} without a category`;
  }

  // ---- filters submit as you change them
  $$("form[data-autosubmit]").forEach((form) => {
    form.addEventListener("change", (e) => {
      if (e.target.matches("select")) form.requestSubmit();
    });
  });

  // ---- inline category changes + "make this a rule?" offer
  $$(".cat-select").forEach((select) => {
    select.dataset.previous = select.value;
    select.addEventListener("change", async () => {
      const id = select.dataset.tx;
      try {
        const data = await postJSON(`/transactions/${id}`, { category: select.value });
        select.className = `cat-select bucket-${data.bucket}${data.category_id ? "" : " is-uncat"}`;
        void select.offsetWidth; // restart the confirmation animation
        select.classList.add("saved");
        if (!data.manual) {
          select.value = data.category_id ?? "";
        }
        $('option[value="auto"]', select).hidden = !data.manual;
        const how = $("[data-how]", select.closest("td"));
        if (how) how.textContent = data.manual ? "Set by you" : "";
        select.dataset.previous = select.value;
        updateBadge(data.uncategorised);
        if (data.suggestion) offerRule(data.suggestion);
        else message(data.category ? `Moved to ${data.category}.` : "Category removed.");
      } catch (err) {
        select.value = select.dataset.previous;
        message("Couldn't save that change. Is the app still running?");
      }
    });
  });

  function offerRule(s) {
    const tpl = $("#rule-offer");
    if (!tpl) return;
    const node = tpl.content.cloneNode(true);
    $("[data-pattern]", node).textContent = `“${s.pattern}”`;
    $("[data-category]", node).textContent = s.category;
    $("[data-others]", node).textContent =
      s.others === 0 ? "Applies to future imports."
      : s.others === 1 ? "1 other transaction will move too."
      : `${s.others} other transactions will move too.`;
    const form = $("[data-rule-form]", node);
    form.elements.pattern.value = s.pattern;
    form.elements.category_id.value = s.category_id;
    form.elements.direction.value = s.direction;
    const params = new URLSearchParams({ pattern: s.pattern, category_id: s.category_id, direction: s.direction });
    $("[data-edit-rule]", node).href = `${window.RULES_URL || "/rules"}?${params}#edit`;
    $("[data-dismiss]", node).addEventListener("click", hideToast);
    showToast(node);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideToast();
  });

  // ---- notes save when you leave the field
  $$(".note-input").forEach((input) => {
    input.dataset.saved = input.value;
    const save = async () => {
      if (input.value === input.dataset.saved) return;
      try {
        await postJSON(`/transactions/${input.dataset.tx}`, { note: input.value });
        input.dataset.saved = input.value;
        message("Note saved.");
      } catch {
        message("Couldn't save the note.");
      }
    };
    input.addEventListener("change", save);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    });
  });

  // ---- drag and drop import
  $$("[data-dropzone]").forEach((zone) => {
    const input = $('input[type="file"]', zone);
    const list = $("[data-file-list]", zone);
    const showFiles = () => {
      const names = Array.from(input.files || []).map((f) => f.name);
      if (names.length && list) list.textContent = names.join(", ");
    };
    input.addEventListener("change", showFiles);
    ["dragenter", "dragover"].forEach((t) =>
      zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((t) =>
      zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("is-over"); }));
    zone.addEventListener("drop", (e) => {
      if (!e.dataTransfer?.files?.length) return;
      input.files = e.dataTransfer.files;
      showFiles();
      zone.requestSubmit();
    });
  });

  // ---- live preview while writing a rule
  $$("form[data-rule-preview]").forEach((form) => {
    const out = $("[data-preview]", form);
    let timer;
    const run = async () => {
      const params = new URLSearchParams(new FormData(form));
      if (!params.get("pattern") && !params.get("tx_type")) { out.replaceChildren(); return; }
      try {
        const res = await fetch(`${window.PREVIEW_URL || "/rules/preview"}?${params}`);
        const data = await res.json();
        out.replaceChildren();
        const p = document.createElement("p");
        p.style.margin = "0";
        if (data.error) {
          p.className = "warn";
          p.textContent = data.error;
          out.append(p);
          return;
        }
        p.textContent = data.count === 0 ? "Matches no transactions yet."
          : `Matches ${data.count} transaction${data.count === 1 ? "" : "s"}, for example:`;
        out.append(p);
        if (data.samples.length) {
          const ul = document.createElement("ul");
          data.samples.forEach((s) => {
            const li = document.createElement("li");
            li.textContent = s.description;
            const cat = document.createElement("span");
            cat.className = "muted";
            cat.textContent = ` (now ${s.category || "no category"})`;
            li.append(cat);
            ul.append(li);
          });
          out.append(ul);
        }
      } catch { /* preview is a nicety */ }
    };
    form.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 200); });
    form.addEventListener("change", run);
    run();
  });

  // ---- confirmations for destructive actions
  $$("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // ---- keep the app awake while you're looking at it (it stops itself when unused)
  setInterval(() => {
    if (document.visibilityState === "visible") fetch("/healthz").catch(() => {});
  }, 5 * 60 * 1000);

  // ---- settings: add investments, live split total, dim removed rows
  $$("[data-platforms]").forEach((list) => {
    const form = list.closest("form");
    const tpl = $("[data-platform-template]", form);
    const total = $("[data-split-total]", form);
    const update = () => {
      const rows = $$("[data-platform]", list).filter((row) => {
        const removed = $("[data-remove]", row)?.checked;
        row.classList.toggle("is-removed", !!removed);
        return !removed && $('input[name$="-name"]', row).value.trim();
      });
      const sum = rows.reduce((n, row) => n + (parseFloat($("[data-share]", row).value) || 0), 0);
      if (!rows.length) { total.textContent = ""; total.classList.remove("bad"); return; }
      const ok = Math.abs(sum - 100) < 0.5;
      total.textContent = ok ? "Total 100%" : `Total ${+sum.toFixed(1)}%. It needs to add up to 100%.`;
      total.classList.toggle("bad", !ok);
    };
    $("[data-add-platform]", form)?.addEventListener("click", () => {
      const i = Number(list.dataset.next);
      list.dataset.next = i + 1;
      const html = tpl.innerHTML.replaceAll("__i__", String(i));
      list.insertAdjacentHTML("beforeend", html);
      $$("[data-platform]", list).at(-1).querySelector('input[name$="-name"]').focus();
    });
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    update();
  });

  // ---- investment split must add to 100%
  $$("form[data-split]").forEach((form) => {
    const total = $("[data-split-total]", form);
    const update = () => {
      const sum = $$('input[type="number"]', form).reduce((n, i) => n + (parseFloat(i.value) || 0), 0);
      total.textContent = sum === 100 ? "Total 100%" : `Total ${sum}%. It needs to add up to 100%.`;
      total.classList.toggle("bad", sum !== 100);
    };
    form.addEventListener("input", update);
    update();
  });
})();
