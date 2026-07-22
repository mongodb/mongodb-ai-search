/**
 * collections.ts — Single source of truth for collection routing config.
 *
 * To add a third (or fourth) collection:
 *   1. Add a new entry to COLLECTION_CONFIGS.
 *   2. Add keyword patterns to DOMAIN_SIGNALS below.
 *   3. Everything else — the client, router, UI — picks it up automatically.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DomainKey = "IT_helpdesk" | "employee_support";

export interface CollectionConfig {
  /** Logical domain label shown in the UI. */
  label: string;
  /** Short description shown alongside answers. */
  description: string;
  /** MongoDB Atlas collection name passed to SearchaaS. */
  collection: string;
  /** Atlas Vector Search index name for this collection. */
  vectorIndex: string;
  /** Atlas Search (Lucene) index name for this collection. */
  searchIndex: string;
  /** Field name that stores the document text. */
  textKey: string;
  /** Field name that stores the embedding vector (null = server-side auto). */
  embeddingKey: string | null;
  /** Vector dimensions (-1 = auto / server-side). */
  dimensions: number;
  /** Default hybrid weights for this domain. */
  hybridWeights: { vectorWeight: number; fulltextWeight: number };
  /** Number of ANN candidates to oversample before re-ranking. */
  numCandidates: number;
  /** Badge colour class (Tailwind) shown in the chat UI. */
  badgeColor: string;
  /** Emoji icon shown alongside the domain badge. */
  icon: string;
}

// ---------------------------------------------------------------------------
// Registry — add new collections here only
// ---------------------------------------------------------------------------

export const COLLECTION_CONFIGS: Record<DomainKey, CollectionConfig> = {
  IT_helpdesk: {
    label: "IT Helpdesk",
    description: "Covers VPN, devices, SSO, MFA, software, and access issues.",
    collection: "IT_helpdesk",
    vectorIndex: "it_helpdesk_vector_index",
    searchIndex: "it_helpdesk_search_index",
    textKey: "text",
    embeddingKey: "embedding",
    dimensions: -1,
    hybridWeights: { vectorWeight: 0.55, fulltextWeight: 0.45 },
    numCandidates: 150,
    badgeColor: "bg-blue-100 text-blue-800",
    icon: "💻",
  },

  employee_support: {
    label: "Employee Support",
    description: "Covers leave, payroll, travel, benefits, and HR policies.",
    collection: "employee_support",
    vectorIndex: "employee_support_vector_index",
    searchIndex: "employee_support_search_index",
    textKey: "text",
    embeddingKey: "embedding",
    dimensions: -1,
    hybridWeights: { vectorWeight: 0.5, fulltextWeight: 0.5 },
    numCandidates: 150,
    badgeColor: "bg-green-100 text-green-800",
    icon: "🧑‍💼",
  },
};

// ---------------------------------------------------------------------------
// Domain signal patterns — used by the keyword classifier
// ---------------------------------------------------------------------------

export interface DomainSignals {
  /** Regex patterns that strongly indicate this domain. */
  patterns: RegExp[];
  /** Additional single-word keywords (lower-cased). */
  keywords: string[];
}

export const DOMAIN_SIGNALS: Record<DomainKey, DomainSignals> = {
  IT_helpdesk: {
    patterns: [
      /\bvpn\b/i,
      /\bsso\b/i,
      /\bmfa\b/i,
      /\b(two.?factor|2fa)\b/i,
      /\boutlook\b/i,
      /\b(wi.?fi|wireless)\b/i,
      /\b(laptop|macbook|desktop|device)\b/i,
      /\bpassword\s+reset\b/i,
      /\b(install|software|application|app)\b/i,
      /\b(access|permission|login|sign.?in|authenticate)\b/i,
      /\b(printer|printing|print)\b/i,
      /\b(onboarding.?tech|tech.?setup|it.?setup)\b/i,
      /\b(azure\s+ad|active\s+directory|okta|slack)\b/i,
      /\b(ticket|helpdesk|it\s+support)\b/i,
      /\b(remote\s+(access|desktop)|rdp)\b/i,
      /\b(network|firewall|proxy|dns)\b/i,
    ],
    keywords: [
      "vpn", "laptop", "desktop", "mfa", "sso", "login", "password", "wifi",
      "printer", "install", "software", "access", "device", "bluetooth",
      "monitor", "keyboard", "outlook", "teams", "zoom", "chrome", "browser",
      "certificate", "antivirus", "encryption", "bitlocker",
    ],
  },

  employee_support: {
    patterns: [
      /\b(leave|vacation|pto|time.?off|sick.?leave|maternity|paternity)\b/i,
      /\b(payroll|salary|payslip|pay\s+stub|compensation)\b/i,
      /\b(reimbursement|expense|travel\s+(claim|policy|allowance))\b/i,
      /\b(health\s+insurance|benefits|401k|pension|provident\s+fund)\b/i,
      /\b(hr|human\s+resources|people\s+ops|people\s+team)\b/i,
      /\b(holiday\s+(calendar|list)|public\s+holiday)\b/i,
      /\b(promotion|performance\s+review|appraisal|increment)\b/i,
      /\b(onboarding\s+process|new\s+hire|joining\s+form)\b/i,
      /\b(work.?from.?home|remote\s+work\s+policy|wfh\s+policy)\b/i,
      /\b(relocation|transfer|deputation)\b/i,
      /\b(code\s+of\s+conduct|ethics|policy)\b/i,
      /\b(referral|employee\s+referral)\b/i,
    ],
    keywords: [
      "leave", "vacation", "payroll", "salary", "hr", "benefits", "insurance",
      "reimbursement", "travel", "expense", "holiday", "policy", "promotion",
      "appraisal", "increment", "bonus", "onboarding", "joining", "offer",
      "resignation", "notice", "separation", "pf", "esic", "gratuity",
    ],
  },
};

// ---------------------------------------------------------------------------
// Retrieval strategy hints
// ---------------------------------------------------------------------------

/** Terms that suggest a fuzzy/semantic (vector-heavy) query. */
export const VECTOR_HEAVY_PATTERNS: RegExp[] = [
  /acting weird/i,
  /not working properly/i,
  /keeps crashing/i,
  /slow(ly)?/i,
  /international(ly)?/i,
  /remotely/i,
  /best way to/i,
  /how (do|can|should) i/i,
  /explain/i,
  /what (is|are|does)/i,
];

/** Terms that suggest an exact-lookup (fulltext-heavy) query. */
export const FULLTEXT_HEAVY_PATTERNS: RegExp[] = [
  /policy$/i,
  /guide$/i,
  /\bcap\b/i,
  /\blimit\b/i,
  /\bform\b/i,
  /\bsteps\b/i,
  /\bprocess\b/i,
  /\bprocedure\b/i,
  /\beligib/i,
  /\bcriteria\b/i,
  /reset guide/i,
  /reimbursement cap/i,
];

export const ALL_DOMAINS = Object.keys(COLLECTION_CONFIGS) as DomainKey[];
