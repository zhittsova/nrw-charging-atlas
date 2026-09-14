"""Project site identity and real WMS thumbnails for the local GeoNode catalogue."""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlsplit

try:
    from scripts.geonode_stack import compose_command, wait_for_http
except ModuleNotFoundError:
    from geonode_stack import compose_command, wait_for_http


def site_identity(site_url: str, site_name: str = "NRW Charging Atlas") -> dict[str, str]:
    parsed = urlsplit(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("SITEURL must be an HTTP(S) URL without credentials")
    if not site_name.strip():
        raise ValueError("NRW_SITE_NAME must not be empty")
    return {"domain": parsed.netloc, "name": site_name.strip()}


def ensure_site(site_url: str, site_name: str = "NRW Charging Atlas") -> bool:
    identity = site_identity(site_url, site_name)
    # The template reads django.contrib.sites, not a SITE_NAME environment
    # variable. Repair Django's seed values while retaining custom site names.
    command = f"""
from django.conf import settings
from django.contrib.sites.models import Site
identity = {identity!r}
site, created = Site.objects.get_or_create(pk=settings.SITE_ID, defaults=identity)
changed = created
for field, value in identity.items():
    if getattr(site, field) in ('', 'example.com', value):
        changed = changed or getattr(site, field) != value
        setattr(site, field, value)
if changed:
    site.save(update_fields=['domain', 'name'])
    Site.objects.clear_cache()
print('NRW_SITE_UPDATED=' + str(int(changed)))
"""
    result = subprocess.run(
        compose_command("exec", "-T", "django", "python", "manage.py", "shell", "-c", command),
        text=True, capture_output=True, check=True, timeout=300,
    )
    return "NRW_SITE_UPDATED=1" in result.stdout


def refresh_thumbnails(layers: list[str]) -> None:
    # Filter by workspace and exact names. Never regenerate unrelated uploads.
    # Use the configured GeoServer credentials for this publication operation.
    # An explicit WMS endpoint avoids a token callback to GeoNode's web workers.
    # Keep the old thumbnail if WMS fails, instead of saving an empty basemap.
    command = f"""
from io import BytesIO
from PIL import Image
from django.conf import settings
from django.utils.module_loading import import_string
from geonode.layers.models import Dataset
from geonode.geoserver.helpers import ogc_server_settings
from geonode.base import bbox_utils
from geonode.thumbs import utils
from geonode.thumbs.thumbnails import _generate_thumbnail_name
names = {json.dumps(layers)}
datasets = Dataset.objects.filter(workspace='nrw', name__in=names).order_by('name')
if datasets.count() != len(names):
    raise RuntimeError('NRW datasets must be synchronized before thumbnail refresh')
width, height = settings.THUMBNAIL_SIZE['width'], settings.THUMBNAIL_SIZE['height']
endpoint = ogc_server_settings.LOCATION.rstrip('/') + '/wms'
Background = import_string(settings.THUMBNAIL_BACKGROUND['class'])
for dataset in datasets:
    bbox = utils.expand_bbox_to_ratio(bbox_utils.clean_bbox(dataset.ll_bbox, 'EPSG:3857'))
    png = utils.get_map(
        endpoint, [dataset.alternate], bbox, wms_version='1.1.1',
        styles=[dataset.default_style.name] if dataset.default_style else None,
        width=width, height=height, max_retries=1,
    )
    if not png:
        raise RuntimeError('WMS thumbnail failed for ' + dataset.alternate)
    with Image.open(BytesIO(png)) as image:
        image.verify()
    with Image.open(BytesIO(png)) as image:
        overlay = image.convert('RGBA')
    thumbnail = Image.new('RGBA', (width, height), (250, 250, 250, 255))
    try:
        background = Background(width, height, max_retries=1).fetch(bbox)
        if background is not None:
            thumbnail.paste(background, (0, 0))
    except Exception as error:
        print('Basemap unavailable for ' + dataset.alternate + ': ' + str(error), flush=True)
    thumbnail = Image.alpha_composite(thumbnail, overlay)
    with BytesIO() as output:
        thumbnail.save(output, format='PNG')
        dataset.save_thumbnail(_generate_thumbnail_name(dataset), image=output.getvalue())
    print('Refreshed ' + dataset.alternate, flush=True)
"""
    subprocess.run(
        compose_command("exec", "-T", "django", "python", "manage.py", "shell", "-c", command),
        check=True, timeout=1800,
    )


def synchronize_presentation(site_url: str, site_name: str, layers: list[str]) -> None:
    changed = ensure_site(site_url, site_name)
    if changed:
        # Each serving process caches Site independently. A management-shell
        # cache clear alone would leave the browser's old title in place.
        subprocess.run(compose_command("restart", "django"), check=True)
        wait_for_http(site_url)
    refresh_thumbnails(layers)
