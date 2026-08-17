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

variable "hf_token" {
  description = "HuggingFace API token (read scope) — prevents rate limiting on BGE model download during cold starts"
  type        = string
  sensitive   = true
  default     = ""
}

variable "asana_project_id" {
  description = "Asana project GID — kept as the documented reference value for the tasks repo (github.com/bdrolet/tasks); inbox no longer creates tasks"
  type        = string
  default     = ""
}

variable "hubspot_token" {
  description = "HubSpot private app access token (pat-na2-...)"
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

variable "deployer_sa" {
  description = "Service account email used by GitHub Actions to deploy (GCP_DEPLOYER_SA secret). Granted AR writer + Cloud Run developer on inbox-api."
  type        = string
}
