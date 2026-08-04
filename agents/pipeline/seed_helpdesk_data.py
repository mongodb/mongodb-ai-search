"""
seed_helpdesk_data.py
─────────────────────
Populates the `IT_helpdesk` and `employee_support` MongoDB collections in the
`ai_search` database with realistic knowledge-base articles, then creates the
Atlas Vector Search indexes (voyage-4 AutoEmbeddings) and Atlas Search (Lucene)
indexes required by the employee-support-copilot agent.

Run:
    cd /Users/venkatesh.shanbhag/Documents/AI-Search
    python -m searchaas.pipeline.seed_helpdesk_data

Env vars read (falls back to .env):
    ATLAS_URI  — mongodb+srv://...
    ATLAS_DB   — default: ai_search
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ── load .env from the project root ──────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
_env_file = _ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip("'\"")
        os.environ.setdefault(k.strip(), v)

import pymongo  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────
ATLAS_URI = os.environ["ATLAS_URI"]
ATLAS_DB  = os.environ.get("ATLAS_DB", "ai_search")

client = pymongo.MongoClient(ATLAS_URI)
db     = client[ATLAS_DB]

# ─────────────────────────────────────────────────────────────────────────────
# IT Helpdesk documents
# ─────────────────────────────────────────────────────────────────────────────
IT_HELPDESK_DOCS = [
    {
        "doc_id": "it-001",
        "title": "VPN Setup and Troubleshooting Guide",
        "category": "network",
        "text": (
            "To connect to the company VPN, download and install GlobalProtect from the IT portal. "
            "Enter the gateway address: vpn.company.com. Authenticate with your SSO credentials and "
            "complete the MFA prompt on your registered device. If the connection drops repeatedly, "
            "flush your DNS cache (ipconfig /flushdns on Windows, sudo dscacheutil -flushcache on Mac) "
            "and reconnect. For persistent issues, raise a ticket at helpdesk.company.com."
        ),
        "tags": ["vpn", "network", "globalprotect", "mfa", "sso"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-002",
        "title": "Password Reset Procedure",
        "category": "account_access",
        "text": (
            "You can reset your Active Directory / Azure AD password at https://aka.ms/sspr. "
            "Click 'Forgot my password', verify your identity via email or authenticator app, "
            "and choose a new password that meets policy: 12+ characters, at least one uppercase, "
            "one number, and one special character. Your new password takes effect across all "
            "Microsoft 365 services within 15 minutes. If you are locked out entirely, call the "
            "IT helpdesk at ext. 1000 for manual unlock."
        ),
        "tags": ["password", "reset", "azure ad", "account"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-003",
        "title": "Setting Up Multi-Factor Authentication (MFA)",
        "category": "security",
        "text": (
            "MFA is mandatory for all employees. Enroll at https://mysignins.microsoft.com/security-info. "
            "Recommended method: Microsoft Authenticator app (iOS/Android). After installing, tap "
            "'Add account → Work or school account' and scan the QR code shown on the enrollment page. "
            "Backup methods: SMS to your registered number, or hardware FIDO2 key (request from IT). "
            "If you lose access to your MFA device, contact IT immediately — do not share codes."
        ),
        "tags": ["mfa", "2fa", "authenticator", "security"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-004",
        "title": "Software Installation Request Process",
        "category": "software",
        "text": (
            "All software must be approved before installation on company devices. Submit a request "
            "via ServiceNow: navigate to IT Catalog → Software Request, search for the application, "
            "and provide the business justification. Standard approvals take 1-2 business days; "
            "licensed software requires manager sign-off. Pre-approved apps (Zoom, Slack, VS Code, "
            "Chrome, Firefox) can be self-installed from the Company App Portal without a ticket."
        ),
        "tags": ["software", "install", "servicenow", "app portal"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-005",
        "title": "Laptop Provisioning and New Hire Tech Setup",
        "category": "hardware",
        "text": (
            "New hires receive a provisioned MacBook Pro or Windows laptop shipped to their home address "
            "or available for pickup at the office. Unbox and power on — the device auto-enrolls in "
            "Jamf (Mac) or Intune (Windows) and applies company policies. Log in with your company "
            "email and temporary password from your onboarding email, then set up MFA immediately. "
            "If your laptop has not arrived by day one, contact it-onboarding@company.com."
        ),
        "tags": ["laptop", "onboarding", "hardware", "macbook", "windows"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-006",
        "title": "Slack and Microsoft Teams Setup",
        "category": "collaboration",
        "text": (
            "Slack: accept the invite email → download the desktop app → sign in with SSO. "
            "Join team channels via the Channels sidebar; DM HR or IT for private matters. "
            "Microsoft Teams: already installed on provisioned devices — sign in with your "
            "company Microsoft 365 account. Use Teams for video calls, and Slack for async "
            "chat. Both tools support @mentions. Status indicators (green/yellow/red) sync "
            "automatically with your Outlook calendar."
        ),
        "tags": ["slack", "teams", "collaboration", "chat"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-007",
        "title": "Printer Setup – Office Locations",
        "category": "hardware",
        "text": (
            "To connect to office printers, open System Preferences → Printers & Scanners (Mac) "
            "or Settings → Devices → Printers (Windows). Click '+' and search for printers on the "
            "network — all office printers are prefixed with 'CORP-'. For secure printing, send "
            "your job then swipe your badge at the printer to release it. Colour printing requires "
            "a cost-centre code; black-and-white is unrestricted. For driver issues, raise a ticket."
        ),
        "tags": ["printer", "print", "hardware", "office"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-008",
        "title": "Remote Desktop Access (RDP) to Office Machines",
        "category": "remote_access",
        "text": (
            "To remotely access your office desktop: (1) Connect to VPN first. (2) Open Remote "
            "Desktop Connection (Windows) or Microsoft Remote Desktop (Mac). (3) Enter the hostname "
            "of your office PC (find it in IT Self-Service → My Devices). (4) Authenticate with "
            "your domain credentials. Sessions auto-lock after 15 minutes of inactivity. If you "
            "cannot locate your machine hostname, email itsupport@company.com with your employee ID."
        ),
        "tags": ["rdp", "remote desktop", "remote access", "vpn"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-009",
        "title": "Outlook and Email Configuration",
        "category": "email",
        "text": (
            "Outlook is pre-configured on provisioned devices. On personal devices, add your "
            "account: File → Add Account → enter your company email → Autodiscover handles the rest. "
            "Mobile: download Outlook for iOS/Android, sign in, approve the MFA prompt, and accept "
            "the MDM policy. Email quota is 100 GB; archive older items to Online Archive to free space. "
            "For shared mailboxes or distribution groups, submit a request through the IT portal."
        ),
        "tags": ["outlook", "email", "microsoft 365", "mobile"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-010",
        "title": "Wi-Fi Access – Corporate and Guest Networks",
        "category": "network",
        "text": (
            "Connect to the corporate Wi-Fi 'CORP-SECURE' using your SSO credentials (no separate "
            "Wi-Fi password). This network grants full access to internal resources and the internet. "
            "The 'CORP-GUEST' network is for visitors and personal devices — it provides internet "
            "only, no access to internal systems. If you cannot authenticate on CORP-SECURE, ensure "
            "your device is enrolled in Intune/Jamf; unenrolled devices are blocked by NAC policy."
        ),
        "tags": ["wifi", "wireless", "network", "corporate"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-011",
        "title": "BitLocker and Disk Encryption Policy",
        "category": "security",
        "text": (
            "All Windows laptops must have BitLocker enabled — it is enforced automatically by Intune "
            "on enrollment. Recovery keys are escrowed to Azure AD; retrieve yours at "
            "https://myaccount.microsoft.com → Devices → your device → BitLocker Key. Mac devices use "
            "FileVault, managed by Jamf. Never disable encryption or store the recovery key in "
            "plaintext. Reporting a lost/stolen device immediately triggers a remote wipe."
        ),
        "tags": ["bitlocker", "filevault", "encryption", "security", "laptop"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-012",
        "title": "Raising a Helpdesk Ticket",
        "category": "support",
        "text": (
            "Log tickets at https://helpdesk.company.com (SSO login). Choose the category that best "
            "matches your issue: Hardware, Software, Access, Network, or General IT. For urgent issues "
            "(total outage, security incident), call ext. 1000 or use the 'Priority' flag. "
            "SLA targets: P1 Critical – 2 hrs, P2 High – 4 hrs, P3 Medium – 1 business day, "
            "P4 Low – 3 business days. You'll receive updates by email as the ticket progresses."
        ),
        "tags": ["ticket", "helpdesk", "sla", "support"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-013",
        "title": "Single Sign-On (SSO) and Okta / Azure AD",
        "category": "account_access",
        "text": (
            "Company SSO is powered by Azure AD, federated with Okta for third-party SaaS apps. "
            "Your company email is your SSO identity — use it to log in to Salesforce, Workday, "
            "GitHub Enterprise, and all other approved tools. If an app shows 'You are not authorized', "
            "request access via IT Catalog → Application Access. SSO sessions expire after 8 hours "
            "of inactivity; MFA re-prompt occurs every 7 days on trusted devices."
        ),
        "tags": ["sso", "okta", "azure ad", "access", "login"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-014",
        "title": "Antivirus and Endpoint Protection",
        "category": "security",
        "text": (
            "All company endpoints run CrowdStrike Falcon, managed centrally by the Security team. "
            "You do not need to manually update or scan — the agent runs continuously in the background. "
            "If CrowdStrike quarantines a file you believe is a false positive, submit a ticket with "
            "the file path and the SHA256 hash. Never disable or uninstall Falcon; doing so triggers "
            "an automatic security alert and may result in device quarantine from the corporate network."
        ),
        "tags": ["antivirus", "crowdstrike", "endpoint", "security"],
        "source": "IT Knowledge Base",
    },
    {
        "doc_id": "it-015",
        "title": "Browser and Certificate Issues",
        "category": "software",
        "text": (
            "Company web apps require the corporate root CA certificate to be trusted by your browser. "
            "On provisioned devices this is pushed automatically via Jamf/Intune. On personal machines, "
            "download the cert from https://pki.company.com/rootca.crt and install it in your OS trust "
            "store (Keychain on Mac, Certificate Manager on Windows, or import into your browser's "
            "certificate settings). If you see 'Your connection is not private' on internal sites, "
            "this certificate is almost always the fix."
        ),
        "tags": ["certificate", "ssl", "browser", "pki", "tls"],
        "source": "IT Knowledge Base",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Employee Support documents
# ─────────────────────────────────────────────────────────────────────────────
EMPLOYEE_SUPPORT_DOCS = [
    {
        "doc_id": "hr-001",
        "title": "Annual Leave and PTO Policy",
        "category": "leave",
        "text": (
            "Full-time employees accrue 20 days of paid time off (PTO) per year, prorated in the "
            "first year of employment. PTO accrues at 1.67 days per month. A maximum of 5 days may "
            "be carried over to the following year; unused balance above 5 days is forfeited on "
            "December 31. To apply for leave, log in to Workday → Time Off → Request. Your manager "
            "must approve requests at least 3 business days in advance for leaves of 3+ days."
        ),
        "tags": ["leave", "pto", "vacation", "time off", "workday"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-002",
        "title": "Sick Leave Policy",
        "category": "leave",
        "text": (
            "Employees are entitled to 10 days of paid sick leave per calendar year. Sick leave does "
            "not carry over and is not encashable. For absences of more than 3 consecutive days, a "
            "medical certificate from a registered practitioner is required and must be submitted to "
            "HR within 5 business days of return. Sick leave is recorded separately from PTO in "
            "Workday — select 'Sick' from the leave type dropdown when submitting."
        ),
        "tags": ["sick leave", "medical", "leave", "policy"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-003",
        "title": "Maternity and Paternity Leave",
        "category": "leave",
        "text": (
            "Primary caregivers (maternity) are entitled to 26 weeks of paid leave. Secondary "
            "caregivers (paternity) receive 4 weeks of paid leave. Both entitlements apply from the "
            "date of birth or adoption and must be applied for in Workday at least 4 weeks in advance "
            "where possible. Additional unpaid leave of up to 12 weeks may be requested subject to "
            "manager approval. Health insurance coverage continues uninterrupted during all parental leave."
        ),
        "tags": ["maternity", "paternity", "parental leave", "leave"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-004",
        "title": "Payroll and Salary Payment Schedule",
        "category": "payroll",
        "text": (
            "Salaries are paid on the last business day of each month via direct bank transfer. "
            "Pay slips are available in Workday → Pay → Pay Slips by the 25th of each month. "
            "For discrepancies, contact payroll@company.com by the 20th to allow processing before "
            "month end. Allowances (transport, meal, etc.) are included in the monthly payroll run. "
            "Tax deductions are per the applicable income-tax slab; your Form 16 / P60 is issued annually."
        ),
        "tags": ["payroll", "salary", "pay slip", "bank transfer"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-005",
        "title": "Travel and Expense Reimbursement Policy",
        "category": "expenses",
        "text": (
            "Business travel must be booked through the approved travel portal (Concur). "
            "Reimbursable expenses include flights (economy for <5 hrs, business for >8 hrs), hotels "
            "up to $200/night (pre-approved), meals up to $50/day, and ground transport. "
            "Submit claims within 30 days of incurring the expense; claims older than 60 days require "
            "VP approval. Attach original receipts for all items above $25. Alcohol is not reimbursable."
        ),
        "tags": ["travel", "expense", "reimbursement", "concur"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-006",
        "title": "Health Insurance and Benefits Enrolment",
        "category": "benefits",
        "text": (
            "The company provides medical, dental, and vision insurance through Aetna. New employees "
            "have 30 days from their start date to enrol — log in to Workday → Benefits → Enrolment. "
            "Coverage begins on the first day of the month following enrolment. Dependants (spouse, "
            "children under 26) may be added at no extra premium. Annual open enrolment occurs each "
            "November for the following plan year. For claims or pre-authorizations, call the Aetna "
            "helpline at 1-800-xxx-xxxx or log in to aetna.com."
        ),
        "tags": ["health insurance", "benefits", "aetna", "medical", "enrolment"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-007",
        "title": "401(k) Retirement Plan",
        "category": "benefits",
        "text": (
            "The company offers a 401(k) plan administered by Fidelity. Employees may contribute up "
            "to the IRS annual limit ($23,000 in 2024; $30,500 if age 50+). The company matches 50% "
            "of contributions up to 6% of base salary — matching vests over 3 years (33%/67%/100%). "
            "Enrol or change contributions anytime via Fidelity NetBenefits at netbenefits.fidelity.com. "
            "Investment options include target-date funds, index funds, and company stock (capped at 10%)."
        ),
        "tags": ["401k", "retirement", "benefits", "fidelity", "pension"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-008",
        "title": "Performance Review and Appraisal Process",
        "category": "performance",
        "text": (
            "Performance reviews are conducted twice a year: mid-year check-in (June) and annual "
            "review (December). In Workday, complete your self-evaluation by the stated deadline, then "
            "your manager provides a rating (Exceeds / Meets / Below expectations). Ratings directly "
            "inform compensation adjustments and bonus amounts announced in February. For role-specific "
            "competency frameworks, visit the HR intranet → Performance & Growth. Request a career "
            "conversation with your manager at any time using the structured template."
        ),
        "tags": ["performance review", "appraisal", "rating", "bonus"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-009",
        "title": "Work-From-Home and Remote Work Policy",
        "category": "remote_work",
        "text": (
            "Employees may work from home up to 3 days per week, with Tuesday and Thursday designated "
            "as anchor (in-office) days. WFH eligibility starts after the first 60 days of employment. "
            "Fully remote roles require VP-level approval and are logged in Workday. When working "
            "remotely, you must be reachable on Slack during core hours (10 AM – 4 PM local time). "
            "Ergonomics equipment (chair, monitor) may be expensed up to $500 once every 3 years — "
            "submit via the standard expense form in Concur."
        ),
        "tags": ["wfh", "remote work", "work from home", "hybrid"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-010",
        "title": "Public Holiday Calendar",
        "category": "leave",
        "text": (
            "The company observes all national public holidays for the country of employment. "
            "In the United States: New Year's Day, MLK Day, Memorial Day, Independence Day, "
            "Labor Day, Thanksgiving (+ day after), and Christmas Day — totalling 10 days. "
            "Additionally, employees receive 2 floating holidays per year, usable for personal, "
            "cultural, or religious observances. Floating holidays must be taken within the "
            "calendar year and are applied in Workday under 'Floating Holiday'."
        ),
        "tags": ["public holiday", "holiday", "calendar", "floating holiday"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-011",
        "title": "Employee Referral Program",
        "category": "recruitment",
        "text": (
            "Employees may refer external candidates for open roles. If your referral is hired and "
            "stays for 90+ days, you receive a referral bonus: $2,000 for individual contributor roles, "
            "$3,500 for senior / lead roles, $5,000 for director-level and above. Submit referrals "
            "through Workday → Recruiting → Submit Referral, attaching the candidate's CV. "
            "Bonuses are paid in the next payroll after the 90-day qualifying period."
        ),
        "tags": ["referral", "bonus", "recruitment", "employee referral"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-012",
        "title": "Promotion and Salary Increment Process",
        "category": "performance",
        "text": (
            "Promotions are considered annually during the December performance cycle. Managers "
            "nominate eligible employees via Workday; nominations require a written justification "
            "and are reviewed by the calibration committee. Salary increments of 5-15% are awarded "
            "for promotions; market adjustments for lateral role changes are evaluated case by case. "
            "Off-cycle promotions require CHRO approval and are rare. Employees are notified of "
            "promotion decisions by the end of January with the new grade and salary effective February 1."
        ),
        "tags": ["promotion", "salary", "increment", "appraisal"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-013",
        "title": "Onboarding Process for New Employees",
        "category": "onboarding",
        "text": (
            "Day 1: Attend new-hire orientation (virtual or in-person), collect your laptop, and complete "
            "all Workday onboarding tasks (tax forms, bank details, I-9 verification). Week 1: Shadow "
            "your buddy, complete mandatory compliance training in LMS, and set up all required tools. "
            "Day 30: Complete 30-day check-in with your manager. Day 60–90: Probationary review. "
            "All new employees join the #new-hires Slack channel for peer support and announcements."
        ),
        "tags": ["onboarding", "new hire", "orientation", "probation"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-014",
        "title": "Resignation and Separation Process",
        "category": "offboarding",
        "text": (
            "To resign, submit a formal resignation letter to your manager and HR (people@company.com). "
            "The standard notice period is 4 weeks for individual contributors, 8 weeks for managers "
            "and above. HR will schedule an exit interview and send an offboarding checklist covering: "
            "laptop return, access revocation, final payslip, PF/pension transfer, and reference letter "
            "request. Final pay including accrued PTO and any outstanding expenses is processed in the "
            "next regular payroll cycle after your last day."
        ),
        "tags": ["resignation", "notice period", "offboarding", "exit"],
        "source": "HR Policy Handbook",
    },
    {
        "doc_id": "hr-015",
        "title": "Code of Conduct and Ethics Policy",
        "category": "compliance",
        "text": (
            "All employees must adhere to the company's Code of Conduct: act with integrity, respect "
            "colleagues, protect confidential information, avoid conflicts of interest, and comply with "
            "all applicable laws. Gifts over $50 in value must be disclosed to your manager. "
            "Violations are reported via the anonymous ethics hotline at 1-800-xxx-xxxx or "
            "https://ethics.company.com. Retaliation against reporters is strictly prohibited. "
            "All employees must complete the annual compliance training in the LMS by March 31."
        ),
        "tags": ["code of conduct", "ethics", "compliance", "policy"],
        "source": "HR Policy Handbook",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Atlas Search index definitions
# ─────────────────────────────────────────────────────────────────────────────

# Vector Search index — voyage-4 AutoEmbeddings on `text` field
def _vector_index_def(text_key: str = "text") -> dict:
    return {
        "fields": [
            {
                "type": "autoEmbed",
                "path": text_key,
                "model": "voyage-4",
                "modality": "text",
            },
            {
                "type": "filter",
                "path": "category",
            },
            {
                "type": "filter",
                "path": "doc_id",
            },
        ]
    }


# Atlas Search (Lucene) index — full-text + keyword filters
def _search_index_def() -> dict:
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                "text":     {"type": "string"},
                "title":    {"type": "string"},
                "category": {"type": "token"},
                "doc_id":   {"type": "token"},
                "tags":     {"type": "token"},
                "source":   {"type": "token"},
            },
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_docs(collection, docs: list[dict]) -> None:
    """Upsert documents by doc_id (idempotent)."""
    for doc in docs:
        collection.update_one(
            {"doc_id": doc["doc_id"]},
            {"$set": doc},
            upsert=True,
        )
    print(f"  ✓ Upserted {len(docs)} documents into '{collection.name}'")


def _ensure_index(collection, index_name: str, index_type: str, definition: dict) -> None:
    """Create a search index if it doesn't already exist; skip gracefully if it does."""
    try:
        existing = {i["name"] for i in collection.list_search_indexes()}
    except Exception as exc:
        print(f"  ⚠  Could not list indexes on '{collection.name}': {exc}")
        existing = set()

    if index_name in existing:
        print(f"  ℹ  Index '{index_name}' already exists on '{collection.name}' — skipping creation.")
        return

    model = {
        "name":       index_name,
        "type":       index_type,
        "definition": definition,
    }
    try:
        collection.create_search_index(model)
        print(f"  ✓ Submitted index '{index_name}' ({index_type}) on '{collection.name}' — Atlas is building it.")
    except pymongo.errors.OperationFailure as exc:
        if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower():
            print(f"  ℹ  Index '{index_name}' already exists (race): {exc}")
        else:
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{'='*60}")
    print(f"  Seeding MongoDB Atlas — database: {ATLAS_DB}")
    print(f"{'='*60}\n")

    # ── IT Helpdesk ──────────────────────────────────────────────────────────
    print("[ IT_helpdesk ]")
    it_col = db["IT_helpdesk"]
    _upsert_docs(it_col, IT_HELPDESK_DOCS)
    _ensure_index(it_col, "it_helpdesk_vector_index", "vectorSearch", _vector_index_def("text"))
    _ensure_index(it_col, "it_helpdesk_search_index", "search",       _search_index_def())

    # ── Employee Support ─────────────────────────────────────────────────────
    print("\n[ employee_support ]")
    es_col = db["employee_support"]
    _upsert_docs(es_col, EMPLOYEE_SUPPORT_DOCS)
    _ensure_index(es_col, "employee_support_vector_index", "vectorSearch", _vector_index_def("text"))
    _ensure_index(es_col, "employee_support_search_index", "search",       _search_index_def())

    print("\n")
    print("Atlas is now building the vector search indexes in the background.")
    print("This typically takes 1-3 minutes for small collections.")
    print("You can monitor progress in the Atlas UI → Search Indexes.")
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
