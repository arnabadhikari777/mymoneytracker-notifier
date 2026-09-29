"""
Runs on GitHub Actions (not on PythonAnywhere) once a day.

Why this lives here and not on PythonAnywhere: sending a real push
notification means talking to Google/Mozilla's push servers
(fcm.googleapis.com etc), and PythonAnywhere's free plan blocks outbound
requests to anything not on its whitelist - which does not include those
push services. GitHub Actions has no such restriction, so it does the actual
sending; PythonAnywhere only ever answers "here's what needs sending" over
a normal HTTPS GET request (which free-plan PythonAnywhere can serve fine,
since that's an inbound request to PythonAnywhere, not an outbound one).

Required environment variables (set as GitHub Secrets):
  PYTHONANYWHERE_API_URL   e.g. https://yourusername.pythonanywhere.com/api/pending-reminders
  CRON_SECRET              must match the CRON_SECRET set in your WSGI file
  VAPID_PRIVATE_KEY        must match the VAPID_PRIVATE_KEY set in your WSGI file
                           (the RAW base64url key from generate_vapid_keys.py -
                           NOT the PEM-formatted one; see that script's comments)
  VAPID_CLAIM_EMAIL        optional, e.g. mailto:you@example.com
  TEST_MODE                optional, "true" to request a test push instead of
                           waiting for a real 7-day/3-day reminder to be due
"""
import os
import sys
import json
import requests
from pywebpush import webpush, WebPushException

API_URL = os.environ["PYTHONANYWHERE_API_URL"]
CRON_SECRET = os.environ["CRON_SECRET"]
VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL") or "mailto:example@example.com"
TEST_MODE = (os.environ.get("TEST_MODE") or "").lower() == "true"

CLEANUP_URL = API_URL.rsplit("/", 1)[0] + "/subscription-cleanup"


def main():
    params = {"secret": CRON_SECRET}
    if TEST_MODE:
        params["test"] = "1"
        print("Running in TEST MODE - this ignores real due-dates and sends "
              "a test push to every subscribed device.")

    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    reminders = resp.json()

    if not reminders:
        print("No reminders due today." if not TEST_MODE else
              "No devices are subscribed yet - open the app, go to Settings, "
              "and tap 'Enable reminders' on your phone first.")
        return

    sent, failed = 0, 0
    for r in reminders:
        try:
            webpush(
                subscription_info={"endpoint": r["endpoint"], "keys": r["keys"]},
                data=json.dumps({"title": r["title"], "body": r["body"], "url": r.get("url", "/")}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
            sent += 1
            print(f"Sent: {r['body']}")
        except WebPushException as e:
            failed += 1
            print(f"Failed ({e}): {r['body']}", file=sys.stderr)
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                # Subscription is gone (browser data cleared, uninstalled, etc.)
                # - tell PythonAnywhere to forget it so it stops being tried.
                try:
                    requests.post(
                        CLEANUP_URL,
                        params={"secret": CRON_SECRET},
                        json={"endpoint": r["endpoint"]},
                        timeout=15,
                    )
                except requests.RequestException:
                    pass
        except Exception as e:
            # Anything else (e.g. a malformed VAPID key) would otherwise
            # crash the whole run silently from the outside - catch it,
            # report it clearly, and keep trying the rest of the reminders.
            failed += 1
            print(f"UNEXPECTED ERROR ({type(e).__name__}: {e}) sending: {r['body']}", file=sys.stderr)

    print(f"Done. Sent {sent}, failed {failed}.")
    if failed and sent == 0:
        # Make a fully-failed run visible as a failed GitHub Actions run,
        # not a quiet green checkmark that hides the problem.
        sys.exit(1)


if __name__ == "__main__":
    main()

