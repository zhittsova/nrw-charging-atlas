from unittest.mock import Mock, patch
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import geonode_presentation as presentation  # noqa: E402


def test_site_identity_uses_public_host_and_port_without_path():
    assert presentation.site_identity("https://atlas.test:8443/catalogue/") == {
        "domain": "atlas.test:8443", "name": "NRW Charging Atlas",
    }


@pytest.mark.parametrize("url", ["localhost:8000", "ftp://atlas.test", "https://user:secret@atlas.test"])
def test_invalid_site_urls_are_rejected(url):
    with pytest.raises(ValueError):
        presentation.site_identity(url)


def test_site_repair_preserves_custom_identity_and_is_idempotent():
    import sys
    fake_site = Mock(domain="example.com")
    fake_site.name = "A custom catalogue"
    manager = Mock()
    manager.get_or_create.return_value = fake_site, False
    django_modules = {
        "django.conf": Mock(settings=Mock(SITE_ID=1)),
        "django.contrib.sites.models": Mock(Site=Mock(objects=manager)),
    }

    def shell(command, **kwargs):
        with patch.dict(sys.modules, django_modules):
            exec(command[-1], {})
        return Mock(stdout="NRW_SITE_UPDATED=0")

    with patch.object(presentation.subprocess, "run", side_effect=shell):
        presentation.ensure_site("http://localhost:8000/")
        assert fake_site.domain == "localhost:8000"
        assert fake_site.name == "A custom catalogue"
        fake_site.save.assert_called_once()
        presentation.ensure_site("http://localhost:8000/")
        fake_site.save.assert_called_once()


@pytest.mark.parametrize("changed", [True, False])
def test_only_changed_site_identity_reloads_cached_serving_processes(changed):
    with (
        patch.object(presentation, "ensure_site", return_value=changed),
        patch.object(presentation, "refresh_thumbnails") as thumbnails,
        patch.object(presentation.subprocess, "run") as run,
        patch.object(presentation, "wait_for_http") as wait,
    ):
        presentation.synchronize_presentation("http://localhost:8000/", "NRW Charging Atlas", ["nrw_chargers"])
    thumbnails.assert_called_once_with(["nrw_chargers"])
    assert run.call_count == int(changed)
    assert wait.call_count == int(changed)
    if changed:
        assert run.call_args.args[0][-2:] == ["touch", "/usr/src/geonode/geonode/wsgi.py"]


@pytest.mark.parametrize("invalid_image", [False, True])
def test_failed_wms_keeps_the_previous_thumbnail(invalid_image):
    from types import SimpleNamespace

    dataset = SimpleNamespace(
        alternate="nrw:nrw_grid_readiness", ll_bbox=[5.8, 9.7, 50.2, 52.8, "EPSG:4326"],
        default_style=None, save_thumbnail=Mock(),
    )

    class Datasets(list):
        def count(self):
            return len(self)

        def order_by(self, key):
            return self

    utils = Mock()
    utils.get_map.return_value = b"not an image" if invalid_image else None
    image_api = Mock()
    image_api.open.side_effect = ValueError("invalid image")
    modules = {
        "PIL": Mock(Image=image_api),
        "django.conf": Mock(settings=SimpleNamespace(
            THUMBNAIL_SIZE={"width": 500, "height": 200},
            THUMBNAIL_BACKGROUND={"class": "test.Background"},
        )),
        "django.utils.module_loading": Mock(),
        "geonode.layers.models": Mock(Dataset=Mock(objects=Mock(
            filter=Mock(return_value=Datasets([dataset]))))),
        "geonode.geoserver.helpers": Mock(ogc_server_settings=SimpleNamespace(
            LOCATION="http://geoserver:8080/geoserver/")),
        "geonode.base": Mock(),
        "geonode.thumbs": Mock(utils=utils),
        "geonode.thumbs.thumbnails": Mock(),
    }

    def shell(command, **kwargs):
        with patch.dict(sys.modules, modules):
            exec(command[-1], {})

    with patch.object(presentation.subprocess, "run", side_effect=shell):
        with pytest.raises((RuntimeError, ValueError)):
            presentation.refresh_thumbnails(["nrw_grid_readiness"])
    dataset.save_thumbnail.assert_not_called()
    assert utils.get_map.call_args.args[0] == "http://geoserver:8080/geoserver/wms"
