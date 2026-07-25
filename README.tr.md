# usage-tracker — Linux için "Pane"

Tüm AI kullanımını, **limitini ve gerçek $ harcamasını tek yerde** gösteren, bağımlılıksız bir Linux aracı — [Pane](https://github.com/ItsJazii/pane) (yalnızca Windows) muadili.

- **Web paneli** `http://127.0.0.1:8770` — harcama kartları, 30 günlük grafik, model tablosu, canlı limit barları.
- **waybar rozeti** — bar'ında `◐ 64%`, duvara ne kadar yaklaştığına göre renklenir.
- **floating widget** — panelin taşınabilir, boyutlandırılabilir, her-zaman-üstte pencere hali.

**stdlib Python + Vanilla JS** ile yazıldı. `pip install` yok, Node yok, Rust yok. Yalnızca loopback.

> 🇬🇧 English: [README.md](README.md)

---

## Kurulum

```bash
git clone https://github.com/ihsandeniz/usage-tracker.git
cd usage-tracker
./setup.sh            # ★ sihirbaz: bağımlılık → sunucu → waybar → widget/tray → key → doğrulama
```

```bash
./setup.sh --auto       # soru sormaz, her adımda önerilen cevabı alır
./setup.sh --uninstall  # sihirbazın kurduğu her şeyi geri alır (key'ler ve repo kalır)
./setup.sh --help
```

Sihirbaz sisteminde ne yapar, ne yapmaz:

- **Dokunduğu her dosyayı yedekler** (`<dosya>.bak-usage-tracker`).
- **waybar config'ini cerrahi düzenler** — yorumların ve biçimin korunur. Dosyayı düzenlemeden
  önce ve sonra parse eder; sonuç geçerli jsonc değilse geri alır.
- **Tahmin yürütmez.** Config bozuksa ya da `modules-*` listesi bir `include` içindeyse dosyaya
  dokunmaz; snippet'i panona kopyalar, sen yapıştırırsın.
- **Tekrar çalıştırılabilir.** Zaten yapılmış adımlar hiçbir şeyi değiştirmez.
- **Shell'inde tanımlı API key'leri** key dosyasına aktarır — çünkü shell export'ları otomatik
  başlayan servise ulaşmaz; kartlar bu yüzden sessizce boş kalıyordu.
- **Söz vermez, doğrular:** sonda sunucuya sorar, çözülen sağlayıcı kartlarını sayar ve waybar
  besleyicisini bir kez çalıştırıp gerçek çıktısını gösterir.
- root yok, dış ağ isteği yok; `~/.config` ve bu repo dışına çıkmaz.

Elle kurmayı tercih edersen sihirbazı atla:

```bash
./install.sh          # makineye özel config üretir, waybar/widget snippet'lerini basar
./start.sh            # → http://127.0.0.1:8770
```

**Gereksinim:** Python 3.9+ (stdlib). waybar besleyicisi için `curl` + `jq`. Floating widget için
Chromium ailesi bir tarayıcı (opsiyonel), `hyprctl` (opsiyonel, Hyprland'de widget'ı otomatik
float eder). `install.sh` eksikleri raporlar.

## Otomatik başlatma (önerilir)

Rozetin her zaman veri göstermesi için sunucu login'de kalksın. Tek komut, **root gerektirmez** — bir **systemd user service** (her systemd dağıtımında çalışır):

```bash
./service.sh install      # üret + etkinleştir + login'de başlat
./service.sh status       # durumu gör
./service.sh uninstall    # kaldır
```

Sadece sunucunun otomatik başlaması yeter; waybar rozeti ve floating widget ona bağlanır.

<details><summary>systemd olmadan otomatik başlatma</summary>

**Hyprland** (`hyprland.conf`):
```
exec-once = /MUTLAK/YOL/start.sh
```

**Genel XDG autostart** — `~/.config/autostart/usage-tracker.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=usage-tracker
Exec=/MUTLAK/YOL/start.sh
X-GNOME-Autostart-enabled=true
```
</details>

## Ne izler?

| Boyut | Ne | Kaynak |
|---|---|---|
| **Harcama** | Bugün / Dün / 30 gün **$** + model bazlı + 30 günlük grafik | `~/.claude/projects/**/*.jsonl` token'ları × gerçek fiyat |
| **Limit** | Oturum (5s) + Haftalık kullanım %'si, reset geri sayımı | Anthropic'in kendi `/api/oauth/usage` ucu (gerçek), yoksa yerel tahmin |
| **Sağlayıcılar** | 15 adaptör: gerçek $ (OpenRouter, OpenAI), kredi/kota (ElevenLabs, HuggingFace, Together, Novita, DeepInfra), yerel (Ollama, LM Studio, Jan), lokal-log token (Codex, Aider, Continue, Cody, Windsurf) | her sağlayıcının API'si / yerel dosyaları |

### Veri gerçek mi?

Güven önemli — her sayı kaynağıyla etiketli, sessizce hiçbir şey uydurulmaz:

- **Token sayıları: %100 gerçek** — transcript'ten okunur, hesap değil.
- **Fiyat: gerçek** — Opus/Sonnet/Haiku [models.dev](https://models.dev) kataloğundan (`source: catalog`); katalogda olmayan modeller resmi üretici fiyatından (`source: official`). Doğrulanmamış her şey `source: estimate` işaretlenir.
- **Limit: gerçek, kalibrasyonsuz** — Claude Code'un `/usage` komutuyla aynı uçtan gelir.
- **Abonelikteysen (Max, ChatGPT Plus…),** $ rakamı **API-eşdeğeri maliyettir** (bu kullanım pay-as-you-go olsa ne tutardı) = abonelikten çıkardığın değer. Gerçek faturan sabit ücrettir, bu rakam değil.
- **Ayrıştırılamaz:** Opus fast-mode ve 1M-context premium fiyatı transcript'in model alanında görünmez → standart fiyattan hesaplanır (gerçek biraz daha yüksek olabilir).

## Yüzey — iki bağımsız seçenek

`surface/` altındaki her şey **izoledir** (mevcut `~/.config`'e asla dokunmaz) ve iki yüzey
**birbirinden bağımsızdır** — waybar rozetini, floating widget'ı, ikisini veya hiçbirini kur.
Hiçbiri diğerine bağlı değil.

### Seçenek A — waybar rozeti

Bar'ında kompakt `◐ %` modülü; manşet = en yüksek Claude limit %'si, tooltip her sağlayıcıyı
listeler, tıklayınca web paneli açılır. `install.sh` hazır `custom/usage` snippet'i + `style.css`
renklerini basar. Bağımsız — tarayıcı gerekmez.

### Seçenek B — floating widget

Web panelini **taşınabilir + boyutlandırılabilir**, her-zaman-üstte bir pencere olarak açar
(Chromium ailesi `--app` penceresi). Bar widget'ının aksine istediğin yere koyar, istediğin
gibi boyutlandırırsın:

- **Taşı** — `Super`+sürükle (veya kompozitörünün taşıma tuşu).
- **Boyutlandır** — `Super`+sağ-sürükle veya kenarlardan. Tarayıcı boyut/konumu hatırlar.

```bash
surface/usage-widget open      # göster
surface/usage-widget close     # gizle
surface/usage-widget toggle    # aç/kapat (waybar on-click için ideal)
surface/usage-widget status    # açık / kapalı
```

**Hyprland**'de otomatik float + pin (her workspace'te görünür) + `WIDGET_SIZE` boyutunda sağ
üste yerleştirilir — config düzenlemeye gerek yok. Diğer kompozitörlerde kendi kontrollerinle
float/boyutlandır. Chromium ailesi tarayıcı gerekir (chromium / chrome / brave / …). Bağımsız —
waybar gerekmez.

#### Masaüstü uyumluluğu

**waybar rozeti** systemd-tabanlı tüm Linux'lerde çalışır (Hyprland, GNOME, Plasma, vb.).  
**floating widget** Chromium ailesi tarayıcı gerektirir ve tüm Linux'lerde çalışır:
- **Hyprland:** Otomatik float/pin dahili.
- **GNOME / Plasma / Cinnamon:** Kendi kontrollerinle float/pin edin (genelde title bar'a sağ tıkla → Özellikler).
- **i3 / Openbox:** Float katmanları çalışır; katman sırası için config'izi inceleyin.

Çekirdek **web paneli** (`http://127.0.0.1:8770`) tüm Linux ve tüm masaüstü ortamında çalışır — özel kurulum gerekmez.

Otomatik başlatma (Hyprland): `exec-once = /MUTLAK/YOL/surface/usage-widget open`.

## Sağlayıcı ekleme

Adaptörler `usage/providers/` altında. Her modül `collect(days) -> dict | None` sunar; `None` = "kart yok" (yapılandırılmamış sağlayıcı sessizce gizlenir — **ölü kart yok**). Eklemek için `usage/providers/<ad>.py` bırak ve `_ADAPTERS`'a kaydet. OpenRouter ortamdan `$OPENROUTER_API_KEY` okur.

## Güvenlik & gizlilik

- Yalnızca **`127.0.0.1`** dinler — ağa asla açılmaz.
- Verilerine **salt-okunur**. `~/.claude/.credentials.json`'daki OAuth token'ı yalnızca *okunur*, asla yazılmaz/yenilenmez (yazmak aktif oturumu düşürebilir).
- Telemetri yok; zaten kullandığın sağlayıcı API'leri dışında dış çağrı yok.
- Statik dosya servisi path-traversal korumalı.

## Lisans

[MIT](LICENSE) © 2026 İhsan Deniz
