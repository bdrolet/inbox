# ---------------------------------------------------------------------------
# inbox-redirect — the one public Cloud Run service.
#
# GET /r/{uuid} is opened from an ntfy push notification on a phone, which
# cannot present a Google credential, so this service keeps roles/run.invoker
# → allUsers. The UUID is the capability (404 on anything else, before the DB
# is touched) and the target is an Outlook URL behind its own login. Same
# image as inbox-api, different entrypoint; least-privilege SA.
# ---------------------------------------------------------------------------
resource "google_service_account" "redirect" {
  account_id   = "inbox-redirect"
  display_name = "Inbox redirect Cloud Run service"
}

resource "google_project_iam_member" "redirect_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.redirect.email}"
}

resource "google_secret_manager_secret_iam_member" "redirect_secrets" {
  for_each  = toset(["inbox-db-password", "msal-token-cache", "client-id", "client-secret", "tenant-id"])
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.redirect.email}"
}

# The Graph client writes refreshed tokens back to the MSAL cache.
resource "google_secret_manager_secret_iam_member" "redirect_msal_version_manager" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.redirect.email}"
}

resource "google_artifact_registry_repository_iam_member" "redirect_ar_reader" {
  repository = google_artifact_registry_repository.inbox.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.redirect.email}"
}

resource "google_cloud_run_v2_service" "redirect" {
  name     = "inbox-redirect"
  location = var.region

  template {
    service_account = google_service_account.redirect.email
    timeout         = "30s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image   = local.api_image
      command = ["uvicorn"]
      args    = ["api.redirect_app:app", "--host", "0.0.0.0", "--port", "8080"]

      resources {
        # Cloud Run rejects < 512Mi with CPU always allocated (the v2 default).
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
        value = data.google_sql_database_instance.inbox.connection_name
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

  # Image updated outside Terraform via gcloud run deploy (deploy-api.yml)
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_project_service.apis,
    data.google_sql_database_instance.inbox,
    google_artifact_registry_repository.inbox,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "redirect_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.redirect.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "redirect_deployer_run_developer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.redirect.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.deployer_sa}"
}

output "redirect_url" {
  value = google_cloud_run_v2_service.redirect.uri
}
