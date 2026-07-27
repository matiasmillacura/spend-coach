# Coach de gastos (web multiusuario)

[![CI](https://github.com/matiasmillacura/spend-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/matiasmillacura/spend-coach/actions/workflows/ci.yml)

App **mobile-first** con dos vistas: un **chat** que es un **coach financiero
conversacional** y un **dashboard** con el análisis completo de tu plata.

El chat es un **agente** (Claude con herramientas y memoria): te hace un
onboarding por pasos (nombre → fecha de nacimiento → ingresos → meta de ahorro),
registra por lenguaje natural tus **gastos, ingresos y ahorros** ("gasté 12 lucas
en almuerzo", "me llegó el sueldo, 800k", "aparté 50 lucas para vacaciones"), te
pregunta el propósito de cada ahorro y **aconseja** de forma proactiva para mejorar
tu calidad de vida financiera (no es un CRUD).

El dashboard compara **ingresos vs gastos**: balance, tasa de ahorro, disponible
para gastar, alerta de déficit, **metas de ahorro con proyección** y la **regla
50/30/20 configurable** (tú pones tus %, el coach te avisa si no es viable).

Es **multiusuario**: cada persona entra con su cuenta de **Google** y ve solo sus
propios datos. Todo el lenguaje natural lo procesa la **API de Claude** (Anthropic).

## Requisitos

- Python 3.10+
- Una **API key de Claude** (https://console.anthropic.com) — se paga por uso
  (con Haiku, registrar un gasto cuesta ~0,1 centavo de dólar).
- (Para el login real) credenciales **OAuth de Google**. Sin ellas, la app corre
  en **modo demo** de un solo usuario local — útil para desarrollar.

## Instalación

```bash
cd coach_gastos
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # y edita .env con tus valores (ver abajo)
```

Genera una clave de sesión:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # → COACH_SECRET_KEY
```

Pon en `.env` al menos `ANTHROPIC_API_KEY` y `COACH_SECRET_KEY`.
**Nunca subas `.env` al repo** (ya está en `.gitignore`).

## Uso — desarrollo local

```bash
. .venv/bin/activate
python app.py            # → http://127.0.0.1:8000
```

- **Sin** credenciales de Google → **modo demo**: "Entrar (modo demo)" abre una
  sesión local sin OAuth. Ideal para probar rápido.
- **Con** credenciales de Google → login real con tu cuenta.

## Login con Google (OAuth)

1. En https://console.cloud.google.com/apis/credentials crea un
   **ID de cliente de OAuth → Aplicación web**.
2. Agrega el **URI de redirección autorizado** (debe coincidir EXACTO):
   - local: `http://localhost:8000/auth/callback`
   - producción: `https://TU-DOMINIO/auth/callback`
3. Copia el `client_id` y `client_secret` a `.env`
   (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

## Despliegue en producción

```bash
gunicorn 'app:app' --bind 0.0.0.0:8000 --workers 3
```

- Sirve **detrás de HTTPS** (Caddy/Nginx o la plataforma) y pon `COACH_HTTPS=1`
  para exigir cookies seguras.
- Usa **PostgreSQL**: define `DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/coach`.
- Define todos los secretos en el entorno / gestor de secretos de la plataforma,
  no en el código.

## Configuración (variables de entorno)

| Variable | Default | Para qué |
|----------|---------|----------|
| `COACH_SECRET_KEY` | (inseguro) | firma las cookies de sesión — **obligatorio en prod** |
| `ANTHROPIC_API_KEY` | (vacío) | clave de la API de Claude |
| `CLAUDE_MODEL_CHAT` | `claude-sonnet-4-6` | modelo del chat de coaching (conversación + herramientas) |
| `CLAUDE_MODEL_COACH` | `claude-opus-4-8` | modelo del comentario del coach en el dashboard |
| `CLAUDE_MODEL_EXTRACTOR` | `claude-haiku-4-5` | (reservado) extracción puntual |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | (vacío) | login con Google; si faltan → modo demo |
| `DATABASE_URL` | `sqlite:///gastos.db` | base de datos (Postgres en prod) |
| `COACH_HOST` / `COACH_PORT` | `127.0.0.1` / `8000` | dirección del servidor de desarrollo |
| `COACH_HTTPS` | `0` | `1` en producción (cookies solo por HTTPS) |

## Estructura

```
coach_gastos/
  config.py       → configuración desde variables de entorno (.env)
  db.py           → SQLAlchemy: users(+perfil), gastos, ingresos_fijos, ingresos,
                    metas_ahorro, ahorros, regla_presupuesto, mensajes. Todo por user_id.
  coach_agent.py  → agente conversacional (Claude + herramientas + memoria): onboarding,
                    registro por chat (gasto/ingreso/ahorro) y coaching proactivo
  extractor.py    → cliente Anthropic compartido (get_client)
  coach.py        → comentario del coach para el dashboard (ingreso vs gasto)
  dashboard.py    → análisis financiero: balance, tasa de ahorro, metas, regla 50/30/20
  auth.py         → login con Google (Authlib), sesión, login_required
  app.py          → app Flask (API + estáticos)  ← punto de entrada
  web/            → frontend mobile-first (chat-agente + dashboard, gráficos SVG)
```

## Costo (API de Claude, por uso)

No necesitas el plan Pro de $20. Cargas crédito en la consola de Anthropic y
pagas por token. Registrar un gasto con **Haiku 4.5** cuesta ~US$0,0009
(≈ 100 gastos por 9 centavos). El comentario del coach usa Opus/Sonnet, se llama
poco y también es de costo despreciable.

## Roadmap

- **Fase 0 (hecha):** cuentas multiusuario + login Google, API de Claude, despliegue.
- **Coach financiero (hecha):** onboarding conversacional, ingresos (fijos y variables),
  metas de ahorro con propósito, ahorros, comparativa ingresos vs gastos, tasa de ahorro,
  disponible, alerta de déficit, proyección de metas y regla 50/30/20 configurable con
  criterio de la IA. Chat como agente con memoria.
- **Siguiente:** ver/borrar movimientos desde la web, navegación entre meses, PWA instalable,
  pantalla de ajustes de perfil/regla.
```
