from dtapp.main import *
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse

ENDPOINTS_TO_CHECK = [
    "https://200.drivetrain.ai/drive/api/v1/public/plans",
    "https://200.drivetrain.ai/drive/api/v1/health/status",

    "https://400.drivetrain.ai/drive/api/v1/health/status",
    "https://400.drivetrain.ai/drive/api/v1/public/plans",
    "https://400.drivetrain.ai/drive/api/v1/internal/elasticsearch/keys",
    "https://400.drivetrain.ai/drive/api/v1/internal/bigquery/execute",
    "https://400.drivetrain.ai/drive/api/v1/internal/launchdarkly/enabled?flag=resetTenant",
]


def _get_apikey():
    get_secret_value_response = secrets_client.get_secret_value(SecretId=app.config['GLOBAL_KEY_SECRET_NAME'])
    return json.loads(get_secret_value_response["SecretString"])["global.drive.auth.key"]


INGRESS_URL = "http://ingress-nginx-controller.ingress-nginx.svc.cluster.local:443"


def _check_endpoint(url, apikey):
    try:
        parsed = urllib.parse.urlparse(url)
        ingress_url = INGRESS_URL + parsed.path + (("?" + parsed.query) if parsed.query else "")
        response = requests.get(ingress_url, headers={"Host": parsed.netloc, "apikey": apikey, "use-internal": "true"}, timeout=10)
        passed = 200 <= response.status_code < 300
        return {
            "url": url,
            "status_code": response.status_code,
            "passed": passed,
            "error": None,
        }
    except requests.exceptions.RequestException as e:
        return {
            "url": url,
            "status_code": None,
            "passed": False,
            "error": str(e),
        }


def check_single_endpoint(url):
    apikey = _get_apikey()
    return _check_endpoint(url, apikey)


def check_all_endpoints():
    apikey = _get_apikey()
    results = []
    with ThreadPoolExecutor(max_workers=len(ENDPOINTS_TO_CHECK)) as executor:
        futures = {executor.submit(_check_endpoint, url, apikey): url for url in ENDPOINTS_TO_CHECK}
        for future in as_completed(futures):
            results.append(future.result())

    all_passed = all(r["passed"] for r in results)
    return {
        "status": "passed" if all_passed else "failed",
        "results": sorted(results, key=lambda r: r["url"]),
    }
