from director_api.providers.storyblocks_client import SEARCH_RESOURCE, storyblocks_hmac_hex


def test_storyblocks_hmac_matches_documented_algorithm() -> None:
    """Vector from Storyblocks API doc sample (PHP hash_hmac sha256, key = private + EXPIRES)."""
    private = "fedcba09876543210"
    expires = 1234567890
    assert (
        storyblocks_hmac_hex(private_key=private, expires=expires, resource=SEARCH_RESOURCE)
        == "ee598daef26439be67656b32de9e2038cc701aa3fad9d740aafa381843f9c4e9"
    )


def test_download_path_resource_distinct_from_search() -> None:
    from director_api.providers.storyblocks_client import _download_resource_path

    assert _download_resource_path(9328, 1234) == "/api/v1/stock-items/download/9328/1234"
