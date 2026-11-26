/* ============================================================
   NUDO DE SEN · DATOS QUE PUEDES EDITAR
   ------------------------------------------------------------
   Este archivo se abre con el Bloc de notas. Cambia solo lo que
   está entre comillas o los números, guarda y recarga la página.

   · precio: va en soles, sin "S/" y sin comillas. Ejemplo: precio: 120
     Si borras el precio de un producto, en la web dirá "A cotizar".
   · foto: nombre del archivo que está en assets/productos/ (sin ".jpg").
   · categoria: debe ser igual a una de la lista "categorias".
   · Para agregar un producto, copia una línea completa (desde { hasta },)
     y cámbiale el id (sin espacios ni tildes), el nombre y lo demás.

   ⚠️ LOS PRECIOS DE ABAJO SON DE EJEMPLO. Cámbialos por los reales
      antes de publicar la página.
   ============================================================ */
window.NUDO = {
  // WhatsApp: código de país + número, sin "+" ni espacios.
  telefono: '51935838575',

  categorias: [
    { nombre: 'Personalizados', frase: 'Tu mini versión, tu pareja o tu familia' },
    { nombre: 'Danzas de Puno', frase: 'Trajes típicos tejidos al detalle' },
    { nombre: 'Ramos tejidos',  frase: 'Flores que no se marchitan' },
    { nombre: 'Llaveros',       frase: 'Para llevarlos siempre contigo' }
  ],

  productos: [
    { id: 'mini-version',   nombre: 'Mini versión de ti',                 categoria: 'Personalizados', precio: 120, foto: 'mini-mochila',     detalle: 'Con tu peinado, tu ropa y tus accesorios.' },
    { id: 'deportista',     nombre: 'Mini versión deportista',            categoria: 'Personalizados', precio: 130, foto: 'deportista',       detalle: 'Con el uniforme de tu equipo, sobre base de madera.' },
    { id: 'policia',        nombre: 'Mini versión policía',               categoria: 'Personalizados', precio: 140, foto: 'policia-caja',     detalle: 'Con uniforme, en caja de regalo.' },
    { id: 'danza-pompones', nombre: 'Danzarina con sombrero de pompones', categoria: 'Danzas de Puno', precio: 170, foto: 'danza-pompones',   detalle: 'Traje típico tejido al detalle.' },
    { id: 'danza-plumas',   nombre: 'Danzarín con penacho de plumas',     categoria: 'Danzas de Puno', precio: 160, foto: 'danza-plumas',     detalle: 'Sobre base de madera.' },
    { id: 'danza-colores',  nombre: 'Danzarina de pollera multicolor',    categoria: 'Danzas de Puno', precio: 180, foto: 'danza-colores',    detalle: 'Con sombrero y bordados de colores.' },
    { id: 'danza-blanca',   nombre: 'Danzarina con penacho blanco',       categoria: 'Danzas de Puno', precio: 175, foto: 'danza-blanca',     detalle: 'Traje de colores con flecos.' },
    { id: 'ramo-crema',     nombre: 'Ramo de rosas crema',                categoria: 'Ramos tejidos',  precio: 95,  foto: 'ramo-crema',       detalle: 'Rosas de crochet que no se marchitan.' },
    { id: 'ramo-rojo',      nombre: 'Ramo de rosas rojas con pareja',     categoria: 'Ramos tejidos',  precio: 165, foto: 'ramo-rojo-pareja', detalle: 'Con dos muñequitos personalizados.' },
    { id: 'ramo-azul',      nombre: 'Ramo de rosas azules con muñeco',    categoria: 'Ramos tejidos',  precio: 145, foto: 'ramo-azul',        detalle: 'Con un muñequito personalizado.' },
    { id: 'rosa',           nombre: 'Rosa tejida',                        categoria: 'Ramos tejidos',  precio: 25,  foto: 'rosa-amarilla',    detalle: 'Una rosa de crochet con tallo y hojas.' },
    { id: 'llaveros',       nombre: 'Llaveros personalizados',            categoria: 'Llaveros',       precio: 35,  foto: 'llaveros-pareja',  detalle: 'Muñequitos con argolla, solos o en pareja.' }
  ]
};
