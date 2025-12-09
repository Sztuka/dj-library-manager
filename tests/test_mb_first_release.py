"""
Unit tests for MusicBrainz first release resolution.

Tests the mb_fetch_first_release_for_recording() function with JSON fixtures.
No network calls - uses mocked responses.
"""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest

from djlib.metadata.mb_client import mb_fetch_first_release_for_recording, FirstReleaseAlbum


# Test fixtures (MusicBrainz API response format)

ACDC_TNT_FIXTURE = {
    "recording": {
        "id": "test-recording-id-acdc",
        "title": "T.N.T.",
        "release-list": [
            {
                "id": "live-release-id",
                "title": "Return of the Phoenix",
                "status": "Official",
                "date": "2009-10-20",
                "release-group": {
                    "id": "live-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": ["Live"],
                },
            },
            {
                "id": "studio-release-id",
                "title": "T.N.T.",
                "status": "Official",
                "date": "1975-12-01",
                "release-group": {
                    "id": "studio-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": [],
                },
            },
            {
                "id": "compilation-release-id",
                "title": "Greatest Hits",
                "status": "Official",
                "date": "2003-06-15",
                "release-group": {
                    "id": "compilation-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": ["Compilation"],
                },
            },
        ],
    }
}

LED_ZEPPELIN_WHOLE_LOTTA_LOVE_FIXTURE = {
    "recording": {
        "id": "test-recording-id-lz",
        "title": "Whole Lotta Love",
        "release-list": [
            {
                "id": "album-release-id",
                "title": "Led Zeppelin II",
                "status": "Official",
                "date": "1969-10-22",
                "release-group": {
                    "id": "album-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": [],
                },
            },
            {
                "id": "single-release-id",
                "title": "Whole Lotta Love",
                "status": "Official",
                "date": "1970-01-01",
                "release-group": {
                    "id": "single-rg-id",
                    "primary-type": "Single",
                    "secondary-type-list": [],
                },
            },
            {
                "id": "best-of-release-id",
                "title": "The Very Best of Led Zeppelin",
                "status": "Official",
                "date": "2003-01-01",
                "release-group": {
                    "id": "best-of-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": ["Compilation"],
                },
            },
        ],
    }
}

TOUCH_AND_GO_FIXTURE = {
    "recording": {
        "id": "test-recording-id-tng",
        "title": "Tango in Harlem",
        "release-list": [
            {
                "id": "album-release-id",
                "title": "I Find You Very Attractive",
                "status": "Official",
                "date": "1999",
                "release-group": {
                    "id": "album-rg-id",
                    "primary-type": "Album",
                    "secondary-type-list": [],
                },
            },
            {
                "id": "single-release-id",
                "title": "Tango in Harlem",
                "status": "Official",
                "date": "1999-06",
                "release-group": {
                    "id": "single-rg-id",
                    "primary-type": "Single",
                    "secondary-type-list": [],
                },
            },
        ],
    }
}

ALL_LIVE_FIXTURE = {
    "recording": {
        "id": "test-recording-id-live",
        "title": "Live Track",
        "release-list": [
            {
                "id": "live-release-1",
                "title": "Live at Venue A (Live)",
                "status": "Official",
                "date": "2020-03-15",
                "release-group": {
                    "id": "live-rg-1",
                    "primary-type": "Live",
                    "secondary-type-list": [],
                },
            },
            {
                "id": "live-release-2",
                "title": "Concert Recording - Live",
                "status": "Official",
                "date": "2019-11-10",
                "release-group": {
                    "id": "live-rg-2",
                    "primary-type": "Album",
                    "secondary-type-list": ["Live"],
                },
            },
        ],
    }
}


class TestMBFirstRelease:
    """Test suite for MusicBrainz first release resolution."""

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_acdc_tnt_prefers_studio_over_live(self, mock_get_recording):
        """AC/DC - T.N.T. should return studio album (1975) not live album (2009)."""
        mock_get_recording.return_value = ACDC_TNT_FIXTURE

        result = mb_fetch_first_release_for_recording("test-recording-id-acdc")

        assert result is not None
        assert isinstance(result, FirstReleaseAlbum)
        assert result.album_title == "T.N.T."
        assert result.original_release_year == 1975
        assert result.release_category == "studio"
        assert result.release_mbid == "studio-release-id"
        assert result.release_group_mbid == "studio-rg-id"
        assert result.source == "musicbrainz_first_release"

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_led_zeppelin_earliest_studio_release(self, mock_get_recording):
        """Led Zeppelin - Whole Lotta Love should return album (1969) not single (1970)."""
        mock_get_recording.return_value = LED_ZEPPELIN_WHOLE_LOTTA_LOVE_FIXTURE

        result = mb_fetch_first_release_for_recording("test-recording-id-lz")

        assert result is not None
        assert result.album_title == "Led Zeppelin II"
        assert result.original_release_year == 1969
        assert result.original_release_date == "1969-10-22"
        assert result.release_category == "studio"
        assert result.release_mbid == "album-release-id"

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_touch_and_go_album_vs_single(self, mock_get_recording):
        """Touch and Go - Tango in Harlem should return album (1999) as earliest."""
        mock_get_recording.return_value = TOUCH_AND_GO_FIXTURE

        result = mb_fetch_first_release_for_recording("test-recording-id-tng")

        assert result is not None
        assert result.album_title == "I Find You Very Attractive"
        assert result.original_release_year == 1999
        assert result.release_category == "studio"

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_all_live_releases_accepted(self, mock_get_recording):
        """When only live releases exist, should return earliest live release."""
        mock_get_recording.return_value = ALL_LIVE_FIXTURE

        result = mb_fetch_first_release_for_recording("test-recording-id-live")

        assert result is not None
        assert result.original_release_year == 2019  # Earliest live release
        assert result.release_category == "live"
        assert "live" in result.album_title.lower()

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_no_official_releases_returns_none(self, mock_get_recording):
        """When no official releases exist, should return None."""
        fixture = {
            "recording": {
                "id": "test-id",
                "title": "Test",
                "release-list": [
                    {
                        "id": "bootleg-id",
                        "title": "Bootleg",
                        "status": "Bootleg",
                        "date": "2000",
                        "release-group": {
                            "id": "bootleg-rg",
                            "primary-type": "Album",
                            "secondary-type-list": [],
                        },
                    }
                ],
            }
        }
        mock_get_recording.return_value = fixture

        result = mb_fetch_first_release_for_recording("test-id")

        assert result is None

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_no_dates_returns_none(self, mock_get_recording):
        """When releases have no dates, should return None."""
        fixture = {
            "recording": {
                "id": "test-id",
                "title": "Test",
                "release-list": [
                    {
                        "id": "release-id",
                        "title": "Album",
                        "status": "Official",
                        "date": "",  # No date
                        "release-group": {
                            "id": "rg-id",
                            "primary-type": "Album",
                            "secondary-type-list": [],
                        },
                    }
                ],
            }
        }
        mock_get_recording.return_value = fixture

        result = mb_fetch_first_release_for_recording("test-id")

        assert result is None

    def test_empty_mbid_returns_none(self):
        """Empty recording MBID should return None immediately."""
        result = mb_fetch_first_release_for_recording("")
        assert result is None

        result = mb_fetch_first_release_for_recording(None)
        assert result is None

    @patch("djlib.metadata.mb_client._get_recording_by_id_with_releases")
    def test_compilation_filtered_out(self, mock_get_recording):
        """Compilations should be filtered out even with valid data."""
        # AC/DC fixture already tests this, but explicit test for clarity
        mock_get_recording.return_value = ACDC_TNT_FIXTURE

        result = mb_fetch_first_release_for_recording("test-recording-id-acdc")

        # Should not return compilation (2003) even though it exists
        assert result is not None
        assert result.album_title != "Greatest Hits"
        assert result.original_release_year == 1975  # Studio album


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
