resource "google_service_account" "job_sa" {
  account_id   = "email-analysis-job"
  display_name = "Email Analysis Cloud Run Job"
}

resource "google_cloud_run_v2_job" "email_analysis" {
  name     = "email-analysis"
  location = var.region

  template {
    template {
      service_account = google_service_account.job_sa.email
      timeout         = "${var.job_timeout}s"

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.email_analysis.repository_id}/analyze-emails:latest"

        resources {
          limits = {
            memory = var.job_memory
            cpu    = var.job_cpu
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }

        env {
          name  = "MSAL_SECRET_NAME"
          value = "msal-token-cache"
        }

        env {
          name = "CLIENT_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets["client-id"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "CLIENT_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets["client-secret"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "TENANT_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets["tenant-id"].secret_id
              version = "latest"
            }
          }
        }

        env {
          name = "OPENAI_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets["openai-api-key"].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.secrets,
  ]
}
