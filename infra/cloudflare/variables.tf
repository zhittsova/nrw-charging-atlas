variable "account_id" {
  description = "Cloudflare account ID (not an API token)."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "Use the 32-character Cloudflare account ID."
  }
}

variable "project_name" {
  description = "Must match the GitHub CLOUDFLARE_PAGES_PROJECT variable."
  type        = string
  default     = "nrw-ev-atlas"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,56}[a-z0-9]$", var.project_name))
    error_message = "Use a lowercase Pages project name with letters, numbers and hyphens."
  }
}

variable "domain" {
  description = "Public atlas subdomain; does not modify the existing blog."
  type        = string
  default     = "nrw-ev-atlas.zhittsova.com"
}

variable "zone_id" {
  description = "Optional Cloudflare DNS zone ID. Null means manage the CNAME at your existing DNS provider."
  type        = string
  default     = null
}

variable "manage_deploy_token" {
  description = "Opt in to Terraform owning the deployment token. Requires a separate token-management credential and stores the generated secret in state."
  type        = bool
  default     = false
}
