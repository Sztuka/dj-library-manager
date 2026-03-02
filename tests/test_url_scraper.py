"""Tests for djlib.metadata.url_scraper — URL metadata extraction."""
from __future__ import annotations

import json
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from djlib.metadata.url_scraper import (
    _empty_result,
    _og_to_result,
    _parse_og_regex,
    _parse_sc_title,
    _sc_parse_url_slug,
    _slug_to_name,
    scrape_url,
)


# ── _slug_to_name ────────────────────────────────────────────────────────────


class TestSlugToName:
    def test_basic_slug(self):
        assert _slug_to_name("dj-sacha-music") == "DJ Sacha Music"

    def test_single_word(self):
        assert _slug_to_name("techno") == "Techno"

    def test_dj_prefix(self):
        assert _slug_to_name("dj-snake") == "DJ Snake"

    def test_x_stays_lowercase(self):
        assert _slug_to_name("one-kiss-x-woolfie") == "One Kiss x Woolfie"

    def test_mc_uppercase(self):
        assert _slug_to_name("mc-hammer-live") == "MC Hammer Live"

    def test_vs_uppercase(self):
        assert _slug_to_name("deadmau5-vs-marshmello") == "Deadmau5 VS Marshmello"

    def test_empty_slug(self):
        assert _slug_to_name("") == ""

    def test_ft_uppercase(self):
        assert _slug_to_name("track-ft-singer") == "Track FT Singer"

    def test_vip_capitalized(self):
        assert _slug_to_name("my-song-vip") == "My Song Vip"


# ── _parse_sc_title ──────────────────────────────────────────────────────────


class TestParseScTitle:
    def test_artist_dash_title_remix(self):
        artist, title, version = _parse_sc_title(
            "Dua Lipa - One Kiss (Gregor Salto Remix)", "uploaderX"
        )
        assert artist == "Dua Lipa"
        assert title == "One Kiss"
        assert version == "Gregor Salto Remix"

    def test_artist_dash_title_no_version(self):
        artist, title, version = _parse_sc_title(
            "Bicep - Glue", "uploaderX"
        )
        assert artist == "Bicep"
        assert title == "Glue"
        assert version == ""

    def test_no_dash_uses_uploader(self):
        artist, title, version = _parse_sc_title(
            "One Kiss (Mashup)", "DJ Sacha"
        )
        assert artist == "DJ Sacha"
        assert "One Kiss" in title
        assert version == "Mashup"

    def test_title_with_edit(self):
        artist, title, version = _parse_sc_title(
            "Armin van Buuren - Blah Blah Blah (Extended Edit)", "trance4ever"
        )
        assert artist == "Armin van Buuren"
        assert "Blah Blah Blah" in title
        assert version == "Extended Edit"

    def test_title_with_bootleg(self):
        artist, title, version = _parse_sc_title(
            "Track Name (DJ Snake Bootleg)", "bootlegz"
        )
        assert artist == "bootlegz"
        assert version == "DJ Snake Bootleg"

    def test_kbps_stripped(self):
        artist, title, version = _parse_sc_title(
            "Artist - Title (320kbps)", "user"
        )
        assert "320" not in title
        assert "kbps" not in title.lower()

    def test_free_download_stripped(self):
        artist, title, version = _parse_sc_title(
            "Artist - Cool Track [Free Download]", "user"
        )
        assert "download" not in title.lower()

    def test_em_dash_separator(self):
        artist, title, version = _parse_sc_title(
            "Fred again.. — Delilah (Pull Me Out of This)", "uploader"
        )
        assert artist == "Fred again.."
        assert "Delilah" in title

    def test_en_dash_separator(self):
        artist, title, version = _parse_sc_title(
            "Skrillex – Bangarang", "dubstep"
        )
        assert artist == "Skrillex"
        assert title == "Bangarang"

    def test_empty_title(self):
        artist, title, version = _parse_sc_title("", "user")
        assert artist == "user"
        assert title == ""
        assert version == ""

    def test_mashup_in_title(self):
        artist, title, version = _parse_sc_title(
            "Dua Lipa & Double Touch - One Kiss x Woolfie (Dj Sacha Mashup)",
            "dj-sacha-music",
        )
        assert artist == "Dua Lipa & Double Touch"
        assert "One Kiss" in title
        assert version == "Dj Sacha Mashup"


# ── _sc_parse_url_slug ───────────────────────────────────────────────────────


class TestScParseUrlSlug:
    def test_standard_soundcloud_url(self):
        result = _sc_parse_url_slug(
            "https://soundcloud.com/dj-sacha-music/dua-lipa-double-touch-one-kiss-x-woolfie-dj-sacha-mashup"
        )
        assert result["source"] == "soundcloud"
        assert result["artist"] != ""
        assert result["title"] != ""

    def test_short_url_no_track(self):
        result = _sc_parse_url_slug("https://soundcloud.com/user-only")
        assert result["artist"] == ""
        assert result["title"] == ""

    def test_preserves_url(self):
        url = "https://soundcloud.com/someone/some-track"
        result = _sc_parse_url_slug(url)
        assert result["url"] == url


# ── scrape_url routing ───────────────────────────────────────────────────────


class TestScrapeUrlRouting:
    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="Empty URL"):
            scrape_url("")

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError, match="Unsupported URL"):
            scrape_url("ftp://example.com")

    @patch("djlib.metadata.url_scraper._scrape_soundcloud")
    def test_routes_soundcloud(self, mock_sc):
        mock_sc.return_value = _empty_result("soundcloud")
        scrape_url("https://soundcloud.com/artist/track")
        mock_sc.assert_called_once()

    @patch("djlib.metadata.url_scraper._scrape_beatport")
    def test_routes_beatport(self, mock_bp):
        mock_bp.return_value = _empty_result("beatport")
        scrape_url("https://www.beatport.com/track/some-track/12345")
        mock_bp.assert_called_once()

    @patch("djlib.metadata.url_scraper._scrape_generic")
    def test_routes_youtube(self, mock_gen):
        mock_gen.return_value = _empty_result("youtube")
        scrape_url("https://www.youtube.com/watch?v=abc123")
        mock_gen.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc123", source="youtube"
        )

    @patch("djlib.metadata.url_scraper._scrape_generic")
    def test_routes_youtu_be(self, mock_gen):
        mock_gen.return_value = _empty_result("youtube")
        scrape_url("https://youtu.be/abc123")
        mock_gen.assert_called_once_with(
            "https://youtu.be/abc123", source="youtube"
        )

    @patch("djlib.metadata.url_scraper._scrape_generic")
    def test_routes_1001tracklists(self, mock_gen):
        mock_gen.return_value = _empty_result("1001tracklists")
        scrape_url("https://www.1001tracklists.com/tracklist/xyz.html")
        mock_gen.assert_called_once()

    @patch("djlib.metadata.url_scraper._scrape_generic")
    def test_routes_unknown_to_generic(self, mock_gen):
        mock_gen.return_value = _empty_result("generic")
        scrape_url("https://example.com/some-track")
        mock_gen.assert_called_once_with(
            "https://example.com/some-track", source="generic"
        )


# ── _parse_og_regex ──────────────────────────────────────────────────────────


class TestParseOgRegex:
    def test_standard_og_tags(self):
        html = '''
        <meta property="og:title" content="Artist - Track Name" />
        <meta property="og:description" content="Listen on SoundCloud" />
        <meta property="og:image" content="https://img.example.com/pic.jpg" />
        '''
        og = _parse_og_regex(html)
        assert og["og:title"] == "Artist - Track Name"
        assert og["og:description"] == "Listen on SoundCloud"
        assert og["og:image"] == "https://img.example.com/pic.jpg"

    def test_reverse_order_attrs(self):
        html = '<meta content="My Title" property="og:title" />'
        og = _parse_og_regex(html)
        assert og["og:title"] == "My Title"

    def test_title_fallback(self):
        html = "<title>Some Page Title</title>"
        og = _parse_og_regex(html)
        assert og["og:title"] == "Some Page Title"

    def test_title_fallback_not_used_when_og_present(self):
        html = '''
        <meta property="og:title" content="OG Title" />
        <title>Page Title</title>
        '''
        og = _parse_og_regex(html)
        assert og["og:title"] == "OG Title"

    def test_empty_html(self):
        og = _parse_og_regex("")
        assert og == {}


# ── _og_to_result ────────────────────────────────────────────────────────────


class TestOgToResult:
    def test_basic_og(self):
        og = {
            "og:title": "Great Track",
            "og:description": "A great track",
            "og:image": "https://img.example.com/pic.jpg",
            "og:url": "https://example.com/track",
        }
        result = _og_to_result(og, "generic")
        assert result["title"] == "Great Track"
        assert result["source"] == "generic"
        assert result["artwork_url"] == "https://img.example.com/pic.jpg"
        assert result["url"] == "https://example.com/track"

    def test_ld_data_precedence(self):
        og = {
            "og:title": "Track Title",
            "ld:artist": "LD Artist",
            "ld:genre": "House",
            "ld:datePublished": "2024-03-15",
        }
        result = _og_to_result(og, "beatport")
        assert result["artist"] == "LD Artist"
        assert result["genre"] == "House"
        assert result["year"] == "2024"

    def test_empty_og(self):
        result = _og_to_result({}, "generic")
        assert result["artist"] == ""
        assert result["title"] == ""
        assert result["genre"] == ""
        assert result["year"] == ""


# ── _empty_result ────────────────────────────────────────────────────────────


class TestEmptyResult:
    def test_has_all_keys(self):
        result = _empty_result("soundcloud", url="https://sc.com/x")
        assert result["source"] == "soundcloud"
        assert result["url"] == "https://sc.com/x"
        assert result["artist"] == ""
        assert result["title"] == ""
        assert result["version"] == ""
        assert result["genre"] == ""
        assert result["year"] == ""


# ── Beatport scraping ────────────────────────────────────────────────────────


class TestScrapeBeatport:
    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_beatport_og_title_parsing(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_beatport

        mock_fetch.return_value = {
            "og:title": "One Kiss (Gregor Salto Remix) by Dua Lipa on Beatport",
            "og:image": "https://img.beatport.com/pic.jpg",
        }
        result = _scrape_beatport("https://www.beatport.com/track/one-kiss/123")
        assert result["artist"] == "Dua Lipa"
        assert result["title"] == "One Kiss"
        assert result["version"] == "Gregor Salto Remix"
        assert result["source"] == "beatport"

    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_beatport_no_remix(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_beatport

        mock_fetch.return_value = {
            "og:title": "Strobe by Deadmau5 on Beatport",
        }
        result = _scrape_beatport("https://www.beatport.com/track/strobe/456")
        assert result["artist"] == "Deadmau5"
        assert result["title"] == "Strobe"
        assert result["version"] == ""

    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_beatport_extended_mix(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_beatport

        mock_fetch.return_value = {
            "og:title": "Cola (Extended Mix) by CamelPhat, Elderbrook on Beatport",
        }
        result = _scrape_beatport("https://www.beatport.com/track/cola/789")
        assert result["artist"] == "CamelPhat, Elderbrook"
        assert result["title"] == "Cola"
        assert result["version"] == "Extended Mix"


# ── Generic scraping ─────────────────────────────────────────────────────────


class TestScrapeGeneric:
    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_youtube_artist_dash_title(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_generic

        mock_fetch.return_value = {
            "og:title": "Bicep - Glue (Official Video)",
        }
        result = _scrape_generic("https://youtube.com/watch?v=x", source="youtube")
        assert result["artist"] == "Bicep"
        assert result["title"] == "Glue"
        assert "Official" not in result["title"]

    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_youtube_remix_extraction(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_generic

        mock_fetch.return_value = {
            "og:title": "Above & Beyond - Sun & Moon (Spencer Brown Remix)",
        }
        result = _scrape_generic("https://youtube.com/watch?v=y", source="youtube")
        assert result["artist"] == "Above & Beyond"
        assert result["title"] == "Sun & Moon"
        assert result["version"] == "Spencer Brown Remix"

    @patch("djlib.metadata.url_scraper._fetch_og_tags")
    def test_generic_no_dash_no_artist(self, mock_fetch):
        from djlib.metadata.url_scraper import _scrape_generic

        mock_fetch.return_value = {
            "og:title": "Just A Title",
        }
        result = _scrape_generic("https://example.com", source="generic")
        assert result["title"] == "Just A Title"
        assert result["artist"] == ""


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestApiScrapeUrl:
    @pytest.fixture
    def client(self):
        from djlib.review.server import app

        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_missing_url(self, client):
        resp = client.post(
            "/api/scrape-url",
            data=json.dumps({"track_id": "abc"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert b"Missing url" in resp.data

    def test_no_json_body(self, client):
        resp = client.post("/api/scrape-url")
        assert resp.status_code == 400

    @patch("djlib.metadata.url_scraper._scrape_soundcloud")
    def test_soundcloud_url_returns_result(self, mock_sc, client):
        mock_sc.return_value = {
            "artist": "DJ Test",
            "title": "My Track",
            "version": "Remix",
            "genre": "House",
            "year": "2024",
            "artwork_url": "",
            "source": "soundcloud",
            "description": "",
            "url": "https://soundcloud.com/test/my-track",
        }
        resp = client.post(
            "/api/scrape-url",
            data=json.dumps({"url": "https://soundcloud.com/test/my-track"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["artist"] == "DJ Test"
        assert data["title"] == "My Track"
        assert data["source"] == "soundcloud"

    def test_invalid_url_returns_400(self, client):
        resp = client.post(
            "/api/scrape-url",
            data=json.dumps({"url": "ftp://invalid.com/track"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("djlib.metadata.url_scraper._scrape_generic")
    def test_network_error_returns_502(self, mock_gen, client):
        import requests as req

        mock_gen.side_effect = req.ConnectionError("Could not connect")
        resp = client.post(
            "/api/scrape-url",
            data=json.dumps({"url": "https://example.com/track"}),
            content_type="application/json",
        )
        assert resp.status_code == 502
