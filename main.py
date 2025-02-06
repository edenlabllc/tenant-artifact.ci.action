#!/usr/bin/python3

import os
import sys
import re
import requests
import subprocess

from git import Repo, InvalidGitRepositoryError, GitCommandError
from github import Github, Repository, GithubException

def run_command(cmd: list | str, *, shell: bool = False) -> None:
    print("Running:", cmd)
    subprocess.run(cmd, check=True, shell=shell, text=True)

def notify_slack(slack_webhook: str, tenant_name: str, tag: str, release_notes_path: str, details: str) -> None:
    icon_emoji = ":package:"
    details_text = f"*Details*: {details}" if details else ""
    github_server_url = os.environ.get('GITHUB_SERVER_URL', "https://github.com")
    github_repository = os.environ.get('GITHUB_REPOSITORY')
    release_notes_url = f"{github_server_url}/{github_repository}/blob/{tag}/{release_notes_path}"

    message = (
        f"*Released a new version of {tenant_name}*: <{github_server_url}/{github_repository}/tree/{tag}|{tag}>\n"
        f"*Release notes*: {release_notes_url}\n"
        f"{details_text}"
    )

    data = {
        "username": "Tenant artifact action",
        "icon_emoji": icon_emoji,
        "text": message
    }
    try:
        response = requests.post(slack_webhook, json=data)
        response.raise_for_status()
    except Exception as e:
        print(f"Error sending notification in Slack: {e}", file=sys.stderr)
        sys.exit(1)

def push_tag(repo: Repo, artifact_version: str) -> None:
    try:
        print("Configure Git user.name and user.email.")
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "github-actions-42wms")
            cw.set_value("user", "email", "github-actions@github.com")
    except Exception as e:
        print(f"Error while setting up Git user: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"Add Git tag {artifact_version}.")
        repo.create_tag(artifact_version, message=f"Release {artifact_version}")
    except GitCommandError as e:
        print(f"Error creating tag {artifact_version}: {e}", file=sys.stderr)
        # sys.exit(1)

    try:
        origin = repo.remote(name="origin")
        origin.push(artifact_version)
        print(f"Tag {artifact_version} successfully sent to origin.")
    except GitCommandError as e:
        print(f"Error sending tag {artifact_version}: {e}", file=sys.stderr)
        sys.exit(1)

def push_release(gh_repo: Repository, artifact_version: str, github_sha: str) -> None:
    try:
        gh_repo.get_release(artifact_version)
        print(f"GitHub release {artifact_version} already exists.")
        print("Skipped.")
    except GithubException as e:
        if e.status == 404:
            print(f"Creating a GitHub release {artifact_version}.")
            try:
                release = gh_repo.create_git_release(
                    tag=artifact_version,
                    name=f"Artifact version - {artifact_version}",
                    message=f"All dependency versions for the artifact version: `{artifact_version}` are described in the asset file: `project.yaml`",
                    target_commitish=github_sha,
                    draft=False,
                    prerelease=False
                )
                asset_path = "project.yaml"
                if os.path.exists(asset_path):
                    release.upload_asset(asset_path)
                    print("The project.yaml file has been successfully uploaded to the release.")
                else:
                    print("File project.yaml not found, skipping asset loading.")
            except GithubException as ge:
                print(f"Error creating release on GitHub: {ge}")
                return
        else:
            print(f"Error checking for release existence: {e}")
            return

def check_pushes_suport(github_org: str, ref: str) -> None:
    if not github_org:
        print("GITHUB_REPOSITORY is not set or has an invalid format.", file=sys.stderr)
        sys.exit(1)

    if not re.match(r"refs/heads/(staging|production)", ref):
        print("Only pushes to staging and production branches are supported. Check the workflow's on.push.* section.", file=sys.stderr)
        sys.exit(1)

def get_artifact_version(repo: Repo, github_org: str, git_branch: str) -> str:
    commit = repo.head.commit
    commit_subject = commit.message.splitlines()[0]
    print(f"Git commit message: {commit_subject}")

    pattern = r"^Merge pull request #[0-9]+ from " + re.escape(github_org) + r"/release/(v[0-9]+\.[0-9]+\.[0-9]+(?:-rc)?)$"

    m = re.match(pattern, commit_subject)
    if not m:
        print(f"Pushes to {git_branch} should be done via merges of PR requests from release/vN.N.N or release/vN.N.N-rc branches only.", file=sys.stderr)
        print("The expected message format (will be used for parsing a release tag):", file=sys.stderr)
        print(f"Merge pull request #N from {github_org}/release/vN.N.N or {github_org}/release/vN.N.N-rc", file=sys.stderr)
        sys.exit(1)

    return m.group(1)

def update_tenant(tenants: set, workflow_file: str, artifact_version: str) -> None:
    if (len(tenants) == 0) or (os.environ.get("GITHUB_REF_NAME") != "production"):
        print("Skip tenant environments update.")
        return

    project_organization = os.environ.get('GITHUB_REPOSITORY').split("/")[0]
    project_dependency = os.environ.get('GITHUB_REPOSITORY').split("/")[1]

    tenant_repository_sufix = ".bootstrap.infra"

    gh_update = Github(os.environ.get('GITHUB_TOKEN'))

    for tenant_map in tenants:
        tenant_name = tenant_map.split("=")[0]
        tenant_environment = tenant_map.split("=")[1]
        tenant_repository = f"{project_organization}/{tenant_name}{tenant_repository_sufix}"

        try:
            gh_update_repo = gh_update.get_repo(tenant_repository)
        except GithubException as e:
            print(f"Error accessing repository {tenant_repository}: {e}", file=sys.stderr)
            continue

        payload = {
            "ref": tenant_environment,
            "inputs": {
                "project_dependency_name": project_dependency,
                "project_dependency_version": artifact_version,
            }
        }

        url = f"/repos/{tenant_repository}/actions/workflows/{workflow_file}/dispatches"
        try:
            gh_update_repo._requester.requestJsonAndCheck("POST", url, input=payload)
            print(f"Updated {project_dependency} dependency in the {tenant_environment} branch of {tenant_name}{tenant_repository_sufix} repository to version {artifact_version}.")
        except GithubException as ge:
            print(f"Error triggering workflow for repository {tenant_name}{tenant_repository_sufix}: {ge}", file=sys.stderr)

def rmk_install(input_rmk_version: str) -> None:
    print("Install RMK.")
    curl_cmd = (
        f'curl -sL "https://edenlabllc-rmk.s3.eu-north-1.amazonaws.com/rmk/s3-installer" '
        f'| bash -s -- "{input_rmk_version}"'
    )
    run_command(curl_cmd, shell=True)

    try:
        rmk_version_output = subprocess.check_output(["rmk", "--version"], text=True).strip()
        m = re.search(r'^.*\s(.*)$', rmk_version_output)
        rmk_version = m.group(1) if m else rmk_version_output
    except subprocess.CalledProcessError:
        print("Error getting RMK version.", file=sys.stderr)
        sys.exit(1)

    print(f"RMK version {rmk_version}")

    run_command(["rmk", "config", "init", "--progress-bar=false"])

def rmk_release_list() -> None:
    run_command(["rmk", "release", "list", "--skip-context-switch"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# read inputs
rmk_github_token = os.environ.get('INPUT_GITHUB_TOKEN_REPO_FULL_ACCESS', '')
os.environ['GITHUB_TOKEN'] = rmk_github_token
os.environ['RMK_GITHUB_TOKEN'] = rmk_github_token
os.environ['RMK_RELEASE_SKIP_CONTEXT_SWITCH'] = os.environ.get('INPUT_RMK_RELEASE_SKIP_CONTEXT_SWITCH' ,'true')

os.environ['AWS_REGION'] = os.environ.get('INPUT_CORE_AWS_REGION', '')
os.environ['AWS_ACCESS_KEY_ID'] = os.environ.get('INPUT_CORE_AWS_ACCESS_KEY_ID', '')
os.environ['AWS_SECRET_ACCESS_KEY'] = os.environ.get('INPUT_CORE_AWS_SECRET_KEY', '')

update_tenant_environments = set(os.environ.get('INPUT_UPDATE_TENANT_ENVIRONMENTS', '').splitlines())
update_tenant_workflow_file = os.environ.get('INPUT_UPDATE_TENANT_WORKFLOW_FILE')

autotag = os.environ.get('INPUT_AUTOTAG') == 'true'
push_tag = os.environ.get('INPUT_PUSH_TAG') == 'true'

slack_notifications = os.environ.get('INPUT_SLACK_NOTIFICATIONS') == 'true'
slack_webhook = os.environ.get('INPUT_SLACK_WEBHOOK')
slack_message_release_notes_path = os.environ.get('INPUT_SLACK_MESSAGE_RELEASE_NOTES_PATH')
slack_message_details = os.environ.get('INPUT_SLACK_MESSAGE_DETAILS')

input_rmk_version = os.environ.get('INPUT_RMK_VERSION', 'latest')
custom_tenant_name = os.environ.get('INPUT_CUSTOM_TENANT_NAME')
artifact_version = os.environ.get('INPUT_ARTIFACT_VERSION')

github_repository = os.environ.get('GITHUB_REPOSITORY')
github_ref = os.environ.get('GITHUB_REF')
github_sha = os.environ.get('GITHUB_SHA')

github_org = github_repository.split("/")[0]
git_branch = github_ref.removeprefix("refs/heads/")

# main
if not custom_tenant_name:
    tenant_name = github_repository.split("/")[1]
    tenant_name = tenant_name.split(".")[0]
else:
    tenant_name = custom_tenant_name

print(f"Tenant: {tenant_name}")

# Git Repo
try:
    repo = Repo(".")
except InvalidGitRepositoryError:
    print("The specified path is not a git repository.", file=sys.stderr)
    sys.exit(1)

# Github Repo
g = Github(os.environ.get('GITHUB_TOKEN'))
try:
    gh_repo = g.get_repo(os.environ.get('GITHUB_REPOSITORY'))
except GithubException as e:
    print(f"Error accessing GitHub repository: {e}", file=sys.stderr)
    sys.exit(1)

if autotag:
    check_pushes_suport(github_org, github_ref)
    artifact_version = get_artifact_version(repo, github_org, git_branch)

if not artifact_version:
    print("Failed to get artifact version from branch name or input parameter.", file=sys.stderr)
    sys.exit(1)

if autotag or push_tag:
    print(f"artifact_version: {artifact_version}")
    push_tag(repo, artifact_version)
    push_release(gh_repo, artifact_version, github_sha)

rmk_install(input_rmk_version)
rmk_release_list()

update_tenant(update_tenant_environments, update_tenant_workflow_file, artifact_version)

if slack_notifications:
    notify_slack(slack_webhook, tenant_name, artifact_version, slack_message_release_notes_path, slack_message_details)
