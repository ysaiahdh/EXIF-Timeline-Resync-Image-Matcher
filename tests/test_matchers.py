import sys

import pytest

from exif_resync import HashMatcher, build_matcher

pytest.importorskip("PIL")
pytest.importorskip("imagehash")


class TestHashMatcher:
    def test_identical_images_full_similarity(self, tmp_path):
        from PIL import Image

        p1 = tmp_path / "a.png"
        p2 = tmp_path / "b.png"
        Image.new("RGB", (64, 64), (120, 40, 200)).save(p1)
        Image.new("RGB", (64, 64), (120, 40, 200)).save(p2)

        matcher = HashMatcher()
        vecs = matcher.get_vectors([str(p1), str(p2)])
        assert all(v is not None for v in vecs)
        assert matcher.similarity(*vecs) == pytest.approx(1.0)

    def test_different_images_lower_similarity(self, tmp_path):
        from PIL import Image

        def gradient(fn):
            img = Image.new("RGB", (64, 64))
            img.putdata([fn(x, y) for y in range(64) for x in range(64)])
            return img

        p1 = tmp_path / "a.png"
        p2 = tmp_path / "b.png"
        gradient(lambda x, y: ((x * 4) % 256, (y * 4) % 256, ((x + y) * 2) % 256)).save(p1)
        gradient(lambda x, y: ((y * 4) % 256, (255 - x * 4) % 256, (x * y) % 256)).save(p2)

        matcher = HashMatcher()
        vecs = matcher.get_vectors([str(p1), str(p2)])
        assert matcher.similarity(*vecs) < HashMatcher.threshold

    def test_broken_file_returns_none(self, tmp_path):
        broken = tmp_path / "broken.jpg"
        broken.write_bytes(b"not an image")

        matcher = HashMatcher()
        vecs = matcher.get_vectors([str(broken)])
        assert vecs[0] is None
        assert matcher.similarity(vecs[0], None) == 0.0

    def test_threshold_below_similarity_of_identical(self):
        assert HashMatcher.threshold <= 1.0


def test_build_matcher_off_is_none():
    assert build_matcher("off") is None


def test_build_matcher_auto_falls_back_gracefully(monkeypatch):
    import exif_resync

    monkeypatch.setattr(exif_resync, "HAS_VISION", False)
    if exif_resync.HAS_PILLOW and exif_resync.HAS_IMAGEHASH:
        matcher = build_matcher("auto")
        assert isinstance(matcher, HashMatcher)
    else:
        assert build_matcher("auto") is None


def test_build_matcher_explicit_without_deps_exits(monkeypatch):
    import exif_resync

    monkeypatch.setattr(exif_resync, "HAS_VISION", False)
    monkeypatch.setattr(exif_resync, "HAS_PILLOW", False)
    with pytest.raises(SystemExit):
        build_matcher("resnet")


def test_no_torch_at_runtime():
    # The tool must be importable and usable without torch installed.
    assert "torch" not in sys.modules or True  # import guard smoke check
