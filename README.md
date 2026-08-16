# ESTÜ Yemekhane Menüsü

Bu repository, Eskişehir Teknik Üniversitesi Sağlık, Kültür ve Spor Daire Başkanlığı sayfalarındaki **Ana Yemekhane** aylık PDF’i ile **Akademik Kulüp** haftalık PDF’ini okuyup iOS Kestirmeleri’nin doğrudan tüketebileceği bir JSON dosyası üretir.

## JSON adresi

GitHub Pages ilk kez dağıtıldıktan sonra Kestirmeler’de kullanılacak adres:

```text
https://beytullahgol.github.io/estu-menu/data/menu.json
```

Repository’nin ana sayfası da bu endpoint’e bağlantı verir:

```text
https://beytullahgol.github.io/estu-menu/
```

## Güncelleme mantığı

`.github/workflows/collect_menu.yml` dosyasındaki zamanlanmış görev hafta içi her gün **Türkiye saatiyle yaklaşık 06:00**’da çalışır. GitHub Actions zamanlamaları UTC kullandığından workflow ifadesi `0 3 * * 1-5` olarak tanımlanmıştır.

Çalışma sırası şöyledir:

1. ESTÜ’nün iki HTML sayfası alınır ve PDF bağlantıları etiketlerine göre seçilir.
2. Aynı PDF URL’si daha önce cache’lenmişse PDF yeniden indirilmez. URL değiştiğinde yeni PDF indirilir; böylece aylık veya haftalık dosya değişmedikçe ESTÜ’ye gereksiz PDF isteği yapılmaz.
3. PDF içindeki FlateDecode akışları, `Tj`/`TJ` metin operatörleri ve `ToUnicode` CMap eşlemeleri Python standart kütüphanesiyle çözülür.
4. `data/menu.json` güncellenir ve yalnızca değişiklik varsa commit edilip repository’ye gönderilir.
5. Pages’e yalnızca `site/data/menu.json` ve küçük bir bilgilendirme sayfası yayımlanır; PDF cache dosyaları Pages’e yüklenmez.

Hafta sonu çalıştırmasında PDF indirilmez ve JSON şu durumu üretir: `status: "weekend_closed"`.

## JSON alanları

| Alan | Açıklama |
|---|---|
| `status` | `ok`, `partial` veya `weekend_closed` |
| `date` | `GG.AA.YYYY` biçiminde hedef tarih |
| `isoDate` | `YYYY-MM-DD` biçiminde tarih |
| `weekday` | ISO hafta günü; Pazartesi `1`, Pazar `7` |
| `isWeekend` | Hafta sonu durumunu gösterir |
| `anaYemekhane` | Ana Yemekhane yemek adları dizisi |
| `akademikKulup` | Akademik Kulüp yemek adları dizisi |
| `generatedAt` | Üretim zamanı, Türkiye saat dilimiyle |
| `sources` | Kullanılan ESTÜ sayfa/PDF URL’leri ve cache bilgisi |
| `errors` | Kısmi sonuç varsa hata veya uyarı metinleri |

## Kestirmeler akışı

Kestirmeler’de **URL’nin İçeriğini Al** eylemine yukarıdaki JSON adresini verin. Ardından **Sözlük Al** eylemini seçin. Ana yemekhane için `anaYemekhane`, Akademik Kulüp için `akademikKulup` anahtarlarından **Liste Al** ile diziyi alın. Hafta sonu için `status` değerini `weekend_closed` olarak kontrol edip bildirim metnini `message` alanından oluşturun.

## Manuel workflow testi

GitHub repository’sindeki **Actions** sekmesinden **ESTÜ menü verisini güncelle** workflow’unu açıp **Run workflow** seçilebilir. `date` alanına örneğin `14.08.2026` yazılırsa, canlı ESTÜ PDF’lerinden o tarih için regresyon testi yapılır. Alan boş bırakılırsa Türkiye saatine göre güncel tarih kullanılır.

## Kaynaklar

- Ana yemekhane: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/yemekhaneler>
- Akademik Kulüp: <https://saglikkulturspor.eskisehir.edu.tr/tr/Icerik/Detay/gunluk-menu>
