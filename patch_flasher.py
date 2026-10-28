#!/usr/bin/env python3
"""
patch_flasher.py — escribir por `patch` (8 B por comando), la ruta YA DEMOSTRADA
en este equipo y la única que no pasa por la fase de datos USB.

Por qué existe: el comando `program` necesita enviar los datos en crudo y ese
canal es justo el que se atasca en este loader. `patch` escribe el valor dentro
del propio XML: va solo por comandos, sin bulk OUT de datos.

Historia en este teléfono:
  - flip/restore de "ANDROID!" en LBA 99824 (LUN0, zona prohibida para program)
  - 31 ACK / 0 NAK, relectura independiente y restauración exacta por hash
  - handler 0x14828180: NO llama a la puerta de política (0x14827c18)

Estrategia: NO escribir a ciegas. Primero lee el rango destino y compara con
la imagen; solo parchea los bloques de 8 B que difieren. Si ya coincide, no
escribe nada.

Uso:
  # ¿hace falta escribir? (solo lectura)
  python3 patch_flasher.py xbl.elf --lun 1 --start 6 --verify-only

  # medir la tasa real sin cambiar nada (mismos valores que ya hay)
  python3 patch_flasher.py xbl.elf --lun 1 --start 6 --bench 500

  # escribir de verdad (solo los bloques distintos)
  python3 patch_flasher.py xbl.elf --lun 1 --start 6 --yes
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from edl_sync import Sync  # noqa: E402

SS = 4096          # tamaño de sector de este equipo
STEP = 8           # máximo de bytes por comando patch


def sha(b):
    return hashlib.sha256(b).hexdigest()


def plan_blocks(old, new, ss=SS, step=STEP):
    """Bloques alineados de `step` bytes que difieren -> (offset_abs, valor, n)."""
    out = []
    n = len(new)
    for base in range(0, n, step):
        chunk_new = new[base:base + step]
        chunk_old = old[base:base + len(chunk_new)]
        if chunk_new == chunk_old:
            continue
        # relleno con el contenido actual si el bloque sobresale de la imagen
        if len(chunk_new) < step:
            chunk_new = chunk_new + chunk_old[len(chunk_new):]
        out.append((base, chunk_new))
    return out


def patch_cmd(sy, lun, abs_off, value_bytes):
    sector, off = divmod(abs_off, SS)
    val = int.from_bytes(value_bytes, "little")
    xml = (f'<patch SECTOR_SIZE_IN_BYTES="{SS}" byte_offset="{off}" '
           f'filename="DISK" physical_partition_number="{lun}" '
           f'size_in_bytes="{len(value_bytes)}" start_sector="{sector}" '
           f'value="{val}" />')
    return sy.cmd(xml, timeout=6.0, fast=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--lun", type=int, required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--edl-dir", default="~/edl")
    ap.add_argument("--loader", default="~/edl/loader.elf")
    ap.add_argument("--verify-only", action="store_true",
                    help="solo leer y comparar; no escribe nada")
    ap.add_argument("--bench", type=int, default=0, metavar="N",
                    help="medir tasa con N parches del valor actual (no cambia nada)")
    ap.add_argument("--diff-detail", action="store_true",
                    help="mostrar exactamente qué bytes difieren (offset, sector, hex)")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="escribir solo los primeros N bloques (prueba por etapas)")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    img = os.path.abspath(os.path.expanduser(a.image))
    with open(img, "rb") as f:
        new = f.read()
    total_sectors = (len(new) + SS - 1) // SS
    if len(new) % SS:
        new += b"\x00" * (total_sectors * SS - len(new))

    edl_dir = os.path.abspath(os.path.expanduser(a.edl_dir))
    loader = os.path.abspath(os.path.expanduser(a.loader))
    print(f"[*] patch_flasher — imagen {img} ({len(new)} B, {total_sectors} sectores)")
    print(f"[*] destino LUN {a.lun}, sectores {a.start}..{a.start + total_sectors - 1}")

    with Sync(edl_dir, loader, lun=a.lun) as sy:
        sy.drain(quiet=0.4)
        ok, raw = sy.nop()
        if not ok:
            sys.exit(f"[-] loader no responde a nop: {raw[-150:]!r}\n"
                     "    -> ciclo de energía + sahara_inject.py")
        print("[+] nop ACK: loader sano")

        print("[*] leyendo el rango actual ...")
        t0 = time.time()
        cur = sy.read_block(a.lun, a.start, total_sectors)
        if cur is None:
            sys.exit("[-] no se pudo leer el rango")
        cur = cur[:len(new)]
        print(f"[+] {len(cur)} B leídos en {time.time()-t0:.1f} s")
        print(f"    actual  sha256 {sha(cur)}")
        print(f"    imagen  sha256 {sha(new)}")

        if cur == new:
            print("\n[+] EL RANGO YA ES IDÉNTICO A LA IMAGEN: no hay nada que escribir.")
            return 0

        blocks = plan_blocks(cur, new)
        pct = 100.0 * len(blocks) * STEP / len(new)
        print(f"\n[*] bloques distintos: {len(blocks)} de {len(new)//STEP} "
              f"({pct:.1f}% del rango) -> {len(blocks)} comandos patch")

        if a.diff_detail:
            print("\n[*] detalle de diferencias:")
            shown = 0
            for off, val in blocks:
                if shown >= 40:
                    print(f"    ... y {len(blocks) - shown} bloques más")
                    break
                old = cur[off:off + len(val)]
                sec, boff = divmod(off, SS)
                print(f"    sector {a.start + sec:<6} off {boff:<4} "
                      f"actual {old.hex():<16} imagen {val.hex()}")
                shown += 1
            # rangos contiguos
            runs, start = [], None
            prev = None
            for off, val in blocks:
                if start is None:
                    start = off
                elif off != prev + STEP:
                    runs.append((start, prev + STEP))
                    start = off
                prev = off
            if start is not None:
                runs.append((start, prev + STEP))
            print(f"\n[*] {len(runs)} rango(s) contiguo(s):")
            for a0, b0 in runs[:20]:
                print(f"    offset {a0}..{b0} (sector {a.start + a0 // SS}, "
                      f"{b0 - a0} bytes)")
            return 0

        if a.verify_only:
            print("\n[!] --verify-only: no se escribe nada. Fin.")
            return 0

        if a.bench:
            n = min(a.bench, len(blocks))
            print(f"\n[*] BENCH: {n} parches con el valor que YA está en el flash "
                  f"(sin cambio efectivo)")
            t0 = time.time()
            for k in range(n):
                off, _ = blocks[k]
                cur_val = cur[off:off + STEP]
                ok, raw = patch_cmd(sy, a.lun, a.start * SS + off, cur_val)
                if not ok:
                    print(f"\n[-] fallo en el parche {k}: {raw[-160:]!r}")
                    return 1
            dt = time.time() - t0
            rate = n / dt
            print(f"\n[+] {n} parches en {dt:.1f} s = {rate:.1f} ops/s "
                  f"({rate*STEP/1024:.1f} KB/s)")
            print(f"    estimación para esta imagen: {len(blocks)/rate/60:.0f} min")
            return 0

        if not a.yes:
            print("\n[!] modo simulación. Añade --yes para escribir de verdad.")
            return 0

        total_blocks = len(blocks)
        last_off = len(new)
        if a.limit:
            blocks = blocks[:a.limit]
            last_off = (blocks[-1][0] + STEP) if blocks else 0
            print(f"[*] --limit {a.limit}: se escribirán {len(blocks)} bloques "
                  f"(de {total_blocks}), hasta el offset {last_off}")

        # respaldo del rango antes de tocar nada
        bkdir = os.path.join(HERE, "patch_backups")
        os.makedirs(bkdir, exist_ok=True)
        bk = os.path.join(bkdir, f"lun{a.lun}_s{a.start}_{total_sectors}s_{int(time.time())}.bin")
        with open(bk, "wb") as f:
            f.write(cur)
        print(f"[*] respaldo -> {bk}")

        t0 = time.time()
        done = 0
        manifest = bk + ".json"
        try:
            for k, (off, val) in enumerate(blocks):
                ok, raw = patch_cmd(sy, a.lun, a.start * SS + off, val)
                if not ok:
                    print(f"\n[-] FALLO en el parche {k} (offset {off}, "
                          f"sector {a.start + off // SS}): {raw[-160:]!r}")
                    with open(manifest, "w") as f:
                        json.dump({"done": done, "total": len(blocks),
                                   "backup": bk, "failed_at": off}, f)
                    print(f"    aplicados {done}/{len(blocks)}; manifiesto {manifest}")
                    return 1
                done += 1
                if done % 200 == 0 or done == len(blocks):
                    dt = time.time() - t0
                    rate = done / dt
                    eta = (len(blocks) - done) / rate / 60 if rate else 0
                    print(f"\r  {done}/{len(blocks)} ({100.0*done/len(blocks):5.1f}%) "
                          f"{rate:5.1f} ops/s  ETA {eta:4.1f} min", end="", flush=True)
        except KeyboardInterrupt:
            print(f"\n[!] interrumpido en {done}/{len(blocks)}; respaldo en {bk}")
            return 130

        print("\n[*] verificando por relectura ...")
        back = sy.read_block(a.lun, a.start, total_sectors)[:len(new)]
        if a.limit:
            if back[:last_off] == new[:last_off]:
                print(f"[+] ETAPA VERIFICADA: {len(blocks)} bloques correctos "
                      f"(hasta offset {last_off})")
                pend = total_blocks - len(blocks)
                if pend:
                    print(f"    quedan {pend} bloques; relanza el mismo comando sin --limit")
                return 0
            dif = [i for i in range(last_off) if back[i] != new[i]]
            print(f"[-] ETAPA CON {len(dif)} BYTES DISTINTOS (primero en {dif[0]})")
            print(f"    respaldo para restaurar: {bk}")
            return 1
        if back == new:
            print(f"[+] VERIFICADO: el rango coincide con la imagen. sha256 {sha(back)}")
            return 0
        dif = [i for i in range(len(new)) if back[i] != new[i]]
        print(f"[-] quedan {len(dif)} bytes distintos (primero en offset {dif[0]})")
        print(f"    respaldo para restaurar: {bk}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
