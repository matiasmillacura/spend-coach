"use strict";

// ---------- utilidades ----------
const $ = (sel) => document.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function formatCLP(n) {
  return "$" + Math.round(n || 0).toLocaleString("es-CL");
}
function el(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}

function animateCLP(elem, target) {
  const to = Math.round(target || 0);
  const from = Number(elem.dataset.v || 0);
  elem.dataset.v = to;
  if (REDUCE_MOTION || from === to) { elem.textContent = formatCLP(to); return; }
  const t0 = performance.now(), dur = 550;
  (function step(t) {
    const p = Math.min(1, (t - t0) / dur);
    const ease = 1 - Math.pow(1 - p, 3);
    elem.textContent = formatCLP(from + (to - from) * ease);
    if (p < 1) requestAnimationFrame(step);
  })(t0);
}

// ---------- sesión / API ----------
let USUARIO = null;

async function api(path, opts = {}) {
  const res = await fetch(path, Object.assign({ credentials: "same-origin" }, opts));
  if (res.status === 401) showLogin();
  return res;
}

// ---------- pantallas ----------
function configLoginButton(estado) {
  const e = estado || {};
  document.querySelectorAll(".js-google").forEach((btn) => {
    if (e.auth_google) return;
    if (e.demo) btn.querySelector(".js-google-text").textContent = "Entrar (modo demo)";
    else btn.hidden = true;
  });
}

$("#go-register").addEventListener("click", () => {
  $("#card-login").hidden = true;
  $("#card-register").hidden = false;
  $("#rg-fecha").max = new Date().toISOString().slice(0, 10);
});
$("#go-login").addEventListener("click", () => {
  $("#card-register").hidden = true;
  $("#card-login").hidden = false;
});

function entrar(user) {
  if (!user.nombre || !user.fecha_nacimiento) showOnboarding(user);
  else showApp(user);
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#li-error");
  err.hidden = true;
  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#li-email").value, password: $("#li-pass").value }),
    });
    const data = await res.json();
    if (data.ok) return entrar(data.user);
    err.textContent = data.error || "No pudimos iniciar sesión.";
    err.hidden = false;
  } catch (e2) {
    err.textContent = "Sin conexión con el servidor.";
    err.hidden = false;
  }
});

$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#rg-error");
  err.hidden = true;
  try {
    const res = await fetch("/auth/register", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nombre_completo: $("#rg-nombre").value,
        apodo: $("#rg-apodo").value,
        rut: $("#rg-rut").value,
        fecha_nacimiento: $("#rg-fecha").value,
        email: $("#rg-email").value,
        password: $("#rg-pass").value,
      }),
    });
    const data = await res.json();
    if (data.ok) return entrar(data.user);
    err.textContent = data.error || "No pudimos crear la cuenta.";
    err.hidden = false;
  } catch (e2) {
    err.textContent = "Sin conexión con el servidor.";
    err.hidden = false;
  }
});
function showLogin() {
  document.body.classList.remove("chat-open");
  $("#login-gate").hidden = false;
  $("#onboarding").hidden = true;
  $("#app").hidden = true;
}
function showOnboarding(user) {
  $("#login-gate").hidden = true;
  $("#onboarding").hidden = false;
  $("#app").hidden = true;
  const nom = $("#onb-nombre");
  if (user && user.nombre && !nom.value) nom.value = user.nombre.split(" ")[0];
  $("#onb-fecha").max = new Date().toISOString().slice(0, 10);
}
function showApp(user) {
  USUARIO = user || USUARIO;
  $("#login-gate").hidden = true;
  $("#onboarding").hidden = true;
  $("#app").hidden = false;
  const primer = ((USUARIO && USUARIO.nombre) || "").split(" ")[0];
  $("#greet-name").textContent = primer ? `Hola, ${primer}` : "Hola";
  cargarDashboard();
  if (USUARIO && !USUARIO.onboarding_completo) goChat();
}

async function checkAuth() {
  try {
    const res = await fetch("/api/me", { credentials: "same-origin" });
    const data = await res.json();
    if (res.ok && data.ok) {
      const u = data.user;
      if (!u.nombre || !u.fecha_nacimiento) showOnboarding(u);
      else showApp(u);
    } else {
      configLoginButton(data);
      showLogin();
    }
  } catch (e) {
    configLoginButton({ auth_google: true });
    showLogin();
  }
}
function logout() { window.location.href = "/logout"; }
document.querySelectorAll(".js-logout").forEach((b) => b.addEventListener("click", logout));

// ---------- onboarding express (formulario) ----------
$("#onb-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#onb-error");
  err.hidden = true;
  const nombre = $("#onb-nombre").value.trim();
  const fecha = $("#onb-fecha").value;
  if (!nombre || !fecha) {
    err.textContent = "Completa tu nombre y tu fecha de nacimiento 🙂";
    err.hidden = false;
    return;
  }
  try {
    const res = await api("/api/perfil", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nombre, fecha_nacimiento: fecha }),
    });
    const data = await res.json();
    if (data.ok) {
      showApp(data.user);
    } else {
      err.textContent = data.error || "No pudimos guardar. Intenta de nuevo.";
      err.hidden = false;
    }
  } catch (e2) {
    err.textContent = "Sin conexión con el servidor.";
    err.hidden = false;
  }
});

// ---------- navegación dashboard ⇄ chat ----------
const chatView = $("#view-chat");
function goChat() {
  document.body.classList.add("chat-open");
  chatView.classList.add("open");
  chatView.setAttribute("aria-hidden", "false");
  cargarChat();
  setTimeout(() => $("#chat-input").focus({ preventScroll: true }), 350);
}
function goDash() {
  document.body.classList.remove("chat-open");
  chatView.classList.remove("open");
  chatView.setAttribute("aria-hidden", "true");
  cargarDashboard();
}
$("#fab-chat").addEventListener("click", goChat);
$("#btn-back").addEventListener("click", goDash);
$("#btn-refresh").addEventListener("click", () => { mesVista = null; cargarDashboard(); });

let mesVista = null;
function shiftMes(delta) {
  const base = mesVista || new Date().toISOString().slice(0, 7);
  let [y, m] = base.split("-").map(Number);
  m += delta;
  if (m < 1) { m = 12; y--; }
  if (m > 12) { m = 1; y++; }
  mesVista = `${y}-${String(m).padStart(2, "0")}`;
  cargarDashboard();
}
$("#mes-prev").addEventListener("click", () => shiftMes(-1));
$("#mes-next").addEventListener("click", () => shiftMes(1));

// ---------- CHAT ----------
const chatLog = $("#chat-log");
const chatForm = $("#chat-form");
const chatInput = $("#chat-input");
let chatCargado = false;

function burbuja(texto, clase) {
  const div = document.createElement("div");
  div.className = "bubble " + clase;
  div.textContent = texto;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}
function burbujaTyping() {
  const div = document.createElement("div");
  div.className = "bubble bubble--bot bubble--typing";
  for (let i = 0; i < 3; i++) {
    const s = document.createElement("span");
    s.className = "tdot";
    div.appendChild(s);
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

async function cargarChat() {
  if (chatCargado) return;
  let data;
  try {
    const res = await api("/api/historial");
    if (!res.ok) return;
    data = await res.json();
  } catch (e) {
    return;
  }
  chatCargado = true;
  chatLog.innerHTML = "";
  let mensajes = data.mensajes || [];
  if (mensajes.length && mensajes[0].rol === "user" && mensajes[0].texto.trim().toLowerCase() === "hola") {
    mensajes = mensajes.slice(1);
  }
  if (mensajes.length === 0) {
    enviarAlCoach("Hola", { mostrarUsuario: false });
  } else {
    for (const m of mensajes) {
      burbuja(m.texto, m.rol === "user" ? "bubble--me" : "bubble--bot");
    }
  }
}

async function enviarAlCoach(texto, opts = {}) {
  if (opts.mostrarUsuario !== false) {
    if (opts.thumbUrl) {
      const div = document.createElement("div");
      div.className = "bubble bubble--me";
      const img = document.createElement("img");
      img.className = "bubble__img";
      img.src = opts.thumbUrl;
      img.alt = "foto enviada";
      div.appendChild(img);
      if (texto) div.appendChild(document.createTextNode(texto));
      chatLog.appendChild(div);
      chatLog.scrollTop = chatLog.scrollHeight;
    } else {
      burbuja(texto, "bubble--me");
    }
  }
  const pensando = burbujaTyping();
  try {
    const body = { texto };
    if (opts.imagenB64) {
      body.imagen = opts.imagenB64;
      body.imagen_tipo = opts.imagenTipo || "image/jpeg";
    }
    const res = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    pensando.remove();
    if (data.ok) {
      burbuja(data.reply, "bubble--bot");
    } else {
      burbuja("⚠️ " + (data.error || "No pude responder ahora."), "bubble--err");
    }
  } catch (err) {
    pensando.remove();
    burbuja("⚠️ No hay conexión con el servidor.", "bubble--err");
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const texto = chatInput.value.trim();
  if (!texto) return;
  chatInput.value = "";
  enviarAlCoach(texto);
});

// ---------- 📷 foto de boleta ----------
const fotoInput = $("#foto-input");
$("#btn-foto").addEventListener("click", () => fotoInput.click());
fotoInput.addEventListener("change", async () => {
  const file = fotoInput.files && fotoInput.files[0];
  fotoInput.value = "";
  if (!file) return;
  try {
    const { b64, thumbUrl } = await comprimirImagen(file);
    const texto = chatInput.value.trim();
    chatInput.value = "";
    enviarAlCoach(texto, { imagenB64: b64, imagenTipo: "image/jpeg", thumbUrl });
  } catch (e) {
    burbuja("⚠️ No pude leer esa imagen. Intenta con otra foto.", "bubble--err");
  }
});

function comprimirImagen(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const MAX = 1400;
      const escala = Math.min(1, MAX / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * escala);
      canvas.height = Math.round(img.height * escala);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.8);
      resolve({ b64: dataUrl.split(",")[1], thumbUrl: dataUrl });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("img")); };
    img.src = url;
  });
}

// ---------- 🎙️ dictado por voz ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const btnVoz = $("#btn-voz");
if (!SR) {
  btnVoz.hidden = true;
} else {
  let rec = null, grabando = false;
  btnVoz.addEventListener("click", () => {
    if (grabando) { rec.stop(); return; }
    rec = new SR();
    rec.lang = "es-CL";
    rec.interimResults = true;
    rec.onresult = (ev) => {
      let t = "";
      for (const r of ev.results) t += r[0].transcript;
      chatInput.value = t;
    };
    rec.onend = () => {
      grabando = false;
      btnVoz.classList.remove("rec");
      chatInput.focus();
    };
    rec.onerror = rec.onend;
    grabando = true;
    btnVoz.classList.add("rec");
    rec.start();
  });
}

// ---------- DASHBOARD ----------
async function cargarDashboard() {
  let d;
  try {
    const url = mesVista ? `/api/dashboard?mes=${mesVista}` : "/api/dashboard";
    const res = await api(url);
    if (!res.ok) return;
    d = await res.json();
  } catch (e) {
    return;
  }
  mesVista = d.es_mes_actual ? null : d.mes;
  $("#mes-next").disabled = d.es_mes_actual;
  renderFinanzas(d.finanzas);
  renderKPIs(d);
  renderInsights(d.insights);
  renderDonut(d);
  renderBars(d);
  renderMayor(d);
  if (d.es_mes_actual) cargarCoach();
  else $("#coach-text").textContent = "Estás mirando un mes anterior. Vuelve al mes actual para el comentario del coach.";
}

function renderInsights(insights) {
  const card = $("#insights-card");
  const ul = $("#insights-list");
  ul.innerHTML = "";
  if (!insights || !insights.length) { card.hidden = true; return; }
  card.hidden = false;
  for (const t of insights) {
    const li = document.createElement("li");
    li.textContent = t;
    ul.appendChild(li);
  }
}

function renderFinanzas(f) {
  if (!f) return;
  $("#deficit-alert").hidden = !f.deficit;
  $("#sin-ingreso").hidden = !f.sin_ingreso;

  animateCLP($("#fin-ingreso"), f.ingreso_mensual);
  animateCLP($("#fin-gasto"), f.gasto_mensual);
  const bal = $("#fin-balance");
  animateCLP(bal, f.balance);
  bal.style.color = f.balance < 0 ? "var(--bad)" : "var(--good)";

  const ratio = f.ingreso_mensual > 0
    ? Math.min(100, Math.round((f.gasto_mensual / f.ingreso_mensual) * 100)) : 0;
  const barIn = $("#fin-bar-in");
  barIn.style.width = ratio + "%";
  barIn.style.background = f.deficit ? "var(--bad)" : "var(--good)";

  const labelBalance = document.querySelector(".hero__foot .hero__label");
  labelBalance.textContent = f.modo_arranque ? "Te queda (desde tu punto de partida)" : "Balance del mes";

  const tasa = $("#fin-tasa");
  if (f.modo_arranque) {
    tasa.textContent = "mes de arranque";
    tasa.className = "badge badge--neutral";
  } else if (f.tasa_ahorro_pct == null) {
    tasa.textContent = "—"; tasa.className = "badge badge--neutral";
  } else {
    tasa.textContent = "Ahorro " + f.tasa_ahorro_pct + "%";
    tasa.className = "badge " + (f.tasa_ahorro_pct >= 20 ? "badge--good"
      : f.tasa_ahorro_pct >= 0 ? "badge--neutral" : "badge--bad");
  }

  $("#fin-disponible").textContent = f.disponible == null ? ""
    : "Disponible sin tocar tu ahorro: " + formatCLP(f.disponible);

  renderMetas(f.metas);
  renderRegla(f);
  renderPresupuestos(f.presupuestos);
  renderSubs(f);
}

function renderPresupuestos(pptos) {
  const card = $("#ppto-card");
  const ul = $("#ppto-list");
  ul.innerHTML = "";
  if (!pptos || !pptos.length) { card.hidden = true; return; }
  card.hidden = false;
  for (const p of pptos) {
    const li = document.createElement("li");
    li.className = "regla__row";
    const head = document.createElement("div");
    head.className = "regla__head";
    const l = document.createElement("span");
    l.textContent = p.categoria;
    l.style.textTransform = "capitalize";
    const v = document.createElement("span");
    v.className = "regla__nums";
    v.textContent = `${formatCLP(p.gastado)} de ${formatCLP(p.monto)} (${p.pct}%)`;
    head.append(l, v);
    const bar = document.createElement("div");
    bar.className = "regla__bar";
    const fill = document.createElement("div");
    fill.className = "regla__fill";
    fill.style.background = p.pct >= 100 ? "var(--bad)" : p.pct >= 80 ? "#fbbf24" : "var(--good)";
    requestAnimationFrame(() => { fill.style.width = Math.min(100, p.pct) + "%"; });
    bar.appendChild(fill);
    li.append(head, bar);
    ul.appendChild(li);
  }
}

function renderSubs(f) {
  const card = $("#subs-card");
  const ul = $("#subs-list");
  ul.innerHTML = "";
  const subs = f.suscripciones || [];
  if (!subs.length) { card.hidden = true; return; }
  card.hidden = false;
  $("#subs-total").textContent = formatCLP(f.gasto_fijo_mes) + "/mes";
  for (const s of subs) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = "var(--accent)";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.descripcion;
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = formatCLP(s.monto);
    li.append(dot, name, val);
    ul.appendChild(li);
  }
}

function renderMetas(metas) {
  const ul = $("#metas-list");
  ul.innerHTML = "";
  if (!metas || !metas.length) {
    const li = document.createElement("li");
    li.className = "meta-empty";
    li.textContent = "Aún no tienes metas. Dile al coach para qué quieres ahorrar 🙂";
    ul.appendChild(li);
    return;
  }
  for (const m of metas) {
    const li = document.createElement("li");
    li.className = "meta";
    const top = document.createElement("div");
    top.className = "meta__top";
    const name = document.createElement("span");
    name.className = "meta__name";
    name.textContent = m.nombre;
    const pct = document.createElement("span");
    pct.className = "meta__pct";
    pct.textContent = m.progreso_pct == null ? "" : m.progreso_pct + "%";
    top.append(name, pct);
    li.appendChild(top);

    if (m.progreso_pct != null) {
      const bar = document.createElement("div");
      bar.className = "meta__bar";
      const fill = document.createElement("div");
      fill.className = "meta__fill";
      requestAnimationFrame(() => { fill.style.width = Math.min(100, m.progreso_pct) + "%"; });
      bar.appendChild(fill);
      li.appendChild(bar);
    }

    const sub = document.createElement("p");
    sub.className = "meta__sub";
    let t = "";
    if (m.tipo === "monto_fecha") {
      t = `${formatCLP(m.ahorrado)} de ${formatCLP(m.objetivo)}`;
      if (m.nota === "vencida") t += " · plazo cumplido";
      else if (m.nota === "lograda") t += " · ¡lograda! 🎉";
      else if (m.cuota_sugerida != null && m.meses_restantes != null) {
        t += ` · ${formatCLP(m.cuota_sugerida)}/mes (${m.meses_restantes} ${m.meses_restantes === 1 ? "mes" : "meses"})`;
      }
    } else if (m.tipo === "monto_mensual") {
      t = `Meta ${formatCLP(m.objetivo)}/mes · total ${formatCLP(m.ahorrado)}`;
    } else if (m.tipo === "porcentaje") {
      t = `Meta ${m.porcentaje}% del ingreso (~${formatCLP(m.objetivo)}/mes) · total ${formatCLP(m.ahorrado)}`;
    }
    sub.textContent = t;
    if (m.nota === "vencida") sub.classList.add("meta__nota");
    li.appendChild(sub);
    ul.appendChild(li);
  }
}

function renderRegla(f) {
  const r = f.regla, rep = f.reparto;
  $("#regla-nums").textContent = `${r.pct_necesidades}/${r.pct_deseos}/${r.pct_ahorro}`;
  const ul = $("#regla-list");
  ul.innerHTML = "";
  const filas = [
    ["Necesidades", rep.necesidades, "menor"],
    ["Deseos", rep.deseos, "menor"],
    ["Ahorro", rep.ahorro, "mayor"],
  ];
  for (const [label, d, sentido] of filas) {
    const li = document.createElement("li");
    li.className = "regla__row";
    const head = document.createElement("div");
    head.className = "regla__head";
    const l = document.createElement("span");
    l.textContent = label;
    const v = document.createElement("span");
    v.className = "regla__nums";
    v.textContent = (d.pct == null ? "—" : d.pct + "%") + " · meta " + d.objetivo_pct + "%";
    head.append(l, v);
    const bar = document.createElement("div");
    bar.className = "regla__bar";
    const fill = document.createElement("div");
    fill.className = "regla__fill";
    let ok = null;
    if (d.pct != null) ok = sentido === "mayor" ? d.pct >= d.objetivo_pct : d.pct <= d.objetivo_pct;
    fill.style.background = ok == null ? "var(--surface-3)" : (ok ? "var(--good)" : "var(--bad)");
    requestAnimationFrame(() => { fill.style.width = (d.pct == null ? 0 : Math.min(100, d.pct)) + "%"; });
    bar.appendChild(fill);
    li.append(head, bar);
    ul.appendChild(li);
  }
}

function renderKPIs(d) {
  $("#dash-mes").textContent = nombreMes(d.mes);
  $("#kpi-hoy").textContent = d.es_mes_actual ? formatCLP(d.gasto_hoy) : "—";
  $("#kpi-prom").textContent = formatCLP(d.promedio_diario);
  $("#kpi-proj").textContent = d.proyeccion == null ? "—" : formatCLP(d.proyeccion);

  const badge = $("#kpi-var");
  if (d.variacion_pct == null) {
    badge.textContent = d.mes_prev_con_datos ? "sin cambios" : "primer mes";
    badge.className = "badge badge--neutral";
  } else {
    const sube = d.variacion_pct > 0;
    badge.textContent = (sube ? "▲ " : "▼ ") + Math.abs(d.variacion_pct) + "% vs mes ant.";
    badge.className = "badge " + (sube ? "badge--up" : "badge--down");
  }
}

function nombreMes(iso) {
  const [a, m] = iso.split("-").map(Number);
  const meses = ["enero","febrero","marzo","abril","mayo","junio","julio",
                 "agosto","septiembre","octubre","noviembre","diciembre"];
  return `${meses[m - 1]} ${a}`;
}

function renderDonut(d) {
  const svg = $("#donut");
  svg.innerHTML = "";
  $("#donut-total").textContent = formatCLP(d.total);
  const legend = $("#cat-legend");
  legend.innerHTML = "";

  const R = 45, C = 60, circ = 2 * Math.PI * R;
  if (!d.categorias.length || d.total === 0) {
    svg.appendChild(el("circle", { cx: C, cy: C, r: R, fill: "none",
      stroke: "#283150", "stroke-width": 14 }));
    const li = document.createElement("li");
    li.className = "name";
    li.style.color = "var(--muted)";
    li.textContent = "Sin gastos aún — registra el primero con tu coach";
    legend.appendChild(li);
    return;
  }

  let offset = 0;
  for (const c of d.categorias) {
    const frac = c.total / d.total;
    svg.appendChild(el("circle", {
      cx: C, cy: C, r: R, fill: "none",
      stroke: c.color, "stroke-width": 14,
      "stroke-dasharray": `${frac * circ} ${circ}`,
      "stroke-dashoffset": -offset,
      "stroke-linecap": "butt",
    }));
    offset += frac * circ;

    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = c.color;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = c.categoria;
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = formatCLP(c.total);
    const pct = document.createElement("span");
    pct.className = "pct";
    pct.textContent = c.pct + "%";
    li.append(dot, name, val, pct);
    legend.appendChild(li);
  }
}

function renderBars(d) {
  const svg = $("#bars");
  svg.innerHTML = "";
  const H = 120, pad = 6, W = 320;
  const serie = d.serie;
  const conGasto = serie.filter((s) => s.monto > 0);

  const pace = ` · día ${d.dia_actual} de ${d.dias_mes}`;
  if (!conGasto.length) {
    $("#bars-caption").textContent = "Sin gastos este mes todavía" + pace;
    return;
  }

  const max = Math.max(...serie.map((s) => s.monto));
  const n = serie.length;
  const bw = (W - pad * 2) / n;
  const hoyISO = serie[d.dia_actual - 1] ? serie[d.dia_actual - 1].fecha : null;

  serie.forEach((s, i) => {
    const h = (s.monto / max) * (H - 24);
    const x = pad + i * bw;
    const esHoy = s.fecha === hoyISO;
    svg.appendChild(el("rect", {
      x: x + bw * 0.15, y: H - h - 4, width: bw * 0.7,
      height: Math.max(h, s.monto > 0 ? 2 : 0), rx: 2,
      fill: esHoy ? "#8b5cf6" : (s.monto > 0 ? "#6366f1" : "#283150"),
      opacity: s.monto > 0 ? 1 : 0.5,
    }));
  });

  $("#bars-caption").textContent =
    `Día más caro: ${formatCLP(max)} · ${conGasto.length} días con gasto` + pace;
}

function renderMayor(d) {
  const card = $("#mayor-card");
  if (!d.mayor_gasto) { card.hidden = true; return; }
  card.hidden = false;
  $("#mayor-monto").textContent = formatCLP(d.mayor_gasto.monto);
  $("#mayor-desc").textContent =
    `${d.mayor_gasto.descripcion} · ${d.mayor_gasto.categoria}`;
}

async function cargarCoach() {
  const box = $("#coach-text");
  box.textContent = "Analizando tus finanzas…";
  try {
    const res = await api("/api/coaching");
    if (!res.ok) { box.textContent = "El coach no está disponible ahora."; return; }
    const data = await res.json();
    if (!data.ok) {
      box.textContent = "El coach no está disponible ahora.";
      return;
    }
    const t = data.texto || "";
    const idx = t.indexOf("💬");
    if (idx >= 0) {
      box.textContent = t.slice(idx + 2).trim();
    } else if (/No hay gastos/i.test(t)) {
      box.textContent = "Aún no hay movimientos este mes para comentar.";
    } else {
      box.textContent = "El coach no está disponible ahora.";
    }
  } catch (e) {
    box.textContent = "No pude conectar con el coach.";
  }
}

// ---------- arranque ----------
checkAuth();
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
