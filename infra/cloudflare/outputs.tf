output "pages_project" {
  value = cloudflare_pages_project.atlas.name
}

output "deploy_api_token" {
  description = "Optional generated token for the GitHub cloudflare-pages environment. Sensitive still means persisted in Terraform state."
  value       = var.manage_deploy_token ? cloudflare_account_token.pages_deploy_account_token[0].value : null
  sensitive   = true
}

output "public_url" {
  value = "https://${var.domain}"
}

output "dns_record" {
  value = {
    type   = "CNAME"
    name   = var.domain
    target = cloudflare_pages_project.atlas.subdomain
  }
}
