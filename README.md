<div align="center">

# unredact.me

### Make the government write back.

**File FOIA requests and write Congress from your terminal.**
<br>unredact.me drafts the letter, sends it, watches the deadline and chases the agency when it goes quiet.

![python](https://img.shields.io/badge/python-3.10%2B-1f2328?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-1f2328?style=flat-square)
![status](https://img.shields.io/badge/status-alpha-b4231f?style=flat-square)
![stack](https://img.shields.io/badge/CLI%20%2B%20web-Typer%20%C2%B7%20FastAPI%20%C2%B7%20SQLite-1f2328?style=flat-square)

</div>

---

```console
$ unredact foia new --agency fbi --subject "Drone surveillance contracts" --file ask.txt --fee-limit 25
…full request letter citing 5 U.S.C. § 552…
draft #1 (UR-3fa9c1) — submit with: unredact foia submit 1

$ unredact foia submit 1
[email] Sent to foia@agency.gov                     # due date set: 20 business days, holidays skipped

# …three weeks later, the daily timer runs on its own…
reply UR-3fa9c1: acknowledged                       # agency tracking number captured
followed up #1 → foia@agency.gov                    # deadline blown, status inquiry sent

$ unredact congress send --address "…, Youngstown, OH 44503" --subject "Pass the PRESS Act" --message msg.txt --mail …
msg 1: sent — CWC accepted (cwc.house.gov)
msg 2: manual — No automated channel for this office yet — submit at https://…/contact
msg 4: sent — Lob letter created; expected delivery 2026-11-19
```

---

## Why this exists

The Freedom of Information Act turns 60 in 2026. It is one of the strongest transparency laws anywhere, and most people never use it. Agencies rarely say no outright; they just let requests drift past a deadline nobody is tracking. Writing to your representative has the same problem: a dozen browser tabs, ZIP-code gates and forms that time out.

Persistence is what gets results here, and persistence is easy to automate. **unredact.me handles the follow-up so you only have to show up when there's a decision to make.**

## What it does

### 🗂 FOIA & public records

| | |
|---|---|
| **Finds the right office** | Syncs every federal agency component from the FOIA.gov API. Add any state or local office (police departments, school boards, city hall) with one command. |
| **Writes a letter that holds up** | Cites the correct statute for each jurisdiction and includes fee category, fee cap, fee waiver, expedited processing with the required certification, segregability, rolling release and native formats. Agencies check for all of these. |
| **Tracks the deadline** | Business-day math with federal and state holidays. 20 days federal, per-state rules in plain YAML, plus states with no fixed deadline (looking at you, Ohio). |
| **Reads the replies** | Watches an IMAP inbox, matches each reply to its request by ID or agency tracking number, classifies it (acknowledged, fee quote, clarification, partial, complete, denied), extracts tracking numbers and saves attachments. |
| **Follows up automatically** | Overdue requests get a status inquiry, at most once every 10 days. Denials and missed deadlines get an appeal drafted for you. |

### 🏛 Write Congress

| | |
|---|---|
| **Address → your three members** | Free Census geocoding (or Geocodio) maps the address to a district, then to your 2 senators and 1 representative using the public-domain `congress-legislators` dataset. |
| **Uses each office's real inbox** | Delivers through the House **Communicating with Congress** API, a headless-browser run through the member's official form, or an honest hand-off with the link and your message ready to paste. |
| **Paper mail, built in** | Optional printed letters through Lob, sent USPS First-Class to the member's **nearest district office**. Mail to D.C. gets irradiated and can take weeks. |
| **Ready to charge for** | Stripe Checkout with signature-verified webhooks and idempotent print jobs. Run it as a public site and charge per letter. |

## Install

One line, macOS or Linux. You need Python 3.10+ and git.

```bash
curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.me/main/install.sh | bash
```

The installer creates an isolated environment, puts `unredact` on your PATH, then starts **`unredact init`**: a short wizard that asks for your name, email and optional API keys, saves them to `~/.config/unredact/.env`, and downloads the agency and Congress data. You never edit a config file by hand.

<details>
<summary>Other ways to install</summary>

```bash
# with web-form autofill (Playwright + headless Chromium)
curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.me/main/install.sh | bash -s -- --web-forms

# pipx
pipx install git+https://github.com/NPCmillionaire/unredact.me && unredact init

# Docker (web app + daily FOIA job)
git clone https://github.com/NPCmillionaire/unredact.me && cd unredact.me
cp .env.example .env && docker compose up -d

# from source (development)
git clone https://github.com/NPCmillionaire/unredact.me && cd unredact.me
./install.sh --local

# uninstall (keeps your config and data)
curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.me/main/install.sh | bash -s -- --uninstall
```
</details>

## Templates: good requests without the legalese

Specific requests get answered; vague ones get stalled. unredact.me ships with **11 request templates** written the way experienced requesters write them:

| | |
|---|---|
| `police-use-of-force` | Use-of-force reports, aggregate data and the governing policy |
| `body-camera` | Body/dash-cam video, CAD logs and reports for one incident |
| `official-emails` | A named official's emails containing your keywords |
| `official-calendar` | Who an official met with, and when |
| `contracts` | Contracts, invoices, bids and scoring sheets for a vendor |
| `surveillance-tech` | ALPR / camera / facial-recognition contracts, policies, sharing deals and audit logs |
| `foia-log` | The agency's own log of every request it received. The best story-finder there is. |
| `inspections` · `complaints` · `purchase-cards` · `communications-with-org` | …and more |

```bash
unredact foia presets
unredact foia new --agency oh-ytown-pd --preset police-use-of-force \
  --var start="January 1, 2025" --var end="October 31, 2026"
```

## The 60-second workflow

```bash
# 1 · Ask
unredact foia new --agency <id> --preset foia-log      # or --subject "…" --file ask.txt

# 2 · Send
unredact foia submit 1                 # or --channel manual --tracking ABC123 for portal filings

# 3 · Forget about it
sudo systemctl enable --now unredact-tick.timer   # daily: read replies, chase overdue requests

# 4 · Act when it matters
unredact foia list                     # fee quote? clarification? denial?
unredact foia appeal 1 --to appeals@agency.gov --send
```

Adding a local agency that isn't on FOIA.gov:

```bash
unredact foia agencies add --id oh-ytown-pd --title "Youngstown Police Department" \
  --jurisdiction OH --email <records email>
```

## Web app

```bash
unredact serve --host 0.0.0.0
```

- **`/congress`** — public flow: enter address → preview each message → **confirm by email** → delivered, with a live status page and optional paid printed letters. Per-IP rate limits are built in.
- **`/foia`** — your private dashboard: start from a template, add local agencies, one-click follow-ups, an appeal editor, a "check inbox" button, CSV export and a full timeline of every reply. Protected by `UNREDACT_ADMIN_PASSWORD`, and localhost-only if that isn't set.

## Under the hood

```
unredact/
├── foia/        directory · compose · deadlines · tracker · submit · inbox
├── congress/    directory · geo · compose · send · billing
│   └── deliver/ cwc · webform · lob
├── core/        smtp · jinja templating
└── web/         FastAPI + server-rendered templates
```

One SQLite file holds everything. There is no ORM, no queue server and no JS build step. It runs happily on a Raspberry Pi.

## Delivery channels, honestly

| Channel | Status | Notes |
|---|---|---|
| FOIA by email | ✅ | Most agencies accept email or a structured FOIA inbox. |
| FOIA portals (FOIA.gov, GovQA, NextRequest) | ✍️ manual | File in the portal, log it with `--channel manual`. Deadlines and follow-ups still work. |
| House CWC | ✅ code · 🔑 approval | Requires House Advocacy Vendor approval and a whitelisted IP. |
| Official web forms | ⚠️ per office | Community form definitions go stale; CAPTCHA forms are flagged `needs_human`, never bypassed. |
| Senate SOAPBox | 🛣 roadmap | The spec comes with Senate onboarding. |
| Printed letters (Lob) | ✅ | Needs your Lob key; Stripe is optional. |

## Ground rules

unredact.me exists to amplify **real people**, not to impersonate them.

- Every FOIA request goes out under a real requester's name and email.
- Every congressional message needs a real constituent address and a **confirmed email** before anything is sent. Offices check.
- The same person can't send the same subject to the same office twice within 24 hours.
- Fee estimates are **never** auto-accepted.
- No CAPTCHA solving, no IP rotation, no astroturf.

## Roadmap

- [ ] Senate SOAPBox delivery
- [ ] OCR plus full-text search across released documents
- [ ] Public request pages and RSS feeds for completed releases
- [ ] Deadline rules for all 50 states
- [ ] Bulk campaigns with per-constituent personalization

## Contributing

PRs are welcome. The easiest wins are **state deadline rules** (`unredact/foia/data/jurisdictions.yaml`) and **reply-classifier patterns** (`unredact/foia/inbox.py`). Run `pytest -q` before you push.

## Disclaimer

unredact.me is a tool, not a lawyer. Statutes change, and deadlines vary by state and by how an agency counts "receipt." Verify anything with legal consequences.

<div align="center">
<br>
<b>Built by <a href="https://github.com/NPCmillionaire">Cloudsculpting Labs</a> in Youngstown, Ohio.</b>
<br><sub>MIT licensed · Sunlight is the best disinfectant.</sub>
</div>
