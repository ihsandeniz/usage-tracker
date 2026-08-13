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

Yazmak yerine tıklamak istersen: `./setup.sh --ui` aynı altı adımı bir tarayıcı
penceresinde açar — her adım ne yazacağının **gerçek diff'ini** gösterir, yanında da
"Geri al" düğmesi durur.

![kurulum sihirbazı, waybar config'ine yazacağı diff'i gösteriyor](docs/setup-wizard.png)

**Windows'ta mısın?** Sihirbaz bash, işine yaramaz; panel ve CLI ikisi de çalışır.
[`docs/WINDOWS.md`](docs/WINDOWS.md) — hangi yüzeylerin **olmadığı** ve oradaki hangi
iddianın gerçek makinede ölçüldüğü dâhil (şimdilik: hiçbiri).

```bash
./setup.sh --ui         # sihirbaz, sayfa olarak (pencere açar; URL'i her hâlükârda yazar)
./setup.sh --auto       # soru sormaz, her adımda önerilen cevabı alır
./setup.sh --uninstall  # sihirbazın kurduğu her şeyi geri alır (key'ler ve repo kalır)
./setup.sh --help
```

Varsayılan terminal sihirbazı kalır — SSH'ta, ekransız makinede ve dotfiles scriptinde
çalışan tek yol o. İki yüz de aynı kodu çağırır (`setup.sh probe` · `do <adım>` ·
`undo <adım>`), bu yüzden zamanla birbirinden ayrışamazlar.

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

### `--ui` neden ayrı ve geçici bir sunucu?

Sürekli çalışan sunucu (`:8770`) **salt-okunurdur** ve aracın "hiçbir şey bu makineden
çıkmaz" iddiası buna dayanır. waybar config'ini düzenleyen veya API anahtarlarına dokunan
uçların orada işi yok: bir tarayıcı, loopback sunucusuna istek atmaya kandırılabilir. Açık
olan herhangi bir sayfa `127.0.0.1`'e POST atabilir (CORS yanıtı *okumayı* engeller, isteği
*göndermeyi* değil) ve `127.0.0.1`'e çözülen bir alan adı aynı-köken sayılıp yanıtı da
okuyabilir. Bu yüzden `--ui` **ayrı** bir sunucu başlatır:

- rastgele bir port seçer ve tek kullanımlık bir jeton üretir; jeton URL'in fragment'ında
  gider (fragment'lar sunucuya hiç gönderilmez, yani bir log'a düşemez);
- `Host` başlığı `127.0.0.1:<port>` değilse reddeder — DNS rebinding'i kapatan şey budur;
- yabancı `Origin`'li her isteği ve jetonsuz her API çağrısını reddeder;
- **hiçbir anahtar değerini geri döndürmez** — anahtar yazabilir veya silebilirsin, okuyamazsın;
- "Bitir"e bastığında ya da 10 dakika işlem olmazsa kendini kapatır.

Anahtar değerleri `setup.sh`'a stdin ile ulaşır; argv ile asla — argv `/proc` üzerinden
herkese okunabilir.

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

### Seçenek C — komut satırı

Diğer iki yüzey *baktığın* şeyler. Bu, başka bir programın **sorabildiği** yüzey:

```bash
python3 server.py guard --threshold 80 || echo "şimdi olmaz — kota bitmek üzere"
```

```
python3 server.py usage        # limitler, harcama ve tüm sağlayıcı kartları
python3 server.py providers    # hangi kartlar var, biri neden yok
python3 server.py guard        # çıkış kodu: 0 iyi · 1 uyarı · 2 kritik · 3 bilinmiyor
python3 server.py watch        # eşik geçişinde bir kez tetikler (--exec / --notify)
python3 server.py doctor       # log istemeden kurulum teşhisi
python3 server.py config       # hangi kartlar görünür + eşik/yenileme
```

Eşiği tek yerden taşırsın, **dört yüzey birden** uyar — çünkü çift wire'ın içinde gezer:

```bash
python3 server.py config --warn 60 --crit 85   # panel · waybar · tepsi · guard
```

Sunucu gerekmez (`:8770` cevap vermiyorsa sayıları kendi hesaplar), bash/curl/jq de
gerekmez — Windows'un kullanılabilir bir yüzeye kavuşma yolu bu; `.sh` besleyiciler
Linux'a özel kalıyor. `usage --format waybar` = kabuksuz waybar besleyicisi. Tek dosyalık
paket aynı ikilidir: `usage-tracker guard`.

**Bilinmeyen sıfır değildir:** güvenilecek bir yüzde yoksa `guard` 0 değil **3** döner —
0 dönseydi tam da engellemek istediğin pahalı işi sessizce başlatırdı.

Tam referans (çıkış kodu sözleşmesi + `watch --exec`'in verdiği `UT_*` değişkenleri):
[`docs/CLI.md`](docs/CLI.md) — belge İngilizce, CLI çıktısı da öyle.

## Sağlayıcı ekleme

Adaptörler `usage/providers/` altında. Her modül `collect(days) -> dict | None` sunar; `None` = "kart yok" (yapılandırılmamış sağlayıcı sessizce gizlenir — **ölü kart yok**). Eklemek için `usage/providers/<ad>.py` bırak ve `_ADAPTERS`'a kaydet. OpenRouter ortamdan `$OPENROUTER_API_KEY` okur.

## Ayarlar

Panelin **⚙ Ayarlar** bölümü (ya da `config` komutu) üç şeyi değiştirir: uyarı/kritik eşiği,
panelin yenileme aralığı ve görüntüleme para birimi. Hepsi `settings.json`'a yazılır;
eşik oradan `/v1/usage`'ın tepesine, oradan da bütün yüzeylere gider.

**Para birimi bir dönüşümdür, bir gerçek değil.** Kuru sen girersin — uygulama hiçbir yerden
kur çekmez. Çevrilmiş tutar dolarların *yanında*, kuruyla ve girdiğin tarihle gösterilir:
`≈ ₺210.850,89 · kur 1 USD = 41,2 TRY · girildi 12.08.2026`. Kaynağı görünmeyen çevrilmiş
sayı, savunulamayan sayıdır.

**Anahtarlar buradan yazılmaz.** Panel ve `config`, ortam değişkeninin *adını* ve dolu/boş
olduğunu gösterir; değerini asla. Anahtar yazma işi `./setup.sh --ui` (adım `keys`) içinde
kalır: rastgele port + tek kullanımlık jeton + kendini kapatma. Kalıcı panel sunucusunun
üçü de yok ve gün boyu açık duruyor — o yüzden oraya bu yetki verilmedi.

## Güvenlik & gizlilik

- Yalnızca **`127.0.0.1`** dinler — ağa asla açılmaz.
- Yazan uçlar `Host` **ve** `Origin` doğrular: internetteki bir sayfa loopback'e istek
  atabilir, ama yabancı `Origin` ile yazamaz (403).
- Verilerine **salt-okunur**. `~/.claude/.credentials.json`'daki OAuth token'ı yalnızca *okunur*, asla yazılmaz/yenilenmez (yazmak aktif oturumu düşürebilir).
- Telemetri yok; zaten kullandığın sağlayıcı API'leri dışında dış çağrı yok.
- Statik dosya servisi path-traversal korumalı.

## Lisans

[MIT](LICENSE) © 2026 İhsan Deniz
