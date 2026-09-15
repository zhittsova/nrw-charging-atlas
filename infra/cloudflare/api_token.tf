# Optional token ownership. Authenticate the provider with a separate
# provisioning credential that has Account API Tokens Write, not this token.
data "cloudflare_account_api_token_permission_groups" "pages" {
  count      = var.manage_deploy_token ? 1 : 0
  account_id = var.account_id
  scope      = "com.cloudflare.api.account"
}

locals {
  account_permission_ids = var.manage_deploy_token ? {
    for permission in data.cloudflare_account_api_token_permission_groups.pages[0].permission_groups :
    permission.name => permission.id
    if contains(permission.scopes, "com.cloudflare.api.account")
  } : {}
}

resource "cloudflare_account_token" "pages_deploy_account_token" {
  count      = var.manage_deploy_token ? 1 : 0
  account_id = var.account_id
  name       = "pages-deploy-nrw-ev-charging-atlas"

  policies = [{
    effect = "allow"
    permission_groups = [{
      # API name for the dashboard's Cloudflare Pages Edit permission.
      # Write also authorizes the project reads used by the deployment preflight.
      id = local.account_permission_ids["Pages Write"]
    }]
    resources = jsonencode({
      "com.cloudflare.api.account.${var.account_id}" = "*"
    })
  }]

  lifecycle {
    prevent_destroy = true
  }
}
