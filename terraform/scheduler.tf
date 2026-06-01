resource "google_cloud_scheduler_job" "email_analysis" {
  name      = "email-analysis-daily"
  schedule  = var.schedule_cron
  time_zone = var.schedule_timezone

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.email_analysis.name}:run"

    oauth_token {
      service_account_email = google_service_account.job_sa.email
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "scheduler_invoke" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.job_sa.email}"
}

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
