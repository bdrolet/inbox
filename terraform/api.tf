# ---------------------------------------------------------------------------
# Artifact Registry — Docker repository for inbox container images
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "inbox" {
  repository_id = "inbox"
  format        = "DOCKER"
  location      = var.region

  depends_on = [google_project_service.apis]
}

locals {
  api_image = "${var.region}-docker.pkg.dev/${var.project_id}/inbox/inbox-api:latest"
}

# ---------------------------------------------------------------------------
# inbox-api Cloud Run service — FastAPI search endpoint
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name     = "inbox-api"
  location = var.region

  template {
    service_account = google_service_account.search_cf.email
    timeout         = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      # Placeholder until the first real image is pushed via gcloud builds submit.
      # After initial apply, update with:
      #   gcloud builds submit --tag <api_image> --project <project_id>
      image = local.api_image

      resources {
        limits = {
          memory = "512Mi"
        }
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "CLOUD_SQL_CONNECTION_NAME"
        value = google_sql_database_instance.inbox.connection_name
      }
      env {
        name  = "POSTGRES_USER"
        value = var.db_user
      }
      env {
        name  = "POSTGRES_DB"
        value = "app"
      }
      env {
        name  = "MSAL_SECRET_NAME"
        value = "msal-token-cache"
      }
      env {
        name  = "SHARED_MAILBOXES"
        value = var.shared_mailboxes
      }
      env {
        name = "SEARCH_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["search-token"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "POSTGRES_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["inbox-db-password"].secret_id
            version = "latest"
          }
        }
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
    }
  }

  # Ignore image tag changes — updated outside Terraform via gcloud run deploy
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_project_service.apis,
    google_sql_database_instance.inbox,
    google_artifact_registry_repository.inbox,
  ]
}

# Allow unauthenticated invocations — bearer token auth enforced in app code via SEARCH_TOKEN
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Allow the service account to pull images from Artifact Registry
resource "google_artifact_registry_repository_iam_member" "search_cf_ar_reader" {
  repository = google_artifact_registry_repository.inbox.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.search_cf.email}"
}

# Allow the GitHub Actions deployer SA to push images and redeploy the Cloud Run service
resource "google_artifact_registry_repository_iam_member" "deployer_ar_writer" {
  repository = google_artifact_registry_repository.inbox.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.deployer_sa}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_run_developer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.deployer_sa}"
}

output "search_url" {
  description = "inbox-api Cloud Run service URL"
  value       = google_cloud_run_v2_service.api.uri
}
