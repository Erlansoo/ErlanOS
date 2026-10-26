// D1 block mechanism: one legacy VirtIO block device or, since 2026-10-26, an NVMe
// namespace (nvme.e) or a SATA disk through AHCI (ahci.e) when there is no VirtIO; one
// identified GPT data partition.
// Ring 3 never addresses boot sectors or another disk. No filesystem here.
import cpu;

import mmio;
import pmm;
import pci;
import pci_io;
import kmem;
import serial;
import ahci;
import nvme;
var disk_kind: u64 = 0;   // 1 = VirtIO legado, 2 = AHCI, 3 = NVMe
var disk_io: u16 = 0;
var disk_sectors: u64 = 0;
var disk_queue: u64 = 0; var disk_queue_phys: u64 = 0;
var disk_req: u64 = 0; var disk_req_phys: u64 = 0;
var disk_avail: u64 = 0; var disk_used: u64 = 0; var disk_qsize: u64 = 0;
var disk_index: u16 = 0; var disk_ready: bool = false;
var volume_start: u64 = 0; var volume_sectors: u64 = 0;
var partition_table: [16384]u8;
var disk_sector: [512]u8;

fn block_get(b: []u8,at: u64,n: u64) -> u64 { var v: u64=0; for i in 0..n { v=v|((b[at+i] as u64)<<(i*8)); } return v; }
fn block_crc(b: []u8) -> u64 {
    var crc: u32=0xffffffff;
    for i in 0..b.len { crc=crc^(b[i] as u32); for j in 0..8 { if (crc&1)!=0 { crc=(crc>>1)^0xedb88320; } else { crc=crc>>1; } } }
    return (crc^0xffffffff) as u64;
}
fn disk_desc(i: u64,addr: u64,len: u32,flags: u16,next: u16) {
    let p=disk_queue+i*16; mmio_write64(p,addr); mmio_write32(p+8,len); mmio_write16(p+12,flags); mmio_write16(p+14,next);
}
fn disk_transfer(op: u64,lba: u64,p: ptr<u8>) -> bool {
    if !disk_ready || (op!=4 && lba>=disk_sectors) { return false; }
    if disk_kind==2 { return ahci_transfer(op,lba,p); }
    if disk_kind==3 { return nvme_transfer(op,lba,p); }
    mmio_write32(disk_req,op as u32); mmio_write32(disk_req+4,0); mmio_write64(disk_req+8,lba);
    mmio_write8(disk_req+16,255);
    if op==1 { unsafe { kmemcpy(disk_req+512,p as u64,512); } }
    if op==4 { disk_desc(0,disk_req_phys,16,1,2); }
    else { disk_desc(0,disk_req_phys,16,1,1); var flags: u16=1; if op==0 { flags=3; } disk_desc(1,disk_req_phys+512,512,flags,2); }
    disk_desc(2,disk_req_phys+16,1,2,0);
    mmio_write16(disk_avail+4+((disk_index as u64)%disk_qsize)*2,0);
    cpu_mfence(); disk_index+=1; mmio_write16(disk_avail+2,disk_index); cpu_mfence(); storage_outw(disk_io+16,0);
    var spins: u64=0;
    while mmio_read16(disk_used+2)!=disk_index && spins<20000000 { cpu_pause(); spins+=1; }
    cpu_mfence(); inb(disk_io+19);
    if spins==20000000 { disk_ready=false; kprintf("[storage] timeout: dispositivo deshabilitado\n"); return false; }
    if mmio_read8(disk_req+16)!=0 { return false; }
    if op==0 { unsafe { kmemcpy(p as u64,disk_req+512,512); } }
    return true;
}
fn disk_probe(bus: u64,slot: u64) -> bool {
    if pci_read32(bus,slot,0,0)!=0x10011af4 { return false; }
    let bar=pci_read32(bus,slot,0,16); if (bar&1)==0 || (bar&0xfffffffc)>65504 { return false; }
    disk_io=(bar&0xfffc) as u16; if disk_io==0 { return false; }
    // Only this identified VirtIO device gains IO+bus-master; preserve status W1C bits.
    let command=(pci_read32(bus,slot,0,4)&0xffff)|0x405;
    pci_conf1_write32((0x80000004|(bus<<16)|(slot<<11)) as u32,command);
    outb(disk_io+18,0); outb(disk_io+18,1); outb(disk_io+18,3);
    let features=storage_inl(disk_io);
    if (features&(1<<5))!=0 || (features&(1<<9))==0 { outb(disk_io+18,128); return false; }
    storage_outl(disk_io+4,1<<9); // FLUSH required, no packed ring or indirect descriptors.
    storage_outw(disk_io+14,0); disk_qsize=storage_inw(disk_io+12) as u64;
    if disk_qsize<3 || disk_qsize>256 || (disk_qsize&(disk_qsize-1))!=0 { return false; }
    disk_queue_phys=pmm_alloc_pages(3); disk_req_phys=pmm_alloc_pages(1);
    if disk_queue_phys==0 || disk_req_phys==0 || (disk_queue_phys>>12)>0xffffffff { return false; }
    disk_queue=pmm_phys_to_virt(disk_queue_phys); disk_req=pmm_phys_to_virt(disk_req_phys);
    kmemset(disk_queue,0,12288); kmemset(disk_req,0,4096);
    disk_avail=disk_queue+disk_qsize*16; disk_used=(disk_avail+6+disk_qsize*2+4095)&~(4095 as u64);
    mmio_write16(disk_avail,1); storage_outl(disk_io+8,(disk_queue_phys>>12) as u32);
    disk_sectors=(storage_inl(disk_io+20) as u64)|((storage_inl(disk_io+24) as u64)<<32);
    outb(disk_io+18,7); disk_index=0; disk_ready=true;
    disk_kind=1;
    kprintf("[storage] virtio blk bus=%d slot=%d sectores=%d\n",bus,slot,disk_sectors);
    return true;
}
fn storage_init() {
    volume_sectors=0; disk_ready=false;
    for bus in 0..256 { for slot in 0..32 { if !disk_ready { disk_probe(bus,slot); } } }
    if !disk_ready && nvme_init() { disk_kind=3; disk_sectors=nvme_sectors(); disk_ready=true; kprintf("[storage] nvme sectores=%d\n",disk_sectors); }
    if !disk_ready && ahci_init() { disk_kind=2; disk_sectors=ahci_sectors(); disk_ready=true; kprintf("[storage] ahci sectores=%d\n",disk_sectors); }
    if !disk_ready { kprintf("[storage] sin volumen compatible\n"); return; }
    unsafe { if !disk_transfer(0,1,&disk_sector[0]) { return; } }
    let b=disk_sector[..];
    if block_get(b,0,8)!=0x5452415020494645 || block_get(b,12,4)!=92 || block_get(b,24,8)!=1 || block_get(b,72,8)!=2 || block_get(b,80,4)!=128 || block_get(b,84,4)!=128 { return; }
    let crc=block_get(b,16,4); for i in 16..20 { b[i]=0; }
    if block_crc(b[0..92])!=crc { return; }
    let guid0=block_get(b,56,8); let guid1=block_get(b,64,8); let entries_crc=block_get(b,88,4);
    for i in 0..32 { unsafe { if !disk_transfer(0,2+i,&partition_table[i*512]) { return; } } }
    if block_crc(partition_table[..])!=entries_crc { return; }
    // First partition is the EFI system partition, second is Erlan workspace.
    if block_get(partition_table[..],0,8)!=0x11d2f81fc12a7328 || block_get(partition_table[..],8,8)!=0x3bc93ec9a0004bba { return; }
    if block_get(partition_table[..],128,8)!=0x444e414c52454c45 || block_get(partition_table[..],136,8)!=0x31534b524f57534f { return; }
    let first=block_get(partition_table[..],160,8); let last=block_get(partition_table[..],168,8);
    if first<=block_get(partition_table[..],40,8) || last<first || last>=disk_sectors-33 || last-first+1!=262144 { return; }
    unsafe { if !disk_transfer(0,first,&disk_sector[0]) { return; } }
    if block_get(b,0,8)!=0x314653574e4c5245 || block_get(b,8,8)!=1 || block_get(b,16,8)!=last-first+1 || block_get(b,24,8)!=guid0 || block_get(b,32,8)!=guid1 { return; }
    if block_crc(b[0..508])!=block_get(b,508,4) { return; }
    volume_start=first; volume_sectors=last-first+1;
    kprintf("[storage] volumen Erlan montado lba=%d sectores=%d\n",volume_start,volume_sectors);
}
fn storage_size() -> u64 { if !disk_ready { return 0; } return volume_sectors; }
fn storage_io(op: u64,sector: u64,p: ptr<u8>) -> bool {
    if storage_size()==0 { return false; }
    if op==3 { if sector!=0 { return false; } return disk_transfer(4,0,p); }
    if sector>=volume_sectors || (op!=1 && op!=2) || (op==2 && sector<8) { return false; }
    return disk_transfer(op-1,volume_start+sector,p);
}
