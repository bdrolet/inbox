# ---------------------------------------------------------------------------
# Graph subscription renewal — runs every 2 days, 1 hour before midnight UTC
# Subscriptions expire after ~3 days; renewing every 2 days gives a 1-day buffer
# ---------------------------------------------------------------------------
resource "google_service_account" "scheduler_sa" {
  account_id   = "inbox-scheduler"
  display_name = "Inbox Cloud Scheduler SA"
}

resource "google_cloudfunctions2_function_iam_member" "renew_invoker" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.renew.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

# Gen2 CFs run on Cloud Run — also need the Cloud Run invoker role
resource "google_cloud_run_v2_service_iam_member" "renew_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.renew.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_cloud_scheduler_job" "inbox_renew" {
  name      = "inbox-subscription-renew"
  schedule  = "0 23 */2 * *"
  time_zone = "UTC"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.renew.service_config[0].uri
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }

    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }

  depends_on = [google_project_service.apis]
}
