import os
import re

from argparse import Namespace

from git import Repo, GitCommandError
from github import Github, GithubException
from github.Repository import Repository
from github_actions.common import GitHubContext

from slack_sdk.webhook import WebhookClient


class GitHubProjectManager:
    def __init__(self, args: Namespace, github_context: GitHubContext):
        self.args = args
        self.github_context = github_context
        self.github = Github(args.github_token)
        self.gh_repo: Repository = self.github.get_repo(self.github_context.repository)
        self.git_branch = github_context.ref.replace("refs/heads/", "") if github_context.ref.startswith(
            "refs/heads/") else None
        self.github_sha = github_context.sha
        self.repo = Repo(".")
        self.version_prev = None
        self.version = None

        if self.git_branch == (self.args.major_version_branch or "").strip():
            self.VERSION_REGEX = fr"v\d+\.\d+\.\d+-{self.args.major_version_branch}$"
        else:
            self.VERSION_REGEX = r"v\d+\.\d+\.\d+(?:-rc)?$"

        ''' Validate major version branch format if provided '''
        if (self.args.major_version_branch or "").strip():
            if not re.match(r"^[a-zA-Z0-9_-]+-v\d+$", self.args.major_version_branch):
                raise ValueError(
                    f"invalid major_version_branch format: '{self.args.major_version_branch}'. "
                    f"Expected format is '<name>-v<major>'."
                )

    def run(self):
        self.version_prev = self._get_latest_valid_version_tag()

        if not self.version_prev:
            raise ValueError("at least one version is required in the repository. "
                             "Tag a Git commit of the master branch manually before running the workflow.")

        if not self.git_branch:
            raise ValueError("only pushes to branches are supported. Check the workflow's on.push.* section.")

        self._handle_master_branch()

    def create_git_tag_and_release(self):
        """
        Creates a Git tag and GitHub release for the current version.
        """
        if (self.args.artifact_version or "").strip():
            print(f"Release service for {self.github_context.ref_name} branch.")
        else:
            print("Release service (only for staging, production or major version branch).")

        if not ((self.git_branch in ["staging", "production", self.args.major_version_branch]) or (self.args.artifact_version or "").strip()):
            print("Skipped: neither on staging, production nor major version branch.")
            return

        release_msg = f"Release {self.version}"

        try:
            print("Configure Git user.name and user.email.")
            self.repo.git.config("user.name", "github-actions")
            self.repo.git.config("user.email", "github-actions@github.com")

            if self.args.autotag or self.args.push_tag:
                print(f"Add Git tag {self.version}")
                self.repo.create_tag(self.version, message=release_msg)
                self.repo.remotes.origin.push(self.version, force=True)
            else:
                print("Skip add Git tag")

        except GitCommandError as err:
            if "already exists" in str(err):
                print(f"Tag {self.version} already exists. Skipping tag creation.")
            else:
                raise RuntimeError(f"failed to create or push Git tag: {err}")

        # Check and create GitHub release
        try:
            self.gh_repo.get_release(self.version)
            print(f"GitHub release {self.version} already exists.")
            print("Skipped.")
        except GithubException as err:
            if err.status == 404:
                print(f"Creating GitHub release {self.version}")
                release = self.gh_repo.create_git_release(
                    tag=self.version,
                    name=f"Artifact version - {self.version}",
                    message=f"All dependency versions for the artifact version: `{self.version}` are described in the asset file: `project.yaml`",
                    draft=False,
                    prerelease=False,
                    target_commitish=self.github_sha
                )
                asset_path = "project.yaml"
                if os.path.exists(asset_path):
                    release.upload_asset(asset_path)
                    print("The project.yaml file has been successfully uploaded to the release.")
                else:
                    print("File project.yaml not found, skipping asset loading.")
            else:
                raise RuntimeError(f"failed to check or create GitHub release: {err}")

        self._handle_notify_slack()

    def _get_latest_valid_version_tag(self):
        tags = sorted(self.repo.tags, key=lambda t: t.commit.committed_date)
        valid_tags = [tag.name for tag in tags if re.fullmatch(self.VERSION_REGEX, tag.name)]
        return valid_tags[-1] if valid_tags else None

    def _handle_master_branch(self):
        if self.args.artifact_version:
            self.version = self.args.artifact_version
            return

        # Print and extract the first line of the latest Git commit message
        print("Git commit message:")
        commit_message = self.repo.head.commit.message.strip()
        first_line = commit_message.splitlines()[0]
        print(first_line)

        # Compose a full pattern to match PR merge commit messages
        pattern = rf"^Merge pull request #[0-9]+ from {re.escape(self.github_context.repository_owner)}/(release|hotfix)/({self.VERSION_REGEX})$"
        match = re.match(pattern, first_line)

        # If the commit message doesn't match an expected format — raise a clear error
        if not match:
            err_message = (f"Invalid commit message for branch '{self.git_branch}'.\n"
                           f"Expected formats:\n"
                           f"- release|hotfix/vX.Y.Z|vX.Y.Z-rc")
            if (self.args.major_version_branch or "").strip():
                raise ValueError(err_message +
                                 f"\n- {self.args.major_version_branch}: release|hotfix/vX.Y.Z-{self.args.major_version_branch}"
                                 )
            else:
                raise ValueError(err_message)

        self.version = match.group(2)

        print("Check GitHub release does not exist.")
        if self._github_release_exists(self.version):
            raise ValueError(f"GitHub release {self.version} already exists. "
                             f"Increase the version following SemVer and create a new release.")

    def _github_release_exists(self, version: str) -> bool:
        try:
            self.gh_repo.get_release(version)
            return True
        except GithubException as err:
            if err.status == 404:
                return False
            raise ValueError(f"checking release existence: {err}")

    def _handle_notify_slack(self):
        github_server_url = "https://github.com"
        repository_url = f"{github_server_url}/{self.github_context.repository}"
        release_ulr = f"{repository_url}/tree/{self.version}|{self.version}"
        release_notes_url = f"{repository_url}/blob/{self.version}/{self.args.slack_message_release_notes_path}"
        tenant_name = self.github_context.get_repository_name().split(".")[0]

        details_text = f"*Details*: {self.args.slack_message_details}" if self.args.slack_message_details else ""

        message = (
            f"*Released a new version of {tenant_name}*: <{release_ulr}>\n"
            f"*Release notes*: {release_notes_url}\n"
            f"{details_text}"
        )

        data = {
            "username": "Tenant artifact action",
            "icon_emoji": ":package:",
            "text": message
        }

        webhook = WebhookClient(url=self.args.slack_webhook)
        response = webhook.send_dict(body=data)

        if response.status_code != 200:
            raise Exception(f"Error sending message: {response.status_code}, {response.body}")


class ProjectUpdateTenants:
    def __init__(self, args: Namespace, github_context: GitHubContext, version: str):
        self.args = args
        self.github_context = github_context
        self.github = Github(args.github_token)
        self.version = version
        self.project = github_context.get_repository_name()
        self.workflow_file = args.update_tenant_workflow_file if args.update_tenant_workflow_file else "project-update.yaml"
        self.tenant_environments = set(args.update_tenant_environments.splitlines())

    def run(self):
        if len(self.tenant_environments) == 0:
            print("Skip tenant environments update.")
            return

        for mapping in self.tenant_environments:
            if "=" not in mapping:
                continue  # malformed line
            tenant, environments = mapping.split("=")
            for env in environments.split(","):
                if env.strip():
                    self._notify_tenant(tenant.strip(), env)

    def _notify_tenant(self, tenant: str, environment: str):
        tenant_repo_name = f"{self.github_context.repository_owner}/{tenant}.bootstrap.infra"
        print(f"Notifying tenant '{tenant}'")
        print(f"Repository: {tenant_repo_name}")
        print(f"Environment: {environment}")
        print(f"Workflow: {self.workflow_file}")
        print(f"Project: {self.project}")
        print(f"Version: {self.version}")

        payload = {
            "ref": environment,
            "inputs": {
                "rmk_project_dependency_name": self.project,
                "rmk_project_dependency_version": self.version
            }
        }

        try:
            gh_repo = self.github.get_repo(tenant_repo_name)
            url = f"/repos/{tenant_repo_name}/actions/workflows/{self.workflow_file}/dispatches"
            # PyGithub doesn't expose workflow_dispatch, so we use internal requester:
            gh_repo._requester.requestJsonAndCheck("POST", url, input=payload)
            print(f"Workflow dispatched for tenant '{tenant}'")
        except GithubException as err:
            raise ValueError(f"failed to notify tenant '{tenant}': {err}")
