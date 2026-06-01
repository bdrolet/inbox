resource "google_sql_database_instance" "inbox" {
  name             = "inbox"
  database_version = "POSTGRES_16"
  region           = var.region

  deletion_protection = true

  settings {
    tier = "db-f1-micro"

    disk_size = 10
    disk_type = "PD_SSD"

    backup_configuration {
      enabled    = true
      start_time = "03:00"
    }

    ip_configuration {
      ipv4_enabled = true
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "app" {
  instance = google_sql_database_instance.inbox.name
  name     = "app"
}

resource "google_sql_user" "inbox" {
  instance = google_sql_database_instance.inbox.name
  name     = var.db_user
  password = var.db_password
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL instance connection name — set as CLOUD_SQL_CONNECTION_NAME in the processor CF"
  value       = google_sql_database_instance.inbox.connection_name
}
