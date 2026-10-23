/* The old fixed 64 KiB BSS allowance cannot contain this model-state fixture. */
volatile unsigned char model_state[131073];
volatile unsigned char initialized_state = 7;
__attribute__((section(".text._start"))) void _start(void) {
    model_state[sizeof model_state - 1] = initialized_state;
    for (;;) { }
}
