#!/usr/bin/env python3
"""md5_odin4_fix.py — firma tars para el Odin4 de Samsung (1.2.1-dc05e3ea).

EL BUG (desensamblado y verificado)
    FirmwareData::verifyMD5:
        hash_len  = tamaño_archivo - longitud_basename - 31
        calculado = md5( archivo[0:hash_len] )
        esperado  = archivo[hash_len : hash_len+32]

MODO 1 (defecto): trailer de L+31 bytes (md5 + relleno)
MODO 2 (--relleno): sin trailer; el md5 va dentro de los ceros de relleno

Escribe SIEMPRE a un temporal y renombra: nunca trunca el original.
Es idempotente: se puede ejecutar varias veces sobre el mismo archivo.
"""
import argparse
import hashlib
import mmap
import os
import re
import shutil
import subprocess
import sys
import tempfile

TRAILER = re.compile(rb"([0-9a-fA-F]{32}) ([^\n]{1,255})\n$")
TRAILER_SOLO = re.compile(rb"[0-9a-fA-F]{32}\n?$")
HEX = b"0123456789abcdefABCDEF"
CHUNK = 8 << 20

def mapea(ruta):
    f = open(ruta, "rb")
    if os.path.getsize(ruta) == 0:
        f.close()
        raise SystemExit("[-] el archivo esta vacio (0 B). Regenera los tars.")
    return f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

def fin_contenido(mm, L=0):
    """Quita el trailer md5 si lo hubiera (tambien el que escribe este script)."""
    S = len(mm)
    cola = bytes(mm[max(0, S - 400):])
    for pat in (TRAILER, TRAILER_SOLO):
        m = pat.search(cola)
        if m:
            return S - len(m.group(0))
    if L:
        T = L + 31
        if S > T:
            fin = S - T
            cabeza = bytes(mm[fin:fin + 32])
            resto = bytes(mm[fin + 32:S])
            if (len(cabeza) == 32
                    and all(c in HEX for c in cabeza)
                    and resto == b" " * len(resto)):
                return fin
    return S

def volcar(destino, mm, fin, cola_fn):
    """Escribe a un temporal y reemplaza. Nunca trunca el archivo de origen."""
    d = os.path.dirname(os.path.abspath(destino)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".odin4fix_")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            for i in range(0, fin, CHUNK):
                f.write(mm[i:min(i + CHUNK, fin)])
            cola_fn(f)
        os.replace(tmp, destino)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archivo")
    ap.add_argument("--relleno", action="store_true")
    ap.add_argument("--verificar", metavar="ODIN4", default=None)
    a = ap.parse_args()

    destino = a.archivo if a.archivo.endswith(".md5") else a.archivo + ".md5"
    nombre = os.path.basename(destino)
    L = len(nombre)
    f, mm = mapea(a.archivo)
    try:
        fin = fin_contenido(mm, L)
        if a.relleno:
            S = ((fin + L + 31 + 511) // 512) * 512
            corte = S - L - 31
            h = hashlib.md5()
            for i in range(0, fin, CHUNK):
                h.update(mm[i:min(i + CHUNK, fin)])
            h.update(b"\x00" * (corte - fin))
            md5 = h.hexdigest()
            def cola(fh, _c=corte, _m=md5, _S=S):
                fh.write(b"\x00" * (_c - fin))
                fh.write(_m.encode())
                fh.write(b"\x00" * (_S - _c - 32))
            volcar(destino, mm, fin, cola)
            info = dict(modo="relleno", L=L, hash_len=corte, md5=md5, tam=S)
        else:
            T = L + 31
            if T < 32:
                raise SystemExit(f"[-] basename demasiado corto (L={L})")
            h = hashlib.md5()
            for i in range(0, fin, CHUNK):
                h.update(mm[i:min(i + CHUNK, fin)])
            md5 = h.hexdigest()
            volcar(destino, mm, fin,
                   lambda fh: fh.write(md5.encode() + b" " * (T - 32)))
            info = dict(modo="trailer", L=L, hash_len=fin, md5=md5, tam=fin + T)
    finally:
        mm.close()
        f.close()

    print(f"[*] entrada  : {a.archivo}")
    print(f"[*] modo     : {info['modo']}")
    print(f"[*] basename : {info['L']} chars")
    print(f"[*] hash_len : {info['hash_len']:,} B")
    print(f"[*] md5      : {info['md5']}")
    print(f"[+] escrito  : {destino} ({info['tam']:,} B)")

    if a.verificar:
        exe = a.verificar
        if not os.path.exists(exe) and not shutil.which(exe):
            print(f"[!] no encuentro {exe}")
            return 0
        r = subprocess.run([exe, "-b", destino], capture_output=True, text=True)
        txt = (r.stdout + r.stderr).strip()
        print("\n--- odin4 ---")
        print(txt if txt else "(sin salida)")
        if "md5 fail" in txt:
            print("\n[-] el md5 sigue fallando")
            return 1
        print("\n[+] MD5 OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
