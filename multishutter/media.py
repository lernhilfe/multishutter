"""Bounded previews for JPEG, simulation images and embedded Sony RAW thumbnails."""
from io import BytesIO
from pathlib import Path, PurePosixPath


def published_source(root, manifest, take_id, camera_id, record):
    """Resolve a published original even when a session moved into Docker."""
    parts = PurePosixPath(str(record['path']).replace('\\', '/')).parts
    if not parts:
        return None
    expected = (manifest.parent.name, 'takes', str(take_id), camera_id, parts[-1])
    if len(parts) < 5 or tuple(parts[-5:]) != expected:
        return None
    if any(part in {'', '.', '..'} or '/' in part or '\\' in part for part in expected):
        return None
    source = (root / Path(*expected)).resolve()
    return source if source.is_relative_to(root.resolve()) and source.is_file() else None


def open_image(path):
    from PIL import Image, ImageOps
    path = Path(path)
    if path.suffix.lower() in {".arw", ".cr2", ".cr3", ".nef", ".dng"}:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                image = Image.open(BytesIO(thumb.data))
                image.load()
            else:
                image = Image.fromarray(thumb.data)
    else:
        image = Image.open(path)
        image.load()
    return ImageOps.exif_transpose(image).convert("RGB")


def make_preview(path):
    path = Path(path)
    destination = path.with_name(path.name + ".preview.jpg")
    if not destination.exists():
        image = open_image(path)
        image.thumbnail((1600, 1200))
        temporary = destination.with_suffix(".part")
        image.save(temporary, format="JPEG", quality=85)
        temporary.replace(destination)
    return destination
