# unredact.me

**File it. Chase it. Read what they hid.**

unredact.me automates civic paperwork from both ends:

- **Public-records requests (FOIA + state laws).**
  - Statute-correct letters and an agency directory (the FOIA.gov API plus your own state and local entries).
  - Deadline math in business days, skipping holidays.
  - Agency replies are matched and classified automatically. Rules handle it by default, and an LLM can do it if you configure one.
  - Automatic follow-ups and appeals.
  - **Redaction X-ray**: flags released PDFs where the black box is drawn over text that's still in the file.
  - An **exhaustion check plus a lawsuit-ready packet**: can you sue yet, which court, timeline, letters, and records.
- **Contacting Congress.** Address → district → your 2 senators and 1 representative, delivered via the House **CWC** API, **web forms** (Playwright), or a manual fallback. You can also buy **printed letters** (Stripe → Lob). A stalled FOIA request can be **escalated** to your members as a casework request in one click.

It's a multi-user web app with accounts, per-user dashboards, and opt-in **public request pages**, plus an operator CLI. Both share one SQLite database.

```
unredact/
  users.py     accounts (scrypt), requester identity per request
  core/        SMTP, Jinja templating
  foia/        directory · compose · deadlines · tracker · submit · inbox · xray · litigation
  congress/    directory · geo · compose · send · billing
    deliver/   cwc · webform · lob
  web/         FastAPI app + templates
```

## Web app quick start

```bash
pip install -e '.[dev]'                 # includes pymupdf for X-ray
cp .env.example .env && $EDITOR .env    # SECRET_KEY, SMTP_*, INBOUND_ADDRESS, INBOUND_KEY, ADMIN_EMAILS
unredact foia agencies sync             # load federal agencies (FOIA_GOV_API_KEY)
unredact serve                          # http://127.0.0.1:8000  → Sign up → New request
# or: docker compose up -d
```

**How agency replies get back to users:** every letter is signed by the user and tells the agency to reply to `INBOUND_ADDRESS`. The subject line carries the request token (`UR-3fa9c1`). Point that domain's MX at Postmark and set the inbound webhook to `$BASE_URL/inbound/email?key=$INBOUND_KEY`. Each reply is then matched by token (or agency tracking number), classified, stored, X-rayed, and forwarded to the user by email. Users' personal addresses never go to agencies. A single shared IMAP mailbox works too (`unredact foia poll`).

**Daily job:** `unredact foia tick --send` polls IMAP and sends a follow-up for each overdue request, at most once every 10 days (systemd units are in `deploy/`, and docker-compose runs it as `tick`).

**Upgrading from redtape:** `REDTAPE_DATA_DIR`, `REDTAPE_ADMIN_PASSWORD`, and an existing `redtape.db` still work. The schema migrates on startup. Old `RT-` tokens still match replies, and new requests get `UR-` tokens.

## Install

```bash
git clone … && cd unredact
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'               # add ,webforms for Playwright form-filling
cp .env.example .env && $EDITOR .env
unredact doctor                        # shows which integrations are live
```

## FOIA quick start

```bash
unredact foia agencies sync                         # needs FOIA_GOV_API_KEY (free: foia.gov/developer)
unredact foia agencies search FBI
unredact foia agencies add --id oh-ytown-pd --title "Youngstown Police Department" \
    --jurisdiction OH --email records@…            # state/local agencies aren't in FOIA.gov

unredact foia new --agency <id> --subject "Drone procurement" \
    --file request.txt --fee-category media --fee-limit 50 --fee-waiver
unredact foia submit 1                              # emails the agency inbox (needs SMTP)
unredact foia submit 1 --channel manual --tracking FOIPA-123   # you filed via a portal yourself

unredact foia list --overdue
unredact foia poll                                  # IMAP: match replies by token/tracking no., save attachments
unredact foia followup 1 --send
unredact foia appeal 1 --to appeals@agency.gov --send
unredact foia tick --send                           # daily cron: poll + auto follow-ups (≤ 1 per 10 days)
```

Every request gets a token like `RT-3fa9c1`. It goes in the subject line so replies can be matched automatically. Deadline rules live in `unredact/foia/data/jurisdictions.yaml`. **Verify them for your state**: statutes change, and some states have no fixed clock (Ohio: "promptly").

FOIA.gov portal submission isn't automated, because the portal's submission API is built for agency case-management systems. For agencies that only take portal filings, file in the portal UI and record it with `--channel manual`.

## Congress quick start

```bash
unredact congress sync                                  # members + district offices (public domain data)
unredact congress lookup "100 W Federal St, Youngstown, OH 44503"
unredact congress send --address "…" --prefix Mr. --first Cam --last Preston \
    --email you@… --subject "Support HR 1234" --message msg.txt --dry-run
unredact congress send … --mail                         # also mail printed letters (your Lob account)
```

### Channels, in priority order

| Channel | Who | Setup |
|---|---|---|
| **CWC** (House) | Representatives | Apply as an Advocacy Vendor at house.gov. Requires a **whitelisted static IP** (even for `test-cwc.house.gov`). Check payloads with `unredact congress cwc-validate`. |
| **Web form** | Anyone with a definition | `pip install 'unredact[webforms]' && playwright install chromium`, then `unredact congress webforms-pull`. Community definitions (`unitedstates/contact-congress` format) are **old**, so test each one. CAPTCHA forms return `needs_human`. |
| **Senate SOAPBox** | Senators | Not implemented. Onboarding with the Senate provides the spec. Until then, senators use web forms or manual. |
| **Manual** | Fallback | Status page links to the office's contact form with the message ready to copy. |

### Paid letters

1. The constituent ticks "mail a printed letter" → Stripe Checkout (`LETTER_PRICE_CENTS` × offices).
2. Stripe calls `POST /stripe/webhook` → letters are released → Lob `POST /v1/letters` to the **district office** nearest the constituent. Mail to DC offices is screened and delayed.
3. Idempotency keys (`unredact-<msg id>`) prevent double prints on webhook retries.

Lob's free Developer tier is enough to start. Postage plus printing costs under $1 per letter on paid tiers.

Local webhook testing:
```bash
stripe listen --forward-to localhost:8000/stripe/webhook   # copy the whsec_… into STRIPE_WEBHOOK_SECRET
```

## Web app

```bash
unredact serve --host 0.0.0.0 --port 8000
```

| Route | What it is |
|---|---|
| `/`, `/r/{slug}` | Landing page and public request pages. Each request is opt-in public. Public pages show the request, agency replies, and released files, never the requester's contact details. |
| `/signup`, `/login`, `/account` | Accounts. The profile signs your letters. |
| `/foia` | Each user's dashboard: draft → submit → follow-up / appeal → upload → X-ray → packet → escalate. Admins (`ADMIN_EMAILS`) also see CLI-created requests. |
| `/congress` | Write your members. Pre-filled from your profile and from FOIA escalations. |
| `/inbound/email?key=…` | Postmark inbound webhook. |
| `/stripe/webhook` | Paid letters. |

- Put it behind Caddy or nginx for TLS, and set `BASE_URL` to the `https://` URL. That also turns on secure cookies.
- Session cookies are SameSite=Lax, which blocks cross-site form posts. Add per-form CSRF tokens before you add any cross-origin embeds.

## Deploy (Arch / systemd)

```bash
sudo cp deploy/unredact-web.service deploy/unredact-tick.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now unredact-web unredact-tick.timer
```

## Ground rules baked in

- Real requester / constituent identity is required. FOIA submits refuse without a requester name and reply address.
- X-ray only reports text the file already exposes. It never reconstructs properly burned-in redactions.
- The lawsuit packet is not legal advice and says so.
- The same constituent can't send the same subject to the same office twice within 24h.
- Fee estimates are never auto-accepted. Letters cap fees (`--fee-limit`, default $25).
- No CAPTCHA solving, IP rotation, or impersonation. Offices block these, and CWC approval depends on behaving.

## Tests

```bash
pytest -q
```

MIT licensed.
