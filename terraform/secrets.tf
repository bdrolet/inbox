locals {
  secrets = {
    "client-id"                       = var.client_id
    "client-secret"                   = var.client_secret
    "tenant-id"                       = var.tenant_id
    "anthropic-api-key"               = var.anthropic_api_key
    "msal-token-cache"                = var.msal_token_cache
    "inbox-db-password"               = var.db_password
    "webhook-label-token"             = var.webhook_label_token
    "grafana-otlp-endpoint"           = var.grafana_otlp_endpoint
    "grafana-otlp-token"              = var.grafana_otlp_token
    "asana-api-key"                   = var.asana_api_key
    "hubspot-token"                   = var.hubspot_token
    "google-calendar-client-id"       = var.google_calendar_client_id
    "google-calendar-client-secret"   = var.google_calendar_client_secret
    "google-calendar-refresh-token"   = var.google_calendar_refresh_token
    "hf-token"                        = var.hf_token
    "search-token"                    = var.search_token
  }

  # msal-token-cache is managed separately so CI can't overwrite the live token
  secrets_without_msal = { for k, v in local.secrets : k => v if k != "msal-token-cache" }
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
  for_each    = local.secrets_without_msal
  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = each.value
}

# Separate resource so lifecycle.ignore_changes prevents CI from overwriting the live MSAL token.
# The Cloud Function refreshes this secret autonomously; CI's copy is intentionally ignored after
# the initial seed.
resource "google_secret_manager_secret_version" "msal_token_cache" {
  secret      = google_secret_manager_secret.secrets["msal-token-cache"].id
  secret_data = var.msal_token_cache

  lifecycle {
    ignore_changes = [secret_data]
  }
}

moved {
  from = google_secret_manager_secret_version.secrets["msal-token-cache"]
  to   = google_secret_manager_secret_version.msal_token_cache
}

# ntfy-token and ntfy-password were created outside Terraform — reference as data sources
data "google_secret_manager_secret" "ntfy_token" {
  secret_id = "ntfy-token"
  project   = var.project_id
}

