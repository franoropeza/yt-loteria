"""multipart/form-data a mano (para Telegram y Facebook), para no sumar dependencias."""
import mimetypes
import uuid


def multipart(campos, archivos):
    """campos: {nombre: valor}; archivos: {nombre_campo: Path}. Devuelve (datos, Content-Type)."""
    borde = uuid.uuid4().hex
    partes = [f'--{borde}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
              for k, v in campos.items()]
    for nombre_campo, ruta in archivos.items():
        tipo = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        partes.append(f'--{borde}\r\nContent-Disposition: form-data; name="{nombre_campo}"; '
                      f'filename="{ruta.name}"\r\nContent-Type: {tipo}\r\n\r\n'.encode()
                      + ruta.read_bytes() + b"\r\n")
    partes.append(f"--{borde}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={borde}"
