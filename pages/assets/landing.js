/* AiSearch landing — chatbot demo, cloud tabs, docs filter */
(function () {
  "use strict";

  /* ── 1. Chatbot demo ───────────────────────────────────────────────────── */
  // Canned turns modelled on agents/employee-support-copilot: the BFF classifies
  // the domain, calls AiSearch /retrieve with per-collection Atlas overrides,
  // then renders a grounded answer with citations.
  var SCENARIOS = {
    pto: {
      q: "How many PTO days do I get per year?",
      domain: "🧑‍💼 Employee Support",
      strategy: "hybrid · $rankFusion",
      ms: "412 ms",
      answer:
        "Full-time employees accrue <b>20 PTO days per year</b>, front-loaded on Jan 1. " +
        "Accrual increases to <b>25 days after 3 years</b> of tenure. Unused days roll over " +
        "up to a 40-hour cap.",
      cites: ["Employee Handbook · p.14", "Leave & Time-Off Policy", "Benefits FAQ"],
    },
    vpn: {
      q: "My VPN keeps disconnecting on my MacBook",
      domain: "💻 IT Helpdesk",
      strategy: "vector · $vectorSearch",
      ms: "358 ms",
      answer:
        "This is usually the <b>MTU mismatch on macOS Sonoma</b>. Set MTU to <b>1380</b> " +
        "(Network → Details → Hardware), then re-auth the client. If drops persist, switch " +
        "the tunnel protocol from UDP to TCP in the VPN profile.",
      cites: ["VPN Troubleshooting Guide", "macOS Known Issues", "KB-2214"],
    },
    payroll: {
      q: "How do I update my direct deposit?",
      domain: "🧑‍💼 Employee Support",
      strategy: "fulltext · $search",
      ms: "296 ms",
      answer:
        "Go to <b>Workday → Pay → Payment Elections</b>, add the new account and set it as " +
        "your primary election. Changes take effect from the <b>next payroll cycle</b>; " +
        "a micro-deposit verification is required for new accounts.",
      cites: ["Payroll & Compensation Policy", "Workday How-To · p.3"],
    },
  };

  var body = document.getElementById("cd-body");
  var busy = false;

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function scrollDown() { body.scrollTop = body.scrollHeight; }

  function addUser(text) {
    var m = el("div", "cd-msg user");
    m.appendChild(el("div", "bubble", text));
    body.appendChild(m); scrollDown();
  }

  function addBot(s) {
    var m = el("div", "cd-msg bot");
    var b = el("div", "bubble");
    var badges = el("div", "cd-badges");
    badges.appendChild(el("span", "cd-badge", "routed → " + s.domain));
    badges.appendChild(el("span", "cd-badge strategy", s.strategy));
    badges.appendChild(el("span", "cd-badge time", s.ms));
    b.appendChild(badges);
    var text = el("div", "cd-text");
    b.appendChild(text);
    var cites = el("div", "cd-cites");
    cites.appendChild(el("span", "lbl", "Citations<br>"));
    s.cites.forEach(function (c) { cites.appendChild(el("span", "cd-cite", "📄 " + c)); });
    m.appendChild(b);
    body.appendChild(m); scrollDown();

    // Typewriter effect for the answer; citations appear at the end.
    var plain = s.answer, i = 0;
    (function tick() {
      i += 4;
      text.innerHTML = plain.slice(0, i);
      scrollDown();
      if (i < plain.length) { setTimeout(tick, 14); }
      else { text.innerHTML = plain; b.appendChild(cites); scrollDown(); busy = false; }
    })();
  }

  function ask(key) {
    if (busy) return;
    busy = true;
    var s = SCENARIOS[key];
    addUser(s.q);
    var m = el("div", "cd-msg bot");
    var b = el("div", "bubble");
    b.appendChild(el("span", "typing", "<i></i><i></i><i></i>"));
    m.appendChild(b); body.appendChild(m); scrollDown();
    setTimeout(function () { m.remove(); addBot(s); }, 900);
  }

  if (body) {
    document.querySelectorAll("[data-ask]").forEach(function (btn) {
      btn.addEventListener("click", function () { ask(btn.getAttribute("data-ask")); });
    });
    ask("pto"); // seed the conversation on load
  }

  /* ── 2. Cloud tabs ─────────────────────────────────────────────────────── */
  var tabs = document.querySelectorAll(".cloudtabs [role=tab]");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) { t.setAttribute("aria-selected", "false"); });
      tab.setAttribute("aria-selected", "true");
      document.querySelectorAll(".cloudpanel").forEach(function (p) {
        p.classList.toggle("active", p.id === tab.getAttribute("aria-controls"));
      });
    });
  });

  /* ── 3. Docs filter ────────────────────────────────────────────────────── */
  var input = document.getElementById("filter");
  if (input) {
    input.addEventListener("input", function () {
      var q = input.value.toLowerCase();
      document.querySelectorAll(".card").forEach(function (c) {
        c.style.display =
          !q || c.dataset.title.includes(q) || c.textContent.toLowerCase().includes(q) ? "" : "none";
      });
      document.querySelectorAll("main.portal section").forEach(function (s) {
        s.style.display = Array.prototype.some.call(
          s.querySelectorAll(".card"),
          function (c) { return c.style.display !== "none"; }
        ) ? "" : "none";
      });
    });
  }
})();
