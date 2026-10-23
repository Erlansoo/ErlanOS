#!/usr/bin/env python3
"""Host regression checks for the UEFI raw-image builder; no mounts or QEMU."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from build_boot_image import build_image, verify_image


class BootImageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Path(__file__).resolve().parents[1] / "build" / "boot-image-test"
        base.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=base)
        cls.directory = Path(cls.temp.name)
        cls.output = cls.directory / "test.img"
        cls.payload = bytes(range(256)) * 100
        cls.files = {"EFI/BOOT/BOOTX64.EFI": b"synthetic EFI payload",
                     "boot/limine.conf": b"synthetic config",
                     "boot/a long model name.gguf": cls.payload,
                     "boot/archivo ñáé.txt": b"unicode filename",
                     "boot/empty": b"",
                     "boot/A collision one.txt": b"first",
                     "boot/A collision two.txt": b"second"}
        cls.manifest = build_image(cls.output, cls.files)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_contents_and_names(self):
        verify_image(self.output, self.manifest)
        expected = {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}
        self.assertEqual({f["path"]: f["sha256"] for f in self.manifest["files"]}, expected)

    def corrupt_and_reject(self, offset, match):
        with self.output.open("r+b") as image:
            image.seek(offset)
            original = image.read(1)
            image.seek(offset)
            image.write(bytes([original[0] ^ 1]))
        try:
            with self.assertRaisesRegex(ValueError, match):
                verify_image(self.output, self.manifest)
        finally:
            with self.output.open("r+b") as image:
                image.seek(offset)
                image.write(original)

    def test_primary_gpt_crc(self):
        self.corrupt_and_reject(512+16, "GPT header CRC")

    def test_backup_gpt_crc(self):
        self.corrupt_and_reject(self.output.stat().st_size-512+16, "GPT header CRC")

    def test_file_corruption(self):
        with self.output.open("rb") as image:
            image.seek(2048*512)
            boot = image.read(512)
            fat_sectors = struct.unpack_from("<I", boot, 36)[0]
            start = (2048 + 32 + 2*fat_sectors)*512
            image.seek(start)
            data = image.read(128*1024)
        offset = data.index(self.payload)
        self.corrupt_and_reject(start+offset, "input hashes")

    def test_reject_fat32_oversize(self):
        large = self.directory / "too-large.gguf"
        with large.open("wb") as file:
            file.truncate(2**32)
        with self.assertRaisesRegex(ValueError, "4 GiB"):
            build_image(self.directory / "bad.img", {"model.gguf": large})

    def test_reject_unsafe_or_colliding_paths(self):
        for files in ({"../escape": b"x"}, {"a": b"x", "A": b"y"},
                      {"a/x": b"x", "A/y": b"y"}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                build_image(self.directory / "bad.img", files)


if __name__ == "__main__":
    unittest.main()
