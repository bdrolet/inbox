resource "google_pubsub_topic" "inbox_messages" {
  name       = "inbox-messages"
  depends_on = [google_project_service.apis]
}

# The inbox-process Cloud Function creates its own push subscription via the
# event_trigger block. The pull subscription has been removed.

resource "google_pubsub_topic" "inbox_labels" {
  name       = "inbox-labels"
  depends_on = [google_project_service.apis]
}

# Domain events: one email_classified per processed email + label_applied
# feedback. The tasks repo's CF subscribes (its terraform references this
# topic by name — this apply must run before the tasks repo's apply).
resource "google_pubsub_topic" "email_events" {
  name       = "email-events"
  depends_on = [google_project_service.apis]
}
