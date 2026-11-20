"""
submit_test_households.py
=========================
Sends test households from test_households_kobo.json to the KoboToolbox
project as ordinary submissions, so they do not have to be typed by hand.
Uses the same .env (KOBO_TOKEN, KOBO_FORM_ID, KOBO_SERVER) as fetch_kobo.py.

By default it sends every test household EXCEPT H902 and H909 (those two are
entered by hand in the web form to test data entry itself).

Safe by default: without --submit it only checks and prints what it would
send. Run from the repo root, in the load-model conda env:

    python questionnaire/test_households/submit_test_households.py            # check only
    python questionnaire/test_households/submit_test_households.py --submit   # send
    python questionnaire/test_households/submit_test_households.py --only H901 H903 --submit

Before sending it checks that:
  - the deployed form is the version in questionnaire/household_survey.xlsx
    (settings "version"), so answers fit the form now in Kobo;
  - none of the chosen households is already in Kobo under that version
    (no double submissions). Older-version copies are only reported.

Only current endpoints are used (KoboCAT's /api/v1 was retired, HTTP 410):
  - GET  {server}/api/v2/assets/{form}/          deployed version, form settings
  - GET  {server}/api/v2/assets/{form}/xform/    the deployed XForm (root element)
  - GET  {server}/api/v2/assets/{form}/data/     existing submissions (as fetch_kobo.py)
  - POST https://kc.kobotoolbox.org/submission   OpenRosa submission (multipart
                                                 field "xml_submission_file")
The submission URL can be overridden with --submission-url.
"""
import html
import re
import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from fetch_kobo import (DEFAULT_SERVER, fetch_submissions, get_json,  # noqa: E402
                        read_env, read_token, setting)

EXPORT = HERE / "test_households_kobo.json"
FORM_XLSX = REPO / "questionnaire" / "household_survey.xlsx"
TYPED_BY_HAND = ("H902", "H909")


def form_version_in_repo():
    ws = openpyxl.load_workbook(FORM_XLSX)["settings"]
    head, row = list(ws.iter_rows(values_only=True))[:2]
    return str(dict(zip(head, row))["version"])


def deployed_instance_root(kf, form_id, token):
    """Root tag and attributes of the deployed XForm's primary instance, the
    form's settings version, and the deployed version id (Kobo's __version__)."""
    asset = get_json(f"{kf}/api/v2/assets/{form_id}/?format=json", token)
    if not asset.get("has_deployment"):
        sys.exit("The form has no deployment in Kobo. Deploy household_survey.xlsx first.")
    settings = asset.get("content", {}).get("settings", {})
    if isinstance(settings, list):
        settings = settings[0] if settings else {}
    version_id = asset.get("deployed_version_id")
    tag, attrib = form_id, {"id": form_id}            # Kobo's default root: the asset uid
    try:
        req = urllib.request.Request(f"{kf}/api/v2/assets/{form_id}/xform/",
                                     headers={"Authorization": f"Token {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", errors="replace")
        # The route may render the XForm as highlighted HTML: strip tags, unescape.
        if "<h:html" not in text:
            text = html.unescape(re.sub(r"<[^>]+>", "", text))
        start, end = text.find("<h:html"), text.rfind("</h:html>") + len("</h:html>")
        xform = ET.fromstring(text[start:end])
        inst = xform.find(".//{http://www.w3.org/2002/xforms}instance")
        root = list(inst)[0]
        tag, attrib = root.tag.split("}")[-1], dict(root.attrib)
    except Exception as e:                             # fall back to the uid root
        print(f"  (could not read the deployed XForm, using root <{form_id} id=\"{form_id}\">: "
              f"{type(e).__name__})")
    # Kobo stores the deployed form under the asset uid as its id_string (the
    # "_xform_id_string" of every submission), even when the XLSForm's
    # settings give a form_id: the /xform/ preview shows the form_id, which
    # KoboCAT does not know (HTTP 404 "Not found"). So the instance must carry
    # id=<asset uid>, and the deployed version id.
    attrib = dict(attrib, id=form_id)
    if version_id:
        attrib["version"] = version_id
    return tag, attrib, str(settings.get("version", "")), version_id


def build_xml(sub, tag, attrib, version_id):
    """Kobo-style export (group paths, repeats as lists) -> OpenRosa XML instance."""
    root = ET.Element(tag, attrib)

    def place(parent, prefix, data):
        nodes = {}                                   # element per path segment, in order

        def node_for(segs):
            el = parent
            path = ()
            for s in segs:
                path += (s,)
                if path not in nodes:
                    nodes[path] = ET.SubElement(el, s)
                el = nodes[path]
            return el

        for key, val in data.items():
            rel = key[len(prefix):] if key.startswith(prefix) else key
            segs = rel.split("/")
            if isinstance(val, list):
                container = node_for(segs[:-1]) if len(segs) > 1 else parent
                for item in val:
                    child = ET.SubElement(container, segs[-1])
                    place(child, f"{key}/", item)
            else:
                node_for(segs).text = str(val)

    place(root, "", {k: v for k, v in sub.items() if not k.startswith("_")})
    ET.SubElement(root, "__version__").text = version_id or ""
    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "instanceID").text = f"uuid:{uuid.uuid4()}"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def post_xml(url, token, xml_bytes):
    boundary = f"----submit{uuid.uuid4().hex}"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"xml_submission_file\"; "
            f"filename=\"submission.xml\"\r\nContent-Type: text/xml\r\n\r\n").encode() + xml_bytes + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Token {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-OpenRosa-Version": "1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode(errors="replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = read_env(REPO / ".env")
    ap.add_argument("--form", default=setting("KOBO_FORM_ID", env))
    ap.add_argument("--server", default=setting("KOBO_SERVER", env, DEFAULT_SERVER))
    ap.add_argument("--only", nargs="+", help="household IDs to send (default: all except H902, H909)")
    ap.add_argument("--submit", action="store_true", help="actually send (default: check only)")
    ap.add_argument("--submission-url", help="OpenRosa submission URL "
                    "(default: the server's kc. host + /submission)")
    args = ap.parse_args(argv)
    if not args.form:
        sys.exit("No form ID. Set KOBO_FORM_ID in .env or pass --form <ID>.")
    token = read_token(env)
    kf = args.server.rstrip("/")
    # kf.kobotoolbox.org answers /submission with 404; the OpenRosa endpoint is on kc.
    submit_url = args.submission_url or kf.replace("://kf.", "://kc.").replace("://eu.", "://kc-eu.") + "/submission"

    subs = json.loads(EXPORT.read_text())["results"]
    by_id = {s["group_start/household_id"]: s for s in subs}
    chosen = args.only or [h for h in by_id if h not in TYPED_BY_HAND]
    missing = [h for h in chosen if h not in by_id]
    if missing:
        sys.exit(f"Not in {EXPORT.name}: {missing}")

    want = form_version_in_repo()
    tag, attrib, deployed, version_id = deployed_instance_root(kf, args.form, token)
    print(f"Form in repo: version {want}. Deployed in Kobo: version {deployed or '?'} ({version_id}).")
    print(f"Submissions go to {submit_url}, root <{tag} {attrib}>.")
    if deployed != want:
        sys.exit("Deployed form is not the repo's version. Replace and redeploy household_survey.xlsx "
                 "in Kobo first; nothing was sent.")

    existing = fetch_submissions(args.form, token, kf)
    clash = []
    for e in existing:
        hid = e.get("group_start/household_id")
        if hid in chosen:
            same = e.get("__version__") == version_id
            print(f"  already in Kobo: {hid} (_id {e.get('_id')}, "
                  f"{'THIS version' if same else 'older version ' + str(e.get('__version__'))})")
            if same:
                clash.append(hid)
    if clash:
        sys.exit(f"Already submitted under the deployed version: {clash}. Nothing was sent "
                 f"(use --only to choose others).")

    print(f"{'Sending' if args.submit else 'Would send'}: {', '.join(chosen)}")
    if not args.submit:
        print("Check only. Re-run with --submit to send.")
        return 0
    bad = 0
    for hid in chosen:
        status, text = post_xml(submit_url, token, build_xml(by_id[hid], tag, attrib, version_id))
        ok = status in (200, 201, 202)
        bad += not ok
        print(f"  {hid}: HTTP {status} {'OK' if ok else text}")
    print("Done. Fetch with fetch_kobo.py to check them." if not bad else f"{bad} failed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
