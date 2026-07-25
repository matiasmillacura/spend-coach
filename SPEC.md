# SPEC — Rediseño UX/UI y coach v2

Enfoque spec-driven: este documento define QUÉ se construye; el código lo implementa.

## 1. Flujo principal (industria fintech)

```
Login → Onboarding express (formulario) → Dashboard (HOME) ⇄ Chat coach
```

- **Onboarding express (formulario, NO chat)**: pide solo *primer nombre/apodo* y
  *fecha de nacimiento*. Motivo: son datos estructurados; pedirlos por chat gasta
  tokens y turnos. Al enviar → abre el chat, donde el coach continúa con lo que sí
  es conversacional (ingresos y meta), ya saludando por el nombre.
- **Dashboard = pantalla principal**: es lo primero que ves al entrar (si el
  onboarding está completo). Saludo personal ("Hola, Matías 👋").
- **Chat = pantalla separada** (overlay a pantalla completa con botón ← volver).
  Se llega con el **botón flotante "💬 Registrar"** del dashboard. Nunca conviven
  ambos scrolls: se acabó la confusión de scroll largo.

## 2. El coach (tono y comportamiento)

- **Cálido pero breve**: máx. 2–3 frases por turno. UNA pregunta por turno y solo
  si es imprescindible. Nada de repetir lo que ya se sabe.
- **Decisivo**: si la instrucción es calculable ("ahorré el 10% de mi sueldo"),
  calcula (10% de $800.000 = $80.000), registra y confirma en una frase.
- **Metas alternativas** ("working holiday o auto, lo que salga primero") =
  UNA meta con nombre combinado, no dos ni interrogatorio.
- **Corrección de errores**: puede **listar, editar y eliminar** movimientos
  (gastos, ingresos, ahorros, ingresos fijos) cuando el usuario reporta un
  duplicado o un monto mal anotado. Confirma lo corregido en una frase.

## 3. Pantallas

| Pantalla | Contenido |
|---|---|
| Login | tarjeta centrada, botón Google/demo |
| Onboarding | tarjeta: "¿Cómo te decimos?" (primer nombre) + fecha de nacimiento + Empezar |
| Dashboard (home) | alertas → hero Balance (ingresos vs gastos, tasa, disponible) → stats (hoy/promedio/proyección) → metas → regla 50/30/20 → gastos por categoría (donut) → tendencia → mayor gasto → coach. FAB "💬 Registrar" |
| Chat | header con ← volver, log burbujas, composer fijo abajo |

## 4. Animaciones (sutiles, 150–400 ms, ease-out)

- Cards del dashboard: fade-up escalonado al cargar.
- Números clave (ingresos/gastos/balance): count-up.
- Barras de progreso (mes, metas, regla): transición de ancho.
- Chat: burbujas entran con slide-in; indicador "escribiendo" con 3 puntos.
- Chat abre deslizándose desde abajo; ← lo cierra.
- Respeta `prefers-reduced-motion`.

## 5. Design system

- Tema oscuro fintech. Fondo `#0a0e18`, superficies elevadas con borde sutil,
  acento índigo→violeta reservado para CTA y datos clave, verde/rojo solo para
  semántica (positivo/negativo).
- Tipografía system-ui; jerarquía por peso y tamaño (números grandes = héroe).
- Radios 18–20 px, sombras suaves, espaciado 4/8/12/16.

## 6. Cambios de backend

- `POST /api/perfil` {nombre, fecha_nacimiento} con validación (edad 5–120).
- El nombre de Google ya no pisa el apodo elegido (solo se usa si está vacío).
- Herramientas nuevas del agente: `listar_movimientos`, `editar_movimiento`,
  `eliminar_movimiento` (con ownership por user_id en la capa de datos).
