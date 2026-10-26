// Programación de sistemas en Erlan (docs/ERLAN-SISTEMA.md): import, asm, varargs,
// literales de array, atributos y direcciones de función. Se compila y corre en el host.
extern c "stdio.h" { fn printf(fmt: str, ...) -> i32; }
import util;

// Tabla de datos definida en C (como las fuentes generadas).
extern c "sistema_datos.h" { let TABLA: [4]u8; }

@copy struct Config { nombres: [3]u8; limites: [2]u64; }

const LIMITES: [3]u64 = [1, 2, 3];
var contador: [4]u64 = [10, 20, 30, 40];
@used @volatile var marca: [2]u64 = [0xf9562b2d5c95a6c8, 0x6a7b384944536bdc];

// Función variádica propia: suma n enteros de 64 bits.
fn suma_var(n: u64, ...) -> u64 {
    var total: u64 = 0;
    vararg_start();
    for i in 0..n { total += vararg_u64(); }
    vararg_end();
    return total;
}

fn rdtsc() -> u64 {
    var lo: u32 = 0; var hi: u32 = 0;
    unsafe { asm("rdtsc" : "=a"(lo), "=d"(hi)); }
    return ((hi as u64) << 32) | (lo as u64);
}

fn cpuid_vendor(out: &mut [12]u8) {
    var b: u32 = 0; var c: u32 = 0; var d: u32 = 0; var a: u32 = 0;
    unsafe { asm("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0), "c"(0)); }
    for i in 0..4 { out[i] = ((b >> (8 * (i as u32))) & 0xFF) as u8; out[4 + i] = ((d >> (8 * (i as u32))) & 0xFF) as u8; out[8 + i] = ((c >> (8 * (i as u32))) & 0xFF) as u8; }
}

fn saludo() -> i64 { return 7; }

@noreturn fn morir(codigo: i32) {
    printf("morir(%d)\n", codigo);
    unsafe { asm("" ::: "memory"); }
    loop { }
}

fn main() -> i32 {
    let p = Par { a: 2, b: 3 };
    printf("util: %lld %s\n", par_suma(p), modo_nombre(Modo.Rapido));
    var cfg: Config = Config { nombres: [65, 66, 67], limites: [5, 6] };
    cfg.limites[1] += LIMITES[2];
    printf("arrays: %c%c%c %llu %llu %llu %llu\n", cfg.nombres[0] as i32, cfg.nombres[1] as i32, cfg.nombres[2] as i32,
           cfg.limites[0], cfg.limites[1], contador[3], marca[1] & 0xFF);
    printf("tabla: %u %u %u %u\n", TABLA[0] as u32, TABLA[1] as u32, TABLA[2] as u32, TABLA[3] as u32);
    printf("varargs: %llu\n", suma_var(3, 100, 20, 3));
    let t0 = rdtsc(); let t1 = rdtsc();
    var vendor: [12]u8;
    cpuid_vendor(&mut vendor);
    var dir: u64 = 0;
    unsafe { dir = saludo as u64; }
    printf("asm: tsc crece=%d vendor=%c%c%c dir!=0=%d\n", (t1 >= t0) as i32, vendor[0] as i32, vendor[1] as i32, vendor[2] as i32, (dir != 0) as i32);
    return 0;
}
