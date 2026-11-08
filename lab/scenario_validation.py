"""Validate scenario structure and engine input boundaries before admission."""

import ipaddress
import re

from lab.apps import MODEL_TYPES, validate_app
from lab.model import APP_TYPES, DEVICE_TYPES, EVENT_ACTIONS, normalize_link_params, ospf_convergence_delay, parse_rate
from lab.validation_numbers import MAX_POSITION, MAX_UINT64, MIN_TIMER_S, MAX_TIMER_S, integer, number, packet_rate

# The native runner assigns ports from 9000 (unicast) and 9500 (multicast).
MAX_EXPANDED_APPS = 65535 - 9500 + 1


def _items(sc, field, errors):
    items = sc.get(field)
    if items is None:
        return []
    if not isinstance(items, list):
        errors.append(f"{field}: array of objects")
        return []
    valid = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{field}[{i}]: object")
        else:
            valid.append((i, item))
    return valid


def _check(errors, prefix, check, *args, **kwargs):
    try:
        return check(*args, **kwargs)
    except ValueError as e:
        errors.append(f"{prefix}: {e}")
        return None


def _endpoint(devs, key, version, prefix, errors):
    dev = devs.get(key) if isinstance(key, str) else None
    if not isinstance(dev, dict):
        errors.append(f"{prefix}: qurilma nomi loyihada bo'lsin")
        return
    dtype = dev.get("type")
    if not isinstance(dtype, str) or not DEVICE_TYPES.get(dtype, {}).get("l3") or dtype == "sdnrouter":
        errors.append(f"{prefix}: trafik manbai/qabul qiluvchisi host/server/router bo'lsin")
    elif version == 6:
        from lab.addressing import interface_address
        try:
            has_ip6 = any(interface_address(ic, 6) for ic in
                          ((dev.get("config") or {}).get("interfaces") or {}).values())
        except ValueError:
            has_ip6 = False
        if not has_ip6:
            errors.append(f"{prefix}: IPv6 manzil yo'q")


def _app(project, devs, a, i, dur, version, ids, errors):
    aid = a.get("id")
    if aid is None or aid == "":
        aid = f"a{i + 1}"
    w = f"ilova {i + 1}"
    if not isinstance(aid, str):
        errors.append(f"{w}: id string bo'lsin")
    elif aid in ids:
        errors.append(f"{w}: id takrorlangan ({aid})")
    else:
        ids.add(aid)
    kind = a.get("type")
    if kind not in APP_TYPES + MODEL_TYPES:
        errors.append(f"{w}: tur {APP_TYPES + MODEL_TYPES} dan biri")
    elif kind in MODEL_TYPES:
        errors.extend(f"{w}: {e}" for e in validate_app(a, dur))
    if a.get("dst_ip") is not None and a.get("dst_ip") != "":
        try:
            if not isinstance(a["dst_ip"], str):
                raise ValueError
            if ipaddress.ip_address(a["dst_ip"]).version != version:
                errors.append(f"{w}: dst_ip ip_version bilan mos bo'lsin")
        except ValueError:
            errors.append(f"{w}: noto'g'ri dst_ip")
    for key, low, high in (("dscp", 0, 63), ("dport", 1, 65535),
                           ("bytes", 1, MAX_UINT64), ("size", 1, 65507)):
        if key not in a or (key != "size" and a[key] is None):
            continue
        if key in ("dscp", "dport") and a[key] == "":
            continue
        _check(errors, w, integer, a[key], key, minimum=low, maximum=high)
    if kind == "ping":
        _check(errors, w, number, a.get("interval", 0.2), "interval",
               minimum=MIN_TIMER_S, maximum=MAX_TIMER_S)
    if kind == "mcast":
        from lab.mcast import validate_app as mc_validate
        errors.extend(f"{w}: {e}" for e in mc_validate(project, a))
        if version == 6:
            errors.append(f"{w}: IPv6 multicast hozircha qo'llab-quvvatlanmaydi")
    elif a.get("mcast") is not None or a.get("mgroup") is not None:
        # Expanded multicast entries can be replayed, but their internal fields
        # still have to be safe before they reach native address constructors.
        if kind != "udp" or not isinstance(a.get("mcast"), str) or not a.get("mcast"):
            errors.append(f"{w}: expanded mcast: UDP va string id kerak")
        from lab.mcast import validate_app as mc_validate
        mc = {**a, "dst": [a.get("dst")], "group": a.get("mgroup")}
        errors.extend(f"{w}: {e}" for e in mc_validate(project, mc))
        if version == 6:
            errors.append(f"{w}: IPv6 multicast hozircha qo'llab-quvvatlanmaydi")
    for key in (("src",) if kind == "mcast" else ("src", "dst")):
        _endpoint(devs, a.get(key), version, f"{w}.{key}", errors)
    st = _check(errors, w, number, a.get("start", 1), "start", minimum=0)
    sp = _check(errors, w, number, a.get("stop", dur), "stop", minimum=0)
    if st is not None and sp is not None and not (st < sp and st < dur):
        errors.append(f"{w}: start < stop va start < davomiylik bo'lsin")
    rate = a.get("rate")
    if rate is None or rate == "":
        if kind == "udp":
            errors.append(f"{w}: UDP uchun rate kerak (masalan 5Mbps)")
    else:
        parsed = _check(errors, w, parse_rate, rate)
        if parsed is not None and parsed < 1:
            errors.append(f"{w}: rate >= 1 bit/s bo'lsin")
        elif parsed is not None and kind in ("udp", "tcp"):
            _check(errors, w, packet_rate, parsed, a.get("size", 1000))


def _event(project, devs, e, i, dur, errors):
    w = f"hodisa {i + 1}"
    action = e.get("action")
    if action not in EVENT_ACTIONS:
        errors.append(f"{w}: amal {EVENT_ACTIONS} dan biri")
    if action == "move":
        dev = e.get("device")
        if not isinstance(dev, str) or dev not in devs:
            errors.append(f"{w}: qurilma loyihada bo'lsin")
        scale = _check(errors, w, number, project.get("meters_per_px", .25), "meters_per_px")
        limit = MAX_POSITION / max(scale or 1, 1)
        for key in ("x", "y"):
            _check(errors, w, number, e.get(key), key, minimum=-limit, maximum=limit)
            initial = devs.get(dev) if isinstance(dev, str) else None
            if isinstance(initial, dict):
                _check(errors, w, number, initial.get(key, 0), f"initial {key}", minimum=-limit, maximum=limit)
        _check(errors, w, number, e.get("duration", 0), "duration", minimum=0)
    else:
        link = e.get("link")
        if not isinstance(link, str) or link not in (project.get("links") or {}):
            errors.append(f"{w}: kabel loyihada bo'lsin")
    _check(errors, w, number, e.get("t", -1), "t", minimum=0, maximum=dur)
    if action == "link_params":
        params = e.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            errors.append(f"{w}: params object bo'lsin")
            return
        for key in ("bw", "loss", "queue"):
            if key in params and params[key] is not None and params[key] != "":
                check = integer if key == "queue" else number
                _check(errors, w, check, params[key], key)
        for key in ("delay", "jitter"):
            if key not in params or params[key] is None or params[key] == "":
                continue
            try:
                spelling = str(params[key]).strip()
            except ValueError:
                errors.append(f"{w}: {key}: finite duration kerak")
                continue
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(us|ms|s)?", spelling)
            if match:
                value = _check(errors, w, number, match[1], key, minimum=0)
                if value is not None:
                    seconds = value * {"us": 1e-6, "ms": .001, "s": 1, None: .001}[match[2]]
                    _check(errors, w, number, seconds, key, minimum=0, maximum=MAX_TIMER_S - dur)
        # Keep the project's existing link normalization and range rules.
        try:
            normalize_link_params(params, horizon=dur)
        except (ValueError, TypeError, OverflowError) as ex:
            errors.append(f"{w}: link params: {ex}")


def _expanded_ids(sc, apps, errors):
    """Reserve actual engine IDs, including bounded high-level expansions."""
    from lab.apps import expand
    seen = set()
    for i, a in apps:
        aid = a.get("id") or f"a{i + 1}"
        if a.get("type") == "mcast":
            from lab.mcast import receivers_of
            receivers = receivers_of(a)
            if len(receivers) + len(seen) > MAX_EXPANDED_APPS:
                errors.append(f"apps: at most {MAX_EXPANDED_APPS} expanded applications (native ports)")
                return
            keys = (f"{aid}.{r}" for r in receivers)
        else:
            # Expanding one app at a time bounds work to HTTP's MAX_PAGES.
            expanded = expand({"duration": sc["duration"], "apps": [{**a, "id": aid}]})
            keys = (raw["id"] for raw in expanded["apps"])
        for key in keys:
            if key in seen:
                errors.append(f"apps: expanded id takrorlangan ({key})")
                return
            seen.add(key)
            if len(seen) > MAX_EXPANDED_APPS:
                errors.append(f"apps: at most {MAX_EXPANDED_APPS} expanded applications (native ports)")
                return


def validate_scenario(project, sc):
    """Return actionable errors without mutating the project or scenario."""
    if not isinstance(sc, dict):
        return ["scenario: object"]
    if not isinstance(project, dict) or not isinstance(project.get("devices", {}), dict) or not isinstance(project.get("links", {}), dict):
        return ["project: devices/links objects"]
    errors = []
    if "ns3_fast" in sc and type(sc["ns3_fast"]) is not bool:
        errors.append("ns3_fast: boolean required")
    version = sc.get("ip_version", 4)
    if type(version) is not int or version not in (4, 6):
        errors.append("ip_version: 4 | 6")
    for field in ("seed", "rng_run"):
        value = sc.get(field, 1)
        if type(value) is not int or not 1 <= value <= 2147483647:
            errors.append(f"{field}: integer 1..2147483647")
    dur = _check(errors, "scenario", number, sc.get("duration", 0), "duration", minimum=1, maximum=3600)
    dur = dur if dur is not None else 0
    sample = sc.get("sample", 1)
    if type(sample) not in (int, float) or not 0.001 <= sample <= 3600:
        errors.append("sample: number 0.001..3600 s")
    elif dur / sample > 100000:
        errors.append("sample: at most 100000 intervals per scenario")
    _check(errors, "scenario", ospf_convergence_delay, sc)
    apps = _items(sc, "apps", errors)
    events = _items(sc, "events", errors)
    if len(apps) > MAX_EXPANDED_APPS:
        return errors + [f"apps: at most {MAX_EXPANDED_APPS} expanded applications (native ports)"]
    devs = project.get("devices") or {}
    ids = set()
    for i, a in apps:
        _app(project, devs, a, i, dur, version, ids, errors)
    for i, e in events:
        _event(project, devs, e, i, dur, errors)
    # The generator consumes event timestamps while sorting; expand only once
    # structural/numeric checks pass, so malformed events never reach it.
    if not errors:
        _expanded_ids(sc, apps, errors)
    if not errors:
        from lab.failures import validate as validate_failures
        errors.extend(validate_failures(project, sc))
    return errors
