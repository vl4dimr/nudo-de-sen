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
import argparse, base64, hashlib, hmac, json, mimetypes, os, re, secrets, shutil, struct, sys, time, unicodedata, zipfile
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
        out["productos"].append({
            "id": pid, "nombre": texto(x.get("nombre"), 80, True), "categoria": cat, "precio": precio,
            "detalle": texto(x.get("detalle"), 400), "visible": bool(x.get("visible", True)), "medios": medios})
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


def precio_txt(p):
    return f"Desde S/ {p['precio']:,}".replace(",", " ") if p.get("precio") is not None else "A cotizar"


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
        desc = (p.get("detalle") or "") + " Amigurumi hecho a mano en Puno, Perú. Envíos a todo el país."
        ld = {"@context": "https://schema.org", "@type": "Product", "name": p["nombre"], "description": desc.strip(),
              "image": [f"{SITIO}/{m['src']}" for m in medios if m["tipo"] == "imagen"] or [f"{SITIO}/{img}"],
              "category": p["categoria"], "brand": {"@type": "Brand", "name": "Nudo de SEN"}, "url": url}
        if p.get("precio") is not None:
            ld["offers"] = {"@type": "Offer", "priceCurrency": "PEN", "price": p["precio"], "availability": "https://schema.org/MadeToOrder", "url": url}
        valores = {
            "NOMBRE": h(p["nombre"]), "DESCRIPCION": h(desc.strip()), "PRECIO_TXT": h(precio_txt(p)), "URL": h(url),
            "IMAGEN_ABS": h(f"{SITIO}/{img}"), "ID": h(p["id"]), "CATEGORIA": h(p["categoria"]), "CATEGORIA_URL": h(quote(p["categoria"])),
            "DETALLE": h(p.get("detalle") or ""), "PRIMER_MEDIO": primer, "MINIATURAS": mini, "RELACIONADOS": rel,
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


# ---------------------------------------------------------------- servidor
class Panel(BaseHTTPRequestHandler):
    server_version = "NudoPanel/1"
    sys_version = ""
    intentos: dict = {}

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
            html = (APP / "index.html").read_bytes()
            csp = ("default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
                   "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                   "script-src 'self' 'unsafe-inline'; connect-src 'self' blob: data:; worker-src 'self' blob:; "
                   + ("" if self.server.dev else "frame-ancestors 'none'; ") + "base-uri 'none'; form-action 'self'")
            return self.enviar(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": csp, "Cache-Control": "no-cache"})
        if p == "/api/sesion":
            return self.enviar(200, {"ok": self.sesion()})
        if p == "/api/catalogo":
            if not self.sesion(): return self.error(401, "Inicia sesión")
            return self.enviar(200, self.server.leer_catalogo(), extra={"Cache-Control": "no-store"})
        if p.startswith("/media/") or p.startswith("/assets/"):
            return self.estatico(self.server.raiz, p.lstrip("/"), "public, max-age=3600")
        self.error(404, "No encontrado")

    def do_POST(self):
        u = urlsplit(self.path)
        p = self.camino()
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
        if p == "/api/subir":
            if not self.sesion(): return self.error(401, "Inicia sesión")
            return self.subir(parse_qs(u.query))
        self.error(404, "No encontrado")

    def do_PUT(self):
        if not self.mutacion_ok(): return self.error(403, "Petición no permitida")
        if self.camino() != "/api/catalogo": return self.error(404, "No encontrado")
        if not self.sesion(): return self.error(401, "Inicia sesión")
        try:
            limpio = validar(self.leer_json())
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
            return self.error(400, str(e) or "Datos no válidos")
        self.server.guardar_catalogo(limpio)
        self.enviar(200, {"ok": True, "catalogo": limpio})

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
            if not medios: return self.error(400, "El ZIP no trae fotos ni modelos 3D que se puedan usar")
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
        texto = hpath.read_text("utf-8", errors="ignore")
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
                tipo = "modelo" if ext == ".glb" else "imagen" if ext in EXT["imagen"] else None
                if not tipo or i.file_size > LIMITES[tipo]:
                    omitidos.append(i.filename); continue
                final = carpeta / f"{slug(Path(i.filename).stem)}-{secrets.token_hex(3)}{'.jpg' if ext == '.jpeg' else ext}"
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
        self.paginas()

    def paginas(self):
        try:
            n = generar_paginas(self.raiz, self.leer_catalogo())
            print(f"Páginas de producto generadas: {n}", flush=True)
        except Exception as e:  # una página rota no debe impedir guardar el catálogo
            print(f"ERROR generando páginas: {e!r}", file=sys.stderr, flush=True)

    def leer_catalogo(self):
        return json.loads(self.archivo.read_text("utf-8"))

    def guardar_catalogo(self, c):
        resp = self.privado.carpeta / "respaldos"
        resp.mkdir(exist_ok=True)
        if self.archivo.exists():
            shutil.copyfile(self.archivo, resp / f"catalogo-{datetime.now():%Y%m%d-%H%M%S}.json")
            for viejo in sorted(resp.glob("catalogo-*.json"))[:-RESPALDOS]: viejo.unlink()
        tmp = self.archivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, self.archivo)
        self.paginas()


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
