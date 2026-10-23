// Funciones matemáticas del model runtime, en Erlan (sin libm): exp, ln, sin, cos.
// Precisión suficiente para inferencia en f32.

extern c "umath.h" {
    fn f32_from_bits(b: u32) -> f32;
    fn f32_bits(f: f32) -> u32;
    fn f32_sqrt(x: f32) -> f32;
}

const LN2: f32 = 0.6931471805599453;
const LOG2E: f32 = 1.4426950408889634;
const PI: f32 = 3.14159265358979;
const TWO_PI: f32 = 6.28318530717959;

// exp(x) = 2^(x*log2e) = 2^n * 2^f, f en [-0.5, 0.5], 2^f por polinomio.
fn mx_exp(x: f32) -> f32 {
    var v = x;
    if v > 88.0 { v = 88.0; }
    if v < -87.0 { return 0.0; }
    let t = v * LOG2E;
    var n = (t + 0.5) as i32;
    if t + 0.5 < 0.0 { n = ((t + 0.5) as i32) - 1; }
    let f = t - (n as f32);                  // [-0.5, 0.5]
    let z = f * LN2;                         // 2^f = e^z
    // e^z, |z| <= 0.35: Taylor grado 6
    var p: f32 = 1.0 + z * (1.0 + z * (0.5 + z * (0.16666667 + z * (0.041666668 + z * (0.008333334 + z * 0.0013888889)))));
    let bits = ((n + 127) as u32) << 23;
    return p * f32_from_bits(bits);
}

// ln(x) para x > 0: descomponer en mantisa y exponente, serie en (m-1)/(m+1).
fn mx_ln(x: f32) -> f32 {
    if x <= 0.0 { return -87.0; }
    let b = f32_bits(x);
    var e = ((b >> 23) & 0xFF) as i32;
    e -= 127;
    let m = f32_from_bits((b & 0x007FFFFF) | 0x3F800000);   // [1, 2)
    // ln(m) = 2 * atanh((m-1)/(m+1))
    let u = (m - 1.0) / (m + 1.0);
    let u2 = u * u;
    let s = 2.0 * u * (1.0 + u2 * (0.33333334 + u2 * (0.2 + u2 * (0.14285715 + u2 * (0.11111111 + u2 * 0.09090909)))));
    return s + (e as f32) * LN2;
}

fn mx_pow(base: f32, expo: f32) -> f32 { return mx_exp(expo * mx_ln(base)); }

// Reduce a [-pi, pi] y evalúa Taylor de grado 11.
fn mx_sin(x: f32) -> f32 {
    var v = x;
    if v > PI || v < -PI {
        let k = (v / TWO_PI + 0.5) as i32;
        var kk = k;
        if v / TWO_PI + 0.5 < 0.0 { kk = k - 1; }
        v = v - (kk as f32) * TWO_PI;
    }
    let z = v * v;
    return v * (1.0 - z * (0.16666667 - z * (0.008333334 - z * (0.00019841270 - z * (0.0000027557319 - z * 0.000000025052108)))));
}

fn mx_cos(x: f32) -> f32 { return mx_sin(x + PI * 0.5); }

fn mx_silu(x: f32) -> f32 { return x / (1.0 + mx_exp(-x)); }

fn mx_sqrt(x: f32) -> f32 { return f32_sqrt(x); }

fn mx_abs(x: f32) -> f32 { if x < 0.0 { return -x; } return x; }
