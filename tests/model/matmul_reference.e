// Reference M3 accumulation order, preserved for numeric/performance comparison.
extern c "umath.h" { fn f16_to_f32(h: u16) -> f32; fn ld_u16(a: u64) -> u16; }
extern c "platform.h" { fn platform_log(s: str); }
const GGML_F32: u32 = 0;
const GGML_F16: u32 = 1;
const GGML_Q8_0: u32 = 8;

fn reference_matmul(out: []f32, xin: []f32, w: u64, wtype: u32, rows: u64, cols: u64) {
    unsafe {
        let xp = xin.ptr;
        if wtype == GGML_Q8_0 {
            let nb = cols / 32;
            let row_bytes = nb * 34;
            for r in 0..rows {
                var sum: f32 = 0.0;
                var bp = (w + r * row_bytes) as ptr<u8>;
                for b in 0..nb {
                    let d = f16_to_f32(ld_u16(bp as u64));
                    let qs = (bp + 2) as ptr<i8>;
                    let xo = xp + b * 32;
                    var acc: f32 = 0.0;
                    for i in 0..32 { acc += (qs[i] as f32) * xo[i]; }
                    sum += d * acc;
                    bp = bp + 34;
                }
                out[r] = sum;
            }
        } else if wtype == GGML_F32 {
            for r in 0..rows {
                let wp = (w + r * cols * 4) as ptr<f32>;
                var sum: f32 = 0.0;
                for c in 0..cols { sum += wp[c] * xp[c]; }
                out[r] = sum;
            }
        } else if wtype == GGML_F16 {
            for r in 0..rows {
                let wp = (w + r * cols * 2) as ptr<u16>;
                var sum: f32 = 0.0;
                for c in 0..cols { sum += f16_to_f32(wp[c]) * xp[c]; }
                out[r] = sum;
            }
        } else { platform_log("llama: tipo de tensor no soportado"); }
    }
}
