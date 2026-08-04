"""
generate_records.py — Bulk synthetic data generator for IT_helpdesk and employee_support.
==========================================================================================

Generates and inserts thousands of realistic knowledge-base articles into the
`ai_search` database so the collections have enough data for meaningful load
testing and semantic search evaluation.

Usage:
    cd /Users/venkatesh.shanbhag/Documents/AI-Search
    python -m agents.pipeline.generate_records [--count N] [--batch B] [--clear]

    --count N   Total records per collection  (default: 5000)
    --batch B   MongoDB insertMany batch size  (default: 500)
    --clear     Drop existing docs before inserting (keeps indexes)

The script is fully self-contained — no external AI calls, no extra deps beyond
pymongo (already installed). Every document is assembled from randomised templates
so the corpus is lexically diverse for both fulltext and vector search.
"""

from __future__ import annotations

import argparse
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

# ── load .env ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
for line in (_ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip().strip("'\"")
    os.environ.setdefault(k.strip(), v)

import pymongo  # noqa: E402

ATLAS_URI = os.environ["ATLAS_URI"]
ATLAS_DB  = os.environ.get("ATLAS_DB", "ai_search")

# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary banks
# ─────────────────────────────────────────────────────────────────────────────

# ── IT Helpdesk ───────────────────────────────────────────────────────────────

_IT_CATEGORIES = [
    "network", "account_access", "security", "software", "hardware",
    "email", "collaboration", "remote_access", "support", "mobile",
]

_IT_ISSUE_TYPES = [
    "VPN", "password reset", "MFA", "SSO", "Wi-Fi", "Outlook", "Teams", "Slack",
    "printer", "laptop", "BitLocker", "CrowdStrike", "certificate", "antivirus",
    "remote desktop", "software installation", "access request", "email setup",
    "monitor", "keyboard", "USB hub", "Bluetooth", "screen sharing",
    "browser extension", "proxy settings", "DNS", "firewall", "Active Directory",
    "Azure AD", "Okta", "FIDO2 key", "YubiKey", "MDM enrolment", "Intune",
    "Jamf", "macOS update", "Windows update", "OneDrive", "SharePoint",
    "Zoom", "WebEx", "Google Meet", "VoIP phone", "headset", "docking station",
    "SSD replacement", "RAM upgrade", "thermal paste", "BIOS update",
    "network drive mapping", "VPN split tunnelling", "corporate Wi-Fi",
    "guest network", "port forwarding", "IP conflict", "DHCP", "static IP",
]

_IT_ACTIONS = [
    "troubleshoot", "configure", "reset", "install", "update", "uninstall",
    "enable", "disable", "connect", "disconnect", "set up", "migrate",
    "back up", "restore", "audit", "enrol", "register", "revoke", "grant",
    "request", "approve", "escalate", "diagnose", "patch", "deploy",
]

_IT_AUDIENCES = [
    "Windows users", "macOS users", "remote employees", "new hires",
    "managers", "contractors", "IT staff", "developers", "finance team",
    "sales team", "all employees", "international employees",
]

_IT_TOOLS = [
    "ServiceNow", "Jira", "Zendesk", "SCCM", "Jamf Pro", "Intune",
    "Azure Portal", "Okta Admin", "CrowdStrike Falcon", "Qualys",
    "Nessus", "SolarWinds", "Nagios", "PagerDuty", "Splunk",
    "LastPass", "1Password", "CyberArk", "HashiCorp Vault",
]

_IT_SLA_LEVELS = ["P1 Critical", "P2 High", "P3 Medium", "P4 Low"]

_IT_RESOLUTION_STEPS = [
    "Open a ticket at helpdesk.company.com",
    "Call the IT helpdesk at ext. 1000",
    "Submit a request via ServiceNow → IT Catalog",
    "Check the IT self-service portal for automated resolution",
    "Contact your local IT support representative",
    "Use the remote support tool sent to your email",
    "Schedule a callback with a level-2 engineer",
    "Restart the affected service or device and try again",
    "Clear the application cache and re-authenticate",
    "Revoke and re-issue the certificate from the IT portal",
    "Run the diagnostic script from the IT intranet",
    "Re-enrol the device in MDM using the company portal app",
    "Flush DNS and reset the network adapter",
    "Disable and re-enable the VPN adapter in Network Settings",
    "Run Windows Update and reboot before raising a ticket",
]

_IT_TEMPLATE = """\
{title}

{overview}

Affected users: {audience}.
Category: {category} | Priority: {sla}.

Steps to resolve:
{steps}

If the issue persists after following these steps, {resolution}.
For urgent escalations, reference ticket type {sla} and include your employee ID and device hostname.
Additional notes: {notes}
"""

_IT_OVERVIEWS = [
    "This issue commonly occurs after a system update or policy change pushed by IT.",
    "Users experiencing this problem should verify their device is enrolled in the company MDM.",
    "This is a known intermittent issue affecting a subset of users on specific OS versions.",
    "The root cause is typically an expired certificate or cached credential.",
    "This behaviour is expected after password changes — follow the steps below to reconfigure.",
    "Network-related issues like this are often caused by proxy misconfiguration or DNS cache.",
    "Software conflicts may arise when third-party tools interfere with company-managed applications.",
    "Security policy changes can trigger this error — the resolution steps restore the previous state.",
    "Hardware compatibility issues sometimes occur after OS upgrades.",
    "This error surfaces when the device has been offline for more than 30 days.",
    "Multi-factor authentication issues are the most common category of access problems.",
    "VPN connectivity problems usually trace back to outdated GlobalProtect agents.",
    "Printer mapping failures occur when Group Policy Objects are not refreshed.",
    "Browser certificate warnings on internal sites indicate a missing root CA installation.",
    "Remote desktop disconnects are caused by session timeout policies or bandwidth throttling.",
]

_IT_NOTES = [
    "This article applies to Windows 10/11 and macOS 13+.",
    "Linux users should contact IT directly as the self-service portal does not support Linux MDM.",
    "VPN must be connected before attempting to reach internal resources.",
    "Admin rights are required for this step — if unavailable, submit an elevation request.",
    "The fix is temporary; a permanent solution will be deployed in the next patch cycle.",
    "Screenshots of the error message will speed up ticket resolution significantly.",
    "This procedure will not delete any personal data or work files.",
    "Back up your work before performing a factory reset or re-enrolment.",
    "A reboot is required to complete the configuration change.",
    "Allow up to 15 minutes for policy changes to propagate across all services.",
    "Two-factor authentication must be active before this change takes effect.",
    "Contact your manager for approval before raising a P1 or P2 ticket.",
    "If working remotely, ensure your home internet connection is stable before troubleshooting.",
    "This fix requires the device to be on the corporate network or connected via VPN.",
    "Document the steps taken in your ticket for audit trail purposes.",
]


def _make_it_steps(n: int = 5) -> str:
    steps = random.sample([
        "Open System Preferences → Network and verify the VPN connection status.",
        "Navigate to Settings → Accounts → Sign in with your company email.",
        "Launch the Company Portal app and click 'Check device compliance'.",
        "Open a browser in Incognito mode and attempt to reproduce the issue.",
        "Clear the browser cache and cookies, then reload the page.",
        "Run `ipconfig /flushdns` on Windows or `sudo dscacheutil -flushcache` on Mac.",
        "Disable the firewall temporarily and test connectivity.",
        "Right-click the network icon → Troubleshoot to run the Windows network diagnostic.",
        "Open Keychain Access on macOS and remove expired certificates.",
        "Re-add your email account in Outlook → File → Add Account.",
        "Uninstall and reinstall the application from the Company App Portal.",
        "Sync time settings with the corporate NTP server.",
        "Remove and re-add the network printer via Settings → Devices.",
        "Force-sync the MDM profile from System Preferences → Profiles.",
        "Run `gpupdate /force` in an elevated command prompt.",
        "Check the Azure AD Sign-in Logs for failed authentication attempts.",
        "Verify the SSL certificate chain using `openssl s_client -connect host:443`.",
        "Download the latest GlobalProtect agent from the IT portal and reinstall.",
        "Generate a new SSH key pair and upload the public key to the developer portal.",
        "Open Disk Utility on macOS → First Aid to repair disk permissions.",
        "Export the BitLocker recovery key to a USB drive as a backup.",
        "Join the device to Azure AD via Settings → Accounts → Access work or school.",
        "Configure the proxy settings in your browser to use `proxy.company.com:8080`.",
        "Enable FIDO2 authentication in the Azure AD security settings.",
        "Submit a privilege elevation request via ServiceNow before proceeding.",
    ], k=n)
    return "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))


def _generate_it_doc(idx: int) -> dict[str, Any]:
    issue    = random.choice(_IT_ISSUE_TYPES)
    action   = random.choice(_IT_ACTIONS)
    category = random.choice(_IT_CATEGORIES)
    audience = random.choice(_IT_AUDIENCES)
    sla      = random.choice(_IT_SLA_LEVELS)
    tool     = random.choice(_IT_TOOLS)
    overview = random.choice(_IT_OVERVIEWS)
    notes    = random.choice(_IT_NOTES)
    resolution = random.choice(_IT_RESOLUTION_STEPS)
    n_steps  = random.randint(4, 7)

    title = f"{action.capitalize()} {issue} — {category.replace('_',' ').title()} Guide"

    body = _IT_TEMPLATE.format(
        title=title,
        overview=overview,
        audience=audience,
        category=category.replace("_", " "),
        sla=sla,
        steps=_make_it_steps(n_steps),
        resolution=resolution,
        notes=notes,
    ).strip()

    tags = list({issue.lower().split()[0], category, action, sla.split()[0].lower()})
    tags += random.sample(
        ["vpn","mfa","sso","password","laptop","email","printer","security","network","access"],
        k=random.randint(2, 4),
    )

    return {
        "doc_id":   f"it-gen-{idx:06d}",
        "title":    title,
        "category": category,
        "text":     body,
        "tags":     list(set(tags)),
        "source":   "IT Knowledge Base",
        "priority": sla,
        "tool":     tool,
        "audience": audience,
    }


# ── Employee Support ───────────────────────────────────────────────────────────

_HR_CATEGORIES = [
    "leave", "payroll", "expenses", "benefits", "performance",
    "remote_work", "onboarding", "offboarding", "compliance", "recruitment",
]

_HR_TOPICS = [
    "annual leave", "sick leave", "maternity leave", "paternity leave",
    "emergency leave", "bereavement leave", "PTO encashment", "leave carry-over",
    "salary payment", "pay slip", "salary revision", "increment process",
    "bonus payment", "commission structure", "payroll discrepancy",
    "travel reimbursement", "meal allowance", "accommodation claim",
    "fuel reimbursement", "internet allowance", "home office stipend",
    "health insurance", "dental coverage", "vision plan", "life insurance",
    "401k contribution", "pension scheme", "provident fund", "ESIC",
    "performance review", "appraisal cycle", "promotion criteria", "PIP process",
    "goal setting", "OKR framework", "360 degree feedback",
    "work from home policy", "hybrid work schedule", "flexible hours",
    "remote work equipment", "co-working space allowance",
    "new hire orientation", "onboarding checklist", "buddy programme",
    "probation period", "confirmation process",
    "resignation process", "notice period", "full and final settlement",
    "exit interview", "relieving letter", "experience certificate",
    "code of conduct", "ethics policy", "anti-harassment policy",
    "data privacy", "GDPR compliance", "whistleblower policy",
    "employee referral", "internal job posting", "lateral transfer",
    "deputation", "secondment",
]

_HR_DEPARTMENTS = [
    "Human Resources", "People Operations", "Finance", "Payroll",
    "Talent Acquisition", "Learning & Development", "Legal & Compliance",
]

_HR_SYSTEMS = [
    "Workday", "BambooHR", "SAP SuccessFactors", "Oracle HCM",
    "Darwinbox", "Keka", "Zoho People", "Concur", "Fyle",
]

_HR_DEADLINES = [
    "by the 5th of each month", "within 30 days of the event",
    "before the end of the financial year", "within 7 business days",
    "at least 4 weeks in advance", "no later than the 20th of the month",
    "within 60 days of the expense date", "before the quarter closes",
    "within the same pay cycle", "by December 31",
]

_HR_ELIGIBILITY = [
    "All full-time employees who have completed their probation period.",
    "Permanent employees on the payroll as of the policy effective date.",
    "Employees with a minimum of 6 months of service.",
    "Full-time and part-time employees excluding contractors and consultants.",
    "Confirmed employees at Grade 4 and above.",
    "Employees in client-facing roles with a valid business justification.",
    "All employees regardless of grade, location, or department.",
    "Employees enrolled in the company health plan as of January 1.",
]

_HR_CONTACTS = [
    "your HR Business Partner", "the People Operations team at people@company.com",
    "payroll@company.com", "benefits@company.com", "the Shared Services Centre",
    "your line manager", "the Talent Acquisition team", "compliance@company.com",
]

_HR_TEMPLATE = """\
{title}

{overview}

Eligibility: {eligibility}

Policy details:
{details}

How to apply / access:
Log in to {system} and navigate to {module}. {process_note}

Deadline: Submit {deadline}.

For queries, contact {contact}. Response time: within 2 business days for standard requests.

Additional information: {extra}
"""

_HR_OVERVIEWS = [
    "This policy applies to all eligible employees globally unless a country-specific addendum exists.",
    "The process described below is effective from the current financial year and supersedes all previous versions.",
    "This benefit is part of the company's commitment to employee wellbeing and work-life balance.",
    "All requests are subject to manager approval and HR verification before processing.",
    "This entitlement is pro-rated for employees who joined mid-year.",
    "Changes to this policy are communicated via the HR newsletter and the company intranet.",
    "The policy is reviewed annually by the People Operations team in consultation with Finance.",
    "Employees on a fixed-term contract may have a different entitlement — refer to your contract.",
    "This process is automated in the HRIS system; paper forms are no longer accepted.",
    "The entitlement resets on January 1 of each calendar year unless otherwise specified.",
    "Tax implications vary by country — consult the Payroll team for location-specific guidance.",
    "Approval workflows are defined in the system; escalation is available if the manager is unresponsive.",
    "Retroactive claims older than 90 days require VP-level approval.",
    "This policy aligns with statutory requirements and may be enhanced by the company.",
    "Employees must keep digital receipts for all claims to facilitate audit compliance.",
]

_HR_DETAILS_BANK = [
    "Entitlement: 20 days per calendar year, accruing at 1.67 days per month.",
    "Payment is processed in the last working day payroll run of each month.",
    "Claims must be supported by original receipts or digital uploads in the HRIS.",
    "The company contributes 50% of the premium; the employee's share is deducted from salary.",
    "Performance ratings are mapped to a compensation band for increment calculation.",
    "The reimbursement cap is ₹5,000 per month for internet and ₹3,000 for mobile.",
    "Carry-over is capped at 5 days; any balance above this is forfeited on December 31.",
    "Employees may nominate up to 4 dependants for health insurance coverage.",
    "The company matches 401k contributions up to 6% of the base salary.",
    "Notice period is 30 days for individual contributors and 60 days for managers.",
    "Probation period is 90 days for most roles; extended to 180 days for senior hires.",
    "The annual increment cycle runs from April 1; letters are issued by March 31.",
    "Expense claims must be categorised as: Travel, Accommodation, Meals, or Miscellaneous.",
    "The referral bonus is paid in the payroll cycle following the 90-day qualifying period.",
    "All compliance training must be completed within 30 days of joining or the policy refresh date.",
]

_HR_EXTRAS = [
    "Policy documents are available on the HR intranet under Policies & Handbooks.",
    "For international travel, pre-approval is mandatory regardless of trip duration.",
    "Unused sick leave cannot be encashed and does not carry over to the next year.",
    "The company observes a hybrid model — at least 2 anchor days in office per week.",
    "Employees may request a one-time advance on salary in case of financial emergency.",
    "All leave requests are subject to team availability and manager discretion.",
    "The annual bonus is discretionary and not a guaranteed component of CTC.",
    "Employees who resign during the performance cycle are not eligible for the annual bonus.",
    "Expense claims submitted after 60 days require additional approval from the Finance head.",
    "The full and final settlement is processed within 45 days of the last working day.",
    "Background verification is mandatory for all new hires before the joining date.",
    "Health insurance coverage lapses 30 days after the last day of employment.",
    "ESOP details are governed by the Employee Stock Option Plan document.",
    "The company provides a ₹10,000 one-time home-office setup allowance for confirmed remote employees.",
    "Relocation assistance is provided for transfers involving a city change.",
]


def _make_hr_details(n: int = 4) -> str:
    items = random.sample(_HR_DETAILS_BANK, k=min(n, len(_HR_DETAILS_BANK)))
    return "\n".join(f"• {s}" for s in items)


def _generate_hr_doc(idx: int) -> dict[str, Any]:
    topic    = random.choice(_HR_TOPICS)
    category = random.choice(_HR_CATEGORIES)
    dept     = random.choice(_HR_DEPARTMENTS)
    system   = random.choice(_HR_SYSTEMS)
    deadline = random.choice(_HR_DEADLINES)
    eligible = random.choice(_HR_ELIGIBILITY)
    contact  = random.choice(_HR_CONTACTS)
    overview = random.choice(_HR_OVERVIEWS)
    extra    = random.choice(_HR_EXTRAS)

    modules = {
        "leave":       "Time Off → Request",
        "payroll":     "Pay → Pay Slips",
        "expenses":    "Expenses → Submit Claim",
        "benefits":    "Benefits → Enrolment",
        "performance": "Performance → Goals & Reviews",
        "remote_work": "Settings → Work Location",
        "onboarding":  "Onboarding → Checklist",
        "offboarding": "Offboarding → Exit Process",
        "compliance":  "Learning → Compliance Training",
        "recruitment": "Recruiting → Submit Referral",
    }
    module = modules.get(category, "HR Portal → My Requests")

    process_notes = [
        f"Complete all mandatory fields and attach supporting documents.",
        f"Ensure your manager has approved the request before submission.",
        f"A confirmation email will be sent within 2 business days.",
        f"Track the status of your request under 'My Requests'.",
        f"Upload receipts in PDF or JPEG format; maximum file size 5 MB.",
    ]

    title = f"{topic.replace('_',' ').title()} — {dept} Policy Guide"

    body = _HR_TEMPLATE.format(
        title=title,
        overview=overview,
        eligibility=eligible,
        details=_make_hr_details(random.randint(3, 5)),
        system=system,
        module=module,
        process_note=random.choice(process_notes),
        deadline=deadline,
        contact=contact,
        extra=extra,
    ).strip()

    tags = list({topic.split()[0], category, dept.split()[0].lower()})
    tags += random.sample(
        ["leave","payroll","benefits","hr","policy","salary","reimbursement",
         "onboarding","performance","remote","compliance","bonus"],
        k=random.randint(2, 4),
    )

    return {
        "doc_id":     f"hr-gen-{idx:06d}",
        "title":      title,
        "category":   category,
        "text":       body,
        "tags":       list(set(tags)),
        "source":     "HR Policy Handbook",
        "department": dept,
        "system":     system,
        "topic":      topic,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bulk insert
# ─────────────────────────────────────────────────────────────────────────────

def _bulk_insert(
    col,
    generator,
    count: int,
    batch_size: int,
    clear: bool,
) -> None:
    name = col.name
    if clear:
        result = col.delete_many({})
        print(f"  Cleared {result.deleted_count:,} existing docs from '{name}'")

    existing = col.estimated_document_count()
    start_idx = existing + 1
    total_inserted = 0
    t0 = time.perf_counter()
    batches = (count + batch_size - 1) // batch_size

    for b in range(batches):
        batch_start = start_idx + b * batch_size
        batch_end   = min(batch_start + batch_size, start_idx + count)
        docs = [generator(i) for i in range(batch_start, batch_end)]
        if not docs:
            break
        col.insert_many(docs, ordered=False)
        total_inserted += len(docs)
        elapsed = time.perf_counter() - t0
        rate    = total_inserted / elapsed
        pct     = 100 * total_inserted / count
        bar     = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(
            f"\r  [{bar}] {pct:5.1f}%  {total_inserted:,}/{count:,}  "
            f"{rate:.0f} docs/s",
            end="", flush=True,
        )

    elapsed = time.perf_counter() - t0
    print(f"\r  ✓ Inserted {total_inserted:,} docs into '{name}' "
          f"in {elapsed:.1f}s ({total_inserted/elapsed:.0f} docs/s)    ")
    print(f"  Total docs in '{name}': {col.estimated_document_count():,}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk synthetic data generator")
    parser.add_argument("--count", type=int, default=5000,
                        help="Records to generate per collection (default: 5000)")
    parser.add_argument("--batch", type=int, default=500,
                        help="insertMany batch size (default: 500)")
    parser.add_argument("--clear", action="store_true",
                        help="Delete existing generated docs before inserting")
    args = parser.parse_args()

    client = pymongo.MongoClient(ATLAS_URI)
    db     = client[ATLAS_DB]

    print(f"\n{'='*60}")
    print(f"  Synthetic data generator — database: {ATLAS_DB}")
    print(f"  Records per collection : {args.count:,}")
    print(f"  Batch size             : {args.batch:,}")
    print(f"  Clear existing         : {args.clear}")
    print(f"{'='*60}\n")

    print("[ IT_helpdesk ]")
    _bulk_insert(db["IT_helpdesk"],     _generate_it_doc, args.count, args.batch, args.clear)

    print("\n[ employee_support ]")
    _bulk_insert(db["employee_support"], _generate_hr_doc, args.count, args.batch, args.clear)

    print(f"\n{'='*60}")
    print("  Done. Atlas is indexing the new documents in the background.")
    print("  Vector index status can be checked in the Atlas UI → Search Indexes.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
