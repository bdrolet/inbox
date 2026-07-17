# The Cloud SQL instance is owned by the platform state (~/src/infra) —
# referenced read-only here. This repo owns only its own database + user on it.
data "google_sql_database_instance" "inbox" {
  name = "inbox"
}

resource "google_sql_database" "app" {
  instance = data.google_sql_database_instance.inbox.name
  name     = "app"
}

resource "google_sql_user" "inbox" {
  instance = data.google_sql_database_instance.inbox.name
  name     = var.db_user
  password = var.db_password
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL instance connection name — set as CLOUD_SQL_CONNECTION_NAME in the processor CF"
  value       = data.google_sql_database_instance.inbox.connection_name
}
