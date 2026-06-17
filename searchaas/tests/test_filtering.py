"""
Unit tests for the filter sanitizer and the index-definition-derived
allowlists.

These cover the building blocks of the fix in isolation:
  * `searchaas.filtering.sanitize_filters`
  * `AtlasConfig.filter_fields` / `AtlasConfig.search_filter_fields`
"""
from __future__ import annotations

import logging

import pytest

from searchaas.config.loader import AtlasConfig, load_config
from searchaas.filtering import sanitize_filters

_URI = "mongodb+srv://user:pass@cluster.mongodb.net"


# --------------------------------------------------------------------------- #
# sanitize_filters
# --------------------------------------------------------------------------- #
def test_sanitize_keeps_only_allowlisted_keys():
    log = logging.getLogger("test")
    out = sanitize_filters(
        {"file_name": "x.pdf", "company": "Amazon", "page_number": 3},
        ["file_name", "page_number"],
        log=log,
    )
    assert out == {"file_name": "x.pdf", "page_number": 3}


def test_sanitize_drops_everything_when_allowlist_empty():
    log = logging.getLogger("test")
    out = sanitize_filters({"company": "Amazon"}, [], log=log)
    assert out == {}


@pytest.mark.parametrize("empty", [None, {}])
def test_sanitize_noop_on_empty_filters(empty):
    log = logging.getLogger("test")
    assert sanitize_filters(empty, ["file_name"], log=log) == {}


def test_sanitize_handles_none_allowlist():
    log = logging.getLogger("test")
    assert sanitize_filters({"company": "x"}, None, log=log) == {}


def test_sanitize_logs_dropped_fields(caplog):
    log = logging.getLogger("searchaas.test.filtering")
    with caplog.at_level(logging.WARNING):
        sanitize_filters({"company": "Amazon", "year": 2022}, ["file_name"], log=log)
    assert "Dropping non-filterable metadata filter(s)" in caplog.text
    # Both invented fields should be named in the warning.
    assert "company" in caplog.text
    assert "year" in caplog.text


def test_sanitize_no_warning_when_nothing_dropped(caplog):
    log = logging.getLogger("searchaas.test.filtering")
    with caplog.at_level(logging.WARNING):
        out = sanitize_filters({"file_name": "x.pdf"}, ["file_name"], log=log)
    assert out == {"file_name": "x.pdf"}
    assert "Dropping" not in caplog.text


def test_sanitize_drops_boolean_operator_keys():
    """`$and`/`$or` cannot be cheaply validated, so they are dropped."""
    log = logging.getLogger("test")
    out = sanitize_filters(
        {"$or": [{"file_name": "a"}], "file_name": "b"},
        ["file_name"],
        log=log,
    )
    assert out == {"file_name": "b"}


# --------------------------------------------------------------------------- #
# AtlasConfig.filter_fields (from the vector index definition)
# --------------------------------------------------------------------------- #
def test_filter_fields_extracted_from_vector_definition():
    cfg = AtlasConfig(
        uri=_URI,
        vector_index_definition={
            "fields": [
                {"type": "vector", "path": "embedding", "numDimensions": 1024},
                {"type": "filter", "path": "file_name"},
                {"type": "filter", "path": "page_number"},
            ]
        },
    )
    assert cfg.filter_fields == ["file_name", "page_number"]


def test_filter_fields_empty_when_only_vector_field():
    cfg = AtlasConfig(
        uri=_URI,
        vector_index_definition={
            "fields": [{"type": "vector", "path": "embedding", "numDimensions": 1024}]
        },
    )
    assert cfg.filter_fields == []


def test_filter_fields_empty_when_no_definition():
    cfg = AtlasConfig(uri=_URI)
    assert cfg.filter_fields == []


# --------------------------------------------------------------------------- #
# AtlasConfig.search_filter_fields (from the Lucene search index definition)
# --------------------------------------------------------------------------- #
def test_search_filter_fields_none_for_dynamic_mapping():
    cfg = AtlasConfig(
        uri=_URI,
        search_index_definition={"mappings": {"dynamic": True}},
    )
    # None == "every field is filterable, no restriction".
    assert cfg.search_filter_fields is None


def test_search_filter_fields_lists_static_mapped_fields():
    cfg = AtlasConfig(
        uri=_URI,
        search_index_definition={
            "mappings": {
                "dynamic": False,
                "fields": {"raw_text": {"type": "string"}, "file_name": {"type": "token"}},
            }
        },
    )
    assert cfg.search_filter_fields == ["file_name", "raw_text"]


def test_search_filter_fields_empty_when_no_definition():
    cfg = AtlasConfig(uri=_URI)
    assert cfg.search_filter_fields == []


# --------------------------------------------------------------------------- #
# The real shipped config (searchaas.yaml)
# --------------------------------------------------------------------------- #
def test_shipped_config_filter_fields_match_yaml(monkeypatch):
    """`atlas.filter_fields` is derived from `vector_index_definition`.

    The shipped YAML currently uses Mode A (client-side voyageai) and does
    NOT declare any `type: filter` fields (the examples are commented out).
    This test guards against accidental drift between the YAML and the
    `filter_fields` property — if the YAML adds filter paths, update this
    expectation to match.
    """
    monkeypatch.setenv("ATLAS_URI", _URI)
    monkeypatch.setenv("ATLAS_DB", "searchaas")
    load_config.cache_clear()
    cfg = load_config()
    load_config.cache_clear()
    # Mode A YAML declares only the `type: vector` field; no filters.
    assert cfg.atlas.filter_fields == []
