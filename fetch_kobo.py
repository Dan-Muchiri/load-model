"""
fetch_kobo.py
=============
Downloads every submission of the household questionnaire from
KoboToolbox and runs survey_to_schema.py on them, in one command:

    python fetch_kobo.py                    # uses KOBO_FORM_ID from .env
    python fetch_kobo.py --form aBcD1234... # or pass the form ID

ONE-TIME SETUP
--------------
1. Kobo > Account settings > Security > API key: copy it.
2. Copy .env.example to .env (same folder as this file) and fill in
       KOBO_TOKEN=your key
       KOBO_FORM_ID=the code after /forms/ in the project's address
   .env is in .gitignore, so it stays on this laptop.
   (KOBO_TOKEN can also come from the environment or ~/.kobo_token.)

Each run writes:
    households/raw/kobo_YYYY-MM-DD_HHMM.json   the untouched export
    households/H00x.json, H00x_report.txt      one per submission
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from survey_to_schema import load_submissions, submission_to_household, write_outputs

ENV_FILE = Path(__file__).parent / ".env"      # git-ignored
TOKEN_FILE = Path.home() / ".kobo_token"
DEFAULT_SERVER = "https://kf.kobotoolbox.org"


def read_env(env_file=ENV_FILE):
    """KEY=VALUE lines from .env (blank lines and # comments ignored)."""
    values = {}
    if Path(env_file).exists():
        for line in Path(env_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def setting(name, env, default=""):
    """Environment variable first, then .env, then the default."""
    return os.environ.get(name, "").strip() or env.get(name, "") or default


def read_token(env, token_file=TOKEN_FILE):
    token = setting("KOBO_TOKEN", env)
    if not token and Path(token_file).exists():
        token = Path(token_file).read_text().strip()
    if not token:
        sys.exit("No Kobo API key. Copy .env.example to .env and set KOBO_TOKEN=your key.")
    return token


def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit("Kobo refused the API key (HTTP %d). Check KOBO_TOKEN in .env." % e.code)
        if e.code == 404:
            sys.exit("Kobo could not find that form (HTTP 404). Check the form ID.")
        raise


def fetch_submissions(form_id, token, server=DEFAULT_SERVER):
    """All submissions, following Kobo's paging ("next") until the end."""
    url = f"{server}/api/v2/assets/{form_id}/data/?format=json"
    results = []
    while url:
        page = get_json(url, token)
        results += page.get("results", [])
        url = page.get("next")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = read_env()
    ap.add_argument("--form", default=setting("KOBO_FORM_ID", env),
                    help="Kobo form ID (the code after /forms/); default KOBO_FORM_ID in .env")
    ap.add_argument("--server", default=setting("KOBO_SERVER", env, DEFAULT_SERVER))
    ap.add_argument("--out", default="households", help="output folder")
    args = ap.parse_args(argv)
    if not args.form:
        sys.exit("No form ID. Set KOBO_FORM_ID in .env or pass --form <ID>.")

    results = fetch_submissions(args.form, read_token(env), args.server)
    raw_dir = Path(args.out) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / f"kobo_{datetime.datetime.now():%Y-%m-%d_%H%M}.json"
    raw.write_text(json.dumps({"results": results}, indent=1, ensure_ascii=False))
    print(f"{len(results)} submission(s) downloaded -> {raw}")

    failed = 0
    for s in load_submissions(raw):
        assert isinstance(s, dict)  # each submission is a JSON object; clean() just can't say so statically
        label = s.get("household_id") or f"submission _id={s.get('_id')}"
        try:
            h, warnings, follow_up, log = submission_to_household(s)
            status = write_outputs(h, warnings, follow_up, log, args.out)
            print(f"  {h['household_id']}: {status}; {len(warnings)} warning(s)")
        except Exception as e:   # one broken submission must not stop the rest
            failed += 1
            print(f"  {label}: could not convert ({type(e).__name__}: {e})")
    if failed:
        print(f"{failed} submission(s) need checking in Kobo.")


if __name__ == "__main__":
    main()
