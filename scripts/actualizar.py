#!/usr/bin/env python3
"""Monitor de Salud Monclova: descarga feeds RSS de Google News y genera datos.json.
Solo usa la biblioteca estándar (sin pip install)."""
import hashlib, html, json, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------- CONFIGURACIÓN (modifica aquí) ----------
TZ = timezone(timedelta(hours=-6))      # Monclova: UTC-6 todo el año
DIAS_MAX = 30                            # antigüedad máxima de las noticias
LIMITE = 200                             # máximo de noticias guardadas
TIMEOUT = 15                             # segundos por solicitud
UA = "MonitorSaludMonclova/1.0 (uso personal; lector RSS)"
SALIDA = Path(__file__).resolve().parent.parent / "datos.json"
URL = "https://news.google.com/rss/search?q={q}&hl=es-419&gl=MX&ceid=MX:es-419"

# Grupo A: medicamentos y desabasto. Nota: el INSABI se extinguió en 2023 y sus
# funciones pasaron a IMSS-Bienestar, por eso se consulta este último término.
CONSULTAS_A = [
    "desabasto medicamentos México", "COFEPRIS alerta sanitaria",
    "COFEPRIS retiro de medicamento", "desabasto medicamentos Coahuila",
    "desabasto insumos médicos México", "IMSS desabasto medicamentos",
    "IMSS-Bienestar medicamentos", "IMSS-Bienestar desabasto",
]
# Grupo B: salud general en Monclova.
CONSULTAS_B = [
    "Monclova salud", "Monclova hospital", "Monclova farmacia",
    "Monclova clínica", "Monclova médicos", "Monclova dengue",
    "Monclova enfermedades", "Monclova Coahuila salud",
]
# Palabras clave por categoría (en minúsculas). Agrega las que necesites.
CLAVES = {
    "desabasto": ["desabasto", "escasez", "falta de medicamento", "faltan medicamento",
                  "sin medicamento", "falta de insumos", "abasto", "carencia de medicamento"],
    "alerta sanitaria": ["cofepris", "retiro", "alerta", "falsific", "adulterad",
                         "riesgo sanitario", "brote", "contingencia sanitaria"],
    "salud general": ["salud", "hospital", "clínica", "clinica", "médico", "medico",
                      "dengue", "enfermedad", "farmacia", "imss", "issste", "vacuna",
                      "paciente", "epidemia", "sarampión", "covid"],
}
# ---------------------------------------------------

def descargar(consulta):
    """Descarga un feed; si falla, avisa en consola y devuelve None."""
    url = URL.format(q=urllib.parse.quote_plus(consulta))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as e:
        print(f"[ERROR] '{consulta}': {e}", file=sys.stderr)
        return None

def limpiar(texto):
    """Quita etiquetas HTML y entidades."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()

def leer_feed(datos, grupo):
    """Convierte el XML en una lista de noticias (dicts)."""
    salida = []
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as e:
        print(f"[ERROR] XML inválido: {e}", file=sys.stderr)
        return salida
    for it in raiz.iter("item"):
        try:
            titulo = limpiar(it.findtext("title"))
            enlace = (it.findtext("link") or "").strip()
            fecha = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TZ)
            fuente = limpiar(it.findtext("source"))
            # Google News pone "Título - Fuente": se separan.
            if " - " in titulo:
                t, f = titulo.rsplit(" - ", 1)
                titulo, fuente = t.strip(), (fuente or f.strip())
            if not (titulo and enlace):
                continue
            salida.append({"titulo": titulo, "fuente": fuente or "Fuente desconocida",
                           "fecha_dt": fecha, "enlace": enlace, "grupo": grupo,
                           "desc": limpiar(it.findtext("description"))})
        except Exception:
            continue  # un item defectuoso no detiene el resto
    return salida

def normalizar(t):
    return re.sub(r"[^a-z0-9áéíóúñ ]", "", t.lower()).strip()

def duplicada(n, vistas_enlace, vistas_titulo):
    """Duplicado por enlace exacto o por título muy parecido (>= 90 %)."""
    if n["enlace"] in vistas_enlace:
        return True
    nt = normalizar(n["titulo"])
    return any(SequenceMatcher(None, nt, v).ratio() >= 0.9 for v in vistas_titulo)

def categorizar(n):
    texto = (n["titulo"] + " " + n["desc"]).lower()
    cats = [c for c, palabras in CLAVES.items() if any(p in texto for p in palabras)]
    if "monclova" in texto:
        cats.append("Monclova")
    if n["grupo"] == "B" and "salud general" not in cats:
        cats.append("salud general")  # todo lo del grupo B es de salud
    return sorted(set(cats))

def main():
    limite_fecha = datetime.now(TZ) - timedelta(days=DIAS_MAX)
    todas = []
    for grupo, consultas in (("A", CONSULTAS_A), ("B", CONSULTAS_B)):
        for q in consultas:
            datos = descargar(q)
            if datos:
                nuevas = leer_feed(datos, grupo)
                print(f"[OK] '{q}': {len(nuevas)} noticias")
                todas += nuevas
    # Orden por fecha y deduplicado (la más reciente se conserva)
    todas.sort(key=lambda n: n["fecha_dt"], reverse=True)
    finales, enlaces, titulos = [], set(), []
    for n in todas:
        if n["fecha_dt"] < limite_fecha or duplicada(n, enlaces, titulos):
            continue
        enlaces.add(n["enlace"]); titulos.append(normalizar(n["titulo"]))
        finales.append({
            "id": hashlib.sha1(n["enlace"].encode()).hexdigest()[:12],
            "titulo": n["titulo"], "fuente": n["fuente"],
            "fecha": n["fecha_dt"].isoformat(timespec="minutes"),
            "enlace": n["enlace"], "grupo": n["grupo"], "categorias": categorizar(n)})
        if len(finales) >= LIMITE:
            break
    if not finales:
        print("[AVISO] Sin noticias; se conserva el datos.json anterior.", file=sys.stderr)
        return
    salida = {"ultima_actualizacion": datetime.now(TZ).isoformat(timespec="minutes"),
              "noticias": finales}
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Guardadas {len(finales)} noticias en {SALIDA}")

if __name__ == "__main__":
    main()
