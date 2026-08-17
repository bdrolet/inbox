# Inbox: remove Google Calendar ownership — handoff plan

**Run this in a separate Claude Code session in `~/src/inbox`.**
Companion to `docs/superpowers/specs/2026-08-17-schedule-service-design.md`
in the schedule repo. Copy this file into inbox as
`docs/superpowers/plans/2026-08-17-remove-google-calendar.md` when starting.

## Gate — do NOT start until all are true

- [ ] `schedule-process` CF is deployed and has created at least one Google
      Calendar event from a live `email_classified` message (check the
      `calendar_events` table in db `schedule` and the calendar itself).
- [ ] The three secrets `google-calendar-client-id`, `google-calendar-client-secret`,
      `google-calendar-refresh-token` have been `terraform import`ed into the
      **schedule** terraform state (schedule's `secrets.tf` declares them as
      owned resources) and `terraform plan` in schedule shows no changes for them.
- [ ] Ben has confirmed the schedule events look right (title/time/timezone).

## Goal

Inbox keeps zero Google Calendar code. The RSVP webhook flow
(`/calendar?id=…&action=accept|decline|maybe`) still sends the Graph
response to the organizer and records `user_response`; it no longer inserts
into Google Calendar — schedule already created that event at
classification time.

## Changes

1. **Terraform state, before code:** in `~/src/inbox/terraform`, run
   `terraform state rm 'google_secret_manager_secret.secrets["google-calendar-client-id"]'`
   (and `…-client-secret`, `…-refresh-token`), plus their
   `google_secret_manager_secret_version` / IAM member resources if present.
   `state rm`, **never** `destroy` — schedule owns the live secrets now.
   Then delete the three names from inbox's secrets map / locals and remove
   the `secret_environment_variables` blocks that inject them into
   `inbox-process` (`terraform/cloud_functions.tf` ~lines 285-300) and any
   IAM accessor bindings for them. `terraform plan` must show only the CF
   env-var change and no secret destruction.
2. **Code removals:**
   - delete `clients/google_calendar.py`
   - delete `scripts/get_google_calendar_token.py` (moved to schedule)
   - `services/calendar_response.py`: drop the `import clients.google_calendar as gcal`
     and the `gcal.add_event(...)` block inside `action == "accept"`; keep
     `graph.accept_event` and `repo_cal.set_response`
   - remove `google-api-python-client` / `google-auth-oauthlib` from
     `requirements.txt` if nothing else imports them (grep first)
   - README / CLAUDE.md / docs: remove mentions of inbox adding events to
     Google Calendar; add a line that schedule owns Google Calendar.
   - `.env`/`fetch-env.sh`: drop `GOOGLE_CALENDAR_*`.
3. **Keep:** `services/email_events.py:invite_extras` (the "Open in Google
   Calendar" template URL is a plain link, still useful in Asana tasks);
   `services/calendar_invite.py` (ICS detection feeds seed_key_points, which
   schedule relies on); `repo/calendar_invites.py`.
4. **Tests:** update `tests/` for `calendar_response` (assert no calendar
   call), delete tests for the removed client.
5. Open PR via `/pr-open`; after merge, `terraform apply` (the state rm above
   is done locally before the plan/apply of this PR).

## Verification

- Trigger an RSVP accept from a task link → Graph shows accepted; no
  exception in `inbox-process`/webhook logs; no duplicate event appears on
  Google Calendar.
- `terraform plan` in both repos is clean.
