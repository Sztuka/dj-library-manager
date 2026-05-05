"""
Unit tests for MusicBrainz first release resolution.

Tests mb_fetch_first_release_for_recording() with mocked responses.
No network calls.

The function flow:
  1. _cached_browse_releases(mbid)  →  {"release-list": [{..., "release-group": {"id": rg_id}}]}
  2. _cached_get_release_group_by_id(rg_id)  →  {"release-group": {id, title, primary-type,
     secondary-type-list, first-release-date, release-list}}
  3. Filter: keep non-Live, non-Compilation RGs that have first-release-date
  4. Return earliest as FirstReleaseAlbum (release_category always "studio")
  Note: when only live releases exist, function returns None.
"""
from __future__ import annotations
from unittest.mock import patch
import pytest

from djlib.metadata.mb_client import mb_fetch_first_release_for_recording, FirstReleaseAlbum

PATCH_BROWSE = "djlib.metadata.mb_client._cached_browse_releases"
PATCH_RG = "djlib.metadata.mb_client._cached_get_release_group_by_id"


def _rg(rg_id, title, primary_type, secondary_types, first_date, releases=None):
    return {
        "release-group": {
            "id": rg_id,
            "title": title,
            "primary-type": primary_type,
            "secondary-type-list": secondary_types,
            "first-release-date": first_date,
            "release-list": releases or [],
        }
    }


def _browse(rg_ids):
    return {"release-list": [{"release-group": {"id": rid}} for rid in rg_ids]}


class TestMBFirstRelease:

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_acdc_tnt_prefers_studio_over_live(self, mock_browse, mock_rg):
        """AC/DC T.N.T.: studio album (1975) wins over live album (2009) and compilation (2003)."""
        mock_browse.return_value = _browse(["live-rg-id", "studio-rg-id", "compilation-rg-id"])
        rg_map = {
            "live-rg-id": _rg("live-rg-id", "Return of the Phoenix", "Album", ["Live"], "2009-10-20",
                               [{"id": "live-release-id", "status": "Official", "date": "2009-10-20"}]),
            "studio-rg-id": _rg("studio-rg-id", "T.N.T.", "Album", [], "1975-12-01",
                                 [{"id": "studio-release-id", "status": "Official", "date": "1975-12-01"}]),
            "compilation-rg-id": _rg("compilation-rg-id", "Greatest Hits", "Album", ["Compilation"], "2003-06-15"),
        }
        mock_rg.side_effect = lambda rg_id: rg_map[rg_id]

        result = mb_fetch_first_release_for_recording("test-recording-id-acdc")

        assert result is not None
        assert isinstance(result, FirstReleaseAlbum)
        assert result.album_title == "T.N.T."
        assert result.original_release_year == 1975
        assert result.release_category == "studio"
        assert result.release_mbid == "studio-release-id"
        assert result.release_group_mbid == "studio-rg-id"
        assert result.source == "musicbrainz_first_release"

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_led_zeppelin_earliest_studio_release(self, mock_browse, mock_rg):
        """Led Zeppelin Whole Lotta Love: album (1969) wins over single (1970) and compilation (2003)."""
        mock_browse.return_value = _browse(["album-rg-id", "single-rg-id", "best-of-rg-id"])
        rg_map = {
            "album-rg-id": _rg("album-rg-id", "Led Zeppelin II", "Album", [], "1969-10-22",
                                [{"id": "album-release-id", "status": "Official", "date": "1969-10-22"}]),
            "single-rg-id": _rg("single-rg-id", "Whole Lotta Love", "Single", [], "1970-01-01",
                                 [{"id": "single-release-id", "status": "Official", "date": "1970-01-01"}]),
            "best-of-rg-id": _rg("best-of-rg-id", "The Very Best of Led Zeppelin", "Album",
                                  ["Compilation"], "2003-01-01"),
        }
        mock_rg.side_effect = lambda rg_id: rg_map[rg_id]

        result = mb_fetch_first_release_for_recording("test-recording-id-lz")

        assert result is not None
        assert result.album_title == "Led Zeppelin II"
        assert result.original_release_year == 1969
        assert result.original_release_date == "1969-10-22"
        assert result.release_category == "studio"
        assert result.release_mbid == "album-release-id"

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_touch_and_go_album_vs_single(self, mock_browse, mock_rg):
        """Touch and Go: album (1999) is earlier than single (1999-06), album wins."""
        mock_browse.return_value = _browse(["album-rg-id", "single-rg-id"])
        rg_map = {
            "album-rg-id": _rg("album-rg-id", "I Find You Very Attractive", "Album", [], "1999",
                                [{"id": "album-release-id", "status": "Official", "date": "1999"}]),
            "single-rg-id": _rg("single-rg-id", "Tango in Harlem", "Single", [], "1999-06",
                                 [{"id": "single-release-id", "status": "Official", "date": "1999-06"}]),
        }
        mock_rg.side_effect = lambda rg_id: rg_map[rg_id]

        result = mb_fetch_first_release_for_recording("test-recording-id-tng")

        assert result is not None
        assert result.album_title == "I Find You Very Attractive"
        assert result.original_release_year == 1999
        assert result.release_category == "studio"

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_all_live_releases_returns_none(self, mock_browse, mock_rg):
        """When only live releases exist the function returns None (no live fallback implemented)."""
        mock_browse.return_value = _browse(["live-rg-1", "live-rg-2"])
        rg_map = {
            "live-rg-1": _rg("live-rg-1", "Live at Venue A", "Album", ["Live"], "2020-03-15",
                              [{"id": "live-release-1", "status": "Official", "date": "2020-03-15"}]),
            "live-rg-2": _rg("live-rg-2", "Concert Recording", "Live", [], "2019-11-10",
                              [{"id": "live-release-2", "status": "Official", "date": "2019-11-10"}]),
        }
        mock_rg.side_effect = lambda rg_id: rg_map[rg_id]

        result = mb_fetch_first_release_for_recording("test-recording-id-live")

        assert result is None

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_compilation_filtered_out(self, mock_browse, mock_rg):
        """A compilation-only release group returns None."""
        mock_browse.return_value = _browse(["compilation-rg-id"])
        mock_rg.return_value = _rg("compilation-rg-id", "Greatest Hits", "Album",
                                   ["Compilation"], "2003-06-15")

        result = mb_fetch_first_release_for_recording("test-recording-id")

        assert result is None

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_rg_without_release_date_returns_none(self, mock_browse, mock_rg):
        """Release-group with no first-release-date is filtered out, returning None."""
        mock_browse.return_value = _browse(["rg-id"])
        mock_rg.return_value = _rg("rg-id", "Album", "Album", [], "")

        result = mb_fetch_first_release_for_recording("test-id")

        assert result is None

    @patch(PATCH_RG)
    @patch(PATCH_BROWSE)
    def test_no_releases_in_browse_returns_none(self, mock_browse, mock_rg):
        """Empty browse result returns None."""
        mock_browse.return_value = {"release-list": []}
        mock_rg.return_value = {}

        result = mb_fetch_first_release_for_recording("test-id")

        assert result is None
        mock_rg.assert_not_called()

    def test_empty_mbid_returns_none(self):
        """Empty or None recording MBID returns None immediately without any API calls."""
        assert mb_fetch_first_release_for_recording("") is None
        assert mb_fetch_first_release_for_recording(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
