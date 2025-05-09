# Tenant Artifact CI Action

[![Release](https://img.shields.io/github/v/release/edenlabllc/tenant.artifact.ci.action.svg?style=for-the-badge)](https://github.com/edenlabllc/tenant.artifact.ci.action/releases/latest)
[![Software License](https://img.shields.io/github/license/edenlabllc/tenant.artifact.ci.action.svg?style=for-the-badge)](LICENSE)
[![Powered By: Edenlab](https://img.shields.io/badge/powered%20by-edenlab-8A2BE2.svg?style=for-the-badge)](https://edenlab.io)

Reusable GitHub Action for auto-tagging, releasing artifacts, and updating dependencies in tenant bootstrap repositories.

## What it does

This action automates tagging, GitHub release creation, and optional dependency updates for multi-tenant infrastructures.  
It supports SemVer-based artifact versioning, RMK integration, Slack notifications, and GitHub-native workflows.

**Key features:**

- Auto-tag on merge of `release/v*` pull requests
- Push annotated tags and create GitHub releases
- Attach `project.yaml` to the release (if exists)
- Trigger `workflow_dispatch` in tenant bootstrap repos with updated versions
- Optional Slack notifications with release context
- RMK version check and installation
- GitHub-native, lightweight, and composable

## When to use

Use this action inside a tenant's artifact repository to:

- Automatically tag and release a new version
- Propagate updates to tenant environments via RMK
- Notify Slack channels on new releases

## Example

```yaml
name: Tenant artifact release

on:
  push:
    branches:
      - staging
      - production

jobs:
  release:
    runs-on: ubuntu-22.04
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Tenant Artifact CI Action
        uses: edenlabllc/tenant.artifact.ci.action@v1
        with:
          github_token_repo_full_access: ${{ secrets.GH_TOKEN_REPO_FULL_ACCESS }}
          autotag: true
          push_tag: true
          rmk_version: v0.45.0
          update_tenant_environments: |
            kodjin=staging
            kodjin=production
          update_tenant_workflow_file: project-update.yaml
          slack_notifications: true
          slack_webhook: ${{ secrets.SLACK_WEBHOOK }}
          slack_message_release_notes_path: docs/release-notes.md
          slack_message_details: |
            Triggered by: ${{ github.actor }}
```

See [`examples/`](./examples) for more templates.

## Required secrets

| Name                         | Purpose                                   |
|------------------------------|-------------------------------------------|
| `GH_TOKEN_REPO_FULL_ACCESS`  | GitHub PAT with access to private repos   |
| `SLACK_WEBHOOK`              | (Optional) Slack Incoming Webhook URL     |

## Inputs

See the [`action.yml`](./action.yml)'s `inputs` section for more details.

## Outputs

This action does not export outputs. All versioning is handled via GitHub tags and releases.

## Slack notifications

Enable by setting:

```yaml
with:
  slack_notifications: true
  slack_webhook: ${{ secrets.SLACK_WEBHOOK }}
  slack_message_release_notes_path: docs/release-notes.md
  slack_message_details: |
    Triggered by: ${{ github.actor }}
```

## Internals

- [`action.yml`](./action.yml) — defines action inputs  
- [`main.py`](./main.py) — executes logic  
- [`examples/`](./examples) — ready-to-use templates
