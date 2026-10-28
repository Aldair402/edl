#!/usr/bin/env python3
"""
quita_pit.py — copia un tar quitando SOLO las entradas *.pit de la raiz.

POR QUE
    El Odin oficial llama siempre a DownloadEngine::sendPitInfo(), pero esa
    funcion hace `ret` inmediato si el paquete no trae PIT (pit_size == 0):

        79cd9:  cmpq $0x0, 0x110(%rdx)
        79ce1:  jne  79ce8
        79ce3:  ret              ; sin PIT -> se salta el envio

    Cuando el tar SI trae PIT, se lo manda al telefono y el telefono se cae
    del bus (libusb -5 = NOT_FOUND) -> "Fail send PIT info".

    El telefono ya tiene su PIT. No hay que enviarle ninguno.

QUE HACE
    Recorre las cabeceras del tar y copia todo byte a byte, excepto los
    bloques de las entradas <nombre>.pit que esten en la raiz.
    Preserva meta-data/*.mf y el orden original de los demas archivos.

USO
    python3 quita_pit.py --listar COMB.tar
    python3 quita_pit.py COMB.tar COMB_sinpit.tar

DESPUES
    python3 md5_odin4_fix.py COMB_sinpit.tar
    sudo odin4 -b COMB_sinpit.tar.md5
"""
import os
import shutil
import sys

CHUNK = 8 << 20
BLOQUE = 512


def tam_octal(campo):
    """Campo de tamano del header USTAR (octal, o base-256 si va marcado)."""
    if campo[:1] and campo[0] & 0x80:
        v = 0
        for b in campo[1:]:
            v = (v << 8) | b
        return v
    limpio = campo.replace(b"\0", b" ").strip()
    if not limpio:
        return 0
    try:
        return int(limpio, 8)
    except ValueError:
        return 0


def recorre(fi, aviso):
    """Genera (nombre, tipo, offset, bytes_totales) de cada entrada."""
    off = 0
    while True:
        h = fi.read(BLOQUE)
        if len(h) < BLOQUE or h == b"\0" * BLOQUE:
            return
        nombre = h[0:100].split(b"\0")[0].decode("utf-8", "replace")
        tipo = h[156:157].decode("latin-1")
        tam = tam_octal(h[124:136])
        total = BLOQUE + ((tam + BLOQUE - 1) // BLOQUE) * BLOQUE
        yield nombre, tipo, off, total
        resto = total - BLOQUE
        while resto > 0:
            salto = min(resto, CHUNK)
            fi.seek(salto, 1)
            resto -= salto
        off += total


def es_pit_raiz(nombre):
    n = nombre.lstrip("./")
    return n.lower().endswith(".pit") and "/" not in n and "\\" not in n


def listar(ruta):
    total = os.path.getsize(ruta)
    pits = []
    n = 0
    with open(ruta, "rb") as fi:
        for nombre, tipo, off, tam in recorre(fi, False):
            n += 1
            if es_pit_raiz(nombre):
                pits.append((nombre, off, tam))
            if nombre.startswith("meta-data") or es_pit_raiz(nombre):
                print(f"    {tipo}  {nombre:<40} {tam:>12,} B  @{off:,}")
    print(f"\n[*] {ruta}: {total:,} B, {n} entradas")
    if pits:
        print(f"[!] {len(pits)} PIT en la raiz (se quitarian):")
        for nombre, off, tam in pits:
            print(f"      {nombre}  {tam:,} B")
    else:
        print("[+] no hay PIT en la raiz")
    return 0


def copia(origen, destino):
    quitados = []
    copiados = 0
    with open(origen, "rb") as fi, open(destino, "wb") as fo:
        while True:
            h = fi.read(BLOQUE)
            if len(h) < BLOQUE or h == b"\0" * BLOQUE:
                fo.write(h)
                shutil.copyfileobj(fi, fo, CHUNK)
                break
            nombre = h[0:100].split(b"\0")[0].decode("utf-8", "replace")
            tipo = h[156:157].decode("latin-1")
            tam = tam_octal(h[124:136])
            total = BLOQUE + ((tam + BLOQUE - 1) // BLOQUE) * BLOQUE
            resto = total - BLOQUE
            if es_pit_raiz(nombre):
                while resto > 0:
                    salto = min(resto, CHUNK)
                    fi.seek(salto, 1)
                    resto -= salto
                quitados.append((nombre, tam))
                print(f"  [-] eliminado  {nombre}  ({tam:,} B)")
                continue
            fo.write(h)
            copiados += 1
            while resto > 0:
                b = fi.read(min(resto, CHUNK))
                if not b:
                    resto = 0
                    break
                fo.write(b)
                resto -= len(b)
    return quitados, copiados


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    if "--listar" in sys.argv:
        return listar(args[0])
    if len(args) < 2:
        print(__doc__)
        return 2
    origen, destino = args[0], args[1]
    if os.path.abspath(origen) == os.path.abspath(destino):
        print("[-] origen y destino son el mismo archivo")
        return 1
    print(f"[*] {origen}")
    quitados, copiados = copia(origen, destino)
    if not quitados:
        os.unlink(destino)
        print("[-] no habia PIT que quitar; no se escribio nada")
        return 1
    a, b = os.path.getsize(origen), os.path.getsize(destino)
    print(f"\n[+] {destino}")
    print(f"    {copiados} entradas copiadas, {len(quitados)} PIT eliminados")
    print(f"    {a:,} B -> {b:,} B  (delta {a - b:,})")
    print("\n[>] ahora:")
    print(f"    python3 md5_odin4_fix.py {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
