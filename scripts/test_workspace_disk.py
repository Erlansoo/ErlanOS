#!/usr/bin/env python3
"""Exercise actual image refresh, identity checks, data preservation and locking."""
import tempfile,hashlib,struct
from pathlib import Path
import workspace_disk as w
with tempfile.TemporaryDirectory(prefix='erlan-workspace-') as directory:
    root=Path(directory); disk=root/'workspace.img';model=root/'model.gguf';model.write_bytes(b'GGUF')
    files={'EFI/BOOT/BOOTX64.EFI':b'boot','boot/limine.conf':b'config','boot/kernel.elf':b'kernel-v1'}
    w.selected_boot_files=lambda *args:files
    with w.DiskLock(disk):
        try:
            with w.DiskLock(disk):raise AssertionError('Second writer accepted')
        except RuntimeError:pass
        first=w.ensure_disk(disk,model);identity=w.inspect(disk);start=identity['first']*512
        with disk.open('r+b') as f:f.seek(start+4096);f.write(b'MI DOCUMENTO PERSISTENTE');f.seek(start+128*1024*1024-512);f.write(b'ULTIMO SECTOR')
        def payload_hash():
            info=w.inspect(disk);h=hashlib.sha256()
            with disk.open('rb') as f:
                f.seek(info['first']*512)
                for _ in range(128):h.update(f.read(1048576))
            return h.digest()
        before=payload_hash();files['boot/kernel.elf']=b'kernel-v2'*10000
        second=w.ensure_disk(disk,model)
        assert second['data_preserved'] and payload_hash()==before
        assert w.inspect(disk)['disk_guid']==identity['disk_guid']
        stamp=disk.stat().st_mtime_ns;w.ensure_disk(disk,model);assert disk.stat().st_mtime_ns==stamp
    bad=root/'bad.img';bad.write_bytes(b'not an Erlan workspace')
    try:w.ensure_disk(bad,model);raise AssertionError('Foreign disk replaced')
    except (ValueError,struct.error):pass
    assert bad.read_bytes()==b'not an Erlan workspace'
print('PASS workspace disk: single writer, refreshed boot, exact preserved data, identity, unchanged fast path, foreign image refusal')
