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
