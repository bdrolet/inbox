variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "client_id" {
  description = "Azure app registration CLIENT_ID"
  type        = string
  sensitive   = true
}

variable "client_secret" {
  description = "Azure app registration CLIENT_SECRET"
  type        = string
  sensitive   = true
}

variable "tenant_id" {
  description = "Azure TENANT_ID"
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key (used by inbox-process Cloud Function)"
  type        = string
  sensitive   = true
}

variable "msal_token_cache" {
  description = "Serialized MSAL token cache JSON (from seed_token_cache.py)"
  type        = string
  sensitive   = true
}

variable "graph_subscription_id" {
  description = "Graph change-notification subscription ID (set after running clients/graph_subscriptions.py register)"
  type        = string
  default     = ""
}

variable "db_user" {
  description = "Cloud SQL database username"
  type        = string
  default     = "inbox"
}

variable "db_password" {
  description = "Cloud SQL database password"
  type        = string
  sensitive   = true
}

variable "ntfy_domain" {
  description = "Domain for the self-hosted ntfy server (e.g. ntfy.drolet.ai)"
  type        = string
  default     = "ntfy.drolet.ai"
}

variable "ntfy_topic" {
  description = "ntfy topic name — treat like a password. Empty string disables notifications."
  type        = string
  default     = ""
}

variable "webhook_label_token" {
  description = "Bearer token required on /label requests from ntfy action buttons. Generate with: openssl rand -hex 32"
  type        = string
  sensitive   = true
}

variable "grafana_otlp_endpoint" {
  description = "Grafana Cloud OTLP gateway URL (e.g. https://otlp-gateway-prod-us-central-0.grafana.net/otlp)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "grafana_otlp_token" {
  description = "Grafana Cloud OTLP Basic Auth token: base64(instance_id:api_key)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "hf_token" {
  description = "HuggingFace API token (read scope) — prevents rate limiting on BGE model download during cold starts"
  type        = string
  sensitive   = true
  default     = ""
}

variable "asana_api_key" {
  description = "Asana Personal Access Token — used by inbox-process CF to create review tasks"
  type        = string
  sensitive   = true
  default     = ""
}

variable "asana_project_id" {
  description = "Asana project GID for inbox review tasks (from https://app.asana.com/0/{gid}/list)"
  type        = string
  default     = ""
}

variable "hubspot_token" {
  description = "HubSpot private app access token (pat-na2-...)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_calendar_client_id" {
  description = "Google OAuth2 client ID for Calendar API"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_calendar_client_secret" {
  description = "Google OAuth2 client secret for Calendar API"
  type        = string
  sensitive   = true
  default     = ""
}

variable "google_calendar_refresh_token" {
  description = "Google OAuth2 refresh token for Calendar API"
  type        = string
  sensitive   = true
  default     = ""
}

variable "search_token" {
  description = "Bearer token callers must include to authenticate search requests. Generate with: openssl rand -hex 32"
  type        = string
  sensitive   = true
}

variable "shared_mailboxes" {
  description = "Comma-separated shared mailbox email addresses the search CF searches by default (e.g. 'inbox@co.com,support@co.com')"
  type        = string
  default     = ""
}
