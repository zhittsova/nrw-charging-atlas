# Public hosting: GitHub Actions + Cloudflare Pages

Target: **https://nrw-ev-atlas.zhittsova.com**. The Cloudflare project defaults to
`nrw-ev-atlas`. The public build provides the map, district comparisons, source
dates and methods. Scenario editing remains in the local application.

GitHub Actions builds and deploys a static site. Cloudflare does not run ETL,
PostGIS, GeoNode, GeoServer, containers, Functions or a Worker backend. Terraform
creates the Pages project, its custom-domain association and optionally one
DNS record. Token ownership is a separate opt-in described below. It does not
subscribe the account to any paid product.

## What you need to do once

### 1. Cloudflare account and token

Create/use a Cloudflare account on the Free plan. Find its **Account ID** in the
dashboard. Create an API token with **Account → Cloudflare Pages → Edit**, scoped
to that account. This is the deployment token; no Workers, R2 or database
permissions are needed. Do not put a token in Git, a public environment variable,
Terraform variables or a chat message.

For this single application, manually creating an account-owned deployment token
is the simplest choice. Account-owned tokens support Pages and do not depend on
an individual user's continued account membership. Keep
`manage_deploy_token = false` (the default). Terraform still owns the Pages
project and custom domain.

If Terraform will also manage your existing Cloudflare DNS zone, use a separate
local provisioning token with Pages Edit plus **Zone → DNS → Edit**, scoped to
that zone. The Actions deployment token still needs only Pages Edit.

#### Optional: let Terraform own the deployment token

Set `manage_deploy_token = true` in your local `terraform.tfvars`. The permission
data source resolves the account-scoped **Pages Write** permission by name rather
than hard-coding its ID. This is the API name for the dashboard's **Cloudflare
Pages → Edit** permission. A separate Pages Read grant is unnecessary for the
project read performed by our deployment preflight. The token is scoped to the
specified account's Pages resources; its descriptive name does not restrict it
to this one project.

Authenticate Terraform using a separate provisioning token with **Account API
Tokens → Edit** (API name **Account API Tokens Write**) and **Cloudflare Pages →
Edit**, scoped to the account. Add zone DNS Edit only if `zone_id` is set. A
Pages-only deployment token cannot create tokens or run the permission lookup.
Keep the provisioning credential local; GitHub receives only the generated
Pages token. Future Terraform refreshes and applies with token ownership enabled
also need token-management access.

The generated token is exposed as the sensitive `deploy_api_token` output, but
**its value is still saved in Terraform state and state backups**. Sensitive
output masking is not encryption. Protect those files as credentials, or use an
encrypted state backend with restricted access. Gitignore alone is insufficient.
After applying, transfer it directly into the GitHub environment secret without
printing it (requires an authenticated GitHub CLI and the environment created
in step 4):

```sh
terraform -chdir=infra/cloudflare output -raw deploy_api_token |
  gh secret set CLOUDFLARE_API_TOKEN --env cloudflare-pages --repo zhittsova/nrw-charging-atlas
```

Creating the Cloudflare token does not automatically update GitHub. Repeat the
secret transfer after rotation. The resource has destruction protection, so
turning `manage_deploy_token` off after creation will fail instead of silently
revoking the deployment credential. Importing a manually created token does not recover its
original secret; retain that secret separately or intentionally create a new one.

### 2. Create the project with Terraform

Install [Terraform](https://developer.hashicorp.com/terraform/install), then from
the repository root:

```sh
cp infra/cloudflare/terraform.tfvars.example infra/cloudflare/terraform.tfvars
```

Edit that local file and insert the Account ID. Keep the project name and domain
defaults unless intentionally renaming both the infrastructure and Actions
configuration. `terraform.tfvars`, state files and plan files are gitignored.
Keep your Terraform state locally and back it up privately; it is how future
applies recognize this project. The provider dependency lockfile is committed.

Enter your token silently in your terminal, then initialize and review the plan:

```sh
read -rs CLOUDFLARE_API_TOKEN
export CLOUDFLARE_API_TOKEN
terraform -chdir=infra/cloudflare init
terraform -chdir=infra/cloudflare plan
terraform -chdir=infra/cloudflare apply
unset CLOUDFLARE_API_TOKEN
```

The expected plan creates `cloudflare_pages_project.atlas` and
`cloudflare_pages_domain.atlas`, plus a DNS record only if `zone_id` was set.
With `manage_deploy_token = true`, it also reads permission groups and creates
the deployment token.
The project has destruction protection. Do not use `terraform destroy` to reset
a deployment. Use Pages deployment rollback instead.

Create this as a **Direct Upload** project, without Cloudflare's Git integration:
Actions owns deployment sequencing. If a project named `nrw-ev-atlas` already
exists, import it into state before applying instead of creating a duplicate:

```sh
terraform -chdir=infra/cloudflare import cloudflare_pages_project.atlas ACCOUNT_ID/nrw-ev-atlas
```

Terraform is optional for the initial setup: the dashboard can create a Direct
Upload Pages project with production branch `main`, followed by adding the custom
domain. If using the dashboard, import existing resources before using Terraform
later. Do not run both provisioning paths independently.

### 3. Point the subdomain to Pages

You do not need to add a Cloudflare DNS zone for `nrw-ev-atlas.zhittsova.com`.
Keep your existing DNS provider unless you separately want to migrate the whole
domain's DNS. An apex Pages domain would require a Cloudflare zone; this subdomain
does not. The Terraform custom-domain resource registers the hostname with Pages
before you point DNS at it; a CNAME alone is not sufficient.

If DNS is outside Cloudflare, leave `zone_id = null`. At the existing DNS provider
add the record printed by `terraform output dns_record`, normally:

| Type | Name | Target |
| --- | --- | --- |
| CNAME | `nrw-ev-atlas` | `nrw-ev-atlas.pages.dev` |

Use the actual target output if Cloudflare assigned a different subdomain. Keep
the blog's existing apex, `www`, mail and verification records. You do not need
to transfer the domain or change nameservers for this subdomain.

If the zone already lives on Cloudflare, setting `zone_id` lets Terraform create
this one CNAME instead. Import any pre-existing matching record before applying.
Domain validation and the HTTPS certificate can remain pending until DNS has
propagated and the first deployment is available.

### 4. Add GitHub configuration

In this repository's **Settings → Secrets and variables → Actions → Variables**:

| Repository variable | Value |
| --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare Account ID; required to enable the workflow |
| `CLOUDFLARE_PAGES_PROJECT` | `nrw-ev-atlas` (the default if omitted) |
| `ENABLE_SCHEDULED_ETL` | Leave unset initially; `true` enables monthly data refresh |

Create an environment called **`cloudflare-pages`** under **Settings →
Environments**. Add its secret **`CLOUDFLARE_API_TOKEN`** with the Pages Edit token.
Restrict deployment branches to `main`. Required reviewers are optional; leave
them off if successful main updates should deploy automatically.

Before enabling ETL, open your GitHub billing settings and configure an Actions
budget with **Stop usage when budget limit is reached** to block paid overage.
Use a $0 paid-usage budget if supported by your account's billing interface.
Check existing account/repository budgets and cache limits as well. The new
workflow uses standard Linux runners and stores no Actions caches or artifacts;
your existing CI and other repositories have separate usage that still counts.

GitHub Pro includes 3,000 private-repository Actions minutes per month. Public
repositories have free standard runner execution. Existing Pro/domain fees are
unchanged. No configuration here guarantees future provider pricing or uptime.

### 5. Publish

Merge/push the reviewed configuration to `main`, then choose **Actions → Public
atlas → Run workflow** on `main`. For the first run, check **refresh_data**.
The first ETL can take tens of minutes. Check the published site at the project's
`pages.dev` address, then at `nrw-ev-atlas.zhittsova.com` once DNS/TLS is ready.
The workflow intentionally skips until the Account ID repository variable exists.

## Everyday operation

| Event | What happens |
| --- | --- |
| Frontend change pushed to `main` | Tests/build, fetch the last published export, validate, browser tests, deploy |
| SQL, scripts, source catalogue or ETL config changes on `main` | Rebuild the data before deployment |
| Manual run with `refresh_data=true` | Fetch current sources, rebuild PostGIS results, validate and deploy |
| Monthly schedule, explicitly enabled | Full refresh on the first day at 04:23 UTC |
| A branch/PR change | Existing CI checks; no public deployment or cloud token exposure |

For ordinary local changes, edit/test locally and push through the normal PR
workflow. After the merge to `main`, Actions handles the update. No always-on
machine is required. There is no parallel Cloudflare Git build to race the ETL.

A preflight verifies the token, project identity, production branch and absence of
backend bindings before ETL starts. The export is recovered from the actual
`pages.dev` origin returned by Cloudflare, independent
of your custom DNS. If it is absent, corrupt, or uses a different formula version,
the workflow performs ETL. This also bootstraps a new project. Raw source data is
not committed. The published snapshot is the durable copy used between refreshes;
download its files if you want an independent offline backup. Pages retains prior
deployments for rollback; this is not a substitute for a data archive.

Scheduled Actions can be delayed, and public-repository schedules can be disabled
after 60 days without repository activity. Check the visible data-export date and
run manually when needed. A schedule is a refresh convenience, not a freshness SLA.

## What gets tested before upload

- Frontend unit tests and the production public build.
- On ETL runs: raw input validation and canonical SQL verification.
- The five-layer export, 53 districts, expected formula version, feature counts,
  provenance, and SHA-256 file hashes.
- Only named public outputs, attribution and source catalogues are copied.
- Every upload stays below 25 MiB per file and 20,000 files. A larger new source
  fails the build; it does not switch to paid storage automatically.
- Server Functions, Worker output, private files and unexpected Wrangler config
  are rejected. Public HTML cannot contain `localhost` links.
- Desktop/mobile browser tests check rankings, district selection, missing-data
  behavior and that no backend requests or writes are made.
- A superseded `main` revision cannot deploy over newer code.

The existing deployment remains available if ETL, a check, or an upload fails.
If a newer `main` revision superseded an in-flight manual refresh and no new
deployment was queued, rerun **Public atlas** on current `main`.

## Local verification and optional first upload

Use an existing canonical runtime export:

```sh
cd frontend
npm ci
npm test
npm run build:public
cd ..
python3 -m scripts.public_site prepare
cd frontend
npx playwright install chromium
npm run test:public
```

To generate a new export with the lightweight stack (no GeoNode/GeoServer), from
the repository root:

```sh
docker compose -p nrw-public-etl-local -f config/compose.public-etl.yml run --build --rm etl
docker compose -p nrw-public-etl-local -f config/compose.public-etl.yml down --volumes
```

This example refreshes `data/raw` and `data/runtime`. To preserve the local data
cache/export, set `NRW_PUBLIC_RAW_DIR` and `NRW_PUBLIC_RUNTIME_DIR` to absolute
scratch directories first. Only the dedicated `nrw-public-etl-local` database
volume is disposable. Never apply its cleanup command to the local GeoNode stack.

If you want to bootstrap from your already verified local export instead of
waiting for a first hosted ETL, run the build/package/browser checks above, then
from `frontend` with the account ID and Pages token exported in the terminal:

```sh
npx wrangler pages deploy dist --project-name nrw-ev-atlas --branch main
```

Do this only with the intended production revision; local upload bypasses the
Actions `main` revision check. Normal updates should use Actions.

## Provider documentation

- [Account-owned API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/)
- [Terraform permission lookup](https://registry.terraform.io/providers/cloudflare/cloudflare/latest/docs/data-sources/account_api_token_permission_groups)
- [Token creation permissions](https://developers.cloudflare.com/api/resources/accounts/subresources/tokens/methods/create/)
- [Terraform sensitive state](https://developer.hashicorp.com/terraform/language/manage-sensitive-data)
- [Pages deployment from CI](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)
- [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
- [Static asset pricing](https://developers.cloudflare.com/pages/functions/pricing/)
- [Custom subdomains](https://developers.cloudflare.com/pages/configuration/custom-domains/)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub budget controls](https://docs.github.com/en/billing/concepts/budgets-and-alerts)
