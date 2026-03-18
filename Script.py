#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    print("Install dependency dulu: pip install requests")
    sys.exit(1)

# =========================================================
# PATH CONFIG
# =========================================================

PREFERRED_DIR = Path("/storage/emulated/0/Download/dual-indexing-app")
FALLBACK_DIR = Path.home() / "dual-indexing-app"


def resolve_app_dir() -> Path:
    try:
        PREFERRED_DIR.mkdir(parents=True, exist_ok=True)
        test = PREFERRED_DIR / ".test"
        test.write_text("ok")
        test.unlink()
        return PREFERRED_DIR
    except Exception:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        return FALLBACK_DIR


APP_DIR = resolve_app_dir()

CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state_urls.json"
QUEUE_FILE = APP_DIR / "queue.json"
STATS_FILE = APP_DIR / "stats.json"
REPORT_DIR = APP_DIR / "reports"
LOG_FILE = APP_DIR / "app.log"

USER_AGENT = "SmartIndexingEngine/5.2"

# =========================================================
# DEFAULT CONFIG
# =========================================================

DEFAULT_CONFIG = {
    "domain": "example.com",
    "blogger": {"latest_limit": 20},
    "hosting": {
        "latest_limit": 20,
        "indexnow_enabled": False,
        "indexnow_key": "",
        "indexnow_key_location": ""
    },
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "mode": "all"
    },
    "engine": {
        "mode": "balanced",
        "max_urls_per_run": 50,
        "retry_schedule_seconds": [3600, 21600, 86400],
        "score_threshold_submit": 40,
        "score_threshold_retry": 20,
        "index_check_enabled": True
    },
    "retry": {
        "max_retries": 3,
        "delay": 1.5
    }
}

# =========================================================
# BASIC UTIL
# =========================================================

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(text: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"[{now()}] {text}\n")


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except:
        return default


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def normalize_domain(domain: str) -> str:
    domain = domain.lower().strip()
    domain = domain.replace("https://", "").replace("http://", "")
    return domain.strip("/")


def site_url(domain: str) -> str:
    return f"https://{normalize_domain(domain)}"


def sitemap_url(domain: str) -> str:
    return f"{site_url(domain)}/sitemap.xml"


# =========================================================
# LOAD / SAVE
# =========================================================

def load_config():
    cfg = read_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
    return cfg


def save_config(cfg):
    write_json(CONFIG_FILE, cfg)


def load_state():
    return read_json(STATE_FILE, {})


def save_state(data):
    write_json(STATE_FILE, data)


def load_queue():
    return read_json(QUEUE_FILE, {
        "priority": [],
        "normal": [],
        "retry": [],
        "failed": []
    })


def save_queue(data):
    write_json(QUEUE_FILE, data)


def load_stats():
    return read_json(STATS_FILE, {
        "runs": 0,
        "submitted": 0,
        "retry": 0
    })


def save_stats(data):
    write_json(STATS_FILE, data)


# =========================================================
# NETWORK
# =========================================================

def request(url, timeout=15):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)


def internet_ok():
    try:
        r = request("https://www.google.com")
        return r.status_code == 200
    except:
        return False


# =========================================================
# SITEMAP PARSER
# =========================================================

def parse_sitemap(url):
    urls = []
    try:
        r = request(url)
        root = ET.fromstring(r.content)

        for u in root:
            loc = ""
            lastmod = ""
            for x in u:
                tag = x.tag.lower()
                if "loc" in tag:
                    loc = x.text
                if "lastmod" in tag:
                    lastmod = x.text

            if loc:
                urls.append({"loc": loc, "lastmod": lastmod})

    except Exception as e:
        log(f"sitemap error {e}")

    return urls


# =========================================================
# STATE MANAGEMENT
# =========================================================

def ensure_state(state, url, lastmod):
    if url not in state:
        state[url] = {
            "url": url,
            "score": 0,
            "status": "new",
            "submitted": 0,
            "retry": 0,
            "next_retry": "",
            "lastmod": lastmod,
            "history": []
        }
    return state[url]


def add_history(entry, text):
    entry["history"].append(f"{now()} | {text}")
    if len(entry["history"]) > 10:
        entry["history"] = entry["history"][-10:]


# =========================================================
# SCORING
# =========================================================

def score_url(entry):
    score = 0

    if entry["submitted"] == 0:
        score += 30

    if entry["lastmod"]:
        score += 10

    if score >= 60:
        grade = "A"
    elif score >= 40:
        grade = "B"
    elif score >= 20:
        grade = "C"
    else:
        grade = "D"

    return score, grade
  # =========================================================
# QUEUE MANAGEMENT
# =========================================================

def reset_url_from_all_queues(queue_data, url):
    for key in ("priority", "normal", "retry", "failed"):
        if url in queue_data[key]:
            queue_data[key].remove(url)


def push_queue(queue_data, queue_name, url):
    reset_url_from_all_queues(queue_data, url)
    if url not in queue_data[queue_name]:
        queue_data[queue_name].append(url)


def build_queues(cfg, discovered_urls, state, queue_data):
    engine = cfg.get("engine", {})
    threshold_submit = int(engine.get("score_threshold_submit", 40))
    threshold_retry = int(engine.get("score_threshold_retry", 20))

    priority_count = 0
    normal_count = 0
    failed_count = 0

    for item in discovered_urls:
        url = item.get("loc", "")
        lastmod = item.get("lastmod", "")
        if not url:
            continue

        entry = ensure_state(state, url, lastmod)
        entry["lastmod"] = lastmod or entry.get("lastmod", "")

        score, grade = score_url(entry)
        entry["score"] = score
        entry["grade"] = grade

        if grade == "A":
            entry["status"] = "priority"
            push_queue(queue_data, "priority", url)
            add_history(entry, f"Scored {score} ({grade}) -> priority")
            priority_count += 1
        elif score >= threshold_submit:
            entry["status"] = "normal"
            push_queue(queue_data, "normal", url)
            add_history(entry, f"Scored {score} ({grade}) -> normal")
            normal_count += 1
        elif score >= threshold_retry:
            entry["status"] = "retry"
            push_queue(queue_data, "retry", url)
            add_history(entry, f"Scored {score} ({grade}) -> retry")
            normal_count += 1
        else:
            entry["status"] = "failed"
            push_queue(queue_data, "failed", url)
            add_history(entry, f"Scored {score} ({grade}) -> failed")
            failed_count += 1

    return priority_count, normal_count, failed_count


def get_retry_schedule(cfg):
    raw = cfg.get("engine", {}).get("retry_schedule_seconds", [3600, 21600, 86400])
    result = []
    for item in raw:
        try:
            val = int(item)
            if val > 0:
                result.append(val)
        except:
            pass
    return result or [3600, 21600, 86400]


def schedule_retry(cfg, entry):
    schedule = get_retry_schedule(cfg)
    retry_count = int(entry.get("retry", 0))
    stage = retry_count if retry_count < len(schedule) else len(schedule) - 1
    seconds = schedule[stage]
    entry["retry"] = retry_count + 1
    entry["next_retry"] = str(int(time.time()) + seconds)
    add_history(entry, f"Retry scheduled in {seconds}s")


def retry_ready(entry):
    next_retry = str(entry.get("next_retry", "")).strip()
    if not next_retry:
        return True
    try:
        return int(time.time()) >= int(next_retry)
    except:
        return True


def select_urls_for_run(cfg, queue_data, state):
    engine = cfg.get("engine", {})
    max_urls = int(engine.get("max_urls_per_run", 50))

    selected = []

    for url in queue_data.get("priority", []):
        if url not in selected:
            selected.append(url)

    for url in queue_data.get("normal", []):
        if url not in selected:
            selected.append(url)

    for url in queue_data.get("retry", []):
        entry = state.get(url, {})
        if retry_ready(entry) and url not in selected:
            selected.append(url)

    if max_urls <= 0:
        return selected

    return selected[:max_urls]


# =========================================================
# INDEXNOW
# =========================================================

def validate_indexnow(cfg):
    result = {
        "valid": False,
        "errors": [],
        "warnings": []
    }

    domain = normalize_domain(cfg.get("domain", ""))
    hosting = cfg.get("hosting", {})
    key = str(hosting.get("indexnow_key", "")).strip()
    key_location = str(hosting.get("indexnow_key_location", "")).strip()

    if not domain:
        result["errors"].append("Domain kosong")

    if not key:
        result["errors"].append("IndexNow key kosong")

    if not key_location:
        result["errors"].append("Key location kosong")
        return result

    parsed = urlparse(key_location)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        result["errors"].append("Key location bukan URL valid")
        return result

    key_domain = normalize_domain(parsed.netloc)
    if domain and key_domain != domain:
        result["errors"].append(
            f"Domain key_location ({key_domain}) tidak sama dengan domain aktif ({domain})"
        )

    if parsed.path.count("/") > 1:
        result["warnings"].append("Key location tidak berada di root domain")

    if not key_location.endswith(".txt"):
        result["warnings"].append("Key location tidak berakhiran .txt")

    try:
        r = request(key_location, timeout=cfg.get("timeouts", {}).get("indexnow", 15))
        if r.status_code != 200:
            result["errors"].append(f"Key file tidak bisa diakses (HTTP {r.status_code})")
        else:
            if r.text.strip() != key:
                result["errors"].append("Isi key file tidak cocok dengan key IndexNow")
    except Exception as e:
        result["errors"].append(f"Gagal mengakses key file: {e}")

    result["valid"] = len(result["errors"]) == 0
    return result


def submit_indexnow(cfg, urls):
    hosting = cfg.get("hosting", {})
    endpoint = "https://api.indexnow.org/indexnow"
    payload = {
        "host": normalize_domain(cfg.get("domain", "")),
        "key": hosting.get("indexnow_key", ""),
        "keyLocation": hosting.get("indexnow_key_location", ""),
        "urlList": urls
    }

    try:
        r = requests.post(endpoint, json=payload, timeout=cfg.get("timeouts", {}).get("indexnow", 15))
        return {
            "ok": r.ok,
            "status_code": r.status_code,
            "response": r.text[:1000]
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": 0,
            "response": str(e)
        }


# =========================================================
# TELEGRAM
# =========================================================

def telegram_send(cfg, text):
    tg = cfg.get("telegram", {})
    if not tg.get("enabled"):
        return False, "Telegram nonaktif"

    token = str(tg.get("bot_token", "")).strip()
    chat_id = str(tg.get("chat_id", "")).strip()

    if not token or not chat_id:
        return False, "Bot token atau chat_id kosong"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text[:3900],
                "disable_web_page_preview": True
            },
            timeout=cfg.get("timeouts", {}).get("telegram", 10)
        )
        if r.ok:
            return True, "Pesan Telegram berhasil dikirim"
        return False, f"HTTP {r.status_code} - {r.text[:500]}"
    except Exception as e:
        return False, str(e)


# =========================================================
# INDEX CHECK
# =========================================================

def run_index_check(cfg, state, queue_data, stats):
    urls = list(state.keys())
    if not urls:
        return {
            "ok": False,
            "message": "Belum ada URL di state"
        }

    indexed_count = 0
    unknown_count = 0
    not_indexed_count = 0

    for idx, url in enumerate(urls, start=1):
        entry = state[url]
        try:
            pf = {
                "status": None,
                "meta_robots": "",
                "canonical": "",
                "issues": []
            }

            try:
                r = request(url, timeout=cfg.get("timeouts", {}).get("preflight", 10))
                pf["status"] = r.status_code
                html = r.text[:300000]
                pf["meta_robots"] = parse_meta_robots(html)
                pf["canonical"] = parse_canonical(html)
            except Exception as e:
                pf["issues"].append(str(e))

            entry["check_count"] = int(entry.get("check_count", 0)) + 1
            entry["index_attempts"] = int(entry.get("index_attempts", 0)) + 1
            entry["last_checked_at"] = now()
            entry["index_checked_at"] = now()

            if pf.get("status") == 200 and "noindex" not in str(pf.get("meta_robots", "")).lower():
                if int(entry.get("submitted", 0)) > 0:
                    entry["index_status"] = "unknown"
                    entry["status"] = "checking"
                    entry["last_result"] = "index_check_unknown"
                    add_history(entry, "Index check: unknown")
                    unknown_count += 1
                else:
                    entry["index_status"] = "unknown"
                    unknown_count += 1
            else:
                entry["index_status"] = "not_indexed"
                entry["status"] = "retry"
                entry["last_result"] = "index_check_not_indexed"
                entry["last_error"] = f"status={pf.get('status')} issues={pf.get('issues')}"
                add_history(entry, "Index check: not indexed")
                schedule_retry(cfg, entry)
                push_queue(queue_data, "retry", url)
                stats["retry"] = int(stats.get("retry", 0)) + 1
                not_indexed_count += 1

            progress_bar(idx, len(urls), "Index Check")

        except Exception as e:
            entry["index_status"] = "unknown"
            entry["last_error"] = str(e)
            add_history(entry, f"Index check error: {e}")
            unknown_count += 1

    save_state(state)
    save_queue(queue_data)
    save_stats(stats)

    report = (
        "INDEX CHECK SELESAI\n"
        f"Total URL dicek: {len(urls)}\n"
        f"Indexed: {indexed_count}\n"
        f"Unknown: {unknown_count}\n"
        f"Not Indexed: {not_indexed_count}"
    )

    log(report.replace("\n", " | "))
    return {"ok": True, "message": report}


# =========================================================
# ENGINE RUNNERS
# =========================================================

def run_blogger_engine(cfg, interactive=True):
    started = time.time()

    if interactive:
        banner()
        slow_print("Menjalankan Blogger Engine...")

    if not internet_ok():
        msg = "Tidak ada koneksi internet"
        if interactive:
            fail(msg)
            pause()
        return {"ok": False, "message": msg}

    domain = normalize_domain(cfg.get("domain", ""))
    sitemap = sitemap_url(domain)
    limit = int(cfg.get("blogger", {}).get("latest_limit", 20))

    if interactive and limit <= 0:
        warn("Mode Blogger memakai limit tak terbatas. Proses bisa lama.")

    discovered = parse_sitemap(sitemap)
    if limit > 0:
        discovered = discovered[:limit]

    state = load_state()
    queue_data = load_queue()
    stats = load_stats()

    priority_count, normal_count, failed_count = build_queues(cfg, discovered, state, queue_data)
    selected = select_urls_for_run(cfg, queue_data, state)

    for url in selected:
        entry = state[url]
        entry["status"] = "checking"
        add_history(entry, "Dipilih untuk run Blogger")

    stats["runs"] = int(stats.get("runs", 0)) + 1
    stats["last_run_at"] = now()

    save_state(state)
    save_queue(queue_data)
    save_stats(stats)

    report = (
        "BLOGGER ENGINE SELESAI\n"
        f"Discovered: {len(discovered)}\n"
        f"Priority: {priority_count}\n"
        f"Normal/Retry: {normal_count}\n"
        f"Failed: {failed_count}\n"
        f"Selected this run: {len(selected)}\n"
        f"Durasi: {time.time() - started:.2f} detik"
    )

    if interactive:
        section("Hasil Blogger Engine")
        print(report)
        tg_mode = cfg.get("telegram", {}).get("mode", "all")
        if tg_mode in {"all", "new_only"}:
            telegram_send(cfg, report)
        pause()

    log(report.replace("\n", " | "))
    return {"ok": True, "message": report}


def run_hosting_engine(cfg, interactive=True):
    started = time.time()

    if interactive:
        banner()
        slow_print("Menjalankan Hosting Engine...")

    if not internet_ok():
        msg = "Tidak ada koneksi internet"
        if interactive:
            fail(msg)
            pause()
        return {"ok": False, "message": msg}

    domain = normalize_domain(cfg.get("domain", ""))
    sitemap = sitemap_url(domain)
    limit = int(cfg.get("hosting", {}).get("latest_limit", 20))

    if interactive and limit <= 0:
        warn("Mode Hosting memakai limit tak terbatas. Proses bisa lama.")

    discovered = parse_sitemap(sitemap)
    if limit > 0:
        discovered = discovered[:limit]

    state = load_state()
    queue_data = load_queue()
    stats = load_stats()

    priority_count, normal_count, failed_count = build_queues(cfg, discovered, state, queue_data)
    selected = select_urls_for_run(cfg, queue_data, state)
    submitted = 0

    if cfg.get("hosting", {}).get("indexnow_enabled"):
        validation = validate_indexnow(cfg)
        if not validation["valid"]:
            if interactive:
                warn("Validasi IndexNow gagal. Submit diblokir.")
                for err in validation["errors"]:
                    fail(err)
            log(f"Validasi IndexNow gagal: {validation}")
        else:
            if interactive:
                section("Submit IndexNow")

            batches = [selected[i:i + 100] for i in range(0, len(selected), 100)]
            for i, batch in enumerate(batches, start=1):
                result = submit_indexnow(cfg, batch)
                progress_bar(i, len(batches), "Submit batch")

                if result["ok"]:
                    for url in batch:
                        entry = state[url]
                        entry["submitted"] = int(entry.get("submitted", 0)) + 1
                        if not entry.get("first_submitted_at"):
                            entry["first_submitted_at"] = now()
                        entry["last_submitted_at"] = now()
                        entry["status"] = "submitted"
                        entry["queue"] = ""
                        entry["last_result"] = "submit_success"
                        entry["last_error"] = ""
                        add_history(entry, "Submit IndexNow berhasil")
                        reset_url_from_all_queues(queue_data, url)
                    submitted += len(batch)
                else:
                    for url in batch:
                        entry = state[url]
                        entry["last_result"] = "submit_failed"
                        entry["last_error"] = str(result["response"])
                        add_history(entry, "Submit IndexNow gagal")
                        schedule_retry(cfg, entry)
                        push_queue(queue_data, "retry", url)
                        stats["retry"] = int(stats.get("retry", 0)) + 1
                    log(f"Submit gagal: {result}")

    else:
        for url in selected:
            entry = state[url]
            entry["status"] = "checking"
            add_history(entry, "Dipilih untuk run Hosting tanpa submit")

    stats["runs"] = int(stats.get("runs", 0)) + 1
    stats["submitted"] = int(stats.get("submitted", 0)) + submitted
    stats["last_run_at"] = now()

    save_state(state)
    save_queue(queue_data)
    save_stats(stats)

    report = (
        "HOSTING ENGINE SELESAI\n"
        f"Discovered: {len(discovered)}\n"
        f"Priority: {priority_count}\n"
        f"Normal/Retry: {normal_count}\n"
        f"Failed: {failed_count}\n"
        f"Selected this run: {len(selected)}\n"
        f"Submitted: {submitted}\n"
        f"Durasi: {time.time() - started:.2f} detik"
    )

    if interactive:
        section("Hasil Hosting Engine")
        print(report)
        tg_mode = cfg.get("telegram", {}).get("mode", "all")
        if tg_mode in {"all", "new_only"}:
            telegram_send(cfg, report)
        pause()

    log(report.replace("\n", " | "))
    return {"ok": True, "message": report}
  # =========================================================
# REPORT EXPORT
# =========================================================

def export_report(cfg):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    queue_data = load_queue()
    stats = load_stats()

    ts = time.strftime("%Y%m%d_%H%M%S")
    txt_path = REPORT_DIR / f"report_{ts}.txt"
    json_path = REPORT_DIR / f"report_{ts}.json"

    payload = {
        "generated_at": now(),
        "domain": normalize_domain(cfg.get("domain", "")),
        "stats": stats,
        "queue_summary": {
            "priority": len(queue_data.get("priority", [])),
            "normal": len(queue_data.get("normal", [])),
            "retry": len(queue_data.get("retry", [])),
            "failed": len(queue_data.get("failed", [])),
        },
        "top_priority": [],
        "top_retry": [],
        "top_failed": [],
    }

    for url in queue_data.get("priority", [])[:20]:
        entry = state.get(url, {})
        payload["top_priority"].append({
            "url": url,
            "score": entry.get("score", 0),
            "grade": entry.get("grade", "D"),
            "status": entry.get("status", "")
        })

    for url in queue_data.get("retry", [])[:20]:
        entry = state.get(url, {})
        payload["top_retry"].append({
            "url": url,
            "retry": entry.get("retry", 0),
            "next_retry": entry.get("next_retry", ""),
            "status": entry.get("status", "")
        })

    for url in queue_data.get("failed", [])[:20]:
        entry = state.get(url, {})
        payload["top_failed"].append({
            "url": url,
            "score": entry.get("score", 0),
            "grade": entry.get("grade", "D"),
            "status": entry.get("status", ""),
            "last_error": entry.get("last_error", "")
        })

    txt_lines = []
    txt_lines.append("SMART INDEXING ENGINE V5.2 REPORT")
    txt_lines.append("=" * 40)
    txt_lines.append(f"Generated at : {payload['generated_at']}")
    txt_lines.append(f"Domain       : {payload['domain']}")
    txt_lines.append("")
    txt_lines.append("STATISTICS")
    for k, v in payload["stats"].items():
        txt_lines.append(f"- {k}: {v}")

    txt_lines.append("")
    txt_lines.append("QUEUE SUMMARY")
    for k, v in payload["queue_summary"].items():
        txt_lines.append(f"- {k}: {v}")

    txt_lines.append("")
    txt_lines.append("TOP PRIORITY")
    for item in payload["top_priority"]:
        txt_lines.append(
            f"- {item['url']} | score={item['score']} | grade={item['grade']} | status={item['status']}"
        )

    txt_lines.append("")
    txt_lines.append("TOP RETRY")
    for item in payload["top_retry"]:
        txt_lines.append(
            f"- {item['url']} | retry={item['retry']} | next={item['next_retry']} | status={item['status']}"
        )

    txt_lines.append("")
    txt_lines.append("TOP FAILED")
    for item in payload["top_failed"]:
        txt_lines.append(
            f"- {item['url']} | score={item['score']} | grade={item['grade']} | status={item['status']} | error={item['last_error']}"
        )

    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
    write_json(json_path, payload)

    return str(txt_path), str(json_path)


# =========================================================
# VIEWERS
# =========================================================

def show_queue_view():
    banner()
    section("Queue Viewer")

    queue_data = load_queue()
    state = load_state()

    queue_name = menu_choice(
        "Pilih queue (priority/normal/retry/failed): ",
        ["priority", "normal", "retry", "failed"]
    )

    urls = queue_data.get(queue_name, [])
    if not urls:
        warn("Queue kosong.")
        pause()
        return

    print(f"Total item: {len(urls)}\n")

    for url in urls[:30]:
        entry = state.get(url, {})
        print(
            f"- {url}\n"
            f"  score      : {entry.get('score', 0)}\n"
            f"  grade      : {entry.get('grade', '-')}\n"
            f"  status     : {entry.get('status', '-')}\n"
            f"  retry      : {entry.get('retry', 0)}\n"
            f"  next_retry : {entry.get('next_retry', '-')}\n"
        )

    pause()


def show_stats():
    banner()
    section("Statistik")
    stats = load_stats()
    print(json.dumps(stats, indent=2))
    pause()


def show_config(cfg):
    banner()
    section("Konfigurasi")
    print(json.dumps(cfg, indent=2))
    pause()


# =========================================================
# SETTINGS
# =========================================================

def setup_domain(cfg):
    banner()
    section("Ubah Domain")

    while True:
        domain = normalize_domain(safe_input("Domain tanpa https: "))
        if not domain:
            warn("Domain tidak boleh kosong.")
            continue
        if not is_valid_domain(domain):
            warn("Format domain tidak valid.")
            continue
        cfg["domain"] = domain
        save_config(cfg)
        success(f"Domain disimpan: {domain}")
        pause()
        return


def setup_blogger(cfg):
    banner()
    section("Pengaturan Blogger")
    print("Masukkan 0 untuk tak terbatas.\n")

    cur = str(cfg.get("blogger", {}).get("latest_limit", 20))
    val = safe_input(f"Jumlah URL terbaru [{cur}]: ", cur)
    limit = safe_int(val, 20)
    if limit < 0:
        limit = 20

    cfg["blogger"]["latest_limit"] = limit
    save_config(cfg)
    success(f"Limit Blogger: {limit_label(limit)}")
    pause()


def setup_hosting(cfg):
    banner()
    section("Pengaturan Hosting")
    print("Masukkan 0 untuk tak terbatas.\n")

    host_cfg = cfg.setdefault("hosting", {})
    cur = str(host_cfg.get("latest_limit", 20))
    val = safe_input(f"Jumlah URL terbaru [{cur}]: ", cur)
    limit = safe_int(val, 20)
    if limit < 0:
        limit = 20

    enabled = menu_choice("Aktifkan IndexNow? (y/n): ", ["y", "n"])
    key = safe_input(
        f"IndexNow key [{host_cfg.get('indexnow_key', '')}]: ",
        host_cfg.get("indexnow_key", "")
    )
    keyloc = safe_input(
        f"Key location URL [{host_cfg.get('indexnow_key_location', '')}]: ",
        host_cfg.get("indexnow_key_location", "")
    )

    host_cfg["latest_limit"] = limit
    host_cfg["indexnow_enabled"] = enabled == "y"
    host_cfg["indexnow_key"] = key
    host_cfg["indexnow_key_location"] = keyloc

    save_config(cfg)
    success(f"Limit Hosting: {limit_label(limit)}")
    pause()


def setup_telegram(cfg):
    banner()
    section("Pengaturan Telegram")

    tg = cfg.setdefault("telegram", {})
    token = safe_input(f"Bot token [{tg.get('bot_token', '')}]: ", tg.get("bot_token", ""))
    chat_id = safe_input(f"Chat ID [{tg.get('chat_id', '')}]: ", tg.get("chat_id", ""))
    enabled = menu_choice("Aktifkan Telegram? (y/n): ", ["y", "n"])
    mode = menu_choice("Mode Telegram (all/new_only/error_only): ", ["all", "new_only", "error_only"])

    tg["bot_token"] = token
    tg["chat_id"] = chat_id
    tg["enabled"] = enabled == "y"
    tg["mode"] = mode

    save_config(cfg)
    success("Pengaturan Telegram disimpan.")
    pause()


def setup_preflight(cfg):
    banner()
    section("Pengaturan Preflight")

    pf = cfg.setdefault("preflight", {})
    pf["enabled"] = menu_choice("Preflight manual? (y/n): ", ["y", "n"]) == "y"
    pf["auto_run_enabled"] = menu_choice("Preflight auto-run? (y/n): ", ["y", "n"]) == "y"

    save_config(cfg)
    success("Pengaturan Preflight disimpan.")
    pause()


def setup_timeouts(cfg):
    banner()
    section("Pengaturan Timeout")

    tc = cfg.setdefault("timeouts", {})
    for key in ["sitemap", "preflight", "telegram", "indexnow", "general"]:
        cur = str(tc.get(key, DEFAULT_CONFIG["timeouts"][key]))
        tc[key] = max(1, safe_int(safe_input(f"Timeout {key} [{cur}]: ", cur), safe_int(cur, 15)))

    save_config(cfg)
    success("Timeout disimpan.")
    pause()


def setup_retry(cfg):
    banner()
    section("Pengaturan Retry")

    rc = cfg.setdefault("retry", {})
    rc["max_retries"] = max(
        1,
        safe_int(
            safe_input(f"Max retries [{rc.get('max_retries', 3)}]: ", str(rc.get("max_retries", 3))),
            3
        )
    )
    rc["delay"] = max(
        0.0,
        safe_float(
            safe_input(f"Delay retry [{rc.get('delay', 1.5)}]: ", str(rc.get("delay", 1.5))),
            1.5
        )
    )

    save_config(cfg)
    success("Retry disimpan.")
    pause()


def setup_auto_run(cfg):
    banner()
    section("Pengaturan Auto Run")

    ar = cfg.setdefault("auto_run", {})
    ar["mode"] = menu_choice(
        f"Mode auto-run [{ar.get('mode', 'blogger')}] (blogger/hosting): ",
        ["blogger", "hosting"]
    )
    ar["interval_seconds"] = max(
        10,
        safe_int(
            safe_input(f"Interval detik [{ar.get('interval_seconds', 1800)}]: ", str(ar.get("interval_seconds", 1800))),
            1800
        )
    )
    ar["enabled"] = menu_choice("Aktifkan auto-run? (y/n): ", ["y", "n"]) == "y"

    save_config(cfg)
    success("Auto-run disimpan.")
    pause()


def setup_engine_mode(cfg):
    banner()
    section("Pengaturan Engine")

    eng = cfg.setdefault("engine", {})
    eng["mode"] = menu_choice(
        f"Mode engine [{eng.get('mode', 'balanced')}] (safe/balanced/aggressive): ",
        ["safe", "balanced", "aggressive"]
    )

    current_max = str(eng.get("max_urls_per_run", 50))
    max_val = safe_input(f"Max URLs per run [{current_max}] (0 = tak terbatas): ", current_max)
    eng["max_urls_per_run"] = max(0, safe_int(max_val, 50))

    current_submit = str(eng.get("score_threshold_submit", 40))
    current_retry = str(eng.get("score_threshold_retry", 20))

    eng["score_threshold_submit"] = safe_int(
        safe_input(f"Score threshold submit [{current_submit}]: ", current_submit),
        40
    )
    eng["score_threshold_retry"] = safe_int(
        safe_input(f"Score threshold retry [{current_retry}]: ", current_retry),
        20
    )

    current_check = "y" if eng.get("index_check_enabled", True) else "n"
    eng["index_check_enabled"] = menu_choice(
        f"Aktifkan index check? ({current_check}) (y/n): ",
        ["y", "n"]
    ) == "y"

    current_sched = ",".join(str(x) for x in eng.get("retry_schedule_seconds", [3600, 21600, 86400]))
    sched_val = safe_input(f"Retry schedule detik koma-separator [{current_sched}]: ", current_sched)

    parsed = []
    for part in sched_val.split(","):
        val = safe_int(part.strip(), 0)
        if val > 0:
            parsed.append(val)

    eng["retry_schedule_seconds"] = parsed or [3600, 21600, 86400]

    save_config(cfg)
    success("Pengaturan Engine disimpan.")
    pause()


# =========================================================
# TEST / VALIDATION
# =========================================================

def test_telegram_menu(cfg):
    banner()
    section("Tes Telegram")

    ok, detail = telegram_send(cfg, "Tes Telegram dari Smart Indexing Engine V5.2")
    if ok:
        success(detail)
    else:
        fail(detail)

    pause()


def show_indexnow_validation(cfg):
    banner()
    section("Validasi IndexNow")

    result = validate_indexnow(cfg)
    if result["valid"]:
        success("Validasi berhasil.")
    else:
        fail("Validasi gagal.")

    for err in result["errors"]:
        fail(err)

    for wrn in result["warnings"]:
        warn(wrn)

    pause()


# =========================================================
# RESET
# =========================================================

def reset_all():
    banner()
    section("Reset State/Queue")

    confirm = menu_choice("Yakin reset semua data engine? (y/n): ", ["y", "n"])
    if confirm != "y":
        warn("Reset dibatalkan.")
        pause()
        return

    save_state({})
    save_queue({
        "priority": [],
        "normal": [],
        "retry": [],
        "failed": []
    })
    save_stats({
        "runs": 0,
        "submitted": 0,
        "retry": 0
    })

    success("State, queue, dan stats direset.")
    pause()


# =========================================================
# DASHBOARD
# =========================================================

def dashboard(cfg):
    domain = normalize_domain(cfg.get("domain", ""))
    blog = cfg.get("blogger", {})
    host = cfg.get("hosting", {})
    eng = cfg.get("engine", {})
    stats = load_stats()
    queue_data = load_queue()

    return "\n".join([
        f"Path aktif        : {APP_DIR}",
        f"Domain aktif      : {domain or '-'}",
        f"Sitemap           : {sitemap_url(domain) if domain else '-'}",
        f"Blogger limit     : {limit_label(safe_int(blog.get('latest_limit', 20), 20))}",
        f"Hosting limit     : {limit_label(safe_int(host.get('latest_limit', 20), 20))}",
        f"Engine mode       : {eng.get('mode', 'balanced')}",
        f"Max URLs/run      : {limit_label(safe_int(eng.get('max_urls_per_run', 50), 50))}",
        f"Total runs        : {stats.get('runs', 0)}",
        f"Total submitted   : {stats.get('submitted', 0)}",
        f"Total retries     : {stats.get('retry', 0)}",
        (
            "Queue             : "
            f"P={len(queue_data.get('priority', []))} | "
            f"N={len(queue_data.get('normal', []))} | "
            f"R={len(queue_data.get('retry', []))} | "
            f"F={len(queue_data.get('failed', []))}"
        ),
    ])


# =========================================================
# AUTO RUN
# =========================================================

def auto_run_loop(cfg):
    banner()
    section("Auto Run")

    ar = cfg.get("auto_run", {})
    if not ar.get("enabled"):
        warn("Auto-run belum aktif.")
        pause()
        return

    mode = ar.get("mode", "blogger")
    interval = safe_int(ar.get("interval_seconds", 1800), 1800)

    if mode == "hosting":
        limit = safe_int(cfg.get("hosting", {}).get("latest_limit", 20), 20)
    else:
        limit = safe_int(cfg.get("blogger", {}).get("latest_limit", 20), 20)

    if limit <= 0:
        warn("Limit auto-run = Tak terbatas. Ini bisa berat di Termux.")
        if menu_choice("Lanjut? (y/n): ", ["y", "n"]) != "y":
            warn("Auto-run dibatalkan.")
            pause()
            return

    info("Tekan Ctrl+C untuk stop auto-run.")

    while True:
        current = load_config()

        if mode == "hosting":
            result = run_hosting_engine(current, interactive=False)
        else:
            result = run_blogger_engine(current, interactive=False)

        if current.get("engine", {}).get("index_check_enabled", True):
            run_index_check(current, load_state(), load_queue(), load_stats())

        print()
        section("Hasil Auto Run")
        print(result.get("message", "Tidak ada hasil"))

        tg_mode = current.get("telegram", {}).get("mode", "all")
        if tg_mode in {"all", "new_only"}:
            telegram_send(current, result.get("message", "Auto-run selesai"))

        info(f"Menunggu {interval} detik...")
        try:
            for i in range(interval):
                progress_bar(i + 1, interval, "Countdown")
                time.sleep(1)
        except KeyboardInterrupt:
            print()
            warn("Auto-run dihentikan pengguna.")
            pause()
            return


# =========================================================
# MAIN LOOP
# =========================================================

def main_loop():
    cfg = load_config()

    while True:
        banner()
        print(dashboard(cfg))
        print(
            textwrap.dedent(
                """

                MENU:
                1. Jalankan Blogger Engine
                2. Jalankan Hosting Engine
                3. Ubah domain
                4. Pengaturan Blogger
                5. Pengaturan Hosting
                6. Pengaturan Telegram
                7. Pengaturan Preflight
                8. Pengaturan Timeout
                9. Pengaturan Retry
                10. Pengaturan Auto Run
                11. Tes Telegram
                12. Validasi IndexNow
                13. Jalankan Index Check
                14. Lihat Queue
                15. Lihat Statistik
                16. Reset State/Queue
                17. Lihat Konfigurasi
                18. Jalankan Auto Run
                19. Pengaturan Engine
                20. Export Report
                21. Keluar
                """
            )
        )

        choice = safe_input("Pilih menu: ").lower()

        if choice in {"1", "blogger", "b"}:
            run_blogger_engine(cfg, interactive=True)
            cfg = load_config()

        elif choice in {"2", "hosting", "h"}:
            run_hosting_engine(cfg, interactive=True)
            cfg = load_config()

        elif choice in {"3", "domain", "d"}:
            setup_domain(cfg)
            cfg = load_config()

        elif choice == "4":
            setup_blogger(cfg)
            cfg = load_config()

        elif choice == "5":
            setup_hosting(cfg)
            cfg = load_config()

        elif choice == "6":
            setup_telegram(cfg)
            cfg = load_config()

        elif choice == "7":
            setup_preflight(cfg)
            cfg = load_config()

        elif choice == "8":
            setup_timeouts(cfg)
            cfg = load_config()

        elif choice == "9":
            setup_retry(cfg)
            cfg = load_config()

        elif choice == "10":
            setup_auto_run(cfg)
            cfg = load_config()

        elif choice == "11":
            test_telegram_menu(cfg)
            cfg = load_config()

        elif choice == "12":
            show_indexnow_validation(cfg)
            cfg = load_config()

        elif choice == "13":
            run_index_check(cfg, load_state(), load_queue(), load_stats())
            pause()
            cfg = load_config()

        elif choice == "14":
            show_queue_view()
            cfg = load_config()

        elif choice == "15":
            show_stats()
            cfg = load_config()

        elif choice == "16":
            reset_all()
            cfg = load_config()

        elif choice == "17":
            show_config(cfg)
            cfg = load_config()

        elif choice == "18":
            auto_run_loop(cfg)
            cfg = load_config()

        elif choice == "19":
            setup_engine_mode(cfg)
            cfg = load_config()

        elif choice == "20":
            banner()
            section("Export Report")
            txt_path, json_path = export_report(cfg)
            success(f"TXT  : {txt_path}")
            success(f"JSON : {json_path}")
            pause()
            cfg = load_config()

        elif choice in {"21", "q", "exit", "keluar"}:
            banner()
            slow_print("Terima kasih. Smart Indexing Engine ditutup dengan aman.")
            break

        else:
            warn("Perintah tidak dikenal. Aplikasi tetap berjalan.")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print()
        warn("Aplikasi dihentikan pengguna.")
