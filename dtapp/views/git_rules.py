from dtapp.main import *
from dtapp.services.email_service import send_email_query_html_mail, send_git_repo_creation_email

REPO_ADMINS = ["admin-user-1", "admin-user-2", "admin-user-3"]
TAP_TEAM_SLUG = "data-platform"
ENGINEERING_TEAM_SLUG = "engineering"
git_api_url = "https://api.github.com/repos/YOUR_GITHUB_ORG"
git_orgs_api_url = "https://api.github.com/orgs/YOUR_GITHUB_ORG"
headers = {
    "Authorization": f"Bearer {git_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
dbt_data = {
        "required_status_checks": {
            "strict": True,
            "contexts": ["check-source", "validate-pr-description"]
        },
        "enforce_admins": False,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1
        },
        "restrictions": None
    }
other_data = {
        "required_status_checks": {
            "strict": True,
            "contexts": []
        },
        "enforce_admins": False,
        "required_conversation_resolution": False,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1
        },
        "restrictions": None
    }
strict_repo_data = {
        "required_status_checks": {
            "strict": True,
            "contexts": []
        },
        "enforce_admins": True,
        "required_conversation_resolution": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1
        },
        "restrictions": None
    }


def get_all_repos(params):
    result = []
    query_url = f"{git_orgs_api_url}/repos"
    while True:
        response = requests.get(query_url, headers=headers, params=params)
        json_response = json.loads(response.text)
        if not json_response:
            break
        
        app.logger.info(f"Page: {params['page']} - Total records: {len(json_response)} - Status code: {response.status_code}")
        result.extend([item['name'] for item in json_response])
        params["page"] += 1
    return result


def enable_dependabot(repo_name):
    query_url = f"{git_api_url}/{repo_name}/vulnerability-alerts"
    response = requests.put(query_url, headers=headers)
    if response.status_code != 204:
        app.logger.info(f"Dependabot Failed: {repo_name} -  - Status code: {response.status_code}")


def enable_branch_protection_rule(repo_name, req_data, branch="main"):
    query_url = f"{git_api_url}/{repo_name}/branches/{branch}/protection"
    response = requests.put(query_url, headers=headers, data=json.dumps(req_data))
    if response.status_code != 200:
        app.logger.info(f"Branch protection rule Failed: {repo_name} -  - Status code: {response.status_code}")
    return response


def git_repo_status_check():
    dbt_repo_to_check = ('dbt-', 'dtx-')
    repo_to_skip = ('drive', 'tesseract', 'ipaas', 'tenant-')
    other_repo = ('dtml', 'dtml-v3')
    params = {"per_page": "100", "page": 1}

    all_repos = get_all_repos(params)
    for repo_name in all_repos:
        # Enabling dependabot security feature
        enable_dependabot(repo_name)
        
        # Enabling branch protection rule
        if repo_name.startswith(repo_to_skip):
            app.logger.info(f"Skipping repo: {repo_name}")
            continue
        
        # For other repos
        elif repo_name.startswith(other_repo):
            app.logger.info(f"Branch protection with other rule: {repo_name}")
            enable_branch_protection_rule(repo_name, other_data)
        
        # For dbt repos
        elif repo_name.startswith(dbt_repo_to_check):
            app.logger.info(f"Branch protection with dbt rule: {repo_name}")
            enable_branch_protection_rule(repo_name, dbt_data)
        
        # For defaults to strict repos
        else:
            app.logger.info(f"Branch protection with strict rule: {repo_name}")
            enable_branch_protection_rule(repo_name, strict_repo_data)

    return {"message": "Git repo check completed"}


new_repo_branch_protection = {
    "required_status_checks": {
        "strict": True,
        "contexts": []
    },
    "enforce_admins": True,
    "required_conversation_resolution": True,
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 1,
        "require_last_push_approval": True
    },
    "restrictions": None
}


def _notify(repo_name, initiated_by, success, message):
    try:
        send_git_repo_creation_email(repo_name, initiated_by, success=success, message=message)
    except Exception as e:
        app.logger.error(f"Failed to send git repo creation email: {str(e)}")


def add_repo_admins(repo_name):
    for username in REPO_ADMINS:
        url = f"{git_api_url}/{repo_name}/collaborators/{username}"
        response = requests.put(url, headers=headers, data=json.dumps({"permission": "admin"}))
        if response.status_code not in (200, 201, 204):
            app.logger.error(f"Failed to add admin {username} to {repo_name}: {response.status_code}")
        else:
            app.logger.info(f"Added {username} as admin to {repo_name}")


def add_collaborator_write_access(repo_name, username):
    url = f"{git_api_url}/{repo_name}/collaborators/{username}"
    response = requests.put(url, headers=headers, data=json.dumps({"permission": "write"}))
    if response.status_code not in (200, 201, 204):
        app.logger.error(f"Failed to add collaborator {username} to {repo_name}: {response.status_code}")
    else:
        app.logger.info(f"Added {username} with write access to {repo_name}")


def add_team_write_access(repo_name, team_slug):
    url = f"{git_orgs_api_url}/teams/{team_slug}/repos/YOUR_GITHUB_ORG/{repo_name}"
    response = requests.put(url, headers=headers, data=json.dumps({"permission": "push"}))
    if response.status_code not in (200, 201, 204):
        app.logger.error(f"Failed to add {team_slug} team to {repo_name}: {response.status_code}")
    else:
        app.logger.info(f"Added {team_slug} team with write access to {repo_name}")


def create_git_repo(repo_name, initiated_by, branch_name, collaborators=None, teams=None):
    check_url = f"{git_api_url}/{repo_name}"
    check_response = requests.get(check_url, headers=headers)
    if check_response.status_code == 200:
        # Repo already exists - just apply the branch protection rule to the given branch
        req_data = dbt_data if repo_name.startswith(('dbt-', 'dtx-')) else new_repo_branch_protection
        response = enable_branch_protection_rule(repo_name, req_data, branch=branch_name)
        if response.status_code != 200:
            error_msg = f"Failed to apply branch protection rule for '{repo_name}' branch '{branch_name}': {response.text}"
            app.logger.error(error_msg)
            return {"error": error_msg}, response.status_code

        success_msg = f"Repository '{repo_name}' already exists; branch protection rule applied to branch '{branch_name}'"
        app.logger.info(success_msg)
        return {"message": success_msg, "repo_name": repo_name, "branch_name": branch_name}, 200

    create_url = f"{git_orgs_api_url}/repos"
    create_payload = {
        "name": repo_name,
        "private": True,
        "auto_init": True,
    }
    create_response = requests.post(create_url, headers=headers, data=json.dumps(create_payload))
    if create_response.status_code not in (200, 201):
        error_msg = f"Failed to create repository '{repo_name}': {create_response.text}"
        app.logger.error(f"Repo creation failed: {error_msg}")
        _notify(repo_name, initiated_by, success=False, message=error_msg)
        return {"error": error_msg}, 500

    if branch_name != "main":
        ref_url = f"{git_api_url}/{repo_name}/git/ref/heads/main"
        ref_response = requests.get(ref_url, headers=headers)
        if ref_response.status_code == 200:
            main_sha = ref_response.json()["object"]["sha"]
            create_ref_url = f"{git_api_url}/{repo_name}/git/refs"
            requests.post(create_ref_url, headers=headers, data=json.dumps({
                "ref": f"refs/heads/{branch_name}",
                "sha": main_sha
            }))
        else:
            app.logger.error(f"Failed to get main SHA for {repo_name}: {ref_response.status_code}")

    update_url = f"{git_api_url}/{repo_name}"
    requests.patch(update_url, headers=headers, data=json.dumps({
        "default_branch": branch_name,
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
    }))

    enable_branch_protection_rule(repo_name, new_repo_branch_protection, branch=branch_name)
    add_repo_admins(repo_name)
    if "tap" in repo_name:
        add_team_write_access(repo_name, TAP_TEAM_SLUG)
    for username in (collaborators or []):
        add_collaborator_write_access(repo_name, username)
    for team_slug in (teams or []):
        add_team_write_access(repo_name, team_slug)

    success_msg = f"Repository '{repo_name}' created successfully with '{branch_name}' as default branch and protection rules applied"
    app.logger.info(success_msg)
    _notify(repo_name, initiated_by, success=True, message=success_msg)
    return {"message": success_msg, "repo_name": repo_name, "repo_url": f"https://github.com/YOUR_GITHUB_ORG/{repo_name}"}, 201
