#!/usr/bin/env python3
"""Build/update the single ErlanOS boot disk, preserving its workspace partition.

Only regular .img files are accepted. Never opens a physical device. A new image
is verified and atomically replaces the old one only after user data was copied.
"""
from __future__ import annotations
import hashlib, json, os, shutil, struct, uuid, zlib
from pathlib import Path
from build_boot_image import build_image, selected_boot_files, _gpt_header, verify_image
SECTOR=512
TYPE=b'ELERLANDOSWORKS1'
MAGIC=b'ERLNWSF1'
DATA_SECTORS=262144

def inspect(path: Path):
    if path.suffix!='.img' or not path.is_file(): raise ValueError('A regular .img disk is required')
    with path.open('rb') as f:
        f.seek(512); h=bytearray(f.read(512))
        if h[:8]!=b'EFI PART' or struct.unpack_from('<I',h,12)[0]!=92: raise ValueError('Invalid GPT')
        crc=struct.unpack_from('<I',h,16)[0]; struct.pack_into('<I',h,16,0)
        if zlib.crc32(h[:92])!=crc: raise ValueError('GPT CRC mismatch')
        total=path.stat().st_size//512
        if struct.unpack_from('<QQ',h,24)!=(1,total-1) or struct.unpack_from('<QII',h,72)!=(2,128,128): raise ValueError('Unsupported GPT geometry')
        f.seek(1024); entries=f.read(16384)
        if len(entries)!=16384 or zlib.crc32(entries)!=struct.unpack_from('<I',h,88)[0]: raise ValueError('GPT entries corrupt')
        if entries[128:144]!=TYPE: raise ValueError('No Erlan workspace partition; refusing replacement')
        f.seek((total-1)*512); backup=bytearray(f.read(512))
        backup_crc=struct.unpack_from('<I',backup,16)[0];struct.pack_into('<I',backup,16,0)
        if backup[:8]!=b'EFI PART' or zlib.crc32(backup[:92])!=backup_crc or backup[56:72]!=h[56:72]: raise ValueError('Invalid backup GPT')
        f.seek((total-33)*512)
        if f.read(16384)!=entries: raise ValueError('GPT copies disagree')
        first,last=struct.unpack_from('<QQ',entries,160)
        if last-first+1!=DATA_SECTORS or first<=struct.unpack_from('<Q',entries,40)[0] or (last+34)*512>path.stat().st_size:
            raise ValueError('Invalid workspace bounds')
        f.seek(first*512); superblock=f.read(512)
        if superblock[:8]!=MAGIC or struct.unpack_from('<QQ',superblock,8)!=(1,DATA_SECTORS) or superblock[24:40]!=h[56:72] or zlib.crc32(superblock[:508])!=struct.unpack_from('<I',superblock,508)[0]:
            raise ValueError('Invalid workspace identity')
        return {'first':first,'last':last,'disk_guid':bytes(h[56:72]),'partition_guid':entries[144:160]}

def source_hashes(model,config=None):
    files=selected_boot_files(model,config)
    def digest(src):
        if isinstance(src,bytes): return hashlib.sha256(src).hexdigest()
        with src.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
    return {name:digest(src) for name,src in files.items()}

def ensure_disk(path: Path,model: Path,config=None):
    path=path.resolve(); model=model.resolve()
    if path.suffix!='.img' or str(path).startswith('\\\\.\\'): raise ValueError('Only a regular .img is supported')
    path.parent.mkdir(parents=True,exist_ok=True)
    manifest_path=path.with_suffix('.workspace.json')
    sources=source_hashes(model,config)
    prior=inspect(path) if path.exists() else None
    if prior and manifest_path.exists():
        previous=json.loads(manifest_path.read_text())
        if previous.get('sources')==sources: return previous
    temporary=path.with_name(path.stem+'.next.img')
    manifest=build_image(temporary,selected_boot_files(model,config))
    if {item['path']:item['sha256'] for item in manifest['files']}!=sources:
        raise ValueError('Boot sources changed while building; previous workspace preserved')
    first=((temporary.stat().st_size//512+2047)//2048)*2048
    last=first+DATA_SECTORS-1; total=last+34
    disk_guid=prior['disk_guid'] if prior else uuid.uuid4().bytes_le
    data_guid=prior['partition_guid'] if prior else uuid.uuid4().bytes_le
    digest=hashlib.sha256()
    with temporary.open('r+b') as out:
        out.seek(1024); entries=bytearray(out.read(16384))
        struct.pack_into('<16s16sQQQ72s',entries,128,TYPE,data_guid,first,last,0,'Erlan Workspace'.encode('utf-16-le').ljust(72,b'\0'))
        out.truncate(total*512)
        if prior:
            with path.open('rb') as old:
                old.seek(prior['first']*512); out.seek(first*512)
                for _ in range(DATA_SECTORS//2048):
                    block=old.read(1048576)
                    if len(block)!=1048576: raise ValueError('Truncated previous workspace')
                    digest.update(block); out.write(block)
        else:
            sb=bytearray(512); sb[:8]=MAGIC
            struct.pack_into('<QQ',sb,8,1,DATA_SECTORS); sb[24:40]=disk_guid
            struct.pack_into('<I',sb,508,zlib.crc32(sb[:508])); out.seek(first*512); out.write(sb)
            # Newly extended file contains zeros: no hidden folders or sample projects.
        crc=zlib.crc32(entries)
        for at,data in ((1024,entries),((total-33)*512,entries),
                        (512,_gpt_header(1,total-1,2,total,disk_guid,crc)),
                        ((total-1)*512,_gpt_header(total-1,1,total-33,total,disk_guid,crc))):
            out.seek(at); out.write(data)
        out.seek(458); out.write(struct.pack('<I',min(total-1,0xffffffff)))
        out.flush(); os.fsync(out.fileno())
    inspect(temporary)
    verify_image(temporary,manifest)
    if prior:
        after=hashlib.sha256()
        with temporary.open('rb') as f:
            f.seek(first*512)
            for _ in range(DATA_SECTORS//2048): after.update(f.read(1048576))
        if after.digest()!=digest.digest(): raise ValueError('Workspace changed while copying')
    temporary.replace(path)
    result={'format':'ErlanOS GPT + workspace D1','image':str(path),'sources':sources,
            'partition_first':first,'partition_sectors':DATA_SECTORS,'bytes':total*512,
            'disk_guid':str(uuid.UUID(bytes_le=disk_guid)),'data_preserved':bool(prior),
            'boot_files':manifest['files']}
    manifest_path.write_text(json.dumps(result,indent=2)+'\n')
    return result

class DiskLock:
    def __init__(self,path): self.path=Path(str(path)+'.lock'); self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); self.file=self.path.open('a+b'); self.file.seek(0)
        self.file.write(b'\0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt; msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl; fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); raise RuntimeError('Este espacio de trabajo ya esta abierto en otra sesion')
        return self
    def __exit__(self,*args): self.file.close()
