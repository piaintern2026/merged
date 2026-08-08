"""
services/file_storage.py
-------------------------
Persistent storage for the documents interns attach to a Project
Submission or their Final Internship Report.

Why not just save to disk (the old approach)?
Vercel's serverless functions run on a read-only/ephemeral filesystem --
anything written under static/uploads/... at request time is gone the
moment that invocation ends, and is never visible to the next request
(which is very likely a *different* instance). Local disk storage looks
like it works when you test it once and immediately re-open the file in
the same request/deploy, then silently loses every uploaded file in
production.

This module stores files in Vercel Blob (https://vercel.com/docs/vercel-blob)
over its plain HTTP API, so no extra SDK/runtime dependency is needed
beyond `requests`, which is already used elsewhere in this project.

Local development (no BLOB_READ_WRITE_TOKEN configured) transparently
falls back to on-disk storage under SUBMISSIONS_UPLOAD_FOLDER, so
nothing changes for anyone running the app locally without Blob set up.
"""

import os
import uuid

import requests
from flask import current_app, url_for
from werkzeug.utils import secure_filename

BLOB_API_BASE = "https://blob.vercel-storage.com"
BLOB_API_VERSION = "7"


def _blob_token() -> str | None:
    return current_app.config.get("BLOB_READ_WRITE_TOKEN") or os.environ.get(
        "BLOB_READ_WRITE_TOKEN"
    )


def allowed_document(filename: str) -> bool:
    """Check whether the uploaded file has an allowed document extension
    (PDF, Word, Excel, or PowerPoint)."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in current_app.config["ALLOWED_DOCUMENT_EXTENSIONS"]
    )


def save_submission_file(file_storage, subfolder: str):
    """
    Persist a single uploaded document and return
    (stored_reference, original_filename), or None if no file was
    provided. `stored_reference` is either a full https:// Vercel Blob
    URL (when BLOB_READ_WRITE_TOKEN is configured, i.e. in production)
    or a bare on-disk filename (local dev fallback) -- pass it to
    resolve_file_url() to get something a browser can open.

    Only ever handles the single file passed in, matching the "one file
    per submission" rule enforced by the calling routes (the HTML forms
    only ever render a single, non-multiple <input type="file">).

    Raises ValueError if the file type isn't allowed or the upload
    couldn't be stored.
    """
    if not file_storage or file_storage.filename == "":
        return None

    if not allowed_document(file_storage.filename):
        allowed = ", ".join(
            sorted(current_app.config["ALLOWED_DOCUMENT_EXTENSIONS"])
        )
        raise ValueError(f"Invalid file type. Allowed formats: {allowed}.")

    original_name = secure_filename(file_storage.filename)
    extension = original_name.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{extension}"

    token = _blob_token()
    if token:
        pathname = f"{subfolder}/{unique_name}"
        try:
            resp = requests.put(
                f"{BLOB_API_BASE}/{pathname}",
                data=file_storage.stream.read(),
                headers={
                    "authorization": f"Bearer {token}",
                    "x-api-version": BLOB_API_VERSION,
                    "x-content-type": file_storage.mimetype
                    or "application/octet-stream",
                    # We already generate a unique filename ourselves.
                    "x-add-random-suffix": "0",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ValueError(
                "Could not upload the file to storage. Please try again."
            ) from exc

        if resp.status_code >= 400:
            current_app.logger.error(
                "Vercel Blob upload failed (%s): %s", resp.status_code, resp.text
            )
            raise ValueError(
                "Could not upload the file to storage. Please try again."
            )

        file_url = resp.json().get("url")
        return file_url, original_name

    # Local dev fallback: plain disk storage (never used on Vercel once
    # BLOB_READ_WRITE_TOKEN is set).
    target_folder = os.path.join(
        current_app.config["SUBMISSIONS_UPLOAD_FOLDER"], subfolder
    )
    os.makedirs(target_folder, exist_ok=True)
    file_storage.save(os.path.join(target_folder, unique_name))
    return unique_name, original_name


def delete_submission_file(stored_reference: str, subfolder: str) -> None:
    """Delete a previously stored submission/report file, if present.
    Non-fatal on failure -- a leftover blob/file is safe to ignore and
    should never block the surrounding database update."""
    if not stored_reference:
        return

    if stored_reference.startswith("http://") or stored_reference.startswith(
        "https://"
    ):
        token = _blob_token()
        if not token:
            return
        try:
            requests.delete(
                BLOB_API_BASE,
                params={"url": stored_reference},
                headers={
                    "authorization": f"Bearer {token}",
                    "x-api-version": BLOB_API_VERSION,
                },
                timeout=15,
            )
        except requests.RequestException:
            current_app.logger.warning(
                "Failed to delete blob %s (leftover file, non-fatal).",
                stored_reference,
            )
        return

    # Legacy local file (uploaded before Blob storage was wired up, or a
    # local dev deployment without BLOB_READ_WRITE_TOKEN set).
    path = os.path.join(
        current_app.config["SUBMISSIONS_UPLOAD_FOLDER"], subfolder, stored_reference
    )
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def resolve_file_url(stored_reference: str, subfolder: str) -> str:
    """Turn a stored file reference into a URL a browser can open: the
    Blob URL as-is if it's already one, or the legacy local
    /static/uploads/submissions/<subfolder>/... path for files that were
    uploaded before Blob storage existed."""
    if not stored_reference:
        return "#"
    if stored_reference.startswith("http://") or stored_reference.startswith(
        "https://"
    ):
        return stored_reference
    return url_for(
        "static", filename=f"uploads/submissions/{subfolder}/{stored_reference}"
    )
