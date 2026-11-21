"""Seed a demo account, workspace and a small but realistic corpus.

The documents are written to exercise the parts of the engine that are easy
to get wrong: a policy with a numeric threshold, a document with a table, a
bilingual document with Urdu, and a document that deliberately does *not*
cover a topic — so the abstention path can be demonstrated rather than
described.

Run with:  python -m scripts.seed
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Document, User, Workspace
from app.security import hash_password
from app.services import ingest_document

DEMO_EMAIL = "abraz@example.com"
DEMO_PASSWORD = "zakheera-demo-2026"

DOCUMENTS: list[dict] = [
    {
        "title": "Employee Handbook — Leave and Attendance",
        "filename": "employee-handbook-leave.md",
        "content": """# Leave and Attendance Policy

## Annual leave

Every full-time employee is entitled to twenty-four paid annual leave days per
calendar year. Leave accrues at two days per completed month of service. An
employee who joins partway through the year receives a pro-rated entitlement
based on the number of complete months remaining in that year.

Annual leave must be requested at least seven calendar days in advance through
the HR portal. Requests shorter than seven days are treated as emergency leave
and require written approval from the employee's line manager before the day
begins.

A maximum of ten unused annual leave days may be carried into the following
year. Any balance above ten days lapses on 31 December and is not paid out.

## Sick leave

Employees receive ten paid sick days per year. A medical certificate is required
for any absence longer than two consecutive working days. The certificate must
be submitted to HR within three working days of returning to the office.

Sick leave may not be used to extend annual leave or public holidays. Where an
employee is absent immediately before or after a period of annual leave, HR will
request supporting documentation regardless of the length of the absence.

## Parental leave

Mothers are entitled to sixteen weeks of paid maternity leave. Fathers and
adoptive parents are entitled to four weeks of paid paternity leave, which must
be taken within six months of the birth or adoption.

Parental leave does not reduce annual leave entitlement for the year in which it
is taken.

## Attendance

Core working hours are 10:00 to 16:00, Monday to Friday. Employees may arrange
the remaining hours with their line manager. Three unexcused absences in a
rolling ninety-day period trigger a formal attendance review.
""",
    },
    {
        "title": "Expense Reimbursement Policy",
        "filename": "expense-reimbursement-policy.md",
        "content": """# Expense Reimbursement Policy

## What can be claimed

Reasonable expenses incurred wholly and necessarily for company business may be
claimed. This includes travel, accommodation, client meals, professional
subscriptions approved in advance, and small equipment purchases under the
threshold set out below.

Personal expenses are never reimbursable, including alcohol, entertainment that
is not client-facing, and any item purchased for personal use.

## Approval thresholds

| Amount (PKR) | Approval required |
| --- | --- |
| Up to 15,000 | Line manager |
| 15,001 to 75,000 | Line manager and department head |
| 75,001 to 250,000 | Department head and finance director |
| Above 250,000 | Chief financial officer |

Expenses must be submitted within thirty days of being incurred. Claims older
than thirty days require written justification and are approved at the finance
director's discretion. Claims older than ninety days are not reimbursed.

## Receipts

An itemised receipt is required for every claim above 2,000 PKR. A bank
statement or card slip alone is not sufficient, because it does not show what
was purchased. Where a receipt has been lost, a signed declaration plus
corroborating evidence may be accepted for amounts under 5,000 PKR.

## Travel

Economy class is standard for all domestic flights. Business class requires
prior approval from a department head and is limited to flights longer than six
hours. Hotel accommodation is capped at 25,000 PKR per night in Karachi,
Lahore and Islamabad, and 40,000 PKR per night elsewhere.

Mileage for personal vehicle use on company business is reimbursed at 18 PKR per
kilometre. A log of dates, destinations and distances must accompany the claim.

## Payment timing

Approved claims are paid with the next monthly payroll run. A claim approved
after the payroll cut-off on the 20th of the month is paid in the following
month.
""",
    },
    {
        "title": "Information Security Policy",
        "filename": "information-security-policy.md",
        "content": """# Information Security Policy

## Passwords and authentication

All company accounts must use a password of at least fourteen characters.
Passwords must not be reused across company and personal accounts. Multi-factor
authentication is mandatory for email, the HR portal, the finance system and any
account with administrative privileges.

Passwords must be stored in the company-approved password manager. Writing
passwords on paper, in a spreadsheet, or in a shared document is prohibited.

## Devices

Company laptops must have full-disk encryption enabled and the screen lock set
to activate after five minutes of inactivity. Personal devices may access
company email only through the managed mobile application, which enforces a
device passcode and remote wipe capability.

Installing unapproved software on a company device requires a written exception
from the security team. Software must come from the official vendor source.

## Data classification

Information is classified as Public, Internal, Confidential or Restricted.
Restricted data includes customer personal data, payment credentials, and
unreleased financial results. Restricted data may only be stored in approved
systems and may never be copied to a personal device or a personal cloud
account.

## Incident reporting

Any suspected security incident must be reported to the security team within one
hour of discovery. This includes a lost device, a suspected phishing email that
was clicked, accidental disclosure of restricted data, and any unexpected
prompt for credentials.

Do not attempt to investigate a suspected incident yourself. Reporting promptly
is always the right action, and no employee will be penalised for reporting an
incident in good faith.

## Remote work

When working outside the office, employees must use the company VPN to reach
internal systems. Public wireless networks may be used only with the VPN active.
Working on confidential material in a public place where the screen is visible
to others is not permitted.
""",
    },
    {
        "title": "Engineering On-Call Runbook",
        "filename": "engineering-oncall-runbook.md",
        "content": """# Engineering On-Call Runbook

## Rotation

The on-call rotation runs weekly, from Monday 10:00 to the following Monday
10:00. Primary and secondary responders are assigned each week. The secondary
responder is paged only if the primary has not acknowledged an alert within ten
minutes.

Handover happens in the Monday engineering stand-up. The outgoing responder
reports open incidents, any alert that fired more than twice in the week, and
anything that was silenced.

## Severity levels

Severity 1 means the service is unavailable for all users, or data is being
lost. The target response time is five minutes and the incident channel is
opened immediately.

Severity 2 means a major function is degraded for many users, or a single
customer is fully blocked. Target response is thirty minutes.

Severity 3 means a minor function is degraded or a workaround exists. Target
response is the next business day.

## Escalation

If an incident is not resolved within thirty minutes at Severity 1, escalate to
the engineering manager. If it is not resolved within two hours, escalate to the
CTO. Escalation is not an admission of failure; it is how a second pair of eyes
gets involved before the situation gets worse.

## Post-incident review

Every Severity 1 and Severity 2 incident requires a written review within five
working days. The review covers the timeline, the customer impact, the root
cause, and the actions that will prevent recurrence. Reviews are blameless: the
goal is to fix the system, not the person.

## Common alerts

The database connection pool exhaustion alert usually means a slow query is
holding connections. Check the slow query log before restarting anything.

The elevated error rate alert on the payments service is frequently caused by an
expired upstream certificate. Check the certificate expiry before assuming a
code regression.
""",
    },
    {
        "title": "دفتری رہنما اصول — Urdu Summary",
        "filename": "daftari-rahnuma-usool.md",
        "content": """# دفتری رہنما اصول

## چھٹیوں کا نظام

ہر مستقل ملازم کو سال میں چوبیس دن کی تنخواہ کے ساتھ سالانہ چھٹی ملتی ہے۔
چھٹی ہر مکمل مہینے پر دو دن کے حساب سے جمع ہوتی ہے۔

سالانہ چھٹی کی درخواست کم از کم سات دن پہلے ایچ آر پورٹل پر دینی ضروری ہے۔
سات دن سے کم مدت کی درخواست ہنگامی چھٹی سمجھی جاتی ہے اور اس کے لیے منیجر کی
تحریری منظوری لازمی ہے۔

## طبی چھٹی

ملازم کو سال میں دس دن کی تنخواہ کے ساتھ طبی چھٹی ملتی ہے۔ دو کام کے دنوں سے
زیادہ کی غیر حاضری پر ڈاکٹر کا سرٹیفکیٹ ضروری ہے۔

## اخراجات کی واپسی

کمپنی کے کام کے لیے کیے گئے جائز اخراجات واپس ملتے ہیں۔ اخراجات کا دعویٰ
اخراجات ہونے کے تیس دن کے اندر جمع کرنا ضروری ہے۔

ہر دعوے کے ساتھ اصل رسید لازمی ہے۔ رسید کے بغیر دعویٰ قبول نہیں کیا جاتا۔

## معلومات کی حفاظت

کمپنی کے تمام اکاؤنٹس کے لیے کم از کم چودہ حروف کا پاس ورڈ ضروری ہے۔
ای میل اور مالی نظام کے لیے دو مرحلوں کی تصدیق لازمی ہے۔

کسی بھی ممکنہ سیکیورٹی واقعے کی اطلاع سیکیورٹی ٹیم کو ایک گھنٹے کے اندر
دینی چاہیے۔
""",
    },
]


def seed() -> None:
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                password_hash=hash_password(DEMO_PASSWORD),
                display_name="ABraz Baloch",
                is_admin=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Created demo user {DEMO_EMAIL}")
        else:
            print(f"Demo user {DEMO_EMAIL} already exists")

        workspace = db.scalar(
            select(Workspace).where(
                Workspace.owner_id == user.id, Workspace.slug == "company-handbook"
            )
        )
        if workspace is None:
            workspace = Workspace(
                owner_id=user.id,
                name="Company Handbook",
                slug="company-handbook",
                description=(
                    "HR, finance, security and engineering policies for a fictional "
                    "company. Ask it something it does not cover to see it abstain."
                ),
            )
            db.add(workspace)
            db.commit()
            db.refresh(workspace)
            print(f"Created workspace '{workspace.name}'")
        else:
            print(f"Workspace '{workspace.name}' already exists")

        existing_titles = {
            title
            for (title,) in db.execute(
                select(Document.title).where(Document.workspace_id == workspace.id)
            ).all()
        }

        added = 0
        for document in DOCUMENTS:
            if document["title"] in existing_titles:
                continue
            ingest_document(
                db,
                workspace,
                filename=document["filename"],
                title=document["title"],
                content=document["content"],
                mime_type="text/markdown",
                size_bytes=len(document["content"].encode("utf-8")),
            )
            added += 1
            print(f"  + {document['title']}")

        print(f"\nAdded {added} document(s).")
        print(f"\nSign in with:\n  email:    {DEMO_EMAIL}\n  password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed()
