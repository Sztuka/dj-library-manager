"""Tests for the review UI server (djlib.review.server)."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

from djlib.review.server import app, _load_genres


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Index page ───────────────────────────────────────────────────────────────

def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<!DOCTYPE html>" in resp.data
    assert b"Review" in resp.data


# ── Genres API ───────────────────────────────────────────────────────────────

def test_genres_returns_list(client):
    resp = client.get("/api/genres")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)
    assert len(data) > 0
    # Check alphabetical order
    assert data == sorted(data)


def test_load_genres_returns_labels():
    labels = _load_genres()
    assert isinstance(labels, list)
    assert len(labels) > 0
    # Should be strings
    assert all(isinstance(g, str) for g in labels)


# ── Tracks API ───────────────────────────────────────────────────────────────

def test_tracks_unsorted(client):
    resp = client.get("/api/tracks?source=unsorted")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)
    # If unsorted.csv exists, should have data
    # (may be empty in CI, but structure should be valid)


def test_tracks_library(client):
    resp = client.get("/api/tracks?source=library")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_tracks_invalid_source(client):
    resp = client.get("/api/tracks?source=bogus")
    assert resp.status_code == 400


# ── Audio streaming ──────────────────────────────────────────────────────────

def test_audio_no_path(client):
    resp = client.get("/api/audio")
    assert resp.status_code == 400


def test_audio_missing_file(client):
    resp = client.get("/api/audio?path=/nonexistent/file.mp3")
    assert resp.status_code == 404


def test_audio_serves_file(client, tmp_path):
    """Create a temp file and verify audio endpoint streams it."""
    p = tmp_path / "test.mp3"
    p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)  # minimal MP3-like header
    resp = client.get(f"/api/audio?path={p}")
    assert resp.status_code == 200
    assert resp.content_type.startswith("audio/")


# ── Track update API ────────────────────────────────────────────────────────

def test_update_no_body(client):
    resp = client.post("/api/tracks/update")
    assert resp.status_code in (400, 415)


def test_update_missing_track_id(client):
    resp = client.post(
        "/api/tracks/update",
        json={"fields": {"status": "accept"}},
    )
    assert resp.status_code == 400


def test_update_no_fields(client):
    resp = client.post(
        "/api/tracks/update",
        json={"track_id": "fake-id"},
    )
    assert resp.status_code == 400


def test_update_track_not_found(client):
    resp = client.post(
        "/api/tracks/update",
        json={"track_id": "nonexistent-id-12345", "fields": {"status": "accept"}},
    )
    assert resp.status_code == 404
