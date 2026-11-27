/* Panel de Nudo de SEN (nudodesen.com/admin).
   Secciones: Inicio (tablero y métricas), Productos (búsqueda, filtros, lote, arrastrar para ordenar),
   Categorías, Historial (versiones y actividad) y Ajustes. Editor de producto con fotos, videos, 3D y giro 360°.
   Avisos y confirmaciones con SweetAlert2; ordenar con SortableJS; recortar fotos con Cropper.js. */
(() => {
'use strict';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = n => '<svg class="i"><use href="#i-' + n + '"/></svg>';
const url = src => '/' + src;
const money = n => 'S/ ' + Number(n).toLocaleString('es-PE');
const clon = o => JSON.parse(JSON.stringify(o));
const num = n => Number(n || 0).toLocaleString('es-PE');
const norm = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const slug = s => norm(s).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) || 'producto';
const TIPOS = { imagen: ['foto', 'Foto'], video: ['video', 'Video'], modelo: ['3d', '3D'], giro: ['360', '360°'] };
const SUG_MAT = ['Hilo de algodón', 'Hilo acrílico', 'Hilo de leche', 'Relleno siliconado', 'Ojos de seguridad', 'Base de madera', 'Fieltro', 'Cinta bordada', 'Plumas', 'Pompones', 'Alambre forrado', 'Hilo de bordar'];

let C = null;      // catálogo que se está editando
let snap = null;   // última versión guardada en el servidor (para deshacer y para volver atrás si algo falla)
let MET = null;    // métricas del periodo
let dias = 30, pestana = 'inicio';
let sel = new Set();
let edId = null, edMed = [], edMat = [], edInicial = '', subiendo = 0;
let CS = [];       // copia de las categorías mientras se editan

/* ================= SweetAlert2 con la marca ================= */
// El editor es un <dialog> (capa superior): los avisos se muestran dentro de él cuando está abierto.
const destino = () => $('ed').open ? $('ed') : 'body';
const Dlg = Swal.mixin({ buttonsStyling: false, reverseButtons: true, focusCancel: false,
  customClass: { confirmButton: 'btn', cancelButton: 'btn ghost', denyButton: 'btn danger-btn' } });
const dlg = o => Dlg.fire({ target: destino(), cancelButtonText: 'Cancelar', ...o });
const confirmar = (titulo, texto, si, peligro) => dlg({ icon: peligro ? 'warning' : 'question', title: titulo, html: texto, showCancelButton: true,
  confirmButtonText: si, customClass: { confirmButton: peligro ? 'btn danger-btn' : 'btn', cancelButton: 'btn ghost' } }).then(r => r.isConfirmed);
const Aviso = Swal.mixin({ toast: true, position: 'bottom', showConfirmButton: false, timerProgressBar: true, buttonsStyling: false,
  customClass: { confirmButton: 'btn ghost' } });
const toast = (t, mal) => Aviso.fire({ target: destino(), icon: mal ? 'error' : 'success', title: t, timer: mal ? 6500 : 3000 });

/* ================= red ================= */
async function api(path, opt = {}) {
  const r = await fetch(path, { ...opt, credentials: 'same-origin',
    headers: { 'X-Nudo': '1', ...(typeof opt.body === 'string' ? { 'Content-Type': 'application/json' } : {}) } });
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (r.status === 401 && path !== '/admin/api/entrar') { mostrarLogin(); throw Object.assign(new Error('Tu sesión terminó. Vuelve a entrar.'), { silencio: true }); }
  if (r.status === 409 && j.conflicto) throw Object.assign(new Error(j.error), { conflicto: true, catalogo: j.catalogo });
  if (!r.ok || j.ok === false) throw new Error(j.error || ('Error ' + r.status));
  return j;
}
const msgError = e => e instanceof TypeError ? 'No hay conexión. Intenta de nuevo cuando vuelva la señal.' : e.message;

/* Guarda el catálogo. Si falla, vuelve a la última versión guardada; si otro dispositivo guardó antes,
   carga esa versión para no pisarla. Tras guardar ofrece «Deshacer». Devuelve true si se guardó. */
async function guardar(msg, { deshacer = true, alGuardar } = {}) {
  const antes = snap;
  try {
    const j = await api('/admin/api/catalogo', { method: 'PUT', body: JSON.stringify(C) });
    C = j.catalogo; snap = clon(C); refrescar();
    if (alGuardar) alGuardar();
    if (deshacer && antes) Aviso.fire({ target: destino(), icon: 'success', title: msg, timer: 7000, showConfirmButton: true, confirmButtonText: 'Deshacer' })
      .then(r => { if (r.isConfirmed) volverA(antes); });
    else toast(msg);
    return true;
  } catch (e) {
    if (e.conflicto) {
      C = e.catalogo; snap = clon(C); refrescar();
      await dlg({ icon: 'warning', title: 'Hubo cambios desde otro dispositivo', confirmButtonText: 'Entendido',
        text: 'Mientras editabas se guardaron cambios desde otro celular o computadora. Cargué la versión más nueva para no borrarlos: vuelve a hacer tu cambio.' });
    } else {
      C = clon(snap); refrescar();
      if (!e.silencio) toast(msgError(e), true);
    }
    return false;
  }
}
async function volverA(antes) { C = clon(antes); C.rev = snap.rev; await guardar('Cambio deshecho.', { deshacer: false }); }

/* ================= subida de archivos ================= */
function subir(blob, nombre, tipo, onp) {
  return new Promise((ok, mal) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/admin/api/subir?tipo=' + tipo + '&nombre=' + encodeURIComponent(nombre)); x.setRequestHeader('X-Nudo', '1');
    x.upload.onprogress = e => { if (e.lengthComputable && onp) onp(e.loaded / e.total); };
    x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (e) {}
      if (x.status === 401) { mostrarLogin(); return mal(new Error('Tu sesión terminó.')); }
      x.status === 200 && j.ok ? ok(j) : mal(new Error(j.error || ('Error ' + x.status))); };
    x.onerror = () => mal(new Error('Se cortó la conexión.'));
    x.send(blob);
  });
}
const aBlob = (cv, q = .86) => new Promise(r => cv.toBlob(b => b ? r(b) : cv.toBlob(r, 'image/jpeg', q), 'image/webp', q));
async function achicar(file, max = 1600) {
  // Las fotos del celular pesan mucho: se reducen a 1600 px y se pasan a WebP antes de subir.
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const cv = document.createElement('canvas'); cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k);
  cv.getContext('2d').drawImage(bmp, 0, 0, cv.width, cv.height); bmp.close && bmp.close();
  const b = await aBlob(cv); return { blob: b, nombre: file.name.replace(/\.[^.]+$/, '') + (b.type === 'image/webp' ? '.webp' : '.jpg') };
}
function portadaVideo(file) {
  // Saca un cuadro del video para usarlo de portada mientras carga.
  return new Promise(res => {
    const v = document.createElement('video'); v.muted = true; v.playsInline = true; v.preload = 'auto';
    const u = URL.createObjectURL(file); v.src = u; const fin = b => { URL.revokeObjectURL(u); res(b); };
    v.onloadedmetadata = () => { v.currentTime = Math.min(1, (v.duration || 2) / 3); };
    v.onseeked = async () => { try { const k = Math.min(1, 900 / Math.max(v.videoWidth, v.videoHeight)); const cv = document.createElement('canvas');
      cv.width = Math.round(v.videoWidth * k); cv.height = Math.round(v.videoHeight * k); cv.getContext('2d').drawImage(v, 0, 0, cv.width, cv.height); fin(await aBlob(cv, .8)); } catch (e) { fin(null); } };
    v.onerror = () => fin(null); setTimeout(() => fin(null), 15000);
  });
}
const tipoDe = f => { const n = f.name.toLowerCase();
  if (n.endsWith('.zip')) return 'zip'; if (/\.html?$/.test(n)) return 'giro'; if (n.endsWith('.glb')) return 'modelo';
  if (f.type.startsWith('video/') || /\.(mp4|webm|mov)$/.test(n)) return 'video';
  if (f.type.startsWith('image/') || /\.(jpe?g|png|webp|heic)$/.test(n)) return 'imagen'; return null; };

/* ================= utilidades de productos ================= */
const portadaDe = p => { const m = p.medios || []; const im = m.find(x => x.tipo === 'imagen'); if (im) return im.src;
  const v = m.find(x => x.poster || x.tipo === 'giro'); return v ? (v.poster || v.src) : null; };
const tieneFoto = p => (p.medios || []).some(m => m.tipo === 'imagen' || m.tipo === 'giro');
const ESTADOS = {
  visibles: p => p.visible !== false, ocultos: p => p.visible === false, 'sin-precio': p => p.precio == null,
  'sin-fotos': p => !tieneFoto(p), 'sin-medidas': p => !p.medidas || !Object.keys(p.medidas).length,
  'sin-materiales': p => !(p.materiales || []).length, 'sin-descripcion': p => !(p.detalle || '').trim() };
const PEND = [['sin-precio', 'Sin precio'], ['sin-fotos', 'Sin fotos'], ['sin-medidas', 'Sin medidas'], ['sin-materiales', 'Sin materiales'],
  ['sin-descripcion', 'Sin descripción'], ['ocultos', 'Ocultos en la web']];
const prod = id => C.productos.find(p => p.id === id);

/* ================= pestañas ================= */
function irA(t) {
  pestana = t;
  document.querySelectorAll('[role=tab]').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === t));
  document.querySelectorAll('[data-panel]').forEach(p => p.hidden = p.dataset.panel !== t);
  history.replaceState(null, '', '#' + t);
  pintarLote();
  if (t === 'historial') cargarHistorial();
  if (t === 'inicio') cargarMetricas();
  window.scrollTo(0, 0);
}
function refrescar() { pintarLista(); pintarCats(); pintarAjustes(); pintarPendientes(); pintarPortada(); }

/* ================= INICIO: tablero ================= */
async function cargarMetricas() {
  try {
    // también se pide el doble de días para comparar con el periodo anterior
    const [m, m2, a] = await Promise.all([api('/admin/api/metricas?dias=' + dias), api('/admin/api/metricas?dias=' + (dias * 2)), api('/admin/api/actividad')]);
    MET = m; MET.antes = m2.serie.slice(0, m2.serie.length - m.serie.length); pintarActividad($('ultimos'), a.actividad, 4);
  } catch (e) { if (!e.silencio) toast(msgError(e), true); }
  pintarInicio(); pintarLista(); pintarAjustes();
}
function pintarInicio() {
  const h = new Date().getHours();
  $('saludo').textContent = h < 12 ? 'Buenos días' : h < 19 ? 'Buenas tardes' : 'Buenas noches';
  const s = MET ? MET.serie : [], s0 = MET ? MET.antes || [] : [];
  const suma = (k, x = s) => x.reduce((t, d) => t + (d[k] || 0), 0);
  const kp = [['ojo-g', 'Visitas', ['inicio', 'vista'], 'portada y productos'],
    ['ojo', 'Productos vistos', ['vista'], 'páginas de producto abiertas'],
    ['corazon', 'Me interesa', ['interes'], 'agregados a «Mi lista»'],
    ['wa', 'Clics a WhatsApp', ['whatsapp'], 'personas que escribieron']];
  // flecha de tendencia contra el periodo anterior del mismo largo (texto + color, nunca solo color)
  const delta = (a, b) => { if (!MET || (!a && !b)) return '';
    if (!b) return '<span class="delta sube">nuevo</span>';
    const p = Math.round((a - b) / b * 100);
    return '<span class="delta ' + (p > 0 ? 'sube">↑ ' : p < 0 ? 'baja">↓ ' : 'igual">= ') + Math.abs(p) + ' %</span>'; };
  $('kpis').innerHTML = kp.map(([i, t, ks, d]) => { const v = ks.reduce((x, k) => x + suma(k), 0), v0 = ks.reduce((x, k) => x + suma(k, s0), 0);
    return '<div class="kpi"><div class="ib">' + ic(i) + '</div><span>' + t + '</span><b>' + (MET ? num(v) : '—') + '</b><small>' + delta(v, v0) + (MET && (v || v0) ? 'vs. ' + dias + ' días antes' : d) + '</small></div>'; }).join('');
  const vis = C ? C.productos.filter(p => p.visible !== false).length : 0, falt = C ? C.productos.filter(p => PEND.slice(0, 5).some(([k]) => ESTADOS[k](p))).length : 0;
  $('resumenTxt').textContent = C ? vis + ' productos a la vista en la tienda' + (falt ? ' · ' + falt + ' con datos por completar' : ' · todo completo') + '.' : '';
  grafico(s);
  const pr = MET ? Object.entries(MET.productos).map(([id, v]) => ({ id, ...v, p: prod(id) })).filter(x => x.p).sort((a, b) => b.vista - a.vista || b.interes - a.interes).slice(0, 6) : [];
  $('top').innerHTML = pr.length
    ? '<table><thead><tr><th>Producto</th><th class="n">Vistas</th><th class="n">Interés</th><th class="n">WhatsApp</th></tr></thead><tbody>'
      + pr.map(x => { const f = portadaDe(x.p);
        return '<tr><td><a href="#" data-ed="' + esc(x.id) + '">' + (f ? '<img src="' + esc(url(f)) + '" alt="" loading="lazy">' : '') + esc(x.p.nombre) + '</a></td>'
          + '<td class="n">' + num(x.vista) + '</td><td class="n">' + num(x.interes) + '</td><td class="n">' + num(x.whatsapp) + '</td></tr>'; }).join('')
      + '</tbody></table>'
    : '<p class="hint" style="margin:0">Todavía no hay visitas registradas en este periodo. Se empiezan a contar desde hoy.</p>';
  pintarPendientes();
}
function pintarPendientes() {
  if (!C) return;
  const items = PEND.map(([k, t]) => [k, t, C.productos.filter(ESTADOS[k]).length]).filter(x => x[2]);
  $('pend').innerHTML = items.length ? items.map(([k, t, n]) => '<button data-pend="' + k + '"><span>' + t + '</span><b>' + n + '</b></button>').join('')
    : '<p class="ok">' + ic('check') + 'Todo completo. ¡Bien!</p>';
}
// Barras de visitas por día: una sola serie (sin leyenda: el título la nombra), barras finas con 2 px de separación,
// esquinas redondeadas solo arriba, cuadrícula suave y detalle al pasar el dedo o el ratón.
function grafico(s) {
  const el = $('grafico');
  if (!s.length) { el.innerHTML = '<p class="hint" style="margin:0">Sin datos todavía.</p>'; return; }
  const W = Math.max(280, el.clientWidth || 600), H = 260, pl = 34, pr = 4, pt = 10, pb = 26;
  const v = s.map(d => (d.inicio || 0) + (d.vista || 0));
  const max = Math.max(...v), tope = Math.max(4, Math.ceil(max / 4) * 4);
  const ancho = (W - pl - pr) / s.length, bw = Math.max(1, ancho - 2), y = n => pt + (H - pt - pb) * (1 - n / tope);
  const fecha = f => { const [a, m, d] = f.split('-'); return d + '/' + m; };
  let g = '';
  for (const t of [0, tope / 2, tope]) g += '<line x1="' + pl + '" x2="' + (W - pr) + '" y1="' + y(t) + '" y2="' + y(t) + '" stroke="var(--grid)" stroke-width="1"/>'
    + '<text class="eje" x="' + (pl - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' + num(t) + '</text>';
  v.forEach((n, i) => { if (!n) return;
    const x = pl + i * ancho + 1, yt = y(n), alto = H - pb - yt, r = Math.min(4, bw / 2, alto);
    g += '<path fill="var(--bar)" d="M' + x + ' ' + (H - pb) + 'V' + (yt + r) + 'q0 -' + r + ' ' + r + ' -' + r + 'h' + (bw - 2 * r) + 'q' + r + ' 0 ' + r + ' ' + r + 'V' + (H - pb) + 'z"/>'; });
  const marcas = [0, Math.floor(s.length / 2), s.length - 1];
  for (const i of marcas) g += '<text class="eje" x="' + (pl + i * ancho + ancho / 2) + '" y="' + (H - 6) + '" text-anchor="' + (i === 0 ? 'start' : i === s.length - 1 ? 'end' : 'middle') + '">' + fecha(s[i].fecha) + '</text>';
  s.forEach((d, i) => { g += '<rect data-i="' + i + '" x="' + (pl + i * ancho) + '" y="' + pt + '" width="' + ancho + '" height="' + (H - pt - pb) + '" fill="transparent"/>'; });
  el.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Visitas por día en los últimos ' + s.length + ' días; total ' + num(v.reduce((a, b) => a + b, 0)) + '">' + g + '</svg><div class="tt" id="tt"></div>';
  const tt = $('tt'), svg = el.querySelector('svg');
  svg.addEventListener('pointermove', e => { const r = e.target.closest('rect[data-i]'); if (!r) { tt.classList.remove('on'); return; }
    const d = s[+r.dataset.i], k = el.clientWidth / W;
    tt.innerHTML = '<b>' + new Date(d.fecha + 'T12:00').toLocaleDateString('es-PE', { weekday: 'short', day: 'numeric', month: 'short' }) + '</b>'
      + num(d.inicio + d.vista) + ' visitas · portada ' + num(d.inicio) + ' · productos ' + num(d.vista);
    tt.style.left = Math.min(el.clientWidth - 90, Math.max(90, (+r.getAttribute('x') + ancho / 2) * k)) + 'px'; tt.style.top = (y(v[+r.dataset.i]) * k - 8) + 'px'; tt.classList.add('on'); });
  svg.addEventListener('pointerleave', () => tt.classList.remove('on'));
}
let tRes; window.addEventListener('resize', () => { clearTimeout(tRes); tRes = setTimeout(() => { if (pestana === 'inicio' && MET) grafico(MET.serie); }, 150); });

/* ================= PRODUCTOS: lista, filtros, selección y lote ================= */
function filtrados() {
  const q = norm($('q').value.trim()), c = $('fCat').value, e = $('fEst').value;
  return C.productos.filter(p => (!q || norm(p.nombre + ' ' + (p.detalle || '') + ' ' + (p.materiales || []).join(' ')).includes(q))
    && (!c || p.categoria === c) && (!e || ESTADOS[e](p)));
}
function fila(p) {
  const th = portadaDe(p), m = p.medios || [], n = t => m.filter(x => x.tipo === t).length, id = esc(p.id), v = MET && MET.productos[p.id];
  const medios = (n('giro') ? '<span title="Giro 360°">' + ic('360') + '360°</span>' : '') + (n('imagen') ? '<span title="Fotos">' + ic('foto') + n('imagen') + '</span>' : '')
    + (n('video') ? '<span title="Videos">' + ic('video') + n('video') + '</span>' : '') + (n('modelo') ? '<span title="Modelos 3D">' + ic('3d') + n('modelo') + '</span>' : '')
    + (v ? '<span title="Vistas en el periodo">' + ic('ojo-g') + num(v.vista) + '</span>' : '');
  return '<div class="row' + (p.visible === false ? ' off' : '') + (sel.has(p.id) ? ' sel' : '') + '" data-id="' + id + '">'
    + '<input type="checkbox" class="ck" data-sel="' + id + '" aria-label="Seleccionar ' + esc(p.nombre) + '"' + (sel.has(p.id) ? ' checked' : '') + '>'
    + '<span class="grip" title="Arrastra para ordenar">' + ic('grip') + '</span>'
    + '<button class="th" data-ed="' + id + '" aria-label="Editar ' + esc(p.nombre) + '">' + (th ? '<img src="' + esc(url(th)) + '" alt="" loading="lazy">' : ic('ovillo')) + '</button>'
    + '<div class="info"><strong>' + esc(p.nombre) + '</strong><span class="cat">' + esc(p.categoria) + (p.medidas && p.medidas.alto ? ' · ' + String(p.medidas.alto).replace('.', ',') + ' cm' : '') + '</span></div>'
    + '<span class="precio' + (p.precio == null ? ' cot' : '') + '">' + (p.precio != null ? money(p.precio) : 'A cotizar') + '</span>'
    + '<div class="meta c-medios">' + (medios || '<span>Sin fotos</span>') + '</div>'
    + '<span class="c-estado"><span class="pill ' + (p.visible === false ? 'off">Oculto' : 'on">Visible') + '</span></span>'
    + '<div class="rbtn"><button class="ico" data-vis="' + id + '" title="' + (p.visible === false ? 'Mostrar en la web' : 'Ocultar de la web') + '" aria-label="' + (p.visible === false ? 'Mostrar' : 'Ocultar') + '">' + ic(p.visible === false ? 'ojo-no' : 'ojo') + '</button>'
    + '<button class="ico" data-dup="' + id + '" title="Duplicar" aria-label="Duplicar">' + ic('copiar') + '</button>'
    + '<button class="ico" data-ed="' + id + '" title="Editar" aria-label="Editar">' + ic('lapiz') + '</button></div></div>';
}
let ordenLista = null;
function pintarLista() {
  if (!C) return;
  const cv = $('fCat').value;
  $('fCat').innerHTML = '<option value="">Todas las categorías</option>' + C.categorias.map(c => '<option' + (c.nombre === cv ? ' selected' : '') + '>' + esc(c.nombre) + '</option>').join('');
  const L = filtrados(), conFiltro = !!($('q').value.trim() || $('fCat').value || $('fEst').value);
  sel = new Set([...sel].filter(id => prod(id)));
  $('lista').classList.toggle('sin-orden', conFiltro); $('notaOrden').hidden = !conFiltro;
  $('cuenta').textContent = conFiltro ? L.length + ' de ' + C.productos.length : C.productos.length + ' productos';
  $('nProd').textContent = C.productos.length;
  $('lista').innerHTML = L.length ? L.map(fila).join('') : '<p class="empty">' + (C.productos.length ? 'Ningún producto coincide con la búsqueda.' : 'Todavía no hay productos. Toca «Nuevo producto».') + '</p>';
  if (!ordenLista) ordenLista = Sortable.create($('lista'), { handle: '.grip', animation: 160, ghostClass: 'fantasma', draggable: '.row',
    onEnd: async ev => { if (ev.oldIndex === ev.newIndex) return;
      const ids = [...$('lista').querySelectorAll('.row')].map(r => r.dataset.id);
      C.productos.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id)); await guardar('Orden guardado.'); } });
  ordenLista.option('disabled', conFiltro);
  $('selTodo').checked = L.length > 0 && L.every(p => sel.has(p.id));
  pintarLote();
}
function pintarLote() { $('lote').hidden = !sel.size || pestana !== 'productos'; $('loteN').textContent = sel.size + (sel.size === 1 ? ' seleccionado' : ' seleccionados'); }
let tBus; $('q').addEventListener('input', () => { clearTimeout(tBus); tBus = setTimeout(pintarLista, 120); });
$('fCat').addEventListener('change', pintarLista); $('fEst').addEventListener('change', pintarLista);
$('lista').addEventListener('change', e => { const c = e.target.closest('[data-sel]'); if (!c) return;
  c.checked ? sel.add(c.dataset.sel) : sel.delete(c.dataset.sel); c.closest('.row').classList.toggle('sel', c.checked);
  $('selTodo').checked = filtrados().every(p => sel.has(p.id)); pintarLote(); });
$('selTodo').addEventListener('change', e => { filtrados().forEach(p => e.target.checked ? sel.add(p.id) : sel.delete(p.id)); pintarLista(); });

async function lote(acc) {
  const ps = C.productos.filter(p => sel.has(p.id)); if (acc === 'nada' || !ps.length) { sel.clear(); pintarLista(); return; }
  const n = ps.length, s = n === 1 ? '' : 's';
  const listo = async msg => { if (await guardar(msg)) { sel.clear(); pintarLista(); } };
  if (acc === 'mostrar' || acc === 'ocultar') { ps.forEach(p => p.visible = acc === 'mostrar'); return listo(n + ' producto' + s + (acc === 'mostrar' ? ' visible' + s + ' en la web.' : ' oculto' + s + '.')); }
  if (acc === 'categoria') {
    const r = await dlg({ title: 'Mover ' + n + ' producto' + s, input: 'select', inputOptions: Object.fromEntries(C.categorias.map(c => [c.nombre, c.nombre])), showCancelButton: true, confirmButtonText: 'Mover' });
    if (!r.isConfirmed) return; ps.forEach(p => p.categoria = r.value); return listo('Movido' + s + ' a «' + r.value + '».');
  }
  if (acc === 'precio') {
    const conP = ps.filter(p => p.precio != null);
    if (!conP.length) return toast('Ninguno de los seleccionados tiene precio.', true);
    const r = await dlg({ title: 'Ajustar precios', input: 'number', inputValue: 10, showCancelButton: true, confirmButtonText: 'Aplicar',
      html: 'Escribe el porcentaje: <b>10</b> sube 10 %, <b>-5</b> baja 5 %. Se redondea a soles enteros.<br><small>' + conP.length + ' de ' + n + ' seleccionado' + s + ' tienen precio.</small>',
      inputAttributes: { step: '1', min: '-90', max: '300' }, inputValidator: v => (v === '' || isNaN(v) || +v < -90 || +v > 300) ? 'Escribe un número entre -90 y 300' : null });
    if (!r.isConfirmed) return; const k = 1 + (+r.value) / 100;
    conP.forEach(p => p.precio = Math.max(0, Math.round(p.precio * k))); return listo('Precios ajustados ' + (+r.value > 0 ? '+' : '') + r.value + ' %.');
  }
  if (acc === 'eliminar') {
    const ok = await confirmar('¿Eliminar ' + n + ' producto' + s + '?', ps.slice(0, 8).map(p => '· ' + esc(p.nombre)).join('<br>') + (n > 8 ? '<br>…' : '') + '<br><br>Puedes deshacerlo enseguida o recuperarlo desde <b>Historial</b>.', 'Eliminar', true);
    if (!ok) return; C.productos = C.productos.filter(p => !sel.has(p.id)); return listo(n + ' producto' + s + ' eliminado' + s + '.');
  }
}
$('bNuevo').onclick = () => abrir(null);
function duplicar(id) {
  const p = clon(prod(id)); delete p.id; p.nombre = (p.nombre + ' (copia)').slice(0, 80); p.visible = false;
  abrir(null, p);
}

/* ================= EDITOR ================= */
const estadoEd = () => JSON.stringify([$('eNom').value, $('eCat').value, $('ePre').value, $('eDet').value, $('eVis').checked, $('mAl').value, $('mAn').value, $('mFo').value, edMat, edMed]);
function abrir(id, plantilla) {
  edId = id; const p = plantilla || (id ? prod(id) : { nombre: '', categoria: C.categorias[0].nombre, precio: null, detalle: '', visible: true, medios: [] });
  if (!p) return;
  edMed = clon(p.medios || []); edMat = [...(p.materiales || [])];
  $('edT').textContent = id ? 'Editar producto' : plantilla ? 'Copia del producto' : 'Nuevo producto';
  $('eNom').value = p.nombre; $('ePre').value = p.precio ?? ''; $('eDet').value = p.detalle || ''; $('eVis').checked = p.visible !== false;
  const md = p.medidas || {}; $('mAl').value = md.alto ?? ''; $('mAn').value = md.ancho ?? ''; $('mFo').value = md.fondo ?? '';
  $('matIn').value = ''; pintarMats();
  $('eCat').innerHTML = C.categorias.map(c => '<option' + (c.nombre === p.categoria ? ' selected' : '') + '>' + esc(c.nombre) + '</option>').join('');
  $('eDel').hidden = $('eDup').hidden = !id;
  pintarMed(); if (!$('ed').open) $('ed').showModal(); $('ed').querySelector('.dbody').scrollTop = 0;
  edInicial = estadoEd(); if (!id) $('eNom').focus();
}
async function cerrar() {
  if (subiendo) { if (!await confirmar('Hay archivos subiéndose', 'Si cierras ahora, se pierden.', 'Cerrar igual', true)) return; }
  else if (estadoEd() !== edInicial) { if (!await confirmar('¿Descartar los cambios?', 'Hay cambios sin guardar en este producto.', 'Descartar', true)) return; }
  $('ed').close();
}
function vista(m, i) {
  if (m.tipo === 'giro') return '<div class="g360" data-g="' + i + '"><img src="' + esc(url(m.src)) + '" alt=""></div>';
  if (m.tipo === 'imagen') return '<img src="' + esc(url(m.src)) + '" alt="" loading="lazy">';
  if (m.tipo === 'video') return '<video src="' + esc(url(m.src)) + '"' + (m.poster ? ' poster="' + esc(url(m.poster)) + '"' : '') + ' muted playsinline preload="metadata" controls></video>';
  return '<model-viewer src="' + esc(url(m.src)) + '" camera-controls interaction-prompt="none" ' + (m.relieve ? 'camera-orbit="0deg 85deg auto" min-camera-orbit="-40deg 70deg auto" max-camera-orbit="40deg 100deg auto"' : 'auto-rotate') + '></model-viewer>';
}
function pintarMed() {
  const pri = edMed.findIndex(m => m.tipo === 'imagen');
  $('eMed').innerHTML = edMed.map((m, i) => '<div class="tile" data-i="' + i + '"><div class="pv' + (m.tipo === 'imagen' ? ' arrastre' : '') + '">'
    + '<span class="kind arrastre" title="Arrastra para ordenar">' + ic(TIPOS[m.tipo][0]) + TIPOS[m.tipo][1] + (m.relieve ? ' relieve' : '') + '</span>'
    + (i === pri ? '<span class="main">Principal</span>' : '') + vista(m, i) + '</div>'
    + (m.tipo === 'modelo' ? '<label class="rel" title="Para modelos casi planos hechos desde una foto: en la web solo giran un poco, para que no se vean de costado"><input type="checkbox" data-rel="' + i + '"' + (m.relieve ? ' checked' : '') + '>Solo de frente</label>' : '')
    + '<div class="tb"><button type="button" data-ml="' + i + '" aria-label="Mover antes"' + (i ? '' : ' disabled') + '>' + ic('izq') + '</button>'
    + (m.tipo === 'imagen' && i !== pri ? '<button type="button" data-mp="' + i + '" title="Usar como foto principal" aria-label="Usar como foto principal">' + ic('estrella') + '</button>' : '')
    + (m.tipo === 'imagen' ? '<button type="button" data-crop="' + i + '" title="Recortar o girar" aria-label="Recortar o girar">' + ic('recortar') + '</button>' : '')
    + '<button type="button" class="danger" data-mx="' + i + '" title="Quitar" aria-label="Quitar">' + ic('basura') + '</button>'
    + '<button type="button" data-mr="' + i + '" aria-label="Mover después"' + (i < edMed.length - 1 ? '' : ' disabled') + '>' + ic('der') + '</button></div></div>').join('')
    + '<div id="subs" style="display:contents"></div><button type="button" class="add" id="bAdd">' + ic('subir') + 'Agregar<small>Fotos, videos (MP4), modelos 3D (.glb), un giro 360° (.html o ZIP de vistas) o un ZIP con modelo y fotos</small></button>';
  $('bAdd').onclick = () => $('fMed').click();
  document.querySelectorAll('#eMed [data-g]').forEach(b => { const m = edMed[+b.dataset.g]; if (window.giro360 && m) giro360(b, m.vistas.map(url), { hint: false, alt: 'Giro 360°' }); });
}
Sortable.create($('eMed'), { animation: 160, ghostClass: 'fantasma', draggable: '.tile[data-i]', handle: '.arrastre',
  onEnd: ev => { if (ev.oldIndex === ev.newIndex) return; edMed = [...$('eMed').querySelectorAll('.tile[data-i]')].map(t => edMed[+t.dataset.i]); pintarMed(); } });

async function agregar(files) {
  for (const f of files) {
    const tipo = tipoDe(f);
    if (!tipo) { toast('«' + f.name + '» no es una foto, video, modelo .glb, giro 360° ni ZIP', true); continue; }
    const t = document.createElement('div'); t.className = 'tile uploading';
    t.innerHTML = '<div class="pv" data-msg="Preparando…">' + ic(tipo === 'zip' ? '3d' : TIPOS[tipo][0]) + '</div><div class="prog"><i></i></div>';
    $('subs').appendChild(t); const pv = t.querySelector('.pv'), bar = t.querySelector('.prog i'); subiendo++; $('eOk').disabled = true;
    try {
      let blob = f, nombre = f.name; const extra = {};
      if (tipo === 'imagen') { try { ({ blob, nombre } = await achicar(f)); } catch (e) { if (/heic/i.test(f.name + f.type)) throw new Error('Este celular guardó la foto en HEIC. Cambia la cámara a «Más compatible» o envíala por WhatsApp y súbela desde allí.'); } }
      if (tipo === 'video') { if (f.size > 90 * 2 ** 20) throw new Error('El video pesa más de 90 MB. Recórtalo o envíalo por WhatsApp para que se comprima.');
        const pb = await portadaVideo(f); if (pb) { pv.dataset.msg = 'Subiendo portada…'; extra.poster = (await subir(pb, f.name.replace(/\.[^.]+$/, '') + '-portada.webp', 'imagen')).src; } }
      if (tipo === 'modelo' && f.size > 50 * 2 ** 20) throw new Error('El modelo 3D pesa más de 50 MB.');
      if (tipo === 'zip' && f.size > 95 * 2 ** 20) throw new Error('El ZIP pesa más de 95 MB.');
      pv.dataset.msg = 'Subiendo… 0%';
      const r = await subir(blob, nombre, tipo, k => { bar.style.width = (k * 100) + '%'; pv.dataset.msg = k < 1 ? 'Subiendo… ' + Math.round(k * 100) + '%' : 'Procesando…'; });
      if (tipo === 'zip' || tipo === 'giro') {
        edMed.push(...r.medios);
        const n = x => r.medios.filter(m => m.tipo === x).length, g = r.medios.find(m => m.tipo === 'giro');
        toast((tipo === 'giro' ? '' : 'Del ZIP: ') + [g && 'giro 360° con ' + g.vistas.length + ' vistas', n('modelo') && n('modelo') + ' modelo 3D', n('imagen') && n('imagen') + ' foto(s)'].filter(Boolean).join(' y ')
          + (r.medios.some(m => m.relieve) ? ' (relieve: se verá de frente)' : '') + (r.omitidos.length ? '. Se ignoró: ' + r.omitidos.join(', ') : ''));
      } else edMed.push({ tipo, src: r.src, ...(r.relieve ? { relieve: true } : {}), ...extra });
      if (tipo === 'modelo' && r.relieve) toast('Este modelo es un relieve (casi plano): en la web se verá de frente, con un giro suave.');
      t.remove(); pintarMed();
    } catch (e) { t.remove(); toast(msgError(e), true); }
    finally { subiendo--; $('eOk').disabled = subiendo > 0; }
  }
}
// Recortar y girar una foto ya subida (Cropper.js); el resultado se sube como foto nueva
async function recortar(i) {
  const m = edMed[i]; let cr = null;
  const r = await dlg({ title: 'Recortar o girar', width: 720, showCancelButton: true, confirmButtonText: 'Aplicar', showLoaderOnConfirm: true,
    html: '<div class="crop"><img id="cImg" src="' + esc(url(m.src)) + '" alt=""></div><div class="cropbar">'
      + '<button type="button" class="ghost" data-rot="-90" aria-label="Girar a la izquierda">' + ic('girar-i') + '</button><button type="button" class="ghost" data-rot="90" aria-label="Girar a la derecha">' + ic('girar') + '</button>'
      + '<button type="button" class="ghost" data-asp="NaN" aria-pressed="true">Libre</button><button type="button" class="ghost" data-asp="0.8333" aria-pressed="false">Catálogo 5:6</button><button type="button" class="ghost" data-asp="1" aria-pressed="false">Cuadrada</button></div>',
    didOpen: pop => {
      cr = new Cropper(pop.querySelector('#cImg'), { viewMode: 1, autoCropArea: 1, background: false, responsive: true });
      pop.querySelector('.cropbar').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return;
        if (b.dataset.rot) cr.rotate(+b.dataset.rot);
        if (b.dataset.asp) { cr.setAspectRatio(+b.dataset.asp); pop.querySelectorAll('[data-asp]').forEach(x => x.setAttribute('aria-pressed', x === b)); } });
    },
    preConfirm: async () => {
      try { const cv = cr.getCroppedCanvas({ maxWidth: 1600, maxHeight: 1600, imageSmoothingQuality: 'high', fillColor: '#fff' });
        return (await subir(await aBlob(cv), 'recorte.webp', 'imagen')).src; }
      catch (e) { Swal.showValidationMessage(msgError(e)); }
    },
    willClose: () => { if (cr) cr.destroy(); } });
  if (r.isConfirmed && r.value) { m.src = r.value; pintarMed(); toast('Foto recortada. Guarda el producto para publicarla.'); }
}

/* materiales */
function pintarMats() {
  $('mats').innerHTML = edMat.map((m, i) => '<li>' + esc(m) + '<button type="button" data-mdel="' + i + '" aria-label="Quitar ' + esc(m) + '">' + ic('x') + '</button></li>').join('');
  const usados = new Set(edMat.map(m => m.toLowerCase()));
  $('matSugBtns').innerHTML = SUG_MAT.filter(s => !usados.has(s.toLowerCase())).map(s => '<button type="button" data-madd="' + esc(s) + '">' + ic('mas') + esc(s) + '</button>').join('');
}
function agregarMat(t) { t = (t || '').trim().slice(0, 50); if (!t) return;
  if (edMat.length >= 15) return toast('Máximo 15 materiales por producto.', true);
  if (!edMat.some(m => m.toLowerCase() === t.toLowerCase())) edMat.push(t); pintarMats(); }
$('matOk').onclick = () => { agregarMat($('matIn').value); $('matIn').value = ''; $('matIn').focus(); };
$('matIn').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('matOk').click(); } });

$('fEd').onsubmit = async e => {
  e.preventDefault(); if (subiendo) return toast('Espera a que terminen de subir los archivos.', true);
  const nombre = $('eNom').value.trim(); if (!nombre) { toast('Escribe el nombre del producto.', true); $('eNom').focus(); return; }
  if ($('matIn').value.trim()) { agregarMat($('matIn').value); $('matIn').value = ''; }  // lo que quedó escrito sin tocar «Agregar»
  const medidas = {}; [['alto', 'mAl'], ['ancho', 'mAn'], ['fondo', 'mFo']].forEach(([k, id]) => { const v = $(id).value.trim(); if (v !== '') medidas[k] = Number(v); });
  const pre = $('ePre').value.trim();
  const datos = { nombre, categoria: $('eCat').value, precio: pre === '' ? null : Number(pre), detalle: $('eDet').value.trim(), visible: $('eVis').checked, medios: edMed, medidas, materiales: [...edMat] };
  if (edId) Object.assign(prod(edId), datos);
  else { let id = slug(nombre), n = 2; while (prod(id)) id = slug(nombre) + '-' + n++; C.productos.unshift({ id, ...datos }); }
  $('eOk').disabled = true;
  await guardar(edId ? 'Producto guardado.' : 'Producto creado.', { alGuardar: () => $('ed').close() });
  $('eOk').disabled = false;
};
$('eDel').onclick = async () => { const p = prod(edId);
  if (!await confirmar('¿Eliminar «' + esc(p.nombre) + '»?', 'Puedes deshacerlo enseguida o recuperarlo desde <b>Historial</b>.', 'Eliminar', true)) return;
  C.productos = C.productos.filter(x => x.id !== edId); await guardar('Producto eliminado.', { alGuardar: () => $('ed').close() }); };
$('eDup').onclick = async () => { if (estadoEd() !== edInicial && !await confirmar('¿Duplicar sin guardar?', 'Los cambios que no guardaste en este producto se descartan.', 'Duplicar')) return;
  const id = edId; $('ed').close(); duplicar(id); };
$('edX').onclick = cerrar; $('eCan').onclick = cerrar;
$('ed').addEventListener('cancel', e => { e.preventDefault(); cerrar(); });
$('fMed').onchange = () => { const fs = [...$('fMed').files]; $('fMed').value = ''; agregar(fs); };
$('eMed').addEventListener('change', e => { const c = e.target.closest('[data-rel]'); if (!c) return; const m = edMed[+c.dataset.rel];
  if (c.checked) m.relieve = true; else delete m.relieve; pintarMed(); });
// Soltar archivos sobre el editor (computadora) y pegar una foto copiada
let arr = 0; const ed = $('ed'), conArchivos = e => [...(e.dataTransfer?.types || [])].includes('Files');
ed.addEventListener('dragenter', e => { if (conArchivos(e)) { arr++; $('soltar').hidden = false; } });
ed.addEventListener('dragleave', e => { if (conArchivos(e) && --arr <= 0) { arr = 0; $('soltar').hidden = true; } });
ed.addEventListener('dragover', e => { if (conArchivos(e)) e.preventDefault(); });
ed.addEventListener('drop', e => { if (!conArchivos(e)) return; e.preventDefault(); arr = 0; $('soltar').hidden = true; agregar([...e.dataTransfer.files]); });
document.addEventListener('paste', e => { if (!ed.open) return; const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); agregar(fs); } });

/* ================= CATEGORÍAS ================= */
function pintarCats(desdeC = true) {
  if (!C) return;
  if (desdeC) CS = C.categorias.map(c => ({ nombre: c.nombre, frase: c.frase || '', old: c.nombre }));
  $('cats').innerHTML = CS.map((c, i) => '<div class="catrow" data-i="' + i + '"><span class="grip" title="Arrastra para ordenar">' + ic('grip') + '</span>'
    + '<label>Nombre<input class="cN" data-i="' + i + '" value="' + esc(c.nombre) + '" maxlength="40"></label><label>Frase<input class="cF" data-i="' + i + '" value="' + esc(c.frase) + '" maxlength="120"></label>'
    + '<div class="rbtn"><button class="ico danger" data-cdel="' + i + '" aria-label="Eliminar categoría" title="Eliminar">' + ic('basura') + '</button></div></div>').join('');
}
Sortable.create($('cats'), { handle: '.grip', animation: 160, ghostClass: 'fantasma', draggable: '.catrow',
  onEnd: ev => { if (ev.oldIndex === ev.newIndex) return; CS = [...$('cats').querySelectorAll('.catrow')].map(r => CS[+r.dataset.i]); pintarCats(false); } });
$('cats').addEventListener('input', e => { const t = e.target, i = +t.dataset.i; if (!CS[i]) return; if (t.classList.contains('cN')) CS[i].nombre = t.value; else CS[i].frase = t.value; });
$('bCat').onclick = () => { CS.push({ nombre: '', frase: '', old: '' }); pintarCats(false); const ins = document.querySelectorAll('.cN'); ins[ins.length - 1].focus(); };
$('gCats').onclick = async () => {
  const cs = CS.map(c => ({ ...c, nombre: c.nombre.trim(), frase: c.frase.trim() }));
  if (cs.some(c => !c.nombre)) return toast('Cada categoría necesita un nombre.', true);
  if (new Set(cs.map(c => c.nombre)).size !== cs.length) return toast('Hay dos categorías con el mismo nombre.', true);
  cs.forEach(c => { if (c.old && c.old !== c.nombre) C.productos.forEach(p => { if (p.categoria === c.old) p.categoria = c.nombre; }); });
  C.categorias = cs.map(c => ({ nombre: c.nombre, frase: c.frase }));
  await guardar('Categorías guardadas.');
};

/* ================= HISTORIAL ================= */
const fechaTxt = iso => new Date(iso).toLocaleString('es-PE', { dateStyle: 'medium', timeStyle: 'short' });
function pintarActividad(el, lista, max) {
  el.innerHTML = lista.length ? lista.slice(0, max || lista.length).map(x => '<li><time>' + fechaTxt(x.fecha) + (x.ip && !max ? ' · ' + esc(x.ip) : '') + '</time>'
    + '<strong>' + esc(x.accion) + '</strong><ul>' + x.cambios.map(c => '<li>' + esc(c) + '</li>').join('') + '</ul></li>').join('')
    : '<li class="hint" style="margin:0">Todavía no hay cambios registrados.</li>';
}
async function cargarHistorial() {
  try {
    const [v, a] = await Promise.all([api('/admin/api/historial'), api('/admin/api/actividad')]);
    pintarActividad($('actividad'), a.actividad);
    $('versiones').innerHTML = v.versiones.length ? v.versiones.map(x => '<div class="ver"><div><b>' + fechaTxt(x.fecha) + '</b><small>' + x.productos + ' productos · ' + x.visibles + ' visibles</small></div>'
      + '<button class="ghost" data-rest="' + esc(x.nombre) + '">' + ic('reloj') + 'Restaurar</button></div>').join('') : '<p class="hint" style="margin:0">Aún no hay versiones anteriores.</p>';
  } catch (e) { if (!e.silencio) toast(msgError(e), true); }
}
async function restaurar(nombre) {
  if (!await confirmar('¿Volver a esta versión?', 'El catálogo quedará como estaba en ese momento. La versión actual también se guarda en el historial, así que puedes regresar.', 'Restaurar')) return;
  try { const j = await api('/admin/api/restaurar', { method: 'POST', body: JSON.stringify({ nombre }) });
    C = j.catalogo; snap = clon(C); refrescar(); toast('Versión restaurada. Ya se ve en la web.'); cargarHistorial(); }
  catch (e) { if (!e.silencio) toast(msgError(e), true); }
}

/* ================= AJUSTES ================= */
let portadaActual = '';
function pintarAjustes() {
  if (!C) return;
  if (document.activeElement !== $('tel')) $('tel').value = C.telefono;
  if (portadaActual !== C.portada.modelo) { portadaActual = C.portada.modelo;
    $('pPv').innerHTML = '<model-viewer src="' + esc(url(C.portada.modelo)) + '"' + (C.portada.poster ? ' poster="' + esc(url(C.portada.poster)) + '"' : '') + ' camera-controls ' + (C.portada.relieve ? 'camera-orbit="0deg 85deg auto" min-camera-orbit="-40deg 70deg auto" max-camera-orbit="40deg 100deg auto"' : 'auto-rotate camera-orbit="20deg 80deg auto"') + ' interaction-prompt="none" shadow-intensity="1"></model-viewer>'; }
  if (MET) { const a = MET.almacenamiento; $('espacio').textContent = num(a.archivos) + ' archivos subidos · ' + (a.bytes / 2 ** 20).toLocaleString('es-PE', { maximumFractionDigits: 1 }) + ' MB'; }
}
$('gTel').onclick = async () => { const t = $('tel').value.replace(/\D/g, '');
  if (t.length < 8) return toast('Escribe el número con código de país, por ejemplo 51935838575.', true);
  C.telefono = t; await guardar('Número de WhatsApp guardado.'); };
$('bPortada').onclick = () => $('fGlb').click();
$('fGlb').onchange = async () => { const f = $('fGlb').files[0]; $('fGlb').value = ''; if (!f) return;
  if (!f.name.toLowerCase().endsWith('.glb')) return toast('El modelo debe ser un archivo .glb', true);
  try { $('pMsg').textContent = 'Subiendo…';
    const r = await subir(f, f.name, 'modelo', k => $('pMsg').textContent = 'Subiendo… ' + Math.round(k * 100) + '%');
    const { poster, relieve, ...resto } = C.portada;   // conserva textos, imágenes y fechas; quita la portada de espera del modelo anterior
    C.portada = { ...resto, modelo: r.src, ...(r.relieve ? { relieve: true } : {}) }; await guardar('Modelo 3D de la portada cambiado.');
  } catch (e) { toast(msgError(e), true); } finally { $('pMsg').textContent = ''; } };

/* ================= PORTADA (mosaico de la tienda) ================= */
// Lo mismo que muestra la tienda si nunca se editó (ver index.html). Se edita una copia (PO) y solo se publica al guardar.
const POR_DEF = {
  a: { acento: 'Tu mini versión', titulo: 'Convierte a quien más quieres en un amigurumi', boton: 'Crear el mío' },
  b: { titulo: 'Danzas de Puno', boton: 'Ver modelos', imagen: 'assets/productos/danza-plumas.jpg', categoria: 'Danzas de Puno' },
  c: { titulo: 'Ramos que no se marchitan', boton: 'Ver ramos', imagen: 'assets/productos/ramo-crema.jpg', categoria: 'Ramos tejidos' },
  d: { acento: 'Pide con anticipación', titulo: 'Hecho a pedido', imagen: 'assets/productos/ramo-rojo-pareja.jpg' },
  fechas: [
    { nombre: 'San Valentín', regla: 'fija', mes: 2, dia: 14, imagen: 'assets/productos/ramo-rojo-pareja.jpg', ocasion: 'San Valentín o aniversario' },
    { nombre: 'el Día de la Madre', regla: 'segundo-domingo', mes: 5, dia: 0, imagen: 'assets/productos/ramo-crema.jpg', ocasion: 'Día de la Madre' },
    { nombre: 'el Día de la Policía', regla: 'fija', mes: 12, dia: 6, imagen: 'assets/productos/policia-caja.jpg', ocasion: 'Día de la Policía' },
    { nombre: 'Navidad', regla: 'fija', mes: 12, dia: 25, imagen: 'assets/productos/mini-mochila.jpg', ocasion: 'Navidad' }] };
const OCASIONES = ['San Valentín o aniversario', 'Graduación', 'Día de la Policía', 'Cumpleaños', 'Navidad', 'Día de la Madre', 'Otra'];
const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
let PO = null, poSucio = false;
const fechaDe = (f, a) => { if (f.regla === 'segundo-domingo') { const d = new Date(a, f.mes - 1, 1); return new Date(a, f.mes - 1, 1 + (7 - d.getDay()) % 7 + 7); } return new Date(a, f.mes - 1, f.dia); };
function proximaFecha(lista) {
  const hoy = new Date(), y = hoy.getFullYear();
  const t = [y, y + 1].flatMap(a => (lista || []).filter(f => f.nombre && f.mes && (f.regla === 'segundo-domingo' || f.dia)).map(f => ({ ...f, d: fechaDe(f, a) })))
    .filter(x => x.d - hoy > 7 * 864e5).sort((a, b) => a.d - b.d)[0];
  return t ? { ...t, dias: Math.floor((t.d - hoy) / 864e5) } : null;
}
function pintarPortada(reset) {
  if (!C) return;
  if (reset || !poSucio || !PO) {
    PO = clon(POR_DEF);
    for (const k of ['a', 'b', 'c', 'd']) if (C.portada[k]) PO[k] = { ...PO[k], ...C.portada[k] };
    if (C.portada.fechas) PO.fechas = clon(C.portada.fechas);
    poSucio = false;
    [['aAcc', 'a.acento'], ['aTit', 'a.titulo'], ['aBtn', 'a.boton'], ['dAcc', 'd.acento'], ['dTit', 'd.titulo']].forEach(([id, ruta]) => { $(id).dataset.po = ruta; $(id).value = leerRuta(ruta) ?? ''; });
    for (const [k, n, t] of [['b', 2, 'Recuadro 2'], ['c', 3, 'Recuadro 3']]) {
      const x = PO[k];
      $('box' + k.toUpperCase()).innerHTML = '<h3><span class="ib">' + n + '</span>' + t + (k === 'b' ? ' (alto, al centro)' : ' (arriba a la derecha)') + '</h3>'
        + '<label>Título<input data-po="' + k + '.titulo" maxlength="60" value="' + esc(x.titulo) + '"></label>'
        + '<div class="two"><label>Texto del botón<input data-po="' + k + '.boton" maxlength="30" value="' + esc(x.boton) + '"></label>'
        + '<label>Al tocarlo muestra<select data-po="' + k + '.categoria"><option value="">Todo el catálogo</option>' + C.categorias.map(c => '<option' + (c.nombre === x.categoria ? ' selected' : '') + '>' + esc(c.nombre) + '</option>').join('') + '</select></label></div>'
        + '<div class="imgsel"><div class="ph">' + (x.imagen ? '<img src="' + esc(url(x.imagen)) + '" alt="">' : ic('foto')) + '</div><button class="ghost" data-img="' + k + '.imagen">' + ic('foto') + 'Cambiar imagen</button></div>';
    }
  }
  pintarFechas(); $('dImg').innerHTML = '<div class="ph">' + (PO.d.imagen ? '<img src="' + esc(url(PO.d.imagen)) + '" alt="">' : ic('foto')) + '</div><button class="ghost" data-img="d.imagen">' + ic('foto') + 'Cambiar imagen</button>';
  pintarPreview();
}
function leerRuta(ruta) { return ruta.split('.').reduce((o, k) => o == null ? o : o[k], PO); }
function ponerRuta(ruta, v) { const ks = ruta.split('.'); let o = PO; for (let i = 0; i < ks.length - 1; i++) o = o[ks[i]]; o[ks[ks.length - 1]] = v; }
function pintarFechas() {
  const hoy = new Date();
  $('fechas').innerHTML = PO.fechas.map((f, i) => {
    let cuando = '';
    if (f.mes && (f.regla === 'segundo-domingo' || f.dia)) { let d = fechaDe(f, hoy.getFullYear()); if (d < hoy) d = fechaDe(f, hoy.getFullYear() + 1);
      cuando = 'Próxima: ' + d.toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) + ' · faltan ' + Math.ceil((d - hoy) / 864e5) + ' días'; }
    return '<div class="fecha"><div class="fcab"><button type="button" class="ph" data-img="fechas.' + i + '.imagen" title="Cambiar imagen" aria-label="Cambiar imagen">' + (f.imagen ? '<img src="' + esc(url(f.imagen)) + '" alt="">' : ic('foto')) + '</button>'
      + '<input data-po="fechas.' + i + '.nombre" maxlength="40" value="' + esc(f.nombre) + '" placeholder="Ej.: el Día del Padre" aria-label="Nombre (se lee «Falta poco para …»)">'
      + '<button type="button" class="ico danger" data-fdel="' + i + '" aria-label="Quitar esta fecha" title="Quitar">' + ic('basura') + '</button></div>'
      + '<div class="fila"><select data-po="fechas.' + i + '.regla" aria-label="Tipo de fecha"><option value="fija"' + (f.regla !== 'segundo-domingo' ? ' selected' : '') + '>Día fijo</option><option value="segundo-domingo"' + (f.regla === 'segundo-domingo' ? ' selected' : '') + '>2.º domingo</option></select>'
      + '<select data-po="fechas.' + i + '.mes" aria-label="Mes">' + MESES.map((m, j) => '<option value="' + (j + 1) + '"' + (f.mes === j + 1 ? ' selected' : '') + '>' + m + '</option>').join('') + '</select>'
      + (f.regla === 'segundo-domingo' ? '<span></span>' : '<input type="number" data-po="fechas.' + i + '.dia" min="1" max="31" value="' + (f.dia || '') + '" aria-label="Día">') + '</div>'
      + '<select data-po="fechas.' + i + '.ocasion" aria-label="Ocasión que se marca en el formulario"><option value="">Ocasión en el formulario: ninguna</option>' + OCASIONES.map(o => '<option' + (o === f.ocasion ? ' selected' : '') + '>' + o + '</option>').join('') + '</select>'
      + '<span class="cuando">' + cuando + '</span></div>';
  }).join('') || '<p class="hint">Sin fechas: la cuenta regresiva no se muestra.</p>';
}
function pintarPreview() {
  const a = PO.a, b = PO.b, c = PO.c, d = PO.d, prox = proximaFecha(PO.fechas), fd = prox ? (prox.imagen || d.imagen) : d.imagen;
  const im = s => s ? '<img src="' + esc(url(s)) + '" alt="">' : '';
  $('pmos').innerHTML = '<div class="ta"><i>' + esc(a.acento) + '</i><b>' + esc(a.titulo) + '</b><span class="m3">' + ic('3d') + '</span><span class="pbtn">' + esc(a.boton) + '</span></div>'
    + '<div class="tb ov">' + im(b.imagen) + '<b>' + esc(b.titulo) + '</b><span class="pbtn">' + esc(b.boton) + '</span></div>'
    + '<div class="tc">' + im(c.imagen) + '<b>' + esc(c.titulo) + '</b><span class="pbtn">' + esc(c.boton) + '</span></div>'
    + '<div class="td ov">' + im(fd) + '<i>' + esc(d.acento) + '</i><b>' + (prox ? 'Falta poco para ' + esc(prox.nombre) : esc(d.titulo)) + '</b>' + (prox ? '<div class="cl"><span>' + prox.dias + ' días</span></div>' : '') + '</div>';
}
$('app').addEventListener('input', e => { const t = e.target.closest('[data-po]'); if (!t) return;
  let v = t.value; if (/\.(mes|dia)$/.test(t.dataset.po)) v = +v || 0;
  ponerRuta(t.dataset.po, v); poSucio = true;
  if (/\.regla$/.test(t.dataset.po) || /^fechas\./.test(t.dataset.po) && e.type === 'change') pintarFechas();
  pintarPreview(); });
$('app').addEventListener('change', e => { const t = e.target.closest('[data-po]'); if (t && /^fechas\.\d+\.(regla|mes|dia)$/.test(t.dataset.po)) { pintarFechas(); pintarPreview(); } });
// Elegir una imagen: entre las fotos de los productos o subiendo una nueva
function elegirImagen() {
  return new Promise(res => {
    const fotos = [...new Set(C.productos.flatMap(p => (p.medios || []).filter(m => m.tipo === 'imagen').map(m => m.src)))];
    dlg({ title: 'Elegir imagen', width: 760, showConfirmButton: false, showCancelButton: true,
      html: '<button type="button" class="ghost" id="gSubir" style="margin:0 auto 14px">' + ic('subir') + 'Subir una foto nueva</button>'
        + '<p class="hint" style="margin-bottom:8px">O toca una de las fotos de tus productos:</p><div class="galeria">'
        + fotos.map(s => '<button type="button" data-src="' + esc(s) + '"><img src="' + esc(url(s)) + '" alt="" loading="lazy"></button>').join('') + '</div>',
      didOpen: pop => {
        pop.querySelector('.galeria').addEventListener('click', e => { const b = e.target.closest('[data-src]'); if (b) { res(b.dataset.src); Swal.close(); } });
        pop.querySelector('#gSubir').onclick = () => { const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
          inp.onchange = async () => { const f = inp.files[0]; if (!f) return;
            try { Swal.showLoading(); const { blob, nombre } = await achicar(f); const r = await subir(blob, nombre, 'imagen'); res(r.src); Swal.close(); }
            catch (e) { Swal.hideLoading(); Swal.showValidationMessage(msgError(e)); } };
          inp.click(); };
      } }).then(() => res(null));
  });
}
async function cambiarImagen(ruta) {
  const s = await elegirImagen(); if (!s) return;
  ponerRuta(ruta, s); poSucio = true;
  if (ruta.startsWith('fechas.')) pintarFechas();
  else if (ruta === 'd.imagen') $('dImg').querySelector('.ph').innerHTML = '<img src="' + esc(url(s)) + '" alt="">';
  else $('box' + ruta[0].toUpperCase()).querySelector('.ph').innerHTML = '<img src="' + esc(url(s)) + '" alt="">';
  pintarPreview();
}
$('bFecha').onclick = () => { if (PO.fechas.length >= 12) return toast('Máximo 12 fechas especiales.', true);
  PO.fechas.push({ nombre: '', regla: 'fija', mes: new Date().getMonth() + 1, dia: 1, imagen: '', ocasion: '' }); poSucio = true; pintarFechas(); pintarPreview();
  const ins = $('fechas').querySelectorAll('[data-po$=".nombre"]'); ins[ins.length - 1].focus(); };
$('gPor').onclick = async () => {
  const vacia = PO.fechas.findIndex(f => !f.nombre.trim()); if (vacia >= 0) return toast('Escribe el nombre de cada fecha especial (o quita la que sobra).', true);
  const mala = PO.fechas.find(f => f.regla === 'fija' && !(f.dia >= 1 && f.dia <= 31)); if (mala) return toast('Revisa el día de «' + mala.nombre + '».', true);
  C.portada = { ...C.portada, ...clon(PO) };
  if (await guardar('Portada guardada. Ya se ve en la tienda.')) { poSucio = false; pintarPortada(true); }
};
$('rPor').onclick = async () => { if (poSucio && !await confirmar('¿Descartar los cambios de la portada?', 'Vuelve a quedar como está publicada.', 'Descartar', true)) return; pintarPortada(true); };

/* ================= app instalable y «Compartir» ================= */
let pedidoInstalar = null;
window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); pedidoInstalar = e; $('bInstalar').hidden = $('bInstalar2').hidden = false; });
const instalar = async () => { if (!pedidoInstalar) return; pedidoInstalar.prompt(); const r = await pedidoInstalar.userChoice; pedidoInstalar = null;
  $('bInstalar').hidden = $('bInstalar2').hidden = true; if (r.outcome === 'accepted') toast('Panel instalado en tu pantalla de inicio.'); };
$('bInstalar').onclick = instalar; $('bInstalar2').onclick = instalar;
if (matchMedia('(display-mode: standalone)').matches || navigator.standalone) $('appTxt').textContent = 'Ya estás usando el panel como app. Desde la galería: Compartir → «Panel SEN» para mandar fotos a un producto (en Android).';
else if (/iphone|ipad/i.test(navigator.userAgent)) $('appTxt').textContent = 'En iPhone: abre este panel en Safari, toca Compartir y luego «Agregar a inicio». (Mandar fotos desde la galería al panel solo funciona en Android.)';
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/admin/sw.js', { scope: '/admin/' }).catch(() => {});
async function compartidos() {
  const q = new URLSearchParams(location.search); if (!q.has('compartido')) return;
  history.replaceState(null, '', '/admin/' + location.hash);
  if (!('caches' in window)) return;
  const c = await caches.open('compartidos'), ks = await c.keys(), files = [];
  for (const k of ks) { const r = await c.match(k); const b = await r.blob(); files.push(new File([b], decodeURIComponent(r.headers.get('X-Nombre') || 'archivo'), { type: b.type })); }
  for (const k of ks) await c.delete(k);
  if (!files.length) return;
  const r = await dlg({ title: 'Recibiste ' + files.length + ' archivo' + (files.length === 1 ? '' : 's'), text: '¿A qué producto los agrego?', input: 'select', showCancelButton: true,
    inputOptions: { __nuevo: 'Crear un producto nuevo', ...Object.fromEntries(C.productos.map(p => [p.id, p.nombre])) }, confirmButtonText: 'Continuar', cancelButtonText: 'Descartar' });
  if (!r.isConfirmed) return;
  irA('productos'); abrir(r.value === '__nuevo' ? null : r.value); agregar(files);
}
const enLinea = () => { $('offline').hidden = navigator.onLine; };
window.addEventListener('online', enLinea); window.addEventListener('offline', enLinea);

/* ================= clics y teclado ================= */
document.addEventListener('click', async e => {
  const b = e.target.closest('button,a[data-ed]'); if (!b) return; const d = b.dataset;
  if (d.tab) return irA(d.tab);
  if (d.ir) return irA(d.ir);
  if (d.dias) { dias = +d.dias; document.querySelectorAll('[data-dias]').forEach(x => x.setAttribute('aria-pressed', x === b)); return cargarMetricas(); }
  if (d.pend) { $('q').value = ''; $('fCat').value = ''; $('fEst').value = d.pend; irA('productos'); return pintarLista(); }
  if (d.ed) { e.preventDefault(); return abrir(d.ed); }
  if (d.dup) return duplicar(d.dup);
  if (d.vis) { const p = prod(d.vis); p.visible = p.visible === false; return guardar(p.visible ? '«' + p.nombre + '» ahora se ve en la web.' : '«' + p.nombre + '» quedó oculto.'); }
  if (d.lote) return lote(d.lote);
  if (d.rest) return restaurar(d.rest);
  if (d.img) return cambiarImagen(d.img);
  if (d.fdel) { PO.fechas.splice(+d.fdel, 1); poSucio = true; pintarFechas(); return pintarPreview(); }
  if (d.ml || d.mr) { const i = +(d.ml ?? d.mr), j = d.ml ? i - 1 : i + 1; [edMed[i], edMed[j]] = [edMed[j], edMed[i]]; return pintarMed(); }
  if (d.mp) { const [m] = edMed.splice(+d.mp, 1); edMed.unshift(m); return pintarMed(); }
  if (d.mx) { edMed.splice(+d.mx, 1); return pintarMed(); }
  if (d.crop) return recortar(+d.crop);
  if (d.madd) return agregarMat(d.madd);
  if (d.mdel) { edMat.splice(+d.mdel, 1); return pintarMats(); }
  if (d.cdel) { const c = CS[+d.cdel], usados = c.old ? C.productos.filter(p => p.categoria === c.old).length : 0;
    if (usados) return toast('No se puede eliminar «' + c.old + '»: tiene ' + usados + ' producto(s). Cámbialos de categoría primero.', true);
    if (CS.length < 2) return toast('Debe quedar al menos una categoría.', true);
    CS.splice(+d.cdel, 1); pintarCats(false); }
});
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && ed.open) { e.preventDefault(); $('fEd').requestSubmit(); return; }
  const escribiendo = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (escribiendo || ed.open || Swal.isVisible() || $('app').hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); irA('productos'); $('q').focus(); }
  else if (e.key.toLowerCase() === 'n') { e.preventDefault(); irA('productos'); abrir(null); }
});
window.addEventListener('beforeunload', e => { if (subiendo || poSucio || (ed.open && estadoEd() !== edInicial)) { e.preventDefault(); e.returnValue = ''; } });

/* ================= sesión ================= */
function mostrarLogin() { $('app').hidden = true; $('login').hidden = false; $('clave').focus(); }
async function entrarApp() {
  C = await api('/admin/api/catalogo'); snap = clon(C);
  $('login').hidden = true; $('app').hidden = false; enLinea();
  refrescar();
  const h = location.hash.slice(1), tabs = ['inicio', 'productos', 'portada', 'categorias', 'historial', 'ajustes'];
  if (/^editar-.+/.test(h) && prod(h.slice(7))) { irA('productos'); abrir(h.slice(7)); } else irA(tabs.includes(h) ? h : 'inicio');
  compartidos();
}
$('fLogin').onsubmit = async e => { e.preventDefault(); $('lErr').textContent = ''; const b = e.submitter; b.disabled = true;
  try { await api('/admin/api/entrar', { method: 'POST', body: JSON.stringify({ clave: $('clave').value }) }); $('clave').value = ''; await entrarApp(); }
  catch (err) { $('lErr').textContent = msgError(err); } finally { b.disabled = false; } };
$('bSalirM').onclick = () => $('bSalir').click();
$('bSalir').onclick = async () => { if (!await confirmar('¿Cerrar sesión?', 'Tendrás que escribir la contraseña la próxima vez.', 'Salir')) return;
  try { await api('/admin/api/salir', { method: 'POST' }); } catch (e) {} mostrarLogin(); };
api('/admin/api/sesion').then(j => j.ok ? entrarApp() : mostrarLogin()).catch(() => mostrarLogin());
})();
