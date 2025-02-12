#!/usr/bin/env python3

import os
import sys
import re
import requests
import subprocess
import argparse

from argparse import Namespace
from git import Repo, InvalidGitRepositoryError, GitCommandError
from github import Github, Repository, GithubException
from packaging import version
from slack_sdk.webhook import WebhookClient

def get_required_env(env_name: str) -> str:
    env_value = os.environ.get(env_name, '')
    if not env_value:
        raise Exception(f"{env_name} is not set or has an invalid format.")
    return env_value

def notify_slack(slack_webhook: str, tenant_name: str, repository_name: str, tag_name: str, release_notes_path: str, details: str = ""):
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

def git_push_tag(artifact_version: str):
    try:
        git_repo = Repo(".")
    except InvalidGitRepositoryError:
        raise Exception("The specified path is not a git repository.")

    try:
        print("Configure Git user.name and user.email.")
        with git_repo.config_writer() as cw:
            cw.set_value("user", "name", "github-actions")
            cw.set_value("user", "email", "github-actions@github.com")
    except Exception as err:
        raise Exception(f"Error while setting up Git user: {err}")

    try:
        print(f"Add Git tag {artifact_version}.")
        git_repo.create_tag(artifact_version, message=f"Release {artifact_version}")
    except GitCommandError as err:
        raise Exception(f"Error creating tag {artifact_version}: {err}")

    try:
        origin = git_repo.remote(name="origin")
        origin.push(artifact_version)
        print(f"Tag {artifact_version} successfully sent to origin.")
    except GitCommandError as err:
        raise Exception(f"Error sending tag {artifact_version}: {err}")

    git_repo.close()

def github_push_release(artifact_version: str, github_sha: str, github_repository:str, github_token:str):
    gh = Github(github_token)
    try:
        gh_repo = gh.get_repo(github_repository)
    except GithubException as err:
        if err.status == 401:
            raise Exception(f"Error accessing GitHub repository.\n"+
                            f"{err.message}")
        elif err.status == 404:
            raise Exception(f"Error accessing GitHub repository.\n"+
                            f"Repository {github_repository} Not Found.")
        else:
            raise Exception(f"Error accessing GitHub repository: {err}")

    try:
        gh_repo.get_release(artifact_version)
        print(f"GitHub release {artifact_version} already exists.")
        print("Skipped.")
    except GithubException as err:
        if err.status == 404:
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
            except GithubException as gh_err:
                raise Exception(f"Error creating release on GitHub: {gh_err}")
        else:
            raise Exception(f"Error checking for release existence: {err}")

    gh.close()

def check_pushes_suport(github_org: str, ref: str) -> bool:
    if not github_org:
        print("GITHUB_REPOSITORY is not set or has an invalid format.", file=sys.stderr)
        return False
    elif not re.match(r"refs/heads/(staging|production)", ref):
        print("Only pushes to staging and production branches are supported. Check the workflow's on.push.* section.", file=sys.stderr)
        return False
    else:
        return True

def get_artifact_version(github_org: str, github_branch: str):
    try:
        git_repo = Repo(".")
    except InvalidGitRepositoryError:
        raise Exception("The specified path is not a git repository.")

    commit = git_repo.head.commit
    commit_subject = commit.message.splitlines()[0]
    print(f"Git commit message: {commit_subject}")

    pattern = r"^Merge pull request #[0-9]+ from " + re.escape(github_org) + r"/release/(v[0-9]+\.[0-9]+\.[0-9]+(?:-rc)?)$"

    regexp_match = re.match(pattern, commit_subject)
    if not regexp_match:
        print(
            f"Pushes to {github_branch} should be done via merges of PR requests from release/vN.N.N or release/vN.N.N-rc branches only.\n"+
            f"The expected message format (will be used for parsing a release tag):\n"+
            f"Merge pull request #N from {github_org}/release/vN.N.N or {github_org}/release/vN.N.N-rc",
            file=sys.stderr)
        return ""

    git_repo.close()
    return regexp_match.group(1)

def update_tenant(tenants: set, workflow_file: str, artifact_version: str, github_org:str, github_repository_name:str, github_token:str):
    if len(tenants) == 0:
        print("Skip tenant environments update.")
        return

    project_organization = github_org
    project_dependency = github_repository_name

    tenant_repository_sufix = ".bootstrap.infra"

    gh = Github(github_token)

    for tenant_and_environment in tenants:
        if len(tenant_and_environment.split("=")) != 2:
            print(f"Item '{tenant_and_environment}' of the tenants and environments list is not in the correct format.\n"+
                  "Example: 'tenant=env'\n"+
                  "Skip this list item.")
            continue

        tenant_name = str(tenant_and_environment.split("=")[0]).strip()
        tenant_environment = str(tenant_and_environment.split("=")[1]).strip()

        if not (tenant_name and tenant_environment):
            print(f"Item '{tenant_and_environment}' of the tenants and environments list is not in the correct format.\n"+
                  "Skip this list item.")
            continue

        tenant_repository = f"{project_organization}/{tenant_name}{tenant_repository_sufix}"

        try:
            gh_repo = gh.get_repo(tenant_repository)
        except GithubException as gh_err:
            if err.status == 401:
                raise Exception(f"Error accessing GitHub repository {tenant_repository}.\n"+
                                f"{err.message}")
            elif err.status == 404:
                raise Exception(f"Error accessing GitHub repository {tenant_repository}.\n"+
                                f"Repository {github_org}/{github_repository_name} Not Found.")
            else:
                raise Exception(f"Error accessing repository {tenant_repository}: {gh_err}")

        payload = {
            "ref": tenant_environment,
            "inputs": {
                "project_dependency_name": project_dependency,
                "project_dependency_version": artifact_version,
            }
        }

        url = f"/repos/{tenant_repository}/actions/workflows/{workflow_file}/dispatches"
        try:
            gh_repo._requester.requestJsonAndCheck("POST", url, input=payload)
            print(f"Updated {project_dependency} dependency in the {tenant_environment} branch of {tenant_name}{tenant_repository_sufix} repository to version {artifact_version}.")
        except GithubException as gh_err:
            raise Exception(f"Error triggering workflow for repository {tenant_name}{tenant_repository_sufix}: {gh_err}")

    gh.close()

def rmk_install(input_rmk_version: str):
    print("Install RMK.")

    try:
        response = requests.get("https://edenlabllc-rmk.s3.eu-north-1.amazonaws.com/rmk/s3-installer")
        response.raise_for_status()
    except requests.RequestException as err:
        raise Exception(f"Error downloading RMK installer file: {err}")

    script_content = response.text

    try:
        subprocess.run(
            ["bash", "-s", "--", input_rmk_version],
            check=True,
            text=True,
            input=script_content)
    except subprocess.CalledProcessError as err:
        raise Exception(f"Error instaling RMK: {err}")

    try:
        rmk_version_output = subprocess.check_output(["rmk", "--version"], encoding='UTF-8').strip()
        print(rmk_version_output)
        reg_match = re.search(r'^.*\s(.*)$', rmk_version_output)
        rmk_version = reg_match.group(1) if reg_match else rmk_version_output
    except subprocess.CalledProcessError as err:
        raise Exception(f"Error getting RMK version: {err}")

    print(f"RMK version {rmk_version}")

    try:
        subprocess.run(
            ["rmk", "config", "init", "--progress-bar=false"],
            check=True,
            text=True)
    except subprocess.CalledProcessError as err:
        raise Exception(f"Error getting RMK config init: {err}")

def get_parser_namespace() -> Namespace:
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
        "--update_tenant_environments", action=EnvDefault, required=False,
        envvar="INPUT_UPDATE_TENANT_ENVIRONMENTS", type=str, default='',
        help="List of tenants and environments for automatically updating the dependency version.")

    parser.add_argument(
        "--update_tenant_workflow_file", action=EnvDefault, required=False,
        envvar="INPUT_UPDATE_TENANT_WORKFLOW_FILE", type=str, default='',
        help="Tenant workflow file with a 'on.workflow_dispatch' trigger (only if update_tenant_environments is specified).")

    parser.add_argument(
        "--autotag", action=EnvDefault, required=False,
        envvar="INPUT_AUTOTAG", type=bool, default=False,
        help="Enable auto tagging when merging into target branch.")

    parser.add_argument(
        "--push_tag", action=EnvDefault, required=False,
        envvar="INPUT_PUSH_TAG", type=bool, default=False,
        help="Custom tenant name for different client repo.")

    parser.add_argument(
        "--slack_notifications", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_NOTIFICATIONS", type=bool, default=False,
        help="Enable Slack notifications.")

    parser.add_argument(
        "--slack_webhook", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_WEBHOOK", type=str, default='',
        help="URL for Slack webhook (required if --slack_notifications=true).")

    parser.add_argument(
        "--slack_message_release_notes_path", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_MESSAGE_RELEASE_NOTES_PATH", type=str, default='',
        help="Path relative to the root of the repository to a file with release notes (required if --slack_notifications=true).")

    parser.add_argument(
        "--slack_message_details", action=EnvDefault, required=False,
        envvar="INPUT_SLACK_MESSAGE_DETAILS", type=str, default='',
        help="Additional information added to the body of the Slack message (only if --slack_notifications=true).")

    parser.add_argument(
        "--rmk_version", action=EnvDefault, required=False,
        envvar="INPUT_RMK_VERSION", type=str, default='latest',
        help="RMK version.")

    parser.add_argument(
        "--custom_tenant_name", action=EnvDefault, required=False,
        envvar="INPUT_CUSTOM_TENANT_NAME", type=str, default='',
        help="Custom tenant name for different client repo.")

    parser.add_argument(
        "--artifact_version", action=EnvDefault, required=False,
        envvar="INPUT_ARTIFACT_VERSION", type=str, default='',
        help="Artifact release version, mandatory in SemVer2 mode.")

    parser.add_argument(
        "--github_token", action=EnvDefault, required=True,
        envvar="INPUT_GITHUB_TOKEN_REPO_FULL_ACCESS", type=str,
        help="GitHub token with full access permissions to repositories.")

    return parser.parse_args()

if __name__ == "__main__":
    try:
        args=get_parser_namespace()

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
        custom_tenant_name = args.custom_tenant_name
        artifact_version = args.artifact_version

        github_repository = get_required_env('GITHUB_REPOSITORY')
        github_org = get_required_env('GITHUB_REPOSITORY_OWNER')
        github_repository_name = github_repository.removeprefix(f"{github_org}/")
        github_ref = get_required_env('GITHUB_REF')
        github_branch = get_required_env('GITHUB_REF_NAME')
        github_sha = get_required_env('GITHUB_SHA')

        tenant_name = custom_tenant_name if custom_tenant_name else github_repository_name.split(".")[0]
        print(f"Tenant: {tenant_name}")

        if input_rmk_version != "latest":
            if version.parse('v0.45.0-rc')>version.parse(input_rmk_version):
                raise Exception(f"Version {input_rmk_version} of RMK is not correct.\n"+
                                "The version for RMK must be at least v0.45.0.")

        os.environ['GITHUB_TOKEN'] = rmk_github_token
        os.environ['RMK_GITHUB_TOKEN'] = rmk_github_token

        if autotag:
            artifact_version = get_artifact_version(github_org, github_branch)

        if not artifact_version:
            raise Exception("Failed to get artifact version from branch name or input parameter.")

        if autotag or push_tag:
            print(f"artifact_version: {artifact_version}")
            git_push_tag(artifact_version)
            github_push_release(artifact_version, github_sha, github_repository, rmk_github_token)

        rmk_install(input_rmk_version)

        if github_branch == "production":
            update_tenant(update_tenant_environments, update_tenant_workflow_file, artifact_version, github_org, github_repository_name, rmk_github_token)
        else:
            print("Skip tenant environments update.")

        if slack_notifications:
            notify_slack(slack_webhook, tenant_name, github_repository, artifact_version, slack_message_release_notes_path, slack_message_details)

    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
