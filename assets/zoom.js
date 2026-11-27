/* Visor ampliado de fotos para las páginas de producto.
   zoomVer(['/media/a.webp', ...], indice)
   - Acercar: pellizcar con dos dedos, rueda del ratón, doble toque o botones + y −.
   - Mover: arrastrar cuando está ampliada. Cambiar de foto: deslizar (sin zoom), flechas o botones.
   - Cerrar: Esc, botón ✕ o tocar fuera de la foto.
   Lleva una marca discreta «© Nudo de SEN» y no permite clic derecho ni arrastrar la imagen. */
window.zoomVer = function (lista, i) {
  if (!lista || !lista.length) return;
  const d = document.createElement('div');
  d.className = 'zoom';
  d.setAttribute('role', 'dialog');
  d.setAttribute('aria-modal', 'true');
  d.setAttribute('aria-label', 'Foto ampliada');
  const varias = lista.length > 1;
  d.innerHTML = '<div class="zl"><img alt="" draggable="false"></div>'
    + '<div class="zb">'
    + (varias ? '<button type="button" data-n="-1" aria-label="Foto anterior">‹</button><span class="zn"></span><button type="button" data-n="1" aria-label="Foto siguiente">›</button><i></i>' : '')
    + '<button type="button" data-z="-" aria-label="Alejar">−</button><button type="button" data-z="+" aria-label="Acercar">+</button>'
    + '<button type="button" data-x aria-label="Cerrar">✕</button></div>'
    + '<small class="zh">Pellizca, usa la rueda o toca dos veces para acercar · arrastra para mover</small>'
    + '<span class="zm" aria-hidden="true">© Nudo de SEN</span>';
  document.body.appendChild(d);
  const antes = document.documentElement.style.overflow;
  document.documentElement.style.overflow = 'hidden';
  const img = d.querySelector('img'), lz = d.querySelector('.zl'), volver = document.activeElement;
  let k = i || 0, s = 1, x = 0, y = 0, d0 = 0, s0 = 1, arr = null, ini = null;
  const pts = new Map();
  const aplicar = () => { img.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')'; d.classList.toggle('ampliada', s > 1); };
  const limitar = () => {
    if (s <= 1) { s = 1; x = 0; y = 0; return; }
    const r = lz.getBoundingClientRect(), mx = Math.max(0, (img.offsetWidth * s - r.width) / 2), my = Math.max(0, (img.offsetHeight * s - r.height) / 2);
    x = Math.max(-mx, Math.min(mx, x)); y = Math.max(-my, Math.min(my, y));
  };
  const zoomA = (ns, cx, cy) => {
    ns = Math.max(1, Math.min(5, ns));
    const r = lz.getBoundingClientRect(), ox = (cx ?? r.left + r.width / 2) - (r.left + r.width / 2), oy = (cy ?? r.top + r.height / 2) - (r.top + r.height / 2);
    x = ox - (ox - x) * ns / s; y = oy - (oy - y) * ns / s; s = ns; limitar(); aplicar();
  };
  const ver = n => {
    k = (n + lista.length) % lista.length; img.src = lista[k]; s = 1; x = 0; y = 0; aplicar();
    const zn = d.querySelector('.zn'); if (zn) zn.textContent = (k + 1) + ' / ' + lista.length;
  };
  const cerrar = () => { d.remove(); document.documentElement.style.overflow = antes; removeEventListener('keydown', tecla, true); if (volver && volver.focus) volver.focus(); };
  const tecla = e => {
    if (e.key === 'Escape') { e.stopPropagation(); cerrar(); }
    else if (e.key === 'ArrowRight' && varias) ver(k + 1);
    else if (e.key === 'ArrowLeft' && varias) ver(k - 1);
    else if (e.key === '+' || e.key === '=') zoomA(s * 1.4);
    else if (e.key === '-') zoomA(s / 1.4);
  };
  addEventListener('keydown', tecla, true);
  d.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (b) { if (b.dataset.z) zoomA(b.dataset.z === '+' ? s * 1.5 : s / 1.5); if (b.dataset.n) ver(k + +b.dataset.n); if ('x' in b.dataset) cerrar(); return; }
    if (e.target === lz && s === 1) cerrar();
  });
  d.addEventListener('contextmenu', e => e.preventDefault());
  lz.addEventListener('wheel', e => { e.preventDefault(); zoomA(s * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX, e.clientY); }, { passive: false });
  lz.addEventListener('dblclick', e => zoomA(s > 1 ? 1 : 2.5, e.clientX, e.clientY));
  lz.addEventListener('pointerdown', e => {
    lz.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { const [a, b] = [...pts.values()]; d0 = Math.hypot(a.x - b.x, a.y - b.y); s0 = s; arr = null; ini = null; }
    else { arr = { x: e.clientX - x, y: e.clientY - y }; ini = { x: e.clientX, t: Date.now() }; }
  });
  lz.addEventListener('pointermove', e => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { const [a, b] = [...pts.values()]; zoomA(s0 * Math.hypot(a.x - b.x, a.y - b.y) / d0, (a.x + b.x) / 2, (a.y + b.y) / 2); }
    else if (arr && s > 1) { x = e.clientX - arr.x; y = e.clientY - arr.y; limitar(); aplicar(); }
  });
  const fin = e => {
    // deslizar sin zoom = cambiar de foto
    if (ini && s === 1 && varias && pts.size === 1) { const dx = e.clientX - ini.x; if (Math.abs(dx) > 60 && Date.now() - ini.t < 600) ver(k + (dx < 0 ? 1 : -1)); }
    pts.delete(e.pointerId);
    if (pts.size === 1) { const v = [...pts.values()][0]; arr = { x: v.x - x, y: v.y - y }; } else arr = null;
    ini = null;
  };
  lz.addEventListener('pointerup', fin); lz.addEventListener('pointercancel', fin);
  ver(k);
  d.querySelector('[data-x]').focus();
};

/* Protección básica de las fotos (disuade, no impide capturas de pantalla):
   sin menú del clic derecho ni «abrir imagen en otra pestaña», sin arrastrar fotos
   y sin la pulsación larga del celular para guardarlas (esto último, con CSS). */
document.addEventListener('contextmenu', e => { if (e.target.closest('img,picture,.g360,.fstage,.zoom,.pic,.ban,.cat,.fthumbs')) e.preventDefault(); });
document.addEventListener('dragstart', e => { if (e.target.closest('img')) e.preventDefault(); });

/* Liquid Glass: el brillo de los botones y superficies de vidrio sigue al dedo o al ratón (variables --mx y --my). */
if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.addEventListener('pointermove', e => {
    const el = e.target.closest && e.target.closest('button,.btn,.header .wrap,.chips,.clock div');
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', (e.clientX - r.left) + 'px');
    el.style.setProperty('--my', (e.clientY - r.top) + 'px');
  }, { passive: true });
}
