locals {
  secrets = {
    "client-id"             = var.client_id
    "client-secret"         = var.client_secret
    "tenant-id"             = var.tenant_id
    "openai-api-key"        = var.openai_api_key
    "anthropic-api-key"     = var.anthropic_api_key
    "msal-token-cache"      = var.msal_token_cache
    "inbox-db-password"     = var.db_password
    "webhook-label-token"   = var.webhook_label_token
    "grafana-otlp-endpoint" = var.grafana_otlp_endpoint
    "grafana-otlp-token"    = var.grafana_otlp_token
  }
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "secrets" {
  for_each    = local.secrets
  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = each.value
}

# ntfy-token and ntfy-password were created outside Terraform — reference as data sources
data "google_secret_manager_secret" "ntfy_token" {
  secret_id = "ntfy-token"
  project   = var.project_id
}

# The Cloud Run service account needs to read all secrets
resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.secrets[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.job_sa.email}"
}

# The Cloud Run service account also needs to add new versions to the MSAL cache secret
resource "google_secret_manager_secret_iam_member" "msal_version_manager" {
  secret_id = google_secret_manager_secret.secrets["msal-token-cache"].secret_id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.job_sa.email}"
}
