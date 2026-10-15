#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ORDER M17-GLM-HOSTILE-FALSIFICATION-P3 — K4: Destruir el edge despues de costes.

Escenarios sinteticos/analiticos reproducibles (semilla fija donde hay Monte Carlo)
que muestran condiciones donde una senal predictiva positiva deja de ser viable:

  E1  incorporacion_de_mercado : el mercado ya impone parte de la senal (beta);
                                 el edge residual tras spread+fee se hunde.
  E2  longshot_barato          : comprar contratos de 5c; el hurdle
                                 spread+fee es ~3.4c (68% del precio) y la
                                 seleccion de series con stopping fabrica
                                 "evidencia" de rentabilidad con EV negativo.
  E3  rebate_maker             : el programa oficial es pro-rata y discrecional;
                                 el rebate esperado es 1-2 ordenes de magnitud
                                 menor que la seleccion adversa del maker.
  E4  latencia                 : senal con vida media 8s; latencia de retorno
                                 de 1-4s ya destruye el edge neto en libro 50c.
  E5  falacia_del_punto_medio  : el "edge" medido contra el mid no es el EV
                                 ejecutable contra el ask (doc. oficial).

Modelo de costes con fuentes oficiales:
  fee_taker = shares x 0.07 x p x (1-p)  [categoria crypto]
  maker fee = 0; maker rebate = 20% del pool taker, pro-rata, diario,
  minimo $1 acumulado, a discrecion de Polymarket (docs.polymarket.com).
  "Makers are never charged fees. Only takers pay fees." (doc oficial)

IMPORTANTE: resultados SINTETICOS/ANALITICOS. Demuestran condiciones bajo las
cuales la hipotesis de edge neto de M17 falla (SYNTHETICALLY_FALSIFIABLE);
NO demuestran ningun defecto historico real de SENEX.
"""
import csv
import math
from pathlib import Path

import numpy as np

SEED = 2026100903
OUT_DIR = Path("/home/z/my-project/download/M17_GLM_HOSTILE_P3")
OUT_CSV = OUT_DIR / "k4_resultados.csv"
rng = np.random.default_rng(SEED)

FEE_RATE = 0.07


def fee(p):
    return FEE_RATE * p * (1.0 - p)


CAMPOS = ["escenario", "sub_escenario_o_parametro", "ev_bruto_por_share",
          "coste_spread_por_share", "coste_fee_por_share", "coste_otros_por_share",
          "ev_neto_por_share", "comentario", "clase"]
filas = []


def fila(**kw):
    base = {c: "n/a" for c in CAMPOS}
    base.update(kw)
    base.setdefault("clase", "SINTETICO")
    filas.append(base)


def fmt(x):
    return round(float(x), 5)


# =====================================================================
# E1 — incorporacion parcial de la senal por el mercado (beta)
# =====================================================================
print("== E1: incorporacion de mercado ==")
ALPHA = 0.05          # senal verdadera: q = 0.5 + alpha*s, s=+-1
H_SPREAD = 0.01       # half-spread (coste de cruzar el spread una vez)
for beta in (0.0, 0.25, 0.50, 0.75, 1.00):
    p_ask = 0.5 + beta * ALPHA + H_SPREAD
    ev_bruto = ALPHA * (1 - beta) - H_SPREAD      # q - ask
    f = fee(p_ask)
    ev_neto = ev_bruto - f
    fila(escenario="E1_incorporacion_de_mercado",
         sub_escenario_o_parametro=f"beta={beta}",
         ev_bruto_por_share=fmt(ev_bruto),
         coste_spread_por_share=fmt(H_SPREAD),
         coste_fee_por_share=fmt(f),
         coste_otros_por_share=0.0,
         ev_neto_por_share=fmt(ev_neto),
         comentario=("q=0.5+0.05s; ask=0.5+beta*0.05s+0.01; el mercado incorpora "
                     "beta de la senal antes de que podamos trading"))
    print(f"  beta={beta:.2f}  EV bruto={ev_bruto:+.4f}  fee={f:.4f}  EV neto={ev_neto:+.4f}")

# Hurdle minimo: alpha necesario para empatar con beta=0
for h in (0.005, 0.01, 0.02):
    p = 0.5 + h
    a_star = h + fee(p)
    fila(escenario="E1_hurdle_minimo",
         sub_escenario_o_parametro=f"half_spread={h}",
         ev_bruto_por_share=fmt(a_star),
         coste_spread_por_share=fmt(h),
         coste_fee_por_share=fmt(fee(p)),
         coste_otros_por_share=0.0,
         ev_neto_por_share=0.0,
         comentario=("edge bruto minimo por share para EV=0 con beta=0 (senal totalmente "
                     "no incorporada) en un libro centrado en 50c"))
    print(f"  half_spread={h:.3f} -> alpha* = {a_star:.4f}")

# =====================================================================
# E2 — longshot barato de 5c
# =====================================================================
print("== E2: longshot barato ==")
MID_LS = 0.05
ASK_LS = 0.055          # half-spread 0.5c en un longshot de 5c
Q_TRUE = 0.045          # el mercado SOBREVALORA el longshot (sesgo FLB)
F_LS = fee(ASK_LS)
EV_LS = Q_TRUE - ASK_LS - F_LS
N2 = 20000
wins = rng.random(N2) < Q_TRUE
pnl_sin_stop = np.where(wins, 1.0 - ASK_LS - F_LS, -ASK_LS - F_LS)
print(f"  ask={ASK_LS} fee={F_LS:.5f} EV/share={EV_LS:+.5f} "
      f"(hurdle ask+fee={ASK_LS+F_LS:.4f} = {(ASK_LS+F_LS)/MID_LS*100:.0f}% del precio)")
fila(escenario="E2_longshot_barato",
     sub_escenario_o_parametro=f"mid={MID_LS}; ask={ASK_LS}; q_verdadera={Q_TRUE}",
     ev_bruto_por_share=fmt(Q_TRUE - ASK_LS),
     coste_spread_por_share=fmt(0.005),
     coste_fee_por_share=fmt(F_LS),
     coste_otros_por_share=0.0,
     ev_neto_por_share=fmt(EV_LS),
     comentario=(f"MC {N2} trades: P&L terminal medio={pnl_sin_stop.mean():.2f}, "
                 f"P(P&L>0)={float((pnl_sin_stop.sum())>0)}; hurdle = "
                 f"{(ASK_LS+F_LS)/MID_LS*100:.0f}% del precio del contrato"))

# con stopping rule (+50 / -150 USD) y 200 "fondos" falsos
N_FONDOS = 200
TOPE_UP, TOPE_DOWN = 50.0, -150.0
reporta_ganancia = 0
terminales = []
for _ in range(N_FONDOS):
    acum = 0.0
    while acum > TOPE_DOWN and acum < TOPE_UP:
        gana = rng.random() < Q_TRUE
        acum += (1.0 - ASK_LS - F_LS) if gana else -(ASK_LS + F_LS)
    terminales.append(acum)
    if acum >= TOPE_UP:
        reporta_ganancia += 1
terminales = np.array(terminales)
p_reporta = reporta_ganancia / N_FONDOS
fila(escenario="E2_longshot_con_stopping",
     sub_escenario_o_parametro=f"{N_FONDOS} series; stop +${TOPE_UP}/-${abs(TOPE_DOWN)}",
     ev_bruto_por_share=fmt(Q_TRUE - ASK_LS),
     coste_spread_por_share=fmt(0.005),
     coste_fee_por_share=fmt(F_LS),
     coste_otros_por_share=0.0,
     ev_neto_por_share=fmt(terminales.mean()),
     comentario=(f"P(reportar ganancia tras stopping)={p_reporta:.3f}; "
                 f"E[terminal]={terminales.mean():.2f}; seleccion de series + "
                 "stopping fabrica 'evidencia' con EV negativo"))
print(f"  stopping +{TOPE_UP}/-{abs(TOPE_DOWN)}: P(reportar ganancia)={p_reporta:.3f}; "
      f"E[terminal]={terminales.mean():.2f}")

# Longshot incluso con sesgo FAVORABLE (q=0.052 > mid)
Q_FAV = 0.052
EV_FAV = Q_FAV - ASK_LS - F_LS
fila(escenario="E2_longshot_sesgo_favorable",
     sub_escenario_o_parametro=f"q_verdadera={Q_FAV} > mid={MID_LS}",
     ev_bruto_por_share=fmt(Q_FAV - ASK_LS),
     coste_spread_por_share=fmt(0.005),
     coste_fee_por_share=fmt(F_LS),
     coste_otros_por_share=0.0,
     ev_neto_por_share=fmt(EV_FAV),
     comentario=("incluso si el longshot esta SUBRASTADO en 0.2c, spread+fee "
                 "dan EV negativo: q debe superar ask+fee=0.0586"))
print(f"  sesgo favorable q={Q_FAV}: EV={EV_FAV:+.5f} (todavia negativo)")

# =====================================================================
# E3 — rebate maker pro-rata (doc oficial) vs seleccion adversa
# =====================================================================
print("== E3: rebate maker ==")
for rho in (0.01, 0.05, 0.20, 0.50):
    r = rho * 0.20 * FEE_RATE * 0.25      # p=0.5 => 0.07*0.25
    fila(escenario="E3_rebate_maker_pro_rata",
         sub_escenario_o_parametro=f"rho={rho} (fraccion de liquidez tomada del mercado)",
         ev_bruto_por_share=0.0,
         coste_spread_por_share=0.0,
         coste_fee_por_share=0.0,
         coste_otros_por_share=0.0,
         ev_neto_por_share=fmt(r),
         comentario=("rebate/share = rho*20%*0.07*p*(1-p); diario, pro-rata, min $1, "
                     "a discrecion del protocolo: NO garantizado por fill"))
    print(f"  rho={rho:.2f}  rebate/share={r:.6f}")

fila(escenario="E3_rebate_vs_adversidad",
     sub_escenario_o_parametro="rho=0.20 optimista vs pi_informado=0.25",
     ev_bruto_por_share=fmt(0.01),
     coste_spread_por_share=0.0,
     coste_fee_por_share=0.0,
     coste_otros_por_share=fmt(0.05 * 0.25),
     ev_neto_por_share=fmt(0.01 - 0.0125 + 0.20 * 0.20 * FEE_RATE * 0.25),
     comentario=("captura de spread 1c - adversidad 0.25*5c + rebate optimista 0.0007c "
                 "= negativo; el rebate es ~14x menor que la adversidad"))

# =====================================================================
# E4 — latencia con vida media de senal
# =====================================================================
print("== E4: latencia ==")
ALPHA4 = 0.03
TAU = 8.0


def resid(L):
    return ALPHA4 * math.exp(-L / TAU)


for h, con_fee, etiqueta in ((0.01, True, "libro 50c con fee crypto"),
                             (0.005, True, "libro 50c spread tenue con fee"),
                             (0.005, False, "libro 50c sin fee (no-BTC5m)")):
    for L in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
        f = fee(0.5) if con_fee else 0.0
        coste = h + f
        ev = resid(L) - coste
        fila(escenario="E4_latencia",
             sub_escenario_o_parametro=f"L={L}s; {etiqueta}",
             ev_bruto_por_share=fmt(resid(L)),
             coste_spread_por_share=fmt(h),
             coste_fee_por_share=fmt(f),
             coste_otros_por_share=0.0,
             ev_neto_por_share=fmt(ev),
             comentario="senal alpha=3c vida media 8s; EV residual = alpha*exp(-L/tau)")
        print(f"  L={L:4.1f}s {etiqueta:32s} resid={resid(L):.4f} EV neto={ev:+.4f}")

# =====================================================================
# E5 — falacia del punto medio (doc oficial prices-orderbook)
# =====================================================================
print("== E5: falacia del punto medio ==")
MID = 0.37
ASK = 0.40             # ejemplo textual de la doc oficial
EDGE_VS_MID = 0.01     # la senal "vale" 1c sobre el mid
Q_TRUE5 = MID + EDGE_VS_MID
EV5 = Q_TRUE5 - ASK - fee(ASK)
fila(escenario="E5_falacia_punto_medio",
     sub_escenario_o_parametro=f"mid={MID}; ask={ASK}; edge_vs_mid={EDGE_VS_MID}",
     ev_bruto_por_share=fmt(EDGE_VS_MID),
     coste_spread_por_share=fmt(ASK - MID),
     coste_fee_por_share=fmt(fee(ASK)),
     coste_otros_por_share=0.0,
     ev_neto_por_share=fmt(EV5),
     comentario=("el backtest contra el mid muestra +1c; el EV ejecutable contra el "
                 "ask con fee es negativo; ejemplo de la doc oficial de orderbook"))
print(f"  EV vs mid={EDGE_VS_MID:+.3f}  EV ejecutable={EV5:+.4f}")

# =====================================================================
with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=CAMPOS)
    w.writeheader()
    for f in filas:
        w.writerow(f)
print(f"\nCSV escrito: {OUT_CSV} ({len(filas)} filas)")
