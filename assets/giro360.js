/* Visor «Giro 360°»: una serie de fotos alrededor del producto que se cambian al arrastrar.
   Uso: giro360(contenedor, ['/media/.../v00.webp', ...], {alt:'Nombre'})
   - Arrastrar (dedo o ratón) gira; las flechas del teclado también.
   - Gira solo y despacio hasta que la persona lo toca (no gira si pidió «reducir movimiento»). */
window.giro360 = function (box, vistas, op) {
  op = op || {};
  const n = vistas.length;
  box.classList.add('g360');
  box.tabIndex = 0;
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label', (op.alt || 'Producto') + ': vista 360°. Arrastra o usa las flechas para girarla');
  const img = document.createElement('img');
  img.draggable = false;
  img.alt = '';
  box.replaceChildren(img);
  if (op.hint !== false) {
    const s = document.createElement('small');
    s.className = 'hint3d';
    s.textContent = 'Arrástrala para girarla 360°';
    box.appendChild(s);
  }
  let k = 0, x0 = null, k0 = 0, auto = true;
  const ver = i => { k = ((i % n) + n) % n; img.src = vistas[k]; };
  ver(0);
  // precarga para que el giro no parpadee
  const listas = Promise.all(vistas.map(s => new Promise(r => { const i = new Image(); i.onload = i.onerror = r; i.src = s; })));
  const paso = () => Math.max(6, box.clientWidth / (n * 1.4));
  img.addEventListener('pointerdown', e => { auto = false; x0 = e.clientX; k0 = k; img.setPointerCapture(e.pointerId); });
  img.addEventListener('pointermove', e => { if (x0 !== null) ver(k0 + Math.trunc((e.clientX - x0) / paso())); });
  ['pointerup', 'pointercancel'].forEach(t => img.addEventListener(t, () => { x0 = null; }));
  box.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); auto = false; ver(k + (e.key === 'ArrowLeft' ? -1 : 1)); }
  });
  if (op.auto !== false && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    listas.then(() => {
      const girar = () => { if (!auto || !img.isConnected) return; ver(k + 1); setTimeout(girar, 9000 / n); };  // una vuelta cada ~9 s
      setTimeout(girar, 900);
    });
  }
  return { ver, parar: () => { auto = false; } };
};
