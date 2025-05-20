#!/usr/bin/env python3
import sys

from github_actions.common import GitHubContext

from src.actions.github_project_manager import GitHubProjectManager
from src.actions.github_project_manager import ProjectUpdateTenants
from src.input_output.input import TenantArtifactCIArgumentParser

if __name__ == "__main__":
    try:
        """Parse command-line arguments"""
        args = TenantArtifactCIArgumentParser().parse_args()

        """Retrieve GitHub Action environment variables"""
        github_context = GitHubContext.from_env()

        """Get Artifact version"""
        project_manager = GitHubProjectManager(args, github_context)
        project_manager.run()
        print(f"Artifact_version: {project_manager.version}")

        """Create Tag and Release"""
        if args.autotag or args.push_tag:
            project_manager.create_git_tag_and_release()

        """Update Artifact version in Tenants"""
        if github_context.ref_name == "production":
            project_update_tenants = ProjectUpdateTenants(args, github_context, project_manager.version)
            project_update_tenants.run()
        else:
            print("Skip tenant environments update.")

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
