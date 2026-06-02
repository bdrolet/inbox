locals {
  cf_source_bucket = "${var.project_id}-cf-source"
}

resource "google_storage_bucket" "cf_source" {
  name                        = local.cf_source_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

# ---------------------------------------------------------------------------
# Webhook function
# ---------------------------------------------------------------------------
data "archive_file" "webhook_source" {
  type        = "zip"
  source_dir  = "${path.module}/../functions/webhook"
  output_path = "${path.module}/.terraform/webhook.zip"
}

resource "google_storage_bucket_object" "webhook_source" {
  name   = "webhook-${data.archive_file.webhook_source.output_md5}.zip"
  bucket = google_storage_bucket.cf_source.name
  source = data.archive_file.webhook_source.output_path
}

resource "google_cloudfunctions2_function" "webhook" {
  name     = "inbox-webhook"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "webhook"
    source {
      storage_source {
        bucket = google_storage_bucket.cf_source.name
        object = google_storage_bucket_object.webhook_source.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.webhook_cf.email
    min_instance_count    = 0
    max_instance_count    = 3
    timeout_seconds       = 30
    environment_variables = {
      GCP_PROJECT_ID       = var.project_id
      WEBHOOK_CLIENT_STATE = "inbox-webhook"
    }
  }

  depends_on = [google_project_service.apis]
}

# Allow unauthenticated invocations — Graph API posts without a bearer token
resource "google_cloudfunctions2_function_iam_member" "webhook_public" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.webhook.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"
}

# Gen2 CFs run on Cloud Run — also need the Cloud Run invoker for unauthenticated access
resource "google_cloud_run_v2_service_iam_member" "webhook_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.webhook.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Renewal function
# ---------------------------------------------------------------------------
data "archive_file" "renew_source" {
  type        = "zip"
  source_dir  = "${path.module}/../functions/renew"
  output_path = "${path.module}/.terraform/renew.zip"
}

resource "google_storage_bucket_object" "renew_source" {
  name   = "renew-${data.archive_file.renew_source.output_md5}.zip"
  bucket = google_storage_bucket.cf_source.name
  source = data.archive_file.renew_source.output_path
}

resource "google_cloudfunctions2_function" "renew" {
  name     = "inbox-renew"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "renew"
    source {
      storage_source {
        bucket = google_storage_bucket.cf_source.name
        object = google_storage_bucket_object.renew_source.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.renew_cf.email
    min_instance_count    = 0
    max_instance_count    = 1
    timeout_seconds       = 60
    environment_variables = {
      GCP_PROJECT_ID = var.project_id
      # Set GRAPH_SUBSCRIPTION_ID after registering the subscription:
      #   terraform apply -var="graph_subscription_id=<id>"
      GRAPH_SUBSCRIPTION_ID = var.graph_subscription_id
    }
    secret_environment_variables {
      key        = "CLIENT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-id"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-secret"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "TENANT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["tenant-id"].secret_id
      version    = "latest"
    }
  }

  depends_on = [google_project_service.apis]
}

output "webhook_url" {
  description = "Webhook Cloud Function URL — use this when registering the Graph subscription"
  value       = google_cloudfunctions2_function.webhook.service_config[0].uri
}

# ---------------------------------------------------------------------------
# Processor function
# ---------------------------------------------------------------------------
data "archive_file" "process_source" {
  type        = "zip"
  source_dir  = "${path.module}/.."
  output_path = "${path.module}/.terraform/process.zip"
  excludes = [
    "terraform/.terraform",
    "terraform/terraform.tfvars",
    "terraform/terraform.tfstate",
    "terraform/terraform.tfstate.backup",
    ".venv",
    ".git",
    ".claude",
    "docs",
    "Dockerfile.analyze-emails",
    "Dockerfile.inbox-worker",
    ".dockerignore",
    ".token_cache.json",
    ".env",
  ]
}

resource "google_storage_bucket_object" "process_source" {
  name   = "process-${data.archive_file.process_source.output_md5}.zip"
  bucket = google_storage_bucket.cf_source.name
  source = data.archive_file.process_source.output_path
}

resource "google_cloudfunctions2_function" "process" {
  name     = "inbox-process"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "process"
    source {
      storage_source {
        bucket = google_storage_bucket.cf_source.name
        object = google_storage_bucket_object.process_source.name
      }
    }
  }

  service_config {
    service_account_email          = google_service_account.process_cf.email
    min_instance_count             = 0
    max_instance_count             = 3
    timeout_seconds                = 300
    available_cpu                  = "1"
    available_memory               = "2Gi"
    annotations = {
      # Mounts the Cloud SQL Unix socket at /cloudsql/<connection_name>
      "run.googleapis.com/cloudsql-instances" = google_sql_database_instance.inbox.connection_name
    }
    environment_variables = {
      GCP_PROJECT_ID             = var.project_id
      CLOUD_SQL_CONNECTION_NAME  = google_sql_database_instance.inbox.connection_name
      POSTGRES_USER              = var.db_user
      POSTGRES_DB                = "app"
      MSAL_SECRET_NAME           = "msal-token-cache"
    }
    secret_environment_variables {
      key        = "POSTGRES_PASSWORD"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["inbox-db-password"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "CLIENT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-id"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "CLIENT_SECRET"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["client-secret"].secret_id
      version    = "latest"
    }
    secret_environment_variables {
      key        = "TENANT_ID"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["tenant-id"].secret_id
      version    = "latest"
    }
  }

  event_trigger {
    trigger_region = var.region
    event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic   = google_pubsub_topic.inbox_messages.id
    retry_policy   = "RETRY_POLICY_RETRY"
  }

  depends_on = [
    google_project_service.apis,
    google_sql_database_instance.inbox,
  ]
}
