resource "google_artifact_registry_repository" "email_analysis" {
  location      = var.region
  repository_id = "email-analysis"
  format        = "DOCKER"
  description   = "Docker images for the email analysis Cloud Run job"

  depends_on = [google_project_service.apis]
}
