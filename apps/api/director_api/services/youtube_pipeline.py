"""Optional auto-upload to YouTube after a final export exists (compat re-exports)."""

from director_api.services.publish_youtube import (
    resolve_publish_to_youtube,
    should_youtube_upload,
    try_youtube_auto_upload,
    try_youtube_upload_after_export,
    youtube_upload_metadata,
)

__all__ = [
    "resolve_publish_to_youtube",
    "should_youtube_upload",
    "try_youtube_auto_upload",
    "try_youtube_upload_after_export",
    "youtube_upload_metadata",
]
