# 15. Dashboard and alert services (Phase 6)

Checked 2026-10-26. Re-verify monthly with the other research files.

## ntfy (phone push)
- Publish: HTTP PUT or POST to `https://ntfy.sh/<topic>`, message as the request body.
  Headers `Title` (aliases X-Title, ti, t), `Priority` (5/max/urgent, 4/high, 3/default,
  2/low, 1/min), `Tags` (comma list; emoji short codes become emojis).
  https://docs.ntfy.sh/publish/ (2026-10-26).
- Size: messages over 4,096 bytes (or not UTF-8) are sent as attachments instead; the
  page's "maximum message size (4,096 bytes)". Same page (2026-10-26).
- **UNVERIFIED:** the exact header for access tokens on a protected topic
  (`Authorization: Bearer <token>` is used, optional, only when NTFY_TOKEN is set), and the
  public server's message rate limits. The parts of the page fetched here did not show them.
  Design: the topic name is the secret (pick a long random one), FlowForge sends at most 20
  messages a minute and caps bodies at 3,500 characters.

## Telegram Bot API
- Requests: `https://api.telegram.org/bot<token>/METHOD_NAME` over HTTPS, GET or POST,
  parameters as query string, form, JSON or multipart; the response is JSON with a Boolean
  `ok` and `result` when ok. https://core.telegram.org/bots/api (2026-10-26).
- **UNVERIFIED:** the sendMessage parameter table (chat_id, text length limit of 4,096
  characters, disable_notification). The page is too long to read in full here. FlowForge
  sends `chat_id`, `text` (capped at 3,500 characters) and `disable_notification` (true for
  info messages) as JSON; a rejected call is journaled as `alert_send_failed` with its HTTP
  status, never the URL.

## Dashboard libraries
- FastAPI 0.162.0, uvicorn 0.54.0, Starlette 1.10.0, httpx 0.28.1 (tests): installed from
  PyPI 2026-10-26 and pinned in pyproject.toml (FastAPI, uvicorn) and the dev extra (httpx).
- Fonts (Public Sans, JetBrains Mono) load from Google Fonts; without internet the page
  falls back to the system fonts and still works.
- Charts: Taste Skill `assets/charts.js` (https://github.com/Leonxlnx/taste-skill, cloned
  2026-10-26), copied unchanged into `src/flowforge/api/static/`.

## IBKR weekly login
- Reminder only, from docs/research/08: IB Gateway needs the operator's login approval once
  a week. The pre-market check and Health screen remind on Mondays.
