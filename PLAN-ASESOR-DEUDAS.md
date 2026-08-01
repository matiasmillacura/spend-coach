# Plan: de registrador de gastos a asesor financiero

Capturado el 2026-07-27. Objetivo: que la app **decida y recomiende**, no solo registre.
El caso que lo motiva: manejar deuda de tarjeta de crédito con el dinero que realmente
hay, no con el que debería haber.

## 1. La decisión de arquitectura que define todo

El asesor son **tres piezas distintas**, y confundirlas es el error caro:

| Pieza | Responsabilidad | Qué NO hace |
|---|---|---|
| **Motor financiero** (Python puro) | Todos los números: intereses, amortización, flujo de caja, prioridad de deudas, cuánto se puede gastar | No conversa |
| **Agente (Claude)** | Entender la pregunta, elegir qué calcular, pedir el dato que falta, explicar el resultado | **No calcula** |
| **RAG (embeddings)** | Recuperar contexto cualitativo: qué dijo el usuario antes, gastos parecidos, para qué era esa meta | No decide montos |

Regla dura: **ningún monto recomendado sale del modelo**. El modelo llama a una
herramienta, el motor devuelve el número y el modelo lo explica. Un asesor que
"estima" cuánto abonar es un asesor que inventa. Además el motor es determinista y
testeable: se le puede escribir un test a "cuánto abono este mes", no a una opinión.

El RAG que ya existe sirve aquí, pero para lo cualitativo — "¿qué me contó del regalo
de su papá?", "¿qué gastó otras veces en cumpleaños?" —, nunca para el cálculo.

## 2. La matemática que justifica el producto

Deuda de $1.000.000 a 2,9% mensual (~41% anual, cerca de la TMC de julio 2026: 41,26%):

| Estrategia | Meses | Total pagado | Intereses |
|---|---:|---:|---:|
| Pago mínimo (5% del saldo) | **74** | $2.035.512 | $1.035.512 |
| Cuota fija $50.000 | 31 | $1.517.437 | $517.437 |
| Cuota fija $100.000 | 12 | $1.198.071 | $198.071 |
| Cuota fija $150.000 | 8 | $1.127.838 | $127.838 |
| Cuota fija $250.000 | 5 | $1.079.024 | $79.024 |

Pagando el mínimo se tarda **6 años y se paga más del doble**. Mostrar esta tabla con
los datos reales del usuario es, por sí solo, media app.

**Ahorrar vs abonar** (la intuición del usuario, confirmada): $100.000 en un depósito a
plazo rinden ~$400 al mes; esos mismos $100.000 abonados a la tarjeta evitan $2.900 de
interés. Abonar es **7,2 veces mejor**, sin riesgo y sin impuestos. De ahí sale la regla:

> Mientras exista deuda cara, ahorrar es perder plata — **con una excepción**: el colchón
> de emergencia. Sin colchón, el próximo imprevisto vuelve a la tarjeta y el ciclo
> recomienza. Por eso: colchón mínimo primero, luego deuda cara, luego metas.

## 3. Reglas del asesor

1. **Orden de prioridad del excedente**: (a) mínimos de todas las deudas — nunca caer en
   mora; (b) colchón hasta 1 mes de gastos esenciales; (c) abono extra a la deuda de
   **mayor tasa** (método avalancha); (d) metas de ahorro.
2. **Umbral de "deuda cara"**: tasa mensual > rendimiento realista del ahorro. En Chile
   hoy, cualquier rotativo de TC lo es. Un crédito hipotecario, no.
3. **Nunca dejar el colchón en cero** para abonar, aunque la matemática pura lo sugiera.
4. **Advertir, no prohibir**: si el usuario quiere ahorrar teniendo deuda cara, se le
   muestra el costo de esa decisión y se respeta si insiste.
5. **Perfil de riesgo** (conservador / equilibrado / date un gustito) modula el colchón
   objetivo y qué parte del excedente va a gustos, no cambia el orden de prioridad.

## 4. Requerimientos que faltaban

- **Flujo de caja por fecha, no por mes.** Lo que importa no es "cuánto queda en julio"
  sino "cuánto queda hasta el próximo sueldo". Si el cumpleaños es el 5 y el sueldo entra
  el 30, el disponible de hoy no es el del mes.
- **Disponible real** = saldo hoy − compromisos hasta el próximo ingreso − colchón.
  Ese es el número que responde "¿cuánto puedo gastar en el regalo?".
- **Cuotas ya contraídas** son un calendario de compromisos, distinto del rotativo.
  Distinguir cuotas **sin interés** ("3 cuotas precio contado") de las con interés: las
  primeras no conviene prepagarlas, las segundas sí.
- **Fecha de corte vs fecha de pago.** Comprar justo después del corte da ~45 días de
  financiamiento gratis; comprar justo antes, ~15. Un asesor real avisa esto.
- **Eventos futuros conocidos**: cumpleaños, viajes, permisos de circulación, matrículas.
  Con fecha y monto estimado, entran al flujo proyectado y cambian el disponible de hoy.
- **Ahorro preexistente**: quien llega con $3.000.000 ahorrados no parte de cero. Hay que
  poder registrarlo y clasificarlo (colchón vs meta), porque cambia toda la recomendación.
- **Simulaciones "¿y si...?"**: "si abono $300.000 este mes, ¿cuándo salgo?" — comparar
  escenarios es la función más útil de un asesor.
- **Alertas proactivas**: se acerca el corte, el mínimo vence en 3 días, este mes vas
  gastando 40% más que tu promedio.
- **Repactación**: comparar el costo total de repactar (cuota nueva × plazo, con su CAE)
  contra seguir como está. Repactar casi siempre baja la cuota **y sube el costo total**;
  el asesor debe decir ambas cosas.

### Cómo pedir los datos sin formularios

El problema de "¿cuándo le pregunto la tasa de su tarjeta?": **completitud progresiva**.
El agente pide un dato solo cuando lo necesita para responder algo que el usuario ya
preguntó, y lo guarda para siempre.

> — ¿Cuánto le abono a la tarjeta este mes?
> — Para calcularlo necesito la tasa que te cobran. Está en tu estado de cuenta como CAE
> o interés rotativo. ¿La tienes a mano?

Si no la tiene: se usa un **supuesto explícito y marcado como tal** (la TMC vigente) y se
recalcula cuando aparezca el dato real. Nunca preguntar por datos que todavía no sirven
para nada.

## 5. Modelo de datos nuevo

> **Una tarjeta no es una deuda: son hasta tres.** Rotativo, compras en cuotas y avances
> son productos distintos, con tasa distinta, y el usuario puede deber en los tres a la
> vez. Modelarlos como una sola deuda con "una tasa" da recomendaciones equivocadas.
> Ejemplo real: en Falabella el rotativo cuesta 2,86% mensual y el avance 4,41%; en el
> Banco de Chile el rotativo cuesta 3,45% mensual pero su CAE es 71,36% — el doble del
> interés puro, por comisiones y seguros. Con esos datos, la prioridad de pago cambia.

- `tarjeta`: user_id, institución, día de corte, día de vencimiento, cupo.
- `linea_deuda`: tarjeta_id (o suelta), modalidad (rotativo / cuotas / avance / consumo /
  hipotecario), saldo_actual, tasa_mensual, cae, activa.
  El **CAE es para comparar y para mostrar el costo real**; la **tasa mensual es la que
  usa el motor** para proyectar saldo mes a mes. Se guardan ambas.
- `deuda` (alias de compatibilidad): user_id, institución, tipo, saldo_actual,
  tasa_mensual, cae, dia_corte, dia_vencimiento, pago_minimo, activa.
- `cuota_comprometida`: deuda_id (o suelta), descripción, monto_cuota, cuotas_totales,
  cuotas_pagadas, con_interés (bool), primera_fecha.
- `pago_deuda`: deuda_id, monto, fecha, tipo (mínimo / total / abono extra).
- `evento_futuro`: descripción, fecha, monto_estimado, prioridad, flexible (bool).
- `perfil_financiero`: perfil_riesgo, colchón_objetivo_meses, ahorro_previo, tolerancia.
- Extender `ahorros` con propósito: colchón de emergencia vs meta específica.

## 6. Casos de uso (todos son cálculo, no CRUD)

1. **Plan mensual de deuda** — "este mes abona $X a la tarjeta Y, deja $Z de colchón;
   así sales en N meses en vez de 74".
2. **Ahorro vs deuda** — "no ahorres los $100.000 este mes: abonados a la TC te ahorran
   7 veces más. Retomamos el ahorro en octubre, cuando la deuda baje de $X".
3. **Presupuesto de un gasto puntual** — "puedes gastar hasta $40.000 en el regalo; sobre
   eso, o atrasas el abono de la tarjeta o tocas el colchón".
4. **Simulación de repactación** — "repactar baja tu cuota de $150.000 a $90.000, pero
   pagas $180.000 más en total. Conviene solo si la cuota actual te está haciendo caer
   en el rotativo cada mes".
5. **Proyección de flujo** — "entre hoy y el 30 tienes $220.000 disponibles y $180.000
   comprometidos; te quedan $40.000 de holgura real".
6. **Mes atípico** — "este mes tienes permiso de circulación y un cumpleaños; te propongo
   abonar el mínimo + $30.000 en vez de tu abono habitual, y retomar en agosto".

## 7. Estado

- ✅ **Fase 1 — Motor + deudas**: `finanzas.py`, tablas `tarjetas` / `lineas_deuda` /
  `pagos_deuda`, herramientas `registrar_deuda`, `plan_de_deuda`, `simular_deuda`,
  `evaluar_ahorro_vs_deuda`, `registrar_pago_deuda`.
- ✅ **Fase 2 — Flujo de caja por fecha**: `dia_pago` del sueldo, tabla `eventos_futuros`,
  `flujo_hasta_proximo_ingreso`, herramientas `puedo_gastar` y `registrar_evento_futuro`.
- ✅ **Fase 3 — Perfil y ahorro previo**: `perfil_riesgo` (conservador / equilibrado /
  gustito) y `ahorro_previo` en el usuario; el colchón objetivo sale del perfil.
- ✅ **Fase 4 — Repactación y alertas**: `evaluar_repactacion` y avisos de corte y
  vencimiento en el resumen y en el dashboard.
- ✅ **Fase 5 — Contexto conversacional**: el índice semántico incluye metas y eventos
  futuros, no solo gastos, para que el asesor recuerde para qué era cada cosa.

Pendientes conocidos: los gastos fijos se descuentan completos del flujo aunque ya se
hayan pagado en el mes (conservador a propósito); el pago mínimo se estima en 5% del
saldo mientras el usuario no informe el real de su estado de cuenta.
