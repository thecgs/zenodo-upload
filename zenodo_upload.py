#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def human_size(n):
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024


def md5sum(path, block_size=8 * 1024 * 1024):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def api_error(r):
    try:
        return json.dumps(r.json(), ensure_ascii=False, indent=2)
    except Exception:
        return r.text[:2000]


class ProgressFile:
    """File-like object that prints progress while requests streams it."""
    def __init__(self, path):
        self.path = Path(path)
        self.fp = self.path.open("rb")
        self.size = self.path.stat().st_size
        self.last_print = 0.0

    def __len__(self):
        return self.size

    def read(self, amount=-1):
        data = self.fp.read(amount)
        now = time.monotonic()
        if now - self.last_print >= 0.5 or not data:
            pos = self.fp.tell()
            pct = 100.0 if self.size == 0 else pos * 100.0 / self.size
            print(
                f"\r  {self.path.name}: {human_size(pos)} / "
                f"{human_size(self.size)} ({pct:6.2f}%)",
                end="",
                flush=True,
            )
            self.last_print = now
        return data

    def tell(self):
        return self.fp.tell()

    def seek(self, *args):
        return self.fp.seek(*args)

    def fileno(self):
        return self.fp.fileno()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.fp.close()


class ZenodoClient:
    def __init__(self, token, sandbox=False, connect_timeout=30, upload_timeout=3600, api_timeout=120):
        host = "sandbox.zenodo.org" if sandbox else "zenodo.org"
        self.api = f"https://{host}/api"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.connect_timeout = connect_timeout
        self.upload_timeout = upload_timeout
        self.api_timeout = api_timeout

    def create_draft(self):
        r = self.session.post(
            f"{self.api}/deposit/depositions",
            json={},
            timeout=(self.connect_timeout, self.api_timeout),
        )
        if r.status_code != 201:
            raise RuntimeError(
                f"Cannot create draft: HTTP {r.status_code}\n{api_error(r)}"
            )
        return r.json()

    def get_draft(self, deposition_id):
        r = self.session.get(
            f"{self.api}/deposit/depositions/{deposition_id}",
            timeout=(self.connect_timeout, self.api_timeout),
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Cannot read draft: HTTP {r.status_code}\n{api_error(r)}"
            )
        return r.json()

    def update_metadata(self, deposition_id, metadata):
        r = self.session.put(
            f"{self.api}/deposit/depositions/{deposition_id}",
            json={"metadata": metadata},
            timeout=(self.connect_timeout, self.api_timeout),
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Cannot update metadata: HTTP {r.status_code}\n{api_error(r)}"
            )
        return r.json()

    @staticmethod
    def remote_file_matches(draft, filename, size, md5):
        for item in draft.get("files", []):
            name = item.get("filename") or item.get("key") or item.get("name")
            remote_size = item.get("filesize", item.get("size"))
            checksum = item.get("checksum", "")
            if checksum.startswith("md5:"):
                checksum = checksum[4:]
            if name == filename:
                try:
                    size_ok = int(remote_size) == int(size)
                except (TypeError, ValueError):
                    size_ok = False
                return size_ok and checksum.lower() == md5.lower()
        return False

    def upload_file(self, deposition_id, bucket_url, path, retries=8):
        path = Path(path)
        size = path.stat().st_size

        print(f"\nCalculating MD5: {path}")
        local_md5 = md5sum(path)
        print(f"  MD5: {local_md5}")

        draft = self.get_draft(deposition_id)
        if self.remote_file_matches(draft, path.name, size, local_md5):
            print(f"Already uploaded and verified, skipping: {path.name}")
            return

        upload_url = f"{bucket_url.rstrip('/')}/{quote(path.name, safe='')}"
        last_error = None

        for attempt in range(1, retries + 1):
            if attempt > 1:
                # The server may have completed the upload even if the response was lost.
                try:
                    draft = self.get_draft(deposition_id)
                    if self.remote_file_matches(draft, path.name, size, local_md5):
                        print(f"Server already has a complete copy: {path.name}")
                        return
                except Exception as exc:
                    print(f"Could not verify remote state before retry: {exc}")

            print(f"Uploading {path.name} (attempt {attempt}/{retries})")

            try:
                with ProgressFile(path) as pf:
                    r = self.session.put(
                        upload_url,
                        data=pf,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=(self.connect_timeout, self.upload_timeout),
                    )
                print()

                if r.status_code in (200, 201):
                    result = r.json()
                    remote_size = result.get("size", result.get("filesize"))
                    remote_md5 = result.get("checksum", "")
                    if remote_md5.startswith("md5:"):
                        remote_md5 = remote_md5[4:]

                    if remote_size is not None and int(remote_size) != size:
                        raise RuntimeError(
                            f"Size mismatch: local={size}, remote={remote_size}"
                        )
                    if remote_md5 and remote_md5.lower() != local_md5.lower():
                        raise RuntimeError(
                            f"MD5 mismatch: local={local_md5}, remote={remote_md5}"
                        )

                    print(f"Upload OK: {path.name}")
                    print(f"  size: {human_size(size)}")
                    print(f"  md5 : {local_md5}")
                    return

                if r.status_code not in RETRYABLE_STATUS:
                    raise RuntimeError(
                        f"Upload failed: HTTP {r.status_code}\n{api_error(r)}"
                    )

                last_error = RuntimeError(
                    f"Retryable HTTP {r.status_code}: {api_error(r)}"
                )

            except (requests.ConnectionError, requests.Timeout) as exc:
                print(f"\nConnection problem: {exc}")
                last_error = exc
            except requests.RequestException as exc:
                print(f"\nRequest problem: {exc}")
                last_error = exc

            if attempt < retries:
                wait = min(60, 2 ** attempt)
                print(f"Retrying this file in {wait} s ...")
                time.sleep(wait)

        raise RuntimeError(f"Giving up on {path.name}: {last_error}")

    def publish(self, deposition_id):
        r = self.session.post(
            f"{self.api}/deposit/depositions/{deposition_id}/actions/publish",
            timeout=(self.connect_timeout, self.api_timeout),
        )
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Publish failed: HTTP {r.status_code}\n{api_error(r)}"
            )
        return r.json()


def parse_creator(value):
    # Example: "Chen, Guisen::Hainan University"
    if "::" in value:
        name, affiliation = value.split("::", 1)
        return {"name": name.strip(), "affiliation": affiliation.strip()}
    return {"name": value.strip()}


def main():
    p = argparse.ArgumentParser(
        description="Upload files to Zenodo with the official REST API."
    )
    p.add_argument("files", nargs="+", help="File(s) to upload")
    p.add_argument(
        "-tk", "--token",
        default=os.environ.get("ZENODO_TOKEN"),
        help="API token; preferably set the ZENODO_TOKEN environment variable",
    )
    p.add_argument(
        "-id", "--deposition-id",
        type=int,
        help="Use an existing unpublished deposition instead of creating a new one",
    )
    p.add_argument("--sandbox", action="store_true", help="Use sandbox.zenodo.org")
    p.add_argument("--title", help="Record title")
    p.add_argument(
        "--creator",
        action="append",
        default=[],
        help='Repeatable. Format: "Family, Given::Affiliation"',
    )
    p.add_argument("--description", help="Record description/abstract")
    p.add_argument(
        "--upload-type",
        default="dataset",
        choices=[
            "publication", "poster", "presentation", "dataset", "image",
            "video", "software", "lesson", "physicalobject", "other",
        ],
    )
    p.add_argument(
        "--publication-date",
        default=date.today().isoformat(),
        help="YYYY-MM-DD; default is today",
    )
    p.add_argument("--keywords", nargs="*", default=None)
    p.add_argument("--retries", type=int, default=8)

    p.add_argument(
        "--connect-timeout",
        type=float,
        default=60,
        help="Connection timeout in seconds (default: 60)",
    )
    p.add_argument(
        "--upload-timeout",
        type=float,
        default=7200,
        help="Upload read/write timeout in seconds (default: 7200)",
    )
    p.add_argument(
        "--api-timeout",
        type=float,
        default=120,
        help="Timeout for non-upload Zenodo API calls in seconds (default: 120)",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="Publish after upload. Without this option the record stays a draft.",
    )
    args = p.parse_args()

    if not args.token:
        p.error("No token. Set ZENODO_TOKEN or use --token.")

    paths = [Path(x).expanduser().resolve() for x in args.files]
    for path in paths:
        if not path.is_file():
            p.error(f"Not a file: {path}")

    if args.publish and not (args.title and args.creator and args.description):
        p.error(
            "--publish requires --title, at least one --creator, and --description"
        )

    client = ZenodoClient(
        args.token,
        sandbox=args.sandbox,
        connect_timeout=args.connect_timeout,
        upload_timeout=args.upload_timeout,
        api_timeout=args.api_timeout,
    )

    if args.deposition_id:
        deposition_id = args.deposition_id
        draft = client.get_draft(deposition_id)
        print(f"Using existing draft: {deposition_id}")
    else:
        draft = client.create_draft()
        deposition_id = draft["id"]
        print(f"Created draft: {deposition_id}")

    bucket_url = draft["links"]["bucket"]
    print(f"Draft page: {draft['links'].get('html', '(not returned)')}")

    if args.title or args.creator or args.description:
        if not (args.title and args.creator and args.description):
            p.error(
                "For metadata, provide --title, --creator and --description together"
            )
        metadata = {
            "title": args.title,
            "upload_type": args.upload_type,
            "publication_date": args.publication_date,
            "description": args.description,
            "creators": [parse_creator(x) for x in args.creator],
            "access_right": "open",
        }
        if args.keywords:
            metadata["keywords"] = args.keywords
        client.update_metadata(deposition_id, metadata)
        print("Metadata saved.")

    for path in paths:
        client.upload_file(
            deposition_id=deposition_id,
            bucket_url=bucket_url,
            path=path,
            retries=args.retries,
        )

    print("\nAll requested files uploaded.")
    print(f"Deposition ID: {deposition_id}")

    if args.publish:
        result = client.publish(deposition_id)
        print("Published.")
        print(f"DOI: {result.get('doi', '(check Zenodo record)')}")
        print(f"URL: {result.get('links', {}).get('html', '(check Zenodo)')}")
    else:
        print("Kept as draft; nothing was published.")


if __name__ == "__main__":
    main()
