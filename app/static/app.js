"use strict";
/* Jack in a Box config page. No framework; every value from the server or an
   email is inserted with textContent, never innerHTML. */

const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}

let CFG = null, SAVED = null, SCHEMA = null, TAB = "dashboard", TPL = "reminder", dirty = false, previewTimer = null;
let POLL = null;

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" };
  if (opts.json !== undefined) { o.body = JSON.stringify(opts.json); o.headers["Content-Type"] = "application/json"; }
  if (opts.body) o.body = opts.body;
  const r = await fetch(path, o);
  if (r.status === 401 && path !== "/api/login") { showGate(); throw new Error("login"); }
  if (opts.raw) return r;
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : ""); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3800);
}
const getPath = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) {
  const ks = p.split("."); const last = ks.pop();
  const t = ks.reduce((a, k) => (a[k] = a[k] ?? {}), o); t[last] = v;
}
const fullPath = p => (p.startsWith("t.") ? `templates.${TPL}.${p.slice(2)}` : p);
function markDirty() {
  dirty = JSON.stringify(CFG) !== JSON.stringify(SAVED);
  $("#savebar").hidden = !dirty;
  if (TAB === "templates") { clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 250); }
}

/* ---------------------------------------------------------------- gate */
function showGate(setup) {
  $("#gate").hidden = false;
  $("#gateMsg").textContent = setup ? "Welcome! Choose a password for this settings page (it also protects your email login)."
    : "Sign in to change settings.";
  $("#gateBtn").textContent = setup ? "Set password" : "Sign in";
  $("#gateForm").dataset.mode = setup ? "setup" : "login";
  $("#gatePw").autocomplete = setup ? "new-password" : "current-password";
}
$("#gateForm").addEventListener("submit", async e => {
  e.preventDefault();
  const mode = e.currentTarget.dataset.mode;
  try {
    await api(mode === "setup" ? "/api/setup" : "/api/login", { method: "POST", json: { password: $("#gatePw").value } });
    $("#gate").hidden = true; $("#gatePw").value = ""; $("#gateErr").textContent = "";
    boot();
  } catch (err) { $("#gateErr").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); location.reload(); });

/* ---------------------------------------------------------------- tabs */
const TABS = [
  ["dashboard", "Dashboard"], ["print", "Print"], ["form", "Google Form"], ["messages", "Messages"], ["symbols", "Symbols"], ["power", "Power & fan"], ["templates", "Templates"], ["email", "Email"], ["calendar", "Calendar"],
  ["text", "Text & sorting"], ["aireview", "AI review"], ["rules", "Rules"], ["printer", "Printer"], ["integrations", "Integrations"],
  ["playground", "Try it"], ["account", "Account"],
];
function renderTabs() {
  const nav = $("#tabs"); nav.replaceChildren();
  for (const [id, label] of TABS) nav.append(h("button", { class: id === TAB ? "on" : "", onclick: () => go(id) }, label));
}
function go(tab) {
  TAB = tab; clearInterval(POLL); POLL = null; renderTabs();
  const main = $("#main"); main.replaceChildren();
  ({ dashboard, print: printPage, form: formPage, aireview: aiReviewPage, messages, symbols, power: powerPage, templates, email: () => simple("email", "Email account"), calendar, text: () => simple("text", "Text, dates & summaries"),
     rules, printer: () => simple("printer", "Printer"), integrations, playground, account }[tab])(main);
  window.scrollTo(0, 0);
}

/* ---------------------------------------------------------------- fields */
function optionsFor(f) {
  if (f.type === "template") return Object.entries(CFG.templates).map(([k, t]) => [k, t.label || k]);
  return f.options || [];
}
function makeField(f, onchange) {
  const p = fullPath(f.path);
  const val = getPath(CFG, p);
  const set = v => { setPath(CFG, p, v); (onchange || (() => {}))(); markDirty(); };
  const label = h("label", { class: "l" }, f.label);
  let input;
  switch (f.type) {
    case "bool":
      input = h("input", { type: "checkbox", checked: !!val, onchange: e => set(e.target.checked) });
      return h("div", { class: "f inline" }, input, label, f.help && h("div", { class: "help" }, f.help));
    case "number":
      input = h("input", { type: "number", value: val ?? "", min: f.min, max: f.max, step: f.step || "any",
        oninput: e => set(e.target.value === "" ? 0 : parseFloat(e.target.value)) }); break;
    case "select": case "template":
      input = h("select", { onchange: e => set(e.target.value) },
        optionsFor(f).map(([v, l]) => h("option", { value: v, selected: v === val }, l))); break;
    case "lines":
      input = h("textarea", { oninput: e => set(e.target.value.split("\n").map(s => s.trim()).filter(Boolean)) }, (val || []).join("\n")); break;
    case "multicheck":
      input = h("div", { class: "checks" }, f.options.map(([v, l]) => h("label", {},
        h("input", { type: "checkbox", checked: (val || []).includes(v), onchange: e => {
          const s = new Set(getPath(CFG, p) || []); e.target.checked ? s.add(v) : s.delete(v); set([...s]); } }), l))); break;
    case "time":
      input = h("input", { type: "time", value: val || "07:00", onchange: e => set(e.target.value) }); break;
    case "password":
      input = h("input", { type: "password", value: val || "", autocomplete: "new-password",
        placeholder: CFG.email.password_set ? "(saved - leave blank to keep)" : "", oninput: e => set(e.target.value) }); break;
    case "image": return imageField(f, p, val, label, set);
    case "order": return orderField(f, p, val, label, set);
    default:
      input = h("input", { type: "text", value: val ?? "", oninput: e => set(e.target.value) });
  }
  return h("div", { class: "f" }, label, input, f.help && h("div", { class: "help" }, f.help));
}
function imageField(f, p, val, label, set) {
  const wrap = h("div", { class: "row" });
  const draw = v => {
    wrap.replaceChildren();
    if (v) wrap.append(h("img", { class: "thumb", src: "/uploads/" + encodeURIComponent(v) }));
    const file = h("input", { type: "file", accept: "image/*", onchange: async e => {
      const fd = new FormData(); fd.append("file", e.target.files[0]);
      try { const r = await api("/api/upload", { method: "POST", body: fd }); set(r.file); draw(r.file); } catch (err) { toast(err.message, true); }
    } });
    wrap.append(file);
    if (v) wrap.append(h("button", { class: "btn small", onclick: () => { set(""); draw(""); } }, "Remove"));
  };
  draw(val);
  return h("div", { class: "f" }, label, wrap, f.help && h("div", { class: "help" }, f.help));
}
function orderField(f, p, val, label, set) {
  const names = Object.fromEntries(f.options);
  const ul = h("ul", { class: "order" });
  const draw = () => {
    ul.replaceChildren();
    const list = getPath(CFG, p);
    list.forEach((id, i) => ul.append(h("li", {}, h("span", {}, names[id] || id),
      h("button", { class: "btn small", disabled: i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; set(list); draw(); } }, "↑"),
      h("button", { class: "btn small", disabled: i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; set(list); draw(); } }, "↓"),
      h("button", { class: "btn small danger", onclick: () => { list.splice(i, 1); set(list); draw(); } }, "✕"))));
    const missing = f.options.filter(([id]) => !list.includes(id));
    if (missing.length) ul.append(h("li", {}, h("span", { class: "muted" }, "Add:"), ...missing.map(([id, l]) =>
      h("button", { class: "btn small", onclick: () => { list.push(id); set(list); draw(); } }, "+ " + l))));
  };
  draw();
  return h("div", { class: "f" }, label, ul, f.help && h("div", { class: "help" }, f.help));
}
function form(fields, onchange) { return h("div", {}, fields.map(f => makeField(f, onchange))); }

function simple(section, title) {
  const main = $("#main");
  let fields = SCHEMA.sections[section];
  if (section === "email") {                              // the most important setting gets its own card, first
    const who = fields.filter(f => f.path === "email.allowed_senders");
    fields = fields.filter(f => f.path !== "email.allowed_senders");
    main.append(h("div", { class: "card" }, h("h2", {}, "Who can email the printer?"),
      h("p", { class: "small" }, h("strong", {}, "Put YOUR email address here"), " (the one you'll send reminders from), one per line. Emails from these addresses print straight away as reminders. ",
        "Anyone else is ignored. Friends who should get a MESSAGE slip go on the ", h("a", { href: "#", onclick: e => { e.preventDefault(); go("messages"); } }, "Messages tab"), "."),
      form(who), blockedPanel()));
  }
  const card = h("div", { class: "card" }, h("h2", {}, title), form(fields));
  main.append(card);
  if (section === "email") {
    const res = h("div", { class: "small muted" });
    card.append(h("div", { class: "row" },
      h("button", { class: "btn", onclick: async () => {
        if (dirty) return toast("Save your changes first, then test.", true);
        res.textContent = "Connecting…"; const r = await api("/api/test-email", { method: "POST" }); res.textContent = r.message; res.style.color = r.ok ? "var(--ok)" : "var(--bad)";
      } }, "Test connection"),
      h("button", { class: "btn", onclick: async () => { await api("/api/check-mail", { method: "POST" }); toast("Checking now…"); } }, "Check mail now"), res));
    main.append(h("div", { class: "card small" }, h("h3", {}, "Setting up Gmail"),
      h("ol", {}, h("li", {}, "Create a dedicated Gmail account for the printer."),
        h("li", {}, "Turn on 2-Step Verification, then create an App Password (myaccount.google.com/apppasswords)."),
        h("li", {}, "Paste the Gmail address above and the 16-letter app password. IMAP is on by default in Gmail."),
        h("li", {}, "Put your own addresses under \"Your addresses (admin)\", and add friends on the Messages tab."))));
  }
  if (section === "text") {
    const out = h("div", { class: "small", style: "margin-top:8px" });
    const show = (msg, ok) => { out.textContent = msg; out.style.color = ok === undefined ? "" : ok ? "var(--ok)" : "var(--bad)"; };
    const on = CFG.summary.backend === "ollama";
    main.append(h("div", { class: "card" }, h("h2", {}, "Local AI summaries (Raspberry Pi 3B+)"),
      h("div", { style: "margin:6px 0;font-weight:600;color:" + (on ? "var(--ok)" : "var(--muted)") },
        on ? "AI mode is ON. (If the AI is off, slow or makes something up, the built-in summary is used instead.)" : "AI mode is OFF: summaries use the built-in method."),
      h("p", { class: "small muted" }, "Install it once (steps below), press “Test AI summary” to see it work and how long it takes, then “Switch on AI mode”. The AI only rewrites a short excerpt of the email, requests and deadlines first, and its answer is thrown away if it mentions anything that isn't in the email. [focus] lookups and [full] never use it."),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: async () => {
          show("Checking…"); const r = await api("/api/ai-status");
          show(`${r.message}  (${r.arch}${r.mem_available_mb != null ? `, ${r.mem_available_mb} of ${r.mem_total_mb} MB RAM free` : ""})`, r.ok);
        } }, "Check AI status"),
        h("button", { class: "btn", onclick: async () => {
          if (dirty) return toast("Save first, then test.", true);
          const r0 = await api("/api/ai-test", { method: "POST" }); show(r0.started ? "Running a sample email through the model - this can take a few minutes on a Pi…" : "A test is already running…");
          const t = setInterval(async () => {
            const r = await api("/api/ai-test"); if (r.running) return; clearInterval(t);
            const x = r.result || {};
            show(x.used_ai ? `Worked in ${x.seconds}s:  ${x.summary.join(" ")}` : `AI not used (${x.seconds}s): ${x.reason}`, !!x.used_ai);
          }, 3000);
        } }, "Test AI summary"),
        h("button", { class: on ? "btn" : "btn primary", onclick: async () => {
          if (dirty) return toast("Save your changes first.", true);
          if (on) { CFG.summary.backend = "extractive"; markDirty(); $("#save").click(); return; }
          show("Checking the AI is ready…"); const r = await api("/api/ai-status");
          if (!r.ok) return show("Not switched on: " + r.message, false);
          CFG.summary.backend = "ollama"; markDirty(); $("#save").click();
        } }, on ? "Switch back to built-in summary" : "Switch on AI mode")), out,
      h("details", { style: "margin-top:12px" }, h("summary", {}, "How to set the Pi up for AI (one-off)"), h("div", { class: "in small" },
        h("ol", {},
          h("li", {}, "A 3B+ must run the 64-bit OS: flash “Raspberry Pi OS Lite (64-bit)” (no desktop) with Raspberry Pi Imager. Check with ", h("code", {}, "uname -m"), " = aarch64."),
          h("li", {}, "Free up RAM (headless, no desktop/Bluetooth, compressed swap): ", h("code", {}, "bash deploy/lean-mode.sh"), " then reboot."),
          h("li", {}, "Install Ollama and the small model: ", h("code", {}, "bash deploy/ai-setup.sh"), "  (or do steps 2 and 3 together: ", h("code", {}, "bash deploy/enable-ai.sh"), ")"),
          h("li", {}, "Reboot, press “Test AI summary” here, then “Switch on AI mode”.")),
        "Prefer not to? Point “AI: Ollama address” at another computer on your network instead.")))
    );
  }
  if (section === "printer") {
    const res = h("div", { class: "small", style: "margin-top:8px" });
    card.append(h("div", { class: "row" },
      h("button", { class: "btn", onclick: async () => {
        if (dirty) return toast("Save your changes first, then check.", true);
        res.textContent = "Checking…"; const r = await api("/api/printer-check", { method: "POST" });
        res.textContent = r.message; res.style.color = r.ok ? "var(--ok)" : "var(--bad)";
      } }, "Check connection"),
      h("button", { class: "btn", onclick: async () => {
        if (dirty) return toast("Save your changes first, then test.", true);
        await api("/api/test-print", { method: "POST", json: { template: "reminder" } }); toast("Test print queued");
      } }, "Print a test reminder")), res);
    main.append(h("div", { class: "card small" }, h("h3", {}, "Ethernet-connected printer?"),
      "Plug the printer's network port straight into the Pi's Ethernet port, run ",
      h("code", {}, "bash deploy/printer-ethernet.sh"), " on the Pi once, then choose “Network” above and enter the printer's IP. ",
      "Wi-Fi keeps working normally. See the README."));
  }
}

/* ---------------------------------------------------------------- who may email the printer */
async function allowSender(email, as) {
  if (dirty) return toast("Save your changes first, then try again.", true);
  try {
    await api("/api/allow-sender", { method: "POST", json: { email, as } });
    CFG = await api("/api/config"); SAVED = JSON.parse(JSON.stringify(CFG));
    toast(as === "admin" ? "Added as you. Send your email again." : "Added as a friend (set their name on the Messages tab).");
    go(TAB);
  } catch (e) { toast(e.message, true); }
}
function blockedPanel() {
  const box = h("div", { style: "margin-top:12px" });
  api("/api/blocked-senders").then(rows => {
    if (!rows.length) return;
    box.append(h("h3", {}, "Recently ignored senders"),
      h("p", { class: "small muted" }, "These emailed the printer but aren't on any list, so nothing was printed. If one is you or a friend, add them:"),
      ...rows.map(r => h("div", { class: "row", style: "margin:6px 0" }, h("code", {}, r.email),
        h("span", { class: "muted small" }, `${r.n} email${r.n > 1 ? "s" : ""}`),
        h("button", { class: "btn small primary", onclick: () => allowSender(r.email, "admin") }, "That's me"),
        h("button", { class: "btn small", onclick: () => allowSender(r.email, "friend") }, "Add as a friend"))));
  }).catch(() => {});
  return box;
}

/* ---------------------------------------------------------------- dashboard */
function dashboard(main) {
  const stat = h("div", { class: "card" });
  const hist = h("div", { class: "hist" });
  const log = h("div", { class: "log" });
  const nobody = CFG.email.enabled && !(CFG.email.allowed_senders || []).length && !(CFG.friends || []).length;
  if (nobody) main.append(h("div", { class: "banner", style: "margin-bottom:16px" }, "Nobody is allowed to email the printer yet, so every email is ignored.  ",
    h("button", { class: "btn small primary", onclick: () => go("email") }, "Add your email address")));
  main.append(stat, h("div", { class: "card", id: "blockedCard" }, blockedPanel()), h("div", { class: "card" }, h("div", { class: "row" }, h("h2", { style: "flex:1;margin:0" }, "Recent printouts"),
    h("button", { class: "btn", onclick: async () => { await api("/api/test-print", { method: "POST", json: { template: "reminder" } }); toast("Test print queued"); refresh(); } }, "Print test"),
    h("button", { class: "btn", onclick: async () => { await api("/api/check-mail", { method: "POST" }); toast("Checking mail…"); } }, "Check mail now")), hist),
    h("div", { class: "card" }, h("h2", {}, "Activity log"), log));
  async function refresh() {
    const [st, hs, lg] = await Promise.all([api("/api/status"), api("/api/history"), api("/api/log")]);
    stat.replaceChildren(h("h2", {}, "Status"), h("div", { class: "small" },
      `Printer queue: ${st.printer.queued} waiting · ${st.printer.printed_last_hour} printed in the last hour` +
      (st.printer.last_print ? ` · last print ${st.printer.last_print.replace("T", " ")}` : ""),
      st.printer.printer_error ? h("div", { class: "err" }, "Printer problem: " + st.printer.printer_error) : null,
      st.printer.held ? h("div", { class: "banner" }, `${st.printer.held} message${st.printer.held > 1 ? "s" : ""} waiting for the button  `,
        h("button", { class: "btn small primary", onclick: async () => { await api("/api/release", { method: "POST" }); toast("Printing waiting messages"); refresh(); } }, "Print them now"),
        h("button", { class: "btn small danger", onclick: async () => { if (confirm("Throw away all waiting messages?")) { await api("/api/discard-held", { method: "POST" }); refresh(); } } }, "Discard all")) : null,
      st.sheet && st.sheet.state && st.sheet.state !== "disabled" ? h("div", {}, `Google Form: ${st.sheet.state}${st.sheet.last_check ? ", checked " + st.sheet.last_check : ""}${st.sheet.error ? " - " + st.sheet.error : ""}${st.sheet.scheduled ? `. ${st.sheet.scheduled} scheduled, next prints ${st.sheet.next_scheduled}` : ""}`) : null,
      st.cpu_temp != null ? h("div", {}, `CPU ${st.cpu_temp.toFixed(0)}°C` + (st.fan && st.fan.state === "ok" ? ` · fan ${st.fan.fan_on ? "ON" : "off"}` : "")) : null,
      h("div", {}, `Button: ${st.button.state === "ok" ? "ready - " : ""}${st.button.message}`),
      h("div", {}, `Mailbox: ${st.mail.state}${st.mail.last_check ? " (checked " + st.mail.last_check + ")" : ""}${st.mail.error ? " - " + st.mail.error : ""}`),
      h("div", {}, `Calendar: ${st.calendar.last_sync ? "synced " + st.calendar.last_sync + ", " + st.calendar.events + " events" : "not synced yet"}${st.calendar.error ? " - " + st.calendar.error : ""}`)));
    hist.replaceChildren(...hs.map(j => h("div", { class: "it" },
      h("img", { src: `/history/${j.id}.png`, loading: "lazy", onclick: e => lightbox(e.target.src) }),
      h("div", { class: "t" }, j.title || "(untitled)"),
      h("div", { class: "row" }, h("span", { class: "badge " + j.status }, j.status), h("span", { class: "muted small" }, j.kind + " · " + j.created.slice(5, 16).replace("T", " "))),
      j.status === "scheduled" ? h("div", { class: "when" }, "Prints " + j.due_text) : null,
      j.error ? h("div", { class: "small err" }, j.error) : null,
      fixedNotes(j.notes),
      h("div", { class: "row" },
        h("button", { class: "btn small", onclick: async () => { await api(`/api/history/${j.id}/reprint`, { method: "POST" }); toast("Reprinting"); refresh(); } }, "Reprint"),
        j.status === "held" || j.status === "scheduled" ? h("button", { class: "btn small primary", onclick: async () => { await api(`/api/history/${j.id}/release`, { method: "POST" }); refresh(); } }, "Print now") : null,
        j.status === "pending" || j.status === "held" || j.status === "scheduled" ? h("button", { class: "btn small danger", onclick: async () => { await api(`/api/history/${j.id}/cancel`, { method: "POST" }); refresh(); } }, "Cancel") : null))));
    if (!hs.length) hist.append(h("div", { class: "muted", style: "grid-column:1/-1" }, "Nothing printed yet. Try “Print test”, or email the printer's address."));
    log.replaceChildren(...lg.map(l => h("div", { class: l.level }, `${l.ts}  ${l.msg}`)));
    renderPills(st);
  }
  refresh(); POLL = setInterval(() => refresh().catch(() => {}), 5000);
}
function fixedNotes(raw) {
  let n = []; try { n = JSON.parse(raw || "[]"); } catch (e) { /* ignore */ }
  return n.length ? h("div", { class: "small muted" }, "Notes: " + n.join(", ")) : null;
}
function lightbox(src) {
  const d = h("div", { class: "lightbox", onclick: () => d.remove() }, h("img", { src }));
  document.body.append(d);
}
function renderPills(st) {
  $("#pills").replaceChildren(...[
    h("span", { class: "pill " + (st.printer.printer_error ? "bad" : "ok") }, st.printer.printer_error ? "Printer: problem" : "Printer OK"),
    h("span", { class: "pill " + (st.mail.state === "ok" ? "ok" : st.mail.state === "error" ? "bad" : "warn") }, "Mail: " + st.mail.state),
    st.printer.held ? h("span", { class: "pill warn" }, st.printer.held + " waiting for button") : null,
    h("span", { class: "pill" }, st.printer.queued + " queued")].filter(Boolean));
}

/* ---------------------------------------------------------------- templates */
function templates(main) {
  const left = h("div"), right = h("div", { class: "sticky" });
  main.append(h("div", { class: "cols tpl" }, left, right));
  const img = h("img", { alt: "preview" });
  const sampleSel = h("select", { onchange: refreshPreview, id: "sampleSel" },
    [["", "Matching sample"], ["reminder", "Reminder"], ["reminder_image", "Reminder + photo"], ["calendar", "Calendar event"], ["email", "Email summary"]]
      .map(([v, l]) => h("option", { value: v }, l)));
  right.append(h("div", { class: "card preview" }, h("h3", {}, "Live preview"), h("div", { class: "row", style: "justify-content:center;margin-bottom:8px" }, sampleSel), img,
    h("div", { class: "small muted", style: "margin:8px 0" }, "Actual size is 72 mm wide. Height grows with the text."),
    h("button", { class: "btn", onclick: async () => {
      if (dirty) return toast("Save first, then test print.", true);
      await api("/api/test-print", { method: "POST", json: { template: TPL, sample: sampleSel.value || undefined } }); toast("Test print queued");
    } }, "Print this sample")));
  function drawLeft() {
    left.replaceChildren();
    const names = Object.entries(CFG.templates);
    const sel = h("select", { onchange: e => { TPL = e.target.value; drawLeft(); refreshPreview(); } },
      names.map(([k, t]) => h("option", { value: k, selected: k === TPL }, t.label || k)));
    const builtin = ["reminder", "calendar", "email"].includes(TPL);
    left.append(h("div", { class: "card" }, h("div", { class: "row" }, h("strong", {}, "Template:"), sel,
      h("button", { class: "btn small", onclick: () => {
        const base = prompt("Name for the new template (copy of “" + (CFG.templates[TPL].label || TPL) + "”):");
        if (!base) return;
        const id = base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "custom";
        if (CFG.templates[id]) return toast("That name is taken", true);
        CFG.templates[id] = JSON.parse(JSON.stringify(CFG.templates[TPL])); CFG.templates[id].label = base;
        TPL = id; markDirty(); drawLeft(); refreshPreview();
      } }, "Duplicate"),
      builtin ? null : h("button", { class: "btn small danger", onclick: () => {
        if (!confirm("Delete this template?")) return; delete CFG.templates[TPL]; TPL = "reminder"; markDirty(); drawLeft(); refreshPreview();
      } }, "Delete")),
      h("div", { class: "small muted", style: "margin-top:6px" }, "Reminder, Calendar and Email are the built-in looks. Duplicate one to make extra templates for rules or specific calendars.")));
    SCHEMA.template_groups.forEach((g, i) => left.append(h("details", { open: i === 3 || i === 5 },
      h("summary", {}, g.title), h("div", { class: "in" }, form(g.fields, g.fields.some(f => f.path === "t.order") ? null : null)))));
  }
  drawLeft(); refreshPreview();
}
async function refreshPreview() {
  const img = $(".preview img"); if (!img) return;
  try {
    const r = await api("/api/preview", { method: "POST", raw: true, json: { tpl: CFG.templates[TPL], sample: ($("#sampleSel") || {}).value || undefined } });
    if (!r.ok) throw new Error("preview failed");
    const url = URL.createObjectURL(await r.blob()); const old = img.src; img.src = url; if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  } catch (e) { /* ignore */ }
}

/* ---------------------------------------------------------------- calendar */
function calendar(main) {
  main.append(h("div", { class: "card" }, h("h2", {}, "Calendar reminders"), form(SCHEMA.sections.calendar)));
  const list = h("div");
  const card = h("div", { class: "card" }, h("h2", {}, "Calendars"),
    h("p", { class: "small muted" }, "In Google Calendar: Settings → your calendar → Integrate calendar. Use “Public address in iCal format” for a public calendar, or the “Secret address in iCal format” for a private one. It ends in .ics."),
    list, h("button", { class: "btn", onclick: () => { CFG.calendars.push({ id: Math.random().toString(36).slice(2, 10), name: "Calendar " + (CFG.calendars.length + 1), url: "", enabled: true, template: "calendar" }); markDirty(); draw(); } }, "+ Add calendar"));
  const up = h("div", { class: "card" });
  main.append(card, up);
  function draw() {
    list.replaceChildren(...CFG.calendars.map((c, i) => {
      const res = h("div", { class: "small muted" });
      return h("div", { class: "item" },
        h("div", { class: "row" },
          h("input", { type: "checkbox", checked: c.enabled, onchange: e => { c.enabled = e.target.checked; markDirty(); } }),
          h("input", { type: "text", value: c.name, style: "flex:1;min-width:120px", placeholder: "Name", oninput: e => { c.name = e.target.value; markDirty(); } }),
          h("button", { class: "btn small danger", onclick: () => { CFG.calendars.splice(i, 1); markDirty(); draw(); } }, "Remove")),
        h("div", { class: "f", style: "margin-top:8px" }, h("label", { class: "l" }, "iCal URL"),
          h("input", { type: "url", value: c.url, placeholder: "https://calendar.google.com/calendar/ical/…/basic.ics", oninput: e => { c.url = e.target.value.trim(); markDirty(); } })),
        h("div", { class: "f" }, h("label", { class: "l" }, "Template"),
          h("select", { onchange: e => { c.template = e.target.value; markDirty(); } },
            Object.entries(CFG.templates).map(([k, t]) => h("option", { value: k, selected: k === c.template }, t.label || k)))),
        h("div", { class: "row" }, h("button", { class: "btn small", onclick: async () => {
          res.textContent = "Checking…"; const r = await api("/api/test-calendar", { method: "POST", json: { url: c.url } });
          res.textContent = r.message + (r.events ? "  e.g. " + r.events.slice(0, 3).map(e => e.title).join(", ") : ""); res.style.color = r.ok ? "var(--ok)" : "var(--bad)";
        } }, "Test URL"), res));
    }));
    if (!CFG.calendars.length) list.append(h("div", { class: "muted", style: "margin-bottom:10px" }, "No calendars yet."));
  }
  async function upcoming() {
    const rows = await api("/api/calendar/upcoming");
    up.replaceChildren(h("div", { class: "row" }, h("h2", { style: "flex:1;margin:0" }, "Upcoming events & when they print"),
      h("button", { class: "btn small", onclick: async () => { await api("/api/sync-calendar", { method: "POST" }); toast("Syncing…"); setTimeout(upcoming, 3000); } }, "Sync now")),
      h("table", { class: "small", style: "width:100%;border-collapse:collapse;margin-top:8px" },
        ...rows.map(r => h("tr", { style: "border-bottom:1px solid var(--line)" },
          h("td", {}, r.title), h("td", { class: "muted" }, r.start.slice(5, 16).replace("T", " ")),
          h("td", {}, r.status === "printed" ? "✓ printed" : r.due ? "prints " + r.due.slice(5, 16).replace("T", " ") : "—")))),
      rows.length ? null : h("div", { class: "muted" }, "No upcoming events yet - save a calendar URL and press Sync now."));
  }
  draw(); upcoming();
}

/* ---------------------------------------------------------------- rules */
const R_FIELDS = [["sender", "Sender"], ["subject", "Subject"], ["body", "Body text"], ["has_image", "Has a picture"], ["is_forward", "Is a forward"], ["calendar", "Calendar name"]];
const R_OPS = [["contains", "contains"], ["not_contains", "doesn't contain"], ["equals", "equals"], ["starts_with", "starts with"], ["ends_with", "ends with"], ["regex", "matches regex"], ["is_true", "is yes"]];
function rules(main) {
  main.append(h("div", { class: "card small" }, h("h2", {}, "Rules for work & business"),
    "Rules run on every email and calendar event. Use them to give clients or bosses their own template, add a tag to the heading, print extra copies, mark things urgent, or ignore noise. Later rules override earlier ones."));
  const list = h("div");
  main.append(list, h("button", { class: "btn", onclick: () => { CFG.rules.push({ name: "New rule", enabled: true, match: "all", conditions: [{ field: "sender", op: "contains", value: "" }], actions: { template: "", title: "", heading_prefix: "", copies: 1, urgent: false, skip: false } }); markDirty(); draw(); } }, "+ Add rule"));
  const sel = (opts, val, on) => h("select", { onchange: e => on(e.target.value) }, opts.map(([v, l]) => h("option", { value: v, selected: v === val }, l)));
  function draw() {
    list.replaceChildren(...CFG.rules.map((r, i) => {
      const conds = h("div");
      const drawC = () => { conds.replaceChildren(...r.conditions.map((c, j) => h("div", { class: "cond" },
        sel(R_FIELDS, c.field, v => { c.field = v; markDirty(); }), sel(R_OPS, c.op, v => { c.op = v; markDirty(); }),
        h("input", { type: "text", value: c.value, placeholder: "text", oninput: e => { c.value = e.target.value; markDirty(); } }),
        h("button", { class: "btn small", onclick: () => { r.conditions.splice(j, 1); markDirty(); drawC(); } }, "✕")))); };
      drawC();
      const a = r.actions;
      return h("div", { class: "item" },
        h("div", { class: "row" }, h("input", { type: "checkbox", checked: r.enabled, onchange: e => { r.enabled = e.target.checked; markDirty(); } }),
          h("input", { type: "text", value: r.name, style: "flex:1;min-width:140px", oninput: e => { r.name = e.target.value; markDirty(); } }),
          h("button", { class: "btn small danger", onclick: () => { CFG.rules.splice(i, 1); markDirty(); draw(); } }, "Delete")),
        h("div", { class: "row", style: "margin:8px 0" }, "When", sel([["all", "ALL"], ["any", "ANY"]], r.match, v => { r.match = v; markDirty(); }), "of these match:"),
        conds, h("button", { class: "btn small", onclick: () => { r.conditions.push({ field: "subject", op: "contains", value: "" }); markDirty(); drawC(); } }, "+ condition"),
        h("h3", { style: "margin-top:12px" }, "Then"),
        h("div", { class: "row" },
          h("label", {}, "Template ", sel([["", "(default)"], ...Object.entries(CFG.templates).map(([k, t]) => [k, t.label || k])], a.template, v => { a.template = v; markDirty(); })),
          h("label", {}, "Copies ", h("input", { type: "number", min: 1, max: 5, value: a.copies, style: "width:70px", oninput: e => { a.copies = +e.target.value || 1; markDirty(); } }))),
        h("div", { class: "row", style: "margin-top:6px" },
          h("input", { type: "text", placeholder: "Title override, e.g. CLIENT", value: a.title, style: "flex:1;min-width:150px", oninput: e => { a.title = e.target.value; markDirty(); } }),
          h("input", { type: "text", placeholder: "Heading prefix, e.g. [WORK]", value: a.heading_prefix, style: "flex:1;min-width:150px", oninput: e => { a.heading_prefix = e.target.value; markDirty(); } })),
        h("div", { class: "checks", style: "margin-top:8px" },
          h("label", {}, h("input", { type: "checkbox", checked: a.urgent, onchange: e => { a.urgent = e.target.checked; markDirty(); } }), "Mark urgent"),
          h("label", {}, h("input", { type: "checkbox", checked: a.skip, onchange: e => { a.skip = e.target.checked; markDirty(); } }), "Don't print at all")));
    }));
    if (!CFG.rules.length) list.append(h("div", { class: "muted", style: "margin-bottom:12px" }, "No rules yet."));
  }
  draw();
}

/* ---------------------------------------------------------------- integrations */
function integrations(main) {
  main.append(h("div", { class: "card" }, h("h2", {}, "Integrations"), form(SCHEMA.sections.integrations),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: async () => { const r = await api("/api/generate-token", { method: "POST" }); CFG.integrations.api_token = r.token; SAVED.integrations.api_token = r.token; go("integrations"); toast("New token created"); } }, "Generate new token"))));
  const tok = CFG.integrations.api_token || "<token>";
  main.append(h("div", { class: "card small" }, h("h3", {}, "Print from anything with one HTTP call"),
    h("pre", {}, `curl -X POST http://${location.host}/api/print \\\n  -H "Authorization: Bearer ${tok}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"heading": "Work", "text": "call the bank, email Sam by Friday", "template": "reminder", "urgent": false}'`),
    "Text goes through the same spelling, splitting and deadline logic as email. Works from Zapier, Make, Home Assistant, iOS Shortcuts, cron jobs, etc.",
    h("h3", { style: "margin-top:14px" }, "Plugins"),
    "Drop a Python file into the plugins/ folder to change or add printouts (see plugins/example_tagger.py). ",
    SCHEMA.plugins.length ? "Loaded: " + SCHEMA.plugins.join(", ") : "None loaded."));
}

/* ---------------------------------------------------------------- playground */
function playground(main) {
  const subject = h("input", { type: "text", placeholder: "Subject (leave empty for none)" });
  const body = h("textarea", { style: "min-height:130px", placeholder: "wash dishes, walk the dog tomorrow at 5pm and reply to work email" });
  const fwd = h("input", { type: "checkbox" }), img = h("input", { type: "checkbox" });
  const fbody = h("textarea", { style: "min-height:130px", placeholder: "Paste the original email text here" });
  const fwrap = h("div", { hidden: true }, h("div", { class: "help small muted" }, "Text above = your actions. Pasted email below gets summarised."), fbody);
  fwd.addEventListener("change", () => { fwrap.hidden = !fwd.checked; });
  const out = h("div", { class: "pg", style: "margin-top:16px" });
  main.append(h("div", { class: "card" }, h("h2", {}, "Try it without sending an email"),
    h("p", { class: "small muted" }, "Type what you'd email and see exactly how it will be corrected, split and laid out. Nothing is printed."),
    h("div", { class: "f" }, h("label", { class: "l" }, "Subject"), subject),
    h("div", { class: "f" }, h("label", { class: "l" }, "Body"), body),
    h("div", { class: "checks" }, h("label", {}, fwd, "It's a forwarded email"), h("label", {}, img, "Has a photo attached")),
    fwrap,
    h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: async () => {
      const r = await api("/api/playground", { method: "POST", json: { subject: subject.value, body: body.value, forward: fwd.checked, fwd_body: fbody.value, with_image: img.checked } });
      out.replaceChildren(...r.cards.map(c => h("div", { class: "out" }, h("img", { src: "data:image/png;base64," + c.png }),
        c.notes.length ? h("div", { class: "small muted" }, "Notes: " + c.notes.join(", ")) : null)));
      if (!r.cards.length) out.append(h("div", { class: "muted" }, "Nothing would be printed."));
      if (r.copies > 1) out.prepend(h("div", { class: "small" }, `Rules ask for ${r.copies} copies of each.`));
    } }, "Preview")), out));
}

/* ---------------------------------------------------------------- print a custom message */
function printPage(main) {
  const heading = h("input", { type: "text", placeholder: "Heading (optional, printed in bold)", maxlength: 200 });
  const text = h("textarea", { style: "min-height:140px", placeholder: "wash dishes, walk the dog tomorrow at 5pm and reply to work email" });
  const files = h("input", { type: "file", accept: "image/*", multiple: true });
  const tpl = h("select", {}, [["", "Automatic"], ...Object.entries(CFG.templates).map(([k, t]) => [k, t.label || k])].map(([v, l]) => h("option", { value: v }, l)));
  const split = h("input", { type: "checkbox", checked: true }), exact = h("input", { type: "checkbox" }), urgent = h("input", { type: "checkbox" });
  const copies = h("input", { type: "number", min: 1, max: 5, value: 1, style: "width:80px" });
  const btn = h("button", { class: "btn primary", onclick: async () => {
    const fd = new FormData();
    fd.append("heading", heading.value); fd.append("text", text.value); fd.append("template", tpl.value);
    fd.append("split", split.checked ? "1" : "0"); fd.append("exact", exact.checked ? "1" : "0");
    fd.append("urgent", urgent.checked ? "1" : "0"); fd.append("copies", copies.value || "1");
    for (const f of files.files) fd.append("images", f);
    btn.disabled = true;
    try {
      const r = await api("/api/print-now", { method: "POST", body: fd });
      toast(r.cards > 1 ? `Printing ${r.cards} reminders` : "Printing…"); text.value = ""; heading.value = ""; files.value = "";
    } catch (e) { toast(e.message, true); }
    btn.disabled = false;
  } }, "Print");
  main.append(h("div", { class: "card" }, h("h2", {}, "Print a message"),
    h("p", { class: "small muted" }, "Type anything and it prints straight away (no queue). It's cleaned up, split into separate reminders and given DUE lines exactly like an email would be. Tick “exactly as typed” to skip all of that."),
    h("div", { class: "f" }, h("label", { class: "l" }, "Heading"), heading),
    h("div", { class: "f" }, h("label", { class: "l" }, "Text"), text),
    h("div", { class: "f" }, h("label", { class: "l" }, "Photos (optional)"), files, h("div", { class: "help" }, "With photos, everything prints as one reminder with the pictures underneath.")),
    h("div", { class: "f" }, h("label", { class: "l" }, "Template"), tpl),
    h("div", { class: "checks", style: "margin-bottom:12px" },
      h("label", {}, split, "Split into separate reminders"), h("label", {}, exact, "Print exactly as typed (no corrections)"),
      h("label", {}, urgent, "Mark urgent"), h("label", {}, "Copies ", copies)),
    btn));
}

/* ---------------------------------------------------------------- messages from friends + button */
function messages(main) {
  const list = h("div");
  main.append(h("div", { class: "card small" }, h("h2", {}, "Messages from friends"),
    "People on this list can email the printer's address. Their email prints as a ", h("strong", {}, "MESSAGE"),
    " with their name under it (subject = heading, body = text, photos underneath). Unless “Skip the queue” is ticked for them, messages wait until you press the button. ",
    "Your own addresses (the ", h("em", {}, "admin"), " list on the Email tab) always print immediately as reminders. Anyone not on either list is ignored."));
  const fcard = h("div", { class: "card" }, h("h3", {}, "Friends"), list,
    h("button", { class: "btn", onclick: () => { CFG.friends.push({ name: "", email: "", bypass_queue: false, enabled: true }); markDirty(); draw(); } }, "+ Add friend"));
  const waiting = h("div", { class: "card" });
  const bstat = h("div", { class: "small muted", style: "margin-bottom:8px" });
  const bcard = h("div", { class: "card" }, h("h3", {}, "Queue"), form(SCHEMA.sections.messages),
    h("h3", { style: "margin-top:16px" }, "Button & LED"), form(SCHEMA.sections.button), bstat,
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: async () => { if (dirty) return toast("Save first, then test.", true); const r = await api("/api/button-test", { method: "POST" }); toast(r.message, !r.ok); } }, "Flash the LED"),
      h("button", { class: "btn", onclick: async () => { const r = await api("/api/release", { method: "POST" }); toast(r.released ? `Printing ${r.released} message(s)` : "Nothing is waiting"); loadWaiting(); } }, "Press the button (from here)")),
    h("details", { style: "margin-top:14px" }, h("summary", {}, "Wiring guide"), h("div", { class: "in small" },
      h("p", {}, h("strong", {}, "Button: "), "one wire to GPIO17 (physical pin 11), the other to any GND (e.g. pin 9). No resistor needed."),
      h("p", {}, h("strong", {}, "LED: "), "GPIO27 (pin 13) → 330 Ω resistor → LED + ; LED − → GND (pin 14). This is right for a 3.3 V LED."),
      h("p", {}, h("strong", {}, "Illuminated arcade buttons "), "usually have a 5 V or 12 V LED. Don't connect those straight to a GPIO pin: drive the LED from the supply through a small transistor/MOSFET switched by GPIO27 (the Pi pin can only supply a few mA)."),
      h("p", {}, "Pin numbers above use GPIO (BCM) numbering; you can change them in the boxes."))));
  main.append(fcard, waiting, bcard);

  function draw() {
    list.replaceChildren(...CFG.friends.map((f, i) => h("div", { class: "item" },
      h("div", { class: "row" },
        h("input", { type: "checkbox", checked: f.enabled !== false, title: "Enabled", onchange: e => { f.enabled = e.target.checked; markDirty(); } }),
        h("input", { type: "text", value: f.name, placeholder: "Name printed on the message", style: "flex:1;min-width:130px", oninput: e => { f.name = e.target.value; markDirty(); } }),
        h("input", { type: "text", value: f.email, placeholder: "their@email.com", style: "flex:2;min-width:180px", oninput: e => { f.email = e.target.value; markDirty(); } }),
        h("label", { class: "row small" }, h("input", { type: "checkbox", checked: !!f.bypass_queue, onchange: e => { f.bypass_queue = e.target.checked; markDirty(); } }), "Skip the queue"),
        h("button", { class: "btn small danger", onclick: () => { CFG.friends.splice(i, 1); markDirty(); draw(); } }, "Remove")))));
    if (!CFG.friends.length) list.append(h("div", { class: "muted", style: "margin-bottom:10px" }, "No friends added yet."));
  }
  async function loadWaiting() {
    const [st, hs] = await Promise.all([api("/api/status"), api("/api/history?limit=200")]);
    const held = hs.filter(j => j.status === "held");
    bstat.textContent = "Button status: " + st.button.message;
    bstat.style.color = st.button.state === "error" ? "var(--bad)" : "";
    waiting.replaceChildren(h("div", { class: "row" }, h("h3", { style: "flex:1;margin:0" }, `Waiting for the button (${held.length})`),
      held.length ? h("button", { class: "btn small primary", onclick: async () => { await api("/api/release", { method: "POST" }); loadWaiting(); } }, "Print all") : null,
      held.length ? h("button", { class: "btn small danger", onclick: async () => { if (confirm("Discard all waiting messages?")) { await api("/api/discard-held", { method: "POST" }); loadWaiting(); } } }, "Discard all") : null),
      held.length ? h("div", { class: "hist", style: "margin-top:10px" }, held.map(j => h("div", { class: "it" },
        h("img", { src: `/history/${j.id}.png`, onclick: e => lightbox(e.target.src) }), h("div", { class: "t" }, j.title || "(no subject)"),
        h("div", { class: "row" },
          h("button", { class: "btn small", onclick: async () => { await api(`/api/history/${j.id}/release`, { method: "POST" }); loadWaiting(); } }, "Print"),
          h("button", { class: "btn small danger", onclick: async () => { await api(`/api/history/${j.id}/cancel`, { method: "POST" }); loadWaiting(); } }, "Discard")))))
        : h("div", { class: "muted small", style: "margin-top:6px" }, "Nothing waiting."));
  }
  draw(); loadWaiting(); POLL = setInterval(() => loadWaiting().catch(() => {}), 5000);
}

/* ---------------------------------------------------------------- symbols */
function symbols(main) {
  const grid = h("div", { class: "symgrid" });
  const csv = v => v.split(",").map(t => t.trim().replace(/^\[|\]$/g, "")).filter(Boolean);
  main.append(h("div", { class: "card small" }, h("h2", {}, "Symbols"),
    "Put a symbol's name in square brackets in the ", h("strong", {}, "subject or text"), " of an email and it prints above the heading. ",
    "Full or short name, any capitalisation: ", h("code", {}, "[school]"), " ", h("code", {}, "[!]"), " ", h("code", {}, "[3D printer]"), " ",
    h("code", {}, "[work][?]"), " (several are fine). A tag in the subject, or alone on the first line, applies to every reminder in the email; a tag on a task's own line applies to just that reminder. ",
    "Tags I don't know (like [draft]) are left as normal text. Works on friends' messages and calendar event titles too."),
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async () => { await api("/api/icons/print-sheet", { method: "POST" }); toast("Printing the symbol sheet"); } }, "Print the symbol cheat-sheet"),
      h("a", { class: "btn", href: "/api/symbol-chart.png?download=1", download: "jack-in-a-box-symbols.png" }, "Download symbol chart (PNG)"),
      h("button", { class: "btn", onclick: async () => { if (dirty) return toast("Save first.", true); await api("/api/test-print", { method: "POST", json: { template: "reminder" } }); toast("Test print queued"); } }, "Print a sample with symbols")));
  main.append(h("div", { class: "card" }, h("h3", {}, "Your symbols"), grid));

  function chip(t) { return h("span", { class: "chip" }, "[" + t.toLowerCase() + "]"); }
  function draw() {
    const I = CFG.icons;
    const items = [
      ...I.custom.map((c, i) => ({ id: c.id, name: c.name, custom: true, c, i, tags: [c.name, ...(c.tags || [])] })),
      ...(window.__builtin || []).map(b => ({ ...b, custom: false, tags: [b.name, ...b.short, ...(I.aliases[b.id] || [])] }))];
    grid.replaceChildren(...items.map(it => {
      const off = !it.custom && I.disabled.includes(it.id);
      return h("div", { class: "sym" + (off ? " off" : "") },
        h("img", { src: it.custom ? `/uploads/${encodeURIComponent(it.c.file)}` : `/api/icon/${it.id}.png?size=110`, loading: "lazy", width: 110, height: 110, style: "object-fit:contain" }),
        it.custom ? h("input", { type: "text", value: it.c.name, oninput: e => { it.c.name = e.target.value; markDirty(); } })
                  : h("div", { class: "t" }, it.name),
        h("div", { class: "chips" }, it.tags.filter(Boolean).slice(0, 6).map(chip)),
        h("input", { type: "text", placeholder: "extra tag names, comma separated",
          value: it.custom ? (it.c.tags || []).join(", ") : (I.aliases[it.id] || []).join(", "),
          oninput: e => { const v = csv(e.target.value); if (it.custom) it.c.tags = v; else if (v.length) I.aliases[it.id] = v; else delete I.aliases[it.id]; markDirty(); },
          onchange: draw }),
        h("div", { class: "row" },
          it.custom ? h("button", { class: "btn small danger", onclick: () => { I.custom.splice(it.i, 1); markDirty(); draw(); } }, "Delete")
            : h("label", { class: "row small" }, h("input", { type: "checkbox", checked: !off, onchange: e => {
                I.disabled = I.disabled.filter(x => x !== it.id); if (!e.target.checked) I.disabled.push(it.id); markDirty(); draw(); } }), "On")));
    }));
  }

  // ---- add your own: upload or draw
  const name = h("input", { type: "text", placeholder: "Name, e.g. Bike", maxlength: 30 });
  const tags = h("input", { type: "text", placeholder: "extra tag names, comma separated (optional)" });
  const mode = h("select", {}, [["line", "Line drawing (crisp black & white)"], ["photo", "Photo (greys)"]].map(([v, l]) => h("option", { value: v }, l)));
  const file = h("input", { type: "file", accept: "image/*" });
  const canvas = h("canvas", { width: 320, height: 320, class: "pad" });
  const pen = h("input", { type: "range", min: 2, max: 28, value: 9 });
  const ctx = canvas.getContext("2d");
  const clear = () => { ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, 320, 320); inked = false; };
  let inked = false, down = false, last = null;
  clear();
  const pos = e => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * 320 / r.width, (e.clientY - r.top) * 320 / r.height]; };
  canvas.addEventListener("pointerdown", e => { down = true; canvas.setPointerCapture(e.pointerId); last = pos(e); e.preventDefault();
    ctx.fillStyle = "#000"; ctx.beginPath(); ctx.arc(last[0], last[1], pen.value / 2, 0, 7); ctx.fill(); inked = true; });
  canvas.addEventListener("pointermove", e => { if (!down) return; const p = pos(e);
    ctx.strokeStyle = "#000"; ctx.lineWidth = pen.value; ctx.lineCap = ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(last[0], last[1]); ctx.lineTo(p[0], p[1]); ctx.stroke(); last = p; });
  const up = () => { down = false; };
  canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);

  async function addFromBlob(blob, defaultName) {
    const fd = new FormData(); fd.append("file", blob, "symbol.png");
    try {
      const r = await api("/api/upload", { method: "POST", body: fd });
      CFG.icons.custom.push({ id: Math.random().toString(36).slice(2, 10), name: name.value.trim() || defaultName, tags: csv(tags.value), file: r.file, mode: mode.value });
      name.value = tags.value = ""; file.value = ""; markDirty(); draw(); toast("Added - press Save to keep it");
    } catch (e) { toast(e.message, true); }
  }
  main.append(h("div", { class: "card" }, h("h3", {}, "Add your own symbol"),
    h("div", { class: "cols two" },
      h("div", {}, h("div", { class: "f" }, h("label", { class: "l" }, "Name"), name),
        h("div", { class: "f" }, h("label", { class: "l" }, "Extra tag names"), tags),
        h("div", { class: "f" }, h("label", { class: "l" }, "How to print it"), mode),
        h("div", { class: "f" }, h("label", { class: "l" }, "Upload a picture"), file,
          h("div", { class: "help" }, "Black-on-white drawings or logos work best.")),
        h("button", { class: "btn", onclick: () => { if (!file.files[0]) return toast("Choose a picture first", true); addFromBlob(file.files[0], "Symbol"); } }, "Add uploaded picture")),
      h("div", {}, h("label", { class: "l" }, "…or draw one (mouse, finger or pen)"), canvas,
        h("div", { class: "row", style: "margin-top:6px" }, "Pen ", pen,
          h("button", { class: "btn small", onclick: clear }, "Clear"),
          h("button", { class: "btn primary small", onclick: () => { if (!inked) return toast("Draw something first", true);
            canvas.toBlob(b => { addFromBlob(b, "Drawing"); clear(); }, "image/png"); } }, "Add my drawing"))))));

  api("/api/icons").then(r => { window.__builtin = r.icons.filter(i => i.builtin).map(i => ({ id: i.id, name: i.name, short: i.short })); draw(); });
}

/* ---------------------------------------------------------------- power & fan */
function powerPage(main) {
  const stat = h("div", { class: "small" });
  main.append(h("div", { class: "card" }, h("h2", {}, "Power & cooling"), stat));
  async function refresh() {
    const r = await api("/api/power-status");
    const th = r.throttle || {};
    stat.replaceChildren(...[
      h("div", {}, r.cpu_temp != null ? `CPU temperature: ${r.cpu_temp.toFixed(1)}°C` : "CPU temperature: not available"),
      h("div", {}, `Fan: ${r.fan.message || ""}${r.fan.state === "ok" ? (r.fan.fan_on ? "  - ON (" + (r.fan.reason || "") + ")" : "  - off") : ""}`),
      h("div", {}, `Idle for ${r.idle_minutes} min.  ${r.power.message || ""}`),
      th.undervoltage || th.undervoltage_seen ? h("div", { class: "err" }, "⚠ The Pi has reported low voltage: use a good 5 V / 2.5 A power supply and a short, thick cable.") : null,
      th.throttled || th.throttled_seen ? h("div", { class: "err" }, "⚠ The Pi has slowed itself down (too hot or low voltage). A fan will help.") : null].filter(Boolean));
  }
  refresh(); POLL = setInterval(() => refresh().catch(() => {}), 5000);

  main.append(h("div", { class: "card" }, h("h3", {}, "Cooling fan"), form(SCHEMA.sections.fan),
    h("details", {}, h("summary", {}, "Wiring guide"), h("div", { class: "in small" },
      h("p", {}, "A GPIO pin gives only a few mA, so it switches the fan through a transistor; it can't power it. For a small 5 V fan:"),
      h("pre", {}, "Pi 5V (pin 2/4) ──┬────────────┐\n                  │         fan (+)\n               diode           │\n         (1N4001, stripe       │\n          towards 5V)        fan (−)\n                  │            │\n                  └────────────┴── transistor collector\n\nGPIO22 (pin 15) ── 1 kΩ ── transistor base      (NPN: 2N2222 / BC337)\nGND (pin 14) ───────────── transistor emitter"),
      h("p", {}, "Or use a ready-made fan driver / MOSFET module with a 3.3 V logic input. Never connect a fan straight to a GPIO pin."))),
    h("div", { class: "small muted", style: "margin-top:8px" }, "Automatic mode runs the fan when the CPU is hot and for the whole time the AI is writing a summary, then lets it cool and switches off.")));

  main.append(h("div", { class: "card" }, h("h3", {}, "Power off when idle"), form(SCHEMA.sections.power),
    h("div", { class: "small" }, h("p", {}, h("strong", {}, "Read this: "), "a Pi 3B+ has no sleep mode and no clock to wake itself, so “deep sleep” means ", h("strong", {}, "switched off"),
      ". While it's off nothing prints and the button does nothing; email just waits in Gmail and everything catches up when it's back on. It never powers off while anything is waiting, an AI summary is running, or a calendar reminder is due soon."),
      h("p", {}, h("strong", {}, "Waking it: "), "wire a push button between ", h("strong", {}, "GPIO3 (pin 5) and GND (pin 6)"), " - pressing it boots a halted Pi. ",
        "Tip: set the message button's pin to ", h("code", {}, "3"), " on the Messages tab and the same button does both jobs (release messages when on, wake when off). Or re-plug the power."),
      h("p", {}, "Prefer scheduled nights off? Put the Pi on a smart plug/timer: it simply restarts in the morning and catches up."))));

  main.append(h("div", { class: "card small" }, h("h3", {}, "Lower idle power"),
    "Run ", h("code", {}, "bash deploy/eco-mode.sh"), " on the Pi (then reboot) to turn off the LEDs, Bluetooth and HDMI and put Wi-Fi in power-save. ",
    "The saving is modest (a few hundred milliwatts); the Pi already slows its CPU when idle. ", h("code", {}, "bash deploy/eco-mode.sh --undo"), " reverses it."));
}

/* ---------------------------------------------------------------- Google Form -> Sheet -> print */
function copyFrom(el) {
  el.focus(); el.select();
  try { document.execCommand("copy"); toast("Copied"); } catch (e) { toast("Press Ctrl+C (or Cmd+C) to copy", true); }
}
function formPage(main) {
  const status = h("div", { class: "small" });
  const symbols = h("textarea", { readonly: true, style: "min-height:150px;font-family:ui-monospace,monospace" });
  const script = h("textarea", { readonly: true, style: "min-height:200px;font-family:ui-monospace,monospace;font-size:.75rem", placeholder: "Press “Show my script”" });
  const result = h("div", { class: "small", style: "margin-top:8px" });
  const mapping = h("div", { class: "small muted", style: "margin-top:6px" });

  async function refresh() {
    const st = await api("/api/status"), s = st.sheet || {};
    status.replaceChildren(s.warning ? h("div", { style: "color:var(--bad);margin-bottom:4px" }, "⚠ " + s.warning) : "", h("div", {}, !CFG.sheet.enabled ? "Not switched on yet (Step 3)." :
      `Google Form link: ${s.state || "starting"}${s.last_check ? ", last checked " + s.last_check : ""}${s.error ? " - " + s.error : ""}${s.scheduled ? `. ${s.scheduled} scheduled, next prints ${s.next_scheduled}` : ""}`));
  }
  const queue = h("div", { class: "small" });
  async function loadQueue() {
    const hs = await api("/api/history?limit=200");
    const sch = hs.filter(j => j.status === "scheduled").reverse().sort((a, b) => (a.due || "").localeCompare(b.due || ""));
    queue.replaceChildren(h("h3", { style: "margin:10px 0 4px" }, `Waiting in the queue (${sch.length})`),
      sch.length ? h("div", { class: "hist" }, sch.map(j => h("div", { class: "it" },
        h("img", { src: `/history/${j.id}.png`, loading: "lazy", onclick: e => lightbox(e.target.src) }),
        h("div", { class: "t" }, j.title || "(untitled)"),
        h("div", { class: "when" }, "Prints " + j.due_text),
        h("div", { class: "row" },
          h("button", { class: "btn small primary", onclick: async () => { await api(`/api/history/${j.id}/release`, { method: "POST" }); toast("Printing now"); loadQueue(); } }, "Print now"),
          h("button", { class: "btn small danger", onclick: async () => { if (confirm("Cancel this scheduled printout?")) { await api(`/api/history/${j.id}/cancel`, { method: "POST" }); loadQueue(); } } }, "Cancel")))))
        : h("div", { class: "muted" }, "Nothing scheduled. A form row with a future Schedule Send time appears here the moment the box sees it, with the time it will print."));
  }
  refresh(); loadQueue(); POLL = setInterval(() => { refresh().catch(() => {}); loadQueue().catch(() => {}); }, 6000);
  api("/api/icons").then(r => { symbols.value = r.icons.filter(i => i.enabled).sort((a, b) => a.n - b.n).map(i => `${i.n} - ${i.name}`).join("\n"); });

  main.append(h("div", { class: "card" }, h("h2", {}, "Print from a Google Form"),
    h("p", { class: "small" }, "Make a Google Form (on your phone or computer) with a heading, body, up to three symbols and an image upload. Every time it's submitted, Google adds a row to a spreadsheet; this box checks the sheet, ",
      h("strong", {}, "prints each new row"), ", and ", h("strong", {}, "deletes the row once it has printed"), ". Rows waiting for the printer or for the button stay in the sheet until they print."), status, queue));

  main.append(h("div", { class: "card" }, h("h3", {}, "Step 1: make the form"),
    h("p", { class: "small" }, "Go to forms.google.com -> Blank form. Add these questions with exactly these titles (the box recognises them by name):"),
    h("ol", { class: "small" },
      h("li", {}, h("strong", {}, "Message or reminder?"), " - Dropdown: ", h("code", {}, "Reminder"), " and ", h("code", {}, "Message"), ". (Reminder = tidied up, split into tasks, dates found. Message = printed as written under a MESSAGE title.)"),
      h("li", {}, h("strong", {}, "Symbol 1"), ", ", h("strong", {}, "Symbol 2"), ", ", h("strong", {}, "Symbol 3"), " - Dropdown (optional). Paste the list below as the options."),
      h("li", {}, h("strong", {}, "Heading"), " - Short answer."),
      h("li", {}, h("strong", {}, "Body"), " - Paragraph."),
      h("li", {}, h("strong", {}, "Image"), " - File upload (images only, up to 3 files, 10 MB)."),
      h("li", {}, h("strong", {}, "Schedule Send (DATE)"), " - ", h("strong", {}, "Date"), " question (optional) and ", h("strong", {}, "Schedule Send (TIME)"), " - ", h("strong", {}, "Time"), " question (optional). ",
        "Both blank (or a moment that has already passed) = prints straight away. Date + time = prints at that moment. Time only = that time on the day you sent the form. ",
        "Date only = that morning at the calendar print time (7:00 by default); today's date alone prints now. The row waits safely in the sheet until it prints.")),
    h("p", { class: "small" }, "Finally ", h("strong", {}, "Responses tab -> Link to Sheets"), " to create the spreadsheet. Everything submitted through the form prints straight away, as you (it never waits for the button). ",
      "If you share the form with others and want friends' entries to wait for the button, switch Step 3 to “decide by sender” and turn on ", h("strong", {}, "Settings -> Responses -> Collect email addresses: Verified"), "."),
    h("div", { class: "f" }, h("label", { class: "l" }, "Symbol dropdown options (one per line: number - name)"), symbols,
      h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => copyFrom(symbols) }, "Copy list"))),
    h("div", { class: "f" }, h("label", { class: "l" }, "Symbol picture for the form"),
      h("div", { class: "help" }, "Add this to the Symbol 1 question (the picture icon on the question) so people can see what each number is. It always matches your current symbols."),
      h("img", { src: "/api/symbol-chart.png", style: "max-width:100%;border:1px solid var(--line);background:#fff", alt: "Chart of all symbols with numbers and names" }),
      h("div", { class: "row", style: "margin-top:6px" }, h("a", { class: "btn small", href: "/api/symbol-chart.png?download=1", download: "jack-in-a-box-symbols.png" }, "Download the picture"))),
    h("p", { class: "small muted" }, "Good to know: Google only allows file uploads from people signed in to a Google account, and uploaded pictures are kept in your Google Drive. Only share the form with people you trust.")));

  main.append(h("div", { class: "card" }, h("h3", {}, "Step 2: add the script to the spreadsheet"),
    h("ol", { class: "small" },
      h("li", {}, "Open the linked spreadsheet -> ", h("strong", {}, "Extensions -> Apps Script"), "."),
      h("li", {}, "Delete what's there, press ", h("strong", {}, "Show my script"), " below, ", h("strong", {}, "Copy script"), ", paste it in, and Save."),
      h("li", {}, h("strong", {}, "Deploy -> New deployment"), " -> type ", h("strong", {}, "Web app"), " -> Execute as: ", h("strong", {}, "Me"), "; Who has access: ", h("strong", {}, "Anyone"), " -> Deploy."),
      h("li", {}, "Google asks you to authorise it (“unverified app”: Advanced -> Go to ... -> Allow). That's your own script reading your own sheet."),
      h("li", {}, "Copy the ", h("strong", {}, "Web app URL"), " it shows.")),
    h("p", { class: "small" }, h("strong", {}, "Already set up before? "), "When this box gets a new script (for example to add Schedule Send), paste the new one over the old one, Save, then ",
      h("strong", {}, "Deploy -> Manage deployments -> pencil -> Version: New version -> Deploy"), ". Your Web app URL and secret code stay the same."),
    h("p", { class: "small muted" }, "“Anyone” just means no Google login is needed to call it; every request still has to carry the secret code that's inside your script, so nobody else can read or change the sheet through it."),
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async () => { const r = await api("/api/sheet-script", { method: "POST", json: {} }); script.value = r.script; toast("Script ready: press Copy script"); } }, "Show my script"),
      h("button", { class: "btn", onclick: () => script.value ? copyFrom(script) : toast("Press “Show my script” first", true) }, "Copy script"),
      h("button", { class: "btn small danger", onclick: async () => { if (!confirm("Make a new secret code? You must paste the new script into the sheet again.")) return; const r = await api("/api/sheet-script", { method: "POST", json: { regenerate: true } }); script.value = r.script; toast("New code made: paste the new script into the sheet"); } }, "Make a new secret code")),
    script));

  main.append(h("div", { class: "card" }, h("h3", {}, "Step 3: connect it"), form(SCHEMA.sections.sheet),
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: async () => {
        if (dirty) return toast("Save your changes first, then test.", true);
        result.textContent = "Connecting to Google…"; result.style.color = "";
        const r = await api("/api/sheet-test", { method: "POST" }); result.textContent = r.message; result.style.color = r.ok ? "var(--ok)" : "var(--bad)";
        if (r.ok) {
          const m = r.mapping, sy = (m.symbols || []).join(", ") || "none";
          mapping.textContent = `Recognised columns - type: ${m.type || "none (all rows = Reminder)"}; heading: ${m.heading || "none"}; body: ${m.body || "none"}; symbols: ${sy}; image: ${m.image || "none"}; schedule: ${m.sched_date || m.sched_time ? [m.sched_date, m.sched_time].filter(Boolean).join(" + ") : "none (everything prints straight away)"}; sender email: ${m.email || "none (uses the 'someone not on your lists' setting)"}.`;
          if (r.warning) mapping.textContent += "  ⚠ " + r.warning;
          if ((r.schedule || []).length) mapping.textContent += `  Waiting rows - ${r.schedule.map((x, i) => `#${i + 1}: ${x}`).join("; ")}. (Sheet timezone: ${r.sheet_timezone || "not reported - old script"}.)`;
        } else mapping.textContent = "";
      } }, "Test connection"),
      h("button", { class: "btn", onclick: async () => { await api("/api/sheet-check", { method: "POST" }); toast("Checking the sheet now…"); setTimeout(refresh, 3000); } }, "Check the sheet now")),
    result, mapping));
}

/* ---------------------------------------------------------------- account */
function account(main) {
  const o = h("input", { type: "password", autocomplete: "current-password" }), n = h("input", { type: "password", autocomplete: "new-password" });
  const upd = h("div", { class: "small", style: "margin:8px 0" });
  async function loadUpdate() {
    const r = await api("/api/update-status");
    const v = r.version, st = r.status;
    upd.replaceChildren(
      h("div", {}, r.git ? `Version ${v.commit} (${v.date}) on branch “${v.branch}” - ${v.subject}` : "Version unknown (not installed from git)."),
      st ? h("div", { style: "color:" + (st.result === "error" || st.result === "rolled_back" ? "var(--bad)" : "var(--ok)") },
        `Last check ${st.checked}: ${st.message}`) : h("div", { class: "muted" }, "No update check has run yet."));
  }
  loadUpdate();
  main.append(h("div", { class: "card" }, h("h2", {}, "Software updates"), form(SCHEMA.sections.updates), upd,
      h("div", { class: "row" }, h("button", { class: "btn", onclick: async () => {
        const r = await api("/api/update-now", { method: "POST" }); toast(r.message, !r.ok);
        if (r.ok) { setTimeout(loadUpdate, 6000); setTimeout(loadUpdate, 30000); }
      } }, "Check & update now"))),
    h("div", { class: "card" }, h("h2", {}, "General"), form(SCHEMA.sections.general)),
    h("div", { class: "card" }, h("h2", {}, "Change password"),
      h("div", { class: "f" }, h("label", { class: "l" }, "Current password"), o),
      h("div", { class: "f" }, h("label", { class: "l" }, "New password (6+ characters)"), n),
      h("button", { class: "btn", onclick: async () => {
        try { await api("/api/password", { method: "POST", json: { old: o.value, new: n.value } }); o.value = n.value = ""; toast("Password changed"); } catch (e) { toast(e.message, true); }
      } }, "Change password")));
}

/* ---------------------------------------------------------------- save / boot */
$("#save").addEventListener("click", async () => {
  try {
    CFG = await api("/api/config", { method: "PUT", json: CFG }); SAVED = JSON.parse(JSON.stringify(CFG));
    dirty = false; $("#savebar").hidden = true; toast("Saved"); go(TAB);
  } catch (e) { toast("Could not save: " + e.message, true); }
});
$("#discard").addEventListener("click", () => { CFG = JSON.parse(JSON.stringify(SAVED)); markDirty(); go(TAB); });
window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

async function boot() {
  const st = await api("/api/state");
  if (st.setup_needed) return showGate(true);
  if (!st.logged_in) return showGate(false);
  [CFG, SCHEMA] = await Promise.all([api("/api/config"), api("/api/schema")]);
  SAVED = JSON.parse(JSON.stringify(CFG));
  go(TAB);
}
boot().catch(e => { if (e.message !== "login") console.error(e); });


/* ---------------------------------------------------------------- AI review: history, corrections, training data */
function aiReviewPage(main) {
  let filter = "new";
  const counts = h("div", { class: "small" }), list = h("div", {}), rulesBox = h("div", {});
  const RULE_FIELDS = ["llm.rules", "llm.use_my_examples", "llm.examples", "llm.keep_history", "llm.history_days"];
  const drawRules = () => rulesBox.replaceChildren(form(SCHEMA.sections.text.filter(f => RULE_FIELDS.includes(f.path))));
  const bulletText = b => b.startsWith("ACTION:") ? "☐ " + b.slice(7).trim() : "• " + b;

  main.append(h("div", { class: "card" }, h("h2", {}, "AI review: teach it what a good summary looks like"),
    h("p", { class: "small" }, "Every forwarded email the box summarises is kept here (on the Pi only), with the printout and what the AI wrote. Mark each one ",
      h("strong", {}, "good"), " or type ", h("strong", {}, "your version"), ". Then:"),
    h("ul", { class: "small" },
      h("li", {}, h("strong", {}, "Worked examples. "), "Your good and corrected summaries are shown to the model before a similar email, ahead of the built-in examples. This is what actually improves the next summary."),
      h("li", {}, h("strong", {}, "Standing rules. "), "A comment such as “always say who it's from” can be made a rule that's added to the AI's instructions every time."),
      h("li", {}, h("strong", {}, "Real training. "), "A 1 GB Pi can't retrain a model, but you can download everything you reviewed as training data (JSONL) for fine-tuning on a Mac or PC.")),
    counts,
    h("div", { class: "row", style: "margin-top:8px" },
      h("a", { class: "btn small", href: "/api/ai-training.jsonl", download: "jack-in-a-box-ai-training.jsonl" }, "Download training data"),
      h("button", { class: "btn small danger", onclick: async () => {
        if (!confirm("Delete the history of emails you haven't reviewed? Summaries you marked good or corrected are kept.")) return;
        const r = await api("/api/ai-runs/clear", { method: "POST", json: {} }); toast(`Deleted ${r.deleted}`); load();
      } }, "Delete unreviewed history"))));
  main.append(h("div", { class: "card" }, h("h3", {}, "Your rules and settings"), rulesBox,
    h("p", { class: "small muted" }, "Changes here use the usual Save bar at the bottom.")));
  drawRules();

  const filt = h("select", { onchange: e => { filter = e.target.value; load(); } },
    [["new", "Waiting for my review"], ["reviewed", "Reviewed"], ["ai", "Written by the AI"], ["all", "Everything"]].map(([v, l]) => h("option", { value: v }, l)));
  main.append(h("div", { class: "card" }, h("div", { class: "row" }, h("h3", { style: "flex:1;margin:0" }, "Summaries"), filt), list));

  function runCard(r) {
    const text = (r.corrected || r.bullets).join("\n");
    const box = h("textarea", { style: "min-height:120px", spellcheck: "true" }, text);
    const comment = h("input", { type: "text", placeholder: "Comment (optional): what was wrong or what you'd prefer", value: r.comment || "", style: "width:100%" });
    const rule = h("input", { type: "checkbox" });
    const send = async verdict => {
      const res = await api(`/api/ai-runs/${r.id}/feedback`, { method: "POST", json: { verdict, corrected: box.value, comment: comment.value, rule: rule.checked } });
      toast(verdict === "bad" ? "Noted" : "Saved: it will learn from this");
      if (rule.checked && !dirty) { CFG = await api("/api/config"); SAVED = JSON.parse(JSON.stringify(CFG)); drawRules(); }
      load();
    };
    const label = { new: "waiting", good: "marked good", fix: "corrected", bad: "marked bad" }[r.status];
    return h("div", { class: "card", style: "background:var(--bg)" },
      h("div", { class: "row", style: "align-items:flex-start" },
        r.job_id ? h("img", { src: `/history/${r.job_id}.png`, loading: "lazy", style: "width:90px;border:1px solid var(--line);background:#fff", onerror: e => e.target.remove(), onclick: e => lightbox(e.target.src) }) : null,
        h("div", { style: "flex:1;min-width:0" },
          h("div", {}, h("strong", {}, r.subject || "(no subject)"), "  ", h("span", { class: "badge " + (r.status === "new" ? "pending" : r.status === "bad" ? "failed" : "printed") }, label)),
          h("div", { class: "small muted" }, `${r.sender ? "From " + r.sender + " · " : ""}${r.created.slice(5, 16).replace("T", " ")} · ` +
            (r.used_ai ? `written by the AI (${r.model}, ${r.seconds}s)` : "built-in summary" + (r.reason ? ` (${r.reason})` : ""))))),
      h("div", { class: "small", style: "margin:8px 0 2px" }, h("strong", {}, "What it wrote")),
      h("div", { class: "small" }, r.bullets.map(b => h("div", {}, bulletText(b)))),
      h("details", { class: "small", style: "margin:6px 0" }, h("summary", {}, "What the AI was shown"),
        h("pre", { style: "white-space:pre-wrap;font-size:.75rem", id: `ai-in-${r.id}` }, "")),
      h("label", { class: "small" }, h("strong", {}, "Your version"), " (one bullet per line; start a line with ", h("code", {}, "ACTION:"), " for something to do)"),
      box, h("div", { style: "margin-top:6px" }, comment),
      h("label", { class: "small", style: "display:block;margin:4px 0" }, rule, " make my comment a standing rule"),
      h("div", { class: "row" },
        h("button", { class: "btn small primary", onclick: () => send("good") }, "✔ It's good"),
        h("button", { class: "btn small", onclick: () => send("fix") }, "Save my version"),
        h("button", { class: "btn small", onclick: () => send("bad") }, "✘ Bad, no replacement"),
        h("button", { class: "btn small danger", onclick: async () => { if (confirm("Delete this from the history?")) { await api(`/api/ai-runs/${r.id}/delete`, { method: "POST" }); load(); } } }, "Delete")));
  }

  async function load() {
    const r = await api("/api/ai-runs?filter=" + filter + "&limit=30"), c = r.counts;
    counts.textContent = r.keep ? `${c.total} kept · ${c.new} waiting for your review · ${c.good} good · ${c.fix} corrected · ${c.bad} marked bad` : "History is switched off (see the setting below), so nothing new is being kept.";
    list.replaceChildren(...(r.runs.length ? r.runs.map(runCard) : [h("div", { class: "muted small" }, filter === "new" ? "Nothing waiting. Forward an email to the printer and its summary appears here." : "Nothing here yet.")]));
    for (const run of r.runs) api(`/api/ai-runs/${run.id}`).then(full => { const el = document.getElementById(`ai-in-${run.id}`); if (el) el.textContent = full.email; }).catch(() => {});
  }
  load();
}
