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
    # packages.ntfy.sh apt repo no longer exists — install from GitHub releases
    NTFY_VERSION=$(curl -s https://api.github.com/repos/binwiederhier/ntfy/releases/latest | grep '"tag_name"' | cut -d'"' -f4 | tr -d v)
    curl -sLo /tmp/ntfy.deb "https://github.com/binwiederhier/ntfy/releases/download/v$${NTFY_VERSION}/ntfy_$${NTFY_VERSION}_linux_amd64.deb"
    apt-get install -y certbot
    dpkg -i /tmp/ntfy.deb

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
# Grant ntfy user read access to cert files
chmod 0755 /etc/letsencrypt/live /etc/letsencrypt/archive
chmod 0644 /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/letsencrypt/archive/$DOMAIN/fullchain1.pem
chmod 0640 /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/letsencrypt/archive/$DOMAIN/privkey1.pem
chown root:ntfy /etc/letsencrypt/live/$DOMAIN/privkey.pem /etc/letsencrypt/archive/$DOMAIN/privkey1.pem
tee /etc/ntfy/server.yml > /dev/null <<CONF
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
