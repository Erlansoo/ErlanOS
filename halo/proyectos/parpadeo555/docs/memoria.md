# Parpadeador LED NE555

Memoria del proyecto · 2026-09-20 · generada en el Halo y ampliada a mano.

## Propósito

_Describe aquí qué hace el circuito y para quién. Este texto es tuyo: las secciones generadas se actualizan solas y no lo tocan._

<!-- halo:seccion esquematico 4c362327f28d -->
## Esquemático

Archivo `parpadeo555.kicad_sch` · 9 componentes · 30 cables · 3 bloques. Verificación de KiCad: ERC: 0 errores, 0 avisos; netlist idéntica a la intención; dibujo: 0 problemas.

![Esquemático](figuras/esquematico.svg)

### Bloques del circuito

- **Entrada de 5 V** (J1)
- **Temporizador astable NE555** (C1, C2, C3, R1, R2, U1)
- **Indicador LED** (D1, R3)

### Componentes

| Referencia | Valor | Símbolo |
|---|---|---|
| C1 | 10uF | `Device:C_Polarized` |
| C2 | 10nF | `Device:C` |
| C3 | 100nF | `Device:C` |
| D1 | LED rojo | `Device:LED` |
| J1 | Entrada 5V | `Connector_Generic:Conn_01x02` |
| R1 | 1k | `Device:R` |
| R2 | 68k | `Device:R` |
| R3 | 470 | `Device:R` |
| U1 | NE555P | `Timer:NE555P` |

Señales entre bloques: `PULSO`.

<!-- halo:fin esquematico -->

<!-- halo:seccion simulacion d972a90cd266 -->
## Simulación

| Simulación | Análisis | Estado | Criterios | Fecha |
|---|---|---|---|---|
| 555 · temporización astable ideal | tran | completado (obsoleto) | cumple | 2026-09-16 10:53 |

### Medidas

| Simulación | Medida | Valor | Estado |
|---|---|---|---|
| 555 · temporización astable ideal | frecuencia (frecuencia de salida) | 1.05263 Hz | cumple |

<!-- halo:fin simulacion -->

<!-- halo:seccion pcb 38e57feec696 -->
## Placa de circuito impreso

Archivo `parpadeo555.kicad_pcb` · 41.5 × 27.5 mm · ['F.Cu', 'B.Cu'] capas · etapa: ruteada. 9 huellas (9 arriba, 0 abajo), 50 tramos de pista, 0 vías. Verificación: DRC: 0 errores, 0 avisos; 0 conexiones pendientes; paridad con el esquemático exacta.

![Placa](figuras/placa.svg)

Zonas de cobre: GND en B.Cu.
Anchos de pista por red: +5V 0.5 mm, GND 0.5 mm.

![Vista 3D](figuras/3d-1789488973865068000-arriba.png)

![Vista 3D](figuras/3d-1789488973865068000-perspectiva.png)

<!-- halo:fin pcb -->

<!-- halo:seccion fabricacion ausente -->
## Fabricación

_Todavía no se han preparado archivos de fabricación._

<!-- halo:fin fabricacion -->

## Conclusiones y siguientes pasos

_Pendiente._
