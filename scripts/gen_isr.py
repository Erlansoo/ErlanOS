#!/usr/bin/env python3
"""Genera kernel/src/isr_stubs.c: 256 stubs de entrada de interrupciones para x86-64.

Cada stub apila el código de error (0 si el CPU no lo apiló) y el número de vector,
y salta a isr_common, que guarda los registros de propósito general y llama a
isr_dispatch(ptr<IntFrame>) definido en kernel/src/idt.e.
"""
import os

ERR = {8, 10, 11, 12, 13, 14, 17, 21, 29, 30}   # vectores con código de error del CPU

def q(line):
    """Una línea de ensamblador como literal C con \\n al final."""
    return '"' + line + '\\n"'

out = [
    '/* Generado por scripts/gen_isr.py — no editar a mano. */',
    '#include "isr.h"',
    '',
    '__asm__(',
    q('.text'),
    q('isr_common:'),
]
push = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15']
out += [q(f'  pushq %{r}') for r in push]
out += [q('  cld'), q('  movq %rsp, %rdi'), q('  call isr_dispatch')]
out += [q(f'  popq %{r}') for r in reversed(push)]
out += [q('  addq $16, %rsp'), q('  iretq')]
for n in range(256):
    out.append(q(f'.global isr{n}'))
    out.append(q(f'isr{n}:'))
    if n not in ERR:
        out.append(q('  pushq $0'))
    out.append(q(f'  pushq ${n}'))
    out.append(q('  jmp isr_common'))
out += [');', '']
out += [f'extern void isr{n}(void);' for n in range(256)]
out += ['', 'static void (*const isr_table[256])(void) = {']
for i in range(0, 256, 8):
    out.append('    ' + ', '.join(f'isr{n}' for n in range(i, i + 8)) + ',')
out += ['};', '', 'u64 isr_stub_addr(u64 n) { return n < 256 ? (u64)(uintptr_t)isr_table[n] : 0; }', '']

path = os.path.join(os.path.dirname(__file__), '..', 'kernel', 'src', 'isr_stubs.c')
with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(out))
print('escrito', os.path.normpath(path), len(out), 'líneas')
