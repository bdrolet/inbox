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

resource "google_pubsub_topic" "inbox_calendar" {
  name       = "inbox-calendar"
  depends_on = [google_project_service.apis]
}
