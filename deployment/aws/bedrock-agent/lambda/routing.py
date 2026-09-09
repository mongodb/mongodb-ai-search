"""
routing.py — Python port of the copilot BFF's collection router.

Mirrors agents/employee-support-copilot/src/lib/{collections,classifier}.ts and
the `buildPayload` half of searchaas-client.ts, so the Bedrock agent routes
across collections the same way the Amplify UI does.

Rule-based on purpose: no LLM call, sub-millisecond, and deterministic. Keep in
step with the TypeScript original — the signal lists are the contract.
"""
from __future__ import annotations

import re
from typing import Any

# ── Collection registry (mirrors COLLECTION_CONFIGS) ─────────────────────────

COLLECTIONS: dict[str, dict[str, Any]] = {
    "IT_helpdesk": {
        "label": "IT Helpdesk",
        "collection": "IT_helpdesk",
        "vector_index": "it_helpdesk_vector_index",
        "search_index": "it_helpdesk_search_index",
        "text_key": "text",
        "embedding_key": "embedding",
        "dimensions": -1,                       # -1 => omitted from the payload
        "hybrid_weights": (0.55, 0.45),         # (vector, fulltext)
        "num_candidates": 150,
    },
    "employee_support": {
        "label": "Employee Support",
        "collection": "employee_support",
        "vector_index": "employee_support_vector_index",
        "search_index": "employee_support_search_index",
        "text_key": "text",
        "embedding_key": "embedding",
        "dimensions": -1,
        "hybrid_weights": (0.5, 0.5),
        "num_candidates": 150,
    },
}
ALL_DOMAINS = list(COLLECTIONS)

# ── Domain signals (mirrors DOMAIN_SIGNALS) ──────────────────────────────────

DOMAIN_SIGNALS: dict[str, dict[str, list]] = {
    "IT_helpdesk": {
        "patterns": [
            r"\bvpn\b", r"\bsso\b", r"\bmfa\b", r"\b(two.?factor|2fa)\b",
            r"\boutlook\b", r"\b(wi.?fi|wireless)\b",
            r"\b(laptop|macbook|desktop|device)\b", r"\bpassword\s+reset\b",
            r"\b(install|software|application|app)\b",
            r"\b(access|permission|login|sign.?in|authenticate)\b",
            r"\b(printer|printing|print)\b",
            r"\b(onboarding.?tech|tech.?setup|it.?setup)\b",
            r"\b(azure\s+ad|active\s+directory|okta|slack)\b",
            r"\b(ticket|helpdesk|it\s+support)\b",
            r"\b(remote\s+(access|desktop)|rdp)\b",
            r"\b(network|firewall|proxy|dns)\b",
        ],
        "keywords": [
            "vpn", "laptop", "desktop", "mfa", "sso", "login", "password", "wifi",
            "printer", "install", "software", "access", "device", "bluetooth",
            "monitor", "keyboard", "outlook", "teams", "zoom", "chrome", "browser",
            "certificate", "antivirus", "encryption", "bitlocker",
        ],
    },
    "employee_support": {
        "patterns": [
            r"\b(leave|vacation|pto|time.?off|sick.?leave|maternity|paternity)\b",
            r"\b(payroll|salary|payslip|pay\s+stub|compensation)\b",
            r"\b(reimbursement|expense|travel\s+(claim|policy|allowance))\b",
            r"\b(health\s+insurance|benefits|401k|pension|provident\s+fund)\b",
            r"\b(hr|human\s+resources|people\s+ops|people\s+team)\b",
            r"\b(holiday\s+(calendar|list)|public\s+holiday)\b",
            r"\b(promotion|performance\s+review|appraisal|increment)\b",
            r"\b(onboarding\s+process|new\s+hire|joining\s+form)\b",
            r"\b(work.?from.?home|remote\s+work\s+policy|wfh\s+policy)\b",
            r"\b(relocation|transfer|deputation)\b",
            r"\b(code\s+of\s+conduct|ethics|policy)\b",
            r"\b(referral|employee\s+referral)\b",
        ],
        "keywords": [
            "leave", "vacation", "payroll", "salary", "hr", "benefits", "insurance",
            "reimbursement", "travel", "expense", "holiday", "policy", "promotion",
            "appraisal", "increment", "bonus", "onboarding", "joining", "offer",
            "resignation", "notice", "separation", "pf", "esic", "gratuity",
        ],
    },
}

VECTOR_HEAVY_PATTERNS = [
    r"acting weird", r"not working properly", r"keeps crashing", r"slow(ly)?",
    r"international(ly)?", r"remotely", r"best way to", r"how (do|can|should) i",
    r"explain", r"what (is|are|does)",
]
FULLTEXT_HEAVY_PATTERNS = [
    r"policy$", r"guide$", r"\bcap\b", r"\blimit\b", r"\bform\b", r"\bsteps\b",
    r"\bprocess\b", r"\bprocedure\b", r"\beligib", r"\bcriteria\b",
    r"reset guide", r"reimbursement cap",
]

AMBIGUITY_THRESHOLD = 0.55
PATTERN_WEIGHT = 3
KEYWORD_WEIGHT = 1

# ── Scoring (mirrors scoreQuery / detectBias / classifyQuery) ────────────────


def _score(query_lower: str, domain: str) -> tuple[int, list[str]]:
    signals = DOMAIN_SIGNALS[domain]
    score, matched = 0, []
    for pattern in signals["patterns"]:
        if re.search(pattern, query_lower, re.I):
            score += PATTERN_WEIGHT
            matched.append(pattern)
    for kw in signals["keywords"]:
        if re.search(rf"\b{re.escape(kw)}\b", query_lower):
            score += KEYWORD_WEIGHT
            matched.append(kw)
    return score, matched


def _bias(query: str) -> str:
    # Vector list is checked first, so it wins when both would match.
    if any(re.search(p, query, re.I) for p in VECTOR_HEAVY_PATTERNS):
        return "vector-heavy"
    if any(re.search(p, query, re.I) for p in FULLTEXT_HEAVY_PATTERNS):
        return "fulltext-heavy"
    return "auto"


def classify(query: str) -> dict[str, Any]:
    """Domain + confidence + bias for a query. Pure function, no I/O."""
    q = query.lower()
    scores, matched = {}, []
    for domain in ALL_DOMAINS:
        s, m = _score(q, domain)
        scores[domain] = s
        matched += [f"{domain}:{x}" for x in m]

    total = sum(scores.values())
    if total == 0:
        # No signal at all -> most general domain, low confidence to force fan-out.
        domain, confidence = "employee_support", 0.5
    else:
        domain = max(scores, key=lambda d: scores[d])
        confidence = scores[domain] / total

    return {
        "domain": domain,
        "confidence": round(confidence, 4),
        "scores": scores,
        "bias": _bias(query),
        "ambiguous": confidence < AMBIGUITY_THRESHOLD,
        "matched_signals": matched,
    }


def ranked_domains(scores: dict[str, int]) -> list[str]:
    return sorted(ALL_DOMAINS, key=lambda d: scores.get(d, 0), reverse=True)


# ── Payload construction (mirrors buildPayload) ─────────────────────────────


def overrides_for(domain: str, bias: str) -> dict[str, Any]:
    """The `atlas` + `retrieval` arguments for one collection."""
    cfg = COLLECTIONS[domain]
    atlas = {
        "collection": cfg["collection"],
        "vector_index": cfg["vector_index"],
        "search_index": cfg["search_index"],
        "text_key": cfg["text_key"],
        "embedding_key": cfg["embedding_key"],
    }
    if cfg["dimensions"] != -1:
        atlas["dimensions"] = cfg["dimensions"]

    if bias == "vector-heavy":
        weights = (0.75, 0.25)
    elif bias == "fulltext-heavy":
        weights = (0.3, 0.7)
    else:
        weights = cfg["hybrid_weights"]

    return {
        "atlas": atlas,
        "retrieval": {
            "vector_weight": weights[0],
            "fulltext_weight": weights[1],
            "num_candidates": cfg["num_candidates"],
        },
    }
