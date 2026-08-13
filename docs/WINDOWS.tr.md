# Windows'ta kurulum

> [English version](WINDOWS.md) · Bu sayfa Windows 10 ve 11 içindir.

Hiçbir komut yazmana gerek yok. Tek bir dosya indiriyorsun, çift tıklıyorsun, gerisini
program soruyor.

---

## Kurulum — 3 adım

### 1. Dosyayı indir

[Releases sayfasına](https://github.com/ihsandeniz/usage-tracker/releases/latest) git ve
**`usage-tracker-<sürüm>-windows-x64.exe`** dosyasına tıkla. Tek dosya, yaklaşık 10 MB.
Kurulum sihirbazı, tepsi ikonu, ek program — hiçbiri ayrı indirilmiyor; hepsi bu dosyanın
içinde.

İndirilenler klasöründe kalması sorun değil; program birazdan kendini kalıcı bir yere
kopyalamayı teklif edecek.

### 2. Çift tıkla

**Ne göreceksin:** Windows muhtemelen mavi bir uyarı çıkaracak:

> **Windows protected your PC** — Microsoft Defender SmartScreen prevented an unrecognized
> app from starting.

Bu, "virüs bulundu" demek **değil**. "Bu dosyayı daha önce görmedim ve dijital imzası yok"
demek. İmza yıllık ücretli bir sertifika gerektiriyor ve bu projede yok. Devam etmek için:

1. **More info** (Daha fazla bilgi) yazısına tıkla
2. Çıkan **Run anyway** (Yine de çalıştır) düğmesine bas

> Bu adımı atmak istemiyorsan — ki makul bir tercih — sayfanın sonundaki
> [Kaynaktan çalıştırma](#kaynaktan-çalıştırma-komut-yazmayı-bilenler-için) bölümüne bak:
> orada çalıştırdığın her satırı okuyabilirsin.

### 3. Sihirbaz açılıyor

Program çalışınca **siyah bir pencere** açılır (bu normaldir, program orada çalışıyor) ve
**tarayıcında kurulum sayfası kendiliğinden açılır**. Sayfanın sağ üstündeki **TR/EN**
düğmesiyle dili değiştirebilirsin.

Sihirbaz dört şey soruyor. Hepsi isteğe bağlı, hepsi geri alınabilir:

| Adım | Ne yapar | Önerim |
|---|---|---|
| **Programı kalıcı bir yere koy** | Dosyayı `%LOCALAPPDATA%\Programs\usage-tracker\` altına kopyalar | ✅ Evet — İndirilenler'i temizleyince kısayol kırılmasın |
| **Oturum açınca başlat** | Bilgisayarı her açtığında **penceresiz** başlar | ✅ Evet — asıl kolaylık bu |
| **Panel kısayolu** | Başlat menüsüne "usage-tracker" ekler | ✅ Evet |
| **API anahtarları** | OpenRouter gibi *dış* servisler için | ⏭️ Şimdilik atla — Claude kullanımı için **anahtar gerekmez** |

Her adımda **"Ne yazacağını göster"** düğmesi var: dosyanın tam yolunu ve içeriğini,
yazılmadan önce gösterir. Beğenmediğin bir adımı **"Geri al"** ile aynı sayfadan geri
alabilirsin.

Bitince **"Bitir"** de. Sihirbaz kapanır, panel açılır. Kurulum bitti.

---

## Kurduktan sonra

- **Paneli açmak:** Başlat menüsünde `usage-tracker` yaz, tıkla. (Ya da tarayıcıda
  <http://127.0.0.1:8770>)
- **Program nerede çalışıyor:** Yalnızca kendi bilgisayarında, `127.0.0.1` adresinde.
  Dışarıya açılmaz, internete veri göndermez.
- **Kaldırmak:** Aşağıdaki [Kaldırma](#kaldırma) bölümü.

---

## Bir şeyler ters gittiyse

### Çift tıkladım, siyah pencere açıldı, başka bir şey olmadı

Bu **eski sürümlerin** davranışıydı ve düzeltildi. `v0.4.0` ve üstünde tarayıcı
kendiliğinden açılır. Sürümünü kontrol et: siyah pencerenin ilk satırında yazıyor
(`usage-tracker 0.4.0 → http://127.0.0.1:8770`).

Tarayıcın yine de açılmadıysa, o pencerede yazan adresi kendin yaz: **http://127.0.0.1:8770**

### Siyah pencereyi kapattım, program durdu

Doğru davranış: o pencere programın kendisi. Kapatmak = programı durdurmak.

Kalıcı çözüm: sihirbazın **"Oturum açınca başlat"** adımını uygula. O adımdan sonra program
her açılışta **penceresiz** başlar ve bu pencereyi bir daha görmezsin.

### Windows dosyayı silmiş / antivirüs karantinaya almış

PyInstaller ile paketlenen programlar antivirüslerde zaman zaman yanlış alarm veriyor.
Yapabileceklerin:

1. İndirilenler klasöründeki dosyayı antivirüs programında **istisna** olarak işaretle
2. Ya da [kaynaktan çalıştır](#kaynaktan-çalıştırma-komut-yazmayı-bilenler-için) — orada
   çalışan her satır okunabilir Python kodu
3. Dosyanın doğru olduğunu kontrol etmek istersen: her sürümde `SHA256SUMS.txt` yayınlanıyor

### Panel açıldı ama hiçbir veri yok

Program, Claude Code'un bilgisayarına yazdığı kayıtları okuyor:
`C:\Users\<kullanıcı-adın>\.claude\projects`

Bu klasör yoksa gösterecek veri de yok. En sık sebepler:

- **Claude Code'u WSL içinde kullanıyorsun.** WSL'in kendi ev dizini var; Windows programı
  oraya bakamaz. Bu durumda çözüm, WSL'in içinde Linux sürümünü çalıştırmak.
- Bu bilgisayarda Claude Code'u hiç çalıştırmadın.

Kontrol etmek için siyah pencerede program çalışırken panelde **⚙ Ayarlar** kısmına bak, ya
da `usage-tracker doctor` çalıştır (aşağıda anlatılıyor).

### "Port zaten kullanımda" hatası

Başka bir program 8770 portunu tutuyor. Farklı bir portta çalıştır:

Başlat menüsüne `cmd` yaz, Komut İstemi'ni aç ve şunu yapıştır (kendi yolunu yaz):

```
set USAGE_PORT=8771
"%LOCALAPPDATA%\Programs\usage-tracker\usage-tracker.exe"
```

---

## Kaldırma

Sihirbazın yazdığı her şeyi geri almanın en kolay yolu, sihirbazı tekrar açıp her adımda
**"Geri al"**a basmak.

Elle yapmak istersen üç yer var:

| Ne | Nerede |
|---|---|
| Programın kendisi | `%LOCALAPPDATA%\Programs\usage-tracker\` |
| Otomatik başlatma | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\usage-tracker.vbs` |
| Ayarların ve verilerin | `%APPDATA%\usage-tracker\` ve `%LOCALAPPDATA%\usage-tracker\` |

Kayıt defterine (registry) hiçbir şey yazılmaz — API anahtarı eklediysen o hariç
(`HKCU\Environment`), onu da sihirbazın anahtar adımından geri alabilirsin.

---

## Windows'ta ne var, ne yok

| | Var mı |
|---|---|
| Web paneli | ✅ |
| Kurulum sihirbazı (tarayıcı + terminal) | ✅ |
| Komut satırı (`usage`, `guard`, `doctor`…) | ✅ |
| Oturum açılışında penceresiz başlatma | ✅ |
| Sistem tepsisi ikonu | ❌ Qt gerektiriyor, pakete konmadı |
| waybar rozeti / yüzen widget | ❌ Bunlar Linux masaüstüne özgü |

---

## Kaynaktan çalıştırma (komut yazmayı bilenler için)

İmzasız bir `.exe` çalıştırmak istemiyorsan:

```powershell
git clone https://github.com/ihsandeniz/usage-tracker.git
cd usage-tracker
python server.py
```

Python 3.9 veya üstü yeterli — başka hiçbir bağımlılık yok, kurulum adımı yok. Bu yolda
`usage-tracker <komut>` yerine `python server.py <komut>` yazılır:

```powershell
python server.py setup --ui     # sihirbaz, tarayıcıda
python server.py doctor         # ne bulundu, ne eksik
python server.py usage          # limitler ve harcama, terminalde
```

---

## Bu sayfadaki hangi bilgi ölçüldü, hangisi ölçülmedi

Bu projenin geliştirme makinesi Linux; Windows tarafı çoğunlukla otomatik testlerle
(GitHub'ın `windows-latest` koşucusu) ölçülüyor. Dürüst ayrım:

**Ölçüldü** (her sürümde, gerçek Windows üzerinde otomatik):
- `.exe` açılıyor, paneli ve verileri sunuyor
- Fiyat kataloğu paketin içinden geliyor
- Sihirbaz Başlangıç klasörüne yazıyor, geri alıyor, kendi yazmadığı dosyaya dokunmuyor
- Taze bir makinede çift tık → sihirbaz açılıyor → "Bitir" → panel devralıyor
- Pencere iki dilde açıklıyor

**Ölçülmedi** (gerçek bir masaüstü gerekiyor):
- SmartScreen ve antivirüs davranışı
- Otomatik başlatmanın yeniden başlatmadan sonra gerçekten çalışması
- Soğuk açılış süresi
- Claude Code'un senin Windows kurulumunda verisini nereye yazdığı

Bu maddelerden birini denersen, [bir issue aç](https://github.com/ihsandeniz/usage-tracker/issues)
— bu sayfanın eksik yarısı tam olarak orası.
