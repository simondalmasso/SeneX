# SENEX GitHub Admin MCP

This is a separate, narrow administration service for **only**
`simondalmasso/SeneX`. It must not be mounted into the public H011 runtime.

## Security contract

- GitHub repository target is hard-coded to `simondalmasso/SeneX`.
- Branch target is hard-coded to `main`.
- The caller must present a **GitHub App user access token** (`ghu_`) obtained
  through GitHub OAuth/SSO.
- The authenticated GitHub login must be exactly `simondalmasso`.
- The GitHub App must be installed only on SENEX and request repository
  permission `Administration: write`.
- PATs and GitHub App installation tokens are rejected.
- The write tool requires the literal confirmation `PROTECT_SENEX_MAIN`.
- There is no generic repository, branch, or arbitrary GitHub API tool.

## Tools

- `verify_main_protection`: read-only status.
- `protect_main(confirm)`: writes the fixed SENEX policy and verifies its
  postcondition.

The fixed policy requires:

- pull request path before merge, with zero mandatory approving reviewers;
- `canonical-ci` required and strict/up-to-date;
- rules enforced for administrators;
- force-push disabled;
- branch deletion disabled.

The mirror workflow is intentionally **not** a required pre-merge check because
it executes after a GitHub push.

## Deployment

Deploy this as a **separate private/admin service**, using
`Dockerfile.admin-mcp`. Do not replace H011's production command and do not
mount this MCP on `backend.main_real:app`.

Set:

- `SENEX_ADMIN_MCP_BASE_URL=https://<admin-service-host>`
- `PORT=8080` (or the provider-assigned port)

The OAuth-capable caller/reverse proxy must obtain a GitHub App user access
token through GitHub SSO and forward it as `Authorization: Bearer <ghu_...>`.

The service does not store a GitHub admin credential.
