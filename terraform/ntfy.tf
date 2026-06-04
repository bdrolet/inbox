resource "google_compute_address" "ntfy" {
  name   = "ntfy"
  region = var.region

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "ntfy_http" {
  name    = "ntfy-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80"]
  }

  target_tags   = ["ntfy-server"]
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "ntfy_https" {
  name    = "ntfy-https"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  target_tags   = ["ntfy-server"]
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_instance" "ntfy" {
  name         = "ntfy"
  machine_type = "e2-micro"
  zone         = "${var.region}-a"
  tags         = ["ntfy-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.ntfy.address
    }
  }

  metadata_startup_script = <<-SCRIPT
    #!/bin/bash
    set -e
    apt-get update -q
    curl -fsSL https://packages.ntfy.sh/apt/gpg.key | gpg --dearmor -o /etc/apt/keyrings/ntfy.gpg
    echo "deb [signed-by=/etc/apt/keyrings/ntfy.gpg] https://packages.ntfy.sh/apt/stable debian main" \
      > /etc/apt/sources.list.d/ntfy.list
    apt-get update -q && apt-get install -y ntfy certbot

    mkdir -p /var/cache/ntfy

    cat > /etc/ntfy/server.yml <<EOF
    base-url: https://${var.ntfy_domain}
    listen-http: :80
    cache-file: /var/cache/ntfy/cache.db
    EOF

    # Run once after DNS points at this IP to obtain a cert and switch to HTTPS
    cat > /root/bootstrap-tls.sh <<'EOF'
    #!/bin/bash
    set -e
    DOMAIN=${var.ntfy_domain}
    systemctl stop ntfy
    certbot certonly --standalone --non-interactive --agree-tos -m admin@drolet.ai -d "$DOMAIN"
    cat > /etc/ntfy/server.yml <<CONF
    base-url: https://$DOMAIN
    listen-https: :443
    cert-file: /etc/letsencrypt/live/$DOMAIN/fullchain.pem
    key-file: /etc/letsencrypt/live/$DOMAIN/privkey.pem
    cache-file: /var/cache/ntfy/cache.db
    CONF
    systemctl start ntfy
    echo "0 3 * * * root certbot renew --quiet --deploy-hook 'systemctl reload ntfy'" >> /etc/crontab
    echo "TLS bootstrap complete — ntfy running at https://$DOMAIN"
    EOF
    chmod +x /root/bootstrap-tls.sh

    systemctl enable ntfy
    systemctl start ntfy
  SCRIPT

  depends_on = [google_project_service.apis]
}

output "ntfy_ip" {
  description = "Static IP of the ntfy VM — point ntfy.drolet.ai A record here"
  value       = google_compute_address.ntfy.address
}
