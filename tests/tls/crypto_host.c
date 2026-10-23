/* Arnés en anfitrión para la cripto nativa de net/tls (Erlan compilado a C).
 * Lee una operación por línea en stdin con argumentos en hex ("-" = vacío) y escribe
 * el resultado en hex. scripts/test_tls_host.py genera los vectores y compara con
 * oráculos independientes (OpenSSL vía hashlib y la librería cryptography). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "erlan_rt.h"
#include "sha2.h"
#include "aes.h"
#include "x25519.h"
#include "ecdsa.h"

#define MAXB 70000
static u8 buf_a[MAXB], buf_b[MAXB], buf_c[MAXB], buf_d[MAXB], buf_o[2 * MAXB];
static char line[9 * MAXB];

static size_t unhex(const char *s, u8 *dst) {
    if (!s || strcmp(s, "-") == 0) return 0;
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++) {
        char pair[3] = { s[2 * i], s[2 * i + 1], 0 };
        dst[i] = (u8)strtoul(pair, NULL, 16);
    }
    return n;
}

static void hexout(const u8 *p, size_t n) {
    if (n == 0) putchar('-');
    for (size_t i = 0; i < n; i++) printf("%02x", p[i]);
    putchar('\n');
}

static erlan_su8 sl(u8 *p, u64 n) { erlan_su8 s = { p, n }; return s; }

int main(void) {
    while (fgets(line, sizeof line, stdin)) {
        char *op = strtok(line, " \r\n");
        if (!op) continue;
        char *f[6] = { 0 };
        for (int i = 0; i < 6; i++) f[i] = strtok(NULL, " \r\n");
        if (!strcmp(op, "sha256") || !strcmp(op, "sha384") || !strcmp(op, "sha512")) {
            size_t n = unhex(f[0], buf_a);
            if (!strcmp(op, "sha256")) { sha256_oneshot(sl(buf_a, n), sl(buf_o, 32)); hexout(buf_o, 32); }
            else if (!strcmp(op, "sha384")) { sha384_oneshot(sl(buf_a, n), sl(buf_o, 48)); hexout(buf_o, 48); }
            else { sha512_oneshot(sl(buf_a, n), sl(buf_o, 64)); hexout(buf_o, 64); }
        } else if (!strcmp(op, "hmac")) {
            u64 kind = strtoull(f[0], NULL, 10);
            size_t k = unhex(f[1], buf_a), m = unhex(f[2], buf_b);
            u64 len = hash_len(kind);
            hmac(kind, sl(buf_a, k), sl(buf_b, m), sl(buf_o, len));
            hexout(buf_o, len);
        } else if (!strcmp(op, "hkdfext")) {
            u64 kind = strtoull(f[0], NULL, 10);
            size_t s = unhex(f[1], buf_a), k = unhex(f[2], buf_b);
            u64 len = hash_len(kind);
            hkdf_extract(kind, sl(buf_a, s), sl(buf_b, k), sl(buf_o, len));
            hexout(buf_o, len);
        } else if (!strcmp(op, "hkdfexp")) {
            u64 kind = strtoull(f[0], NULL, 10);
            size_t p = unhex(f[1], buf_a), i = unhex(f[2], buf_b);
            u64 len = strtoull(f[3], NULL, 10);
            hkdf_expand(kind, sl(buf_a, p), sl(buf_b, i), sl(buf_o, len));
            hexout(buf_o, len);
        } else if (!strcmp(op, "label")) {
            u64 kind = strtoull(f[0], NULL, 10);
            size_t s = unhex(f[1], buf_a), l = unhex(f[2], buf_c), c = unhex(f[3], buf_b);
            u64 len = strtoull(f[4], NULL, 10);
            buf_c[l] = 0;
            hkdf_expand_label(kind, sl(buf_a, s), (const char *)buf_c, sl(buf_b, c), sl(buf_o, len));
            hexout(buf_o, len);
        } else if (!strcmp(op, "aes")) {
            size_t k = unhex(f[0], buf_a);
            unhex(f[1], buf_b);
            if (!aes_block_oneshot(sl(buf_a, k), sl(buf_b, 16), sl(buf_o, 16))) printf("FAIL\n");
            else hexout(buf_o, 16);
        } else if (!strcmp(op, "gcmseal")) {
            size_t k = unhex(f[0], buf_a), iv = unhex(f[1], buf_b), ad = unhex(f[2], buf_c), pt = unhex(f[3], buf_d);
            if (!gcm_seal_oneshot(sl(buf_a, k), sl(buf_b, iv), sl(buf_c, ad), sl(buf_d, pt), sl(buf_o, pt + 16))) printf("FAIL\n");
            else hexout(buf_o, pt + 16);
        } else if (!strcmp(op, "gcmopen")) {
            size_t k = unhex(f[0], buf_a), iv = unhex(f[1], buf_b), ad = unhex(f[2], buf_c), ct = unhex(f[3], buf_d);
            if (ct < 16 || !gcm_open_oneshot(sl(buf_a, k), sl(buf_b, iv), sl(buf_c, ad), sl(buf_d, ct), sl(buf_o, ct - 16))) printf("FAIL\n");
            else hexout(buf_o, ct - 16);
        } else if (!strcmp(op, "x25519")) {
            unhex(f[0], buf_a);
            unhex(f[1], buf_b);
            if (!x25519_scalarmult(sl(buf_o, 32), sl(buf_a, 32), sl(buf_b, 32))) printf("ZERO\n");
            else hexout(buf_o, 32);
        } else if (!strcmp(op, "x25519pub")) {
            unhex(f[0], buf_a);
            if (!x25519_public(sl(buf_o, 32), sl(buf_a, 32))) printf("ZERO\n");
            else hexout(buf_o, 32);
        } else if (!strcmp(op, "ecdsa")) {
            /* ecdsa <curva 256|384> <pub x||y> <hash> <r||s> -> OK / FAIL */
            u64 curve = strtoull(f[0], NULL, 10);
            size_t q = unhex(f[1], buf_a), h = unhex(f[2], buf_b), s = unhex(f[3], buf_c);
            printf("%s\n", ecdsa_verify(curve, sl(buf_a, q), sl(buf_b, h), sl(buf_c, s)) ? "OK" : "FAIL");
        } else {
            printf("UNKNOWN\n");
        }
        fflush(stdout);
    }
    return 0;
}
