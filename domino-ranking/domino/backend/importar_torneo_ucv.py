"""Carga el '1er Torneo de Dominó UCV' (jugadores, parejas y las 5 rondas) en la base de datos.

Uso (desde la carpeta backend, con el entorno virtual activado y el servidor DETENIDO):

    python importar_torneo_ucv.py                       # usa datos/torneo_ucv_2026.json
    python importar_torneo_ucv.py --fecha 2026-11-08    # cambia la fecha de las partidas
    python importar_torneo_ucv.py --sede "Aula Magna"   # agrega la sede
    python importar_torneo_ucv.py --reemplazar          # borra el torneo y lo carga de nuevo

No borra nada que ya exista (salvo con --reemplazar, y solo ese torneo). Los jugadores que ya
estén registrados se reutilizan. Después todo se puede editar desde la web.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app import competitions as comps
from app.database import SessionLocal, engine, ensure_schema
from app.importer import ImportError_, importar_torneo

AQUI = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archivo", default=str(AQUI / "datos" / "torneo_ucv_2026.json"))
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto la del archivo)")
    ap.add_argument("--sede", help="Lugar donde se jugó")
    ap.add_argument("--reemplazar", action="store_true", help="Si el torneo ya existe, lo borra y lo carga de nuevo")
    args = ap.parse_args()

    datos = json.loads(Path(args.archivo).read_text(encoding="utf-8"))
    fecha = date.fromisoformat(args.fecha) if args.fecha else None

    for col in ensure_schema():
        print(f"[base de datos] columna agregada: {col}")

    with SessionLocal() as db:
        try:
            r = importar_torneo(db, datos, fecha=fecha, sede=args.sede, reemplazar=args.reemplazar)
        except ImportError_ as e:
            print(f"\nNO SE IMPORTÓ NADA: {e}")
            return 1

        comp = r["competencia"]
        print(f"\nBase de datos: {engine.url}")
        print(f"Torneo cargado: {comp.name} (id {comp.id}) — {r['fecha']:%d/%m/%Y}")
        print(f"Jugadores nuevos: {len(r['creados'])} | ya existentes (reutilizados): {len(r['reutilizados'])}")
        print(f"Partidas cargadas: {r['partidas']}")
        info = comps.detail(db, comp)
        print("\nClasificación del suizo (5 rondas, 1 punto por victoria, desempate por average):")
        for row in info["standings"]:
            print(
                f"  {row['position'] or '--':>2}. {row['name']:<48} {row['points']:>2} pts  "
                f"PF {row['pf']:>3}  PC {row['pa']:>3}  prom {row['diff']:+d}"
            )
        final = next((m for s in info["sections"] if s["phase"] == "ko" for m in s["matches"]), None)
        if final:
            a = (final["entry_a"] or {}).get("name", "?")
            b = (final["entry_b"] or {}).get("name", "?")
            if final["status"] == "pending":
                print(f"\nFinal pendiente: {a}  vs  {b}  (registra el marcador desde la web)")
            else:
                print(f"\nFinal: {a} {final['points_a']} - {final['points_b']} {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
