# Copyright 2015 Hewlett-Packard Development Company, L.P.
# Copyright 2023 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# Unmodified methods from Zuul 14.3.0 zuul/driver/github/githubconnection.py.
# https://opendev.org/zuul/zuul/src/tag/14.3.0/zuul/driver/github/githubconnection.py
class GithubConnection:
    def _create_or_update_check(self, github, project_name, check_run_id,
                                    **kwargs):
            if check_run_id:
                # Update an existing check run
                url = github.session.build_url(
                    'repos', project_name, 'check-runs', str(check_run_id))
                resp = github.session.patch(url, json=kwargs)
            else:
                # Create a new check run
                url = github.session.build_url(
                    'repos', project_name, 'check-runs')
                resp = github.session.post(url, json=kwargs)
            resp.raise_for_status()
            return resp.json().get('id')
    def updateCheck(self, project, pr_number, sha, status, completed, context,
                        details_url, message, file_comments, external_id,
                        zuul_event_id=None, check_run_id=None):
            log = get_annotated_logger(self.log, zuul_event_id)
            github = self.getGithubClient(project, zuul_event_id=zuul_event_id)
            self._append_accept_header(github, PREVIEW_CHECKS_ACCEPT)
    
            # Always provide an empty list of actions by default
            actions = []
    
            # Track a list of failed check run operations to report back to Github
            errors = []
    
            if not self._github_client_manager.usesAppAuthentication:
                # We don't try to update check runs, if we aren't authenticated as
                # Github app at all. If we are, we still have to ensure that we
                # don't crash on missing permissions.
                log.debug(
                    "Not authenticated as Github app. Unable to create or update "
                    "check run '%s' for sha %s on %s",
                    context, sha, project
                )
    
                errors.append(
                    "Unable to create or update check {}. Must be authenticated "
                    "as app integration.".format(
                        context
                    )
                )
                return None, errors
    
            output = {"title": "Summary", "summary": message}
    
            if file_comments:
                # Build the list of annotations to be applied on the check run
                output["annotations"] = self._buildAnnotationsFromComments(
                    file_comments
                )
    
            # Currently, the GithubReporter only supports start and end reporting.
            # During the build no further update will be reported.
            if completed:
                # As the buildset itself does not provide a proper end time, we
                # use the current time instead. Otherwise, we would have to query
                # all builds contained in the buildset and search for the latest
                # build.end_time available.
                completed_at = datetime.datetime.now(utc).isoformat()
    
                # When reporting the completion of a check_run, we must set the
                # conclusion, as the status will always be "completed".
                conclusion = status
    
                if not check_run_id:
                    log.debug("Could not find check run %s for %s#%s on sha %s. "
                              "Creating a new one", context, project, pr_number,
                              sha)
                    action = 'create'
                    arguments = dict(
                        name=context,
                        head_sha=sha,
                        conclusion=conclusion,
                        completed_at=completed_at,
                        output=output,
                        details_url=details_url,
                        external_id=external_id,
                        actions=actions,
                    )
                else:
                    log.debug("Updating existing check run %s for %s#%s on sha %s "
                              "with status %s", context, project, pr_number, sha,
                              status)
                    action = 'update'
                    arguments = dict(
                        conclusion=conclusion,
                        completed_at=completed_at,
                        output=output,
                        details_url=details_url,
                        external_id=external_id,
                        actions=actions,
                    )
            else:
                # Add an abort/dequeue action to running check runs
                actions.append(
                    {
                        "label": "Abort",
                        "description": "Abort this check run",
                        # Usually Github wants us to provide an identifier for our
                        # system here, so we can identify this action. But as zuul
                        # is already identifying this event based on the check run
                        # this shouldn't be necessary.
                        "identifier": "abort",
                    }
                )
    
                action = 'create'
                arguments = dict(
                    name=context,
                    head_sha=sha,
                    status=status,
                    output=output,
                    details_url=details_url,
                    external_id=external_id,
                    actions=actions,
                )
    
            # Create/update the check run
            try:
                check_run_id = self._create_or_update_check(
                    github,
                    project,
                    check_run_id,
                    **arguments,
                )
    
            except requests.exceptions.HTTPError as exc:
                log.error("Failed to %s check run %s for %s#%s on sha %s: %s",
                          action, context, project, pr_number, sha, str(exc))
                errors.append("Failed to {} check run {}: {}".format(
                    action, context, str(exc)))
    
            return check_run_id, errors
