# ErlanOS — build raíz. Corre en WSL2 Ubuntu (scripts/setup-wsl.sh) o en Windows
# con Git Bash + LLVM + QEMU + make (scripts/setup-windows.sh, luego `source scripts/env.sh`).
#
#   make        -> build/kernel.elf + build/user/*.bin
#   make run    -> disco GPT/FAT32 con un modelo y arranque OVMF + Limine
#   make gdb    -> igual que run pero detenido esperando gdb en :1234
#   make test   -> pruebas en host del compilador Erlan
#   make clean

CC      := clang
LD      := ld.lld
OBJCOPY := llvm-objcopy
QEMU    := qemu-system-x86_64
PYTHON  := python
ERLAN   := $(PYTHON) erlan/erlancompiler.py

BUILD   := build
ESP     := esp
MODEL   ?= qwen35
MODEL_qwen35 := models/qwen3.5-2b-q8_0.gguf
MODEL_coder := models/qwen2.5-coder-1.5b-instruct-q8_0.gguf
MODEL_smollm := models/smollm2-135m-instruct-q8_0.gguf
MODEL_FILE ?= $(MODEL_$(MODEL))
BOOT_IMAGE := $(BUILD)/erlanos-$(MODEL).img
KSRC    := kernel/src
GEN     := $(BUILD)/gen
LIMINE  := third_party/limine

# En Windows se define OVMF en scripts/env.sh (la ruta tiene espacios y wildcard no la soporta).
OVMF    ?= $(firstword $(wildcard /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/ovmf/OVMF.fd))

# El C generado por erlancompiler usa paréntesis y constantes de forma mecánica.
EFLAGS := -Wno-parentheses-equality -Wno-unused-const-variable -Wno-unused-parameter

# ---------------------------------------------------------------- kernel (L1)
CFLAGS  := -std=gnu11 -O2 -g -Wall -Wextra -pipe \
           --target=x86_64-unknown-none-elf \
           -ffreestanding -fno-stack-protector -fno-stack-check -fno-lto -fno-PIC \
           -fno-asynchronous-unwind-tables -fno-unwind-tables \
           -m64 -march=x86-64 -mno-80387 -mno-mmx -mno-sse -mno-sse2 -mno-red-zone \
           -mcmodel=kernel -I$(KSRC) -I$(GEN) -Ierlan/runtime

LDFLAGS := -nostdlib -static -z max-page-size=0x1000 -T kernel/linker.ld

SRCS  := $(wildcard $(KSRC)/*.c)
OBJS  := $(patsubst $(KSRC)/%.c,$(BUILD)/%.o,$(SRCS))
ESRCS := $(wildcard $(KSRC)/*.e)
GENH  := $(patsubst $(KSRC)/%.e,$(GEN)/%.h,$(ESRCS))
EOBJS := $(patsubst $(KSRC)/%.e,$(BUILD)/%.e.o,$(ESRCS))
# Pila de red en Erlan (net/): se enlaza en el kernel de prueba de red; en su forma
# final correrá en un servicio Ring 3 con el mismo código.
NET_ESRCS := $(wildcard net/*.e net/tls/*.e)
NET_GENH  := $(patsubst %.e,$(GEN)/%.h,$(notdir $(NET_ESRCS)))
NET_EOBJS := $(patsubst %.e,$(BUILD)/%.e.o,$(notdir $(NET_ESRCS)))

# ---------------------------------------------------------------- servicios (L2)
UGEN     := $(BUILD)/ugen
UOUT     := $(BUILD)/user
SERVICES := $(filter-out lib,$(notdir $(patsubst %/,%,$(wildcard services/*/))))
UBINS    := $(patsubst %,$(UOUT)/%.bin,$(SERVICES))
# Usuario: SSE/AVX permitidos (el kernel guarda el estado con XSAVE).
UCFLAGS  := -std=gnu11 -O2 -g -Wall -pipe --target=x86_64-unknown-none-elf \
            -ffreestanding -fno-stack-protector -fno-stack-check -fno-lto -fno-PIC \
            -fno-asynchronous-unwind-tables -fno-unwind-tables -m64 -mcmodel=small \
            -Iservices/lib -I$(UGEN) -Ierlan/runtime $(EFLAGS)

# ---------------------------------------------------------------- QEMU
# WHPX en Windows o KVM en Linux/WSL, comprobado con una VM pausada y sin discos.
# Evaluación diferida: compilar no arranca la sonda. TCG queda como fallback explícito.
# Para depurar: make run QEMU_ACCEL='-accel tcg -cpu max'
QEMU_ACCEL ?= $(shell $(PYTHON) scripts/qemu_accel.py --qemu "$(QEMU)" --flags)
QEMU_MEMORY ?= 4G
QEMU_FLAGS = -M q35 -m $(QEMU_MEMORY) $(QEMU_ACCEL) \
              -drive if=pflash,format=raw,readonly=on,file="$(OVMF)" \
              -drive format=raw,file="$(BOOT_IMAGE)" \
              -serial stdio -no-reboot -no-shutdown

.PHONY: all run gdb clean esp test services test-m4 test-m4-host hardware-image run-hardware net-image run-net test-net
.SECONDARY:   # conservar los .c/.h generados por erlancompiler

all: $(BUILD)/kernel.elf services

test:
	bash erlan/tests/run.sh

test-m4-host:
	bash scripts/test_m4_host.sh

test-m4: esp
	$(PYTHON) scripts/qemu_m4_test.py

# ---- kernel
$(GEN)/%.c $(GEN)/%.h &: $(KSRC)/%.e erlan/erlancompiler.py | $(GEN)
	$(ERLAN) $< -o $(GEN)/$*.c --header $(GEN)/$*.h

$(GEN)/%.c $(GEN)/%.h &: net/%.e erlan/erlancompiler.py | $(GEN)
	$(ERLAN) $< -o $(GEN)/$*.c --header $(GEN)/$*.h

# Criptografía y TLS nativos (net/tls/): SHA-2, HMAC/HKDF, AES-GCM, X25519, ECDSA.
$(GEN)/%.c $(GEN)/%.h &: net/tls/%.e erlan/erlancompiler.py | $(GEN)
	$(ERLAN) $< -o $(GEN)/$*.c --header $(GEN)/$*.h

$(BUILD)/%.e.o: $(GEN)/%.c erlan/runtime/erlan_rt.h $(wildcard $(KSRC)/*.h) $(GENH) $(NET_GENH) | $(BUILD)
	$(CC) $(CFLAGS) $(EFLAGS) -c $< -o $@

$(BUILD)/%.o: $(KSRC)/%.c $(wildcard $(KSRC)/*.h) $(GENH) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/kernel.elf: $(OBJS) $(EOBJS) kernel/linker.ld
	$(LD) $(LDFLAGS) $(OBJS) $(EOBJS) -o $@

# Standalone discovery image: only the entry object differs from the main kernel.
$(BUILD)/main-probe.o: $(KSRC)/main.c $(wildcard $(KSRC)/*.h) $(GENH) | $(BUILD)
	$(CC) $(CFLAGS) -DERLANOS_HARDWARE_PROBE -c $< -o $@

$(BUILD)/kernel-probe.elf: $(filter-out $(BUILD)/main.o,$(OBJS)) $(EOBJS) $(BUILD)/main-probe.o kernel/linker.ld
	$(LD) $(LDFLAGS) $(filter-out $(BUILD)/main.o,$(OBJS)) $(EOBJS) $(BUILD)/main-probe.o -o $@

hardware-image: $(BUILD)/kernel-probe.elf
	$(PYTHON) scripts/build_hardware_image.py

# Imagen de prueba de red: kernel + driver e1000 + pila net/, sin servicios ni modelo.
$(BUILD)/main-net.o: $(KSRC)/main.c $(wildcard $(KSRC)/*.h) $(GENH) $(NET_GENH) | $(BUILD)
	$(CC) $(CFLAGS) -DERLANOS_NET_PROBE -c $< -o $@

$(BUILD)/kernel-net.elf: $(filter-out $(BUILD)/main.o,$(OBJS)) $(EOBJS) $(NET_EOBJS) $(BUILD)/main-net.o kernel/linker.ld
	$(LD) $(LDFLAGS) $(filter-out $(BUILD)/main.o,$(OBJS)) $(EOBJS) $(NET_EOBJS) $(BUILD)/main-net.o -o $@

net-image: $(BUILD)/kernel-net.elf
	$(PYTHON) scripts/build_hardware_image.py --kernel $(BUILD)/kernel-net.elf --output $(BUILD)/erlanos-net.img --profile net-n0 --name "ErlanOS Red N0"

# NIC e1000 con red de usuario (SLIRP: DHCP 10.0.2.15, NAT real) y captura pcap.
QEMU_NET := -netdev user,id=n0,ipv6=off -device e1000,netdev=n0,mac=52:54:00:12:34:56 \
            -object filter-dump,id=f0,netdev=n0,file=$(BUILD)/net.pcap

run-net: net-image
	$(QEMU) -M q35 -m 512M $(QEMU_ACCEL) \
	    -drive if=pflash,format=raw,readonly=on,file="$(OVMF)" \
	    -drive format=raw,file="$(BUILD)/erlanos-net.img" $(QEMU_NET) \
	    -serial stdio -no-reboot -no-shutdown

test-net: $(BUILD)/kernel-net.elf
	$(PYTHON) scripts/qemu_net_test.py

run-hardware: hardware-image
	$(QEMU) -M q35 -m 512M $(QEMU_ACCEL) \
	    -drive if=pflash,format=raw,readonly=on,file="$(OVMF)" \
	    -drive format=raw,file="$(BUILD)/erlanos-hardware.img" \
	    -serial stdio -no-reboot -no-shutdown

# ---- servicios de usuario: Erlan -> C -> ELF en 0x400000 -> binario plano
services: $(UBINS)

$(UGEN)/ulib.c $(UGEN)/ulib.h &: services/lib/ulib.e erlan/erlancompiler.py | $(UGEN)
	$(ERLAN) $< -o $(UGEN)/ulib.c --header $(UGEN)/ulib.h

$(UOUT)/crt0.o: services/lib/crt0.c services/lib/usys.h | $(UOUT)
	$(CC) $(UCFLAGS) -c $< -o $@

$(UOUT)/ulib.o: $(UGEN)/ulib.c $(UGEN)/ulib.h | $(UOUT)
	$(CC) $(UCFLAGS) -c $< -o $@

$(UGEN)/ai_wire.c $(UGEN)/ai_wire.h &: services/lib/ai_wire.e erlan/erlancompiler.py | $(UGEN)
	$(ERLAN) $< -o $(UGEN)/ai_wire.c --header $(UGEN)/ai_wire.h

$(UOUT)/ai_wire.o: $(UGEN)/ai_wire.c $(UGEN)/ai_wire.h erlan/runtime/erlan_rt.h | $(UOUT)
	$(CC) $(UCFLAGS) -c $< -o $@

# L3 controller is linked with the model process in the M4 prototype.
model_EXTRA_OBJS := $(UOUT)/model/controller.e.o $(UOUT)/model/document_client.e.o $(UOUT)/model/workspace_client.e.o
model_EXTRA_GENH := $(UGEN)/model/controller.h $(UGEN)/model/document_client.h $(UGEN)/model/workspace_client.h

# Only this object may contain AVX2. The portable caller checks CPUID and XCR0
# before entry; all other model code remains compatible with the base ISA.
$(UOUT)/model/%.e.o: UCFLAGS += -O3
$(UOUT)/model/matmul_fast.e.o: UCFLAGS += -mavx2 -mno-fma

$(UGEN)/model/controller.c $(UGEN)/model/controller.h &: mind/controller.e erlan/erlancompiler.py | $(UGEN)
	mkdir -p $(UGEN)/model
	$(ERLAN) $< -o $(UGEN)/model/controller.c --header $(UGEN)/model/controller.h

$(UGEN)/model/document_client.c $(UGEN)/model/document_client.h &: mind/document_client.e erlan/erlancompiler.py | $(UGEN)
	mkdir -p $(UGEN)/model
	$(ERLAN) $< -o $(UGEN)/model/document_client.c --header $(UGEN)/model/document_client.h

$(BUILD)/document_view.e.o: assets/fonts/font_data.h
$(BUILD)/workspace_view.e.o: assets/fonts/font_data.h
$(UGEN)/model/workspace_client.c $(UGEN)/model/workspace_client.h &: mind/workspace_client.e erlan/erlancompiler.py | $(UGEN)
	mkdir -p $(UGEN)/model
	$(ERLAN) $< -o $(UGEN)/model/workspace_client.c --header $(UGEN)/model/workspace_client.h
$(UOUT)/system/document.e.o: assets/fonts/font_data.h services/lib/docfont.h

# Cada servicio: todos los .e de su directorio -> C -> objetos -> ELF -> binario plano.
define SERVICE_RULES
$(1)_ESRCS := $$(wildcard services/$(1)/*.e)
$(1)_GENH  := $$(patsubst services/$(1)/%.e,$$(UGEN)/$(1)/%.h,$$($(1)_ESRCS)) $$($(1)_EXTRA_GENH)
$(1)_OBJS  := $$(patsubst services/$(1)/%.e,$$(UOUT)/$(1)/%.e.o,$$($(1)_ESRCS))

$$(UGEN)/$(1)/%.c $$(UGEN)/$(1)/%.h &: services/$(1)/%.e erlan/erlancompiler.py | $$(UGEN)
	mkdir -p $$(UGEN)/$(1)
	$$(ERLAN) $$< -o $$(UGEN)/$(1)/$$*.c --header $$(UGEN)/$(1)/$$*.h

$$(UOUT)/$(1)/%.e.o: $$(UGEN)/$(1)/%.c $$($(1)_GENH) $$(UGEN)/ulib.h $$(UGEN)/ai_wire.h erlan/runtime/erlan_rt.h erlan/runtime/umath.h $$(wildcard services/$(1)/*.h) services/lib/usys.h | $$(UOUT)
	mkdir -p $$(UOUT)/$(1)
	$$(CC) $$(UCFLAGS) -Iservices/$(1) -I$$(UGEN)/$(1) -c $$< -o $$@

$$(UOUT)/$(1).elf: $$(UOUT)/crt0.o $$(UOUT)/ulib.o $$(UOUT)/ai_wire.o $$($(1)_OBJS) $$($(1)_EXTRA_OBJS) services/user.ld
	$$(LD) -nostdlib -static -z max-page-size=0x1000 -T services/user.ld \
	    $$(UOUT)/crt0.o $$(UOUT)/ulib.o $$(UOUT)/ai_wire.o $$($(1)_OBJS) $$($(1)_EXTRA_OBJS) -o $$@
endef
$(foreach s,$(SERVICES),$(eval $(call SERVICE_RULES,$(s))))

$(UOUT)/%.bin: $(UOUT)/%.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD) $(GEN) $(UGEN) $(UOUT):
	mkdir -p $@

# ---- imagen de arranque
esp: $(BUILD)/kernel.elf services
	@test -f $(LIMINE)/BOOTX64.EFI || { echo "falta $(LIMINE): corre scripts/setup-wsl.sh o scripts/setup-windows.sh"; exit 1; }
	@test -n "$(OVMF)" || { echo "falta OVMF (sudo apt install ovmf, o QEMU para Windows)"; exit 1; }
	$(PYTHON) scripts/build_boot_image.py --model "$(MODEL_FILE)" --output "$(BOOT_IMAGE)"

run run-workspace: all
	$(PYTHON) scripts/run_workspace.py --model "$(MODEL_FILE)" --accel "$(word 2,$(QEMU_ACCEL))" --cpu "$(word 4,$(QEMU_ACCEL))" --memory "$(QEMU_MEMORY)"

gdb: esp
	$(QEMU) $(QEMU_FLAGS) -s -S

clean:
	rm -rf $(BUILD) $(ESP)
