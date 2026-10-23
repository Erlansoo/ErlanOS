# Decisión: ErlanOS sobre una base Linux mínima

Estado: aprobada por el usuario el 2026-09-15. Primer hito: LB0 en VirtualBox.

## Contexto

La meta es un sistema instalado en la PC física donde la IA manda: Claude Code y
Codex por línea de comandos con la suscripción del usuario, Qwen local sin
internet, y una interfaz propia que oculta la terminal. Nuestro microkernel ya
arranca en hardware real, pero le faltan años para tener controlador Intel
I219-V, discos, USB, CUDA para la GTX 1050 Ti y un entorno donde corran los
binarios oficiales de Claude Code y Codex.

Hecho decisivo: el login con suscripción sólo está permitido en el binario
oficial y sin modificar de Claude Code (code.claude.com/docs/en/legal-and-compliance).
Un cliente propio no puede usarlo. El binario oficial necesita Linux, macOS o
Windows.

## Decisión

El núcleo del sistema instalado es Linux. ErlanOS es todo lo que está encima:
política, servicios, lenguaje, mente e interfaz. El microkernel de ErlanOS sigue
vivo como laboratorio, arrancando como máquina virtual con aceleración.

Distribución: Debian 13 estable, instalación mínima, sin escritorio. No Alpine:
usa musl y el controlador propietario de NVIDIA exige glibc; la GTX 1050 Ti
(Pascal) sólo tiene soporte en la rama 580 de NVIDIA, y los módulos abiertos no
cubren Pascal.

## La filosofía, traducida a esta base

"La IA manda; el núcleo garantiza; el Immutable Core decide."

| Capa | Antes (microkernel) | Ahora (base Linux) |
|---|---|---|
| L0 Hardware | PC física | igual |
| L1 Núcleo | microkernel Erlan, capabilities | kernel Linux de Debian, sin módulos ni servicios innecesarios; nunca ejecuta IA |
| L2 Servicios | procesos Ring 3 en Erlan | servicios en Erlan compilados a binarios Linux: `erlan-sistema` (broker de capabilities y recibos), `erlan-mente` (adaptadores), Qwen local |
| L3 Mente | Qwen en Ring 3 | Claude Code, Codex y Qwen, cada uno como usuario sin privilegios; sólo hablan con el broker |
| L4 Interfaz | consola del kernel, halo | escritorio Erlan a pantalla completa sobre DRM/KMS: halo, chat translúcido estilo WhatsApp, lienzo |
| L5 Motores | documentos | documentos, y luego CAD, KiCad y simulación como motores con operaciones tipadas |
| Immutable Core | kernel verificado | sistema base de solo lectura con actualizaciones A/B firmadas y reversibles |

Reglas que no cambian:

1. **La IA nunca tiene root ni dispositivos.** Cada mente corre con su propio usuario
   Linux, sin sudo, con un directorio de trabajo acotado. Lo que no esté concedido
   explícitamente, no lo puede hacer.
2. **Toda acción sobre el sistema pasa por el broker** `erlan-sistema` y deja un
   recibo verificable. El halo y el panel los pinta un proceso de confianza, no la IA.
3. **La mente propone, el sistema aplica.** Una respuesta de la IA nunca se ejecuta
   como código del sistema; se traduce a operaciones tipadas o se rechaza.
4. **Por capas, desde el día uno.** Cada hito es arrancable y probado antes del siguiente.
5. **Nada se da por hecho sin evidencia.** Cada hito deja un registro reproducible
   (serial, capturas, `result.json`) igual que las pruebas en QEMU.
6. **Erlan es el lenguaje propio.** El código nuevo de ErlanOS se escribe en Erlan;
   C sólo como pegamento con Linux, igual que antes con el hardware.

## Qué se conserva del trabajo anterior

- Compilador `erlancompiler` y lenguaje Erlan: emiten C, que Linux compila nativo.
- Runtime de Qwen en Erlan: pasa a proceso Linux, luego con GPU.
- Pasarela y adaptadores de Claude Code/Codex (`scripts/gateway/`): pasan a servicio local.
- Diseño de interfaz: halo de seis estados, panel verificado, chat, editor de documentos.
- Pila de red y cripto nativas: quedan en el laboratorio del microkernel.
- Microkernel: laboratorio en máquina virtual.

## Hitos

| Hito | Contenido | Criterio de aceptación |
|---|---|---|
| LB0 | Debian mínima desatendida en VirtualBox | arranca sola, SSH por llave, red real, lista de procesos y paquetes registrada |
| LB1 | Erlan en Linux | `erlan/tests/basics.e` compila y corre dentro de la VM con el resultado esperado |
| LB2 | Mentes instaladas | Claude Code y Codex oficiales instalados en usuarios sin privilegios; el login lo hace el usuario |
| LB3 | Escritorio Erlan v0 | programa a pantalla completa sin X ni Wayland: halo y chat conectados a Claude Code |
| LB4 | Broker y recibos | `erlan-sistema` en Erlan; la mente sólo actúa mediante operaciones con recibo |
| LB5 | Qwen local | runtime Erlan como proceso Linux, respaldo sin red |
| LB6 | Immutable Core | raíz de solo lectura, actualización A/B, vuelta atrás automática |
| LB7 | PC física | instalación en disco elegido por el usuario, GPU con controlador 580, I219-V |

## Límites declarados

- La confianza en L1 pasa a ser la de Linux y Debian, no un microkernel verificado.
- VirtualBox con Hyper-V activo en Windows usa el backend de Hyper-V y es más lento
  que en metal; sirve para desarrollo, no para medir rendimiento.
- La GTX 1050 Ti depende de una rama heredada de NVIDIA con fecha de fin.
