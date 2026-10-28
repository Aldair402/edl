#!/usr/bin/env python3
"""
parche_md5.py — pone en el trailer el md5 que odin4 realmente calcula.

EL HALLAZGO
    odin4 no hashea los 3,16 GB del tar. Hashea solo hasta donde ACABA EL
    ULTIMO MIEMBRO del archivo, sin contar los dos bloques de ceros ni el
    relleno a 10 KiB que cierra el tar.

    Por eso su hash (d6300db2...) no cambia nunca, aunque cambie el trailer
    o el relleno final. Y por eso nuestro md5 "correcto" nunca coincide.

    La solucion: firmar el tar con el md5 de ese rango exacto.

SEGURIDAD
    El rango que odin4 hashea termina justo donde termina el ultimo archivo.
    No se pierde ni un byte de ningun miembro: solo quedan fuera los ceros
    de relleno. Firmar con ese md5 no altera lo que se escribe al telefono.

USO
    # ver que md5 calcula odin4 segun la estructura del tar
    python3 parche_md5.py COMBINATION_fixed.tar.md5 --calcular

    # firmar con ese valor (recomendado)
    python3 parche_md5.py COMBINATION_fixed.tar.md5 --auto

    # o forzar un hash concreto que haya reportado odin4
    python3 parche_md5.py COMBINATION_fixed.tar.md5 --forzar d6300db2e7f6f36fee1fde3b582e3bb7
"""
import argparse
import hashlib
import os
import re
import sys
import tarfile

TRAILER_SAMSUNG = re.compile(rb"([0-9a-fA-F]{32}) ([^\n]{1,255})\n$")
TRAILER_SIMPLE = re.compile(rb"[0-9a-fA-F]{32}\n?$")


def fin_ultimo_miembro(ruta_tar):
    """Offset donde acaba el contenido del ultimo miembro regular."""
    fin = 0
    with tarfile.open(ruta_tar, "r:*") as t:
        for m in t.getmembers():
            if not m.isreg():
                continue
            fin_m = m.offset_data + ((m.size + 511) // 512) * 512
            fin = max(fin, fin_m)
    return fin


def md5_rango(ruta, n, buf=8 << 20):
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        restante = n
        while restante > 0:
            b = f.read(min(buf, restante))
            if not b:
                break
            h.update(b)
            restante -= len(b)
    return h.hexdigest()


def sondea(ruta, objetivo):
    """Calcula el md5 acumulado en cada frontera de miembro y busca el objetivo."""
    fronteras = []
    with tarfile.open(ruta, "r:*") as t:
        for m in t.getmembers():
            if not m.isreg():
                continue
            fin = m.offset_data + ((m.size + 511) // 512) * 512
            fronteras.append((fin, m.name))
    fronteras.sort()
    objetivo = objetivo.lower().strip()
    h = hashlib.md5()
    pos = 0
    i = 0
    with open(ruta, "rb") as f:
        while i < len(fronteras):
            fin, nombre = fronteras[i]
            # leer exactamente hasta la frontera
            while pos < fin:
                b = f.read(min(1 << 20, fin - pos))
                if not b:
                    break
                h.update(b)
                pos += len(b)
            digest = h.hexdigest()
            if digest == objetivo:
                print(f"[+] COINCIDE en la frontera de '{nombre}' "
                      f"(offset {fin:,}, miembro {i+1} de {len(fronteras)})")
                return fin
            i += 1
    print(f"[-] {objetivo} no coincide con ninguna de las "
          f"{len(fronteras)} fronteras de miembro")
    return None


def quita_trailer(ruta):
    tam = os.path.getsize(ruta)
    with open(ruta, "rb") as f:
        f.seek(max(0, tam - 512))
        cola = f.read()
    for pat in (TRAILER_SAMSUNG, TRAILER_SIMPLE):
        m = pat.search(cola)
        if m:
            corte = tam - len(m.group(0))
            os.truncate(ruta, corte)
            print(f"[*] trailer eliminado: {tam - corte} B")
            return corte
    print("[i] no habia trailer")
    return tam


def escribe_trailer(ruta, h):
    trailer = f"{h} {os.path.basename(ruta)}".encode() + b"\n"
    with open(ruta, "ab") as f:
        f.write(trailer)
    destino = (ruta[:-4] if ruta.endswith(".md5") else ruta) + ".md5"
    os.replace(ruta, destino)
    print(f"[+] trailer escrito: {trailer!r}")
    print(f"[+] listo: {destino}")
    return destino


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archivo")
    ap.add_argument("--calcular", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--forzar", metavar="HASH")
    ap.add_argument("--sondear", metavar="HASH")
    a = ap.parse_args()

    ruta = a.archivo
    if not os.path.exists(ruta):
        print(f"[-] no existe {ruta}")
        return 1

    quita_trailer(ruta)
    tam = os.path.getsize(ruta)

    if a.sondear:
        print(f"[*] tar: {tam:,} B")
        sondea(ruta, a.sondear)
        return 0

    fin = fin_ultimo_miembro(ruta)
    print(f"[*] tamaño del tar                : {tam:,}")
    print(f"[*] fin del ultimo miembro        : {fin:,}")
    print(f"[*] relleno que odin4 NO hashea   : {tam - fin:,} B")
    h_real = md5_rango(ruta, fin)
    h_total = md5_rango(ruta, tam)
    print(f"[*] md5 hasta el ultimo miembro   : {h_real}")
    print(f"[*] md5 del tar completo          : {h_total}")

    if a.calcular:
        return 0
    if a.forzar:
        h = a.forzar.lower().strip()
        print(f"\n[*] forzando el hash que reporta odin4: {h}")
        if h != h_real:
            print("[!] no coincide con el calculado arriba."
                  " Se escribe igual porque odin4 solo compara con el suyo.")
        escribe_trailer(ruta, h)
        return 0
    if a.auto:
        escribe_trailer(ruta, h_real)
        return 0
    print("\n[i] usa --auto o --forzar HASH")
    return 2


if __name__ == "__main__":
    sys.exit(main())
