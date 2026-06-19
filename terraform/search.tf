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

resource "google_cloudfunctions2_function" "search" {
  name     = "inbox-search"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "search"
    source {
      storage_source {
        bucket = google_storage_bucket.cf_source.name
        object = google_storage_bucket_object.process_source.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.search_cf.email
    min_instance_count    = 0
    max_instance_count    = 3
    timeout_seconds       = 60
    available_memory      = "512Mi"
    environment_variables = {
      GCP_PROJECT_ID            = var.project_id
      CLOUD_SQL_CONNECTION_NAME = google_sql_database_instance.inbox.connection_name
      POSTGRES_USER             = var.db_user
      POSTGRES_DB               = "app"
      MSAL_SECRET_NAME          = "msal-token-cache"
      SHARED_MAILBOXES          = var.shared_mailboxes
    }
    secret_environment_variables {
      key        = "SEARCH_TOKEN"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["search-token"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "POSTGRES_PASSWORD"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["inbox-db-password"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "CLIENT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-id"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-secret"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "TENANT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["tenant-id"].secret_id
      version    = "latest"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_sql_database_instance.inbox,
  ]
}

# Allow unauthenticated invocations — bearer token auth is enforced in code via SEARCH_TOKEN
resource "google_cloudfunctions2_function_iam_member" "search_public" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.search.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "search_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.search.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "search_url" {
  description = "Search Cloud Function URL"
  value       = google_cloudfunctions2_function.search.service_config[0].uri
}
