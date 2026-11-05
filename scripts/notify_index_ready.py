"""Send the confirmed Index version to Ricky's webhook receiver."""

import http.client
import json
import os
import sys
from urllib.parse import urlsplit


def index_ready_event(repository, version, release_sha, workflow_run_url):
    """Return a version event with the same delivery id across retries."""
    if not all((repository, version, release_sha, workflow_run_url)):
        raise ValueError("Index event metadata is incomplete")
    return {
        "event": "freecad.index_ready",
        "repository": repository,
        "version": version,
        "release_tag": f"v{version}",
        "release_sha": release_sha,
        "workflow_run_url": workflow_run_url,
        "delivery_id": f"{repository}:index-ready:{version}",
    }


def _post(url, sender_key, event):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("The webhook requires an HTTPS URL")
    if not sender_key:
        raise ValueError("Webhook sender key is missing")
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port, timeout=30
    )
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(event).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {sender_key}",
            },
        )
        response = connection.getresponse()
        response.read(4096)
        return response.status
    finally:
        connection.close()


def main():
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        run_id = os.environ["GITHUB_RUN_ID"]
        event = index_ready_event(
            repository,
            os.environ["INDEX_VERSION"],
            os.environ["INDEX_RELEASE_SHA"],
            f"{server}/{repository}/actions/runs/{run_id}",
        )
        status = _post(
            os.environ["RICKY_INDEX_WEBHOOK_URL"],
            os.environ["RICKY_INDEX_WEBHOOK_KEY"],
            event,
        )
    except (OSError, KeyError, ValueError, http.client.HTTPException):
        # The endpoint and sender key may both contain credentials.
        print(
            "Index webhook delivery failed. Check its configuration and "
            "connection.",
            file=sys.stderr,
        )
        return 1
    if not 200 <= status < 300:
        print(f"Index webhook returned HTTP {status}", file=sys.stderr)
        return 1
    print(f"Index ready notification accepted for {event['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
