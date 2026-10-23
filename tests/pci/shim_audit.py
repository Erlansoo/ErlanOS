"""Symbolically check the actual clang-emitted privileged CONF1 shim.

This intentionally small interpreter rejects unknown instructions. It follows
both incoming IF states, records every IN/OUT port, and checks restoration of
the selector and IF. It does not claim to execute privileged hardware I/O.
"""
import re


def audit(path, symbol="pci_test_shim", write_value=None):
    instructions, labels = [], {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith(":"):
            labels[line[:-1]] = len(instructions)
        elif not line.startswith("."):
            pieces = line.split(None, 1)
            instructions.append((pieces[0], pieces[1].split(", ") if len(pieces) == 2 else []))
    aliases = {
        "eax": ("rax", 32), "ecx": ("rcx", 32), "edx": ("rdx", 32),
        "esi": ("rsi", 32), "edi": ("rdi", 32), "ebx": ("rbx", 32),
        "esp": ("rsp", 32), "ebp": ("rbp", 32), "dx": ("rdx", 16),
    }

    def register(name):
        assert name.startswith("%"), f"non-register operand {name}"
        name = name[1:]
        if name in aliases:
            return aliases[name]
        if re.fullmatch(r"r\d+d", name):
            return name[:-1], 32
        if re.fullmatch(r"r\d+w", name):
            return name[:-1], 16
        assert re.fullmatch(r"r(?:ax|bx|cx|dx|si|di|sp|bp|\d+)", name), name
        return name, 64

    for incoming_if in (False, True):
        selector, old_selector, result = 0x80FFFFFC, 0x12345678, 0x76543210
        registers = {"rdi": selector, "rsp": 0x20000, "rbp": 0x30000}
        if write_value is not None:
            registers["rsi"] = write_value
        stack, ports, operations = [], {0xCF8: old_selector}, []
        enabled, zero, pc = incoming_if, False, labels[symbol]

        def read(operand):
            if operand.startswith("$"):
                return int(operand[1:], 0)
            name, width = register(operand)
            return registers.get(name, 0) & ((1 << width) - 1)

        def write(operand, value):
            name, width = register(operand)
            value &= (1 << width) - 1
            if width == 16:
                value |= registers.get(name, 0) & ~0xFFFF
            registers[name] = value

        for _ in range(256):
            op, args = instructions[pc]
            pc += 1
            if op == "pushq":
                stack.append(read(args[0]))
            elif op == "popq":
                write(args[0], stack.pop())
            elif op == "pushfq":
                stack.append(0x42 | (0x200 if enabled else 0))
            elif op == "cli":
                enabled = False
            elif op == "sti":
                enabled = True
            elif op in ("movq", "movl", "movw"):
                write(args[1], read(args[0]))
            elif op == "inl":
                port = read(args[0])
                assert not enabled, "PCI selector/read must be atomic against local IRQs"
                assert port in (0xCF8, 0xCFC), f"unexpected input port {port:#x}"
                value = ports[0xCF8] if port == 0xCF8 else result
                operations.append(("in", port, value))
                write(args[1], value)
            elif op == "outl":
                port, value = read(args[1]), read(args[0])
                assert not enabled, "PCI selector write must run with IRQs disabled"
                if write_value is None:
                    assert port == 0xCF8, "forbidden PCI configuration data write"
                else:
                    assert port in (0xCF8, 0xCFC), f"unexpected output port {port:#x}"
                ports[port] = value
                operations.append(("out", port, value))
            elif op in ("testl", "testq"):
                zero = read(args[0]) & read(args[1]) == 0
            elif op in ("je", "jz", "jne", "jnz"):
                if zero == (op in ("je", "jz")):
                    pc = labels[args[0]]
            elif op == "jmp":
                pc = labels[args[0]]
            elif op in ("retq", "ret"):
                break
            else:
                raise AssertionError(f"shim audit does not understand {op} {args}")
        else:
            raise AssertionError("shim did not return within bounded instruction count")
        assert not stack, "unbalanced stack"
        if write_value is None:
            assert registers["rax"] == result, "incorrect return value"
        assert enabled == incoming_if, "caller interrupt state was changed"
        assert ports[0xCF8] == old_selector, "previous selector was not restored"
        if write_value is None:
            expected = [("in", 0xCF8, old_selector), ("out", 0xCF8, selector),
                        ("in", 0xCFC, result), ("out", 0xCF8, old_selector)]
        else:
            expected = [("in", 0xCF8, old_selector), ("out", 0xCF8, selector),
                        ("out", 0xCFC, write_value), ("out", 0xCF8, old_selector)]
        assert operations == expected, f"unexpected I/O sequence: {operations}"
    if write_value is None:
        print("PASS compiled PCI shim audit: IF=0/1 preserved, CF8 restored, zero CFC writes")
    else:
        print("PASS compiled PCI write-shim audit: IF=0/1 preserved, CF8 restored, exactly one CFC write")
