/* AiSearch Connection Playground — interactive agent ↔ backend demo.
 *
 * Faithfully mirrors the real request path:
 *   agents/employee-support-copilot/src/lib/classifier.ts      (domain routing)
 *   agents/employee-support-copilot/src/lib/AiSearch-client.ts (payload + wire)
 *   AiSearch/api/app.py                                        (REST surface)
 */
(function () {
  "use strict";

  /* ════════════════════════════════════════════════════════════════════════
     1. Domain registry — mirror of COLLECTION_CONFIGS in collections.ts
     ════════════════════════════════════════════════════════════════════════ */
  var COLLECTIONS = {
    IT_helpdesk: {
      label: "IT Helpdesk",
      icon: "💻",
      collection: "IT_helpdesk",
      vectorIndex: "it_helpdesk_vector_index",
      searchIndex: "it_helpdesk_search_index",
      textKey: "text",
      embeddingKey: "embedding",
      hybridWeights: { vectorWeight: 0.55, fulltextWeight: 0.45 },
      numCandidates: 150,
    },
    employee_support: {
      label: "Employee Support",
      icon: "🧑‍💼",
      collection: "employee_support",
      vectorIndex: "employee_support_vector_index",
      searchIndex: "employee_support_search_index",
      textKey: "text",
      embeddingKey: "embedding",
      hybridWeights: { vectorWeight: 0.5, fulltextWeight: 0.5 },
      numCandidates: 150,
    },
  };

  /* Keyword/pattern signals — condensed port of DOMAIN_SIGNALS. */
  var DOMAIN_SIGNALS = {
    IT_helpdesk: {
      patterns: [
        /\bvpn\b/i, /\bsso\b/i, /\bmfa\b/i, /\b(two.?factor|2fa)\b/i,
        /\boutlook\b/i, /\b(wi.?fi|wireless)\b/i,
        /\b(laptop|macbook|desktop|device)\b/i, /\bpassword\s+reset\b/i,
        /\b(install|software|application|app)\b/i,
        /\b(access|permission|login|sign.?in|authenticate)\b/i,
        /\b(printer|printing|print)\b/i,
        /\b(network|firewall|proxy|dns)\b/i,
      ],
      keywords: ["vpn", "laptop", "desktop", "mfa", "sso", "login", "password",
        "wifi", "printer", "install", "software", "access", "device", "outlook",
        "teams", "zoom", "browser", "certificate", "antivirus"],
    },
    employee_support: {
      patterns: [
        /\b(leave|vacation|pto|time.?off|sick.?leave|maternity|paternity)\b/i,
        /\b(payroll|salary|payslip|pay\s+stub|compensation)\b/i,
        /\b(direct\s+deposit|paycheck)\b/i,
        /\b(reimbursement|expense|travel\s+(claim|policy|allowance))\b/i,
        /\b(health\s+insurance|benefits|401k|pension|provident\s+fund)\b/i,
        /\b(hr|human\s+resources|people\s+ops|people\s+team)\b/i,
        /\b(holiday\s+(calendar|list)|public\s+holiday)\b/i,
        /\b(promotion|performance\s+review|appraisal|increment)\b/i,
        /\b(work.?from.?home|remote\s+work\s+policy|wfh\s+policy)\b/i,
        /\b(code\s+of\s+conduct|ethics|policy)\b/i,
      ],
      keywords: ["leave", "vacation", "payroll", "salary", "hr", "benefits",
        "insurance", "reimbursement", "travel", "expense", "holiday", "policy",
        "promotion", "appraisal", "bonus", "onboarding", "resignation", "deposit"],
    },
  };

  var VECTOR_HEAVY = [
    /acting weird/i, /not working properly/i, /keeps (crashing|disconnecting)/i,
    /slow(ly)?/i, /international(ly)?/i, /remotely/i, /best way to/i,
    /how (do|can|should) i/i, /explain/i, /what (is|are|does)/i,
  ];
  var FULLTEXT_HEAVY = [
    /policy$/i, /guide$/i, /\bcap\b/i, /\blimit\b/i, /\bform\b/i,
    /\bsteps\b/i, /\bprocess\b/i, /\bprocedure\b/i, /\beligib/i, /\bcriteria\b/i,
  ];

  var AMBIGUITY_THRESHOLD = 0.55;
  var PATTERN_WEIGHT = 3;
  var KEYWORD_WEIGHT = 1;

  /* ════════════════════════════════════════════════════════════════════════
     2. Deployed environments — where the agent runs + how it reaches AiSearch
        (mirrors the transports in AiSearch-client.ts and the deployment READMEs)
     ════════════════════════════════════════════════════════════════════════ */
  var ENVS = {
    local: {
      label: "Local",
      agentHost: "Next.js dev server · localhost:3000",
      backend: "FastAPI · http://localhost:8000",
      method: "POST",
      url: "http://localhost:8000/retrieve",
      auth: ["Content-Type: application/json", "# optional: Authorization: Bearer $AISEARCH_API_KEY"],
      envelope: "direct",
      hopSub: "POST /retrieve",
      docs: [
        ["Run locally — README", "README.html"],
        ["Copilot quick start", "agents/employee-support-copilot/README.html"],
      ],
    },
    gcp: {
      label: "Google Cloud",
      agentHost: "Cloud Run · employee-support-copilot",
      backend: "Vertex AI Agent Engine (Reasoning Engine)",
      method: "POST",
      url: "https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT/locations/us-central1/reasoningEngines/$ENGINE_ID:query",
      auth: ["Content-Type: application/json", "Authorization: Bearer <OAuth2 token from ADC>"],
      envelope: "agent-engine",
      hopSub: "POST :query · Agent Engine envelope",
      docs: [
        ["Copilot on Cloud Run", "deployment/google/agents/README.html"],
        ["AiSearch on Agent Engine", "deployment/google/agent_runtime/README.html"],
        ["AiSearch on Cloud Run", "deployment/google/cloud_run/README.html"],
      ],
    },
    aws: {
      label: "AWS",
      agentHost: "Amplify Hosting (SSR) · copilot",
      backend: "ECS Express · AiSearch FastAPI service",
      method: "POST",
      url: "https://AiSearch-api.us-east-1.ecs.aws.dev/retrieve",
      auth: ["Content-Type: application/json", "Authorization: Bearer $AISEARCH_API_KEY  # Secrets Manager"],
      envelope: "direct",
      hopSub: "POST /retrieve · bearer key",
      docs: [
        ["Copilot on Amplify", "deployment/aws/amplify/README.html"],
        ["AiSearch on ECS Express", "deployment/aws/ecs/README.html"],
      ],
    },
    azure: {
      label: "Azure",
      agentHost: "Container Apps · copilot",
      backend: "Container Apps · AiSearch-api",
      method: "POST",
      url: "https://AiSearch-api.wittymoss-1234.eastus.azurecontainerapps.io/retrieve",
      auth: ["Content-Type: application/json", "Authorization: Bearer $AISEARCH_API_KEY  # Key Vault"],
      envelope: "direct",
      hopSub: "POST /retrieve · bearer key",
      docs: [
        ["Container Apps + AI Foundry", "deployment/azure/DEPLOYMENT.html"],
      ],
    },
  };

  /* ════════════════════════════════════════════════════════════════════════
     3. Canned AiSearch responses (modelled on real /retrieve replies)
     ════════════════════════════════════════════════════════════════════════ */
  var SCENARIOS = {
    pto: {
      q: "How many PTO days do I get per year?",
      domain: "employee_support",
      strategy: "hybrid",
      strategyLabel: "hybrid · $rankFusion",
      answer:
        "Full-time employees accrue <b>20 PTO days per year</b>, front-loaded on Jan 1. " +
        "Accrual increases to <b>25 days after 3 years</b> of tenure. Unused days roll over " +
        "up to a 40-hour cap; anything above that expires on Dec 31.",
      cites: ["Employee Handbook · p.14", "Leave & Time-Off Policy", "Benefits FAQ"],
      pipeline: "db.employee_support.aggregate([\n  { $vectorSearch: { index: \"employee_support_vector_index\", numCandidates: 150, … } },\n  { $search: { index: \"employee_support_search_index\", … } },\n  { $rankFusion: { input: { pipelines: { vector: …, fulltext: … } } } }\n])",
      timings: { classificationMs: 2, AiSearchMs: 412, mongoMs: 187 },
    },
    vpn: {
      q: "My VPN keeps disconnecting on my MacBook",
      domain: "IT_helpdesk",
      strategy: "vector",
      strategyLabel: "vector · $vectorSearch",
      answer:
        "This is usually the <b>MTU mismatch on macOS Sonoma</b>. Set MTU to <b>1380</b> " +
        "(Network → Details → Hardware), then re-auth the client. If drops persist, switch " +
        "the tunnel protocol from UDP to TCP in the VPN profile.",
      cites: ["VPN Troubleshooting Guide", "macOS Known Issues", "KB-2214"],
      pipeline: "db.IT_helpdesk.aggregate([\n  { $vectorSearch: {\n      index: \"it_helpdesk_vector_index\",\n      path: \"embedding\", numCandidates: 150, limit: 8 } }\n])",
      timings: { classificationMs: 1, AiSearchMs: 358, mongoMs: 164 },
    },
    payroll: {
      q: "How do I update my direct deposit?",
      domain: "employee_support",
      strategy: "fulltext",
      strategyLabel: "fulltext · $search",
      answer:
        "Go to <b>Workday → Pay → Payment Elections</b>, add the new account and set it as " +
        "your primary election. Changes take effect from the <b>next payroll cycle</b>; " +
        "a micro-deposit verification is required for new accounts.",
      cites: ["Payroll & Compensation Policy", "Workday How-To · p.3"],
      pipeline: "db.employee_support.aggregate([\n  { $search: { index: \"employee_support_search_index\",\n      text: { query: \"update direct deposit\", path: \"text\" } } },\n  { $limit: 8 }\n])",
      timings: { classificationMs: 2, AiSearchMs: 296, mongoMs: 121 },
    },
    mfa: {
      q: "How do I set up MFA on a new phone?",
      domain: "IT_helpdesk",
      strategy: "hybrid",
      strategyLabel: "hybrid · $rankFusion",
      answer:
        "Open <b>Okta → Settings → Extra Verification</b> and choose <b>Reset beside Okta Verify</b>. " +
        "Scan the new QR code from your new phone, then approve the test push. Hardware tokens " +
        "must be re-issued by the IT Service Desk.",
      cites: ["MFA Setup Guide", "Okta End-User FAQ", "KB-1042"],
      pipeline: "db.IT_helpdesk.aggregate([\n  { $vectorSearch: { index: \"it_helpdesk_vector_index\", … } },\n  { $search: { index: \"it_helpdesk_search_index\", … } },\n  { $rankFusion: … }\n])",
      timings: { classificationMs: 2, AiSearchMs: 377, mongoMs: 172 },
    },
    /* Ambiguous — both domains score equally → BFF fans out to both collections. */
    dual: {
      q: "Is there a policy for VPN usage?",
      ambiguous: true,
      answer:
        "This touches <b>both domains</b>, so I searched the two collections in parallel:<br><br>" +
        "<b>💻 IT Helpdesk</b> — the <b>VPN Acceptable Use Guide</b> requires company-managed devices, " +
        "split-tunnel disabled, and re-auth every 12 hours.<br>" +
        "<b>🧑‍💼 Employee Support</b> — the <b>Remote Work Policy</b> adds that remote days must be " +
        "pre-approved in Workday and require an always-on VPN.",
      cites: ["VPN Acceptable Use Guide", "Remote Work Policy", "IT Security Standards"],
      perDomain: {
        IT_helpdesk: { strategy: "hybrid", strategyLabel: "hybrid · $rankFusion", ms: 401, mongoMs: 178 },
        employee_support: { strategy: "fulltext", strategyLabel: "fulltext · $search", ms: 342, mongoMs: 133 },
      },
      timings: { classificationMs: 2, AiSearchMs: 401, mongoMs: 178 },
    },
    /* Neutral dual answer for free-text queries that score low on both domains. */
    dualGeneric: {
      ambiguous: true,
      answer:
        "Your question scored below the <b>0.55 confidence threshold</b>, so the BFF queried " +
        "<b>both collections in parallel</b> and I merged the strongest chunks:<br><br>" +
        "<b>💻 IT Helpdesk</b> — device, access and troubleshooting guides.<br>" +
        "<b>🧑‍💼 Employee Support</b> — HR policies, payroll and benefits.<br><br>" +
        "Rephrase with a domain keyword (e.g. <i>VPN</i>, <i>payroll</i>, <i>leave</i>) for a " +
        "single-collection deep dive.",
      cites: ["IT_helpdesk · top chunks", "employee_support · top chunks"],
      perDomain: {
        IT_helpdesk: { strategy: "hybrid", strategyLabel: "hybrid · $rankFusion", ms: 388, mongoMs: 171 },
        employee_support: { strategy: "hybrid", strategyLabel: "hybrid · $rankFusion", ms: 356, mongoMs: 158 },
      },
      timings: { classificationMs: 2, AiSearchMs: 388, mongoMs: 171 },
    },
    /* Fallback for free-text questions with no canned answer. */
    generic: {
      answer:
        "I routed your question through AiSearch and grounded the answer in the " +
        "<b>{collection}</b> collection. In a live deployment the retrieved chunks are " +
        "assembled here with citation cards — try one of the suggested questions to see " +
        "a full grounded answer.",
      pipeline: "db.{collection}.aggregate([\n  { $vectorSearch: { index: \"{vectorIndex}\", numCandidates: 150, … } }\n])",
      timings: { classificationMs: 2, AiSearchMs: 334, mongoMs: 149 },
    },
  };

  /* ════════════════════════════════════════════════════════════════════════
     4. Classifier — port of classifyQuery() from classifier.ts
     ════════════════════════════════════════════════════════════════════════ */
  function scoreQuery(query, domain) {
    var signals = DOMAIN_SIGNALS[domain];
    var score = 0, matched = [];
    signals.patterns.forEach(function (p) {
      if (p.test(query)) { score += PATTERN_WEIGHT; matched.push(p.source.replace(/\\b/g, "")); }
    });
    signals.keywords.forEach(function (kw) {
      if (new RegExp("\\b" + kw + "\\b", "i").test(query)) { score += KEYWORD_WEIGHT; matched.push(kw); }
    });
    return { score: score, matched: matched };
  }

  function detectBias(query) {
    if (VECTOR_HEAVY.some(function (p) { return p.test(query); })) return "vector-heavy";
    if (FULLTEXT_HEAVY.some(function (p) { return p.test(query); })) return "fulltext-heavy";
    return "auto";
  }

  function classify(query) {
    var scores = {}, matched = [];
    Object.keys(COLLECTIONS).forEach(function (d) {
      var r = scoreQuery(query, d);
      scores[d] = r.score;
      r.matched.forEach(function (m) { matched.push(d + ":" + m); });
    });
    var total = scores.IT_helpdesk + scores.employee_support;
    var domain, confidence;
    if (total === 0) { domain = "employee_support"; confidence = 0.5; }
    else {
      domain = scores.IT_helpdesk > scores.employee_support ? "IT_helpdesk" : "employee_support";
      confidence = scores[domain] / total;
    }
    return {
      domain: domain,
      confidence: confidence,
      scores: scores,
      bias: detectBias(query),
      ambiguous: confidence < AMBIGUITY_THRESHOLD,
      matchedSignals: matched.slice(0, 6),
    };
  }

  /* ════════════════════════════════════════════════════════════════════════
     5. Payload + wire — port of buildPayload() / callAiSearch()
     ════════════════════════════════════════════════════════════════════════ */
  function buildPayload(query, col, bias, topK) {
    var weights;
    if (bias === "vector-heavy") weights = { vector_weight: 0.75, fulltext_weight: 0.25 };
    else if (bias === "fulltext-heavy") weights = { vector_weight: 0.3, fulltext_weight: 0.7 };
    else weights = { vector_weight: col.hybridWeights.vectorWeight, fulltext_weight: col.hybridWeights.fulltextWeight };
    weights.num_candidates = col.numCandidates;
    return {
      query: query,
      top_k: topK || 8,
      atlas: {
        collection: col.collection,
        vector_index: col.vectorIndex,
        search_index: col.searchIndex,
        text_key: col.textKey,
        embedding_key: col.embeddingKey,
      },
      retrieval: weights,
      summarize: false,
      understand: false,
    };
  }

  /* Agent Engine wraps the payload; FastAPI takes it as-is. */
  function wireBody(env, payload) {
    if (env.envelope === "agent-engine") {
      return {
        class_method: "query",
        input: {
          input: payload.query,
          top_k: payload.top_k,
          atlas: payload.atlas,
          retrieval: payload.retrieval,
        },
      };
    }
    return payload;
  }

  /* ════════════════════════════════════════════════════════════════════════
     6. Tiny DOM helpers
     ════════════════════════════════════════════════════════════════════════ */
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  /* Minimal JSON syntax highlighter for the inspector. */
  function jsonHtml(obj) {
    var s = esc(JSON.stringify(obj, null, 2));
    return s
      .replace(/&quot;([^&]+?)&quot;:/g, '<span class="jk">&quot;$1&quot;</span>:')
      .replace(/: &quot;([^&]*?)&quot;/g, ': <span class="js">&quot;$1&quot;</span>')
      .replace(/: (-?\d+\.?\d*)/g, ': <span class="jn">$1</span>')
      .replace(/: (true|false|null)/g, ': <span class="jb">$1</span>');
  }

  /* ════════════════════════════════════════════════════════════════════════
     7. UI state + element refs
     ════════════════════════════════════════════════════════════════════════ */
  var state = { env: "local", busy: false, ran: false };

  var chatBody = document.getElementById("chat-body");
  var chatForm = document.getElementById("chat-form");
  var chatInput = document.getElementById("chat-text");
  var chatHost = document.getElementById("chat-host");
  var flow = document.getElementById("flow");
  var inspectorPre = document.getElementById("inspector-pre");
  var drawer = document.getElementById("drawer");
  var envNote = document.getElementById("envnote");
  if (!chatBody || !flow) return;

  /* ── Connection hops ───────────────────────────────────────────────────── */
  var HOPS = [
    { id: "ui",       icon: "💬", title: "Browser → Agent BFF",   sub: "POST /api/chat" },
    { id: "classify", icon: "🧭", title: "Domain classifier",     sub: "classifier.ts · < 1 ms" },
    { id: "wire",     icon: "🔌", title: "BFF → AiSearch",       sub: "POST /retrieve" },
    { id: "core",     icon: "⚙️", title: "AiSearch pipeline",    sub: "plan → retrieve" },
    { id: "atlas",    icon: "🍃", title: "MongoDB Atlas",         sub: "aggregation pipeline" },
    { id: "resp",     icon: "✨", title: "Grounded response",     sub: "answer + citations" },
  ];

  var hopEls = {};
  function buildFlow() {
    flow.innerHTML = "";
    HOPS.forEach(function (h, i) {
      var row = el("button", "hop");
      row.type = "button";
      row.setAttribute("data-hop", h.id);
      row.appendChild(el("span", "hop-icon", h.icon));
      var mid = el("span", "hop-mid");
      mid.appendChild(el("span", "hop-title", h.title));
      var sub = el("span", "hop-sub", h.sub);
      mid.appendChild(sub);
      row.appendChild(mid);
      var st = el("span", "hop-state", "·");
      row.appendChild(st);
      var stages = null;
      if (h.id === "core") {
        stages = el("span", "hop-stages");
        ["understand", "plan", "retrieve", "summarize"].forEach(function (s) {
          var c = el("span", "stage", s);
          c.setAttribute("data-stage", s);
          stages.appendChild(c);
        });
        row.appendChild(stages);
      }
      if (i < HOPS.length - 1) {
        var conn = el("span", "hop-conn");
        conn.appendChild(el("i", "packet"));
        flow.appendChild(row);
        flow.appendChild(conn);
        hopEls[h.id + "-conn"] = conn;
      } else {
        flow.appendChild(row);
      }
      hopEls[h.id] = { row: row, sub: sub, state: st, stages: stages };
      row.addEventListener("click", function () { openDrawer(h.id); });
    });
  }

  function resetFlow() {
    HOPS.forEach(function (h) {
      var e = hopEls[h.id];
      e.row.classList.remove("active", "done");
      e.state.textContent = "·";
      e.sub.textContent = h.sub;
      if (h.id === "wire") e.sub.textContent = ENVS[state.env].hopSub;
      if (e.stages) {
        e.stages.querySelectorAll(".stage").forEach(function (s) {
          s.classList.remove("active", "done", "skip");
        });
      }
      var conn = hopEls[h.id + "-conn"];
      if (conn) conn.classList.remove("live");
    });
  }

  function hopStart(id, subText) {
    var e = hopEls[id];
    e.row.classList.add("active");
    if (subText) e.sub.textContent = subText;
    var conn = hopEls[id + "-conn"];
    if (conn) conn.classList.add("live");
  }
  function hopDone(id, msText, subText) {
    var e = hopEls[id];
    e.row.classList.remove("active");
    e.row.classList.add("done");
    e.state.textContent = msText || "✓";
    if (subText) e.sub.textContent = subText;
    var conn = hopEls[id + "-conn"];
    if (conn) conn.classList.remove("live");
  }
  function stage(id, name, cls) {
    var e = hopEls[id];
    if (!e || !e.stages) return;
    var c = e.stages.querySelector('[data-stage="' + name + '"]');
    if (c) { c.classList.remove("active", "done", "skip"); c.classList.add(cls); }
  }

  /* ── Inspector ─────────────────────────────────────────────────────────── */
  var inspectorTab = "request";
  var inspectorData = { request: "// send a question to see the exact wire payload", response: "// the AiSearch /retrieve response appears here" };

  function renderInspector() {
    var content = inspectorData[inspectorTab];
    if (typeof content === "string") {
      inspectorPre.className = "plain";
      inspectorPre.textContent = content;
    } else {
      inspectorPre.className = "";
      inspectorPre.innerHTML = content.raw ? esc(content.raw) : jsonHtml(content);
    }
    document.querySelectorAll(".inspector-tab").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-tab") === inspectorTab);
    });
  }
  document.querySelectorAll(".inspector-tab").forEach(function (t) {
    t.addEventListener("click", function () { inspectorTab = t.getAttribute("data-tab"); renderInspector(); });
  });

  /* ── Detail drawer (per-hop explanation + doc links) ───────────────────── */
  function drawerContent(id) {
    var env = ENVS[state.env];
    var linkList = function (links) {
      return links.map(function (l) { return '<a href="' + l[1] + '">' + l[0] + " →</a>"; }).join("");
    };
    switch (id) {
      case "ui":
        return {
          t: "1 · Browser → Agent BFF",
          d: "The Next.js chat UI posts <code>{ query, topK }</code> to the BFF route " +
             "<code>POST /api/chat</code> on the agent host — <b>" + env.agentHost + "</b>. " +
             "The browser never talks to AiSearch or MongoDB Atlas directly; the BFF is the only egress.",
          l: linkList([["Employee Support Copilot", "agents/employee-support-copilot/README.html"]].concat(env.docs.slice(0, 1))),
        };
      case "classify":
        return {
          t: "2 · Domain classifier (in the BFF)",
          d: "<code>classifier.ts</code> scores the query against <code>DOMAIN_SIGNALS</code> " +
             "(patterns ×3, keywords ×1) for <code>IT_helpdesk</code> vs <code>employee_support</code>. " +
             "Confidence below <b>0.55</b> fans out to <b>both collections in parallel</b>. " +
             "A bias signal (<code>vector-heavy</code> / <code>fulltext-heavy</code> / <code>auto</code>) nudges the hybrid weights.",
          l: linkList([["Copilot routing rules", "agents/employee-support-copilot/README.html"]]),
        };
      case "wire":
        return {
          t: "3 · BFF → AiSearch — the wire call",
          d: "<code>AiSearch-client.ts → buildPayload()</code> attaches <b>per-request atlas overrides</b> " +
             "(collection, vector/search indexes, field keys) and retrieval weights, so the backend never needs a " +
             "restart when collections change. <code>callAiSearch()</code> picks the transport from " +
             "<code>AISEARCH_BASE_URL</code>: a plain <code>POST /retrieve</code> to FastAPI, or the Agent Engine " +
             "<code>:query</code> envelope with an ADC-minted OAuth2 token.<br><br>" +
             "<b>" + env.label + ":</b> <code>" + esc(env.method + " " + env.url) + "</code><br>" +
             env.auth.map(function (a) { return "<code>" + esc(a) + "</code>"; }).join("<br>"),
          l: linkList([["AiSearch REST API", "README.html"]].concat(env.docs)),
        };
      case "core":
        return {
          t: "4 · AiSearch factory pipeline",
          d: "The copilot sends <code>understand: false</code> — NLU already happened in the BFF classifier, " +
             "so <b>query understanding is skipped</b>. The <b>Retrieval Planner</b> still applies Atlas-managed " +
             "guardrails from the policy store, and the <b>RetrieverFactory</b> dispatches one of six strategies " +
             "(vector / fulltext / hybrid / graph / parent-doc / metadata). <code>summarize: false</code> — " +
             "the copilot's assembler builds the final answer itself.",
          l: linkList([["Full architecture spec", "docs/Instructions.html"], ["Architecture diagrams", "docs/ARCHITECTURE_DIAGRAMS.html"]]),
        };
      case "atlas":
        return {
          t: "5 · MongoDB Atlas executes the pipeline",
          d: "Atlas runs the real aggregation on the routed collection — <code>$vectorSearch</code>, " +
             "<code>$search</code> or server-side <code>$rankFusion</code> — using that collection's own indexes. " +
             "The exact pipeline is captured server-side and returned in the response (<code>pipeline</code> field), " +
             "which is what the inspector shows above.",
          l: linkList([["Retrieval strategies", "README.html"], ["Quick reference", "docs/QUICK_REFERENCE.html"]]),
        };
      case "resp":
        return {
          t: "6 · Grounded response → assembler → chat",
          d: "The BFF <code>assembler.ts</code> merges the retrieved chunks into an answer with citation cards, " +
             "then the chat UI renders the domain badge, strategy chip, confidence and per-stage timings. " +
             "On failure it degrades gracefully: the non-forced domain is tried as a fallback before erroring.",
          l: linkList([["Copilot answer assembly", "agents/employee-support-copilot/README.html"]]),
        };
    }
    return { t: "", d: "", l: "" };
  }

  function openDrawer(id) {
    var c = drawerContent(id);
    drawer.innerHTML = "";
    var head = el("div", "drawer-head");
    head.appendChild(el("b", null, c.t));
    var x = el("button", "drawer-x", "✕");
    x.type = "button";
    x.addEventListener("click", closeDrawer);
    head.appendChild(x);
    drawer.appendChild(head);
    drawer.appendChild(el("div", "drawer-body", c.d));
    drawer.appendChild(el("div", "drawer-links", c.l));
    drawer.classList.add("open");
  }
  function closeDrawer() { drawer.classList.remove("open"); }

  /* ── Environment pills ─────────────────────────────────────────────────── */
  function setEnv(key) {
    state.env = key;
    var env = ENVS[key];
    document.querySelectorAll(".envpill").forEach(function (p) {
      p.setAttribute("aria-selected", String(p.getAttribute("data-env") === key));
    });
    chatHost.textContent = env.agentHost;
    envNote.innerHTML =
      "Agent on <b>" + env.agentHost + "</b> → AiSearch on <b>" + env.backend + "</b> · " +
      env.docs.map(function (d) { return '<a href="' + d[1] + '">' + d[0] + " →</a>"; }).join(" ");
    hopEls.wire.sub.textContent = env.hopSub;
    if (!state.ran) resetFlow();
    closeDrawer();
  }
  document.querySelectorAll(".envpill").forEach(function (p) {
    p.addEventListener("click", function () { setEnv(p.getAttribute("data-env")); });
  });

  /* ── Chat renderers ────────────────────────────────────────────────────── */
  function scrollChat() { chatBody.scrollTop = chatBody.scrollHeight; }

  function addUser(text) {
    var m = el("div", "msg user");
    var b = el("div", "bubble");
    b.textContent = text; /* user input — escaped */
    m.appendChild(b);
    chatBody.appendChild(m); scrollChat();
  }

  function addTyping() {
    var m = el("div", "msg bot");
    var b = el("div", "bubble");
    b.appendChild(el("span", "typing", "<i></i><i></i><i></i>"));
    m.appendChild(b); chatBody.appendChild(m); scrollChat();
    return m;
  }

  function addAnswer(html, opts) {
    var m = el("div", "msg bot");
    var b = el("div", "bubble");
    var badges = el("div", "msg-badges");
    opts.badges.forEach(function (bg) { badges.appendChild(el("span", "mbadge " + (bg.cls || ""), bg.text)); });
    b.appendChild(badges);
    var text = el("div", "msg-text");
    b.appendChild(text);
    var cites = el("div", "msg-cites");
    cites.appendChild(el("span", "lbl", "Citations<br>"));
    opts.cites.forEach(function (c) { cites.appendChild(el("span", "cite", "📄 " + c)); });
    m.appendChild(b); chatBody.appendChild(m); scrollChat();

    var i = 0;
    (function tick() {
      i += 4;
      text.innerHTML = html.slice(0, i);
      scrollChat();
      if (i < html.length) setTimeout(tick, 12);
      else { text.innerHTML = html; b.appendChild(cites); scrollChat(); }
    })();
  }

  /* ── Response JSON shown in the inspector ──────────────────────────────── */
  function fakeResponse(sc, domainKey) {
    var col = COLLECTIONS[domainKey];
    return {
      strategy: sc.strategy || "hybrid",
      plan: { strategy: sc.strategy || "hybrid", top_k: 8, collection: col.collection, source: "planner+policy_store" },
      results: [
        { content: "…" + (sc.cites ? sc.cites[0] : "chunk") + "…", metadata: { source: (sc.cites || ["doc"])[0] }, score: 0.83 },
        { content: "…" + (sc.cites && sc.cites[1] ? sc.cites[1] : "supporting chunk") + "…", metadata: { source: (sc.cites || ["doc", "doc"])[1] || "doc" }, score: 0.79 },
      ],
      summary: null,
      timings: { mongo_ms: sc.timings.mongoMs, planning_ms: 41, understanding_ms: null, summarize_ms: null, total_ms: sc.timings.AiSearchMs },
      pipeline: "/* captured server-side */\n" + (sc.pipeline || SCENARIOS.generic.pipeline)
        .replace(/\{collection\}/g, col.collection).replace(/\{vectorIndex\}/g, col.vectorIndex),
    };
  }

  /* ════════════════════════════════════════════════════════════════════════
     8. The animated turn — drives chat + connection view together
     ════════════════════════════════════════════════════════════════════════ */
  function pickScenario(query, cls) {
    var q = query.toLowerCase();
    if (/\bpto\b|\bvacation|leave\b|time.?off/.test(q)) return SCENARIOS.pto;
    if (/\bvpn\b/.test(q) && /policy/.test(q)) return SCENARIOS.dual;
    if (/\bvpn\b/.test(q)) return SCENARIOS.vpn;
    if (/mfa|2fa|two.?factor/.test(q)) return SCENARIOS.mfa;
    if (/deposit|payroll|payslip|salary/.test(q)) return SCENARIOS.payroll;
    if (cls.ambiguous) return SCENARIOS.dualGeneric;
    var g = SCENARIOS.generic;
    return {
      q: query,
      domain: cls.domain,
      strategy: "vector",
      strategyLabel: "vector · $vectorSearch",
      answer: g.answer.replace(/\{collection\}/g, cls.domain),
      cites: [COLLECTIONS[cls.domain].label + " · top chunks"],
      pipeline: g.pipeline,
      timings: g.timings,
    };
  }

  function runTurn(query) {
    if (state.busy) return Promise.resolve();
    state.busy = true; state.ran = true;
    document.body.classList.add("busy");
    closeDrawer();
    resetFlow();
    addUser(query);

    var env = ENVS[state.env];
    var typing = addTyping();

    /* 1 · Browser → BFF */
    hopStart("ui", "POST /api/chat · " + env.agentHost.split("·")[0].trim());
    return sleep(420).then(function () {
      hopDone("ui", "✓ 8 ms");

      /* 2 · Classify */
      var cls = classify(query);
      var sc = pickScenario(query, cls);
      if (sc.ambiguous) cls.ambiguous = true; /* canned dual scenario */
      hopStart("classify");
      return sleep(560).then(function () {
        var conf = Math.round(cls.confidence * 100);
        var sub = COLLECTIONS[cls.domain].icon + " " + COLLECTIONS[cls.domain].label +
          " · conf " + conf + "% · bias " + cls.bias +
          (cls.ambiguous ? " · ⚡ fan-out ×2" : "");
        hopDone("classify", "✓ " + sc.timings.classificationMs + " ms", sub);

        /* 3 · Wire call(s) */
        hopStart("wire", env.hopSub);
        var calls = cls.ambiguous ? ["IT_helpdesk", "employee_support"] : [sc.domain || cls.domain];
        var payloads = calls.map(function (d) {
          return buildPayload(query, COLLECTIONS[d], cls.ambiguous ? "auto" : cls.bias, cls.ambiguous ? 5 : 8);
        });
        var wire = payloads.map(function (p, i) {
          return {
            method: env.method, url: env.url,
            headers: env.auth,
            body: wireBody(env, p),
          };
        });
        /* Build a combined pseudo-object for dual calls */
        inspectorData.request = wire.length === 1
          ? { "→ request": { method: wire[0].method, url: wire[0].url, headers: wire[0].headers, body: wire[0].body } }
          : { "→ call 1 · IT_helpdesk": { method: wire[0].method, url: wire[0].url, headers: wire[0].headers, body: wire[0].body },
              "→ call 2 · employee_support (parallel)": { method: wire[1].method, url: wire[1].url, headers: wire[1].headers, body: wire[1].body } };
        inspectorTab = "request"; renderInspector();

        return sleep(780).then(function () {
          hopDone("wire", "✓ " + (cls.ambiguous ? "2 calls ∥" : "1 call"));

          /* 4 · Core pipeline stages */
          hopStart("core");
          stage("core", "understand", "skip");
          hopEls.core.sub.textContent = "understand: skipped (BFF did NLU)";
          return sleep(430).then(function () {
            stage("core", "plan", "active");
            hopEls.core.sub.textContent = "planning — policy store guardrails";
            return sleep(470);
          }).then(function () {
            stage("core", "plan", "done");
            stage("core", "retrieve", "active");
            var strat = cls.ambiguous ? "hybrid ∥ fulltext" : (sc.strategyLabel || "vector · $vectorSearch");
            hopEls.core.sub.textContent = "retrieving — " + strat;
            return sleep(620);
          }).then(function () {
            stage("core", "retrieve", "done");
            stage("core", "summarize", "skip");
            hopDone("core", "✓ planned", "plan ✓ · retrieve ✓ · understand/summarize skipped");

            /* 5 · Atlas */
            hopStart("atlas", calls.map(function (d) { return "db." + d + ".aggregate(…)"; }).join(" ∥ "));
            return sleep(680).then(function () {
              hopDone("atlas", "✓ " + sc.timings.mongoMs + " ms", "pipeline captured server-side");

              /* Response in inspector */
              if (cls.ambiguous && sc.perDomain) {
                inspectorData.response = {
                  "← call 1 · IT_helpdesk": fakeResponse({ strategy: sc.perDomain.IT_helpdesk.strategy, cites: [sc.cites[0]], timings: { mongoMs: sc.perDomain.IT_helpdesk.mongoMs, AiSearchMs: sc.perDomain.IT_helpdesk.ms } }, "IT_helpdesk"),
                  "← call 2 · employee_support": fakeResponse({ strategy: sc.perDomain.employee_support.strategy, cites: [sc.cites[1]], timings: { mongoMs: sc.perDomain.employee_support.mongoMs, AiSearchMs: sc.perDomain.employee_support.ms } }, "employee_support"),
                };
              } else {
                inspectorData.response = { "← 200 OK": fakeResponse(sc, sc.domain || cls.domain) };
              }
              inspectorTab = "response"; renderInspector();

              /* 6 · Response → chat */
              hopStart("resp", "assembler.ts → ChatResponse");
              return sleep(420).then(function () {
                typing.remove();
                hopDone("resp", "✓ " + (sc.timings.AiSearchMs + sc.timings.classificationMs + 14) + " ms total");
                var badges = [];
                if (cls.ambiguous) {
                  var pd = sc.perDomain;
                  badges.push({ text: "⚡ fanned out to both collections", cls: "dual" });
                  badges.push({ text: "💻 " + pd.IT_helpdesk.strategyLabel + " — " + pd.IT_helpdesk.ms + " ms" });
                  badges.push({ text: "🧑‍💼 " + pd.employee_support.strategyLabel + " — " + pd.employee_support.ms + " ms" });
                } else {
                  var d = sc.domain || cls.domain;
                  badges.push({ text: "routed → " + COLLECTIONS[d].icon + " " + COLLECTIONS[d].label });
                  badges.push({ text: sc.strategyLabel || "vector · $vectorSearch", cls: "strategy" });
                  badges.push({ text: "conf " + Math.round(cls.confidence * 100) + "%", cls: "conf" });
                  badges.push({ text: "mongo " + sc.timings.mongoMs + " ms · total " + sc.timings.AiSearchMs + " ms", cls: "time" });
                }
                addAnswer(sc.answer, { badges: badges, cites: sc.cites });
                state.busy = false;
                document.body.classList.remove("busy");
              });
            });
          });
        });
      });
    });
  }

  /* ── Wire up chips, form, reset ────────────────────────────────────────── */
  document.querySelectorAll("[data-q]").forEach(function (btn) {
    btn.addEventListener("click", function () { runTurn(btn.getAttribute("data-q")); });
  });
  if (chatForm) {
    chatForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var q = chatInput.value.trim();
      if (!q || state.busy) return;
      chatInput.value = "";
      runTurn(q);
    });
  }
  var resetBtn = document.getElementById("chat-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      state.ran = false;
      chatBody.innerHTML = "";
      inspectorData.request = "// send a question to see the exact wire payload";
      inspectorData.response = "// the AiSearch /retrieve response appears here";
      renderInspector();
      resetFlow();
      closeDrawer();
    });
  }

  /* ── Boot ──────────────────────────────────────────────────────────────── */
  buildFlow();
  setEnv("local");
  renderInspector();
  setTimeout(function () { runTurn(SCENARIOS.pto.q); }, 700);
})();
