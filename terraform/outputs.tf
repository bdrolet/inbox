output "cloud_run_job_name" {
  description = "Name of the Cloud Run Job"
  value       = google_cloud_run_v2_job.email_analysis.name
}

output "artifact_registry_url" {
  description = "Docker registry URL for pushing images"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.email_analysis.repository_id}"
}

output "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job"
  value       = google_cloud_scheduler_job.email_analysis.name
}

output "service_account_email" {
  description = "Service account used by the Cloud Run Job"
  value       = google_service_account.job_sa.email
}
