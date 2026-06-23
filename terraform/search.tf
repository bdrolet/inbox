# ---------------------------------------------------------------------------
# Search Cloud Function — HTTP trigger, searches Outlook + M365 groups
# ---------------------------------------------------------------------------
resource "google_service_account" "search_cf" {
  account_id   = "inbox-search-cf"
  display_name = "Inbox Search Cloud Function"
}

resource "google_project_iam_member" "search_cf_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.search_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "search_cf_msal_accessor" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.search_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "search_cf_msal_version_manager" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.search_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "search_cf_db_password" {
  secret_id = google_secret_manager_secret.secrets["inbox-db-password"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.search_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "search_cf_azure" {
  for_each  = toset(["client-id", "client-secret", "tenant-id"])
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.search_cf.email}"
}

resource "google_secret_manager_secret_iam_member" "search_cf_search_token" {
  secret_id = google_secret_manager_secret.secrets["search-token"].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.search_cf.email}"
}

