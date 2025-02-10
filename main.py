#!/usr/bin/env python3

import os
import sys
import re
import requests
import subprocess
import argparse

from git import Repo, InvalidGitRepositoryError, GitCommandError
from github import Github, Repository, GithubException
from slack_sdk.webhook import WebhookClient

def notify_slack(slack_webhook: str, tenant_name: str, repository_name: str, tag_name: str, release_notes_path: str, details: str = "") -> None:
    github_server_url = "https://github.com"
    repository_url = f"{github_server_url}/{repository_name}"
    release_ulr = f"{repository_url}/tree/{tag_name}|{tag_name}"
    release_notes_url = f"{repository_url}/blob/{tag_name}/{release_notes_path}"

    details_text = f"*Details*: {details}" if details else ""

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

    webhook = WebhookClient(url=slack_webhook)
    response = webhook.send_dict(body=data)

    if response.status_code != 200:
        raise Exception(f"Error sending message: {response.status_code}, {response.body}")

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

    try:
        response = requests.get("https://edenlabllc-rmk.s3.eu-north-1.amazonaws.com/rmk/s3-installer")
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error downloading RMK installer file: {e}", file=sys.stderr)
        sys.exit(1)

    script_content = response.text

    try:
        subprocess.run(
            ["bash", "-s", "--", input_rmk_version],
            check=True,
            text=True,
            input=script_content)
    except subprocess.CalledProcessError as e:
        print(f"Error instaling RMK: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        rmk_version_output = subprocess.check_output(["rmk", "--version"], encoding='UTF-8').strip()
        print(rmk_version_output)
        m = re.search(r'^.*\s(.*)$', rmk_version_output)
        rmk_version = m.group(1) if m else rmk_version_output
    except subprocess.CalledProcessError as e:
        print(f"Error getting RMK version: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"RMK version {rmk_version}")

    try:
        subprocess.run(
            ["rmk", "config", "init", "--progress-bar=false"],
            check=True,
            text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error getting RMK config init: {e}", file=sys.stderr)
        sys.exit(1)

def rmk_release_list() -> None:
    try:
        subprocess.run(
            ["rmk", "release", "list", "--skip-context-switch"],
            check=True,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error getting RMK release list: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    class EnvDefault(argparse.Action):
        def __init__(self, envvar, required=True, default=None, **kwargs):
            if envvar:
                if envvar in os.environ:
                    default = os.environ.get(envvar, default)
            if required and default:
                required = False
            super(EnvDefault, self).__init__(default=default, required=required, **kwargs)

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)

    parser=argparse.ArgumentParser()

    parser.add_argument(
        "-ute" , "--update_tenant_environments", action=EnvDefault, required=False,
        envvar="INPUT_UPDATE_TENANT_ENVIRONMENTS", type=str, default='',
        help="List of tenants and environments for automatically updating the dependency version.")

    parser.add_argument(
        "-utwf" , "--update_tenant_workflow_file", action=EnvDefault, required=False,
        envvar="INPUT_UPDATE_TENANT_WORKFLOW_FILE", type=str, default='',
        help="Tenant workflow file with a 'on.workflow_dispatch' trigger (only if update_tenant_environments is specified).")

    parser.add_argument(
        "-at" , "--autotag", action=EnvDefault, required=False,
        envvar="INPUT_AUTOTAG", type=bool, default=False,
        help="Enable auto tagging when merging into target branch.")

    parser.add_argument(
        "-pt" , "--push_tag", action=EnvDefault, required=False,
        envvar="INPUT_PUSH_TAG", type=bool, default=False,
        help="Custom tenant name for different client repo.")

    parser.add_argument(
        "-sn" , "--slack_notifications", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_NOTIFICATIONS", type=bool, default=False,
        help="Enable Slack notifications.")

    parser.add_argument(
        "-sw" , "--slack_webhook", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_WEBHOOK", type=str, default='',
        help="URL for Slack webhook (required if --slack_notifications=true).")

    parser.add_argument(
        "-smrnp" , "--slack_message_release_notes_path", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_MESSAGE_RELEASE_NOTES_PATH", type=str, default='',
        help="Path relative to the root of the repository to a file with release notes (required if --slack_notifications=true).")

    parser.add_argument(
        "-smd" , "--slack_message_details", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_MESSAGE_DETAILS", type=str, default='',
        help="Additional information added to the body of the Slack message (only if --slack_notifications=true).")

    parser.add_argument(
        "-rv" , "--rmk_version", action=EnvDefault, required=False,
        envvar="INPUT_RMK_VERSION", type=str, default='latest',
        help="RMK version.")

    parser.add_argument(
        "-rrscs" , "--rmk_release_skip_context_switch", action=EnvDefault, required=False,
        envvar="INPUT_RMK_RELEASE_SKIP_CONTEXT_SWITCH", type=bool, default=True,
        help="Skip context switch for not provisioned cluster.")

    parser.add_argument(
        "-ctn" , "--custom_tenant_name", action=EnvDefault, required=False,
        envvar="INPUT_CUSTOM_TENANT_NAME", type=str, default='',
        help="Custom tenant name for different client repo.")

    parser.add_argument(
        "-av" , "--artifact_version", action=EnvDefault, required=False,
        envvar="INPUT_ARTIFACT_VERSION", type=str, default='',
        help="Artifact release version, mandatory in SemVer2 mode.")

    parser.add_argument(
        "-ght" , "--github_token", action=EnvDefault, required=True,
        envvar="INPUT_GITHUB_TOKEN_REPO_FULL_ACCESS", type=str,
        help="GitHub token with full access permissions to repositories.")

    parser.add_argument(
        "-grep" , "--github_repository", action=EnvDefault, required=True,
        envvar="GITHUB_REPOSITORY", type=str,
        help="The owner and repository name.")

    parser.add_argument(
        "-gref" , "--github_ref", action=EnvDefault, required=True,
        envvar="GITHUB_REF", type=str,
        help="The fully-formed ref of the branch or tag.")

    parser.add_argument(
        "-gsha" , "--github_sha", action=EnvDefault, required=True,
        envvar="GITHUB_SHA", type=str,
        help="The commit SHA that triggered the workflow.")

    args=parser.parse_args()

    rmk_github_token = args.github_token
    update_tenant_environments = set(args.update_tenant_environments.splitlines())
    update_tenant_workflow_file = args.update_tenant_workflow_file
    autotag = args.autotag
    push_tag = args.push_tag
    slack_notifications = args.slack_notifications
    slack_webhook = args.slack_webhook
    slack_message_release_notes_path = args.slack_message_release_notes_path
    slack_message_details = args.slack_message_details
    input_rmk_version = args.rmk_version
    rmk_release_skip_context_switch = args.rmk_release_skip_context_switch
    custom_tenant_name = args.custom_tenant_name
    artifact_version = args.artifact_version
    github_repository = args.github_repository
    github_ref = args.github_ref
    github_sha = args.github_sha

    os.environ['GITHUB_TOKEN'] = rmk_github_token
    os.environ['RMK_GITHUB_TOKEN'] = rmk_github_token
    os.environ['RMK_RELEASE_SKIP_CONTEXT_SWITCH'] = "true" if rmk_release_skip_context_switch else "false"

    github_org = github_repository.split("/")[0]
    git_branch = github_ref.removeprefix("refs/heads/")

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
        notify_slack(slack_webhook, tenant_name, github_repository, artifact_version, slack_message_release_notes_path, slack_message_details)
