"""Lee el cronograma mensual de sorteos (PDF de loteriadesalta.com) y lo guarda como CSV.

Uso:
  python cronograma.py                 # descarga el PDF publicado y genera cronograma.csv
  python cronograma.py archivo.pdf     # usa un PDF local
"""
import csv
import datetime as dt
import re
import sys
import tempfile
import urllib.request
from pathlib import Path


URL = "https://www.loteriadesalta.com/puntosdeventa/cronograma.pdf"
CSV = Path(__file__).resolve().parent / "cronograma.csv"
ULTIMA_CONSULTA = Path(__file__).resolve().parent / "estado" / "cronograma_ultima_consulta.txt"
DIA_ANTICIPO = 25  # desde este día se busca el cronograma del mes siguiente (se publica a fin de mes)
NOMBRES = {"12:00": "MATUTINA", "15:00": "VESPERTINA", "18:00": "EXTRA", "21:00": "NOCTURNA", "22:30": "LA REVANCHA"}

FILA = re.compile(r"(?:(\d{2}/\d{2}/\d{4}) )?(\d{5}) (\d{5}) (\d{2}:\d{2}) hs\. (?:(\d+) \d{2}:\d{2} hs\.|-----)")
FECHA_SOLA = re.compile(r"^(\d{2}/\d{2}/\d{4}) \d{2}/\d{2}/\d{4}$")  # fecha + caducidad en su propia linea


def leer_pdf(ruta):
    """Devuelve una lista de dicts: fecha, hora, nombre, sorteo, changuita, tombola_total."""
    import pdfplumber  # solo al leer el PDF: la etapa de detección no lo instala

    filas, fechas = [], []
    with pdfplumber.open(ruta) as pdf:
        for pagina in pdf.pages:
            for linea in pagina.extract_text().splitlines():
                if m := FILA.search(linea):
                    if m[1]:
                        fechas.append(m[1])
                    filas.append({"hora": m[4], "sorteo": m[2], "changuita": m[3], "tombola_total": m[5] or ""})
                elif m := FECHA_SOLA.match(linea.strip()):
                    fechas.append(m[1])
    # la fecha aparece una sola vez por dia, en medio de su grupo: cada dia arranca con el sorteo de las 12:00
    dias = []
    for f in filas:
        if f["hora"] == "12:00" or not dias:
            dias.append([])
        dias[-1].append(f)
    if len(dias) != len(fechas):
        raise ValueError(f"El cronograma no se pudo leer bien: {len(dias)} días y {len(fechas)} fechas")
    out = []
    for fecha, sorteos in zip(fechas, dias):
        for s in sorteos:
            out.append({"fecha": fecha, "hora": s["hora"], "nombre": NOMBRES.get(s["hora"], ""), **{
                k: s[k] for k in ("sorteo", "changuita", "tombola_total")}})
    return out


def actualizar(pdf=None):
    if pdf is None:
        pdf = Path(tempfile.mkdtemp()) / "cronograma.pdf"
        urllib.request.urlretrieve(URL, pdf)
    filas = leer_pdf(pdf)
    existentes = {r["sorteo"]: r for r in cargar()} if CSV.exists() else {}
    existentes.update({r["sorteo"]: r for r in filas})  # se acumulan los meses
    with CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(sorted(existentes.values(), key=lambda r: int(r["sorteo"])))
    return filas


def cargar():
    with CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def buscar(sorteo=None, fecha=None, hora=None):
    """Busca un sorteo por número o, si no, por fecha + hora (HH:MM)."""
    if not CSV.exists():
        return None
    filas = cargar()
    if sorteo:
        r = next((r for r in filas if r["sorteo"] == sorteo), None)
        if r:
            return r
    if fecha and hora:
        return next((r for r in filas if r["fecha"] == fecha and r["hora"] == hora[:5]), None)
    return None


def _tiene_mes(fecha):
    mes = fecha.strftime("/%m/%Y")
    return CSV.exists() and any(r["fecha"].endswith(mes) for r in cargar())


def asegurar(hoy):
    """Descarga el PDF solo cuando hace falta: si falta el mes de `hoy`, o desde el día 25 una vez por día
    hasta que aparezca el mes siguiente. Devuelve una lista de avisos."""
    avisos = []
    siguiente = (hoy.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    falta_actual = not _tiene_mes(hoy)
    anticipar = hoy.day >= DIA_ANTICIPO and not _tiene_mes(siguiente)
    ya_consultado = ULTIMA_CONSULTA.exists() and ULTIMA_CONSULTA.read_text().strip() == hoy.isoformat()
    if falta_actual or (anticipar and not ya_consultado):
        try:
            actualizar()
        except Exception as e:  # sitio caído, PDF con otro formato, etc.
            avisos.append(f"No se pudo actualizar el cronograma: {e}")
        ULTIMA_CONSULTA.parent.mkdir(parents=True, exist_ok=True)
        ULTIMA_CONSULTA.write_text(hoy.isoformat())
    if not _tiene_mes(hoy):
        avisos.append(f"El cronograma publicado no incluye {hoy.strftime('%m/%Y')}")
    return avisos


def por_horario(inicio, tolerancia_min=45):
    """Sorteo cuyo horario está más cerca de `inicio` (datetime con zona horaria de Argentina)."""
    if not CSV.exists():
        return None
    mejor, menor = None, None
    for r in cargar():
        if r["fecha"] != inicio.strftime("%d/%m/%Y"):
            continue
        hora = dt.datetime.strptime(f"{r['fecha']} {r['hora']}", "%d/%m/%Y %H:%M").replace(tzinfo=inicio.tzinfo)
        dif = abs((hora - inicio).total_seconds()) / 60
        if dif <= tolerancia_min and (menor is None or dif < menor):
            mejor, menor = r, dif
    return mejor


if __name__ == "__main__":
    filas = actualizar(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"{len(filas)} sorteos ({filas[0]['fecha']} al {filas[-1]['fecha']}) -> {CSV}")
