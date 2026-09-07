"""Interfaz de linea de comandos."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import db, normalizar, panel, parsers, pdf
from .modelo import parse_monto

VERDE, AMARILLO, ROJO, GRIS, FIN = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"


def _color(texto: str, color: str) -> str:
    return texto if not sys.stdout.isatty() else f"{color}{texto}{FIN}"


def _plata(valor: Decimal, moneda: str = "ARS") -> str:
    signo = "-" if valor < 0 else ""
    entero = f"{abs(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{signo}{'$' if moneda == 'ARS' else 'US$'} {entero}"


# ----------------------------------------------------------------------
# ingerir
# ----------------------------------------------------------------------


def cmd_ingerir(args) -> int:
    con = db.conectar(args.base)
    normalizar.sembrar_reglas(con)

    rutas: list[Path] = []
    for entrada in args.archivos:
        p = Path(entrada)
        rutas.extend(sorted(p.glob("*.pdf")) if p.is_dir() else [p])

    if not rutas:
        print(_color("No se encontro ningun PDF en lo que pasaste.", AMARILLO))
        return 1

    total_nuevos = 0
    for ruta in rutas:
        if not ruta.exists():
            print(_color(f"  {ruta.name}: no existe", ROJO))
            continue

        sha = pdf.hash_archivo(ruta)
        if db.archivo_ya_ingerido(con, sha) and not args.reprocesar:
            print(_color(f"  {ruta.name}: ya procesado, se saltea", GRIS))
            continue

        try:
            doc = pdf.leer(ruta, claves=args.clave)
        except pdf.PdfProtegido as exc:
            print(_color(f"  {ruta.name}: {exc}", ROJO))
            continue
        except Exception as exc:
            print(_color(f"  {ruta.name}: no se pudo abrir ({exc})", ROJO))
            continue

        if doc.esta_vacio:
            print(_color(
                f"  {ruta.name}: no tiene texto extraible. Probablemente sea un "
                "escaneo; necesita OCR y este sistema todavia no lo hace.", ROJO))
            continue

        parser = parsers.detectar(doc)
        if parser is None:
            print(_color(f"  {ruta.name}: ningun parser lo reconocio", ROJO))
            continue

        res = parser.parsear(doc)
        nuevos, repetidos = db.guardar_movimientos(con, res.movimientos)
        db.registrar_archivo(con, sha, ruta.name, parser.nombre, len(res.movimientos))
        total_nuevos += nuevos

        estado = VERDE if res.tasa_exito > 0.9 else (AMARILLO if res.tasa_exito > 0.6 else ROJO)
        print(
            f"  {ruta.name}: {_color(f'{nuevos} nuevos', estado)}, {repetidos} ya estaban "
            f"{GRIS}[{parser.nombre}, {len(res.sin_parsear)} lineas sin parsear]{FIN}"
        )
        for adv in res.advertencias:
            print(_color(f"      aviso: {adv}", AMARILLO))
        if args.verboso:
            for linea in res.sin_parsear[:20]:
                print(_color(f"      sin parsear: {linea[:100]}", GRIS))

    if total_nuevos:
        conteo = normalizar.categorizar(con)
        categorizados = sum(conteo.values())
        print(f"\n{total_nuevos} movimientos nuevos, {categorizados} categorizados.")
    return 0


# ----------------------------------------------------------------------
# diagnostico
# ----------------------------------------------------------------------


def cmd_diagnostico(args) -> int:
    """Muestra que texto sale realmente de un PDF.

    Es lo primero que hay que correr cuando un parser no reconoce nada: lo que
    ves aca es exactamente lo que el parser tiene disponible.
    """
    ruta = Path(args.archivo)
    try:
        doc = pdf.leer(ruta, claves=args.clave)
    except Exception as exc:
        print(_color(f"No se pudo abrir: {exc}", ROJO))
        return 1

    parser = parsers.detectar(doc)
    print(f"Archivo:  {ruta.name}")
    print(f"Paginas:  {len(doc.paginas)}")
    print(f"Tablas:   {len(doc.tablas)}")
    print(f"Parser:   {parser.nombre if parser else _color('ninguno lo reconoce', ROJO)}")
    print(f"SHA256:   {doc.sha256[:16]}...")

    if doc.esta_vacio:
        print(_color("\nEl PDF no tiene texto extraible (escaneo). Necesita OCR.", ROJO))
        return 1

    print(f"\n{'-' * 70}\nPrimeras {args.lineas} lineas:\n{'-' * 70}")
    for i, linea in enumerate(doc.lineas[: args.lineas], 1):
        print(f"{i:3}  {linea}")

    if parser:
        res = parser.parsear(doc)
        print(f"\n{'-' * 70}")
        print(f"Movimientos reconocidos: {len(res.movimientos)}")
        print(f"Lineas sin parsear:      {len(res.sin_parsear)}")
        print(f"Tasa de exito:           {res.tasa_exito:.0%}")
        for mov in res.movimientos[:10]:
            print(f"  {mov.fecha}  {_plata(mov.monto, mov.moneda.value):>18}  {mov.descripcion[:45]}")
    return 0


# ----------------------------------------------------------------------
# mercado pago
# ----------------------------------------------------------------------


def cmd_mp_probar(args) -> int:
    """El spike: contesta si la API de MP sirve para tus consumos."""
    from .mercadopago import ClienteMP, ErrorMercadoPago, parsear_csv, periodo_por_defecto

    try:
        with ClienteMP() as cli:
            yo = cli.quien_soy()
            print(f"Cuenta: {yo.get('nickname')} ({yo.get('email', 'sin email')})")
            print(f"ID:     {yo.get('id')}\n")

            desde, hasta = periodo_por_defecto(args.dias)
            print(f"Pidiendo el reporte del {desde} al {hasta}. Puede tardar un minuto...")
            csv_texto = cli.reporte_del_periodo(desde, hasta)
    except ErrorMercadoPago as exc:
        print(_color(str(exc), ROJO))
        return 1

    movs = parsear_csv(csv_texto)
    egresos = [m for m in movs if m.monto < 0]
    ingresos = [m for m in movs if m.monto > 0]

    print(f"\nFilas en el reporte: {len(movs)}")
    print(f"  ingresos: {len(ingresos)}")
    print(f"  egresos:  {len(egresos)}")

    print(f"\n{'-' * 70}")
    if egresos:
        print(_color("Aparecen movimientos de consumo. La API sirve como fuente.", VERDE))
        print("\nUltimos egresos:")
        for m in sorted(egresos, key=lambda m: m.fecha, reverse=True)[:10]:
            print(f"  {m.fecha}  {_plata(m.monto, m.moneda.value):>18}  {m.descripcion[:45]}")
    else:
        print(_color("No hay egresos en el reporte.", AMARILLO))
        print(
            "La API esta devolviendo solo el lado cobrador. Para tus consumos vas a\n"
            "tener que exportar el resumen desde la app de Mercado Pago y subirlo\n"
            "como PDF, igual que Galicia."
        )
    return 0


def cmd_mp_sync(args) -> int:
    from .mercadopago import ClienteMP, ErrorMercadoPago, parsear_csv, periodo_por_defecto

    con = db.conectar(args.base)
    normalizar.sembrar_reglas(con)
    try:
        with ClienteMP() as cli:
            desde, hasta = periodo_por_defecto(args.dias)
            print(f"Reporte del {desde} al {hasta}...")
            csv_texto = cli.reporte_del_periodo(desde, hasta)
    except ErrorMercadoPago as exc:
        print(_color(str(exc), ROJO))
        return 1

    movs = parsear_csv(csv_texto)
    nuevos, repetidos = db.guardar_movimientos(con, movs)
    db.registrar_cuenta(con, "mercadopago", "Mercado Pago", "mercadopago", "billetera")
    normalizar.categorizar(con)
    print(f"{nuevos} movimientos nuevos, {repetidos} ya estaban.")
    return 0


# ----------------------------------------------------------------------
# saldo y estado
# ----------------------------------------------------------------------


def cmd_saldo(args) -> int:
    """Carga el ancla mensual: el saldo real de una cuenta."""
    crudo = args.monto_opt or args.monto
    if crudo is None:
        print(_color("Falta el monto. Para negativos: --monto=-34.070,62", ROJO))
        return 2
    try:
        monto = parse_monto(crudo)
    except ValueError as exc:
        print(_color(f"Monto invalido: {exc}", ROJO))
        return 2

    con = db.conectar(args.base)
    fecha = date.fromisoformat(args.fecha) if args.fecha else date.today()
    db.guardar_saldo(con, args.cuenta, fecha, monto, args.moneda)
    print(f"Saldo de {args.cuenta} al {fecha}: {_plata(monto, args.moneda)}")

    # Filtrar tambien por moneda: si no, cargar un saldo en dolares reporta
    # el resultado de la conciliacion en pesos, que es otra cosa.
    conciliaciones = [
        c for c in normalizar.conciliar(con)
        if c.cuenta == args.cuenta and c.moneda == args.moneda
    ]
    if not conciliaciones:
        print(_color(
            "Todavia no hay conciliacion para esta cuenta y moneda: hace falta un "
            "saldo anterior contra el cual comparar. Cargá el del mes pasado y "
            "desde el proximo ya valida sola.", GRIS))
    else:
        ultima = conciliaciones[-1]
        if ultima.cuadra:
            print(_color("La conciliacion cierra: la ingesta esta capturando todo.", VERDE))
        else:
            print(_color(
                f"Diferencia de {_plata(ultima.diferencia, ultima.moneda)} contra lo "
                f"derivado de los movimientos. Falta capturar ese monto.", AMARILLO))
    return 0


def cmd_estado(args) -> int:
    con = db.conectar(args.base)

    cuentas = db.resumen_cuentas(con)
    if not cuentas:
        print("La base esta vacia. Empeza con: finanzas ingerir entrada/")
        return 0

    print(f"{'CUENTA':<28} {'MOV':>5}  {'DESDE':<11} {'HASTA':<11} {'NETO':>16}")
    print("-" * 76)
    for c in cuentas:
        print(
            f"{c['cuenta'][:28]:<28} {c['movimientos']:>5}  "
            f"{c['desde']:<11} {c['hasta']:<11} {_plata(c['neto'], c['moneda']):>16}"
        )

    conciliaciones = normalizar.conciliar(con)
    if conciliaciones:
        print(f"\n{'CONCILIACION':<28} {'FECHA':<11} {'DIFERENCIA':>16}")
        print("-" * 60)
        for c in conciliaciones[-8:]:
            marca = _color("cierra", VERDE) if c.cuadra else _color("no cierra", AMARILLO)
            print(f"{c.cuenta[:28]:<28} {c.fecha}  {_plata(c.diferencia, c.moneda):>16}  {marca}")
    else:
        print(_color(
            "\nSin saldos cargados: no hay conciliacion posible todavia.\n"
            "Carga uno con: finanzas saldo <cuenta> <monto>", GRIS))

    pendientes = normalizar.compromisos_pendientes(con)
    if pendientes:
        total = sum(c.total_pendiente for c in pendientes)
        print(f"\nCuotas comprometidas hacia adelante: {_plata(total)} en {len(pendientes)} compras")
        for c in pendientes[:5]:
            print(f"  {c.descripcion[:40]:<40} {c.cuota_actual}/{c.cuota_total}  "
                  f"{_plata(c.total_pendiente, c.moneda):>14}")

    dias = normalizar.dias_sin_movimientos(con)
    if dias is not None and dias > args.alerta_dias:
        print(_color(
            f"\nHace {dias} dias que no entra un movimiento. Si eso no es normal, "
            "la ingesta esta rota.", ROJO))
    return 0


def cmd_categorizar(args) -> int:
    con = db.conectar(args.base)
    normalizar.sembrar_reglas(con)
    if args.regla:
        patron, categoria = args.regla
        normalizar.agregar_regla(con, patron, categoria)
        print(f"Regla agregada: /{patron}/ -> {categoria}")
    conteo = normalizar.categorizar(con, solo_sin_categoria=not args.recategorizar)
    if conteo:
        print(f"Categorizados ahora: {sum(conteo.values())}")
        for categoria, n in sorted(conteo.items(), key=lambda kv: -kv[1]):
            print(f"  {categoria:<24} {n:>5}")

    # El estado actual siempre, aunque esta corrida no haya cambiado nada.
    print("\nDistribucion actual:")
    for f in con.execute(
        "SELECT COALESCE(categoria, 'sin categoria') AS c, COUNT(*) AS n, "
        "SUM(centavos) AS total FROM movimientos GROUP BY c ORDER BY ABS(SUM(centavos)) DESC"
    ):
        print(f"  {f['c']:<24} {f['n']:>5}  {_plata(Decimal(f['total']) / 100):>16}")
    sin = con.execute(
        "SELECT COUNT(*) AS n FROM movimientos WHERE categoria IS NULL"
    ).fetchone()["n"]
    if sin:
        print(_color(f"\n{sin} movimientos sin categoria.", AMARILLO))
        print("Mira cuales son con: finanzas sin-categoria")
    return 0


def cmd_sin_categoria(args) -> int:
    con = db.conectar(args.base)
    filas = con.execute(
        "SELECT descripcion, COUNT(*) AS n, SUM(centavos) AS total "
        "FROM movimientos WHERE categoria IS NULL "
        "GROUP BY comercio ORDER BY ABS(SUM(centavos)) DESC LIMIT ?",
        (args.limite,),
    ).fetchall()
    if not filas:
        print(_color("Todo categorizado.", VERDE))
        return 0
    print(f"{'DESCRIPCION':<50} {'N':>4} {'TOTAL':>16}")
    print("-" * 72)
    for f in filas:
        print(f"{f['descripcion'][:50]:<50} {f['n']:>4} {_plata(Decimal(f['total']) / 100):>16}")
    print(_color("\nAgrega una regla con:\n  finanzas categorizar --regla 'PATRON' categoria", GRIS))
    return 0


def cmd_panel(args) -> int:
    con = db.conectar(args.base)
    salida = panel.generar(con, Path(args.salida))
    print(f"Panel generado en {salida}")
    print(f"Abrilo con: python3 -m http.server -d {salida.parent}")
    return 0


# ----------------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="finanzas",
        description="Seguimiento de finanzas personales desde resumenes PDF y Mercado Pago.",
    )
    p.add_argument("--base", default="finanzas.db", help="ruta de la base (default: finanzas.db)")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("ingerir", help="procesa PDFs de resumen")
    s.add_argument("archivos", nargs="+", help="archivos PDF o carpetas")
    s.add_argument("--clave", action="append", default=[],
                   help="clave del PDF (repetible; se prueban todas)")
    s.add_argument("--reprocesar", action="store_true", help="reprocesa archivos ya vistos")
    s.add_argument("-v", "--verboso", action="store_true", help="muestra las lineas sin parsear")
    s.set_defaults(func=cmd_ingerir)

    s = sub.add_parser("diagnostico", help="muestra que texto extrae un PDF")
    s.add_argument("archivo")
    s.add_argument("--clave", action="append", default=[])
    s.add_argument("--lineas", type=int, default=40)
    s.set_defaults(func=cmd_diagnostico)

    s = sub.add_parser("mp-probar", help="verifica si la API de MP cubre tus consumos")
    s.add_argument("--dias", type=int, default=90)
    s.set_defaults(func=cmd_mp_probar)

    s = sub.add_parser("mp-sync", help="importa movimientos desde Mercado Pago")
    s.add_argument("--dias", type=int, default=90)
    s.set_defaults(func=cmd_mp_sync)

    s = sub.add_parser(
        "saldo",
        help="carga el saldo real de una cuenta (ancla mensual)",
        epilog="Para un monto negativo usa la forma con '=': --monto=-34.070,62",
    )
    s.add_argument("cuenta")
    # Positional y opcion a la vez: un monto negativo empieza con '-' y
    # argparse lo tomaria como una opcion desconocida. Con --monto=-123 no
    # hay ambiguedad posible.
    s.add_argument("monto", nargs="?", help="ej: 34.070,62")
    s.add_argument("--monto", dest="monto_opt", help="igual que el positional, admite negativos")
    s.add_argument("--fecha", help="YYYY-MM-DD (default: hoy)")
    s.add_argument("--moneda", default="ARS")
    s.set_defaults(func=cmd_saldo)

    s = sub.add_parser("estado", help="resumen de cuentas, conciliacion y cuotas")
    s.add_argument("--alerta-dias", type=int, default=10)
    s.set_defaults(func=cmd_estado)

    s = sub.add_parser("categorizar", help="aplica reglas de categoria")
    s.add_argument("--regla", nargs=2, metavar=("PATRON", "CATEGORIA"))
    s.add_argument("--recategorizar", action="store_true", help="tambien los ya categorizados")
    s.set_defaults(func=cmd_categorizar)

    s = sub.add_parser("sin-categoria", help="lista lo que ninguna regla reconoce")
    s.add_argument("--limite", type=int, default=25)
    s.set_defaults(func=cmd_sin_categoria)

    s = sub.add_parser("panel", help="genera el panel HTML")
    s.add_argument("--salida", default="panel/index.html")
    s.set_defaults(func=cmd_panel)

    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
