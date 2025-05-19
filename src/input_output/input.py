from github_actions.common import ArgumentParser

class TenantArtifactCIArgumentParser(ArgumentParser):
    def setup_arguments(self):
        self.parser.add_argument("--artifact_version",
                                 action=self.EnvDefault, envvar="INPUT_ARTIFACT_VERSION",
                                 type=str, required=False)

        self.parser.add_argument("--autotag",
                                 action=self.EnvDefault, envvar="INPUT_AUTOTAG",
                                 type=str, required=False)

        self.parser.add_argument("--github-token",
                                 action=self.EnvDefault, envvar="INPUT_GITHUB_TOKEN_REPO_FULL_ACCESS",
                                 type=str, required=False)

        self.parser.add_argument("--push_tag",
                                 action=self.EnvDefault, envvar="INPUT_PUSH_TAG",
                                 type=str, required=False)

        self.parser.add_argument("--slack_message_details",
                                 action=self.EnvDefault, envvar="INPUT_SLACK_MESSAGE_DETAILS",
                                 type=str, required=False)

        self.parser.add_argument("--slack_message_release_notes_path",
                                 action=self.EnvDefault, envvar="INPUT_SLACK_MESSAGE_RELEASE_NOTES_PATH",
                                 type=str, required=False)

        self.parser.add_argument("--slack_notifications",
                                 action=self.EnvDefault, envvar="INPUT_SLACK_NOTIFICATIONS",
                                 type=str, required=False)

        self.parser.add_argument("--slack_webhook",
                                 action=self.EnvDefault, envvar="INPUT_SLACK_WEBHOOK",
                                 type=str, required=False)

        self.parser.add_argument("--update_tenant_environments",
                                 action=self.EnvDefault, envvar="INPUT_UPDATE_TENANT_ENVIRONMENTS",
                                 type=str, required=False)

        self.parser.add_argument("--update_tenant_workflow_file",
                                 action=self.EnvDefault, envvar="INPUT_UPDATE_TENANT_WORKFLOW_FILE",
                                 type=str, required=False)
