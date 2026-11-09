#!/usr/bin/env python3
"""
Vigilante de stock de iPhone para retiro en Apple Store (EE. UU.).
Consulta la misma disponibilidad que usa apple.com y avisa por ntfy
cuando el modelo aparece disponible en las tiendas que elijas.

Uso rápido:
  python watcher.py --test                 # una revisión + notificación de prueba
  python watcher.py                        # vigila sin parar
Configuración por argumentos o variables de entorno (ver CONFIG abajo).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime


def env(name, default):
    value = os.environ.get(name, "")
    return value if value.strip() else default


# ---------------- CONFIG ----------------
DEFAULT_PART = env("APPLE_PART", "MJW44LL/A")        # iPhone 18 Pro Max 256 GB negro (confírmalo con --test)
DEFAULT_ZIP = env("APPLE_ZIP", "10153")              # código postal de Apple Fifth Avenue
DEFAULT_STORES = env("APPLE_STORES", "Fifth Avenue")  # nombres separados por coma, ej. "Fifth Avenue,World Trade Center"
DEFAULT_TOPIC = env("NTFY_TOPIC", "")
DEFAULT_EVERY = int(env("CHECK_EVERY_SECONDS", "60"))
DEFAULT_RUN_MINUTES = float(env("RUN_MINUTES", "0"))  # 0 = sin límite
# ----------------------------------------

APPLE_URL = "https://www.apple.com/shop/fulfillment-messages"
BUY_URL = "https://www.apple.com/shop/buy-iphone"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone",
}


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def fetch(part, zip_code):
    query = urllib.parse.urlencode({
        "fae": "true", "pl": "true", "mts.0": "regular",
        "parts.0": part, "location": zip_code,
    })
    req = urllib.request.Request(f"{APPLE_URL}?{query}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def parse(data, part):
    stores = (((data or {}).get("body") or {}).get("content") or {}).get("pickupMessage", {}).get("stores", []) or []
    result = []
    for s in stores:
        pa = (s.get("partsAvailability") or {}).get(part) or {}
        regular = ((pa.get("messageTypes") or {}).get("regular") or {})
        result.append({
            "name": s.get("storeName", "?"),
            "number": s.get("storeNumber", ""),
            "available": pa.get("pickupDisplay") == "available",
            "quote": pa.get("pickupSearchQuote", "") or regular.get("storePickupQuote", ""),
            "title": regular.get("storePickupProductTitle", "") or pa.get("storePickupProductTitle", ""),
        })
    return result


def notify(topic, title, message, priority=5, click=BUY_URL):
    if not topic:
        log("(sin NTFY_TOPIC: no se envía notificación)")
        return
    body = json.dumps({
        "topic": topic, "title": title, "message": message,
        "priority": priority, "click": click, "tags": ["iphone"],
    }).encode("utf-8")
    req = urllib.request.Request("https://ntfy.sh/", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # no detener el vigilante por un fallo de ntfy
        log(f"No se pudo enviar la notificación: {e}")


def watched(stores, names):
    keys = [n.strip().lower() for n in names.split(",") if n.strip()]
    return [s for s in stores if any(k in s["name"].lower() for k in keys)]


def run_test(a):
    log(f"Revisando parte {a.part} cerca de {a.zip}...")
    try:
        stores = parse(fetch(a.part, a.zip), a.part)
    except Exception as e:
        log(f"ERROR al consultar Apple: {e}")
        notify(a.topic, "Vigilante iPhone: error", f"No pude consultar Apple: {e}", 3)
        sys.exit(1)
    if not stores:
        log("Apple no devolvió tiendas. Revisa el número de parte y el código postal.")
        sys.exit(1)
    title = next((s["title"] for s in stores if s["title"]), "(sin nombre)")
    log(f"Producto según Apple: {title}")
    for s in stores:
        mark = "DISPONIBLE" if s["available"] else "no"
        log(f"  {s['name']:<28} {mark:<11} {s['quote']}")
    mine = watched(stores, a.stores)
    if not mine:
        log(f"Ninguna tienda coincide con '{a.stores}'. Usa uno de los nombres de arriba.")
    else:
        log("Tiendas vigiladas: " + ", ".join(s["name"] for s in mine))
    notify(a.topic, "Vigilante iPhone: prueba OK",
           f"Producto: {title}\nTiendas vigiladas: {', '.join(s['name'] for s in mine) or 'ninguna'}", 3)


def run_loop(a):
    log(f"Vigilando {a.part} en '{a.stores}' cada {a.every} s")
    deadline = time.time() + a.run_minutes * 60 if a.run_minutes > 0 else None
    prev_available = set()
    errors, error_alerted, matched_ok = 0, False, False
    while deadline is None or time.time() < deadline:
        try:
            stores = parse(fetch(a.part, a.zip), a.part)
            mine = watched(stores, a.stores)
            if not mine and not matched_ok:
                log("Ninguna tienda coincide. Tiendas devueltas: " + ", ".join(s["name"] for s in stores))
            matched_ok = matched_ok or bool(mine)
            now_available = {s["name"] for s in mine if s["available"]}
            for s in mine:
                if s["available"] and s["name"] not in prev_available:
                    log(f"*** DISPONIBLE en {s['name']} ***")
                    notify(a.topic, f"¡Hay stock en {s['name']}!",
                           f"{s['title'] or a.part}\n{s['quote']}\nReserva YA en la app de Apple Store.")
            if prev_available and not now_available:
                log("Se agotó de nuevo.")
            prev_available = now_available
            log("Revisado: " + (", ".join(f"{s['name']}={'SÍ' if s['available'] else 'no'}" for s in mine) or "sin tiendas"))
            errors, error_alerted = 0, False
        except Exception as e:
            errors += 1
            log(f"Error #{errors}: {e}")
            if errors >= 10 and not error_alerted:
                notify(a.topic, "Vigilante iPhone: falla repetida",
                       f"10 errores seguidos. Último: {e}", 3)
                error_alerted = True
        time.sleep(a.every)
    log("Tiempo de ejecución terminado.")


def main():
    p = argparse.ArgumentParser(description="Vigila stock de retiro en Apple Store")
    p.add_argument("--part", default=DEFAULT_PART)
    p.add_argument("--zip", default=DEFAULT_ZIP)
    p.add_argument("--stores", default=DEFAULT_STORES)
    p.add_argument("--topic", default=DEFAULT_TOPIC)
    p.add_argument("--every", type=int, default=max(DEFAULT_EVERY, 30))
    p.add_argument("--run-minutes", type=float, default=DEFAULT_RUN_MINUTES)
    p.add_argument("--test", action="store_true")
    a = p.parse_args()
    a.every = max(a.every, 30)  # no consultar más seguido que cada 30 s
    run_test(a) if a.test else run_loop(a)


if __name__ == "__main__":
    main()
