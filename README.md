# redtape

Automate the two most tedious parts of civic paperwork:

- **FOIA / public-records requests.** Agency directory (FOIA.gov API, plus your own state/local entries), statute-correct letters, deadline math (business days and federal holidays), an IMAP reply parser that auto-classifies acknowledgments, fee quotes, denials and releases, and generated follow-ups and appeals.
- **Contacting Congress.** Address → district → your 2 senators and 1 representative. Delivery goes through the House **CWC** API, the office's **web form** (Playwright), or an honest manual fallback. Constituents can also pay for **printed letters** (Stripe → Lob) mailed to the member's district office.

You get a CLI plus a FastAPI web app sharing one SQLite database.

```
redtape/
  core/        SMTP, Jinja templating
  foia/        directory · compose · deadlines · tracker · submit · inbox
  congress/    directory · geo · compose · send · billing
    deliver/   cwc · webform · lob
  web/         FastAPI app + templates
```

## Install

```bash
git clone … && cd redtape
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'               # add ,webforms for Playwright form-filling
cp .env.example .env && $EDITOR .env
redtape doctor                        # shows which integrations are live
```

## FOIA quick start

```bash
redtape foia agencies sync                         # needs FOIA_GOV_API_KEY (free: foia.gov/developer)
redtape foia agencies search FBI
redtape foia agencies add --id oh-ytown-pd --title "Youngstown Police Department" \
    --jurisdiction OH --email records@…            # state/local agencies aren't in FOIA.gov

redtape foia new --agency <id> --subject "Drone procurement" \
    --file request.txt --fee-category media --fee-limit 50 --fee-waiver
redtape foia submit 1                              # emails the agency inbox (needs SMTP)
redtape foia submit 1 --channel manual --tracking FOIPA-123   # you filed via a portal yourself

redtape foia list --overdue
redtape foia poll                                  # IMAP: match replies by token/tracking no., save attachments
redtape foia followup 1 --send
redtape foia appeal 1 --to appeals@agency.gov --send
redtape foia tick --send                           # daily cron: poll + auto follow-ups (≤ 1 per 10 days)
```

Every request gets a token like `RT-3fa9c1`. It goes in the subject line so replies can be matched automatically. Deadline rules live in `redtape/foia/data/jurisdictions.yaml`. **Verify them for your state**: statutes change, and some states have no fixed clock (Ohio: "promptly").

FOIA.gov portal submission isn't automated, because the portal's submission API is built for agency case-management systems. For agencies that only take portal filings, file in the portal UI and record it with `--channel manual`.

## Congress quick start

```bash
redtape congress sync                                  # members + district offices (public domain data)
redtape congress lookup "100 W Federal St, Youngstown, OH 44503"
redtape congress send --address "…" --prefix Mr. --first Cam --last Preston \
    --email you@… --subject "Support HR 1234" --message msg.txt --dry-run
redtape congress send … --mail                         # also mail printed letters (your Lob account)
```

### Channels, in priority order

| Channel | Who | Setup |
|---|---|---|
| **CWC** (House) | Representatives | Apply as an Advocacy Vendor at house.gov. Requires a **whitelisted static IP** (even for `test-cwc.house.gov`). Check payloads with `redtape congress cwc-validate`. |
| **Web form** | Anyone with a definition | `pip install 'redtape[webforms]' && playwright install chromium`, then `redtape congress webforms-pull`. Community definitions (`unitedstates/contact-congress` format) are **old**, so test each one. CAPTCHA forms return `needs_human`. |
| **Senate SOAPBox** | Senators | Not implemented. Onboarding with the Senate provides the spec. Until then, senators use web forms or manual. |
| **Manual** | Fallback | Status page links to the office's contact form with the message ready to copy. |

### Paid letters

1. The constituent ticks "mail a printed letter" → Stripe Checkout (`LETTER_PRICE_CENTS` × offices).
2. Stripe calls `POST /stripe/webhook` → letters are released → Lob `POST /v1/letters` to the **district office** nearest the constituent. Mail to DC offices is screened and delayed.
3. Idempotency keys (`redtape-<msg id>`) prevent double prints on webhook retries.

Lob's free Developer tier is enough to start. Postage plus printing costs under $1 per letter on paid tiers.

Local webhook testing:
```bash
stripe listen --forward-to localhost:8000/stripe/webhook   # copy the whsec_… into STRIPE_WEBHOOK_SECRET
```

## Web app

```bash
redtape serve --host 0.0.0.0 --port 8000
```

- `/congress` is public.
- `/foia` is your dashboard. It requires `REDTAPE_ADMIN_PASSWORD` (HTTP Basic). Without one, it only answers on localhost.
- Put it behind Caddy or nginx for TLS. Set `BASE_URL` to the public URL (Stripe redirects use it).

## Deploy (Arch / systemd)

```bash
sudo cp deploy/redtape-web.service deploy/redtape-tick.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now redtape-web redtape-tick.timer
```

## Ground rules baked in

- Real requester / constituent identity is required. FOIA submits refuse without `REQUESTER_NAME`/`EMAIL`.
- The same constituent can't send the same subject to the same office twice within 24h.
- Fee estimates are never auto-accepted. Letters cap fees (`--fee-limit`, default $25).
- No CAPTCHA solving, IP rotation, or impersonation. Offices block these, and CWC approval depends on behaving.

## Tests

```bash
pytest -q
```

MIT licensed.
