// G0: human-readable report for the standalone hardware diagnostic image.
extern c "console.h" { fn console_write(p: ptr<u8>, n: u64); }
extern c "pci.h" {
    fn pci_count() -> u64;
    fn pci_target_gpu_count() -> u64;
    fn pci_target_nic_count() -> u64;
    fn pci_display_count() -> u64;
    fn pci_network_count() -> u64;
}

fn hardware_text(s: str) {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } console_write(s as ptr<u8>, n); }
}

fn hardware_number(v: u64) {
    var digits: [20]u8;
    var n: u64 = 20;
    var value = v;
    loop {
        n -= 1;
        digits[n] = 48 + ((value % 10) as u8);
        value /= 10;
        if value == 0 { break; }
    }
    unsafe { console_write(&digits[n], 20 - n); }
}

fn hardware_report() {
    hardware_text("ErlanOS | Diagnostico de hardware G0\n\n");
    hardware_text("Inventario PCI: segmento 0, configuracion convencional.\n");
    hardware_text("Funciones detectadas: "); hardware_number(pci_count());
    hardware_text("\nDispositivos de pantalla: "); hardware_number(pci_display_count());
    hardware_text("\nDispositivos de red: "); hardware_number(pci_network_count());
    hardware_text("\n\nGTX 1050 Ti (10de:1c82): ");
    if pci_target_gpu_count() > 0 { hardware_text("detectada"); }
    else { hardware_text("no detectada"); }
    hardware_text("\nIntel I219-V (8086:15b8): ");
    if pci_target_nic_count() > 0 { hardware_text("detectada"); }
    else { hardware_text("no detectada"); }
    hardware_text("\n\nLa deteccion no significa que exista un controlador.\n");
    hardware_text("GPU de computo e internet: pendientes.\n");
    hardware_text("Detalle de dispositivos y BAR: salida serial.\n\n");
    hardware_text("Diagnostico terminado. Puedes apagar o reiniciar.\n");
}
