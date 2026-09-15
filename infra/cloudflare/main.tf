terraform {
  required_version = ">= 1.6, < 2.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }
}

# Reads CLOUDFLARE_API_TOKEN from the environment. Never put it in tfvars.
provider "cloudflare" {}

# Direct Upload only: GitHub Actions owns builds and deployment ordering.
# There are no Functions, bindings, storage products or paid subscriptions.
resource "cloudflare_pages_project" "atlas" {
  account_id        = var.account_id
  name              = var.project_name
  production_branch = "main"

  lifecycle {
    prevent_destroy = true
  }
}

resource "cloudflare_pages_domain" "atlas" {
  account_id   = var.account_id
  project_name = cloudflare_pages_project.atlas.name
  name         = var.domain
}

# Optional: leave zone_id null when DNS is hosted elsewhere. In that case,
# create the CNAME shown in the outputs at your current DNS provider.
resource "cloudflare_dns_record" "atlas" {
  count   = var.zone_id == null ? 0 : 1
  zone_id = var.zone_id
  name    = var.domain
  type    = "CNAME"
  content = cloudflare_pages_project.atlas.subdomain
  ttl     = 1
  proxied = false
}
