# 🚀 Desplegar el Coach de Gastos (Render + Neon, $0/mes)

La app queda en internet con HTTPS, cada usuario entra con su Google y sus datos
viven en Postgres (Neon). Costo cero; única pega del plan gratis de Render: si
nadie la usa por ~15 min, la primera visita tarda ~30–60 s en despertar.

## 1. Base de datos — Neon (5 min)

1. Crea cuenta en <https://neon.tech> (con tu Google).
2. "New project" → nombre `coach-gastos` → región AWS São Paulo (la más cercana).
3. Copia el **connection string** (`postgresql://usuario:clave@...neon.tech/...`).
   Guárdalo: es tu `DATABASE_URL`.

> ⚠️ No uses el Postgres gratis de Render: expira a los 30 días y borra los
> datos. El de Neon no expira (se suspende cuando no se usa y despierta solo).

## 2. Sube el código a GitHub (listo)

El repo ya vive en <https://github.com/matiasmillacura/spend-coach> y los
secretos están fuera (`.gitignore` cubre `.env` y `*.db`). Para actualizar:
`git push origin main`.

## 3. Servicio web — Render (10 min)

1. Crea cuenta en <https://render.com> (con tu GitHub).
2. **New + → Blueprint** → elige tu repo `spend-coach` (Render lee `render.yaml`).
3. Cuando pida las variables marcadas como secretas, pega:
   - `ANTHROPIC_API_KEY` → tu clave de <https://console.anthropic.com>
   - `DATABASE_URL` → el connection string de Neon (paso 1)
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` → paso 4 (puedes volver y pegarlas después)
4. Deploy. Tu URL queda tipo `https://coach-gastos.onrender.com`.

> La app se NIEGA a arrancar en producción si falta la clave de sesión o el login
> de Google (fail-fast a propósito). Si el deploy falla, mira los logs: el mensaje
> dice exactamente qué variable falta.

## 4. Login con Google para el dominio real (5 min)

1. <https://console.cloud.google.com/apis/credentials> → tu **ID de cliente OAuth**
   (o crea uno: tipo "Aplicación web").
2. En **URIs de redireccionamiento autorizados** agrega EXACTO:
   `https://TU-APP.onrender.com/auth/callback`
3. En la pantalla de consentimiento, agrega como **usuarios de prueba** los Gmail
   que quieras dejar entrar (o publica la app para abrirla a cualquiera).
4. Pega `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET` en Render → Environment →
   redeploy.

## 5. Probar e instalar como app 📱

- Abre `https://TU-APP.onrender.com` en el celular → Entrar con Google.
- **Android/Chrome:** menú ⋮ → "Agregar a pantalla de inicio" (o el aviso de
  instalación). **iPhone/Safari:** Compartir → "Agregar a inicio".
- Queda con ícono propio, pantalla completa y abre al instante (PWA).

## Motor del coach en producción

En producción NO definas `COACH_ENGINE` (usa el motor original): el motor
LangGraph guarda su memoria en un archivo SQLite (`checkpoints.db`) y el disco
de Render es efímero — cada redeploy la borraría. Para usar LangGraph en prod
hay que migrar el checkpointer a Postgres (`langgraph-checkpoint-postgres`).

## Mantención

- **Actualizar la app:** `git push` → Render redeploya solo.
- **Costos:** Render free + Neon free = $0. La API de Claude se paga por uso
  (centavos). Si el arranque en frío molesta, Render Starter (~US$7/mes) lo quita.
- **Datos:** viven en Neon, por cuenta de Google; borrar el servicio de Render NO
  borra los datos.
