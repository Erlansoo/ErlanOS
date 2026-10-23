// GENERADO por scripts/gen_tls_anchors.py desde el almacén de raíces del anfitrión. No editar.
// Anclas de confianza TLS con clave EC P-256/P-384: Name del sujeto (DER) y clave X||Y.
//
// [ 0] DigiCert Global Root G3 | P-384 | vence 2038-01-15 | sha256 31ad6648f8104138c738f39ea4320133393e3a18cc02296ef97c2ac9ef6731d0
// [ 1] GTS Root R4 (cadena verificada por el SO: api.anthropic.com) | P-384 | vence 2028-01-28 | sha256 76b27b80a58027dc3cf1da68dac17010ed93997d0b603e2fadbe85012493b5a7
// [ 2] GlobalSign | P-384 | vence 2038-01-19 | sha256 179fbc148a3dd00fd24ea13458cc43bfa7f59c8182d783a513f6ebec100c8924
// [ 3] ISRG Root X2 | P-384 | vence 2040-09-17 | sha256 69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470
// [ 4] Microsoft ECC Product Root Certificate Authority 2018 | P-384 | vence 2043-02-27 | sha256 caca93b9d23d2b6fa76e8b8471931e0df3ec6f63af3cdbb936c41954a1872326
// [ 5] Microsoft ECC TS Root Certificate Authority 2018 | P-384 | vence 2043-02-27 | sha256 3fd4be8baad2f26e1bde06c7584bb720dd1a972d111f5a4999bc44b08fb4960d
// [ 6] SSL.com Root Certification Authority ECC | P-384 | vence 2041-02-12 | sha256 3417bb06cc6007da1b961c920b8ab4ce3fad820e4aa30b9acbc4a74ebdcebc65
// [ 7] SSL.com TLS ECC Root CA 2022 (cadena verificada por el SO: example.com) | P-384 | vence 2028-12-31 | sha256 ba06d3d3e348fce7478cc84b422d0e638e9e221ef1a0b53adc14cc70e04b8ab8
// [ 8] USERTrust ECC Certification Authority | P-384 | vence 2038-01-18 | sha256 4ff460d54b9c86dabfbcfc5712e0400d2bed3fbc4d4fbdaa86e06adcd2a9ad7a

const ANCHOR_COUNT: u64 = 9;
var anc_blob: [1841]u8;
var anc_subj_off: [9]u64;
var anc_subj_len: [9]u64;
var anc_key_off: [9]u64;
var anc_bits: [9]u64;
var anc_fill: u64 = 0;
var anc_loaded: bool = false;

fn anc_hexval(c: u8) -> u64 {
    let v = c as u64;
    if v >= 97 { return v - 87; }
    if v >= 65 { return v - 55; }
    return v - 48;
}

// Añade los bytes de una cadena hex al blob; devuelve cuántos añadió.
fn anc_put(s: str) -> u64 {
    var n: u64 = 0;
    unsafe {
        while s[2 * n] != 0 {
            anc_blob[anc_fill + n] = ((anc_hexval(s[2 * n]) << 4) | anc_hexval(s[2 * n + 1])) as u8;
            n += 1;
        }
    }
    anc_fill += n;
    return n;
}

fn anchors_load() {
    if anc_loaded { return; }
    // DigiCert Global Root G3
    anc_subj_off[0] = anc_fill;
    anc_subj_len[0] = anc_put("3061310b300906035504061302555331153013060355040a130c446967694365727420496e6331193017060355040b13"
        "107777772e64696769636572742e636f6d3120301e06035504031317446967694365727420476c6f62616c20526f6f74"
        "204733");
    anc_key_off[0] = anc_fill;
    anc_put("dda7d9bb8ab80bfb0b7f21d2f0bebe73f3335d1abc34eadec69bbcd095f6f0ccd00bba615b51467e9e2d9fee8e630c17"
        "ec0770f5cf842e40839ce83f416d3badd3a4145936789d0343ee10136c72deae88a7a16bb543ce67dc23ff031ca3e23e");
    anc_bits[0] = 384;
    // GTS Root R4 (cadena verificada por el SO: api.anthropic.com)
    anc_subj_off[1] = anc_fill;
    anc_subj_len[1] = anc_put("3047310b300906035504061302555331223020060355040a1319476f6f676c6520547275737420536572766963657320"
        "4c4c43311430120603550403130b47545320526f6f74205234");
    anc_key_off[1] = anc_fill;
    anc_put("f37473a7688b60ae43b835c581307b4b499dfbc161cee6de46bd6bd5611835ae40dd73f78991305aeb3cee857ca24076"
        "3ba9c6b847d82ae792916a73e9b172399f299fa298d35f5e5886650fa1846506d1dc8bc9c773c88c6a2fe5c4abd11d8a");
    anc_bits[1] = 384;
    // GlobalSign
    anc_subj_off[2] = anc_fill;
    anc_subj_len[2] = anc_put("305031243022060355040b131b476c6f62616c5369676e2045434320526f6f74204341202d2052353113301106035504"
        "0a130a476c6f62616c5369676e311330110603550403130a476c6f62616c5369676e");
    anc_key_off[2] = anc_fill;
    anc_put("47450e96fb7d5dbfe939d121f89f0bb6d57b1e923a48591cf062312dc07a28fe1aa75cb3b6cc97e745d458fad1776d43"
        "a2c08765340a1f7addeb3c33a1c59d4da46f4195387fc91e84ebd19e49928794870c3a854a669f9d59934d976106864a");
    anc_bits[2] = 384;
    // ISRG Root X2
    anc_subj_off[3] = anc_fill;
    anc_subj_len[3] = anc_put("304f310b300906035504061302555331293027060355040a1320496e7465726e65742053656375726974792052657365"
        "617263682047726f7570311530130603550403130c4953524720526f6f74205832");
    anc_key_off[3] = anc_fill;
    anc_put("cd9bd59f80830aec094af3164a3e5ccf77acde67050d1d07b6dc16fb5a8b14dbe27160c4ba459511898eea06dff72a16"
        "1ca4b9c5c532e003e01e8218388bd745d80a6a6ee60077fb02517d22d80a6e9a5b77dff0fa41ec39dc75ca68070c1fea");
    anc_bits[3] = 384;
    // Microsoft ECC Product Root Certificate Authority 2018
    anc_subj_off[4] = anc_fill;
    anc_subj_len[4] = anc_put("308194310b3009060355040613025553311330110603550408130a57617368696e67746f6e3110300e06035504071307"
        "5265646d6f6e64311e301c060355040a13154d6963726f736f667420436f72706f726174696f6e313e303c0603550403"
        "13354d6963726f736f6674204543432050726f6475637420526f6f7420436572746966696361746520417574686f7269"
        "74792032303138");
    anc_key_off[4] = anc_fill;
    anc_put("c711162a761d568ebeb96265d4c3ceb4f0c330ec8f6dd76e39bcc849ababb8e34378d581065defc77d9fced6b39075de"
        "0cb090de23bac8d13e67e019a91b86311e5f342dee17fd15fb7e278a32a1eac98fc97e18cb2f3b2c487a7da6f40107ac");
    anc_bits[4] = 384;
    // Microsoft ECC TS Root Certificate Authority 2018
    anc_subj_off[5] = anc_fill;
    anc_subj_len[5] = anc_put("30818f310b3009060355040613025553311330110603550408130a57617368696e67746f6e3110300e06035504071307"
        "5265646d6f6e64311e301c060355040a13154d6963726f736f667420436f72706f726174696f6e313930370603550403"
        "13304d6963726f736f66742045434320545320526f6f7420436572746966696361746520417574686f72697479203230"
        "3138");
    anc_key_off[5] = anc_fill;
    anc_put("decdbb7020f12520b494e8d7b43b0f6e87ddabaccf4d402f81336b590918d6870d26239cb48d959d769fa5b90642e6ad"
        "36b2c4b3ae7a3c08d5cb9d3a5e45216c0be320f59bc2dd4433e342b9eaf2284292aafe0c07ca8a13993b6200eddaf335");
    anc_bits[5] = 384;
    // SSL.com Root Certification Authority ECC
    anc_subj_off[6] = anc_fill;
    anc_subj_len[6] = anc_put("307c310b3009060355040613025553310e300c06035504080c0554657861733110300e06035504070c07486f7573746f"
        "6e31183016060355040a0c0f53534c20436f72706f726174696f6e3131302f06035504030c2853534c2e636f6d20526f"
        "6f742043657274696669636174696f6e20417574686f7269747920454343");
    anc_key_off[6] = anc_fill;
    anc_put("456ea950c4a623369e5f288d17cb9622643fdc7a8e1dcc08b3a27124ba8e49b9041b479658ab2d95c8ed9e0835c827eb"
        "898c5358eb628afef05b0f6b315263413b89cdececb68d19d33407dcbbc6067fc24595eccb7fa823e009e981faf347d3");
    anc_bits[6] = 384;
    // SSL.com TLS ECC Root CA 2022 (cadena verificada por el SO: example.com)
    anc_subj_off[7] = anc_fill;
    anc_subj_len[7] = anc_put("304e310b300906035504061302555331183016060355040a0c0f53534c20436f72706f726174696f6e31253023060355"
        "04030c1c53534c2e636f6d20544c532045434320526f6f742043412032303232");
    anc_key_off[7] = anc_fill;
    anc_put("45293573fac2b823ce147da8b14da05b36ee2a2c53c3600935b224662669c0b395d65d9240190ec6a51370f4ef125128"
        "5de7ccbdf93c85c1cf9490c92bce9242585967fd942710648c4f04b14d49e47b4f9bf5e708f80388f7a7c3924b195481");
    anc_bits[7] = 384;
    // USERTrust ECC Certification Authority
    anc_subj_off[8] = anc_fill;
    anc_subj_len[8] = anc_put("308188310b3009060355040613025553311330110603550408130a4e6577204a6572736579311430120603550407130b"
        "4a65727365792043697479311e301c060355040a131554686520555345525452555354204e6574776f726b312e302c06"
        "035504031325555345525472757374204543432043657274696669636174696f6e20417574686f72697479");
    anc_key_off[8] = anc_fill;
    anc_put("1aac545aa9f96823e77ad5246f53c65ad84babc6d5b6d1e67371aedd9cd60c61fddba08903b80514ec57ceee5d3fe221"
        "b3cef7d48a79e0a3837e2d97d061c4f199dc259163ab7f30a3b470e2c7a1339cf3bf2e5c53b15fb37d327f8a34e37979");
    anc_bits[8] = 384;
    anc_loaded = true;
}

fn anchor_count() -> u64 { anchors_load(); return ANCHOR_COUNT; }
fn anchor_bits(i: u64) -> u64 { anchors_load(); return anc_bits[i]; }
fn anchor_subject(i: u64) -> []u8 { anchors_load(); return anc_blob[anc_subj_off[i]..anc_subj_off[i] + anc_subj_len[i]]; }
fn anchor_key(i: u64) -> []u8 {
    anchors_load();
    let n = anc_bits[i] / 4;
    return anc_blob[anc_key_off[i]..anc_key_off[i] + n];
}
