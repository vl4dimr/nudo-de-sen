"""
Panel de Nudo de SEN: servidor pequeño (solo biblioteca estándar de Python 3.12) que
 - sirve la página del panel (panel/index.html),
 - guarda el catálogo en <raiz>/data/catalogo.json (lo lee la web pública),
 - recibe fotos, videos y modelos 3D y los deja en <raiz>/media/AAAA-MM/.

Escucha solo en 127.0.0.1; desde internet se llega por el túnel de Cloudflare con una ruta
por camino: nudodesen.com/admin* -> http://127.0.0.1:8089. Todo vive bajo /admin
(página en /admin/, API en /admin/api/...). Las fotos y videos (/media, /assets) los sirve IIS;
este servidor también los sirve, pero solo para las pruebas locales.

Uso:
  python servidor.py --raiz C:\\sitios\\nudodesen --privado C:\\sitios\\nudodesen-panel
  python servidor.py --privado C:\\sitios\\nudodesen-panel --clave "nueva contraseña"   (cambia la contraseña y sale)
"""
import argparse, base64, hashlib, hmac, json, mimetypes, os, re, secrets, shutil, struct, sys, threading, time, unicodedata, zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP = Path(__file__).resolve().parent
PREFIJO = "/admin"
SESION_DIAS = 30
LIMITES = {"imagen": 15 * 2**20, "video": 90 * 2**20, "modelo": 50 * 2**20, "zip": 95 * 2**20, "giro": 60 * 2**20}
EXT = {"imagen": {".jpg", ".jpeg", ".png", ".webp"}, "video": {".mp4", ".webm", ".mov"}, "modelo": {".glb"}, "zip": {".zip"},
       "giro": {".html", ".htm"}}
GIRO_MIN, GIRO_MAX = 8, 72   # vistas de un giro 360°
ZIP_MAX_ARCHIVOS = 60
ZIP_MAX_TOTAL = 300 * 2**20          # tamaño descomprimido máximo (evita "bombas zip")
GLTF_EXT = {".gltf", ".bin", ".png", ".jpg", ".jpeg", ".webp"}  # archivos que acompañan a un .gltf
MAX_JSON = 2 * 2**20
RESPALDOS = 40

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("text/javascript", ".js")


def firma_ok(data: bytes, ext: str) -> bool:
    """Comprueba que el contenido sea de verdad del tipo que dice la extensión."""
    if ext in (".jpg", ".jpeg"): return data[:3] == b"\xff\xd8\xff"
    if ext == ".png": return data[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == ".webp": return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext in (".mp4", ".mov"): return data[4:8] == b"ftyp"
    if ext == ".webm": return data[:4] == b"\x1a\x45\xdf\xa3"
    if ext == ".glb": return data[:4] == b"glTF"
    if ext == ".zip": return data[:4] == b"PK\x03\x04"
    if ext in (".html", ".htm"): return data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<"
    return False


def optimizar_imagen(crudo: bytes, ext: str, lado=1600):
    """Pasa una foto a WebP (calidad 82) y la reduce si mide más de `lado` px, conservando la transparencia.
    Usa Pillow si está instalado; si no está o algo falla, devuelve la imagen tal cual."""
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        im = Image.open(BytesIO(crudo))
        im = ImageOps.exif_transpose(im)
        if max(im.size) > lado: im.thumbnail((lado, lado), Image.LANCZOS)
        if im.mode not in ("RGB", "RGBA"): im = im.convert("RGBA" if "A" in im.getbands() or im.info.get("transparency") is not None else "RGB")
        out = BytesIO(); im.save(out, "WEBP", quality=82, method=4)
        nuevo = out.getvalue()
        return (nuevo, ".webp") if len(nuevo) < len(crudo) or ext not in (".webp", ".jpg", ".png") else (crudo, ext)
    except Exception:
        return crudo, ext


def ext_de_firma(data: bytes):
    for e in (".webp", ".png", ".jpg"):
        if firma_ok(data, e): return e
    return None


def agrupar_giro(fotos):
    """Si hay 8 o más fotos con el mismo nombre y un número al final (vista01, vista02…),
    son las vistas de un giro 360°: las devuelve ordenadas. Si no, devuelve None."""
    grupos = {}
    for nombre, m in fotos:
        r = re.fullmatch(r"(.*?)(\d+)", Path(nombre).stem)
        if r: grupos.setdefault(r.group(1).lower(), []).append((int(r.group(2)), m))
    if not grupos: return None
    clave, lista = max(grupos.items(), key=lambda kv: len(kv[1]))
    if not GIRO_MIN <= len(lista) <= GIRO_MAX: return None
    return [m for _, m in sorted(lista, key=lambda x: x[0])]


def es_relieve(gltf_json: dict) -> bool:
    """True si el modelo es casi plano (un "relieve" hecho desde una sola foto):
    su profundidad (eje Z, hacia el frente en glTF) es menos de un quinto de su alto o ancho."""
    try:
        acc = gltf_json["accessors"]
        mn, mx = [1e9] * 3, [-1e9] * 3
        for malla in gltf_json.get("meshes", []):
            for prim in malla.get("primitives", []):
                a = acc[prim["attributes"]["POSITION"]]
                for i in range(3):
                    mn[i] = min(mn[i], a["min"][i]); mx[i] = max(mx[i], a["max"][i])
        dx, dy, dz = (mx[i] - mn[i] for i in range(3))
        return dz < 0.2 * max(dx, dy)
    except Exception:
        return False


def json_de_glb(ruta: Path):
    with ruta.open("rb") as f:
        cab = f.read(20)
        if len(cab) < 20 or cab[:4] != b"glTF": return None
        largo, tipo = struct.unpack("<II", cab[12:20])
        if tipo != 0x4E4F534A or largo > 64 * 2**20: return None
        return json.loads(f.read(largo))


def slug(txt: str, maximo=40) -> str:
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return (t[:maximo].strip("-")) or "archivo"


# ---------------------------------------------------------------- configuración privada
class Privado:
    def __init__(self, carpeta: Path):
        self.carpeta = carpeta
        self.archivo = carpeta / "config.json"
        carpeta.mkdir(parents=True, exist_ok=True)
        self.cfg = json.loads(self.archivo.read_text("utf-8")) if self.archivo.exists() else {}
        if "secreto" not in self.cfg:
            self.cfg["secreto"] = secrets.token_hex(32)
            self.guardar()

    def guardar(self):
        tmp = self.archivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cfg, indent=1), "utf-8")
        os.replace(tmp, self.archivo)

    def poner_clave(self, clave: str):
        sal = secrets.token_bytes(16)
        it = 300_000
        h = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, it)
        self.cfg["clave"] = {"sal": sal.hex(), "it": it, "hash": h.hex()}
        self.cfg["secreto"] = secrets.token_hex(32)  # cierra las sesiones abiertas
        self.guardar()

    def clave_ok(self, clave: str) -> bool:
        c = self.cfg.get("clave")
        if not c: return False
        h = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(c["sal"]), c["it"])
        return hmac.compare_digest(h.hex(), c["hash"])

    def token(self) -> str:
        exp = str(int(time.time()) + SESION_DIAS * 86400)
        f = hmac.new(self.cfg["secreto"].encode(), exp.encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{f}"

    def token_ok(self, tok: str) -> bool:
        try:
            exp, f = tok.split(".", 1)
            bueno = hmac.new(self.cfg["secreto"].encode(), exp.encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(f, bueno) and int(exp) > time.time()
        except Exception:
            return False


# ---------------------------------------------------------------- validación del catálogo
def texto(v, maximo, obligatorio=False):
    if v is None: v = ""
    if not isinstance(v, str): raise ValueError("Texto no válido")
    v = v.strip()
    if obligatorio and not v: raise ValueError("Falta un dato obligatorio")
    if len(v) > maximo: raise ValueError(f"Texto demasiado largo (máx. {maximo})")
    return v


def ruta_medio(v):
    v = texto(v, 200, True)
    if not re.fullmatch(r"(media|assets)/[A-Za-z0-9_\-./]+", v) or ".." in v:
        raise ValueError("Ruta de archivo no válida")
    return v


def validar_portada(p: dict, out: dict, categorias: set):
    """Textos, imágenes y fechas del mosaico de la portada (todo opcional: si falta, la web usa lo de siempre).
    a = recuadro principal con el 3D, b y c = recuadros con foto que llevan a una categoría,
    d = cuenta regresiva hacia la próxima fecha especial de la lista `fechas`."""
    def img(v):
        return ruta_medio(v) if v else None
    POS = {v + h for v in "tmb" for h in "lcr"}       # posición del texto: arriba/centro/abajo × izq./centro/der.
    def lugar(x, r):
        if x.get("pos") in POS: r["pos"] = x["pos"]
        if x.get("foco") in ("top", "center", "bottom"): r["foco"] = x["foco"]
        if x.get("btn") in POS or x.get("btn") == "junto": r["btn"] = x["btn"]   # botón suelto en otra posición
    a = p.get("a") or {}
    if a: out["a"] = {"acento": texto(a.get("acento"), 60), "titulo": texto(a.get("titulo"), 120), "boton": texto(a.get("boton"), 30)}
    for k in ("b", "c"):
        x = p.get(k) or {}
        if not x: continue
        cat = texto(x.get("categoria"), 40)
        if cat and cat not in categorias: cat = ""
        r = {"titulo": texto(x.get("titulo"), 60), "boton": texto(x.get("boton"), 30), "categoria": cat}
        if x.get("imagen"): r["imagen"] = img(x["imagen"])
        lugar(x, r)
        out[k] = r
    d = p.get("d") or {}
    if d:
        out["d"] = {"acento": texto(d.get("acento"), 60), "titulo": texto(d.get("titulo"), 80)}
        if d.get("imagen"): out["d"]["imagen"] = img(d["imagen"])
        lugar(d, out["d"])
    t = p.get("trabajos") or {}
    if t:   # textos de la sección «Nuestros trabajos»
        out["trabajos"] = {"titulo": texto(t.get("titulo"), 60), "sub": texto(t.get("sub"), 160), "nota": texto(t.get("nota"), 240)}
    fechas = p.get("fechas")
    if fechas is not None:
        if not isinstance(fechas, list) or len(fechas) > 12: raise ValueError("La portada admite hasta 12 fechas especiales")
        out["fechas"] = []
        for f in fechas:
            regla = f.get("regla") if f.get("regla") in ("fija", "segundo-domingo") else "fija"
            mes = int(f.get("mes") or 0)
            dia = int(f.get("dia") or 0) if regla == "fija" else 0
            if not 1 <= mes <= 12 or (regla == "fija" and not 1 <= dia <= 31): raise ValueError("Revisa el día y el mes de las fechas especiales")
            r = {"nombre": texto(f.get("nombre"), 40, True), "regla": regla, "mes": mes, "dia": dia, "ocasion": texto(f.get("ocasion"), 60)}
            if f.get("imagen"): r["imagen"] = img(f["imagen"])
            out["fechas"].append(r)


def validar(c):
    if not isinstance(c, dict): raise ValueError("Formato no válido")
    out = {"version": 1}
    tel = re.sub(r"\D", "", str(c.get("telefono", "")))
    if not 8 <= len(tel) <= 15: raise ValueError("El número de WhatsApp debe tener código de país y número")
    out["telefono"] = tel
    p = c.get("portada") or {}
    out["portada"] = {"modelo": ruta_medio(p.get("modelo") or "assets/muneca.glb")}
    if p.get("poster"): out["portada"]["poster"] = ruta_medio(p["poster"])
    if p.get("relieve"): out["portada"]["relieve"] = True
    cats = c.get("categorias")
    if not isinstance(cats, list) or not cats or len(cats) > 30: raise ValueError("Debe haber entre 1 y 30 categorías")
    out["categorias"] = []
    nombres = set()
    for k in cats:
        n = texto(k.get("nombre"), 40, True)
        if n in nombres: raise ValueError(f"Categoría repetida: {n}")
        nombres.add(n)
        out["categorias"].append({"nombre": n, "frase": texto(k.get("frase"), 120)})
    validar_portada(p, out["portada"], nombres)
    prods = c.get("productos")
    if not isinstance(prods, list) or len(prods) > 500: raise ValueError("Lista de productos no válida")
    out["productos"] = []
    ids = set()
    for x in prods:
        pid = texto(x.get("id"), 60, True)
        if not re.fullmatch(r"[a-z0-9\-]+", pid) or pid in ids: raise ValueError(f"Identificador no válido: {pid}")
        ids.add(pid)
        cat = texto(x.get("categoria"), 40, True)
        if cat not in nombres: raise ValueError(f"La categoría «{cat}» no existe")
        precio = x.get("precio")
        if precio in ("", None): precio = None
        else:
            precio = float(precio)
            if not 0 <= precio <= 100000: raise ValueError("Precio fuera de rango")
            precio = int(precio) if precio == int(precio) else round(precio, 2)
        medios = []
        for m in (x.get("medios") or [])[:30]:
            tipo = m.get("tipo")
            if tipo not in ("imagen", "video", "modelo", "giro"): raise ValueError("Tipo de archivo no válido")
            if tipo == "giro":
                vistas = m.get("vistas")
                if not isinstance(vistas, list) or not GIRO_MIN <= len(vistas) <= GIRO_MAX:
                    raise ValueError(f"Un giro 360° necesita entre {GIRO_MIN} y {GIRO_MAX} vistas")
                vistas = [ruta_medio(v) for v in vistas]
                medios.append({"tipo": "giro", "src": vistas[0], "vistas": vistas})
                continue
            mm = {"tipo": tipo, "src": ruta_medio(m.get("src"))}
            if m.get("poster"): mm["poster"] = ruta_medio(m["poster"])
            if tipo == "modelo" and m.get("relieve"): mm["relieve"] = True
            medios.append(mm)
        medidas = {}
        for k in ("alto", "ancho", "fondo"):
            v = (x.get("medidas") or {}).get(k)
            if v in ("", None): continue
            v = float(v)
            if not 0 < v <= 500: raise ValueError(f"La medida «{k}» debe estar entre 0 y 500 cm")
            medidas[k] = int(v) if v == int(v) else round(v, 1)
        materiales = []
        for mat in (x.get("materiales") or [])[:15]:
            mat = texto(mat, 50)
            if mat and mat.lower() not in (m.lower() for m in materiales): materiales.append(mat)
        prod = {
            "id": pid, "nombre": texto(x.get("nombre"), 80, True), "categoria": cat, "precio": precio,
            "detalle": texto(x.get("detalle"), 400), "visible": bool(x.get("visible", True)), "medios": medios}
        if medidas: prod["medidas"] = medidas
        if materiales: prod["materiales"] = materiales
        out["productos"].append(prod)
    return out


# ---------------------------------------------------------------- páginas de producto
SITIO = "https://nudodesen.com"
MARCA = ".nudodesen-generado"  # archivo que marca las carpetas creadas por el generador


def h(t):  # escapa para HTML
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def foto_de(p):
    m = p.get("medios") or []
    for x in m:
        if x["tipo"] == "imagen": return x["src"]
    for x in m:
        if x.get("poster"): return x["poster"]
        if x["tipo"] == "giro": return x["src"]
    return "assets/og-imagen.png"


def giro(m):
    """Atributos del visor 3D. Un modelo en relieve (casi plano) solo se deja girar ±40°
    y se balancea suave (lo hace el script de la página); uno completo gira entero."""
    if m.get("relieve"):
        return 'data-relieve camera-orbit="0deg 85deg auto" min-camera-orbit="-40deg 70deg auto" max-camera-orbit="40deg 100deg auto"'
    return 'auto-rotate camera-orbit="20deg 80deg auto"'


def cm(v):
    return f"{v:g}".replace(".", ",") + " cm"


def medidas_txt(p):
    """«25 cm de alto × 12 cm de ancho × 10 cm de fondo» con lo que haya."""
    m = p.get("medidas") or {}
    return " × ".join(f"{cm(m[k])} de {k}" for k in ("alto", "ancho", "fondo") if k in m)


def precio_txt(p):
    return f"Desde S/ {p['precio']:,}".replace(",", " ") if p.get("precio") is not None else "A cotizar"


def version(ruta: Path) -> str:
    """Huella corta del contenido de un archivo, para ponerla en su dirección (archivo.js?v=huella).
    Si el archivo cambia, cambia la dirección y ni Cloudflare ni el navegador pueden usar una copia vieja."""
    try: return hashlib.sha1(ruta.read_bytes()).hexdigest()[:10]
    except OSError: return "0"


def con_versiones(html: str, raiz: Path, rutas) -> str:
    for r in rutas:   # r = "assets/zoom.js" -> "/assets/zoom.js?v=..." (con o sin barra inicial)
        v = version(raiz / r)
        patron = r"""(["'])(/?""" + re.escape(r) + r""")(\?v=[0-9a-f]+)?(["'])"""
        html = re.sub(patron, lambda m, v=v: m.group(1) + m.group(2) + "?v=" + v + m.group(4), html)
    return html


def generar_paginas(raiz: Path, cat: dict):
    """Crea /productos/<id>/index.html para cada producto visible y /sitemap.xml.
    Reutiliza los estilos y los iconos de index.html para que todo combine."""
    from urllib.parse import quote
    portada = (raiz / "index.html").read_text("utf-8")
    estilos = re.search(r"<style>.*?</style>", portada, re.S).group(0)
    iconos = re.search(r'<svg width="0".*?</svg>', portada, re.S).group(0)
    plantilla = (APP / "plantilla-producto.html").read_text("utf-8")
    tel = cat["telefono"]
    wa = lambda t: "https://wa.me/" + tel + "?text=" + quote(t)
    visibles = [p for p in cat["productos"] if p.get("visible", True)]
    base = raiz / "productos"
    base.mkdir(exist_ok=True)
    (base / MARCA).write_text("carpeta generada por el panel", "utf-8")
    hechos = set()
    for p in visibles:
        medios = p.get("medios") or []
        img = foto_de(p)
        primero = medios[0] if medios else {"tipo": "imagen", "src": img}
        if primero["tipo"] == "imagen":
            primer = f'<img src="/{h(primero["src"])}" alt="{h(p["nombre"])}">'
        elif primero["tipo"] == "giro":  # sin JavaScript se ve la primera vista; con JS, giro360 la vuelve girable
            primer = f'<div class="g360"><img src="/{h(primero["src"])}" alt="{h(p["nombre"])}"></div>'
        elif primero["tipo"] == "video":
            pos = f' poster="/{h(primero["poster"])}"' if primero.get("poster") else ""
            primer = f'<video src="/{h(primero["src"])}"{pos} controls playsinline preload="metadata"></video>'
        else:
            primer = (f'<model-viewer src="/{h(primero["src"])}" camera-controls shadow-intensity="1" {giro(primero)} '
                      f'interaction-prompt="none" alt="{h(p["nombre"])} en 3D"></model-viewer><small class="hint3d">Gíralo con el dedo</small>')
        mini = ""
        if len(medios) > 1:
            for i, m in enumerate(medios):
                etiqueta = {"video": "Video", "modelo": "Modelo 3D", "giro": "Giro 360°"}.get(m["tipo"], f"Foto {i + 1}")
                if m["tipo"] == "modelo": dentro = '<svg class="i"><use href="#i-cubo"/></svg>'
                else: dentro = f'<img src="/{h(m.get("poster") or "assets/logo.png") if m["tipo"] == "video" else h(m["src"])}" alt="" loading="lazy">'
                if m["tipo"] == "video": dentro += '<svg class="i pl"><use href="#i-play"/></svg>'
                if m["tipo"] == "giro": dentro += '<svg class="i pl g"><use href="#i-360"/></svg>'
                mini += f'<button aria-label="{etiqueta}" aria-current="{"true" if i == 0 else "false"}" data-fi="{i}">{dentro}</button>'
        otros = [x for x in visibles if x["id"] != p["id"]]
        otros = ([x for x in otros if x["categoria"] == p["categoria"]] + [x for x in otros if x["categoria"] != p["categoria"]])[:4]
        rel = ""
        if otros:
            rel = '<section class="sec relacionados"><h2 class="ttl"><span>También te puede gustar</span></h2><div class="grid">' + "".join(
                f'<article class="card"><div class="pic"><a href="/productos/{h(x["id"])}/"><img src="/{h(foto_de(x))}" alt="{h(x["nombre"])}" width="304" height="344" loading="lazy"></a></div>'
                f'<div class="body"><span class="k">{h(x["categoria"])}</span><h3><a href="/productos/{h(x["id"])}/">{h(x["nombre"])}</a></h3>'
                f'<span class="p">{"<small>Desde</small> S/ " + h(x["precio"]) if x.get("precio") is not None else "A cotizar"}</span></div></article>'
                for x in otros) + "</div></section>"
        url = f"{SITIO}/productos/{p['id']}/"
        desc = ((p.get("detalle") or "") + (f" Mide {medidas_txt(p)}." if p.get("medidas") else "")
                + " Amigurumi hecho a mano en Puno, Perú. Envíos a todo el país.")
        ld = {"@context": "https://schema.org", "@type": "Product", "name": p["nombre"], "description": desc.strip(),
              "image": [f"{SITIO}/{m['src']}" for m in medios if m["tipo"] == "imagen"] or [f"{SITIO}/{img}"],
              "category": p["categoria"], "brand": {"@type": "Brand", "name": "Nudo de SEN"}, "url": url}
        med = p.get("medidas") or {}
        for k, campo in (("alto", "height"), ("ancho", "width"), ("fondo", "depth")):
            if k in med: ld[campo] = {"@type": "QuantitativeValue", "value": med[k], "unitCode": "CMT"}
        if p.get("materiales"): ld["material"] = ", ".join(p["materiales"])
        ficha = ""
        if med or p.get("materiales"):
            ficha = '<dl class="ficha">'
            if med: ficha += f'<div><dt><svg class="i"><use href="#i-regla"/></svg>Medidas</dt><dd>{h(medidas_txt(p))}</dd></div>'
            if p.get("materiales"):
                ficha += ('<div><dt><svg class="i"><use href="#i-ovillo"/></svg>Materiales</dt><dd><ul class="mats">'
                          + "".join(f"<li>{h(x)}</li>" for x in p["materiales"]) + "</ul></dd></div>")
            ficha += "</dl>"
        if p.get("precio") is not None:
            ld["offers"] = {"@type": "Offer", "priceCurrency": "PEN", "price": p["precio"], "availability": "https://schema.org/MadeToOrder", "url": url}
        valores = {
            "NOMBRE": h(p["nombre"]), "DESCRIPCION": h(desc.strip()), "PRECIO_TXT": h(precio_txt(p)), "URL": h(url),
            "IMAGEN_ABS": h(f"{SITIO}/{img}"), "ID": h(p["id"]), "CATEGORIA": h(p["categoria"]), "CATEGORIA_URL": h(quote(p["categoria"])),
            "DETALLE": h(p.get("detalle") or ""), "PRIMER_MEDIO": primer, "FICHA": ficha, "MINIATURAS": mini, "RELACIONADOS": rel,
            "PRECIO_HTML": ('<small>Desde</small> S/ ' + h(p["precio"])) if p.get("precio") is not None else "A cotizar",
            "WA_GENERAL": h(wa("Hola, vi su página y quiero información sobre sus amigurumis.")),
            "WA_PRODUCTO": h(wa(f"Hola, vi en su página el producto «{p['nombre']}» ({url}) y quiero más información.")),
            "MODEL_VIEWER": '<script type="module" src="/assets/model-viewer.min.js"></script>' if any(m["tipo"] == "modelo" for m in medios) else "",
            "GIRO_JS": '<script src="/assets/giro360.js"></script>' if any(m["tipo"] == "giro" for m in medios) else "",
            "JSONLD": json.dumps(ld, ensure_ascii=False).replace("</", "<\\/"),
            "MEDIOS_JSON": json.dumps(medios, ensure_ascii=False).replace("</", "<\\/"),
            "NOMBRE_JS": json.dumps(p["nombre"], ensure_ascii=False).replace("</", "<\\/"),
            "ESTILOS": estilos, "ICONOS": iconos,
        }
        html = re.sub(r"%%([A-Z_]+)%%", lambda m: valores[m.group(1)], plantilla)
        html = con_versiones(html, raiz, ("assets/zoom.js", "assets/giro360.js"))
        carpeta = base / p["id"]
        carpeta.mkdir(exist_ok=True)
        tmp = carpeta / "index.html.tmp"
        tmp.write_text(html, "utf-8")
        os.replace(tmp, carpeta / "index.html")
        hechos.add(p["id"])
    # quita páginas de productos borrados u ocultos (solo carpetas creadas por el generador)
    for d in base.iterdir():
        if d.is_dir() and d.name not in hechos and (d / "index.html").exists() and len(list(d.iterdir())) == 1:
            shutil.rmtree(d, ignore_errors=True)
    hoy = datetime.now().strftime("%Y-%m-%d")
    urls = [f"{SITIO}/"] + [f"{SITIO}/productos/{i}/" for i in sorted(hechos)]
    (raiz / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc><lastmod>{hoy}</lastmod></url>\n" for u in urls) + "</urlset>\n", "utf-8")
    return len(hechos)


# ---------------------------------------------------------------- historial y métricas
class Conflicto(Exception):
    """El catálogo cambió desde otro dispositivo después de que este lo abrió."""


EVENTOS = {"inicio", "vista", "interes", "whatsapp"}
METRICAS_DIAS = 400
ACTIVIDAD_MAX = 2000


def resumen_cambios(a: dict, b: dict) -> list:
    """Describe en palabras qué cambió entre dos versiones del catálogo (para el registro de actividad)."""
    cambios = []
    pa = {p["id"]: p for p in a.get("productos", [])}
    pb = {p["id"]: p for p in b.get("productos", [])}
    for i in [x["id"] for x in b.get("productos", []) if x["id"] not in pa]: cambios.append(f"Agregó «{pb[i]['nombre']}»")
    for i in [x["id"] for x in a.get("productos", []) if x["id"] not in pb]: cambios.append(f"Eliminó «{pa[i]['nombre']}»")
    nombres = {"nombre": "nombre", "categoria": "categoría", "detalle": "descripción", "medios": "fotos, videos o 3D",
               "medidas": "medidas", "materiales": "materiales"}
    for i in [x["id"] for x in b.get("productos", []) if x["id"] in pa]:
        x, y = pa[i], pb[i]
        partes = []
        if x.get("precio") != y.get("precio"):
            f = lambda v: "a cotizar" if v is None else f"S/ {v}"
            partes.append(f"precio {f(x.get('precio'))} → {f(y.get('precio'))}")
        if x.get("visible", True) != y.get("visible", True): partes.append("lo mostró" if y.get("visible", True) else "lo ocultó")
        partes += [n for k, n in nombres.items() if (x.get(k) or None) != (y.get(k) or None)]
        if partes: cambios.append(f"Editó «{y['nombre']}»: " + ", ".join(partes))
    oa = [p["id"] for p in a.get("productos", []) if p["id"] in pb]
    ob = [p["id"] for p in b.get("productos", []) if p["id"] in pa]
    if oa != ob: cambios.append("Cambió el orden de los productos")
    if a.get("categorias") != b.get("categorias"): cambios.append("Cambió las categorías")
    if a.get("telefono") != b.get("telefono"): cambios.append("Cambió el número de WhatsApp")
    pa_, pb_ = a.get("portada") or {}, b.get("portada") or {}
    if pa_.get("modelo") != pb_.get("modelo"): cambios.append("Cambió el 3D de la portada")
    if {k: v for k, v in pa_.items() if k != "modelo"} != {k: v for k, v in pb_.items() if k != "modelo"}: cambios.append("Cambió los textos, imágenes o fechas de la portada")
    return cambios or ["Guardó sin cambios"]


def leer_json_archivo(ruta: Path, defecto):
    try: return json.loads(ruta.read_text("utf-8"))
    except Exception: return defecto


def escribir_json(ruta: Path, datos):
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False), "utf-8")
    os.replace(tmp, ruta)


# ---------------------------------------------------------------- servidor
class Panel(BaseHTTPRequestHandler):
    server_version = "NudoPanel/1"
    sys_version = ""
    intentos: dict = {}
    eventos_ip: dict = {}

    # utilidades
    def ip(self):
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def enviar(self, codigo, cuerpo=b"", tipo="application/json; charset=utf-8", extra=None):
        if isinstance(cuerpo, (dict, list)): cuerpo = json.dumps(cuerpo, ensure_ascii=False).encode()
        elif isinstance(cuerpo, str): cuerpo = cuerpo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "same-origin")
        if not self.server.dev: self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(cuerpo)

    def error(self, codigo, msg):
        self.enviar(codigo, {"ok": False, "error": msg})

    def sesion(self):
        if self.server.dev: return True
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "nds" and self.server.privado.token_ok(v): return True
        return False

    def es_https(self):
        return "https" in (self.headers.get("CF-Visitor") or "") or self.headers.get("X-Forwarded-Proto") == "https"

    def cookie(self, valor, edad):
        segura = "; Secure" if self.es_https() else ""
        return f"nds={valor}; Path={PREFIJO}; Max-Age={edad}; HttpOnly; SameSite=Strict{segura}"

    def camino(self):
        """Camino de la petición sin el prefijo /admin (None si no le corresponde al panel)."""
        p = urlsplit(self.path).path
        if p.startswith("/media/") or p.startswith("/assets/"): return p
        if p == PREFIJO: return ""
        if p.startswith(PREFIJO + "/"): return p[len(PREFIJO):]
        return None

    def leer_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_JSON: raise ValueError("Cuerpo vacío o demasiado grande")
        return json.loads(self.rfile.read(n))

    def mutacion_ok(self):
        # Defensa contra peticiones de otros sitios: exige una cabecera propia (fuerza preflight CORS, que no se permite).
        return self.headers.get("X-Nudo") == "1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.ip(), fmt % args))

    # archivos estáticos
    def estatico(self, base: Path, rel: str, cache="no-cache"):
        destino = (base / rel).resolve()
        if base.resolve() not in destino.parents or not destino.is_file():
            return self.error(404, "No encontrado")
        tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
        tam = destino.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(tam))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD": return
        with destino.open("rb") as f: shutil.copyfileobj(f, self.wfile, 1 << 16)

    # métodos HTTP
    def do_HEAD(self): self.do_GET()

    def do_GET(self):
        p = self.camino()
        if p is None: return self.error(404, "No encontrado")
        if p == "":
            return self.enviar(301, b"", "text/plain", {"Location": PREFIJO + "/"})
        if p in ("/", "/index.html"):
            html = con_versiones((APP / "index.html").read_text("utf-8"), self.server.raiz, ("assets/giro360.js",))
            html = re.sub(r'(/admin/panel\.js)(\?v=[0-9a-f]+)?"', lambda m: m.group(1) + "?v=" + version(APP / "panel.js") + '"', html).encode()
            csp = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                   "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                   # Cloudflare inyecta su script de estadísticas (Web Analytics): se permite solo ese origen
                   "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com; "
                   "connect-src 'self' blob: data: https://cloudflareinsights.com; worker-src 'self' blob:; "
                   + ("" if self.server.dev else "frame-ancestors 'none'; ") + "base-uri 'none'; form-action 'self'")
            return self.enviar(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": csp, "Cache-Control": "no-cache"})
        # archivos de la app del panel (instalable): JS, manifiesto y service worker
        if p in ("/panel.js", "/sw.js", "/manifest.webmanifest"):
            tipo = "application/manifest+json" if p.endswith(".webmanifest") else "text/javascript; charset=utf-8"
            return self.enviar(200, (APP / p.lstrip("/")).read_bytes(), tipo, {"Cache-Control": "no-cache"})
        if p == "/api/sesion":
            return self.enviar(200, {"ok": self.sesion()})
        if p.startswith("/api/") and not self.sesion(): return self.error(401, "Inicia sesión")
        if p == "/api/catalogo":
            return self.enviar(200, self.server.leer_catalogo(), extra={"Cache-Control": "no-store"})
        if p == "/api/historial":
            return self.enviar(200, {"ok": True, "versiones": self.server.historial()}, extra={"Cache-Control": "no-store"})
        if p == "/api/actividad":
            return self.enviar(200, {"ok": True, "actividad": self.server.actividad()}, extra={"Cache-Control": "no-store"})
        if p == "/api/metricas":
            dias = int((parse_qs(urlsplit(self.path).query).get("dias") or ["30"])[0])
            return self.enviar(200, {"ok": True, **self.server.metricas(max(1, min(dias, 365)))}, extra={"Cache-Control": "no-store"})
        if p.startswith("/media/") or p.startswith("/assets/"):
            return self.estatico(self.server.raiz, p.lstrip("/"), "public, max-age=3600")
        self.error(404, "No encontrado")

    def do_POST(self):
        u = urlsplit(self.path)
        p = self.camino()
        if p == "/api/e": return self.evento()          # público: contador de visitas y clics de la web
        if p == "/compartir":                            # «Compartir» desde el celular sin service worker activo
            return self.enviar(303, b"", "text/plain", {"Location": PREFIJO + "/"})
        if not self.mutacion_ok(): return self.error(403, "Petición no permitida")
        if p == "/api/entrar":
            ahora = time.time()
            lista = [t for t in Panel.intentos.get(self.ip(), []) if ahora - t < 900]
            if len(lista) >= 8: return self.error(429, "Demasiados intentos. Espera 15 minutos.")
            try: clave = str(self.leer_json().get("clave", ""))
            except Exception: return self.error(400, "Petición no válida")
            time.sleep(0.4)
            if not self.server.privado.clave_ok(clave):
                lista.append(ahora); Panel.intentos[self.ip()] = lista
                return self.error(401, "Contraseña incorrecta")
            Panel.intentos.pop(self.ip(), None)
            return self.enviar(200, {"ok": True}, extra={"Set-Cookie": self.cookie(self.server.privado.token(), SESION_DIAS * 86400)})
        if p == "/api/salir":
            return self.enviar(200, {"ok": True}, extra={"Set-Cookie": self.cookie("", 0)})
        if not self.sesion(): return self.error(401, "Inicia sesión")
        if p == "/api/subir":
            return self.subir(parse_qs(u.query))
        if p == "/api/restaurar":
            try:
                nombre = str(self.leer_json().get("nombre", ""))
                if not re.fullmatch(r"catalogo-[0-9\-]+\.json", nombre): raise ValueError("Versión no válida")
                viejo = leer_json_archivo(self.server.privado.carpeta / "respaldos" / nombre, None)
                if viejo is None: raise ValueError("Esa versión ya no existe")
                limpio = validar(viejo)
                c = self.server.guardar_catalogo(limpio, None, self.ip(), "Restauró una versión anterior")
            except (ValueError, TypeError, AttributeError) as e:
                return self.error(400, str(e))
            return self.enviar(200, {"ok": True, "catalogo": c})
        self.error(404, "No encontrado")

    def do_PUT(self):
        if not self.mutacion_ok(): return self.error(403, "Petición no permitida")
        if self.camino() != "/api/catalogo": return self.error(404, "No encontrado")
        if not self.sesion(): return self.error(401, "Inicia sesión")
        try:
            cuerpo = self.leer_json()
            limpio = validar(cuerpo)
            c = self.server.guardar_catalogo(limpio, cuerpo.get("rev"), self.ip())
        except Conflicto:
            return self.enviar(409, {"ok": False, "conflicto": True,
                                     "error": "Alguien guardó cambios desde otro dispositivo mientras editabas.",
                                     "catalogo": self.server.leer_catalogo()})
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
            return self.error(400, str(e) or "Datos no válidos")
        self.enviar(200, {"ok": True, "catalogo": c})

    def evento(self):
        """Cuenta una visita o un clic de la web pública. No guarda IP, cookies ni datos de la persona:
        solo suma 1 al contador del día. Llega por navigator.sendBeacon (texto plano, sin preflight)."""
        ua = (self.headers.get("User-Agent") or "").lower()
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 300 or any(b in ua for b in ("bot", "crawl", "spider", "preview", "headless", "facebookexternalhit", "whatsapp")):
            return self.enviar(204, b"", "text/plain")
        ahora = time.time()
        lista = [t for t in Panel.eventos_ip.get(self.ip(), []) if ahora - t < 60]
        if len(lista) >= 40: return self.enviar(204, b"", "text/plain")
        lista.append(ahora); Panel.eventos_ip[self.ip()] = lista
        if len(Panel.eventos_ip) > 5000: Panel.eventos_ip.clear()
        try:
            d = json.loads(self.rfile.read(n))
            t, pid = str(d.get("t", "")), str(d.get("p") or "")
        except Exception:
            return self.enviar(204, b"", "text/plain")
        if t in EVENTOS and (not pid or pid in self.server.ids): self.server.contar(t, pid)
        self.enviar(204, b"", "text/plain")

    def subir(self, q):
        tipo = (q.get("tipo") or [""])[0]
        nombre = (q.get("nombre") or ["archivo"])[0]
        if tipo not in LIMITES: return self.error(400, "Tipo no válido")
        ext = Path(nombre).suffix.lower()
        if ext not in EXT[tipo]: return self.error(400, f"Formato no admitido para {tipo}: {ext or 'sin extensión'}")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0: return self.error(400, "Archivo vacío")
        if n > LIMITES[tipo]: return self.error(413, f"El archivo pesa demasiado (máx. {LIMITES[tipo] // 2**20} MB)")
        carpeta = self.server.raiz / "media" / datetime.now().strftime("%Y-%m")
        carpeta.mkdir(parents=True, exist_ok=True)
        final = carpeta / f"{slug(Path(nombre).stem)}-{secrets.token_hex(3)}{'.jpg' if ext == '.jpeg' else ext}"
        tmp = final.with_suffix(final.suffix + ".parcial")
        resto, cabeza = n, b""
        try:
            with tmp.open("wb") as f:
                while resto:
                    trozo = self.rfile.read(min(1 << 16, resto))
                    if not trozo: raise IOError("Subida interrumpida")
                    if len(cabeza) < 16: cabeza += trozo[:16]
                    f.write(trozo); resto -= len(trozo)
            if not firma_ok(cabeza, ext): raise ValueError("El contenido no corresponde al tipo de archivo")
            # ZIP y HTML se procesan desde el temporal (.parcial, que IIS no sirve) y luego se borran
            if tipo not in ("zip", "giro"): os.replace(tmp, final)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            return self.error(400, str(e))
        rel = final.relative_to(self.server.raiz).as_posix()
        if tipo in ("zip", "giro"): final = tmp
        if tipo == "zip":
            creados = []
            try:
                medios, omitidos = self.abrir_zip(final, carpeta, creados)
            except Exception as e:
                # borra solo lo que este ZIP alcanzó a sacar antes de fallar
                for x in creados:
                    shutil.rmtree(x, ignore_errors=True) if x.is_dir() else x.unlink(missing_ok=True)
                return self.error(400, f"No se pudo abrir el ZIP: {e}")
            finally:
                final.unlink(missing_ok=True)
            if not medios: return self.error(400, "El ZIP no trae fotos, modelos 3D ni un giro 360° que se puedan usar")
            return self.enviar(200, {"ok": True, "medios": medios, "omitidos": omitidos})
        if tipo == "giro":
            try:
                return self.enviar(200, {"ok": True, "medios": [self.giro_de_html(final, carpeta, Path(nombre).stem)], "omitidos": []})
            except Exception as e:
                return self.error(400, f"No se pudieron sacar las vistas: {e}")
            finally:
                final.unlink(missing_ok=True)   # el HTML subido nunca se publica
        r = {"ok": True, "src": rel, "bytes": n}
        if tipo == "modelo":
            j = json_de_glb(final)
            if j is not None and es_relieve(j): r["relieve"] = True
        self.enviar(200, r)

    def giro_de_html(self, hpath: Path, carpeta: Path, base: str):
        """Saca, en orden, las imágenes incrustadas (data:image/...;base64) de una página de
        «vistas 360°» y las guarda como archivos. Del HTML no se usa nada más."""
        return self.giro_de_texto(hpath.read_text("utf-8", errors="ignore"), carpeta, base)

    def giro_de_texto(self, texto: str, carpeta: Path, base: str):
        datos = re.findall(r"data:image/(?:webp|png|jpe?g);base64,([A-Za-z0-9+/=]+)", texto)
        if not GIRO_MIN <= len(datos) <= GIRO_MAX:
            raise ValueError(f"trae {len(datos)} imágenes; un giro necesita entre {GIRO_MIN} y {GIRO_MAX}")
        destino = carpeta / f"{slug(base)}-{secrets.token_hex(3)}"
        destino.mkdir()
        try:
            vistas = []
            for i, b64 in enumerate(datos):
                crudo = base64.b64decode(b64)
                ext = ext_de_firma(crudo[:16])
                if not ext or len(crudo) > LIMITES["imagen"]: raise ValueError(f"la vista {i + 1} no es una imagen válida")
                crudo, ext = optimizar_imagen(crudo, ext)
                f = destino / f"v{i:02d}{ext}"
                f.write_bytes(crudo)
                vistas.append(f.relative_to(self.server.raiz).as_posix())
        except Exception:
            shutil.rmtree(destino, ignore_errors=True)
            raise
        return {"tipo": "giro", "src": vistas[0], "vistas": vistas}

    def abrir_zip(self, zpath: Path, carpeta: Path, creados: list):
        """Saca de un ZIP los modelos 3D (.glb o .gltf con sus archivos) y las fotos.
        Devuelve la lista de medios (el modelo primero) y los nombres que se ignoraron."""
        medios, fotos, omitidos = [], [], []
        with zipfile.ZipFile(zpath) as z:
            entradas = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
                        and not Path(i.filename).name.startswith(".")]
            if len(entradas) > ZIP_MAX_ARCHIVOS: raise ValueError(f"trae más de {ZIP_MAX_ARCHIVOS} archivos")
            if sum(i.file_size for i in entradas) > ZIP_MAX_TOTAL: raise ValueError("descomprimido pesa demasiado")
            gltfs = [i for i in entradas if i.filename.lower().endswith(".gltf")]
            usados = set()
            # 1) modelos .gltf: se copian con sus archivos (.bin y texturas) a una carpeta propia
            for g in gltfs:
                raiz_zip = g.filename.rsplit("/", 1)[0] + "/" if "/" in g.filename else ""
                destino = carpeta / f"{slug(Path(g.filename).stem)}-{secrets.token_hex(3)}"
                destino.mkdir(); creados.append(destino)
                for i in entradas:
                    if not i.filename.startswith(raiz_zip): continue
                    relz = i.filename[len(raiz_zip):]
                    if Path(relz).suffix.lower() not in GLTF_EXT: continue
                    partes = [p for p in relz.split("/") if p]
                    if any(p in ("..", ".") or not re.fullmatch(r"[A-Za-z0-9_\-. ]+", p) for p in partes):
                        omitidos.append(i.filename); continue
                    salida = destino.joinpath(*[p.replace(" ", "_") for p in partes])
                    if destino.resolve() not in salida.resolve().parents: continue
                    salida.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(i) as src, salida.open("wb") as dst: shutil.copyfileobj(src, dst)
                    usados.add(i.filename)
                # las referencias dentro del .gltf usan los nombres originales: se ajustan los espacios
                gpath = destino.joinpath(*[p.replace(" ", "_") for p in g.filename[len(raiz_zip):].split("/") if p])
                datos = json.loads(gpath.read_text("utf-8"))
                for lista in ("buffers", "images"):
                    for b in datos.get(lista, []):
                        u = b.get("uri", "")
                        if u and not u.startswith("data:"):
                            if "://" in u or u.startswith("/") or ".." in u: raise ValueError("el .gltf apunta a archivos de fuera")
                            b["uri"] = u.replace("%20", "_").replace(" ", "_")
                gpath.write_text(json.dumps(datos), "utf-8")
                m = {"tipo": "modelo", "src": gpath.relative_to(self.server.raiz).as_posix()}
                if es_relieve(datos): m["relieve"] = True
                medios.append(m)
            # 2) modelos .glb y fotos sueltas
            for i in entradas:
                if i.filename in usados: continue
                ext = Path(i.filename).suffix.lower()
                # una página de «vistas 360°» dentro del ZIP: se sacan sus imágenes como un giro
                if ext in EXT["giro"] and i.file_size <= LIMITES["giro"]:
                    try:
                        g = self.giro_de_texto(z.read(i).decode("utf-8", "ignore"), carpeta, Path(i.filename).stem)
                        creados.append(carpeta / Path(g["src"]).parent.name)
                        medios.insert(0, g)
                    except ValueError:
                        omitidos.append(i.filename)
                    continue
                tipo = "modelo" if ext == ".glb" else "imagen" if ext in EXT["imagen"] else None
                if not tipo or i.file_size > LIMITES[tipo]:
                    omitidos.append(i.filename); continue
                if tipo == "imagen":   # las fotos se pasan a WebP (más livianas para celulares)
                    crudo = z.read(i)
                    if not firma_ok(crudo[:16], ext): omitidos.append(i.filename); continue
                    crudo, ext2 = optimizar_imagen(crudo, ext)
                    final = carpeta / f"{slug(Path(i.filename).stem)}-{secrets.token_hex(3)}{'.jpg' if ext2 == '.jpeg' else ext2}"
                    creados.append(final); final.write_bytes(crudo)
                    fotos.append((i.filename, {"tipo": "imagen", "src": final.relative_to(self.server.raiz).as_posix()}))
                    continue
                final = carpeta / f"{slug(Path(i.filename).stem)}-{secrets.token_hex(3)}{ext}"
                creados.append(final)
                with z.open(i) as src, final.open("wb") as dst: shutil.copyfileobj(src, dst)
                with final.open("rb") as f: cab = f.read(16)
                if not firma_ok(cab, ext):
                    final.unlink(); omitidos.append(i.filename); continue
                m = {"tipo": tipo, "src": final.relative_to(self.server.raiz).as_posix()}
                if tipo == "modelo":
                    j = json_de_glb(final)
                    if j is not None and es_relieve(j): m["relieve"] = True
                    medios.append(m)
                else:
                    fotos.append((i.filename, m))
        # 3) muchas fotos numeradas (vista01, vista02…) = un giro 360°
        giro = agrupar_giro(fotos)
        if giro:
            medios.insert(0, {"tipo": "giro", "src": giro[0]["src"], "vistas": [m["src"] for m in giro]})
            fotos = [(n, m) for n, m in fotos if m not in giro]
        return medios + [m for _, m in fotos], omitidos


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, dir_, raiz: Path, privado: Privado, dev=False):
        super().__init__(dir_, Panel)
        self.raiz, self.privado, self.dev = raiz, privado, dev
        self.archivo = raiz / "data" / "catalogo.json"
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        (raiz / "media").mkdir(exist_ok=True)
        if not self.archivo.exists():
            shutil.copyfile(APP / "catalogo-inicial.json", self.archivo)
        self.candado = threading.Lock()       # un guardado a la vez (control de versiones)
        self.mcandado = threading.Lock()      # métricas
        self.f_metricas = privado.carpeta / "metricas.json"
        self.f_actividad = privado.carpeta / "actividad.jsonl"
        self.met = leer_json_archivo(self.f_metricas, {})
        self.ids = set()
        self.paginas()

    def paginas(self):
        try:
            cat = self.leer_catalogo()
            self.ids = {p["id"] for p in cat.get("productos", [])}
            n = generar_paginas(self.raiz, cat)
            print(f"Páginas de producto generadas: {n}", flush=True)
        except Exception as e:  # una página rota no debe impedir guardar el catálogo
            print(f"ERROR generando páginas: {e!r}", file=sys.stderr, flush=True)

    def leer_catalogo(self):
        c = json.loads(self.archivo.read_text("utf-8"))
        c.setdefault("rev", 0)
        return c

    def guardar_catalogo(self, c, rev_cliente=None, ip="", accion=None):
        """Guarda una versión nueva. Si rev_cliente no coincide con la versión actual, otro
        dispositivo guardó antes: se rechaza (Conflicto) para no pisar sus cambios."""
        with self.candado:
            actual = self.leer_catalogo()
            if rev_cliente is not None and int(rev_cliente) != actual["rev"]: raise Conflicto()
            c = dict(c, rev=actual["rev"] + 1)
            resp = self.privado.carpeta / "respaldos"
            resp.mkdir(exist_ok=True)
            shutil.copyfile(self.archivo, resp / f"catalogo-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
            for viejo in sorted(resp.glob("catalogo-*.json"))[:-RESPALDOS]: viejo.unlink()
            tmp = self.archivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self.archivo)
            self.registrar(accion or "Guardó", resumen_cambios(actual, c), ip)
        self.paginas()
        return c

    # --- historial y actividad
    def historial(self):
        out = []
        for f in sorted((self.privado.carpeta / "respaldos").glob("catalogo-*.json"), reverse=True):
            r = re.match(r"catalogo-(\d{8})-(\d{6})", f.name)
            c = leer_json_archivo(f, {})
            out.append({"nombre": f.name, "fecha": datetime.strptime(r.group(1) + r.group(2), "%Y%m%d%H%M%S").isoformat() if r else "",
                        "productos": len(c.get("productos", [])), "visibles": sum(1 for p in c.get("productos", []) if p.get("visible", True))})
        return out

    def registrar(self, accion, cambios, ip=""):
        linea = json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "accion": accion, "cambios": cambios[:30], "ip": ip}, ensure_ascii=False)
        with self.f_actividad.open("a", encoding="utf-8") as f: f.write(linea + "\n")
        if self.f_actividad.stat().st_size > 2 * 2**20:   # recorta el registro cuando crece mucho
            lineas = self.f_actividad.read_text("utf-8").splitlines()[-ACTIVIDAD_MAX:]
            self.f_actividad.write_text("\n".join(lineas) + "\n", "utf-8")

    def actividad(self, n=150):
        try: lineas = self.f_actividad.read_text("utf-8").splitlines()[-n:]
        except FileNotFoundError: return []
        return [json.loads(x) for x in reversed(lineas) if x.strip()]

    # --- métricas propias (sin cookies ni IP)
    def contar(self, t, pid):
        hoy = datetime.now().strftime("%Y-%m-%d")
        with self.mcandado:
            d = self.met.setdefault(hoy, {})
            k = d.setdefault(t, {})
            k["_"] = k.get("_", 0) + 1
            if pid: k[pid] = k.get(pid, 0) + 1
            if len(self.met) > METRICAS_DIAS:
                for viejo in sorted(self.met)[:-METRICAS_DIAS]: del self.met[viejo]
            escribir_json(self.f_metricas, self.met)

    def metricas(self, dias):
        from datetime import timedelta
        hoy = datetime.now().date()
        serie, por = [], {}
        with self.mcandado:
            for i in range(dias - 1, -1, -1):
                f = (hoy - timedelta(days=i)).isoformat()
                d = self.met.get(f, {})
                serie.append({"fecha": f, **{t: d.get(t, {}).get("_", 0) for t in EVENTOS}})
                for t in ("vista", "interes", "whatsapp"):
                    for pid, n in d.get(t, {}).items():
                        if pid != "_": por.setdefault(pid, {"vista": 0, "interes": 0, "whatsapp": 0})[t] += n
        media = self.raiz / "media"
        archivos = [f for f in media.rglob("*") if f.is_file()] if media.exists() else []
        return {"serie": serie, "productos": por, "almacenamiento": {"archivos": len(archivos), "bytes": sum(f.stat().st_size for f in archivos)}}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--raiz", default=r"C:\sitios\nudodesen", help="carpeta de la web pública")
    a.add_argument("--privado", default=r"C:\sitios\nudodesen-panel\privado", help="contraseña, sesiones y respaldos")
    a.add_argument("--puerto", type=int, default=8089)
    a.add_argument("--clave", help="cambia la contraseña del panel y sale")
    a.add_argument("--dev-sin-clave", action="store_true", help="SOLO PRUEBAS LOCALES: entra sin contraseña")
    a.add_argument("--generar", metavar="CATALOGO", help="solo genera las páginas de producto en --raiz a partir de ese .json y sale")
    o = a.parse_args()
    if o.generar:
        n = generar_paginas(Path(o.raiz).resolve(), json.loads(Path(o.generar).read_text("utf-8")))
        print(f"Páginas de producto generadas: {n}")
        return
    privado = Privado(Path(o.privado))
    if o.clave is not None:
        if len(o.clave) < 8: sys.exit("La contraseña debe tener al menos 8 caracteres.")
        privado.poner_clave(o.clave)
        print("Contraseña actualizada. Se cerraron las sesiones abiertas.")
        return
    if "clave" not in privado.cfg and not o.dev_sin_clave:
        sys.exit("Falta la contraseña: ejecuta con --clave \"...\" primero.")
    s = Servidor(("127.0.0.1", o.puerto), Path(o.raiz).resolve(), privado, o.dev_sin_clave)
    print(f"Panel en http://127.0.0.1:{o.puerto}{PREFIJO}/  (web: {s.raiz})", flush=True)
    s.serve_forever()


if __name__ == "__main__":
    main()
