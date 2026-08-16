# ESTÜ Yemekhane Menüsü

Bu repository, Eskişehir Teknik Üniversitesi Sağlık, Kültür ve Spor Daire Başkanlığı sayfalarındaki **Ana Yemekhane** aylık PDF’i ile **Akademik Kulüp** haftalık PDF’ini okuyup iOS Kestirmeleri’nin doğrudan tüketebileceği bir JSON dosyası üretir.

## JSON adresi

Öncelikli Kestirmeler adresi:

```text
https://raw.githubusercontent.com/beytullahgol/estu-menu/main/data/menu.json
```

GitHub Pages adresi alternatif olarak kullanılabilir:

```text
https://beytullahgol.github.io/estu-menu/data/menu.json
```

Telefon ağı GitHub Pages alan adına erişemiyorsa aşağıdaki ücretsiz CDN adreslerinden biri kullanılabilir:

```text
https://cdn.jsdelivr.net/gh/beytullahgol/estu-menu@main/data/menu.json
https://fastly.jsdelivr.net/gh/beytullahgol/estu-menu@main/data/menu.json
```

Workflow her veri değişikliğinden sonra jsDelivr cache’ini temizler. CDN güncellemesi birkaç saniye gecikirse URL’nin sonuna `?v=1` gibi bir sorgu parametresi eklenebilir.

Repository’nin ana sayfası da bu endpoint’e bağlantı verir:

```text
https://beytullahgol.github.io/estu-menu/
```

## Güncelleme mantığı

`.github/workflows/collect_menu.yml` dosyasındaki zamanlanmış görev hafta içi her gün **Türkiye saatiyle yaklaşık 06:03, 09:03 ve 12:03**’te çalışır. GitHub Actions zamanlamaları UTC kullandığından workflow ifadeleri `3 3`, `3 6` ve `3 9` UTC olarak tanımlanmıştır. Böylece 06:03 kontrolünde ESTÜ menüyü henüz yayımlamamışsa, aynı gün 09:03 ve 12:03 kontrollerinde tekrar denenir.

Çalışma sırası şöyledir:

1. ESTÜ’nün iki HTML sayfası alınır ve PDF bağlantıları etiketlerine göre seçilir.
2. Aynı PDF URL’si repository’deki `data/cache` klasöründe bulunuyorsa PDF yeniden indirilmez. Cache dosyaları workflow commit’iyle korunur. URL değiştiğinde yeni PDF indirilir; böylece aylık veya haftalık dosya değişmedikçe ESTÜ’ye gereksiz PDF isteği yapılmaz.
3. PDF içindeki FlateDecode akışları, `Tj`/`TJ` metin operatörleri ve `ToUnicode` CMap eşlemeleri Python standart kütüphanesiyle çözülür.
4. `data/menu.json` güncellenir ve yalnızca değişiklik varsa commit edilip repository’ye gönderilir.
5. Pages’e yalnızca `site/data/menu.json` ve küçük bir bilgilendirme sayfası yayımlanır; PDF cache dosyaları Pages’e yüklenmez.

Hafta sonu çalıştırmasında PDF indirilmez ve JSON şu durumu üretir: `status: "weekend_closed"`. Hafta içi sayfalarda hedef tarih henüz bulunamazsa JSON `status: "not_published"`, boş menü dizileri ve `message` alanında “Bugünün yemek listesi ESTÜ sitesinde henüz yayımlanmadı.” bilgisi üretir. Sadece bir yemekhane bulunursa `status: "partial"` yazılır. Ağ hatasında son geçerli JSON korunur ve bir sonraki zamanlanmış kontrol yeniden dener.

## JSON alanları

| Alan | Açıklama |
|---|---|
| `status` | `ok`, `partial`, `not_published` veya `weekend_closed` |
| `date` | `GG.AA.YYYY` biçiminde hedef tarih |
| `isoDate` | `YYYY-MM-DD` biçiminde tarih |
| `weekday` | ISO hafta günü; Pazartesi `1`, Pazar `7` |
| `isWeekend` | Hafta sonu durumunu gösterir |
| `anaYemekhane` | Ana Yemekhane yemek adları dizisi |
| `akademikKulup` | Akademik Kulüp yemek adları dizisi |
| `generatedAt` | Üretim zamanı, Türkiye saat dilimiyle |
| `sources` | Kullanılan ESTÜ sayfa/PDF URL’leri ve cache bilgisi |
| `retrySchedule` | Menü hazır değilse planlanan tekrar kontrol saatleri |
| `errors` | Kısmi sonuç veya yayınlanmamış menü varsa hata/uyarı metinleri |

## Kestirmeler akışı

Kestirmeler’de **URL’nin İçeriğini Al** eylemine öncelikli raw GitHub adresini veya telefonda açılan CDN JSON adresini verin. Eylemin **Yöntem** seçeneği `GET`, **İstek Gövdesi** ise boş olmalıdır; `POST`, JSON gövdesi veya `menu2.php` adresi kullanılmamalıdır. Ardından **Sözlük Al** eylemini seçin. Ana yemekhane için `anaYemekhane`, Akademik Kulüp için `akademikKulup` anahtarlarından **Liste Al** ile diziyi alın. `status` değeri `weekend_closed` veya `not_published` ise doğrudan `message` alanını bildirim olarak gösterin. Raw GitHub ve jsDelivr hızlı statik dosya sunduğu için bu akış ESTÜ veya InfinityFree bot korumasına bağlı değildir. GitHub Pages yalnızca alternatif yayın adresidir.

## Manuel workflow testi

GitHub repository’sindeki **Actions** sekmesinden **ESTÜ menü verisini güncelle** workflow’unu açıp **Run workflow** seçilebilir. `date` alanına örneğin `14.08.2026` yazılırsa, canlı ESTÜ PDF’lerinden o tarih için regresyon testi yapılır. Alan boş bırakılırsa Türkiye saatine göre güncel tarih kullanılır.

## Kaynaklar

- Ana yemekhane: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/yemekhaneler>
- Akademik Kulüp: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/gunluk-menu>
