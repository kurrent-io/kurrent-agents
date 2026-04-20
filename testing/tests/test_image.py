import pytest

from kurrent_agents_testing import image as image_mod


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("KURRENTDB_IMAGE", "example.com/kurrentdb:override")
    monkeypatch.setattr(image_mod.platform, "machine", lambda: "x86_64")
    assert image_mod.resolve_image() == "example.com/kurrentdb:override"


@pytest.mark.parametrize("machine", ["arm64", "aarch64", "ARM64"])
def test_arm64_hosts(monkeypatch, machine):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: machine)
    assert image_mod.resolve_image() == image_mod._IMAGES["arm64"]


@pytest.mark.parametrize("machine", ["x86_64", "amd64", "AMD64"])
def test_amd64_hosts(monkeypatch, machine):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: machine)
    assert image_mod.resolve_image() == image_mod._IMAGES["amd64"]


def test_unsupported_arch_raises(monkeypatch):
    monkeypatch.delenv("KURRENTDB_IMAGE", raising=False)
    monkeypatch.setattr(image_mod.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="Unsupported CPU arch"):
        image_mod.resolve_image()


def test_image_table_has_both_arches():
    assert set(image_mod._IMAGES) == {"arm64", "amd64"}
    for tag in image_mod._IMAGES.values():
        assert tag.startswith("kurrentplatform/kurrentdb:")
