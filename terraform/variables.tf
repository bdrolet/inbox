variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "schedule_cron" {
  description = "Cron schedule for the Cloud Scheduler job"
  type        = string
  default     = "0 8 * * *"
}

variable "schedule_timezone" {
  description = "Timezone for the cron schedule"
  type        = string
  default     = "America/New_York"
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

variable "openai_api_key" {
  description = "OpenAI API key (used by analyze-emails Cloud Run Job; removed in Phase 5)"
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

variable "job_memory" {
  description = "Memory limit for the Cloud Run Job"
  type        = string
  default     = "1Gi"
}

variable "job_cpu" {
  description = "CPU limit for the Cloud Run Job"
  type        = string
  default     = "1"
}

variable "job_timeout" {
  description = "Max execution time for the Cloud Run Job (seconds)"
  type        = string
  default     = "86400"
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
