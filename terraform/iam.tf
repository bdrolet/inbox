# ---------------------------------------------------------------------------
# Processor Cloud Function service account
# ---------------------------------------------------------------------------
resource "google_service_account" "process_cf" {
  account_id   = "inbox-process-cf"
  display_name = "Inbox Processor Cloud Function"
}

# Connect to Cloud SQL
resource "google_project_iam_member" "process_cf_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.process_cf.email}"
}

# Read the MSAL token cache from Secret Manager
resource "google_secret_manager_secret_iam_member" "process_cf_msal_accessor" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# Write refreshed MSAL tokens back to Secret Manager
resource "google_secret_manager_secret_iam_member" "process_cf_msal_version_manager" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# Read Azure credentials (client-id, client-secret, tenant-id)
resource "google_secret_manager_secret_iam_member" "process_cf_azure" {
  for_each  = toset(["client-id", "client-secret", "tenant-id"])
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# Read the DB password from Secret Manager
resource "google_secret_manager_secret_iam_member" "process_cf_db_password" {
  secret_id = google_secret_manager_secret.secrets["inbox-db-password"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# Read the Anthropic API key from Secret Manager
resource "google_secret_manager_secret_iam_member" "process_cf_anthropic" {
  secret_id = google_secret_manager_secret.secrets["anthropic-api-key"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# Read the ntfy access token from Secret Manager
resource "google_secret_manager_secret_iam_member" "process_cf_ntfy_token" {
  secret_id = data.google_secret_manager_secret.ntfy_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.process_cf.email}"
}

# ---------------------------------------------------------------------------
# Webhook Cloud Function service account
# ---------------------------------------------------------------------------
resource "google_service_account" "webhook_cf" {
  account_id   = "inbox-webhook-cf"
  display_name = "Inbox Webhook Cloud Function"
}

# Publish to inbox-messages topic
resource "google_pubsub_topic_iam_member" "webhook_cf_publisher" {
  topic  = google_pubsub_topic.inbox_messages.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.webhook_cf.email}"
}

# Publish to inbox-labels topic (human feedback from ntfy action buttons)
resource "google_pubsub_topic_iam_member" "webhook_cf_labels_publisher" {
  topic  = google_pubsub_topic.inbox_labels.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.webhook_cf.email}"
}

# ---------------------------------------------------------------------------
# Renewal Cloud Function service account
# ---------------------------------------------------------------------------
resource "google_service_account" "renew_cf" {
  account_id   = "inbox-renew-cf"
  display_name = "Inbox Subscription Renewal Cloud Function"
}

resource "google_secret_manager_secret_iam_member" "renew_cf_msal_accessor" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.renew_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "renew_cf_msal_version_manager" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.renew_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "renew_cf_azure" {
  for_each  = toset(["client-id", "client-secret", "tenant-id"])
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.renew_cf.email}"
}
