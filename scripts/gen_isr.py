#!/usr/bin/env python3
"""Genera kernel/src/isr_stubs.e: 256 stubs de entrada de interrupciones para x86-64, en Erlan.

Cada stub apila el código de error (0 si el CPU no lo apiló) y el número de vector, y salta
a isr_common, que guarda los registros de propósito general y llama a isr_dispatch(ptr<IntFrame>)
definido en kernel/src/idt.e. Los stubs van alineados a 16 bytes, así que la dirección del
vector n es isr0 + 16*n y no hace falta una tabla de punteros a función.
"""
import os

ERR = {8, 10, 11, 12, 13, 14, 17, 21, 29, 30}   # vectores con código de error del CPU

def q(line):
    """Una línea de ensamblador como literal de cadena con \\n al final."""
    return '    "' + line + '\\n"'

out = [
    '// Generado por scripts/gen_isr.py — no editar a mano. Stubs de interrupción en Erlan.',
    '// Desde el 26/10/2026 sustituye a isr_stubs.c.',
    'asm(',
    q('.text'),
    q('isr_common:'),
]
push = ['rax','rbx','rcx','rdx','rsi','rdi','rbp','r8','r9','r10','r11','r12','r13','r14','r15']
out += [q(f'  pushq %{r}') for r in push]
out += [q('  cld'), q('  movq %rsp, %rdi'), q('  call isr_dispatch')]
out += [q(f'  popq %{r}') for r in reversed(push)]
out += [q('  addq $16, %rsp'), q('  iretq')]
for n in range(256):
    out.append(q('  .balign 16'))
    if n == 0: out.append(q('.global isr0'))
    out.append(q(f'isr{n}:'))
    if n not in ERR:
        out.append(q('  pushq $0'))
    out.append(q(f'  pushq ${n}'))
    out.append(q('  jmp isr_common'))
out[-1] = out[-1] + ');'
out += ['',
        '// El primer stub, visto desde Erlan; los demás están a 16 bytes cada uno.',
        'extern c { fn isr0(); }',
        '',
        '// Dirección del stub de entrada para el vector n (0..255).',
        'fn isr_stub_addr(n: u64) -> u64 {',
        '    if n >= 256 { return 0; }',
        '    return (isr0 as u64) + n * 16;',
        '}',
        '']

path = os.path.join(os.path.dirname(__file__), '..', 'kernel', 'src', 'isr_stubs.e')
with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(out))
print('escrito', os.path.normpath(path), len(out), 'líneas')
