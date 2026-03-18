# Smart Indexing Engine

![Python](https://img.shields.io/badge/python-3.10+-blue)
![Status](https://img.shields.io/badge/status-active-success)
![License](https://img.shields.io/badge/license-MIT-green)

Smart Indexing Engine adalah tool CLI berbasis Python untuk membantu mengelola workflow indexing secara lebih terstruktur.

Fitur utama:
- membaca sitemap
- scoring URL
- queue management
- retry scheduler
- integrasi IndexNow (hosting)
- export report
- auto-run

Dirancang untuk Termux dan environment Python ringan.

---

## Quick Start

```bash
pip install -r requirements.txt
python script.py
```

---

## Fitur

### Blogger Engine
- membaca sitemap Blogger
- mengambil URL terbaru
- scoring otomatis
- queue: priority, normal, retry, failed

### Hosting Engine
- validasi IndexNow
- submit batch URL
- retry otomatis

### URL Scoring
Penilaian berdasarkan:
- URL baru
- lastmod

Output:
- A (prioritas)
- B
- C
- D

---

### Queue Management

Menggunakan 4 queue:
- priority
- normal
- retry
- failed

---

### Retry Scheduler

Retry otomatis dengan interval:
- 1 jam
- 6 jam
- 24 jam

---

### Index Check (Basic)

Cek:
- HTTP status
- noindex

Status:
- unknown
- not_indexed

---

### Export Report

Format:
- TXT
- JSON

Isi:
- statistik
- queue
- URL prioritas
- retry list

---

### Telegram Notification

- kirim hasil run
- test bot
- mode fleksibel

---

### Auto Run

Jalankan otomatis dengan interval tertentu.

---

## Struktur File

```
script.py
README.md
requirements.txt
.gitignore
LICENSE
config.example.json
```

---

## Konfigurasi

File otomatis:
- config.json
- state_urls.json
- queue.json
- stats.json

Gunakan `config.example.json` sebagai template.

---

## Cara Kerja

1. baca sitemap  
2. kumpulkan URL  
3. scoring  
4. masuk queue  
5. pilih URL  
6. submit / monitor  
7. simpan state  
8. retry  
9. update statistik  

---

## Use Cases

- monitoring Blogger
- workflow IndexNow
- queue URL management
- automation Termux

---

## Limitations

- tidak menjamin index
- tergantung search engine
- bukan pengganti GSC
- index check hanya estimasi

---

## Termux Setup

```bash
termux-setup-storage
```

---

## Version

v5.2

---

## License

MIT License
