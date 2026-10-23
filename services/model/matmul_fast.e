// Erlan Q20 specialization. Compile ONLY this module with -O3 -mavx2 -mno-fma.
// Caller checks CPU/OS support and Q8_0 dimensions; math matches the portable path.
extern c "umath.h" { fn f16_to_f32(h: u16) -> f32; fn ld_u16(a: u64) -> u16; }

fn matmul_q20_fast(out: []f32, xin: []f32, w: u64, rows: u64, cols: u64, quant: []i32, scales: []f32) {
    let nb = cols / 32;
    unsafe {
        let xp = xin.ptr;
        for b in 0..nb {
            let off = b * 32;
            var mx: f32 = 0.0;
            for i in 0..32 {
                var a = xp[off + i];
                if a < 0.0 { a = -a; }
                if a > mx { mx = a; }
            }
            scales[b] = mx / 524287.0;
            for i in 0..32 {
                var q: i32 = 0;
                if mx > 0.0 {
                    let scaled = (xp[off + i] / mx) * 524287.0;
                    if scaled >= 0.0 { q = (scaled + 0.5) as i32; }
                    else { q = (scaled - 0.5) as i32; }
                    if q > 524287 { q = 524287; }
                    if q < -524287 { q = -524287; }
                }
                quant[off + i] = q;
            }
        }
        let row_bytes = nb * 34;
        for r in 0..rows {
            var sum: f32 = 0.0;
            var bp = (w + r * row_bytes) as ptr<u8>;
            for b in 0..nb {
                let d = f16_to_f32(ld_u16(bp as u64));
                let qs = (bp + 2) as ptr<i8>;
                let off = b * 32;
                var acc: i32 = 0;
                for i in 0..32 { acc += (qs[i] as i32) * quant[off + i]; }
                sum += (d * scales[b]) * (acc as f32);
                bp = bp + 34;
            }
            out[r] = sum;
        }
    }
}
