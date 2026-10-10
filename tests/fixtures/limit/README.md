# Limit Kara Kutusu fikstürleri (sentetik)

`uret.py` üretir, deterministik. Gerçek veri yok. Yeniden üretmek: `python3 tests/fixtures/limit/uret.py`.

| Dosya | Senaryo | Beklenen |
|---|---|---|
| `masaustu-gecmis.json` | masaüstü uygulamasının `plan-usage-history.json` biçimi (v2), **465 örnek**, her örnekte `org` = `org-kanarya-7f3e9c1d`; 2 adet 6 saatlik boşluk (uygulama kapalı) | `geri_al()` → 465 zaman · 930 gözlem (fh + sd) · kanarya hiçbir çıktıda yok |
| `masaustu-gecmis-v3.json` | biçim değişmiş (version 3, `samples` yok) | `geri_al()` → `okunamadi`, çökme yok |
| `seri-senaryo.jsonl` | 23 Mart 2026, 5 saat penceresi: sakin aralık (%0→%20, 4 saat) · **sıfırlanma** (yüzde 0'a düşer, `r` ileri) · **"Mart 23" hızlı tüketim** (%0→%20, 2 saat — aynı iş, 2 kat hızlı) | FAZ 3: hızlı aralık bayraklanır, sakin aralık bayraklanmaz; sıfırlanma aralığı atlanır |
| `seri-bozuk.jsonl` | **#52326**: yüzde alanında sıfırlanma anı; ayrıca −3, metin yüzde, metin `t`, yarım satır | `oku()` hepsini atlar (0 gözlem) |
| `statusline-bozuk.json` | durum satırı girdisi, `five_hour.used_percentage` = sıfırlanma anı | seriye yalnız `seven_day` girer |
| `transcripts/oturum-{a,b,c}.jsonl` | 3 oturum: a opus 10 turn (+1 tekrar satırı, aynı `message.id` + `requestId`) · b sonnet 20 turn · c opus 5 turn alt ajan (`isSidechain`) | FAZ 2: her turn in 100 · out 50 · cc 1000 · cr 5000; tekrar 1 sayılır → 35 turn |
