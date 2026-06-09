---
name: creating-inbox-task
description: Use when locally testing Asana task creation and Outlook draft generation for the inbox respond or review handler without deploying, verifying the end-to-end flow against a real email before pushing to production, or smoke-testing changes to clients/asana.py, services/draft_reply.py, or services/asana_tag_cache.py locally.
metadata:
  type: project
---

# Creating an Inbox Task Locally

Runs the core steps of the respond or review handler (draft generation + Outlook draft + Asana task) against a real email, without triggering a folder move or Pub/Sub event.

## Run

```bash
CLOUD_SQL_CONNECTION_NAME=bens-project-462804:us-central1:inbox \
  POSTGRES_USER=inbox \
  POSTGRES_DB=app \
  POSTGRES_PASSWORD=$(gcloud secrets versions access latest --secret=inbox-db-password --project=bens-project-462804) \
  .venv/bin/python .claude/skills/creating-inbox-task/scripts/create-task-local.py --category review
```

Run from the repo root (`/Users/ben/src/inbox`). Swap `--category respond` for the respond flow.

## Source selection

| DB env vars set? | Email source |
|-----------------|--------------|
| Yes | Cloud SQL — queries the latest message classified as `--category` |
| No | Graph API — fetches the latest non-`RE:` email live from Outlook |

The DB path exercises real classification data (tags, reasoning, importance). The Graph API fallback is useful when Cloud SQL isn't available or to test with a freshly sent email.

## Required env vars in `.env`

| Var | Source |
|-----|--------|
| `ASANA_API_KEY` | `gcloud secrets versions access latest --secret=asana-api-key --project=bens-project-462804` |
| `ASANA_PROJECT_ID` | `1215520877182033` |
| `WEBHOOK_URL` | `https://inbox-webhook-aizbgjlava-uc.a.run.app` |
| `WEBHOOK_LABEL_TOKEN` | `gcloud secrets versions access latest --secret=webhook-label-token --project=bens-project-462804` |

## What each category does

**review:** Resolves tag GIDs from `asana_tag_cache` (creating tags in Asana if new), creates Asana task with Confirmed review / Respond instead / Reference / Ignore action buttons.

**respond:** Same as review, plus generates a Claude draft reply and creates a pre-addressed Outlook draft. Outlook draft only created when running in Graph API source mode (requires a live `external_id`).

## Gotchas

- Always uses a fresh UUID for `external.gid` — avoids Asana's "already assigned" 400 when re-running against the same message.
- Bodies from Cloud SQL are HTML — the script strips tags before passing to Asana.
- Empty `WEBHOOK_URL` causes Asana 400 (relative hrefs rejected by Asana's HTML validator).
- `clients/asana.py` module-level vars are reloaded after dotenv — handled by the script.
- `createReply` fails on `RE:` emails — Graph API fallback filters these out automatically.
