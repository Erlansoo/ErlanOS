#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ai_wire.h"

int main(void) {
    u8 bytes[48] = {0};
    erlan_su8 request = {bytes, 40};
    ai_encode(request, 0x0102030405060708ull, 2);
    const u8 golden[40] = {
        1,0,0,0,0,0,0,0, 8,7,6,5,4,3,2,1,
        2,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0
    };
    assert(memcmp(bytes, golden, 40) == 0);
    assert(ai_get64(request, 8) == 0x0102030405060708ull);
    assert(ai_validate(request, 0) == 0);
    assert(ai_validate(request, 0x0102030405060708ull) == 3);
    for (u64 op = 1; op <= 4; ++op) {
        ai_encode(request, 10, op);
        assert(ai_validate(request, 9) == 0);
        assert(ai_validate(request, 10) == 3);
    }
    ai_put64(request, 8, 0);
    assert(ai_validate(request, 0) == 3);
    ai_encode(request, 10, 0);
    assert(ai_validate(request, 0) == 4);
    ai_put64(request, 16, 5);
    assert(ai_validate(request, 0) == 4);
    ai_encode(request, 10, 2);
    ai_put64(request, 0, 2);
    assert(ai_validate(request, 0) == 2);
    ai_put64(request, 0, 1);
    ai_put64(request, 24, 1);
    assert(ai_validate(request, 0) == 5);
    ai_put64(request, 24, 0);
    ai_put64(request, 32, 1);
    assert(ai_validate(request, 0) == 5);
    for (u64 n = 0; n <= 48; ++n) {
        if (n != 40) assert(ai_validate((erlan_su8){bytes, n}, 0) == 1);
    }
    ai_encode(request, 1, 1);
    const u64 edges[] = {0, 1, 255, 256, 0x8000000000000000ull, UINT64_MAX};
    for (unsigned i = 0; i < sizeof edges / sizeof *edges; ++i) {
        ai_put64(request, 8, edges[i]);
        assert(ai_get64(request, 8) == edges[i]);
    }
    puts("M4 wire: golden bytes, enum/version/length/flags/replay boundaries PASS");
    return 0;
}
